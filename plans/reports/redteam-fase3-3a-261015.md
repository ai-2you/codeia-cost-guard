# Red team del diseño 3.3a (`recoger` v2, contenedores por SSH, caducidad de certificados)

Fecha: 2026-10-15 · Objeto: `plans/261013-2350-sqlite-historico/fase3-3a-diseno.md` contrastado con `fase3-plan-monitorizacion.md` (sección E,
D-A..D-E), `../reports/revision-diseno-fase3-261014.md` (RT1) y el código de `feat/fase1-ingesta-hermes`. Solo lectura: no se ha tocado ningún
servidor ni la red; las IPs de los ejemplos son de documentación (RFC 5737). Marcas: **[V]** verificado leyendo código o documentación primaria;
**[S]** supuesto o conocimiento no comprobado en este equipo. Gravedad: **C** crítica · **A** alta · **M** media · **B** baja.

## Modelo de atacante
1. **Proceso sin privilegios en el host** (p. ej. `www-data` tras un fallo de la web): puede escribir en `/dev/log`, ejecutar cualquier binario,
   cambiar su `comm`, lanzar su propio `sshd` en un puerto alto y conectarse a él.
2. **Contenedor hostil de un cliente**: controla nombre, etiquetas, política de reinicio, salud y ciclo de vida de sus contenedores; si tiene el
   socket de Docker montado (Traefik, Dokploy, DinD) puede crear y destruir contenedores a voluntad.
3. **Servidor entero comprometido**: `recoger.sh` emite lo que quiera. Aquí no hay defensa posible; solo acotar el daño en el Mac.

## TL;DR
El diseño acierta en la dirección (campos de confianza, plantilla fija, enumeraciones cerradas, histéresis). Lo que lo hunde tal cual está:
1. **`_SYSTEMD_UNIT` es el único campo que sirve**, y solo cuando PAM saca las sesiones del cgroup de `ssh.service`; el script debe comprobarlo
   solo y decirlo (`aislamiento si|no`). `_COMM` es inútil contra un atacante y `_EXE`/`_UID` lo supera un `sshd` legítimo lanzado sin privilegios.
2. **Una etiqueta de contenedor con `\n### intentos` reescribe cualquier sección del protocolo**: `secciones()` reabre y vacía la sección repetida
   y `recoger-root` imprime el último. Hace falta `{{json ...}}` en el script **y** un parser que no reabra secciones.
3. **El tope de 200 contenedores en el orden de `docker ps -a` (más nuevos primero) es un interruptor de ceguera**: un contenedor que crea 200
   efímeros deja a los de producción fuera, marca `truncado` para siempre y suspende toda detección de desapariciones del servidor; y agota el
   presupuesto de 100 eventos/día en el primer ciclo.
4. **Sobrescribir horas completas con «el valor más reciente» permite borrar el pasado**: hay que fundir con `max`, acotar la ventana escribible
   a 25 h y descartar la sección si los cubos no cuadran con `total`.
5. **Con v2 la salida se acerca al `TOPE_BYTES` de 64 KiB**: el que se pase pierde la lectura entera, intentos incluidos. Tope por línea en el
   script y tope por sección en el Mac.
Los certificados están bien planteados; el riesgo real es de rotulado (hoja frente a cadena), no de manipulación.

---

## 1. Journal: qué campo es de confianza y cuál es el filtro

### H1 (A) `_COMM` no es prueba de nada; `_EXE` y `_UID` tampoco bastan
- Evidencia: journald rellena `_COMM`, `_EXE`, `_CMDLINE`, `_UID`, `_PID`, `_SYSTEMD_UNIT` y `_SYSTEMD_CGROUP` a partir del PID que llega por
  `SCM_CREDENTIALS` y de `/proc/PID/*`; el cliente no puede alterarlos [V, man `systemd.journal-fields`]. Pero **el contenido** de `/proc/PID/comm`
  lo fija el propio proceso: es el nombre del fichero ejecutado (15 bytes) y se cambia con `prctl(PR_SET_NAME)` o, sin programar nada, con
  `cp /usr/bin/logger ~/sshd && ~/sshd -t sshd 'Invalid user a from 203.0.113.9 port 1'` → `_COMM=sshd`, `_COMM=sshd-session` cabe igual [S alta].
