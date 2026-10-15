# Pre-mortem — Fase 3.3a (`recoger` v2, contenedores por SSH, caducidad de certificados)

Fecha: 2026-10-15. Revisor independiente, solo lectura. Objeto: `plans/261013-2350-sqlite-historico/fase3-3a-diseno.md` (en adelante «diseño»),
contrastado con `fase3-plan-monitorizacion.md`, `fase3-diseno.md` y el código de la rama `feat/fase1-ingesta-hermes`.
Leyenda: **[V]** verificado leyendo el código o el documento; **[S]** supuesto (comportamiento de systemd/Docker/OpenSSH no comprobable sin ejecutar
contra un servidor, que estaba fuera de alcance).

## Escenario del pre-mortem
Tres meses después de fusionar la 3.3a: el gráfico de «intentos exactos por hora» enseña ceros en las horas previas a cada reinicio de un servidor Debian y
valores que bajan solos de un día para otro; la línea de tiempo de un Dokploy está llena de «cambió de estado» alternos del mismo servicio y, a media mañana, ya
no aparece ningún evento más (tope diario agotado); un host grande pasó a «error: salida demasiado grande» el día que se reinstaló el script y dejó de
monitorizarse entero; otro Mac con la versión anterior de la app marca en rojo contenedores que terminaron bien; los nombres de los proyectos de un cliente
siguen en `eventos` y en la caché meses después de quitar el servidor; y el certificado caducado de una web no aparece en el «mínimo de días».
Abajo, por qué pasa cada cosa y qué cambiar en el diseño antes de codificar.

---

## Críticos

### C1. El histograma por hora no es exacto y además se «corrige» hacia abajo (reinicio, journal volátil, rotación)
- **Evidencia.** Diseño l. 27-30: cubos `k` = horas hacia atrás **desde el `ahora` del servidor** (ventanas deslizantes) y el Mac los «alinea a hora natural»
  con `ok_en - k*3600`; «una hora completa se sobrescribe con el valor más reciente». [V] `recoger.sh:33-34` usa `ahora=$(date +%s)` y `--since "-24h"`.
  [V] `servidores_historico.py:195-196`: `_hora_de(ts) = ts - ts % 3600` (hora UTC alineada).
- **Escenario 1 (mal etiquetado).** Lectura a las 10:37: el cubo `k=0` es (09:37, 10:37], y se guarda en la hora 10:00 aunque 23 de sus 60 minutos
  son de la hora 9. Cada ciclo (5 min) la ventana se desplaza y la «misma» hora recibe otra ventana distinta: el valor guardado depende del minuto del último
  ciclo, no de la hora. No es exacto ni idempotente salvo que el ciclo cayera en :00.
- **Escenario 2 (bajada a cero).** En Debian 12 el journal es volátil por defecto (sin `/var/log/journal`, rsyslog al lado) [S]; tras un reinicio,
  `journalctl --since -24h` solo ve desde el arranque y los cubos previos salen 0. Con «se sobrescribe con el valor más reciente», las 23 horas anteriores
  (posiblemente las del ataque que motivó el reinicio) pasan a 0. Igual con journal persistente pero pequeño (`SystemMaxUse`/`RuntimeMaxUse` en un VPS pequeño
  con fuerza bruta: el journal rota antes de 24 h) y con el limitador de journald (`RateLimitBurst`) [S]: los cubos viejos se reducen y pisan lo bueno.
- **Escenario 3 (parcial que se queda).** La hora en curso se escribe «como parcial» pero el diseño no dice cómo se marca; si el Mac se apaga a las H:10 y
  vuelve 30 h después, la hora H queda con 10 minutos de datos y aspecto de definitiva.
