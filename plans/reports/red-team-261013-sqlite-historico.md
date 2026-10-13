# Red team del plan «histórico permanente en SQLite»

Fecha: 2026-10-14 (Europe/Madrid). Alcance: solo el plan (`plans/261013-2350-sqlite-historico/plan.md`) contrastado con el código actual. Solo lectura; no se ha tocado el repo. Valores reales (IPs, claves) aparecen como [redactado].

## TL;DR

El plan protege el fichero equivocado. Hoy el dato sensible ya sale por tres sitios que la base no cambia: un servidor HTTP local **sin autenticación** que entrega la configuración completa (hosts, usuarios, proyectos) y acepta escrituras a cualquier proceso o usuario del Mac; una carpeta `~/.cost-guard` `0755` con `panel.html`, capturas PNG del desplegable y logs `0644`; y copias en claro (`.bak`) que seguirán existiendo. Tres de los controles propuestos no hacen lo que se cree (cifrado con clave en el Llavero, HMAC de IPs, exclusión de Time Machine). Lo que sí hay que decidir antes de la fase 1 es el esquema: hora en UTC, dueño por fila, identificadores de fuente sin rutas, y borrado real (`secure_delete` + purga de copias).

## 1. Ataques que el plan no cubre o cubre mal

### 1.1 (a) Otro usuario/proceso del Mac lee y escribe todo por HTTP, sin credencial — CRÍTICA
Camino: `curl -H 'Host: 127.0.0.1:47391' http://127.0.0.1:47391/ajustes`. La puerta solo mira `Host` y `Sec-Fetch-Site`, y si la cabecera no viene se asume `same-origin` (`infobar/servidor_local.py:105-112`). La respuesta de `/ajustes` lleva `config` entera menos secretos: `servidores` (ssh `usuario@host`, `ssh_admin`, `panel_url`), `ips_conocidas`, `proyectos` crudos y `origenes` (`:134-150`); `/origenes` y `/config.json` lo mismo (`:126-133`, `:151-157`). Con `-H 'Origin: http://127.0.0.1:47391' -H 'Content-Type: application/json'` pasa también `POST /guardar` y `POST /clave` (`:161-166`, `:181-228`): el atacante añade un servidor, cambia `ips_conocidas` (deja de saltar el ámbar de sesión extraña) o sobrescribe las claves del Llavero. 127.0.0.1 es compartido por todos los usuarios del Mac. Lo que obtiene: todo lo que la base va a guardar y la capacidad de alterar la vigilancia. Los `0600` del control 1 y la «misma puerta endurecida que `/guardar`» del control 5 no tocan este camino: esa puerta es anti-CSRF, no autenticación.
Qué hacer: un secreto de sesión generado al arrancar, guardado `0600`, que la barra entregue al navegador una sola vez (URL con token → cookie `HttpOnly; SameSite=Strict; Path=/`) y que **todo** endpoint exija; o un socket Unix `0600` en vez de TCP. Para la aplicación final, autenticación real por usuario.

### 1.2 (a) Las vistas derivadas ya están en claro y legibles por todos — ALTA
Verificado en la instalación: `~/.cost-guard` es `drwxr-xr-x` (`install.sh:11` solo hace `mkdir -p`); `panel.html` `0644` de 110 KB con proyectos y consumo embebidos (`panel.py:90` abre sin modo); `logs/render/*.png` (40 capturas del desplegable: host, sesiones, IPs atacantes, protección que falta) y `popover_*.png`, todos `0644`; `logs/costbar.log` `0644` (`infobar/config_y_estado.py:16-19`, sin modo); `barra.log`/`barra.err` los crea launchd (`install.sh:60-61`). Proteger `consumo.sqlite` con `0600` mientras sus proyecciones siguen `0644` no cambia lo que un usuario local ve.
Qué hacer: `os.umask(0o077)` al arrancar `barra.py`/`costbar.py`/`panel.py`; `chmod 700` del directorio en `install.sh`; renders en un tmp `0700` y borrado tras usar; `Umask` en el plist.