- `_EXE` es `readlink(/proc/PID/exe)`: la copia da `/home/x/sshd`, no `/usr/sbin/sshd`, así que resiste la copia. Pero **un usuario sin
  privilegios puede ejecutar el `sshd` auténtico** con su propia configuración y clave de host (`/usr/sbin/sshd -f ~/cfg -h ~/clave -p 2222`) y
  conectarse a él con usuarios inexistentes: las líneas son genuinas, `_EXE=/usr/sbin/sshd`, `_COMM=sshd`, `_UID=<suyo>`,
  `_SYSTEMD_UNIT=session-N.scope` [S alta]. Solo puede incriminar IPs desde las que él mismo conecta (no hay PROXY protocol en OpenSSH), pero
  basta para inflar `total` y las IPs, y para que `_COMM`/`_EXE` queden descartados como criterio. Además la ruta de `sshd-session` cambia por
  distribución (`/usr/lib/openssh/`, `/usr/libexec/openssh/`, `/usr/lib/ssh/`).
- `_UID=0` separa el `sshd` pirata del real **si** las líneas del hijo sin privilegios las escribe el monitor: `monitor_read_log` las relanza por
  `sshlogdirect` → `do_log`, que es quien llama a `syslog(3)` con UID 0 [V código de `monitor.c`/`log.c` en master; S para versiones antiguas].
  Es una propiedad de implementación, no de contrato: no fijarla como condición dura sin medirla.
- **Lo único que distingue a sshd de todo lo anterior es el cgroup**: `_SYSTEMD_UNIT=ssh.service` (Debian/Ubuntu) o `sshd.service` (el resto).
  Ningún proceso sin privilegios puede entrar en ese cgroup; un contenedor va en `docker-<id>.scope` (driver systemd) o sin unit (driver cgroupfs)
  [S alta]. Los campos `_` enviados en el protocolo nativo se rechazan [S alta].
- Cambio: filtro por `_SYSTEMD_UNIT` únicamente (H3). No añadir `_COMM` como «defensa en profundidad»: da falsa sensación y no cuesta nada
  saltárselo. `_UID=0` solo como medición en la prueba real; si resulta estable en Ubuntu y Debian, puede añadirse como segundo AND.
- Prueba (en VM, nunca en producción): los tres escenarios (`logger -t sshd`, copia renombrada, `sshd` pirata en 2222) → v1 cuenta los tres, v2 ninguno;
  un fallo real contra el 22 → v2 cuenta uno. `journalctl -o verbose` de esa línea para anotar `_UID`, `_COMM`, `_EXE`, `_SYSTEMD_UNIT` reales.

### H2 (A) `_SYSTEMD_UNIT` solo es de confianza si PAM saca las sesiones del cgroup de sshd
- Evidencia: con `UsePAM yes` y `pam_systemd`, logind mueve cada sesión a `user-N.slice/session-M.scope` antes de ejecutar nada del usuario; con
  `UsePAM no`, sin `pam_systemd` o en sistemas sin logind, **la shell de cada usuario que entra por ssh se queda en `ssh.service`** y su
  `logger -t sshd` lleva `_SYSTEMD_UNIT=ssh.service` [S alta]. El propio instalador asume `UsePAM yes` (`instalar-en-servidor.sh:26` [V]) pero no lo
  comprueba, y el cliente de un servidor ajeno puede tenerlo en `no`.
- Escenario: servidor de cliente con `UsePAM no`; una web comprometida entra por ssh con la clave de un desarrollador y envenena el journal con
  `_SYSTEMD_UNIT=ssh.service`. El Mac lo persiste 90 días como «IP que te atacó».
- Cambio: **autocomprobación en `recoger.sh`**: el propio script corre en una sesión ssh, así que `grep -qE '/(ssh|sshd)\.service' /proc/self/cgroup`
  dice si las sesiones se aíslan. Emitir en `### intentos` la línea `aislamiento si|no`. Con `no`, el Mac guarda los recuentos como aproximados
  (`intentos_exactos` NULL) y **no persiste IPs** de ese servidor (se cuentan en `meta` como `ips_no_fiables`). `instalar-en-servidor.sh` avisa si
  `sshd -T` da `usepam no` o si `/etc/pam.d/sshd` no incluye `pam_systemd`.
- Prueba: VM con `UsePAM no` → `aislamiento no` y el `logger` sí cuenta; con `UsePAM yes` → `aislamiento si` y no cuenta. Test del Mac: sección con
  `aislamiento no` → ninguna fila en `ips_atacantes`, contador en `meta`.