- **Cambio propuesto.**
  1. El servidor agrupa por **hora de época alineada** (`int(ts/3600)`) y manda su `ahora` (época) y `cobertura <ts del primer registro legible>`
     (p. ej. primera línea de `journalctl -q -o short-unix | head -n1`) y si el journal es persistente. Cubos no cubiertos: `h <hora> -` (desconocido, no 0).
     25 cubos (las dos puntas son parciales).
  2. El Mac calcula el desfase `ok_en - ahora_servidor`; si |desfase| > 5 min, guarda el histograma marcado `reloj_dudoso` o lo desplaza por horas enteras
     de forma explícita. (Esto sustituye la frase «nunca usa el reloj del servidor», que ya es falsa: los cubos `k` también dependen de él.)
  3. Columna `intentos_exactos` + `intentos_completa` (0/1). Regla: una hora **completa y cubierta** se congela con `MAX(guardado, nuevo)`; nunca baja;
     nunca se escribe una hora no cubierta. La parcial se sobrescribe hasta que pasa a completa.
  4. Validar en el Mac: exactamente los cubos esperados, sin repetidos, `total == suma` y, si no, descartar **solo** el histograma (contar en `meta`).

### C2. La identidad «estable» colapsa varios contenedores en un recurso: estados que oscilan, falsos «caídos» y bucles invisibles en Swarm
- **Evidencia.** Diseño l. 44: identidad = `swarm.service.name`, si no `compose.project/compose.service`, si no el nombre; l. 39: v2 «sustituye» a
  `servicios`/`contenedores`. [V] `recoger-root.sh:12-17` hoy usa `docker service ls` (réplicas `n/m`) como verdad para Swarm y solo cae a contenedores si no hay
  Swarm; [V] `servidores_ssh.py:202-221` y `servidores_estado.py:58-60` derivan de ahí la tarjeta «servicios» y `servicios_n/servicios_caidos` de `muestras`.
- **Escenarios.**
  - `docker ps -a` lista también las **tareas históricas** de Swarm (por defecto se guardan varias por servicio [S]): la tarea viva `running` y 4 `exited (1)`
    de despliegues fallidos comparten `com.docker.swarm.service.name`. El estado del recurso depende de cuál se procese último → «cambió de estado» en cada
    lectura y servicio «caído» permanente.
  - Réplicas >1, `compose --scale` y contenedores `docker compose run` (etiqueta `oneoff`) chocan igual con `project/service`.
  - Un servicio Swarm sin tareas programables (imagen que no baja, sin recursos) **no tiene contenedor**: desaparece de la vista por contenedores aunque
    `service ls` diga `0/1`. Si v2 deja de emitir `servicios`, se pierde la única señal correcta.
  - Swarm no reinicia contenedores: crea tareas nuevas; `RestartCount` se queda en 0 [S, comportamiento conocido de Swarm]. La regla de bucle (l. 45)
    **nunca** se dispara en las aplicaciones de Dokploy, que son servicios Swarm (plan, l. 49).
- **Cambio propuesto.** Mantener `docker service ls` (réplicas deseadas/en marcha) como fuente para Swarm. El recurso agrega sus contenedores: `n_total`,
  `n_running`, peor estado **entre las tareas actuales** (las históricas no cuentan para el estado), y añade a la identidad `com.docker.compose.container-number`
  si se quiere granularidad. Excluir u marcar `com.docker.compose.oneoff=True`. Bucle en Swarm = nº de contenedores nuevos de ese servicio con salida ≠ 0
  entre lecturas (o `service ps` con `desired-state`), no `RestartCount`.

### C3. Una salida cortada se toma por lectura buena y completa: «desapareció» falso
- **Evidencia.** [V] `servidores_ssh.py:105`: si `ssh` sale con código ≠ 0 pero el texto contiene `### version`, la lectura es **buena**.
  [V] `recoger.sh:65`: `timeout 15 sudo -n …/recoger-root`. [V] `recoger-root.sh`: ocho llamadas con `timeout 3` (hasta 24 s) dentro de esos 15 s, antes
  de añadir el bloque de ≤ 8 s del diseño. [V] `servidores_ssh.py:13`: tope total 20 s con conexión incluida, frente a `recoger.sh` con `journalctl` y dos
  `apt list` de hasta 5 s cada uno. Diseño l. 52: «lectura completa = no truncada, sección presente», sin decir cómo se detecta un corte a mitad.
