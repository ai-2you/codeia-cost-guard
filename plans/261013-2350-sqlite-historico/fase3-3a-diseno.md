# Fase 3.3a — `recoger` v2, contenedores por SSH y caducidad de certificados (diseño v2, tras pre-mortem y red team)

Estado: **diseño v2, listo para codificar por entregas**. Fecha: 2026-10-15. Sustituye al borrador v1 con los informes
`../reports/premortem-fase3-3a-261015.md` (C1–C3, A1–A5, M1–M8) y `../reports/redteam-fase3-3a-261015.md` (H1–H20), cuyos cambios se aceptan salvo lo indicado.
Parte de `fase3-plan-monitorizacion.md` (D-A, D-C, D-E). Sin API de ningún panel: todo por SSH con la clave dedicada ya existente.

## Resultado buscado
1. **Intentos de acceso fiables** (solo líneas que sshd escribió) con histograma por hora natural y más IPs candidatas (cierra M1 de la 3.1).
2. **Contenedores y servicios** de cada servidor con historial: estado, salud, reinicios y eventos de aparición, desaparición, cambio de estado e inestabilidad.
3. **Caducidad del certificado** de cada web, en días.

## No objetivos
Actuar sobre servidores · variables de entorno, montajes, comandos, imágenes ni logs de contenedores · API de paneles (3.2) · reglas y avisos (3.5) · pantalla (3.6) ·
bases de datos (3.3b) · cambiar `sudoers` o permisos de los servidores más allá de reinstalar los dos scripts.

## Entregas (cada una con revisión de código independiente al terminar)
- **E1 — Protocolo v2 e intentos**: marcos de sección, límites de bytes, parser que no reabre secciones, filtro del journal, histograma, migración 2 (columnas de horas exactas).
- **E2 — Contenedores y recursos/eventos**: `recoger-root` v2, parseo hostil, tablas `recursos` y `eventos` (migración 3), histéresis, presupuestos, retención, borrado.
- **E3 — Certificados**: `cert_dias` y su columna.

## E1 — Protocolo y intentos

### Protocolo (C3, A1, A2, H6, H19, H20)
- `### version` es la **primera** línea y vale `2`. El Mac decide v1/v2 **por sección**: una sección v2 lleva su marco; una sin marco es v1 (formato de siempre). `recoger-root`
  imprime además `### version_root 2` antes de sus secciones, de modo que un recoger.sh v2 con root v1 (o al revés) se detecta.
- **Marco de completitud** en las secciones nuevas (`intentos`, `contenedores`): primera línea `n <esperados>` y última `fin <emitidos> <código>`; el script termina con `### fin`. El Mac
  solo da una sección por completa con cierre, `emitidos == líneas leídas`, código 0 y, en `contenedores`, `emitidos == esperados` o `truncado`. Sin cierre → sección no fiable
  (se conserva lo anterior, no hay eventos, el ciclo sigue). Una lectura con código ssh ≠ 0 **no** produce eventos ni histograma aunque lleve `### version`.
- **Presupuesto de tiempos** con test: suma de topes de `recoger-root` < `timeout` del `sudo` < tope del Mac menos conexión. `contenedores` va **al final** de `recoger-root` y con su `timeout` global.
- **Bytes** (A1): `TOPE_BYTES` sube a 128 KiB y cada línea nueva se corta en el servidor (`cut -b -512`); en el Mac, **una lectura que se pasa del tope descarta solo las secciones nuevas**,
  no la lectura entera (las secciones v1 van primero y caben siempre). `secciones()` no reabre una sección ya vista: un `### nombre` repetido invalida la lectura de esa sección y se cuenta en `meta` (H6).
- Cada campo de texto que viene de Docker sale con `{{json ...}}` en la plantilla (un valor con `\n`, `\t` o `###` no puede romper líneas ni secciones); el Mac vuelve a validar.

### Filtro del journal y fuente (H1–H4, A3)
- Solo `_SYSTEMD_UNIT`: `journalctl -q -o short-unix --since=-24h _SYSTEMD_UNIT=ssh.service _SYSTEMD_UNIT=sshd.service`. `_COMM`, `_EXE` y `_UID` **no** se usan como prueba (los cambia un proceso sin privilegios).
  La IP es el campo anterior al último `port` de la línea, no «cualquier IP de la línea».
- `fuente`: `unit` si `journalctl -F _SYSTEMD_UNIT` lista ssh.service/sshd.service con líneas; `ninguna` si no (sin unit o sin journal): el Mac muestra «sin dato», nunca 0.
- `aislamiento si|no`: el script comprueba que las sesiones de usuario **no** viven en el cgroup de ssh (`/proc/self/cgroup` de una sesión de infobar ≠ `ssh.service`). Con `no`, el Mac muestra los
  números pero **no persiste IPs** y rotula «sin garantía de origen».
