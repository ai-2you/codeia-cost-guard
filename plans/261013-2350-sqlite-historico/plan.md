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
**Avance (rama `feat/fase1-almacen`):** 1.1 hecho — `infobar/plataforma.py` (directorio de datos por sistema, rutas no fiables) y `infobar/almacen.py` (esquema `STRICT`, PRAGMA por conexión y WAL verificado, upsert monótono con anomalías, `horas` materializado en la misma transacción, escritor único por latido, solo-lectura `mode=ro`, copias con la API de backup) con 51 tests; revisado por un agente (`../reports/revision-fase1-1-261014.md`) y corregidos sus 12 hallazgos: texto sin NUL/control/sustitutos, clave con prefijo de agente, identidad distinta no suma contadores, agregado por espacio de la fila, `fuente_id` sin cascada, URI de solo lectura escapada, migración bajo cerrojo, identidad de escritor por proceso (`identidad()`), anomalías únicas, copias privadas sin depender de la umask y con rotación por prefijo, rutas por componentes. **1.2a hecho (rama `feat/fase1-ingesta-claude`):** `infobar/ingesta.py` (UTC con zona obligatoria, lectura incremental con inodo + hash de cabecera, reanudable, líneas hostiles anotadas) e `infobar/ingesta_claude.py` (clave `claude:<message.id>`, proyecto sin `Users/<usuario>`). Tabla `lecturas` en el esquema; el almacén tolera 1 h de deriva de hora entre líneas del mismo mensaje y conserva la primera. **Sombra preliminar sobre los 1.370 logs reales de Claude (base temporal):** 123.503 turnos, ingesta completa 17 s (el parser actual 15 s), consulta 45 ms, base 24 MB. Diferencia total con el parser actual −0,066 % de tokens, explicada por (1) mensajes que aparecen en dos ficheros (forks): el parser los cuenta dos veces y la base una (−102 turnos), y (2) atribución de modelo: el parser arrastra el último modelo visto y la base usa `message.model` (2,1 M de tokens pasan de `opus` a `deepseek-v4-flash`); ambas son **divergencias aceptables a firmar** en la sombra. **Revisión de 1.2a (`../reports/revision-fase1-2a-261014.md`) corregida:** una línea hostil ya no bloquea un fichero (cada línea aislada y validada antes de fusionar), `leer_nuevas` usa un solo descriptor y comprueba cabecera **y cola** (el test de reescritura en el mismo inodo falla sin ese control: comprobado por mutación), las líneas enormes se saltan enteras, proveedor por modelo (vacío si no es Claude), `null` cuenta como 0, el proyecto quita el nombre de usuario completo, zonas horarias absurdas rechazadas, FIFO sin colgar, anomalías por fuente. Resultado sobre los logs reales: 123.623 turnos, 18,7 s, diferencia −0,066 % de tokens. **Divergencias a firmar en la sombra (Claude):** forks (el parser cuenta dos veces), atribución de modelo, 114 mensajes que cruzan hora y 7 que cruzan día local (la base conserva la hora de la primera línea y el parser la de la última), y 5 líneas de uso que vienen de un MCP. **1.2b Codex hecho (rama `feat/fase1-ingesta-codex`, revisado en `../reports/revision-fase1-2b-codex-261014.md`):** `infobar/ingesta_codex.py` lee **`token_usage_record`** con clave `codex:<response_id>` (único; los forks copian las líneas del padre con los mismos ids y se deduplican solos). La primera versión leía `token_count` y la revisión demostró dos fallos: tras una compactación `token_count` trae el uso a cero y el consumo real solo está en el registro (52 respuestas, 13,9 M de tokens perdidos), y `token_count` re-emite eventos con el mismo acumulado en segundos distintos (212, 37,98 M). Un `token_count` sin registro previo se anota y no se cuenta. Entrada fresca = input − cached; modelo (`turn_context`) y proyecto (`cwd`) persistidos entre pasadas en `lecturas.contexto` (ahora sin sustitutos ni control: antes un sustituto suelto bloqueaba el fichero). Esquema v1 de desarrollo obsoleto → error claro (no existe ninguna base v1 previa fuera de pruebas). Sombra sobre los 47 rollouts reales: 7.534 turnos en 1,3 s; −2,05 % de tokens frente al parser actual, que **cuenta las re-emisiones** y atribuye el modelo por el último `"model"` visto (la base usa `turn_context`): divergencias a firmar. **1.2c pi hecho (rama `feat/fase1-ingesta-pi`, revisado en `../reports/revision-fase1-2b-2c-261014.md`):** `infobar/ingesta_pi.py` con clave `pi:<responseId>` (fallback `pi:<sesión>:<id de línea>`: el `id` de línea son 8 hexadecimales, colisión del 25 % a 50.000 turnos), proveedor por turno y proyecto unificado. **`reasoning` ya va dentro de `output`** (medido por el revisor y por mí: `totalTokens = input+output+cache` en los 9.620 turnos, `reasoning <= output` siempre): el parser actual lo sumaba e inflaba la salida de pi un 52 % (3,40 M de tokens); corregido también `parsear_pi`. La revisión confirmó que Codex por `token_usage_record` cuadra al token con un recuento independiente (1.159.755.623 tokens; solo se desplaza un turno de compactación al modelo anterior: 269.647 tokens, aceptado) y que pi cubre todos los turnos; la hora de pi es la de la línea (`timestamp` ISO), igual que Claude y Codex. Sombra sobre los 22 ficheros reales tras la corrección: 9.620 turnos y 0 tokens de diferencia con el parser corregido. **1.2d OpenCode hecho (rama `feat/fase1-ingesta-opencode`):** `infobar/ingesta_opencode.py` lee `opencode.db` **y** `opencode-.db` como fuentes distintas (el parser actual solo miraba la primera), solo `assistant` con `modelID`, clave `opencode:<id de mensaje>` (4.885 ids únicos), avance por `(time_updated, id)` con paginación (los mensajes que se completan después suben sus tokens por la regla del máximo), base sin cambios ni se abre (tamaño+fecha de la base y su WAL), filas podadas que no se pierden, `json_valid` para que una fila rota no tumbe la consulta. Real: 4.351 turnos en 0,28 s, **coincidencia exacta con un SQL independiente** (21 modelos). **Pendiente de decisión del dueño:** en OpenCode `tokens.reasoning` va aparte (supera a `output` en 550 mensajes y los totales de sesión no lo suman) y el parser actual no lo cuenta (502.293 tokens, 30 % de la salida); no se suma aquí porque no se sabe si algún proveedor ya lo incluye en `output`. **1.2e Hermes hecho (rama `feat/fase1-ingesta-hermes`):** `infobar/ingesta_hermes.py` lee `session_model_usage` (tareas auxiliares incluidas, decisión del dueño): una fila acumulada = un turno con clave `hermes:<sesión>:<huella de la clave primaria real: modelo, proveedor, URL, modo y tarea>` (la primera versión fundía dos filas de la misma sesión y modelo con distinto proveedor y perdía 79.000 tokens: lo destapó la comparación con la tabla real), contadores por máximo, hora = `first_seen` (inmutable; con `last_seen` la identidad se movería; todo el consumo de una sesión larga cae en su primera hora, igual que el lector anterior; repartirlo exigiría deltas y perdería la idempotencia), jamás la hora de la lectura, un perfil por fuente sin entrar en la clave. Real: 127 turnos de 4 perfiles en 0,02 s, **coincidencia exacta con la suma directa de la tabla** (66,3 M entrada, 7,1 M salida, 810,5 M caché leída); frente al lector actual (`sessions`) +17,9 % de tokens por las tareas auxiliares. Extraídas a `ingesta.py` las ayudas comunes de las fuentes SQLite ajenas (`firma_sqlite`, `abrir_sqlite_solo_lectura`, `aplicar_filas`). **Revisión de 1.2d/1.2e (`../reports/revision-fase1-2d-2e-261014.md`) corregida:** (A1) **Hermes: falta la base raíz `~/.hermes/state.db`** (perfil por defecto, activa, 120,5 M tokens de entrada frente a 66,3 M de los 4 perfiles juntos; el lector actual tampoco la leía: **el panel actual infravalora Hermes**); ahora `ingesta_hermes.bases()` la incluye: 261 turnos, 186,9 M de entrada, coincidencia exacta con la suma directa. (M1) OpenCode relee un margen de 5 minutos al empezar un drenado (mensajes confirmados tarde por otro proceso no se pierden) y usa marca exacta a mitad de uno (`drenando`). (M2) `time_updated` siempre `CAST(... AS INTEGER)`, ruta absoluta, errores de ruta capturados. (M3) la URL del proveedor de Hermes se guarda sin credenciales, consulta ni fragmento (esquema+host+ruta; si pasa de 120, esquema+host): antes una URL larga descartaba la fila y una con credenciales las guardaba. (M4) **reasoning**: OpenCode va aparte de `output` → se SUMA (+502.293 tokens); Hermes lo lleva dentro de `output_tokens` según su código → no se suma. Bajos: bases ajenas sin WAL se abren `immutable=1` (no crean `-wal`/`-shm` ni exigen escribir en el directorio), la firma incluye ctime, las filas descartadas cuentan como anomalías, la anomalía de JSON roto no se duplica. **Decisión pendiente del dueño (A2):** el 96 % de los tokens de Hermes está en filas de más de 1 h y el 85,5 % en filas de más de 24 h; todo cae en `first_seen`, así que las vistas de 5 h y de hoy salen mal para Hermes (igual que hoy con `started_at`). Mejora posible y futura: guardar instantáneas acumuladas por hora de `last_seen` y repartir por diferencias al consultar (idempotente, más trabajo). Pregunta abierta: el nombre de perfil de Hermes que hoy sale en la interfaz no se puede recuperar del hash de la fuente. **Aplazado de la revisión:** detectar volúmenes de red montados con nombre arbitrario y el aviso de volumen sin propiedad de ficheros (EVO990) se resuelven en 1.2 con comprobación activa; solo se comprueba WAL al abrir. **Pendiente:** 1.2 ingesta por agente con sus claves, 1.3 `consultar()` desde la base y partir `escanear()`, 1.4 sombra, 1.5 export cifrado.
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