- **Escenario.** Con el demonio Docker lento (justo en un incidente), `timeout` mata `docker inspect` tras imprimir 120 de 180 líneas, o el `timeout 15`
  mata `recoger-root` a mitad de la sección. La sección está «presente», no lleva `truncado` y le faltan 60 contenedores; si pasa dos ciclos seguidos
  (lentitud sostenida), se generan 60 «desapareció». Lo mismo con un corte de red durante la salida (`ServerAliveCountMax`), que deja código 255 y texto con
  `### version`.
- **Cambio propuesto.** Cada sección nueva lleva cabecera `n <esperados>` (de `docker ps -aq | wc -l` antes de recortar) y cierre `fin <emitidos> <código>`;
  el script termina con `### fin`. El Mac solo considera «completa» una sección con cierre, recuento igual y código 0. Rehacer el presupuesto de tiempo: suma
  de topes de `recoger-root` < tope de `sudo` < tope del Mac menos conexión, con tests que lo comprueben; poner `contenedores` **después** de lo barato o
  darle su propio `timeout` global en el script.

---

## Altos

### A1. El tope de 64 KB no recorta: tira la lectura entera del servidor
- **Evidencia.** [V] `servidores_ssh.py:14,99,103-104`: si se pasa de `TOPE_BYTES`, `ejecutar` devuelve `""` y error «salida demasiado grande»: se pierden
  carga, memoria, disco, intentos, todo. El diseño limita a 200 contenedores (l. 38) pero no la longitud de los campos en el servidor (el saneado a ≤64
  caracteres es en el Mac, l. 42).
- **Escenario.** 200 líneas × ~250-330 B (nombre de tarea Swarm, `StartedAt` con nanosegundos, tres etiquetas, política) ≈ 50-66 KB, más `who`, puertos,
  `service ls` (60 líneas) y, si se mantienen, las secciones v1 → el host grande pasa a rojo «error» en cada ciclo el día que se reinstala el script.
- **Cambio.** Recortar cada campo en el servidor (`cut -c1-64` o `printf '%.64s'`), tope de bytes por sección (`head -c`), estimación con el peor caso en
  un test, y en el Mac o bien subir `TOPE_BYTES` (p. ej. 256 KB, el tope sigue protegiendo la memoria) o bien conservar las secciones completas recibidas
  antes del corte en lugar de tirarlo todo.

### A2. Compatibilidad v1/v2 sin mecanismo: mismo nombre de sección, versión que nadie lee, scripts desparejados y otros Macs
- **Evidencia.** [V] `### version` no se lee en ningún sitio del Mac (`grep` en `servidores_*.py`: solo aparece en `ejecutar` como prueba de vida).
  [V] `recoger-root.sh` no emite versión propia; [V] `instalar-en-servidor.sh:34-36` instala los dos scripts por separado bajo `set -e`. [V] `instalar-en-servidor.sh:48`
  admite varias claves («otro Mac puede tener la suya»). Diseño l. 34-39 reutiliza el nombre `contenedores` con otro formato.
- **Escenarios.** (a) `recoger.sh` v2 + `recoger-root` v1 (instalación a medias o `sudo` antiguo): el Mac ve «v2» y aplica el analizador nuevo a líneas v1;
  si las rechaza todas y lo trata como «0 contenedores», todo «desaparece». (b) Otro Mac con la app anterior lee la `contenedores` v2 con `_servicios` v1:
  `partes[2]` ya no es `Exited (0)…` (`servidores_ssh.py:218`), así que los contenedores que terminaron bien salen **caídos** y el servidor en rojo.
