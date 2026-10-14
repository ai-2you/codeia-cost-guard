# Plan: histórico permanente en SQLite (v2, tras pre-mortem y red team)

Estado: **v2 para revisar**, sin código todavía. Fecha: 2026-10-14.
Informes que lo respaldan: `../reports/red-team-261013-sqlite-historico.md` y
`../reports/pre-mortem-261013-sqlite-historico.md`. La v1 (agregados por hora con clave de ruta) queda
sustituida: ambos informes demostraron que fijaba errores y fugas.

## Resultado buscado
Una base SQLite local y permanente para el consumo medido (y después el estado de los servidores), con
histórico más allá de lo que duren los logs, presupuestos con alertas y exportación. La configuración
(`config.json`, `servidores.json`) sigue siendo JSON editable desde Ajustes. El destino final es una
**aplicación** que haga todo esto: nada del diseño puede suponer "un solo usuario en un solo Mac".

Decisiones del dueño: objetivos = histórico permanente, menos trabajo por minuto, presupuestos y
alertas, histórico de servidores; retención por hora para siempre; abordaje por fases; exportador sí;
presupuestos por proyecto y por plan; se guardan las IPs que intentan entrar; todo plan y toda fase
pasa por pre-mortem y red team antes de codificar (información sensible).

## No objetivos
Mover la configuración a SQLite · subir datos a la nube · defender los datos frente a malware que ya corre
con tu usuario y puede leer el Llavero y todo `~/.cost-guard` (se documenta como fuera de alcance; la
defensa real ahí es FileVault, minimización y retención).

## Hallazgos que cambian el plan (verificados)
1. **La app ya cuenta de más el consumo de Claude Code.** Claude Code escribe varias líneas por mensaje con la
   misma entrada y caché; la deduplicación de `parsear()` incluye el segundo de la marca de tiempo y no las
   une. Comprobado con una muestra independiente de los 150 logs más recientes: 31.261 líneas con uso para
   16.121 mensajes únicos; con la regla actual salen **1,70×** los tokens que por mensaje único. Los forks
   tampoco se deduplican entre ficheros. Si esto se grabara en un histórico permanente, el error sería
   irreparable. **Se corrige antes de acumular nada.**
2. **El servidor local no tiene autenticación**: Host/Origin/Content-Type frenan a las webs del navegador, no a
   otro proceso del Mac. `/ajustes` entrega hosts, usuarios SSH y proyectos con solo poner la cabecera Host.
3. **Datos sensibles legibles por cualquiera del Mac hoy:** `~/.cost-guard` en 0755, `panel.html` y
   `costbar.log` en 0644, y los PNG del desplegable en `logs/render` (con host e IPs de servidores).
4. Las claves de cifrado en el Llavero no protegen frente a un proceso del mismo usuario; el HMAC de IPs
   se invierte (2^32 valores) y sigue siendo dato personal; Time Machine incluye la carpeta hoy.
5. Claude Code borra sus logs a los 30 días: cada semana de retraso es histórico que se pierde.

## Fase 0 — antes de tocar la base (rama propia, entregable por sí misma)
**0a. Cerrar la app actual.**
- Autenticación del servidor local: token aleatorio por arranque (fichero 0600), entregado a la barra, que abre
  el panel con él; el servidor lo cambia por una cookie `HttpOnly; SameSite=Strict` y **rechaza todo sin ella**,
  lectura incluida. La puerta Host/Origin se mantiene como anti-CSRF.
- Permisos: `umask 0077` en todos los procesos, `chmod 700` de `~/.cost-guard` en `install.sh` y `Umask` en el
  plist; `panel.html`, `costbar.log` y copias en 0600; renders del desplegable en un directorio privado y
  borrados tras usarlos.
- `log()` sin rutas de logs ajenos ni IPs y con corte del bucle "Address already in use".
**0b. Corregir el conteo** (`parsear`, `parsear_pi`, lectores de bases): una vez por mensaje (`message.id`),
entrada y caché una sola vez, la última salida; deduplicación entre ficheros para forks; fórmula de coste sin
restar la caché dos veces; `CLIFF` por turno. Tests con fixtures sintéticos. **Efecto esperado: las cifras de
Claude Code bajan en torno a un 40 %**; se explica en la interfaz (nota de versión) y en el README.
**Criterio de salida:** nuevo test de abuso pasa (otro proceso sin cookie recibe 401/403 en todo), permisos
verificados por test, y el conteo corregido coincide con una cuenta independiente sobre un corpus sintético.