- `parcial si|no` por `PIPESTATUS` (un `timeout` que mata journalctl no se cuenta como lectura entera).

### Histograma y su fusión (C1, H5, H15, H16)
- El servidor agrupa por **hora de época alineada** (`int(ts/3600)`), manda su `ahora` y `cobertura` (primer registro legible de la ventana) y 25 cubos; los no cubiertos van como `-` (desconocido, no 0).
- El Mac calcula el desfase `ok_en − ahora_servidor`; con |desfase| > 900 s el histograma queda `reloj_dudoso` y no se escribe. Si no, las horas se sitúan en el reloj del Mac desplazando por horas enteras.
- Fusión: columnas `intentos_exactos` e `intentos_completa`; una hora **completa y cubierta** se congela con `MAX(guardado, nuevo)` y **nunca baja**; una no cubierta no se escribe; la parcial se actualiza
  hasta que pasa a completa. Ventana escribible: las últimas 25 horas. Validación: cubos esperados sin repetir, `Σ h == total`, `Σ ip ≤ total`; si no cuadra se descarta **solo el histograma** y se cuenta.
- Hasta 12 IPs por muestra (M1); `interpretar()` ya no recorta a 4 antes de validar. Con `aislamiento no` o `fuente ninguna` no se guardan.
- Esquema: migración 2 añade columnas nullable a `horas_servidor`. Los picos de la 3.1 siguen para servidores v1.

### Cambios tras la revisión de código de E1 (`../reports/revision-codigo-fase3-3a-e1-261015.md`)
- Presupuesto de tiempo medido, no solo declarado: cada comando del script del usuario se salta pasados 6 s, el bloque del journal y el `sudo` reciben solo lo que queda
  (`resta`), el vigilante mata el grupo a los 14 s y hay un test que ejecuta el script con todo lento y mide el cierre del canal. Si no hay tiempo para el journal la fuente es `error`
  (sin dato), nunca 0.
- Reglas de intentos aplicadas al **mensaje** de journald (tras `ident[pid]: `), no a la línea entera: el nombre de usuario que elige el atacante ya no puede esconder ni triplicar un intento.
  Lectura de la más nueva a la más vieja (un corte pierde lo antiguo), ventana `--since @inicio_del_primer_cubo --until @ahora`, y un código de salida de journalctl distinto de 0/1 es lectura incompleta.
- `recoger-root` v2 declara `### version_root` y `### fin_root` con `omitidos N` (comandos saltados **o** agotados). Con N > 0 el Mac descarta las cuatro secciones de root («sin dato»), nunca las toma por ceros
  (cierra la regresión de `fail2ban 0` y las alarmas falsas de cortafuegos). Root v1 sin marcas se sigue aceptando.
- Una sección abierta cuando llega `### cortada` queda invalidada. Las secciones y motivos descartados se cuentan en `meta` (conjunto cerrado).
- Las horas exactas solo se anotan con **origen fiable y lectura completa**; la `completa` de una hora exige que haya terminado para el reloj del Mac y para el del servidor; la suma de cubos está acotada
  por `total + primer cubo`. Hasta 12 IPs por muestra. En la serie por día, un día solo es «completo» con todas sus horas (23, 24 o 25) con dato y cerradas.
- La tarjeta rotula «lectura parcial» y «origen sin verificar». Pendiente para E2: la parte de `contenedores` de `fin_root` y la ventana de 26 h (el diseño decía 25).

## E2 — Contenedores, recursos y eventos

### Recogida (C2, H7, H12, H13, M6, M7)
- Una llamada a `docker inspect` sobre los ids de `docker ps -aq`, **ordenados por antigüedad ascendente** y recortados a 200 **antes** del inspect; `n` = total real, `truncado <n>` si se recortó.
  Plantilla fija con lista blanca literal y **test en el repo** que la lee y falla si aparece cualquier campo fuera de la lista: nombre, `State.Status`, `State.Health.Status` (nunca `.Health` ni `.Log`),
  `RestartCount`, `State.ExitCode`, `State.StartedAt`, política de reinicio, y las etiquetas `com.docker.compose.project|service` y `com.docker.swarm.service.name|task.id`, todo con `{{json}}`.
- Con Swarm se sigue usando `docker service ls` (réplicas deseadas/en marcha) como fuente del estado del **servicio**; los contenedores de tareas viejas no cuentan (en Swarm `RestartCount` no crece).
- Un inspect fallido de un contenedor se cuenta (`fallos <n>`) y la sección no es «completa».