### 1.3 — diseño v2 (tras `../reports/revision-diseno-1-3-261014.md`)
**Objetivo:** el informe sale de la base sin cambiar el formato que consume `agregar()` (salvo la clave de `horas`, ver 3), y el panel, la barra y el servidor nunca escriben.

1. **Lectura** (`infobar/consulta.py`, `mode=ro`, una conexión por llamada, nunca compartida entre hilos): reconstruye `vistos` (un registro por agente, proyecto y plan con `dia`, `turnos`, `horas`) y llama a `agregar()`. Antes de consultar comprueba `user_version` (mayor que la conocida → error) y envuelve la consulta en `sqlite3.DatabaseError`; en ambos casos `informe()` cae a `escanear()` anotándolo **una vez por proceso**. Sin `quick_check` aquí. El día local sale de `turnos` (índice por hora) para que las zonas de media hora partan bien; `horas` solo se usa para `ritmo` y `cinco_h`. Hermes lee de `turnos ⋈ fuentes` (el perfil se recupera casando huellas de los perfiles presentes; si el perfil ya no existe sale `hermes/<huella corta>`, firmado).
2. **Plan y proyecto:** `plan_de(agente, proveedor)` **byte a byte** igual que hoy, porque es la clave de los `aliases`: Claude → `"Claude (Max)"` constante (`plan_ccs` es código muerto hoy, activarlo sería otra decisión); Codex → alias del proveedor (`openai` → `"OpenAI (Codex)"`); pi y OpenCode → proveedor tal cual; Hermes → URL pública o proveedor facturador. **Nombres de proyecto unificados** (un directorio es un solo proyecto en todos los agentes; cambio de producto a firmar, con nota de versión). La migración de `config.proyectos` es **aditiva**: se conserva la clave antigua y se añade la nueva; `nombre_proyecto` prueba las dos, así `apagado` y `activo` leen la misma configuración.
3. **`agregar()` acepta como clave de `horas` un epoch entero de inicio de cubo** (y sigue aceptando el texto de `escanear()`); en el día de 25 h el `mktime` sobre texto local fallaba.
4. **Coste (USD):** la base no guarda dinero. Las horas de `ritmo` y `cinco_h` se calculan desde `turnos` de las últimas ~6 h con el recargo por contexto largo por turno; los días, sin recargo, como hoy. El USD de pi (`cost.total`) no está en la base: divergencia firmada, solo informativa. `fuentes`, `bases` y `nuevos` salen de `fuentes` recientes y del resumen de la última vuelta (en `meta`); un contador de anomalías va en el informe.
5. **Ingesta** (`infobar/ingestor.py`): `ingerir()` recorre las cinco fuentes. **Solo la barra y `costbar.py --ingerir` pueden ser escritores**; `panel.py`, `popover.py`, `servidor_local.py` y `analizar_gasto.py` solo abren solo-lectura (test estático de imports y test del subproceso con el directorio de datos de solo escritura bloqueada). El hilo de ingesta readquiere el puesto **al empezar cada vuelta** (el latido solo lo refrescaba `ingerir_turnos`) y da una vuelta al menos cada 30 s. Carga una vez `lecturas ⋈ fuentes`, hace `stat` y solo entra en `ingerir_fichero` si `(inodo, tamaño, mtime_ns)` cambia; `visto_en` se actualiza en lote al final. Encadena vueltas (cediendo ≥ 50 ms) hasta que todas las fuentes están `completo`; entonces escribe `meta.construccion_completa` y duerme 60 s. Ese hilo también ejecuta `mantenimiento()` y el `quick_check` diario tolerando `busy`. Nunca ingiere el temporizador ni los endpoints.
6. **Interruptor `almacen` con dos ejes:** `apagado` = lee con `escanear()` e ingiere; `sombra` = lee con `escanear()`, ingiere y compara; `activo` = lee con `consultar()` e ingiere; `desactivado` (sin ingesta) solo a mano en `config.json`. Valor inicial `sombra`. `informe(dias)` en `agregacion.py` es el único punto; los 8 llamadores pasan por él. En `activo`, mientras falte `construccion_completa` cae a `escanear()`. La vuelta atrás no borra la base.
7. **Sombra honesta:** compara **antes de `agregar()`**, por `(agente, día local, proveedor, modelo)` en enteros; del lado antiguo sale de `vistos` (la ruta dice el agente), del nuevo de `turnos`. Solo días cerrados de ≤ 27 días y, en Claude, sin los días cuyo fichero ya no existe (Claude borra a los 30 días). **Hermes fuera de la comparación numérica** (validado por la suma directa de la tabla; solo se registra que no baja). Las categorías firmadas se **calculan**, no se declaran: forks = claves vistas en más de una fuente; atribución de modelo = celdas donde solo cambia `modelo` con el mismo total; cruce de hora o día = turnos a menos de 1 h del límite; lo que no cae en una categoría es residuo. Se registra además, sin bloquear, la diferencia de `cinco_h` y `ritmo` en el mismo instante. **No se cierra antes del 28-10-2026** (el 25 y el 26 deben ser días cerrados); siete días cerrados seguidos son 8-9 de calendario. El fichero de divergencias se crea 0600, sin URLs completas y con rotación. Se firma que el 25-10 entre 02:00 y 03:00 la ruta antigua es la equivocada.
8. **Marcha atrás ensayada dentro de la sombra:** apagar la ingesta 48 h y comprobar que al volver no hay turnos con `hora_utc` en el hueco que `escanear()` no tenga.
9. **Criterios:** `panel.py` y el servidor no ejecutan ningún `INSERT/UPDATE/DELETE` (autorizador de SQLite como cinturón, `mode=ro` + `query_only` como garantía); 4 consultores a la vez **mientras** el escritor aplica lotes de 500, ninguno por encima de 1 s y los servidores siguen entregando; mismo informe para los mismos datos en tres zonas y en los días de 23 h y 25 h.