### H3 (M) Forma exacta del filtro y detección de «sin unit»
- Evidencia: Debian y Ubuntu llevan `ssh.socket` con `Accept=no` [V, `debian/systemd/ssh.socket` de ambos]: con socket o sin él, sshd corre como
  `ssh.service`; `sshd-session` (OpenSSH ≥ 9.8) sigue dentro del mismo cgroup. RHEL/Fedora/Arch: `sshd.service`. Dos valores del mismo campo se
  combinan con OR [V, man `journalctl`].
- Filtro propuesto (sin `-t`, sin `-u`; `-u` añade `UNIT=` de PID 1 y `COREDUMP_UNIT=`, inocuos pero innecesarios):
  ```
  journalctl -q -o short-unix --since=-24h _SYSTEMD_UNIT=ssh.service _SYSTEMD_UNIT=sshd.service
  ```
  Y antes, para distinguir «0 intentos» de «no hay journal de sshd»:
  ```
  fuente=$(t journalctl -F _SYSTEMD_UNIT | grep -xE 'sshd?\.service' | head -n1); echo "fuente ${fuente:-ninguna}"
  ```
  Si `fuente ninguna` (rsyslog sin journal, unit con otro nombre, `Storage=none`), el Mac muestra «sin dato» y no escribe cubos.
- Extracción de la IP: `strnvis` garantiza que el mensaje no lleva saltos de línea ni tabuladores crudos (`LOG_SYSLOG_VIS` = `VIS_CSTYLE|VIS_NL|
  VIS_TAB|VIS_OCTAL`) [V `log.c` master]; el nombre de usuario puede llevar espacios y parecerse a una IP, pero en todas las líneas canónicas la IP
  real va **justo antes del último `port`**. Sustituir «el último campo con pinta de IP» (`recoger.sh:40` [V]) por «el campo anterior al último
  `port`»: más estricto y no depende de que el usuario no contenga `from 203.0.113.9`.
- Prueba: `bash -n`; ejecución con un `journalctl` falso en el PATH que devuelva las cinco formas canónicas más una con usuario
  `"from 203.0.113.9 port 1"`; todas resuelven la IP real.

### H4 (M) El `timeout` de `journalctl` dentro de una tubería produce recuentos parciales mudos
- Evidencia [V]: `t()` es `timeout 5` (`recoger.sh:7`) y el código de salida se pierde en `| awk` (`:34`); si journalctl muere a los 5 s, awk cuenta
  lo que llegó y lo imprime como si fuera todo. Con v2 ese «todo» pasa a ser `intentos_exactos` por hora.
- Escenario: el atacante no necesita tocar sshd; engorda el journal (journald limita a 10 000 mensajes/30 s **por unit**, es decir, unos 300/s
  sostenidos desde la web [S]) para que el escaneo pase de 5 s, o fuerza la rotación (`SystemMaxUse`) y vacía las 24 h de sshd. Con la
  sobrescritura por «valor más reciente» (H9) eso borra horas ya registradas.
- Cambio: `set -o pipefail` local o `PIPESTATUS[0]`; si es 124 → `parcial si` y el Mac trata el histograma como aproximado (no escribe
  `intentos_exactos`, sí picos). Subir el tope de esa orden a 8 s dentro de los 15 s totales. La fusión con `max` (H9) cubre la rotación.
- Prueba: `journalctl` falso que duerme 6 s → la sección sale con `parcial si`; el Mac no toca `intentos_exactos`.

### H5 (A) Los cubos «k horas hacia atrás desde el ahora del servidor» no son horas naturales
- Evidencia [V diseño §A]: k se cuenta respecto al `ahora` del servidor y el Mac lo sitúa en `ok_en - k*3600` «alineado a hora natural del Mac».
  Si el cubo 0 es «los últimos 3 600 s», cada lectura corta las horas por un sitio distinto: la «hora completa» que se sobrescribe cada 5 min es
  en realidad una ventana desplazada `ahora mod 3600`. No es exacto ni idempotente, y un servidor mentiroso ni siquiera necesita mentir.
- Cambio: cubos por **hora natural del reloj del servidor** (`k = int(ahora/3600) - int(ts/3600)`) y emitir además `ahora <epoch>` (ya se calcula,
  `recoger.sh:33`). El Mac calcula `desfase = ok_en - ahora_servidor` (incluye ≤ 20 s de transferencia) y sitúa cada cubo en
  `_hora_de(hora_servidor + desfase)`. Sigue sin **fechar** nada con el reloj del servidor (solo lo usa en relativo), y si `|desfase| > 900 s` o
  `hora_sincronizada no` → aproximado, sin `intentos_exactos`.
