# Pre-mortem y red team del diseño de la Fase 3 (historial de servidores e IPs de quien intenta entrar)

Fecha: 2026-10-14 (Europe/Madrid) · Objeto: `plans/261013-2350-sqlite-historico/fase3-diseno.md` (v1) y las secciones "Fase 3",
"Seguridad transversal" y "Privacidad y legal" de `plan.md`, contrastadas con el código de `feat/fase1-ingesta-hermes`. Solo
lectura: no se ha ejecutado nada que escriba en `~/.cost-guard` ni en `Application Support`; no se reproduce ningún host, usuario
ni IP real. Marcas: **[V]** verificado (lectura con `fichero:línea` o ejecución local inocua); **[S]** supuesto, creencia o
documentación externa no comprobada en este equipo.

## TL;DR

El diseño acierta en lo estructural (base aparte, un escritor, `ts` del Mac, solo globales, picos en vez de sumas, nada de hosts ni
`who`, copias fuera). Lo que lo hundiría a los seis meses no está en las tablas sino en lo que rodea a la base:

1. **La promesa "las IPs se olvidan a los 90 días" ya es falsa hoy fuera de la base**: `logs/servidores.json` conserva las 4 IPs del
   último dato bueno de cada servidor sin límite de tiempo (`servidores_ciclo.py:147-160`), los PNG del desplegable con esas IPs solo se
   purgan al arrancar (`permisos.py:53-70`), la exclusión de Time Machine que `plan.md:219` da por hecha **no existe** en el código, y
   las copias previas a migración de `almacen._migrar` dejan **cinco copias en claro** que nadie borra (`almacen.py:225,615-640`).
2. **"Dedupe por `generado_en`" no funciona**: `generado_en` es un sello global del fichero, no por servidor (`servidores_ciclo.py:161`),
   y una entrada con error conserva los `datos` viejos (`:157-159`): se persistirían métricas rancias como nuevas. La clave es `ok_en`.
3. **El enganche propuesto (`_guardar`, bajo `_bloqueo`) convierte cualquier lentitud de SQLite en bloqueo del ciclo** y, peor, el
   `__main__` de `servidores_ciclo` (`:185-189`) ejecuta `ciclo()` desde otro proceso: habría **dos escritores**.
4. **La semántica de "intentos" es la de una ventana deslizante de 24 h leída cada 5 min (o cada 60 s)**: `veces` contaría muestras,
   no intentos, y se inflaría ×5 en incidencia. Solo son honestos los picos y "visto en N horas distintas". Contar de verdad exige
   `recoger.sh` v2 (histograma por hora de reloj), que además cierra el **envenenamiento del journal por `-t sshd`** (RT1).
5. **La IP del propio Mac acabará en `ips_atacantes`**: cuando la clave falla, los intentos del Mac entran en el top 4 (el propio código
   lo reconoce en `servidores_ciclo.py:23-26`). Hay que excluir `ips_conocidas` y contarlas aparte.
6. **Windows no está en el camino**: el ciclo de servidores no arranca allí (`fcntl` en `config_y_estado.py:8`; `os.killpg` en
   `servidores_ssh.py:68`). La fase 3 hereda esa deuda y debe declararla como precondición, no como "funciona también en Windows".

## Lo que está bien y no debe tocarse

- Base aparte con las mismas reglas de ruta, PRAGMA y `STRICT` (`almacen.py:138-173`): reutilizar `abrir()` parametrizado por ruta.
  `secure_delete` va en **todas** las conexiones, también las `mode=ro` (`:160,172`), y `journal_size_limit` ya está (`:165`).
- `ts` del Mac, nunca del servidor; números acotados y enumeraciones cerradas; descartar y contar en `meta` en vez de romper el ciclo.
- Solo IPs globales por `ipaddress` (misma regla que `servidores.py:19-22`, que ya excluye CGNAT y multicast).
- Picos, no sumas, para la ventana de 24 h (decisión abierta 2): es lo único honesto con el script actual (`recoger.sh:30-47`).
- No guardar host, usuario, puerto, servicios, contenedores, webs, salida cruda ni `who`; de `who` solo conteos calculados en memoria.
- IPs fuera del primer nivel de la API y del CSV de consumo; pintado por DOM (`historico.js:2`); sesión + Host + `Sec-Fetch-Site`
  + semáforo (`servidor_local.py:176,207-221`); parámetros por lista cerrada y `max_num_fields` (`:238-242`); fechas por regex +
  librería C (`historico.py:24,50-54`).
- Copias cifradas **sin** `servidores.sqlite` (decisión 3): `hilo_copias.py:33-35` copia solo lo que abre `almacen.abrir()`, así que
  hoy ya es así por construcción.