**Avance de 1.3 (rama `feat/fase1-ingesta-hermes`, sin fusionar):** hecho `infobar/consulta.py` (informe desde la base en `mode=ro`, `SinAlmacen` si no existe, versión mayor, dañada o sin `construccion_completa`; plan y proyecto reproducidos; día local por cubos de 15 min, correcto en 23 h, 25 h y media hora; `agregar()` acepta epoch en `horas`), `agregacion.informe()` (único punto; `almacen: activo` lee la base y cae a los logs anotándolo una vez), `infobar/ingestor.py` (vuelta con presupuesto de 5 s, `stat` antes de entrar, escritor readquirido en cada vuelta, `construccion_completa`, mantenimiento horario y `quick_check` diario; `HiloIngesta` en la barra; `costbar.py --ingerir [--una]`), los 8 llamadores pasan por `informe()` y un test vigila que ninguno importe la ingesta, y `infobar/sombra.py` (por agente y día cerrado, con la dirección de la diferencia, `sombra.jsonl` 0600 sin proyectos). 285 tests. **Sombra sobre datos reales (base temporal, 27 días):** pi 11/11 días exactos; OpenCode 2/3 exactos (+200 tokens un día); Claude 12/27 exactos, el resto entre −0,25 % y +0,03 % (el lector antiguo cuenta de más por forks; los días con la base por encima son del día en curso o cruce de hora); Codex −4,8 % a +1,6 % por día (el lector antiguo cuenta re-emisiones y pierde las compactaciones: se vio en 1.2b). `consultar()` 0,39 s con 4 consultores a la vez y el escritor aplicando lotes de 500 sin superar 1 s. Con la base entera: `mes` 38,83 G (base) frente a 38,79 G (logs): la base suma además Hermes completo. **Pendiente de 1.3:** perfil de Hermes desde `turnos ⋈ fuentes` (M1), `nuevos` del informe desde `meta.ultima_vuelta`, aviso de volumen que ignora propiedad de ficheros, README.