### 1.3 (f) Fase 3 convierte datos hostiles en filas permanentes — ALTA
Hoy del servidor llegan 4 IPs (`infobar/remoto/recoger.sh:47`) filtradas con `^[0-9a-fA-F.:]{3,45}$` (`infobar/servidores_ssh.py:144`), que acepta `....` o `::::`; `sesiones.desde` es texto libre de 64 caracteres (`:172`); claves/valores de `bastionado` y nombres de servicio, 40/64 caracteres libres (`:199`, `:213`). El pintado actual escapa (`infobar/desplegable_comun.py:16`, `panel.py:20-33`), así que **hoy no hay XSS**. Pero «guardar la lista de IPs que intentan entrar» obliga a cambiar `recoger.sh` para enviar más IPs, y un servidor comprometido puede: inventar IPs de terceros (contaminación, falsa acusación en un export), inflar la base sin tope y meter texto que luego saldrá por el CSV.
Qué hacer: validar con `ipaddress.ip_address()` (ya se hace para `ips_conocidas`, `infobar/servidores.py:121`), rechazar IPs no públicas, tope de IPs por muestra (p. ej. 50) y de filas/día por servidor, longitud y charset `[\x20-\x7e]` en todo texto, y sección de `version` obligatoria para que un formato nuevo no se interprete a medias.

### 1.4 (c) XSS en la vista de histórico de la fase 2 — MEDIA
`app.js` pinta con `innerHTML` y plantillas (`panel_assets/app.js:150-157`, `:195`, `:287`); es seguro solo porque `panel.py:_sana` muta los datos antes de embeberlos. Los endpoints nuevos (`/historico`, `/export`) devolverán JSON que el JS pintará; si se repite el patrón de `app.js` sin `_sana`, hay XSS same-origin con `/guardar` y `/clave`. `ajustes.js` ya hace lo correcto (DOM, `:2`, `:7-14`).
Qué hacer: regla fija: la API devuelve crudo, la UI pinta con `textContent`/DOM; nunca «sanear en almacenamiento» (ver 4.3).

### 1.5 (d) Las copias de seguridad y las páginas borradas conservan lo caducado — ALTA
`consumo.sqlite.bak.<fecha>` (N copias) son copias en claro completas: una IP «caducada a los 90 días» sigue en las copias y, dentro de la base, en páginas libres y en el WAL (SQLite no sobrescribe al borrar salvo `PRAGMA secure_delete=ON`). La retención del control 4 es falsa si no se purgan copias con la misma caducidad.

### 1.6 Escaneo desde el Mac a terceros — BAJA (deriva de 1.1)
Quien pueda `POST /guardar` hace que el Mac lance `HEAD` a URLs y un barrido TCP de hasta 64 puertos (`infobar/servidores_externo.py:50-73`, `:90-107`) y `ssh` contra un host arbitrario. Ya limitado a IPs públicas (`infobar/servidores.py:19-46`) y `StrictHostKeyChecking=yes` (`servidores_ssh.py:29`). Se cierra con 1.1.

### Lo que está bien resuelto (una línea cada uno)
- DNS rebinding y CSRF: `Host` exacto + `Sec-Fetch-Site` + `Origin` exacto + `Content-Type` (`servidor_local.py:105-112`, `:161-166`). Bien.
- Claves de API fuera de la config y fuera de `ps` (`infobar/claves.py:45-46`); nombres de cuenta validados (`:10`). Bien.
- SSH del recolector: `restrict,command=`, sudoers de un solo binario, `IdentitiesOnly`, sin reenvíos (`servidores_ssh.py:25-31`, `remoto/instalar-en-servidor.sh:40-53`). Bien.
- Tope de bytes/tiempo en lo que devuelve el servidor (`servidores_ssh.py:12-13`, `:53-63`). Bien.
- `servidores.json` y `state.json` se escriben `0600` y atómicos (`config_y_estado.py:98-104`); `servidores.json` fuera de git (`.gitignore`). Bien.
- Imagen de docs con rangos RFC 5737 y nombres ficticios (`docs/img/desplegable-servidores.png`). Bien.
- SQLite 3.53.4 en el venv (Python 3.14): `STRICT` y `RETURNING` disponibles. Bien.