- `CLASES_SERVIDORES` con `ip` y `nombre` como personales y test de cobertura sobre `PRAGMA table_info`.

## Qué se puede agregar honestamente (y qué no) con el `recoger.sh` actual

[V] `recoger.sh:34-47`: `journalctl --since -24h` sobre el **reloj del servidor**, cuenta líneas canónicas, `hora` = las de los últimos
3 600 s, y `top 4` por IP. Cada lectura es una **foto de una ventana deslizante**; el Mac la lee ~12 veces/hora (60 en incidencia).

| Se puede decir | No se puede decir |
|---|---|
| "máximo de intentos en 24 h observado durante el día D" (pico) | "intentos del día D" (las ventanas se solapan; sumar o restar picos miente) |
| "máximo de intentos en 1 h observado en la hora H" | "intentos en la hora H" (la ventana de 1 h no coincide con la hora de reloj) |
| "la IP X estuvo entre las 4 que más lo intentaban, con hasta N intentos en 24 h" | "la IP X hizo N intentos" ni "primera vez que X lo intentó" (puede llevar 24 h fuera del top 4) |
| "X se vio en K horas de reloj distintas" (independiente de la cadencia) | "X salió `veces` veces" como si fueran intentos (son muestras, ×5 en incidencia) |

Efectos a documentar en la pantalla: al **reiniciar** el servidor con journal volátil (`Storage=auto` sin `/var/log/journal`) la ventana
se vacía y el pico baja a 0 sin que nadie haya dejado de atacar [S]; con journald **rotado** (`SystemMaxUse`) la ventana es menor de
24 h; un **cambio de zona** no afecta (`-o short-unix` es epoch) pero un reloj del servidor mal puesto sí (hay `hora_sincronizada`
en `bastionado`, `recoger.sh:61`: úsalo para marcar la muestra como dudosa). La única forma de contar de verdad es que el script
emita un **histograma por hora de reloj** (24 líneas `h <epoch_hora> <n>`), que el Mac funde con `max` por cubo (idempotente ante
relecturas y rotación). Eso es `recoger.sh` v2: el script ya emite `### version` (`:9-10`) y el Mac hoy **no lo lee**
(`servidores_ssh.py:147-201` no toca `sec["version"]`).

## PRE-MORTEM — "se implementó tal cual y a los seis meses…"

Severidad: C crítica · A alta · M media · B baja. Cada punto: mecanismo → cambio de diseño → cómo se prueba.

### PM1 (C) El dedupe por `generado_en` persiste datos rancios y pierde muestras
- Mecanismo [V]: `generado_en` se pone una vez por ciclo para todo el fichero (`servidores_ciclo.py:161`); las entradas llevan
  `intento_en` (cada intento) y `ok_en` (cada lectura buena) (`:152-159`). Si un servidor falla, `previo` conserva los `datos` de la última
  lectura buena (`:157-159`): un `registrar(nombre, entrada, ahora)` que mire `generado_en` escribiría una muestra "nueva" con métricas
  viejas cada 5 min mientras dure el fallo, y `veces`/`ultima` de sus IPs seguirían subiendo sin que el servidor diga nada.
- Cambio: dedupe por servidor con `ok_en` (muestra con métricas) y, aparte, `intento_en` (muestra de estado `rojo`/`error` **sin**
  métricas ni IPs). `muestras.ts = ok_en`. Guardar en `meta` la última `ok_en` registrada por servidor para no depender del fichero.
- Prueba: un servidor que responde una vez y luego falla 10 ciclos → 1 muestra con métricas, 10 de estado, `veces` = 1.

### PM2 (C) El enganche en `_guardar` bloquea el ciclo y abre un segundo escritor
- Mecanismo [V]: `_guardar` corre bajo `_bloqueo(rutas.SERVIDORES)` (`servidores_ciclo.py:145`) y dentro de `_EN_MARCHA` (`:105`), en
  el hilo trabajador (`trabajador_barra.py:69`), que también escanea agentes y repinta. `busy_timeout` es 2 s por sentencia
  (`almacen.py:158`), un `VACUUM` tarda lo que tarde, y todo eso retrasa el escaneo de agentes y el repintado. Además
  `python -m infobar.servidores_ciclo --forzar` ejecuta `ciclo()` → `_guardar()` desde **otro proceso** (`:185-189`): dos escritores.
- Cambio: `registrar()` se llama **fuera** del `with _bloqueo` y solo cuando el trabajador pasa explícitamente el registrador (p. ej.
  `ciclo(forzar, historial=...)`; el `__main__` y los tests no lo pasan). Purga/compactación/`VACUUM` en un paso propio del trabajador
  con presupuesto de tiempo, nunca dentro del ciclo. Si la base falla, **retroceso**: un intento por hora y un solo `log()` por fallo,
  no uno cada 15 s (`trabajador_barra.py:17,40`).