**Revisión de 1.3 (`../reports/revision-fase1-3-261014.md`) corregida:** (A1) la sombra compara los cuatro contadores por separado, el traspaso entre contadores ya no se llama «atribución de modelo», una diferencia mayor del 10 % lleva el sufijo `_grande` y nunca se archiva como explicada, el cruce de día marca los dos días; (A2) una línea sin terminar que llega al final del fichero cuenta como completa (la ingesta ya no gira); (M2) los días cerrados se calculan por fecha, no por restas de 24 h (cambios de hora); (M3) una fuente que falla no marca la base como construida y espera 60 s; (M4) si el fichero de la base cambia de inodo o desaparece, el ingestor se reabre; (M5) al abrir carga de la base qué ficheros están al día, y `fuentes` cuenta ficheros; además `abrir()` reintenta «locked» al crear la base entre dos procesos (fallo intermitente real del test de carrera). Tests nuevos por cada hallazgo, con mutación comprobada en el de la línea enorme: 299 tests. **No corregido y por decidir:** Codex con un `model_provider` distinto de `openai` cambia de plan respecto al informe antiguo (divergencia a firmar); calcular los forks del lado antiguo exige instrumentar `parsear()` o una pasada aparte (de momento se ven por la dirección y el tamaño); un lector sin escritor abierto crea `-wal`/`-shm` (0600) y con el directorio de solo lectura cae a los logs.

