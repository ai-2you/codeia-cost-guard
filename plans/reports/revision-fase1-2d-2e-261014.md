# Revisión de 1.2d (OpenCode) y 1.2e (Hermes + ayudas comunes) — fase 1

Revisión en SOLO LECTURA de `git diff feat/fase1-ingesta-pi...feat/fase1-ingesta-hermes` (3620b23 OpenCode, ea082a4 Hermes y
ayudas comunes). Las bases reales se copiaron (`.db`, `-wal`, `-shm`) a un directorio temporal y se abrieron con `mode=ro`;
los contraejemplos usan bases temporales. Rutas personales redactadas (`~`), perfiles de Hermes nombrados p1–p4 y "raíz".
Scripts de verificación: `oc_stats.py`, `oc_hostil.py`, `wal_ro.py`, `he_stats.py`, `sombra.py`, `he_extra.py` (scratchpad).

## Resultado

- `python3 -m unittest discover tests`: **249 tests OK** (3,3 s).
- **Totales exactos** frente a SQL directo sobre las copias:
  - OpenCode (dos bases): 4.351 turnos; 68.001.271 de entrada, 1.701.464 de salida, 375.237.568 de caché leída, 413.133 de caché escrita. Tres pasadas paginadas (0,37 s); la cuarta, `sin_cambios`.
  - Hermes (p1–p4): 127 turnos; 66.306.972 / 7.105.089 / 810.511.788 / 0, que son las cifras del plan.
  - Hermes con la base raíz incluida: 261 turnos; 186.852.250 / 8.245.379 / 840.554.515. Ver A1.
- **El refactor de ayudas comunes no introduce regresiones.** El diff 3620b23..ea082a4 de `ingesta_opencode.py` es un movimiento literal: misma firma, mismo abridor, lote de 500 y mismos textos de anomalía. El único cambio es que el texto pasa por `_ctrl_fuera` (recorte a 120). En `ingesta.py` y `almacen.py` solo se añaden líneas, y los extractores de Claude, Codex y pi no se tocan.
- Hay 2 hallazgos altos (uno de cobertura y una decisión de producto con su magnitud medida), 4 medios y 4 bajos. Además, 2 tests no prueban lo que dicen.

## Respuestas a las preguntas (con datos reales)

### OpenCode

- **Esquema.** En las dos bases, `time_created`/`time_updated` son `integer NOT NULL` y `data` es `text NOT NULL`. Los 4.887 ids son `msg_` con 30 caracteres. `opencode-.db` tiene la misma tabla `message` (1 turno) y las dos bases no comparten ningún id. No hay filas con `time_updated < time_created` ni JSON inválido, y todos los tokens son enteros.
- **`time_updated` NULL.** No ocurre en los datos reales. Si ocurriera, `coalesce` cae a `time_created` y la fila se pierde cuando ese valor es menor que la marca (contraejemplo c5).
- **Ids no ordenables.** Si se mezclan un id entero y uno de texto, la paginación sigue terminando (c8). Con ids reales no pasa.
- **Fallo entre `ingerir_turnos` y `guardar_marca`.** Es seguro. La marca se guarda DESPUÉS de los lotes, que ya están confirmados. Si falla entre medias no hay marca, se relee y el resultado es `iguales=3` (c7). La firma solo se guarda con `completo`, y se calcula ANTES de abrir la base: si algo cambia entre medias, la firma queda vieja y se relee, que es la dirección segura.
- **Firma.** Con el escritor vivo, `checkpoint(TRUNCATE)` y una escritura posterior cambian la firma (`wal_ro.py`). Un WAL reutilizado del mismo tamaño también cambia `mtime_ns` en APFS. Solo daría un falso "sin cambios" en un sistema de ficheros con mtime de 1–2 s (exFAT, HFS+, algunos de red) y dos escrituras de igual tamaño en el mismo segundo, y se corrige con la siguiente escritura (B3).
- **`mode=ro` contra un WAL.** Funciona con el escritor vivo y lee los datos que solo están en el WAL. Hay casos que crean ficheros o fallan (B1).
- **`json_valid` y `CASE`.** Protegen de verdad:
  - Un `data` que es BLOB con JSON de texto se lee bien (SQLite 3.53).
  - El JSON roto o `NULL` se salta y se cuenta.
  - Con `NaN` el JSON es inválido y la fila se salta.
  - Tokens `"100"` (texto), `100.0`, objeto, `-5` o `1e400` producen "turno descartado" con su anomalía.
  - `true` entra como 1, que es irrelevante.