## 2. Controles propuestos que no funcionan como se cree

### 2.1 Cifrado a nivel de campo con clave en el Llavero (control 3) — no protege de (a-mismo uid) ni (b)
Verificado: `security find-generic-password -s infobar -a nan -w` devuelve el secreto **sin diálogo** (26 bytes, rc 0) porque los ítems creados por la CLI `security` llevan a `security` en su ACL; la app depende de eso cada minuto (`infobar/claves.py:26`, `cuota_nan`). Una clave de cifrado guardada igual la lee cualquier proceso del usuario con una línea. SQLCipher tiene el mismo problema: la app arranca sola con `RunAtLoad`/`KeepAlive` (`install.sh:49-63`), así que la clave tiene que estar disponible sin usuario. Conclusión: el cifrado local solo defiende de (d) copias fuera del disco. Y para (d) en disco local ya está FileVault.
En su lugar: minimizar (no guardar lo que no se va a mostrar), retención corta, y cifrar **las copias y los exports** (lo que sale de la carpeta) con una passphrase pedida en el momento, no con una clave residente. Si se quiere Llavero: ítem creado por una app firmada con ACL solo para ella y `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`; con un venv de Python eso no existe.

### 2.2 Exclusión de Time Machine (control 1) — hoy no está y es frágil
`tmutil isexcluded ~/.cost-guard` → **Included**. `tmutil addexclusion` sin `-p` es «sticky»: un xattr en el ítem. Si se aplica al **fichero**, cada `.bak.<fecha>`, el `-wal`, un `VACUUM INTO` o un `cp` nacen sin él. Aplicado al **directorio** cubre lo que contiene, pero no sobrevive a mover la carpeta ni a iCloud/Dropbox, que ignoran `tmutil`. En su lugar: excluir el directorio, comprobar `isexcluded` al arrancar y avisar; detectar `~/Library/Mobile Documents`, `~/Dropbox` y similares en la ruta; y, sobre todo, asumir que la copia saldrá (2.1).

### 2.3 `0600` frente a malware con los mismos permisos (control 1) — inútil, y lo sabe el plan
Ningún permiso POSIX separa dos procesos del mismo uid. La única barrera real en macOS es TCC (carpetas `Documents/Desktop/Downloads` y Full Disk Access), que exigiría una app firmada y diálogos; no compensa para un venv. Lo honesto: declarar (b) fuera del alcance de la base y defenderlo con minimización, retención y (en la app final) datos en servidor con autenticación.

### 2.4 Export CSV (control 6) — la neutralización es lo menos importante
El prefijo `'` en celdas que empiezan por `= + - @ \t \r` está bien, pero no se aplica a columnas numéricas (rompería negativos) y hay que usar `csv.writer` con `QUOTE_ALL` para que un nombre de proyecto con salto de línea no fabrique filas. El riesgo real es otro: el export es una copia en claro que el usuario moverá a `Downloads`/correo en segundos; `0600` y «carpeta propia» duran un minuto. Lo útil: columnas personales excluidas por defecto (ya está), aplicar `oculto` de `config.proyectos` también al export (hoy solo afecta a la UI, `infobar/agregacion.py:153-168`), tope de filas, columna `exportado_por/en`, y registro de auditoría (ya está). Para multiusuario: export por ámbito y con permisos.

### 2.5 HMAC de IPs en vez de guardarlas (control 3) — no resuelve ni lo legal ni lo útil
IPv4 son 2^32 valores: sin secreto se invierte en segundos; con secreto en el Llavero, quien lo lea (2.1) lo invierte igual. Un seudónimo con clave sigue siendo dato personal (RGPD, considerando 26 y 28). Y pierde el uso: no se puede mostrar la IP, consultar whois, ni cruzarla con `ips_conocidas` o fail2ban. En su lugar: IP en claro en una tabla aparte con caducidad corta y `secure_delete`; contadores agregados (total, por hora, por país grueso si se quiere) para siempre; HMAC solo si se quiere «atacante recurrente» a largo plazo, y entonces con clave rotada.