**Resultado de la fase 0 (revisión `../reports/revision-fase0-261014.md`):** corregidos el ticket visible en `ps`
(la URL se abre con `NSWorkspace`), el parseo de cookies, la cookie por puerto, el recargo `CLIFF` sobre sumas diarias,
la ruta en logs de Hermes y la purga de renders tras cada pintado. **Aplazado a la fase 1 (consciente):** deduplicar
mensajes entre ficheros (6 de 60.636 ids en la muestra) y verificar con la documentación de Hermes que su entrada
excluye siempre la caché. **Límite aceptado:** las cookies no se separan por puerto; un servidor ajeno en `127.0.0.1`
podría leerla (documentado en el README); la solución real es un socket Unix o la aplicación final.

## Fase 1 — almacén de turnos (solo añadir) — v3, tras la segunda ronda
**Avance (rama `feat/fase1-almacen`):** 1.1 hecho — `infobar/plataforma.py` (directorio de datos por sistema, rutas no fiables) y `infobar/almacen.py` (esquema `STRICT`, PRAGMA por conexión y WAL verificado, upsert monótono con anomalías, `horas` materializado en la misma transacción, escritor único por latido, solo-lectura `mode=ro`, copias con la API de backup) con 31 tests. **Pendiente:** 1.2 ingesta por agente con sus claves, 1.3 `consultar()` desde la base y partir `escanear()`, 1.4 sombra, 1.5 export cifrado.
Informes: `../reports/red-team-fase1-261014.md` y `../reports/pre-mortem-fase1-261014.md`.

**Esquema** (`consumo.sqlite`, aparte del futuro `servidores.sqlite`; `STRICT`):
```
meta(clave, valor)                          -- version_esquema, version_minima_escritor, productor, dispositivo_id, latido del escritor
espacios(id, nombre)                        -- dueño de cada dato; hoy una fila
fuentes(id, espacio_id, dispositivo_id, tipo, id_fuente, visto_en)   -- id_fuente = ruta relativa normalizada o hash; sin rutas absolutas
turnos(clave TEXT PK, espacio_id, fuente_id, hora_utc INTEGER /*segundos*/, agente, proveedor, modelo,
       gi, go, cr, cw, proyecto_raw, productor_version)
anomalias(...)                              -- conflictos, marcas sin zona, deltas negativos; visible en el panel
horas(...)                                  -- agregado horario materializado en la misma transacción
```

**Regla de escritura (decisión crítica de ambos informes):** upsert **monótono**
(`ON CONFLICT DO UPDATE … SET c = max(c, excluded.c)` por contador; nunca baja un contador ni mueve `hora_utc`), con
rangos por campo y una fila en `anomalias` cuando cambia el modelo o baja un contador. `INSERT OR REPLACE` queda
**prohibido**: dejaba que el orden de lectura o una línea inyectada en un `jsonl` decidieran la cifra para siempre.
Tests en orden directo e inverso y con copia truncada. Nunca `DELETE` por fuente.

**Claves por agente (con prefijo de agente; nunca `requestId`; prohibido filtrar `isSidechain`):**
- Claude: `message.id`, más `sessionId` si el id no tiene forma de Anthropic (OpenCode y backends no-Anthropic usan
  `msg_<ms>_<sufijo>` predecible).
- Codex: `session_id` de la primera `session_meta` + `total_tokens` acumulado (0 colisiones medidas entre ficheros).
- pi: id de sesión + id de línea. OpenCode: `message.id` en **todas** las `opencode*.db`, con aviso de "fuente callada".
- Hermes: `session_id` + `model` + `task` + total acumulado; fuente `session_model_usage`; fecha `last_seen`/
  `last_activity_at`, nunca la de la lectura; deltas negativos descartados y anotados.

**Escritor único y no bloqueo:** se parte `escanear()` en `ingerir()` y `consultar()`; los 8 llamadores pasan a
`consultar()` (incluido el temporizador de `costbar.py`, que nunca ingiere). Escritor único por arrendamiento/latido
en `meta` con `BEGIN IMMEDIATE`, sin `fcntl`; el resto abre `mode=ro`. La ingesta va en un hilo propio, separado del
ciclo de servidores, con lotes de ≤ 200 ms. Criterio: con la base vacía y 4 consultores a la vez, ninguno tarda más
de 1 s y los servidores siguen entregando. Test: `panel.py` y el servidor no llegan a ningún `INSERT`.

**Conexión y PRAGMA:** una sola función `abrir()` fija `secure_delete`, `foreign_keys`, `journal_size_limit`,
`busy_timeout` en **cada** conexión (son por conexión), comprueba que `journal_mode` devuelve `wal`, rechaza rutas
en nube/red y avisa si el volumen ignora la propiedad de ficheros (el disco EVO990 la tiene desactivada); checkpoint
`TRUNCATE` periódico; sin `max_page_count`; SQLite ≥ 3.37 comprobado al abrir y **Python mínimo 3.11** (README).
`quick_check` diario, nunca `integrity_check` en la barra.