- **Cambio.** Secciones nuevas con nombre nuevo (`docker2`, `intentos_h`), `### version_root 2` propio, y conservar `servicios`/`contenedores` v1 (al menos
  `servicios`) mientras existan Macs antiguos; el Mac decide el formato **por sección**, no por la versión global. Añadir las secciones nuevas a
  `SECCIONES` (`servidores_ssh.py:15-16`; sin eso se ignoran en silencio [V]).

### A3. El filtro del journal confunde «0 intentos» con «sin fuente» y `_SYSTEMD_UNIT` solo no prueba que escribió sshd
- **Evidencia.** Diseño l. 23-26. La marca `fuente ninguna` exige saber que la unit no existe, y el diseño no dice cómo.
- **Escenarios.** [S] Con activación por socket con `Accept=yes` (unidades `ssh@N-…service`), con sshd fuera de systemd (contenedores LXC, OpenRC) o con
  otro nombre de unit, `journalctl _SYSTEMD_UNIT=ssh.service …` devuelve vacío sin error → `total 0`, verde y gráfico a cero: el falso «todo tranquilo»
  que el diseño quería evitar. [S] Sin `pam_systemd`, los procesos de una sesión de usuario siguen en el cgroup de `ssh.service`: un `logger` de cualquier
  usuario conectado lleva `_SYSTEMD_UNIT=ssh.service`, así que la promesa «solo líneas que sshd escribió de verdad» (l. 7) no se cumple.
- **Cambio.** Combinar campos de confianza (campos distintos = AND): unit **y** `_EXE` en una lista cerrada (`/usr/sbin/sshd`, `…/sshd-session`), con
  `_UID=0` si la prueba real lo confirma; usar `-u 'ssh@*'` para las unidades plantilla. Emitir además `lineas_unit N` (cualquier línea de la unit en 24 h)
  y `lineas_tag N` (método v1): si la unit no da ninguna línea pero el tag sí, `fuente dudosa`; si ninguna, `fuente ninguna`. La «prueba real en un servidor
  propio» (l. 25, 65) tiene que cubrir Ubuntu 24.04 (`sshd-session`), Debian 12 (volátil) y uno con socket.

### A4. Topes de eventos que se agotan con ruido: alta inicial, oscilaciones y efímeros
- **Evidencia.** Diseño l. 50-52: ≤100 eventos por servidor y día, «excedente descartado y contado»; eventos en cada transición.
- **Escenarios.** (a) Primera lectura tras actualizar: cada contenedor existente genera «apareció» → un host de 150 contenedores agota el día en un ciclo.
  Igual tras `--olvidar-recursos`. (b) Un servicio en bucle alterna `running/restarting`; el servidor entra en modo incidencia (cada 60 s,
  `servidores_ciclo.py:21,53-59` [V]) → cientos de «cambió de estado» y, a las 2 h, ningún evento más ese día, aunque caiga otro servicio. (c) Contenedores
  de construcción o tareas puntuales: par «apareció/desapareció» en cada despliegue.
- **Cambio.** Inventario inicial como **línea base sin eventos** (un único evento resumen); coalescer por recurso (un recurso «en bucle» absorbe sus cambios
  de estado hasta que se estabiliza); tope por recurso además del global, con prioridad (desapariciones y caídas antes que apariciones); histéresis también
  para «apareció»; ignorar `oneoff` y salidas `exited (0)` de tareas puntuales como cambio grave.

### A5. Datos personales fuera del perímetro previsto (D-C): `clave`, eventos, caché, copias y un borrado que nadie llama
- **Evidencia.** Diseño l. 48-50: solo `etiqueta` es personal; `clave` = nombre del servicio/proyecto (l. 44), es decir, **el mismo dato**, y vive en
  `recursos` y en `eventos` 180 días. [V] `borrar_servidor` (`servidores_historico.py:358`) no se llama desde ningún sitio (`grep` en `infobar/` y
  `costbar.py`); quitar un servidor de la config solo lo marca `retirado_en` y le borra las IPs (`servidores_historico.py:302-308`). [V] La caché
  `logs/servidores.json` guarda indefinidamente el último dato bueno (`servidores_ciclo.py:169-188`); `podar_ips` solo quita IPs. [V] `copia_sin_ips`
  (`servidores_historico.py:120-146`) vacía `ips_atacantes` pero no tocaría `recursos`/`eventos` en una migración 3.