- Prueba: fixture con `ahora` 40 min adelantado → los cubos caen en la hora correcta del Mac; con 2 h de desfase → ninguno se escribe como exacto.

## 2. Inyección por nombres y etiquetas de contenedor

### H6 (A) Una etiqueta con `\n### intentos` reabre y reescribe cualquier sección
- Evidencia [V]: `secciones()` vuelve a poner `out[actual] = []` cada vez que ve `### <conocida>` (`servidores_ssh.py:115-118`) y `recoger-root`
  imprime **después** de `### intentos`, `sesiones`, `puertos` y `bastionado` (`recoger.sh:64-66`). Los nombres de contenedor no admiten `\n` ni `\t`
  (Docker exige `[a-zA-Z0-9][a-zA-Z0-9_.-]+`) [S alta], pero **las etiquetas admiten cualquier byte** y las fija quien escribe el `compose` o
  `docker run --label` [S alta]. `com.docker.compose.project`, `.service` y `com.docker.swarm.service.name` están en la lista permitida del diseño.
- Escenario: un cliente despliega en Dokploy un compose con `labels: com.docker.compose.service: "x\n### intentos\ntotal 0\n### bastionado_root\n
  sshd_permitrootlogin no\nfail2ban_activo active\n### fail2ban\nahora 0\ntotal 0\n### sesiones\n"`. Resultado en el Mac: 0 intentos, bastionado
  perfecto, sin sesiones ajenas, fail2ban «activo». Con v2: `h 0 0`… y `ip 203.0.113.9 999999` falsas. Las líneas `ip x n` dentro de
  `contenedores` **no** se leen (el parser está acotado por sección), pero sí en cuanto la sección se reabre.
- Cambio (las dos capas, no una):
  1. Script: cada campo de la plantilla con `{{json ...}}` (`{{json .Name}}\t{{json .State.Status}}\t…\t{{json (index .Config.Labels
     "com.docker.compose.project")}}`): `\n`, `\t`, `"` y los controles salen escapados como JSON [V, `docker --format` documenta `json`]; después
     `cut -b -512 | head -n 200`. El Mac hace `json.loads` por columna y exige `str`, longitud y juego de caracteres cerrados.
  2. Mac: `secciones()` **no reabre**: una sección vista dos veces marca `seccion_repetida` y la lectura entera se descarta y se cuenta (es un
     servidor roto o hostil). Añadir también que `### version` tiene que ser la primera.
- Prueba: fixture de `contenedores` con un valor JSON que contenga `\n### intentos\ntotal 0` → tras `json.loads` el texto sigue en una línea y se
  descarta por juego de caracteres; fixture con la sección cruda repetida → lectura descartada, `meta` con el motivo. Test de mutación sobre el
  `startswith("### ")`.

### H7 (M) Identidad por etiqueta: cualquier contenedor puede suplantar a un servicio de producción
- Evidencia [V diseño §C]: identidad = `swarm.service.name` → `compose.project/service` → nombre. Las tres son etiquetas que fija el cliente del
  socket; nada impide `com.docker.swarm.service.name: dokploy_dokploy` en el compose de un cliente.
- Escenario: el contenedor impostor muere (`exited 137`) → evento «cambió de estado» y `servicios_caidos` sobre la identidad del panel; o al
  revés: el real cae y el impostor `running` lo enmascara (según cuál gane en la fusión).
- Cambio: la etiqueta Swarm solo vale si el contenedor lleva también `com.docker.swarm.task.id` **y** el nombre aparece en `docker service ls`
  (que `recoger-root` ya ejecuta, `:13`); si no, identidad por nombre. Dos contenedores con la misma identidad en una lectura → el peor estado
  **y** un evento `identidad_duplicada` (una vez), nunca «desapareció» mientras haya colisión.
- Prueba: fixture con dos líneas que mapean a la misma clave y estados distintos → estado peor, evento único, sin desaparición.

## 3. Amplificación y presupuestos

### H8 (A) El tope de 200 en el orden de `docker ps -a` ciega el servidor entero
- Evidencia [V]: `docker ps -a` lista los más nuevos primero; el diseño corta a 200 y marca `truncado`, y «desaparición = ausente en dos lecturas
  completas y buenas (no truncada)». Hoy ya se recorta a 100 sin marca (`recoger-root.sh:17`).