**Decisiones del dueño sobre 1.3 (2026-10-14):** (1) **Codex con otro proveedor:** un consumo hecho con Codex pero con un `model_provider` distinto de `openai` se muestra en la cuenta de ese proveedor y no afecta al plan de suscripción de Codex; es lo que ya hace `plan_de()` y queda firmado como divergencia respecto al informe antiguo. (2) **Nombres de proyecto unificados** (un directorio es un solo proyecto en todos los agentes). (3) El resto, a criterio del asistente: **no se instrumenta `parsear()` para contar los forks** (el lector antiguo se retira al pasar a `activo`; basta la dirección y el tamaño de la diferencia, ya clasificados en la sombra), **la hora de Hermes se queda en `first_seen`** y documentada (el reparto por diferencias horarias se valora tras la fase 2, si las vistas de 5 h y de hoy lo exigen), y **el nombre del perfil de Hermes** se mostrará en Ajustes/orígenes recuperándolo por diccionario de huellas, sin guardarlo en la base.

**1.3 cerrado (2026-10-14):** perfil de Hermes por diccionario de huellas (`perfiles_hermes` en el informe y en `/origenes`; `id_fuente` pasó a `plataforma.py` para que la consulta no importe la ingesta), `nuevos` desde `meta.ultima_vuelta` solo si la vuelta es de menos de 2 min, aviso de volumen sin propiedad de ficheros (`plataforma.volumen_sin_propietarios`, lee `mount`) y sección del README. 304 tests.