### Identidad y parseo hostil (H7, M5)
- Identidad: servicio Swarm solo si hay `task.id` **y** el servicio aparece en `service ls`; si no, `compose.project/compose.service` solo con `project` válido y único por servicio; si no, el nombre.
  Una colisión de identidad (dos contenedores vivos que reclaman la misma) genera un evento `colision` y **bloquea las desapariciones** de ese recurso.
- Nombres/etiquetas: juego `[A-Za-z0-9._/-]`, ≤ 64; estados y salud de conjuntos cerrados; enteros acotados; fechas estrictas, futuro = None. Un dato imposible descarta **solo ese contenedor**
  (no la lectura entera, ni las otras secciones) y se cuenta.

### Almacén: migración 3 (A4, A5, H9–H11, M3, M8)
- `recursos(servidor_id, tipo, clave_hash, etiqueta, primera, ultima, activo, estado, salud, reinicios, ...)`, clave primaria `(servidor_id, tipo, clave_hash)`. **`clave` no guarda texto**: es un hash
  con sal por instalación; la `etiqueta` (el nombre saneado) es el único texto y es dato personal (D-C). `eventos(ts, servidor_id, tipo, clave_hash, evento, severidad)` solo con conjuntos cerrados, sin texto.
- Eventos: `aparecio`, `desaparecio`, `cambio_estado`, `inestable`, `colision`, `reinicios_en_bucle`. **«Apareció» y «desapareció» exigen dos lecturas completas y buenas separadas por ≥ 2 intervalos**
  (tiempo, no número de lecturas). Un fallo SSH o una sección no fiable nunca genera eventos.
- **Alta inicial**: el primer ciclo de un servidor (o tras `--olvidar-recursos`) puebla `recursos` **sin eventos** (línea base). Presupuestos: 100 eventos por servidor y día **por tipo de evento**, y como máximo
  10 por identidad y día; el excedente se cuenta en `meta`. Un recurso que cambia de estado más de N veces/hora pasa a `inestable` (un solo evento) y deja de contar para el modo incidencia.
- Reinicio en bucle: `RestartCount` sube **y** `StartedAt` es de hace < 10 min **en ≥ 2 lecturas distintas**; tiempos medidos con `ok_en` del Mac, no con el reloj del servidor.
- Cardinalidad: ≤ 500 recursos por servidor; los inactivos > 30 días se purgan en `mantener`; la expulsión protege a los activos y a los que tienen eventos recientes. Eventos: 180 días.
- Borrado (A5): `borrar_servidor` se llama de verdad al quitar un servidor (hoy nadie lo llama); nueva orden `--olvidar-recursos [--servidor NOMBRE]`; la caché JSON poda las etiquetas a las 48 h como las IPs;
  la copia previa a migrar se hace sin `etiqueta` ni eventos con texto, igual que sin IPs.
- Migración 3 y bajada (M2): la app anterior no sabe leer `user_version 3`. Se documenta; el ensayo de subida/bajada con copia previa es **requisito antes de fusionar**; una base más nueva que la app deja un
  aviso visible (no un fallo mudo del historial).

### Cambios tras la revisión de código de E2 (`../reports/revision-codigo-fase3-3a-e2-261015.md`)
- El inventario tiene **presupuesto propio** (arranca solo si no se han gastado 5 s, `timeout 2` + `timeout 3`, sin `t`): si no llega, se pierde el inventario («fuente error»)
  pero no las secciones de seguridad de root. Lleva el reloj del servidor (`ahora`), que es con el que se mide la edad de `StartedAt` en la regla de bucle.
- Un contenedor cuya línea no vale se descarta **él solo** (nombres hasta 128, códigos de salida negativos válidos, líneas de hasta 1200 B); con descartados o recortada la lectura
  actualiza lo que ve pero no concluye desapariciones, y un recorte no fabrica apariciones (los «nuevos» de una lectura recortada entran como línea base).
- Presupuestos de eventos por servidor, día, evento **y tipo de recurso** (los contenedores sueltos no gastan el de los servicios); si un evento de aparición o desaparición no cabe,
  el recurso no cambia de estado y se reintenta (se anota cuando hay presupuesto). La expulsión de inactivos respeta lo que se ve en la lectura y lo que tiene eventos de la última semana.