- Prueba: base bloqueada por otro proceso durante 30 s → el ciclo termina en su tiempo normal y la caché se escribe; el `__main__`
  no crea ni toca `servidores.sqlite`.

### PM3 (C) La retención de 90 días no se cumple fuera de la base
- Mecanismo [V]:
  - `logs/servidores.json` guarda `datos.intentos.top` (IPs) y `datos.sesiones[].desde` (IPs propias) del último dato bueno
    (`servidores_ssh.py:172,183-185`) y lo conserva mientras el servidor siga en la config, aunque lleve meses sin responder
    (`servidores_ciclo.py:147-160`). Es 0600 (`config_y_estado.py:100-104`) y está fuera de git (`.gitignore`), pero no caduca.
  - El desplegable pinta las 4 IPs (`desplegable_servidores.py:111-114`) y el PNG va a `logs/render` (`desplegable_png.py:25-26`);
    `purgar_render()` solo corre en `endurecer()` al arrancar (`permisos.py:33-50,53-70`) y `limpiar()` conserva los vivos (`:112-125`).
    Una barra que no se reinicia en meses tiene PNG con IPs de meses.
  - `plan.md:219` afirma "exclusión de Time Machine comprobada al arrancar": no hay `tmutil`/`xattr`/`backupd` en `infobar/` ni en
    `costbar.py`. Instantáneas locales de APFS y Time Machine se llevarán `servidores.sqlite` con las IPs.
  - Al migrar esquema, `_migrar` hace `copia()` en claro a `DATOS/copias` y conserva 5 (`almacen.py:223-225,615-640`). Si la fase 3
    reutiliza `_migrar` tal cual, la primera migración de `servidores.sqlite` dejará IPs en claro sin purga.
- Cambio: (1) la caché no guarda `top` ni `desde` más de lo que dura su utilidad: al fusionar, se vacían `intentos.top` y
  `sesiones[].desde` de toda entrada cuyo `ok_en` tenga más de 48 h, y al quitar un servidor de la config se borra su entrada (ya pasa,
  `:147`) **y** se purgan sus IPs de la base si el dueño así lo decide (D6). (2) `purgar_render()` cada hora desde el trabajador.
  (3) O se implementa la exclusión de Time Machine del directorio de datos (`tmutil addexclusion` / `com.apple.metadata:com_apple_backup_excludeItem`) o se retira la frase de `plan.md`
  y se documenta que las instantáneas conservan copias. (4) La copia previa a migración de `servidores.sqlite` se hace **sin**
  `ips_atacantes` (backup API a temporal, `DELETE` + `VACUUM` con `secure_delete`, como `exportacion.copiar_base`,
  `exportacion.py:69-86`) y caduca a los 30 días.
- Prueba: inyectar reloj +100 días → ni la base, ni la caché, ni `logs/render`, ni `DATOS/copias` contienen ninguna IP (grep de
  bytes sobre los ficheros, tras `wal_checkpoint(TRUNCATE)`).

### PM4 (A) `caduca_en` almacenado deja de ser verdad al cambiar la retención
- Mecanismo: el diseño guarda `caduca_en = ultima + retención`. Si el dueño baja `retencion_ips_dias` de 90 a 30, las filas viejas
  conservan `caduca_en` a 90 y la purga diaria las respeta; la pantalla diría "caduca el …" con la fecha antigua.
- Cambio: no almacenar `caduca_en`; purga y pantalla lo calculan de `ultima` + retención vigente. Y **filtrar al leer**: una IP con
  `ultima + retención < ahora` nunca se devuelve aunque la purga no haya corrido (la barra puede llevar semanas apagada).
  Purga también en el **primer** ciclo tras arrancar, no solo "una vez al día".
- Prueba: bajar la retención y consultar sin purga → no sale ninguna IP fuera de plazo; subir la retención no resucita borradas.

### PM5 (A) Identidad del servidor: renombrar, borrar y reutilizar un nombre
- Mecanismo [V]: la config identifica el servidor solo por `nombre` (`servidores.py:81-87`); el panel añade `{nombre: "", ssh: ""}`
  (`ajustes.js:176`). Con `servidores.nombre UNIQUE`, quitar "Cliente A" y meses después dar de alta otro "Cliente A" de otro cliente
  **fusiona** dos históricos; renombrar parte el histórico en dos y deja un servidor fantasma con IPs hasta que caduquen.