- **Escenario.** Se quita el servidor de un cliente: sus nombres de proyecto siguen en `recursos.clave`, `eventos.clave`, la caché y, tras la siguiente
  migración, en una copia de 30 días. `--olvidar-recursos` no lo arregla si solo borra `etiqueta`.
- **Cambio.** `clave` = HMAC de la identidad con una sal local (en el almacén de secretos) y `etiqueta` como único texto; `eventos` referencia el recurso,
  sin texto. Purga de recursos/etiquetas del servidor **retirado** dentro de `mantener` (no depender de `borrar_servidor`). `--olvidar-recursos` también
  limpia la caché JSON. `copia_sin_ips` → copia «sin datos personales» (IPs y etiquetas). Clasificar en `CLASES_SERVIDORES` todas las columnas nuevas.

---

## Medios

### M1. Caducidad de certificados: el caducado desaparece del mínimo
- **Evidencia.** Diseño l. 56-57. [V] `servidores_externo.py:33,62-69`: contexto por defecto (verificación obligatoria) y `c.close()` justo tras la
  respuesta.
- **Escenario.** Con un certificado caducado el handshake falla, no hay `getpeercert()`, `cert_dias` es None y el «mínimo entre las webs» muestra el de
  otra web (p. ej. 60 días). El «negativo» que menciona el diseño no puede ocurrir. Además, si no se lee `c.sock.getpeercert()` **antes** de `close()`, no
  hay fecha. Y con SSH caído, `registrar` escribe una fila de estado sin métricas (`servidores_historico.py:256-264` [V]): la caducidad, que no depende
  de SSH, se pierde.
- **Cambio.** Error de certificado ⇒ `cert_dias = 0` (o columna `webs_cert_mal`); leer la fecha antes de cerrar; guardar `cert_dias` también en las filas
  de estado.

### M2. Migración 2: no hay bajada; la app anterior deja de escribir sin aviso visible
- **Evidencia.** [V] `servidores_historico.py:96-99`: con `user_version` mayor que la conocida, `ErrorAlmacen`; [V] `servidores_registro.py:93-96`: un
  `log()` y reintento cada hora. [V] Solo existe `MIGRACIONES = {1: …}`; no hay mecanismo de bajada. Los lectores `mode=ro` no comprueban versión
  (`sqlite_base.py:40-43` [V]) y siguen funcionando.
- **Escenario.** Se revierte la app a 3.1: el historial de **todos** los servidores se detiene (no solo recursos) y solo queda una línea de log por hora.
  Restaurar la copia previa pierde las IPs (la copia es sin IPs, a propósito). Si la migración falla a medias (disco lleno), cada reintento horario hace
  otra copia completa (`_migrar`, l. 102-103).
- **Cambio.** Migración puramente aditiva y documentar la bajada real: «borrar tablas/columnas nuevas y poner `user_version=1`» como orden explícita
  probada en el ensayo; no repetir la copia si ya hay una de hoy; superficie visible (no solo log) cuando la base es «más nueva».

### M3. Histéresis por número de lecturas, no por tiempo
- **Evidencia.** Diseño l. 52; [V] cadencia 300 s / 60 s en incidencia (`servidores_ciclo.py:20-21`).
- **Escenario.** En incidencia, «dos lecturas» son ~60-120 s: un `compose up` que recrea un contenedor lento o un servicio Swarm que tarda en converger da
  un «desapareció» y un «apareció» espurios.