**Hora:** `hora_utc` en segundos con camino propio `a_epoch_utc` que exige zona; marcas sin zona a cuarentena visible;
lo local solo en `consultar()`; zona inyectada (no `tzset`); fixtures 29-03-2026 y 25-10-2026 (día de 25 h), tres zonas
incluida una de media hora.

**Datos y volumen (medido):** ~1,4 M turnos/año; a 3 años ≈ 700 MB y 2 s para consultar todo; `integrity_check` 11 s;
el reparseo en frío tarda ~14 s. Topes con una base sintética de 3 años: ventana 5 h ≤ 5 ms, 31 días ≤ 50 ms, mensual
≤ 100 ms, ≤ 1 GB. Lectura **incremental** por desplazamiento (inodo + hash de cabecera, línea partida no avanza),
≤ 300 ms por vuelta con un fichero vivo de 250 MB. Upsert condicional para no reescribir miles de filas por minuto.

**Export cifrado propio (decisión del dueño: portable a Windows):** dependencia `cryptography` fijada con hashes +
`scrypt`; formato versionado `.cgx` por trozos con la cabecera como AAD y bandera de último trozo; temporal solo en un
directorio privado (o `serialize()`) con `temp_store=MEMORY`; la passphrase solo se pide en barra y CLI, nunca en el
panel web, y no se promete borrado en memoria (imposible en CPython); copias automáticas con **cifrado a destinatario**
(clave privada fuera del equipo). Criterios de salida: vector de prueba, ida y vuelta, manipulación, truncado y una
restauración ensayada en Windows.

**Windows:** `infobar/plataforma.py` (cerrojo, rutas, permisos, secretos); datos en `%LOCALAPPDATA%` con aviso si es una
carpeta sincronizada; `pathlib` en los lectores; `tzdata`; reintentos en `os.replace` y en `BUSY`; tests con rutas
`ntpath`.

**Ruta de datos:** `rutas.DATOS` independiente de `RAIZ`; el checkout de desarrollo no escribe sin `COSTGUARD_DATOS`;
`consumo.sqlite*` en `.gitignore` **antes** del primer commit; `rutas.ALMACEN` en `tests/aislamiento.py`.
`proyecto_raw` recorta `Users/<x>` (con migración de claves de `config.proyectos`).

**Fase 1b en sombra (medible):** las dos rutas salen de la **misma lectura** y solo se comparan días cerrados;
tolerancia 0 en Codex, pi y OpenCode, y solo categorías firmadas en Claude y Hermes; residuo sin explicar = 0 durante
7 días seguidos con el contador a cero tras cada divergencia; a los 3 reinicios decide el dueño; eventos obligatorios
dentro de la ventana: reinicio, guardado de Ajustes, reposo y tres agentes. **El calendario debe incluir el cambio de
hora del 25-10-2026.**

**Marcha atrás ensayada:** `VERSION_ESTADO` congelado, `olvidar_cache()` sin tocar la base, ingesta mínima también en
`main` mientras dure la fase, `version_minima_escritor`; ensayo de ida, 48 h de vuelta e ida, comparado con un control.
Copias con `VACUUM INTO`/backup, con la misma caducidad que los datos personales.
Limpieza menor: no recorrer los 1.263 `*.json` sueltos de `~/.claude/projects`; el `CLIFF` por turno en Hermes va a
las divergencias aceptadas.

## Fase 2 — histórico, presupuestos y exportador
- Vista histórica en el panel (meses, comparativas, proyección de fin de mes).
- Presupuestos por proyecto **y** por plan (`ambito`, `nombre`, `periodo`, `tokens`), con alertas en la barra.
- **Exportador:** CSV con `QUOTE_ALL` y neutralización de fórmulas en columnas de texto (`= + - @` y tabulador),
  fichero 0600 en una carpeta propia (no `~/Downloads`), **IPs y hosts fuera por defecto** y solo con
  confirmación explícita, tope de filas, respeta `oculto`, y cada export queda en un registro de auditoría. Las
  copias en claro son el riesgo mayor: se avisa y se ofrece export cifrado con passphrase.
- Reglas de UI: los endpoints devuelven datos crudos y el JS pinta por DOM; **prohibido** el patrón
  `innerHTML` de `app.js` para datos nuevos.
- Clasificación por columna (público / interno / personal / secreto) en `almacen.py`, para que export y API
  filtren por clase y no por lista a mano.

## Fase 3 — servidores
- `servidores.sqlite` aparte. Se graba **solo lo nuevo** por ciclo (hoy el ciclo repite lecturas ~12× por hora).
- Métricas tipadas (calibre o contador con reinicios), muestra cruda N días y luego compactada por hora.
- **IPs de quien intenta entrar:** tabla aparte con `caduca_en` (**propuesta 90 días**, configurable; contadores
  agregados permanentes), validadas con `ipaddress` (solo públicas), con topes por muestra y por día, y fuera
  de las copias pasado el plazo. Las IPs propias (`sesiones.desde`) fuera de la base o seudonimizadas. Un
  servidor comprometido no puede contaminar la base: charset, longitud y número acotados antes de persistir.