### 2.6 Log `0600` (control 9) — correcto, pero incompleto
`log()` no fija modo (`config_y_estado.py:16-19`); `barra.log`/`barra.err` los crea launchd. Hoy `costbar.log` tiene 30.287 líneas de «Address already in use» (dos instancias compitiendo por el puerto, `costbar.py:63` arranca `servir()` también en CLI): prueba de que **varios procesos** escribirán en la base; el plan lo contempla (WAL, `busy_timeout`), pero conviene cortar el bucle de log.

## 3. Privacidad y legal: IPs de terceros y retención

- Una IP (también dinámica) es dato personal: TJUE *Breyer* C-582/14. El considerando 49 del RGPD reconoce la seguridad de red como interés legítimo (art. 6.1.f), así que guardarlas para detectar ataques es lícito; lo que exige es **limitación del plazo** (art. 5.1.e) y proporcionalidad. 90 días es defendible si se documenta; 30 bastan para «pico» y fail2ban. Contadores agregados sin IP pueden quedarse para siempre.
- La retención tiene que ser real: `PRAGMA secure_delete=ON`, purga en el WAL (`checkpoint`), y las `.bak` caducan con el mismo plazo o se cifran (1.5). Un export con IPs es una nueva copia con su propio plazo.
- `sesiones.desde` (`servidores_ssh.py:171-172`) son IPs/hosts de **tu propio personal**; en la app final es dato de empleados del cliente: minimizar (solo «conocida/desconocida» y la hora) o mismo plazo.
- Nombres de proyecto/cliente: si el cliente es persona física (autónomos), es dato personal; además es información confidencial. Las rutas de los logs (`lectores_logs.py:81-83`, `:236-239`, `:340`) llevan nombre de usuario del Mac y estructura de carpetas: no deben ir a `fuentes.ruta` tal cual (ver 4.3).
- App final (varios usuarios/inquilinos): el cliente es responsable y vosotros encargados → contrato art. 28, registro de actividades, supresión por inquilino (borrar un servidor borra sus muestras e IPs: claves foráneas `ON DELETE CASCADE`), derecho de acceso (exportar lo de un ámbito, no más), y localización de datos (UE). Nada de esto cuesta ahora; cuesta mucho si el esquema no tiene dueño.
- No geolocalizar fino ni enriquecer con whois en la base (el plan ya lo dice): hacerlo bajo demanda y sin guardar.

## 4. Decidir antes de la fase 1 (para no cerrar la puerta a la app multiusuario)

4.1 **Tiempo en UTC.** Hoy las claves de hora son hora local sin zona (`lectores_logs.py:38-61` convierte a local; `:127-128` usa `ts[:13]`). Con `PRIMARY KEY (fuente_id, hora, …)` la hora repetida del cambio de octubre colisiona y dos dispositivos en zonas distintas no se pueden fundir. Guardar `hora_utc` (época de la hora) y derivar lo local al leer. CRÍTICA para multiusuario, ALTA ya.
4.2 **Dueño por fila desde el día 1:** `espacio_id` (no «usuario» de texto) en `fuentes`, `consumo`, `servidores_muestra`, `presupuestos`; toda función de `almacen.py` recibe el ámbito; ninguna consulta sin él. Claves foráneas con `ON DELETE CASCADE` para que borrar un ámbito/servidor borre de verdad.
4.3 **Identidad de fuente sin ruta:** `fuentes.ruta` absoluta filtra `/Users/<nombre>/…` y no es portable; guardar `tipo`, `dispositivo_id` (UUID por instalación), `id_fuente` estable (hash de la ruta relativa al origen o el `session_id` de OpenCode/Hermes) y la ruta solo si hace falta, en una columna marcada como local. Nombres de proyecto **crudos** acotados (longitud, sin caracteres de control), nunca saneados al guardar (`_sana`, `panel.py:20-33`, cambia la identidad y rompería `config.proyectos`).
4.4 **Clasificación por columna** (público / interno / personal / secreto) declarada en el esquema o en `almacen.py`, para que el exportador, los endpoints y la futura API filtren por clase y no por lista a mano. Las IPs de atacantes y `sesiones.desde` en tablas aparte con `caduca_en`.
4.5 **Semántica de permanencia para bases ajenas.** «`DELETE`+`INSERT` por `fuente_id`» con fuente = `opencode.db` entera borra las sesiones que OpenCode pode: contradice la fila «poda sesiones → las filas se quedan» de la tabla de riesgos. Fuente = sesión (id estable) o upsert sin borrado con `visto_en`.
4.6 **Migraciones numeradas en `meta`** con copia previa (ya está) y **identificadores UUID** en vez de autoincrementales para poder fundir bases de varios dispositivos.
4.7 **Transporte y auth como requisito de la API local**, aunque hoy sea un token local (1.1): que `almacen.py` nunca sea alcanzable sin ámbito autenticado.