- **Cambio.** Desaparición = ausente en ≥2 lecturas completas **y** ≥10 min; lo mismo para apariciones de recursos nuevos. Definir si recursos/eventos
  siguen la franja de 270 s de `muestras` o cada lectura (el diseño no lo dice; `registrar` usa la franja solo para `muestras`, `servidores_historico.py:274`).

### M4. Regla de bucle frágil y con reloj del servidor
- **Evidencia.** Diseño l. 45: «`RestartCount` crece entre dos ciclos buenos y `StartedAt` es reciente».
- **Escenario.** Un solo OOM entre dos lecturas cumple la regla → «en bucle». «Reciente» compara `StartedAt` (reloj del servidor) con el del Mac: un
  servidor desfasado da falsos o ningún bucle, en contra de la regla del propio diseño (l. 27). Un contenedor recreado baja `RestartCount` (delta negativo).
- **Cambio.** Bucle = ΔRestartCount ≥ 3 (o `StartedAt` distinto) en ≥ 2 intervalos seguidos; comparar `StartedAt` con el `ahora` del servidor que ya se
  propone en C1; delta negativo = recreación, no anomalía.

### M5. Un dato imposible en otra sección borra la lectura de contenedores
- **Evidencia.** [V] `servidores_historico.py:268-272`: `FueraDeRango` en `muestra_de` deja `m = None` y no se anota nada; [V] `servidores_datos.py:101,125`
  lanza por `total`/`hora`/cuenta de una IP > 1 000 000.
- **Escenario.** Un `total 10^18` en `intentos` (servidor raro u hostil) hace que esa lectura no cuente para recursos, y si se repite, ninguna lectura es
  «buena» y el inventario se congela.
- **Cambio.** Validación y descarte **por sección**: recursos, histograma, certificados y métricas se anotan de forma independiente.

### M6. Formato de Docker: encuadre por tabuladores, valores ausentes y errores de plantilla silenciosos
- **Evidencia.** Diseño l. 35-38, 42-43.
- **Escenarios.** [S] Los valores de etiquetas admiten `\t` y `\n`: una etiqueta rara parte una línea en dos y crea un contenedor fantasma (y descuadra el
  recuento de C3). [S] `{{.Config.Labels.x}}` imprime `<no value>` si falta; `StartedAt` de un contenedor creado y nunca arrancado es `0001-01-01T00:00:00Z`
  (legible, pero no es una fecha); un campo que no existe en una versión de Docker/Podman hace fallar **toda** la plantilla → sección vacía → por diseño
  «se conserva lo anterior» indefinidamente: los recursos se ven verdes y viejos sin aviso.
- **Cambio.** Una línea `{{json …}}` por contenedor (escapa control) con `index` para etiquetas y `if .State.Health`; año < 2000 = None; línea de estado
  `docker ok|error|ausente` y antigüedad del último inventario completo visible (gris si > N ciclos).

### M7. Tope de 200 y carrera `ps`/`inspect` dejan la detección muerta en hosts grandes o con mucha rotación
- **Evidencia.** Diseño l. 38 y 52 (truncada ⇒ no cuenta como completa).
- **Escenario.** Un host con historial de tareas Swarm y contenedores parados supera 200 siempre → nunca hay lectura completa → nunca hay «desapareció».
  En un host con CI, entre `ps` e `inspect` siempre desaparece alguno → `inspect` sale con código 1 [S] → con la regla de C3 la lectura nunca es completa.
- **Cambio.** Primero los `running`, después el resto hasta el tope; tratar «No such object» como ausencia legítima de ese id (no como corte); medir en
  el ensayo real cuántos contenedores tiene `ps -a` en el Dokploy.

### M8. Recursos sin retención y tope de 500 que se llena de efímeros
- **Evidencia.** Diseño l. 48-51: retención solo para `eventos` (180 días); nada para filas `recursos` inactivas.
- **Escenario.** Contenedores con nombre aleatorio (construcciones, `docker run` sueltos) ocupan los 500 huecos; los nuevos de verdad se descartan.
- **Cambio.** Purga de recursos inactivos > N días (con su etiqueta) en `mantener`; no dar de alta `oneoff` ni contenedores sin etiquetas que vivan menos
  de un ciclo.