- **`cwd` hostil.** Se neutraliza: control, `..`, rutas de Windows, 5.000 barras y enteros dan `31m/etc`, `p`, `otros` y `sin-proyecto`. El usuario se quita.
- **Reasoning.** Ver M4: los datos resuelven la duda.

### Hermes

- **Identidad.** El código de Hermes (`hermes_state_usage.py`, `_MODEL_USAGE_UPSERT_SQL`) solo SUMA deltas en el `ON CONFLICT` y nunca reescribe `first_seen`, así que la identidad no cambia. No existe ningún `UPDATE` que reduzca contadores; el único `DELETE` es el de mover o borrar una sesión. Por tanto un contador no puede quedarse atascado en el máximo por bajar. Lo único que lo haría es borrar la fila y recrearla, y entonces también cambia `first_seen` (ver T2).
- **Colisiones entre perfiles.** No hay ningún `session_id` repetido entre las 5 bases. Además Hermes MUEVE sesiones entre perfiles (`export_session_for_move` / `import_moved_session` copian las filas con el mismo id y el mismo `first_seen`), y gracias a que el perfil no entra en la clave el movimiento se deduplica: `subidos=1`, sin duplicar (`he_extra.py`). Es una buena decisión.
- **LEFT JOIN.** No duplica: `sessions.id` es PRIMARY KEY. Tampoco hay filas de SMU huérfanas.
- **`first_seen`.** Siempre REAL en segundos (5 bases, 261 filas), nunca anterior a `started_at`, y `first_seen <= last_seen` siempre.
- **NULL frente a `''` en la huella.** El caso es hipotético: las seis columnas de la clave primaria son `NOT NULL DEFAULT ''`.
- **`billing_base_url`.** Hoy hay 6 URLs distintas, de 29 caracteres como máximo y sin credenciales ni parámetros de consulta. El riesgo es latente (M3).
- **Lo que se pierde de `sessions`.** Nada. Hay 0 sesiones con tokens y sin SMU, y 0 sesiones con más entrada principal en `sessions` que en SMU. La ruta `absolute=True`, que no escribe SMU, no tiene ningún llamador en producción.

## Alta

### A1. El perfil por defecto de Hermes (`~/.hermes/state.db`) no se lee: es más de la mitad del consumo de Hermes

Ni el lector actual (`infobar/lectores_logs.py:391`, que solo recorre `glob(.../.hermes/profiles/*/state.db)`) ni el alcance del
extractor (docstring de `ingesta_hermes.py:1` y "127 turnos de 4 perfiles" en el plan) incluyen `~/.hermes/state.db`.
Según `hermes_constants.get_hermes_home()`, es el `HERMES_HOME` por defecto, y sus 249 sesiones tienen `profile_name='default'`.

Datos de esa base:
- 134 filas de SMU (92 sesiones);
- 120.545.278 tokens de entrada, 1.140.290 de salida y 30.042.727 de caché leída;
- último `last_seen` hace 0,2 días, así que está activa;
- ningún `session_id` compartido con p1–p4.

El extractor la lee sin ningún problema (134 nuevos, 0 anomalías, total exacto). La "coincidencia exacta con la tabla real" del
plan solo vale para un subconjunto: la entrada de Hermes casi se triplica (66,3 M frente a 186,9 M). No es una regresión,
porque el lector actual tampoco la ve, pero el cableado de 1.2 copiará el glob.