- Cambio: identidad por `id` estable (uuid) en cada entrada de `servidores.json`, generado por `normalizar` al guardar si falta; la
  base guarda `servidores(id, nombre, creado_en, retirado_en)` y renombrar actualiza `nombre`. Resuelve D1 sin "fusionar".
- Prueba: renombrar → mismo `servidor_id`, nuevo nombre; quitar y reañadir con el mismo nombre → `servidor_id` distinto.

### PM6 (A) `veces` miente y la incidencia lo agrava
- Mecanismo [V]: cadencia 300 s / 60 s (`servidores_ciclo.py:20-21`); un servidor en ámbar/rojo se lee cada minuto mientras lo esté
  (`:52-58`). Un servidor que diga `hora 31` permanentemente (`servidores_estado.py:44-48`) se lee ×5 para siempre; `veces` sube ×5 y
  `muestras` tiene 1 440 filas/día en vez de 288.
- Cambio: sustituir `veces` por `horas_vista` (nº de horas de reloj distintas en las que la IP estuvo en el top 4) y guardar en
  `horas_servidor.n` el nº de muestras para poder normalizar; en incidencia, persistir como mucho una muestra por franja de 5 min
  (máximo de la franja) para que la serie tenga cadencia uniforme.
- Prueba: 60 muestras en una hora con la misma IP → `horas_vista` = 1, `max_24h` = máximo.

### PM7 (A) Windows: la fase 3 hereda un ciclo que allí no arranca
- Mecanismo [V]: `import fcntl` a nivel de módulo (`config_y_estado.py:8`) impide importar el paquete en Windows; `os.killpg`
  (`servidores_ssh.py:68`) no existe allí y `start_new_session` se ignora (`:85`). `_privado` es no-op (`almacen.py:122-123`), así
  que la base con IPs depende de la ACL de `%LOCALAPPDATA%` (documentado en `permisos.py:6-7`). `VACUUM` escribe un temporal con el
  contenido completo (IPs) en `%TEMP%` o `/tmp` [S].
- Cambio: declarar en el plan la precondición "ciclo de servidores en Windows" (bloqueo por sistema, árbol de procesos con
  `CREATE_NEW_PROCESS_GROUP`/`taskkill`), y `PRAGMA temp_store=MEMORY` en la conexión que hace `VACUUM`. Sin eso, la fase 3 es
  macOS/Linux y hay que decirlo (D8).
- Prueba: `python -c "import infobar.servidores_ciclo"` en Windows; test de `VACUUM` con `temp_store` comprobado por `PRAGMA`.

### PM8 (M) Crecimiento y tope de 5 000 IPs bajo rotación constante
- Estimación [S]: `muestras` ≤ 288 (1 440 en incidencia) × 14 días × N servidores → con N = 20, 80 k–400 k filas, < 40 MB. `ips_atacantes`
  ≤ 5 000 × N. `horas_servidor` 8 760 × N al año. No hay problema de volumen. El problema es el **churn**: un atacante que rota IP
  aporta hasta 4 IPs nuevas por muestra → 1 152/día (5 760/día en incidencia): el tope se alcanza en menos de un día y la expulsión
  "por `ultima` más antigua" borra en < 24 h cualquier IP que haya dejado de aparecer, incluida la del atacante persistente que paró ayer.
- Cambio: tope de **altas nuevas por servidor y día** (p. ej. 500; el resto se cuenta en `meta`), y expulsión por `(horas_vista asc,
  ultima asc)`: primero las que solo se vieron una hora. Con `secure_delete` cada expulsión reescribe la página: irrelevante a este tamaño.
- Prueba: 20 000 IPs distintas en un día → ≤ 500 altas, contador en `meta`, la IP persistente del día anterior sigue.

### PM9 (M) Fallo de la base y versiones
- `abrir()` lanza si la base es de versión más nueva (`almacen.py:216-217`) o si es un checkout sin `COSTGUARD_DATOS` (`:134-135`); en
  solo lectura exige que el fichero exista (`:145-146`). Con el `try` que nunca propaga, el ciclo sigue (bien), pero la **purga tampoco
  corre** tras un downgrade: las IPs se quedan hasta que alguien actualice. Y el panel vería `FileNotFoundError` antes de la primera
  muestra.
- Cambio: el panel traduce "no existe" a "sin datos todavía"; el estado "base no utilizable" se enseña en Ajustes (como `copias`), con el
  motivo; se documenta que el rollback de versión congela la purga. Migraciones numeradas con `user_version` propio, como `_migrar`.
- Prueba: `user_version` 99 → ciclo normal, panel con aviso, ningún traceback en `costbar.log`.