- Un nodo Swarm sin `docker service ls` (worker) o con la lista recortada no sirve para el inventario (la identidad dependería de ella). `docker service ls` admite hasta 300 servicios.
- `ssh.analizar` separa líneas solo por `\n` (U+0085, U+2028 y compañía no reabren secciones). Los nombres también salen de la caché a las 48 h y con `--olvidar-recursos`.
- Hechos explícitos: la etiqueta tiene un `CHECK` de caracteres; el inventario va en un `SAVEPOINT` (un fallo suyo no deshace la muestra); la marca de línea base se guarda en `meta`.
- **Límites conocidos, aceptados** (no verificables desde el servidor): un contenedor con etiquetas `com.docker.compose.*` de otro proyecto se suma a su identidad (el evento
  `colision` del diseño se descarta); la sal de los hashes vive en la misma base, así que los eventos de 180 días son comprobables contra nombres candidatos por quien tenga la base
  (que ya tiene las etiquetas de los recursos activos); con más de 200 contenedores se vigilan los 200 más antiguos.
- Diferido a la 3.5 junto con las reglas: «un recurso inestable deja de contar para el modo incidencia».

## E3 — Certificados (H17, H18, M1)
- `medir_web`: tras el handshake verificado (D-D), `ssl.cert_time_to_seconds(notAfter)` → `cert_dias` (entero acotado ±36500, calculado con el reloj del Mac). Rotulado «del certificado que sirve el servidor»
  (la hoja, no la cadena). Un certificado ya caducado no completa el handshake: `error` lo dice y `cert_dias` queda None; **`cert_min` en `muestras` se guarda solo si todas las webs dieron fecha** y el estado
  «certificado inválido» sigue en `webs_caidas`, de modo que un caducado no desaparece del mínimo. Columna nullable en `muestras` (migración con la 2).

### Cambios tras la revisión de código de E3 (`../reports/revision-codigo-fase3-3a-e3-261015.md`)
- El certificado tiene **su propia tarjeta** (`certificado`): ámbar con menos de 14 días, rojo con menos de 3 o con un certificado inválido. Cuenta para el estado global pero **no para la
  salud viva**, de modo que un certificado a punto de caducar no mete al servidor en modo incidencia (sus días cambian una vez al día).
- La causa no se pierde: `muestras.webs_cert_mal` cuenta las webs con error de certificado, y `horas_servidor` guarda `cert_dias_min` (el que antes caduca en la hora) y
  `webs_cert_mal_max`; `historia()` los expone por hora y por día, de modo que el resumen permanente conserva más de los 14 días del crudo. Migración 4 ampliada (aún sin desplegar).
- Límite conocido: si la lectura SSH falla no se anota la muestra de métricas (solo una fila de estado), así que ese ciclo no guarda caducidad aunque las webs se hayan medido.
- El rótulo del desplegable dice «certificado inválido» si alguna web lo tiene, y no presume la fecha de otra.

## Despliegue y reversibilidad
- Los dos scripts se reinstalan con `alta-servidor.sh` (idempotente). El Mac sigue aceptando v1 por sección; revertir = reinstalar los de la rama anterior. Nada exige reinstalar todos a la vez.
- Antes de cada migración: copia previa sin IPs ni etiquetas. `VERSION_ESQUEMA` sube a 2 (E1) y a 3 (E2).

## Verificación prevista
- Tests con salidas v1 y v2 simuladas y hostiles: etiquetas con `\n`, `\t`, `### intentos`; contadores negativos y 10^18; 100 000 líneas; cortes a mitad de sección; fechas futuras; 250 contenedores efímeros;
  histograma con reinicio de journal; servidor que miente sobre horas pasadas; `docker` y `journalctl` falsos en el PATH para ejecutar los scripts reales (`bash -n` y ejecución).
- Mutaciones sobre cada filtro de seguridad (marcos, `max`, lista blanca de plantilla, dos lecturas, presupuestos).
- Protocolo de prueba del filtro en VM (redteam, «Protocolo de prueba»): **pendiente de que el dueño apruebe crear dos VM locales**; mientras tanto el filtro se prueba con `journalctl` falso y, en solo lectura,
  contra un servidor propio para fijar `_SYSTEMD_UNIT` y `aislamiento`.
- Revisión de código independiente al terminar cada entrega, con foco en privacidad y mínimo privilegio.

## Preguntas abiertas para el dueño
1. ¿Autorizas crear dos VM locales (Ubuntu 24.04 y Debian 12) para probar el filtro y los envenenamientos? Si no, solo se prueba con binarios falsos y una lectura de solo lectura en un servidor tuyo.
2. ¿Qué servidor propio uso para la lectura de solo lectura que fija `_SYSTEMD_UNIT` y `aislamiento` (sin imprimir nada identificable)?