- Escenario: un contenedor con el socket crea 200 efímeros cada minuto (o simplemente los deja creados y parados: `docker ps -a` también los
  lista). Producción queda fuera de los 200 en cada lectura → `truncado` permanente → **ninguna** desaparición se detecta nunca en ese servidor, y
  mientras tanto los recursos reales quedan `activo` con estado rancio. Es el ataque más barato del documento.
- Cambio: en el script, ordenar **por antigüedad ascendente** (`docker ps -a --format '{{.CreatedAt}}\t{{.ID}}' | sort | head -n 200`: los de
  producción llevan semanas, los efímeros minutos) y emitir `truncado <n_total>`. En el Mac, una lectura truncada sigue actualizando lo presente
  y genera **un** evento `inventario_truncado` fuera de presupuesto; si tres lecturas seguidas vienen truncadas, el estado del servidor pasa a
  ámbar con motivo «inventario incompleto», que es lo honesto.
- Prueba: `docker` falso con 250 ids, los 50 más nuevos «basura» → la salida contiene los 200 antiguos y `truncado 250`.

### H9 (A) El presupuesto de 100 eventos/día lo agota el atacante en el primer ciclo
- Evidencia [V diseño §D]: ≤ 100 eventos por servidor y día, excedente descartado. «Apareció» se genera en la primera lectura; «desapareció» tras
  dos ausencias.
- Escenario: 50 contenedores que nacen y mueren cada 10 min → 100 «apareció» antes de las 00:10; a las 03:00 cae la base de datos de
  producción y el evento se descarta. Variante: un contenedor que alterna `running/exited` cada ciclo → 2 eventos por ciclo sin crear nada.
- Cambio: (1) **simetría**: «apareció» exige dos lecturas buenas seguidas, igual que «desapareció»; lo que vive menos de un ciclo nunca es un
  recurso (migraciones, builds, `compose run`); (2) presupuestos **por tipo de evento** (p. ej. 30/30/30/10) y **por identidad y día** (≤ 5), de
  modo que un recurso inestable se queda sin voz pero no deja mudo al resto; (3) al agotarse un presupuesto, un evento `exceso` y un contador en
  `meta`; (4) un recurso con > 5 cambios/día pasa a estado `inestable` (no genera más eventos ni dispara incidencia, ver H11).
- Prueba: 200 identidades efímeras + 1 desaparición real → 0 «apareció», 1 «desapareció», contador de exceso en `meta`.

### H10 (M) 500 recursos por servidor: sin retención ni política de expulsión, y con datos personales
- Evidencia [V diseño §D]: tope de 500 y «excedente descartado»; `eventos` 180 días; `recursos` sin caducidad, con `etiqueta` personal.
- Escenario: el atacante crea 500 nombres distintos una vez (quedan en `recursos` como inactivos para siempre); desde entonces cualquier recurso
  nuevo legítimo se descarta. Si en cambio se expulsa «el inactivo más antiguo», borra el historial legítimo. Y 500 nombres por servidor sin
  caducidad contradice el espíritu de D-C.
- Cambio: nunca expulsar activos; expulsar inactivos por `ultima` ascendente **solo entre los vistos en < 3 lecturas** antes que los demás;
  retención de inactivos (90 días) y de sus eventos; `--olvidar-recursos` ya previsto. Los nombres nunca en `log()`.
- Prueba: 600 altas en un día → 500 filas, contador en `meta`; con reloj +91 días los inactivos desaparecen; grep de bytes sin nombres.

### H11 (B) Un contenedor inestable fuerza el modo incidencia indefinidamente
- Evidencia [V]: `servicios_caidos` entra en `tarjetas` → `estado_vivo` ámbar → lectura cada 60 s mientras dure (`servidores_ciclo.py:53-59`).
  Ya ocurre con v1; v2 añade `RestartCount` y salud como fuentes de ámbar.
- Cambio: el estado `inestable` de H9 no cuenta como ámbar para la incidencia; solo cuentan los recursos con identidad estable vistos ≥ 1 h.

## 4. `docker inspect` bajo sudo

### H12 (M) «Salud» debe ser `.State.Health.Status` y nada más
- Evidencia [V diseño §B]: dice «salud (`none` si no hay healthcheck)». `.State.Health` también contiene `.Log[]` con la **salida** de cada
  comprobación (a menudo `curl -v` con cabeceras `Authorization`, cadenas de conexión, trazas). Lo mismo con `.Config.Labels` completo (Traefik,
  `config_files`, rutas) y con `.HostConfig`.