### PM10 (M) Concurrencia escritor / lectores y mantenimiento
- [V] WAL permite lectores `mode=ro` durante la escritura. `wal_checkpoint(TRUNCATE)` devuelve `busy` con un lector largo
  (`almacen.py:643-646`): si el panel está abierto en Histórico el WAL con las páginas antiguas (IPs recién borradas) puede sobrevivir
  al día. `VACUUM` con lectores activos [S] funciona pero deja las páginas viejas en el WAL hasta el siguiente checkpoint.
- Cambio: tras purgar, reintentar el checkpoint cada hora hasta `busy = 0`; anotar en `meta` la última vez que se logró, y mostrarlo
  ("borrado consolidado el …"). El test de "bytes tras borrar" debe ejecutarse **con** un lector abierto y después de cerrarlo.

### PM11 (M) IPs propias
- [V] `sesiones[].desde` sale de `who` y se guarda en la caché (`servidores_ssh.py:171-172`); `ips_conocidas` vive en `servidores.json`
  (`config_y_estado.py:24`). El diseño solo persiste `sesiones_ajenas`: correcto. Pero `_ip_conocida` compara la cadena de `who` tal
  cual (`servidores_estado.py:23-25`), que con `UseDNS yes` es un nombre: todo cuenta como ajeno. Y la IP del Mac entra en
  `intentos.top` cuando la clave falla (`servidores_ciclo.py:23-26`): quedaría como "atacante" 90 días (ver RT2).
- Cambio: excluir `ips_conocidas` de `ips_atacantes` y contarlas en `meta` como `intentos_propios`; `sesiones_ajenas` se marca `NULL`
  cuando `ips_conocidas` está vacía (hoy el ámbar ya se condiciona a `conocidas`, `servidores_estado.py:43`).

### PM12 (B) Detalles del esquema
- `dispositivo_id` por fila es redundante: en `consumo` va en `meta` (`almacen.py:236`) y se etiqueta al fundir. Poner `ts` en
  segundos UTC enteros con `CHECK` de rango como `HORA_MIN/MAX` (`:26`). Máximo de `retencion_ips_dias` = 365 contradice
  `plan.md:226-227` ("30–90 días"); ver D6. Un `total`/`hora` ilegible se convierte hoy en 0 (`servidores_ssh.py:126-132,182`): en la
  base debe ser `NULL` (dato ausente), no un cero honesto.

## RED TEAM — vectores

### RT1 (A) Envenenamiento del journal sin ser root: atacantes falsos durante 90 días
- Evidencia [V]: `recoger.sh:34` filtra por `-t sshd -t sshd-session`, es decir, `SYSLOG_IDENTIFIER`, un campo que **pone el cliente**
  [S: systemd.journal-fields(7); los campos de confianza son los que empiezan por `_`]. Cualquier proceso local sin privilegios
  (una web comprometida como `www-data`, un contenedor con `/dev/log` montado) puede hacer `logger -t sshd "Invalid user x from
  203.0.113.9 port 1"` y el awk lo cuenta (`:37-42`). Con la fase 3 eso pasa de un número en una tarjeta a un registro persistente
  "esta IP te atacó" sobre una IP que puede ser la de un cliente, la del Mac o `8.8.8.8`.
- El nombre de usuario hostil **no** es vector [V]: el awk recorre los campos desde el final (`:40`) y la IP real va después del
  usuario en todas las líneas canónicas; OpenSSH escapa caracteres no imprimibles en los logs [S], así que no inyecta líneas nuevas.
- Cambio: en `recoger.sh` v2, filtrar por campos de confianza (`_COMM=sshd` o `_SYSTEMD_UNIT=ssh.service`) en vez de `-t`. En el Mac,
  nada puede distinguir una línea falsa: la pantalla debe decir "según el servidor X" y nunca cruzar IPs entre servidores ni sugerir
  acciones (bloquear, denunciar). Mantener `servidor_id` en la clave primaria (ya está).
- Prueba: en un servidor de pruebas, `logger -t sshd …` → v1 lo cuenta, v2 no.

### RT2 (A) IPs falsas: propias, privadas, mapeadas, raras
- Evidencia [V, ejecutado en local con Python 3.14]: `is_global` ya rechaza privadas, loopback, CGNAT (`100.64/10`), documentación
  (`2001:db8::`), 6to4, `0.0.0.0`, `255.255.255.255` y `240/4`. Pero `::ffff:8.8.8.8` es global y se guardaría como texto IPv6
  distinto de `8.8.8.8` (dos filas para la misma IP); en Python 3.11–3.12 la misma dirección da `is_global = False` [S: el desenvuelto
  de mapeadas llegó en 3.13], así que el resultado dependería de la versión. `64:ff9b::8.8.8.8` (NAT64) se canoniza a `64:ff9b::808:808`
  y es global. `fe80::1%eth0` conserva el scope id. `01.2.3.4` y `1.2.3` lanzan `ValueError` (bien). La regex del Mac
  (`servidores_ssh.py:144`) deja pasar `...`, `::` o `aaaa` al `top` actual, que hoy se pinta escapado (`desplegable_servidores.py:113`).