**Arreglo:** que el descubrimiento incluya `$HERMES_HOME/state.db` (por defecto `~/.hermes/state.db`) además de
`profiles/*/state.db`. Con `id_fuente` relativo a `~/.hermes` las rutas ya son distintas, y el test de "dos perfiles" puede
ampliarse con la base raíz.

### A2. Hermes: el 96 % de los tokens se fecha en una hora que no es la de su consumo (decisión del dueño, con magnitud)

`ingesta_hermes.py:44` usa `first_seen` para todo el acumulado de la fila. Sobre las 5 bases reales:
- el **96,2 %** de los tokens está en filas con `last_seen − first_seen > 1 h`;
- el **85,5 %**, en filas con más de 24 h.

Las vistas de "ventana de 5 h" y "hoy" atribuirán casi todo el consumo de Hermes al día en que empezó cada sesión. Como el
almacén es histórico, el error queda congelado. Es igual que el lector anterior (`started_at`), pero el propio proyecto ya llamó
defecto a esto mismo en OpenCode (comentario en `lectores_logs.py:343-348`: "todo el trabajo nuevo de una sesión larga se
fechaba en su hora de inicio y no entraba en la ventana de 5 h ni en el día").

`messages.token_count` no sirve para repartirlo: es el tamaño del contenido, no el uso facturado (el uso real solo existe en SMU).

**Opciones:**
1. **Mantenerlo y firmarlo** como divergencia de la sombra.
2. **Deltas en adelante, sin perder la idempotencia:** clave `hermes:<sesión>:<huella>:<hora de last_seen redondeada a 1 h>`, con valor = acumulado actual − suma de los cubos ya guardados de esa fila (se lee del almacén). Releer desde cero no duplica nada, porque los cubos previos ya cuadran y el delta es 0. El coste es que el histórico anterior al despliegue se queda en su `first_seen`, y que una reconstrucción del almacén desde cero llevaría todo al cubo de la reconstrucción.
3. **Mientras tanto,** anotarlo en la interfaz como "Hermes: hora de inicio de sesión".

## Media

### M1. OpenCode: un commit tardío con `time_updated` menor que la marca se pierde para siempre

`ingesta_opencode.py:29` avanza por `(time_updated, id) > marca`. OpenCode calcula `time_updated` (`Date.now()`) antes de
escribir. Con dos procesos de OpenCode sobre la misma base (varias terminales), uno puede esperar el cerrojo mientras el otro
confirma una marca mayor. Si la barra lee justo en medio, la fila tardía queda por detrás de la marca.

Contraejemplo c6: B confirma `T0+200`, se ingiere, A confirma `T0+150`, y las dos pasadas siguientes leen 0 filas.
Resultado: `['opencode:msg_b0000001']`, A perdida. Si la fila perdida es la actualización final de tokens de un mensaje, el
turno se queda con los tokens parciales. La probabilidad no se puede medir con una instantánea, pero la consecuencia es
permanente.

**Arreglo** (una línea, e idempotente gracias al máximo): consultar desde `marca − 5 min` (`t - 300000` con el `id` vacío) y
seguir guardando la marca real.

### M2. Un `time_updated` que no es entero bloquea la fuente o crashea la pasada

- **TEXT** (c4): `maximo < t` en `ingesta_opencode.py:75` compara `str` con `int` y lanza un `TypeError` que no se captura (solo se captura `sqlite3.Error`). La excepción sale de `ingerir_base`.
- **REAL** (c3): la marca `"1790000000010.5:msg_…"` no pasa `isdigit()` (`ingesta_opencode.py:68`), así que `t` vuelve a 0 y la lectura repite las primeras páginas para siempre. Nunca queda `completo`, la firma nunca se guarda y la base se abre en cada pasada. Con una base mayor que dos páginas, las filas posteriores a la fila REAL nunca se leen: la secuencia observada es `(3, False)` seis veces.
- **Ruta relativa:** `abrir_sqlite_solo_lectura` (`ingesta.py:291`) lanza `ValueError` en `as_uri()`, que tampoco se captura.

Con los datos reales no ocurre (la columna es `integer NOT NULL`), pero es el mismo listón que se exigió en 1.2a: "una fila
hostil no bloquea la fuente".

**Arreglo:**
- usar `CAST(coalesce(time_updated, time_created, 0) AS INTEGER)` en el `max`, en el `WHERE`, en el `ORDER BY` y en la columna devuelta;
- capturar `(sqlite3.Error, ValueError, TypeError)` al abrir y al consultar;
- aceptar en la marca lo mismo que se escribe.

### M3. Hermes: `proveedor` es la URL completa, así que una fila con URL larga se descarta y unas credenciales irían al almacén

`ingesta_hermes.py:47` guarda `billing_base_url` tal cual. Hay dos efectos, ambos latentes (máximo real de 29 caracteres y
ninguna URL con credenciales):
- Con más de 120 caracteres, `almacen.problema` (`almacen.py:359`) rechaza el turno ENTERO, con todos sus tokens. Contraejemplo con una URL de despliegue de Azure de unos 140 caracteres: `nuevos=0` y la anomalía "proveedor no válido".
- Una URL del tipo `https://usuario:<secreto>@proxy/v1?api_key=<secreto>` se guarda en `turnos.proveedor` y en `horas.proveedor`, y más tarde viajaría en el export de 1.5. Comprobado: una fila en `turnos` y otra en `horas`.

De paso, el mismo proveedor aparece como `nan` en OpenCode y como `https://api…/v1` en Hermes, así que no agrupa entre agentes.

**Arreglo:** guardar `urlsplit(burl).hostname`, o `billing_provider` si no hay URL. La huella de la clave puede seguir usando
la URL completa, porque es un hash y no sale del equipo.

### M4. Reasoning: los datos ya resuelven la decisión, en sentidos opuestos para cada agente

- **OpenCode: `reasoning` NO está dentro de `output`.** Todos los proveedores con reasoning tienen mensajes con `reasoning > output`, lo que es imposible si fuera un subconjunto: apizai 71, glm-paulo 16, openai 197, opencode 5, opencode-go 190 y zai-coding-plan 71. Por ejemplo, en `openai` el reasoning suma 172.884 frente a 184.021 de salida. No sumarlo deja la salida corta en unos 502 K tokens (el 30 %). Recomendación: sumarlo a `go`, o guardarlo aparte si se quiere mostrar.
- **Hermes: `reasoning` SÍ está dentro de `output`.** En `agent/usage_pricing.py`, `output_tokens` es `completion_tokens`/`output_tokens` del proveedor (que en la semántica de OpenAI incluye el razonamiento) y `reasoning_tokens` es su desglose `*_details.reasoning_tokens`. En los datos, solo 1 fila de 261 tiene `reasoning > output`. No sumarlo es correcto. El docstring (`ingesta_hermes.py:9`, "mismo pendiente que OpenCode") debería decir que en Hermes no hay nada pendiente.

## Baja

- **B1. `mode=ro` crea `-wal`/`-shm` en el directorio ajeno o falla** (`wal_ro.py`):
  - Si la base está en modo WAL, cerrada limpiamente (sin `-wal`/`-shm`, que es lo normal con OpenCode o Hermes cerrados) y el directorio admite escritura, la apertura CREA `opencode.db-wal` y `-shm` vacíos y los deja ahí. Eso contradice "sin poder modificarla", y además cambia la firma en la pasada siguiente, lo que provoca una relectura inútil.
  - Si el directorio no admite escritura, falla con "attempt to write a readonly database".
  - Si hay `-wal` pero no `-shm` y el directorio es de solo lectura, falla con "unable to open database file".
  
  No es grave (los ficheros son del mismo usuario y SQLite los gestiona), pero conviene documentarlo y, si el directorio es de solo lectura, reintentar con `immutable=1`, solo en ese caso.
- **B2. `aplicar_filas` devuelve `anomalias=0` aunque descarte filas.** Las filas rechazadas en `ingesta.py:302-318` solo se anotan; no se suman a `total["anomalias"]`. En c2 se descartaron 5 filas con `anomalias=0`, y lo mismo pasó con la URL larga. El que llama no sabe que perdió filas. Es el mismo patrón que `ingerir_fichero` (`ingesta.py:204`), así que no es una regresión.
- **B3. Firma con mtime grueso.** Ver la respuesta sobre la firma más arriba. Para mayor robustez puede añadirse `st_ino` y `st_ctime_ns` a la firma, que no cuesta nada.
- **B4. El detalle de "filas con JSON inválido: N" cambia con N**, así que cada recuento distinto crea una anomalía nueva (UNIQUE por detalle). Hoy hay 0 filas rotas.

## Tests que no prueban lo que dicen

- **T1.** `test_el_origen_no_se_modifica` (en los dos ficheros de test) crea el origen en modo rollback-journal y solo compara los bytes del `.db`. Las bases reales están en WAL, y la modificación real (B1) son los ficheros `-wal`/`-shm` nuevos, que el test no mira. Debería poner `journal_mode=wal`, cerrar la base y comprobar `os.listdir` antes y después.
- **T2.** `test_un_contador_que_baja_no_se_aplica` (Hermes) modela un caso que Hermes no produce, porque solo suma. El caso real que falta es "borrar y recrear la fila con otro `first_seen`": el almacén anota `cambio_hora_utc` y congela la fila con el valor viejo.
- **T3.** Los tests hostiles comprueban qué turnos entran, pero no qué anomalías se anotan. Combinado con B2, una fila descartada sin anotar pasaría los tests.
- Faltan tests para M1 (commit tardío), M2 (`time_updated` REAL o TEXT), A1 (base raíz) y el movimiento de una sesión entre perfiles. Este último funciona bien y merece un test que lo fije.

## Bien resuelto

- La huella con la clave primaria real de SMU.
- El perfil fuera de la clave: hace que los movimientos de sesión entre perfiles sean idempotentes.
- La paginación con empates de `time_updated`.
- La marca guardada después de los lotes.
- La firma calculada antes de leer.
- `json_valid` dentro del `CASE`.
- El almacén conserva las filas podadas.
- La relectura cuando la base se recrea.
- La URI escapada (`#`, `?`, espacios).
- Las ayudas comunes, extraídas sin cambiar el comportamiento.

## Acciones recomendadas (por prioridad)

1. A1: incluir `~/.hermes/state.db` en el descubrimiento de 1.2, con un test.
2. A2: que el dueño decida entre firmar la divergencia o pasar a deltas por hora de `last_seen`.
3. M4: sumar `reasoning` en OpenCode y corregir el docstring de Hermes.
4. M1: releer con una ventana hacia atrás de 5 min.
5. M2: `CAST AS INTEGER` y capturar `ValueError`/`TypeError`.
6. M3: guardar solo el host como proveedor.
7. T1 y T2, y después B1–B4.

## Preguntas abiertas

- ¿Debe la interfaz conservar la dimensión de perfil de Hermes (`Hermes/<perfil> · cwd`, como hoy)? `id_fuente` es un hash y no permite reconstruirla. Si se quiere, hay que guardar el nombre del perfil (no es una ruta) en `fuentes` o en `proyecto_raw`.
- ¿Algún día se ejecutarán las bases ajenas desde un volumen de solo lectura o una copia de seguridad? Si es así, B1 deja de ser baja.