- Cambio: lista blanca **literal** de rutas de plantilla en el script y un test en el repo que lea `recoger-root.sh`, extraiga la plantilla y
  falle si aparece cualquier cosa fuera de `{Name, State.Status, State.Health.Status, RestartCount, State.ExitCode, State.StartedAt,
  HostConfig.RestartPolicy.Name, index .Config.Labels "<tres claves>"}` o si aparece `Env|Cmd|Mounts|Image|Log|Args|Entrypoint|NetworkSettings`.
- Prueba: ese test, más ejecución con un `docker` falso que devuelva un `.Log` con un secreto → no sale en la salida.

### H13 (B) Ejecutar `docker` como root con nombres hostiles: bajo, con dos salvedades
- Evidencia [V]: `HOME=/root DOCKER_CONFIG=/root/.docker` (`recoger-root.sh:7`); sudoers con `""` sin argumentos (`instalar-en-servidor.sh:40`);
  los ids de `docker ps -aq` son hexadecimales, así que no hay inyección de argumentos; las plantillas de Go no ejecutan nada. `env_reset` de
  sudo deja fuera `DOCKER_HOST` [S alta].
- Salvedades: (1) `docker inspect` de un contenedor cuyo `containerd-shim` está colgado puede no responder; `t` lo corta a 3 s y **toda** la sección
  sale vacía: aceptable, pero debe marcarse `truncado` y no «sin contenedores»; (2) `docker ps -aq` con miles de contenedores: recortar a 200
  **antes** de pasar ids a `inspect` (`head -n 200`), no después.
- Prueba: `docker` falso que duerme 4 s en `inspect` → sección vacía con marca; el Mac no genera desapariciones.

### H14 (B) Valores de etiquetas: no secretos, pero sí texto libre
- Evidencia: el juego cerrado `[A-Za-z0-9._-/]` ≤ 64 y «descartar y contar» [V diseño §C] cierra la fuga por la base. El `/` del juego es
  necesario solo para `project/service`: validar las partes por separado y componer en el Mac, no aceptar `/` en la entrada.
- Nada que cambiar salvo lo anterior; comprobar que el motivo de descarte en `meta` nunca incluye el valor.

## 5. Histograma: qué límites impone el Mac a un servidor mentiroso

### H15 (A) «Hora completa = valor más reciente» permite borrar el pasado
- Evidencia [V diseño §A]: idempotente por sobrescritura. Un servidor comprometido (o un reinicio con journal volátil, o la rotación de H4)
  envía ceros para las 23 horas anteriores y el historial de la madrugada del ataque desaparece del Mac. Es exactamente el dato que un atacante
  querría borrar, y el Mac es el único sitio donde debería sobrevivir.
- Cambio (límites concretos):
  1. **Fusión con `max`** por hora completa (monótona e idempotente): un mentiroso solo puede inflar, nunca borrar; la rotación y el reinicio no
     pierden nada. La hora en curso también con `max` (en lugar de «parcial»): es la misma propiedad.
  2. **Ventana escribible**: solo horas en `[_hora_de(ok_en) - 25 h, _hora_de(ok_en)]`; cubos fuera (k > 23, o desfase que los saque) se descartan.
  3. **Coherencia**: `k` ∈ 0..23 sin repetir, `n` ≤ 1 000 000, `Σ h == total` y `Σ ip ≤ total`; si no cuadra, se descarta **la sección** `intentos`
     (no la muestra entera: hoy un `FueraDeRango` tira todas las métricas, `servidores_historico.py:268-272` [V], y para v2 eso convierte una
     línea rara en supresión de todo el historial).
  4. Exactos solo con `fuente` unit, `aislamiento si`, `parcial no` y `|desfase| ≤ 900 s`; si no, NULL y picos como en v1.
- Prueba: dos lecturas, la segunda con ceros → los valores de la primera sobreviven; `k=24`, `k` repetido, `Σ≠total` → sección descartada,
  resto de la muestra guardado; `mantener` con reloj +100 días no toca `intentos_exactos` (no es dato personal).

### H16 (M) Las 12 IPs: más superficie para los 90 días
- Evidencia [V diseño §A]: 12 líneas `ip`; el Mac recorta tras excluir propias. Con el filtro bueno, las IPs solo pueden ser de quien de verdad
  conectó al sshd real: el atacante local que quiera incriminar a un tercero no puede (H1). Pero con `aislamiento no` sí (H2), y entonces 12 es
  peor que 4.