- La IP del Mac entra de forma **legítima** en el top 4 cuando la clave falla (`servidores_ciclo.py:23-26`); la de un cliente puede
  entrar por RT1; `8.8.8.8` igual. Nada en el Mac puede refutarlo.
- Cambio: canonizar con `ipaddress`, desenvolver `ipv4_mapped` **antes** de `is_global`, rechazar scope id y `%`, rechazar
  `is_reserved`/`is_unspecified`; excluir `ips_conocidas` (PM11) y la IP pública actual del Mac si se conoce (no es objetivo
  obtenerla: basta `ips_conocidas`). Tope de 45 caracteres antes de parsear. Tabla de casos como fixture.
- Prueba: mutaciones con la lista de arriba; `::ffff:8.8.8.8` y `8.8.8.8` → una fila; `::ffff:10.0.0.1` → descartada en 3.11 y en 3.14.

### RT3 (M) Números gigantes, negativos, duplicados y `TOPE_BYTES`
- Evidencia [V]: `_num` admite `1e308` y `1_000` (`servidores_ssh.py:126-132`); `int()` de eso es un entero enorme que el `CHECK` de la
  fase 3 rechazará (bien, si se descarta y cuenta, no si se "recorta"). `nan`/`inf` se vuelven **0** silenciosamente. `total`/`hora`
  repetidos: gana el último (`:181-182`); secciones repetidas: gana la última (`:117-120`), y `recoger-root` (root) imprime después
  (`recoger.sh:64-66`), así que puede pisar `### intentos`. Más de 64 KiB → toda la salida se descarta (`:105-106`): el servidor puede
  **ocultarse** a voluntad, pero con PM1 eso produce muestras de estado, no métricas rancias.
- Cambio: lo ilegible es `NULL`, no 0; fuera de rango se descarta la **muestra entera** y se cuenta por motivo; `top` con más de 4 `ip`
  válidas se recorta tras canonizar (hoy se ordena antes de validar, `:185`).
- Prueba: fixture hostil con `total -5`, `hora 1e308`, `ip 1.2.3.4 nan`, `### intentos` duplicada.

### RT4 (M) Inundación para llenar o desplazar
- Cubierto por PM8 (tope de altas/día y expulsión por relevancia). El disco no peligra: `TOPE_BYTES` acota la entrada y las cotas
  por servidor acotan la base. Un servidor en incidencia perpetua multiplica ×5 las lecturas de SSH (coste de red, no de base).

### RT5 (M) Pantalla de IPs: inyección y exfiltración por `con_ips`
- Evidencia [V]: una IP canónica solo lleva `[0-9a-f.:]`; el nombre de servidor es `^[\w .-]{1,40}$` (`servidores.py:11`), sin `<`,
  pero `\w` admite cualquier alfabeto (no admite bidi, que no son `\w`). Con DOM y `textContent` (`historico.js:2,53`) no hay XSS
  desde estos datos. La CSP tiene `connect-src 'self'` y `form-action 'none'` (`servidor_local.py:38-39`), pero una navegación
  `location = "https://…?datos"` no la bloquea ninguna directiva: un XSS residual en el panel (la revisión de código de la fase 2
  encontró uno por comentario `/* */`, que el commit `15dba25` dice cerrar) leería `/servidores/ips?con_ips=1` con la sesión y lo
  sacaría por navegación. `app.js:278` sigue usando `innerHTML` con nombres de fuentes.
- Cambio: `con_ips=1` exige además un clic explícito en la UI (no se carga al abrir la pestaña) y deja una línea de auditoría con el
  **número** de IPs servidas (como `exportacion_csv.auditar`, `exportacion_csv.py:83-90`, nunca las IPs). Reutilizar el semáforo
  `historiando` (`servidor_local.py:176`) y exigir `servidor=` (nunca "todas"). `Cache-Control: no-store` ya está (`:197`); el JS no
  guarda IPs en `localStorage`. Aceptar como riesgo residual que la defensa real es "sin XSS".
- Prueba: test del tipo `test_panel_seguro` con un nombre de servidor `a<b` y una IP válida; auditoría con contador.