## 5. Cambios al plan, ordenados

| # | Cambio | Gravedad |
|---|---|---|
| 1 | Autenticación en el servidor local (token de sesión o socket Unix) exigida en **todos** los endpoints, incluidos los de lectura; anotar que la puerta Host/Origin es solo anti-CSRF (1.1) | Crítica |
| 2 | Hora en UTC en el esquema; local solo al leer (4.1) | Crítica (multiusuario) / Alta |
| 3 | `umask 0077` en los procesos, `chmod 700` del directorio en `install.sh`, renders en tmp `0700`, `Umask` en el plist; `panel.html` y PNG `0600` (1.2) | Alta |
| 4 | Retención real: `secure_delete`, checkpoint, `.bak` con la misma caducidad o cifradas; purga cubierta por test (1.5, 3) | Alta |
| 5 | Sustituir «cifrado de campo con clave en el Llavero / SQLCipher» por: minimización + retención + cifrado de copias/exports con passphrase; declarar (b) fuera de alcance (2.1, 2.3) | Alta |
| 6 | Validación estricta del recolector antes de persistir: `ipaddress`, solo públicas, topes por muestra y por día, charset (1.3) | Alta |
| 7 | Dueño (`espacio_id`), `dispositivo_id`, fuentes sin rutas, FK `ON DELETE CASCADE`, UUIDs (4.2, 4.3, 4.6) | Alta |
| 8 | Quitar el HMAC de IPs; tabla aparte de IPs con `caduca_en` 30-90 días y contadores agregados permanentes (2.5) | Media |
| 9 | Exclusión de Time Machine sobre el **directorio**, comprobada al arrancar, con aviso si la ruta está bajo iCloud/Dropbox (2.2) | Media |
| 10 | Regla UI: endpoints devuelven crudo, el JS pinta por DOM; prohibir el patrón `innerHTML` de `app.js` para datos nuevos (1.4) | Media |
| 11 | Fuente = sesión para bases ajenas; upsert sin borrado (4.5) | Media |
| 12 | Export: `csv` con `QUOTE_ALL`, prefijo solo en columnas de texto, respetar `oculto`, tope de filas, columna de auditoría (2.4) | Media |
| 13 | Clasificación por columna en el esquema para filtrar exports/API por clase (4.4) | Media |
| 14 | Log: modo `0600` en `log()`, cortar el bucle «Address already in use», no escribir rutas de logs ajenos (2.6) | Baja |
| 15 | Referencias de validación en `tests/datos/` con datos sintéticos (hoy solo hay fixtures de servidor con nombres ficticios); nunca una captura real de `--print` | Baja |

## Supuestos

- La instalación real es `~/.cost-guard` (comprobado por `ls`); en desarrollo `rutas.RAIZ` es el repo, así que `consumo.sqlite*` debe entrar en `.gitignore` antes de la fase 1 (confianza alta).
- Se asume que la app final seguirá teniendo un componente local (barra) que lee logs del equipo; si todo pasa a servidor, el punto 5 cambia a cifrado en reposo del lado servidor con clave por inquilino (confianza media).
- Las afirmaciones legales son de conocimiento general del RGPD/jurisprudencia citada, no de asesoría jurídica; si hay clientes fuera de la UE, revisar (confianza media).