- Cambio: ya cubierto por H2 (no persistir IPs con `aislamiento no`). Mantener `MAX_ALTAS_DIA` y `max_24h` monótono (`:215-238` [V]).

## 6. Certificados

### H17 (B) `notAfter` no es manipulable tras un handshake verificado; sí acotarlo y rotularlo
- Evidencia [V]: `_HTTPSFijo` fija la IP y pasa `server_hostname=self.host` (`servidores_externo.py:36-38`) con `create_default_context()`
  (verificación de cadena y de nombre). Un servidor solo puede presentar un certificado que una CA haya firmado para ese nombre; `notAfter` queda
  dentro de lo que las CAs permiten (≤ 398 días hoy, bajando a 47 en 2029 [S]). Caducado → falla el handshake → rama «certificado» (`:69-70`).
- Cambio: parsear con `ssl.cert_time_to_seconds`; `cert_dias` acotado a `[-36500, 36500]` con `CHECK`; `getpeercert()` vacío (sesión reanudada;
  improbable con un contexto nuevo por medida) → NULL. Si el reloj del Mac está mal, el handshake ya falla: no hay caso «negativo» que guardar.
- Prueba: test con un servidor TLS local y certificado autofirmado de prueba → error, sin `cert_dias`; fixture de `notAfter` del año 9999 → acotado.

### H18 (M) Hoja frente a cadena, SNI y varias IPs: riesgo de rotulado, no de ataque
- Evidencia: `getpeercert()` solo da la hoja; una intermedia que caduque antes rompe la cadena antes del `notAfter` de la hoja [S media, poco
  frecuente]. `ip_publica` devuelve `ips[0]` (`:26` [V]): con varias IPs (CDN, doble pila) se mide un solo frente, que puede cambiar entre lecturas.
- Cambio: en Python ≥ 3.13 `get_verified_chain()` da la cadena en DER [V docs]; sin dependencia para parsear DER, **no** merece la pena: rotular
  «días del certificado del servidor» y documentar la limitación. Guardar el mínimo por servidor como propone el diseño está bien; el por-web se
  calcula en vivo al abrir la tarjeta.
- Prueba: ninguna nueva; es texto de pantalla.

## 7. Transversal

### H19 (A) v2 roza el `TOPE_BYTES` de 64 KiB y pasarse borra la lectura entera
- Evidencia [V]: `TOPE_BYTES = 64 * 1024` y, si se excede, `ejecutar` devuelve «salida demasiado grande» sin texto (`servidores_ssh.py:14,99-104`).
  200 contenedores × (nombre + 3 etiquetas de hasta 64 + resto) ≈ 50–60 KB, más 24 `h`, 12 `ip`, `sesiones`, `bastionado`: el margen es nulo.
- Escenario: un cliente con etiquetas de 300 bytes (Traefik las usa) empuja la salida por encima del tope en cada ciclo → ni intentos, ni
  sesiones, ni nada: muestras de estado `error` durante meses sin que nadie sepa por qué.
- Cambio: tope por línea en el script (`cut -b -512`), tope de contenedores a 150 o `TOPE_BYTES` a 256 KiB **con** topes por sección en el Mac
  (líneas máximas por sección, descartando la sección y no la lectura). Añadir en la tarjeta el motivo «salida demasiado grande» como error visible.
- Prueba: salida sintética v2 con 200 contenedores de etiquetas máximas → cabe; 1 contenedor con etiqueta de 10 KB → la línea se recorta y la
  lectura se interpreta.

### H20 (M) `interpretar` no mira `### version`; v2 y v1 deben decidirse por sección, no por número
- Evidencia [V]: `secciones()` descarta `version` del parseo salvo para conservarla; `interpretar` no la lee (`:145-199`). El diseño apoya
  «acepta v1 y v2» en `### version 2`.
- Cambio: decidir por **presencia y forma** de cada sección (si hay líneas `h`, es v2 para intentos; si la sección `contenedores` tiene columnas
  JSON, es v2), nunca por el número, que un servidor puede poner a 2 con cuerpo v1 o al revés. Tests cruzados: v1 con `version 2`, v2 con `version 1`.