### RT6 (B) CSRF y escrituras
- [V] Los endpoints nuevos son GET sin efecto; `--olvidar-ips` es CLI. Si algún día hay botón "olvidar ahora", pasa por la puerta de
  `_cuerpo_json` (`servidor_local.py:325-343`), nunca por GET.

### RT7 (M) Ficheros, permisos y restos
- [V] Base y `-wal`/`-shm` 0600 (`almacen.py:150-155,168-170`); en Windows nada (`:122-123`). Páginas con IPs borradas viven en el WAL
  hasta el checkpoint (PM10). `VACUUM` usa un temporal con todo el contenido (PM7). `servidores.json.<pid>.tmp` solo queda si el
  proceso muere entre `open` y `replace` (`config_y_estado.py:100-104`), y es 0600. PNG con IPs en `logs/render` (PM3).
- Cambio: `temp_store=MEMORY` en la conexión de mantenimiento; `purgar_render` horaria; el barrido de `DATOS/tmp` de
  `exportacion.barrer_tmp` (`exportacion.py:48-66`) debe cubrir también los temporales de la copia premigración de servidores.

### RT8 (B) Fugas por logs, errores y CSV
- [V] Hoy `costbar.log` no recibe IPs: los `log()` del ciclo son fijos (`servidores_ciclo.py:111`) y el trabajador escribe los 600 últimos
  caracteres de un traceback (`trabajador_barra.py:79`), que no incluye valores. `log()` abre sin modo explícito (`config_y_estado.py:16-19`)
  pero `endurecer()` pone umask 077 y corrige `*.log` (`permisos.py:35,44-46`). El CSV de consumo sale solo de `historico()`
  (`servidor_local.py:250-252`): sin servidores.
- Cambio: regla de revisión: ningún `log()` ni mensaje de error de `servidores_historico` interpola filas, parámetros ni `str(e)` de
  `sqlite3.IntegrityError` con valores; el registro de auditoría de `--olvidar-ips` lleva solo servidor y recuento. Test: grep de
  `log(f"` en el módulo nuevo.

## Coherencia legal: base de interés legítimo y 90 días frente a lo que ya se guarda

- `plan.md:225-227`: interés legítimo (cdo. 49 RGPD), plazo corto 30–90 días, sin geolocalización. La base nueva lo cumple **si** se
  aplican PM3 y PM4. Hoy lo contradicen: la caché sin caducidad, los PNG, las instantáneas (sin exclusión) y, mañana, las copias
  premigración. El máximo configurable de 365 días no encaja con "corto" sin una justificación escrita (D6).
- Servidores **de clientes**: ahí el dueño actúa de encargado y la finalidad y el plazo los fija el cliente. Eso pide retención y
  `guardar_ips` **por servidor** (D4), y un texto en Ajustes al dar de alta ("¿servidor de un cliente?") que lo deje escrito. Un
  `nombre` que sea el de una persona es dato personal y es permanente en `horas_servidor` por diseño: debe poder renombrarse a
  posteriori (PM5 lo permite) y estar clasificado como personal (ya está).
- El registro de `--olvidar-ips` en auditoría es la prueba de supresión: fecha, servidor, recuento; nada más.

## Decisiones abiertas: recomendación para cada una (y las que faltan)

| # | Decisión | Recomendación | Qué la cambiaría |
|---|---|---|---|
| D1 | Renombrar: nuevo histórico o fusionar | Ninguna de las dos: **identidad por `id` estable en `servidores.json`** (PM5); renombrar solo cambia la etiqueta. Fallback si no se quiere tocar la config: nuevo histórico, con `--olvidar-ips --servidor` aceptando nombres retirados. | — |
| D2 | Picos de 24 h o cambiar `recoger.sh` | **Las dos cosas, por fases**: ahora picos rotulados como tal y `horas_vista`; y `recoger.sh` v2 (histograma por hora de reloj + filtro por `_COMM`/`_SYSTEMD_UNIT`, RT1) leído solo si `### version ≥ 2`, con el Mac aceptando v1 indefinidamente. Se reinstala cuando se pueda; no bloquea la fase. | Que ningún servidor vaya a reinstalarse nunca: entonces solo picos y aviso permanente "cifras aproximadas". |
| D3 | Copias con `servidores.sqlite` | **No en esta fase** (ya es así por construcción, `hilo_copias.py:33-35`). Si más adelante se quieren, copiar **sin** `ips_atacantes` (temporal + `DELETE` + `VACUUM`), nunca con ellas. | Que el histórico agregado de servidores sea valioso para el dueño: entonces la copia sin IPs. |
| D4 | Interruptor `guardar_ips` | **Sí, y por servidor** (global como valor por defecto, override por entrada). Necesario para servidores de clientes. | — |
| D5 (nueva) | Identidad del servidor en config | `id` uuid por entrada, generado al guardar (ver D1). | — |
| D6 (nueva) | Techo de retención y borrado al quitar un servidor | Máximo 90 días (o 180 con justificación escrita en el README); al quitar un servidor en Ajustes, sus IPs se borran en la siguiente purga (no esperar 90 días). | Un cliente que exija retención mayor por contrato: por servidor, documentado. |
| D7 (nueva) | `ips_conocidas` nunca como atacantes | Excluirlas y contarlas aparte (`intentos_propios`). | — |
| D8 (nueva) | ¿Windows es requisito de esta fase? | Declarar "servidores" como macOS/Linux hasta que el ciclo funcione en Windows (PM7); la base y la purga sí deben ser portables desde el principio. | Que Windows sea el siguiente hito: entonces PM7 entra antes que la fase 3. |
| D9 (nueva) | Retención de IPs en la caché y los PNG | 48 h para `intentos.top` y `sesiones[].desde` en la caché; purga horaria de `logs/render`. | — |
| D10 (nueva) | Exclusión de Time Machine | Implementarla (es una línea con `tmutil`/xattr) o retirar la afirmación de `plan.md:219`. | — |