### 1.5 — diseño v2 del export cifrado (tras `../reports/revision-diseno-1-5-261014.md`)
**Objetivo:** sacar la base de la máquina (o guardar copias automáticas) de forma que quien se lleve el fichero sin la clave no lea nada, cualquier manipulación se detecte y no se pueda sustituir una copia por otra fabricada, en macOS, Windows y Linux. **No objetivos:** cifrar la base en reposo, borrado seguro en memoria, sincronizar entre equipos.

1. **Formato `.cgx` v1** (`infobar/cgx.py`, formato puro sin SQLite ni claves). Cabecera fija en claro y autenticada: magia `CGX1`, versión, modo (1 frase, 2 destinatario), sal de scrypt o **pública efímera completa**, **pública completa del destinatario**, prefijo de nonce, fecha UTC. Sin tamaño total ni versión del esquema (se leen tras descifrar). Trozo **constante de 1 MiB**; el lector lee `1 MiB + 16` bytes y, con 1 byte de anticipación, sabe cuál es el último (bandera 1); cero trozos, bytes tras el último o un trozo que falta son error. ChaCha20-Poly1305, nonce = prefijo (8) + **índice real** (4); AAD = cabecera + índice + bandera. El último trozo vacío solo cuando el tamaño es múltiplo exacto. Relleno aleatorio del último trozo a múltiplo de 64 KiB con la longitud real en los 4 primeros bytes del texto en claro. Compromiso de clave: 32 bytes `HKDF(clave, "cgx1 compromiso")` en la cabecera, comparados en tiempo constante antes de descifrar (da «frase incorrecta» sin leer datos).
2. **Claves.** Frase: scrypt **fijo en escritura** N = 2^17, r = 8, p = 1 (187 MiB, 0,7 s); **en lectura** se rechaza si `128·N·r·p` > 256 MiB, p > 2, r fuera de 1..16 o N < 2^14. Destinatario: X25519 efímero + HKDF-SHA256 con `info` = cabecera. Cada fichero, clave nueva. Codificación textual con prefijo y suma de control (estilo Bech32): `CGX-SECRET-KEY-1…` (privada) y `cgx1…` (pública); huella = 16 primeros bytes de SHA-256 de la pública, en hexadecimal por grupos de 4. El fichero de la clave privada con protección es un `.cgx` modo 1 de un trozo (nunca `BestAvailableEncryption`, que es PBKDF2 de 2.048 vueltas). Puntos X25519 de orden bajo rechazados (con test).
3. **Autenticidad del productor (modo 2).** El dispositivo genera una clave de firma Ed25519 la primera vez (`DATOS/clave_firma.cgx`, 0600); el fichero termina con un tráiler `Ed25519(cabecera || SHA-256 acumulado de los trozos cifrados)`. La restauración verifica la firma contra públicas de firma conocidas (se imprimen junto a la clave del destinatario en el «paquete de restauración») **antes** del reemplazo. Modo frase: la frase ya autentica.
4. **`--claves-generar`** muestra el aviso «sin esta clave las copias son irrecuperables; no existe recuperación; guárdala en dos sitios fuera de este equipo» y exige escribir `SI`; escribe la privada donde diga el usuario (negándose si es la carpeta de copias o una carpeta no fiable), **la vuelve a leer desde ese fichero y descifra con ella un fichero de prueba** y solo entonces escribe `copias.destinatario` (lista admitida, para rotar) en `config.json`. Sin ensayo no hay copias automáticas.
5. **Origen del claro:** siempre `con.backup()` a un temporal en `DATOS/tmp` (0700; fichero 0600, `O_EXCL`, nombre aleatorio, borrado en `finally` y barrido al arrancar), cifrado en streaming. Una sola vía; nada de `serialize()`. `quick_check` fallido: se copia igual con sufijo `.danada.cgx`, fuera de la rotación, con aviso persistente.
6. **Copias automáticas:** las lanza la barra en **su propio hilo** con conexión `mode=ro` (no necesitan el arrendamiento ni tocan el latido) y `costbar.py --copia-ahora`; nunca el panel. Criterio diario en `meta.ultima_copia` (no en la carpeta). Nombre `copia-AAAAMMDD-HHMMSS-<4 hex>.cgx` creado con `O_EXCL`; **verificación por relectura** del fichero ya escrito con la clave aún en memoria (etiquetas, tráiler, SHA-256 del claro); **rotación solo tras verificar**: 14 diarias + la primera de cada mes de los últimos 6. Aviso persistente si la última copia buena tiene más de 48 h o falta destinatario. La carpeta cifrada es distinta de `rutas.COPIAS` (copias en claro de premigración, siempre en `DATOS`) y es la única ruta donde se admite la nube.
7. **Restauración** (`--importar RUTA --clave-privada RUTA`): toma el arrendamiento con identidad propia y lo refresca; descifra a un temporal sin WAL (`journal_mode=DELETE`); verifica trozos, firma, `user_version` (no mayor que la conocida) y `quick_check`; muestra huella del destinatario y número de turnos y pide confirmación; copia previa de la base actual con `backup()` y `--forzar` si existe; `wal_checkpoint(TRUNCATE)` de la actual, **borrado de `-wal`/`-shm`** (si no se puede, aborta sin tocar nada) y `os.replace` con reintentos; después comprueba que no quedan `-wal`/`-shm` ajenos y abre con `almacen.abrir()`. Un fallo no deja ficheros parciales. Test de regresión: el experimento del WAL huérfano (la restaurada no puede contener la fila que solo vivía en el WAL antiguo).
8. **Interfaz:** `--exportar`, `--importar`, `--claves-generar`, `--copia-ahora`, `--probar-restauracion RUTA --clave-privada RUTA` (la barra recuerda hacerlo cada 90 días), `--frase-sugerir`, `--autoprueba-copias`. **La barra nunca toca una frase ni una clave privada**: solo hace copias a destinatario con la pública. La frase solo en el CLI: `getpass` con `GetPassWarning` convertido en error, sin TTY se niega, se pide dos veces, mínimo 12 caracteres, `RLIMIT_CORE` a 0 en POSIX durante el export. Cualquier argumento que empiece por `CGX-SECRET` aborta. La privada se carga de fichero 0600 (o `getpass`), con aviso si está en una carpeta no fiable. Sin diálogo nativo en v1.
9. **Windows:** `os.replace`/`os.remove` con reintentos e inyección de `PermissionError` en los tests; rutas con `ntpath`; aviso si la ruta final pasa de 200 caracteres; la privacidad descansa en la ACL de `%LOCALAPPDATA%`; `getpass` exige consola Win32. Criterio de salida real: el dueño ejecuta `costbar.py --autoprueba-copias` en Windows (máquina virtual vale) y pega la salida.
10. **Dependencias:** `cryptography` exacta con hashes por plataforma en un lock (`requirements.txt` con marcadores; `install.sh` instala desde el lock); import perezoso con mensaje «instala cryptography».
**Pasos de implementación:** (1) formato puro, (2) claves, (3) modo destinatario y firma, (4) fichero de clave y `--claves-generar`, (5) origen y copia con verificación y rotación, (6) restauración, (7) CLI, (8) hilo de copias, (9) autoprueba, (10) dependencias. Cada paso cierra con sus tests antes de abrir el siguiente; 1-3 son criptografía aislada y se revisan aparte.

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