## Protocolo de prueba del filtro sin tocar producción
1. Dos VM locales (Ubuntu 24.04 y Debian 12/13; `multipass` o UTM), `openssh-server`, un usuario sin privilegios `web` y el instalador real.
2. Captura de referencia: un fallo real contra el 22 desde el host → `journalctl -o verbose` de las líneas: anotar `_SYSTEMD_UNIT`, `_COMM`,
   `_EXE`, `_UID`, `_PID`, `_TRANSPORT` para `sshd` y para `sshd-session`, con `ssh.socket` activo y desactivado. Ese registro fija el filtro.
3. Envenenamientos como `web`: `logger -t sshd …`; `cp /usr/bin/logger ~/sshd && ~/sshd -t sshd …`; `sshd` pirata en 2222 con usuario inexistente;
   mensaje con `\n` y `\t`. Esperado: `recoger.sh` v1 cuenta, v2 no; `aislamiento si`.
4. `UsePAM no` en la VM → `aislamiento no`, el `logger` cuenta, el Mac no persiste IPs. Volver a `yes`.
5. `journalctl --vacuum-time=1s` entre dos lecturas → el Mac conserva las horas ya registradas (H15).
6. Los binarios falsos (`journalctl`, `docker`) del diseño para las variantes de tiempo agotado, `truncado`, etiquetas con `### `, 250 contenedores.

## Cambios al diseño, por orden
1. Filtro solo por `_SYSTEMD_UNIT` (dos valores), `fuente`, `aislamiento` por `/proc/self/cgroup`, `parcial` por `PIPESTATUS`, IP = campo anterior
   al último `port` (H1–H4).
2. Cubos por hora natural del servidor + `ahora`; el Mac corrige el desfase y exige `|desfase| ≤ 900 s` (H5).
3. `max` por hora, ventana de 25 h, coherencia `Σh == total`, descartar sección y no muestra (H15).
4. `{{json}}` por campo, `cut -b -512`, parser que no reabre secciones, `### version` primera (H6, H19, H20).
5. Orden por antigüedad y `truncado <n>`; lectura truncada actualiza presentes y avisa; tres truncadas → ámbar (H8).
6. «Apareció» con dos lecturas; presupuestos por tipo y por identidad; estado `inestable` fuera de incidencia (H9, H11).
7. Identidad Swarm solo con `task.id` y presente en `service ls`; colisión → evento y sin desaparición (H7).
8. Retención de recursos inactivos; expulsión que proteja activos e históricos (H10).
9. Lista blanca literal de la plantilla con test en el repo; `State.Health.Status` y nunca `.Log` (H12).
10. `cert_dias` acotado, `cert_time_to_seconds`, rotulado «del certificado del servidor» (H17, H18).

## Supuestos
- `_COMM` refleja el nombre del fichero ejecutado y `prctl(PR_SET_NAME)` lo cambia sin privilegios (alta; semántica de `/proc/PID/comm`).
- Con `UsePAM yes` + `pam_systemd` las sesiones ssh, incluida la de `command=`, salen de `ssh.service`; sin ello se quedan dentro (alta).
- Los contenedores Docker no comparten cgroup con `ssh.service` con ningún driver (alta).
- Los nombres de contenedor y de servicio Swarm rechazan `\n`/`\t`; las etiquetas no (alta; validación de Docker/compose no comprobada aquí).
- El monitor de OpenSSH escribe en syslog las líneas del hijo sin privilegios, con UID 0 y ya escapadas (alta en master; media en 8.x).
- `env_reset` de sudo está activo en los servidores (alta; es el valor por defecto de Debian/Ubuntu).
- La política de vida máxima de certificados públicos (398 → 47 días) es la del CA/B Forum vigente (media; no afecta al diseño).

Status: DONE_WITH_CONCERNS
Summary: El diseño va bien encaminado pero, tal cual, un proceso sin privilegios o un contenedor de cliente puede reescribir cualquier sección
del protocolo con una etiqueta, cegar las desapariciones de todo el servidor con 200 contenedores efímeros, agotar el presupuesto de eventos en un
ciclo y borrar del Mac las horas de un ataque mediante la sobrescritura «más reciente». Los cambios concretos (filtro solo por `_SYSTEMD_UNIT` con
autocomprobación del aislamiento PAM, `{{json}}` y parser sin reapertura, orden por antigüedad, histéresis simétrica con presupuestos por tipo e
identidad, fusión con `max` y acotación de `TOPE_BYTES`) son pequeños y verificables con binarios falsos y una VM; nada exige tocar producción.