## Cambios al diseño, ordenados por severidad

1. Dedupe por `ok_en` / `intento_en`, muestras de estado sin métricas, `ts = ok_en` (PM1).
2. Enganche fuera de `_bloqueo`, registrador inyectado por el trabajador, mantenimiento aparte con presupuesto y retroceso (PM2).
3. Retención fuera de la base: caché a 48 h, `purgar_render` horaria, Time Machine real o retirada, copia premigración sin IPs (PM3, D9, D10).
4. `caduca_en` calculado, filtro al leer, purga al arrancar (PM4).
5. `id` estable por servidor (PM5, D1, D5).
6. `horas_vista` en vez de `veces`; una muestra por franja de 5 min en incidencia; `n` en `horas_servidor` (PM6).
7. Precondición Windows declarada; `temp_store=MEMORY` en `VACUUM` (PM7, D8).
8. Tope de altas/día y expulsión por relevancia (PM8, RT4).
9. `recoger.sh` v2 con histograma por hora y campos de confianza; el Mac lee `### version` (D2, RT1).
10. Canonización de IPs: mapeadas, scope id, reservadas; `ips_conocidas` fuera (RT2, PM11, D7).
11. `NULL` para lo ilegible; descartar la muestra entera fuera de rango; recortar `top` tras validar (RT3, PM12).
12. `con_ips=1` solo tras clic, con auditoría de recuento, `servidor=` obligatorio y semáforo compartido (RT5).
13. Panel: "no existe" = "sin datos"; estado de la base visible en Ajustes; downgrade congela la purga, documentado (PM9).
14. Checkpoint reintentado hasta `busy = 0` y fecha de consolidación visible; test de bytes con lector abierto (PM10).
15. Retención máxima 90 (o 180 justificado); `guardar_ips` y retención por servidor; texto "servidor de cliente" en Ajustes (D4, D6).

## Supuestos

- La fase 3 se construye sobre `feat/fase1-ingesta-hermes` tal como está hoy, con la fase 2 fusionada (alta).
- `SYSLOG_IDENTIFIER` es falsificable por procesos locales y `_COMM`/`_SYSTEMD_UNIT` no (alta; documentación de systemd, no probado aquí).
- OpenSSH escapa caracteres no imprimibles de los nombres de usuario al registrar (media-alta; no verificado en este repo).
- El comportamiento de `is_global` con IPv4 mapeadas difiere entre Python 3.11–3.12 y ≥ 3.13 (media-alta; verificado solo en 3.14).
- Las cifras de volumen son estimación, no medición (baja): medir con una base sintética de 20 servidores × 1 año.
- Los servidores monitorizados usan systemd-journald (alta: `recoger.sh` lo asume); con rsyslog sin journal la sección `intentos` sale vacía.

Status: DONE_WITH_CONCERNS
Summary: El esquema y las cotas del diseño son correctos, pero tal cual se implementaría con un dedupe roto, un enganche que bloquea el
ciclo y admite dos escritores, una retención que la caché, los PNG, las instantáneas y las copias premigración incumplen, `veces` como
cifra falsa y la IP del propio Mac como atacante; diez decisiones (cuatro originales y seis nuevas) quedan con recomendación.
Preguntas sin resolver: D6 (techo de retención 90/180 y borrado al quitar servidor), D8 (si Windows es requisito de esta fase) y D2
(cuándo se reinstala `recoger.sh` v2 en los servidores de clientes) dependen del dueño; el resto se puede fijar con lo propuesto.