- Nunca se guarda una clave; `purga` cubierta por test (`secure_delete`, checkpoint, copias).

## Seguridad transversal
- Acceso a datos **solo** por `infobar/almacen.py` con consultas parametrizadas; toda función recibe el
  `espacio_id`; `CHECK`, tipos estrictos, longitudes acotadas; nombres de proyecto **crudos** acotados (sin
  caracteres de control), nunca saneados al guardar (el saneado es solo de presentación).
- Cifrado: FileVault como base + minimización + retención + cifrado de copias y exports con passphrase.
  **Se descarta** "cifrado de campo con clave en el Llavero" y SQLCipher con clave en el Llavero (un proceso del
  mismo usuario la lee sin diálogo) y el HMAC de IPs.
- Time Machine: exclusión del **directorio** comprobada al arrancar, con aviso si está bajo iCloud/Dropbox.
- `quick_check` diario (no `integrity_check` en cada apertura); migraciones numeradas con copia previa;
  UUIDs en vez de autoincrementales para poder fundir dispositivos.
- Camino a la aplicación: `espacio_id` y `dispositivo_id` desde la fase 1; auth y TLS como requisito de la
  API; encargado del tratamiento y supresión por inquilino cuando haya más usuarios.

## Privacidad y legal
Una IP es dato personal (TJUE C-582/14). Base proporcionada: interés legítimo (considerando 49) con plazo
real y corto (30–90 días), sin geolocalización fina. Nombres de cliente persona física también cuentan.

## Decisiones del dueño (cerradas el 2026-10-14)
1. **Bajada de cifras de Claude Code (~40 %)**: aceptada. Se explica en el README (sección "Cómo cuenta") y en las notas de versión.
2. **Copias del histórico:** **export cifrado propio** (no Time Machine), porque el producto final tiene que funcionar
   también en **Windows**. Consecuencias para el diseño: el cifrado del export (passphrase, KDF y AEAD) debe ser
   portable y no depender de Llavero/Time Machine; la base y las copias no se apoyan en `chmod`/`tmutil`; el
   almacén de secretos pasa a una interfaz por sistema (Llavero en macOS, Credential Manager en Windows); y los
   permisos de la fase 0 tienen que tener equivalente por ACL en Windows (hoy `permisos.py` solo actúa en
   macOS/Linux y deja Windows sin tocar, documentado).
3. **Fase en sombra:** 7 días (propuesta aceptada por defecto; se ajusta si el pre-mortem de la fase 1 lo pide).
4. **Caducidad de IPs de atacantes:** 90 días, configurable.
5. **Orden:** fase 0 ya, en una rama aparte (`feat/fase0-seguridad-y-conteo`).

## Decisiones de la fase 1 (cerradas el 2026-10-14)
1. **Regla de conflicto:** el máximo gana (upsert monótono). ✔
2. **Dependencias:** `cryptography` fijada con hashes (y `tzdata` en Windows). ✔
3. **Python mínimo 3.11** (README actualizado al implementar). ✔
4. **Copias automáticas:** cifrado a destinatario con la clave privada fuera del equipo. ✔
5. **Hermes:** **cuentan** las tareas auxiliares (`session_model_usage`). Sus cifras suben entre un 17 % y un 47 %
   frente a `sessions`; va a la lista de divergencias aceptadas de la sombra y a las notas de versión. ✔
6. **Quién escribe cuando la barra está cerrada:** el CLI (o el panel) toma el relevo solo si la barra lleva un rato sin latido, y nunca a la vez que ella. ✔ (opción A)
7. **Ruta de los datos:** el **directorio estándar de datos de aplicación de cada sistema**, no el checkout ni la
   carpeta de código: macOS `~/Library/Application Support/CostGuard`, Windows `%LOCALAPPDATA%\CostGuard`, Linux
   `~/.local/share/costguard` (o `$XDG_DATA_HOME`). `rutas.DATOS` apunta ahí; el checkout de desarrollo no escribe sin
   `COSTGUARD_DATOS`. ✔ (propuesta del dueño, coincide con la recomendación del red team).
8. **`proyecto_raw`:** se recorta `Users/<usuario>`, con migración de las claves de `config.proyectos`. ✔
9. **Sombra:** 7 días incluyendo el cambio de hora del 25-10-2026. ✔

## Puerta de verificación (cada fase)
Pre-mortem → red team → cambios al plan → informe en `plans/reports/` → solo entonces código. Tras
implementar: revisión de código con foco en seguridad y pruebas de abuso (inyección en nombres, CSV con
fórmulas, Host/Origin falsos, petición sin cookie, base con permisos incorrectos, copia sin cifrar).