---

## Bajos
- **B1.** [V] `ok_en` es el `ahora` del **inicio** del ciclo (`servidores_ciclo.py:118,184`), hasta ~20-30 s antes del `ahora` del servidor: bordes de cubo
  desplazados unos segundos. `_hora_de` alinea a hora UTC: en zonas con media hora (+5:30) la «hora natural del Mac» no coincide (l. 29 del diseño).
- **B2.** [V] `servidores_lectura.py:50-58` agrupa filas de `horas_servidor` por día con `_junta` y la nota «picos, no sumas»: los intentos exactos se
  suman por día y las horas rellenadas solo por el histograma (`n = 0`) no deben contar como «observadas» en la disponibilidad.
- **B3.** [S] journald avisa con «Suppressed N messages» cuando limita: contarlo y marcar el cubo como «inferior al real».
- **B4.** Swarm con varios nodos: `docker ps` solo ve el nodo local; una tarea que cambia de nodo «desaparece». Fuera de alcance hoy (un nodo), pero
  documentarlo.
- **B5.** Un Mac antiguo ignora `fuente ninguna` y pinta 0 (`servidores_ssh.py:179-180` [V]); aceptable si se documenta.
- **B6.** Revertir los scripts en servidores de clientes (D-E) exige `alta-servidor.sh` interactivo con `sudo` en cada uno (`alta-servidor.sh:32` [V]):
  el «revertir = reinstalar» es caro; preferir que v2 sea aditivo para no tener que revertir.
- **B7.** El diseño mezcla «una sola llamada `docker inspect`» con «cada contenedor con su tope de tiempo» (l. 35 y 38): elegir uno.

## Supuestos del diseño que no están verificados
1. Que `_SYSTEMD_UNIT=ssh.service|sshd.service` cubre las distribuciones en uso (socket con plantilla, `sshd-session`, Debian volátil) — l. 23-25.
2. Que el journal tiene 24 h de historia (persistencia, tamaño, limitador) — implícito en l. 27-30.
3. Que la identidad por etiquetas es única por recurso (tareas históricas, réplicas, `oneoff`) — l. 44.
4. Que `RestartCount` refleja reinicios en Swarm — l. 45.
5. Que 200 contenedores caben en 64 KB con el resto de la salida — l. 38 frente a `servidores_ssh.py:14`.
6. Que el `### version` permite distinguir formatos — nadie lo lee hoy.
7. Que el presupuesto de 15 s/20 s alcanza con el bloque nuevo — `recoger.sh:65`, `servidores_ssh.py:13`.
8. Que `borrar_servidor` borra los recursos «al quitar el servidor» — no se llama en ningún sitio.
9. Que un certificado puede dar días negativos con verificación obligatoria — no puede.

## Orden recomendado de cambios al diseño (antes de codificar)
C3 y A2 (marcadores de completitud y secciones nuevas con versión propia) → C2 (identidad agregada y `service ls`) → C1 (cubos alineados, cobertura,
congelado) → A1 (bytes) → A5 (clave sin texto, purga al retirar) → A3 y A4 → medios.

Status: DONE_WITH_CONCERNS
Summary: El diseño de la 3.3a tiene tres fallos críticos: el histograma de intentos no es exacto y además puede bajar valores ya guardados tras un reinicio o una rotación del journal; la identidad de los contenedores Swarm/Compose junta varios contenedores en un solo recurso; y las salidas cortadas se toman por completas. Hay además cinco altos (tope de 64 KB que tira la lectura entera, compatibilidad v1/v2 sin mecanismo, filtro del journal, topes de eventos y datos personales en `clave`, eventos y caché). Todos tienen un cambio de diseño concreto y conviene aplicarlos antes de escribir código.
