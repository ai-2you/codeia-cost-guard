# Revisión del paso 1.2a (ingesta de Claude Code) — 2026-10-14

**Alcance:** `git diff feat/fase1-almacen...feat/fase1-ingesta-claude` (commit 54bc879): `infobar/ingesta.py`, `infobar/ingesta_claude.py`, cambios en `infobar/almacen.py` (tabla `lecturas`, `estado_lectura`, `guardar_lectura`, `anotar`, `TOLERANCIA_HORA_S`), `tests/test_ingesta.py`, `tests/test_almacen.py`. Revisión de solo lectura; no se ha tocado el repo ni `plan.md`.

**Suite:** `python3 -m unittest discover tests` → **185 tests OK** (Python 3.14.6, 3,2 s).

**Método:** contraejemplos ejecutables con bases y ficheros temporales del scratchpad (`contra.py`, `contra2.py`, `contra3.py`) y dos muestras estadísticas de solo lectura sobre `~/.claude/projects` (`muestra.py`, `muestra2.py`, con `python3 -I`, sin imprimir ids, rutas ni contenido): 1.371 ficheros, 236.876 líneas con uso y 123.545 ids.

## Veredicto

La estructura es buena: monótona, reanudable y con lo hostil acotado. Pero **dos fallos bloquean 1.2b**, porque los extractores de Codex, pi, OpenCode y Hermes heredarán este bucle:

- **(1)** Una sola línea con un tipo inesperado lanza una excepción no capturada. El fichero queda **bloqueado para siempre**, y los turnos buenos que vienen detrás no entran nunca.
- **(2)** El desplazamiento guardado no garantiza que la lectura se reanude al inicio de una línea. Una reescritura en sitio que conserve la cabecera **pierde turnos de forma permanente**.

El resto son defectos de grado medio o bajo, y divergencias de equivalencia que hay que firmar en la sombra.

---

## Alto

### 1. Sin frontera de excepción por línea: una línea bloquea el fichero para siempre
**Dónde:**
- `ingesta.py:28-32`: `float(v)` con un entero enorme lanza `OverflowError`.
- `ingesta.py:101` y `ingesta.py:137`: `_maximo` hace `max()` antes de validar, y eso lanza `TypeError` si los tipos son mixtos.
- `ingesta.py:126`: el extractor se llama sin `try`.

Como la excepción se propaga antes de `guardar_lectura` (`ingesta.py:151`), cada pasada vuelve a leer desde el mismo desplazamiento y vuelve a fallar.

**Contraejemplos** (dos pasadas seguidas en cada caso):

| Caso | Entrada | Resultado en las dos pasadas | Turnos guardados |
|---|---|---|---|
| c1 | `"timestamp": 999…9` (400 cifras) | `OverflowError` | 0 (el turno bueno de detrás tampoco entra) |
| c2 | dos líneas del mismo `msg_…` con `output_tokens: 5` y `"7"` | `TypeError` | 0 |
| c2b | lo mismo con `cache_read_input_tokens: null` en la segunda línea | `TypeError: NoneType > int` | 0 |

El caso c2b no requiere un atacante: basta un proxy o backend no Anthropic que escriba `null` en una línea intermedia del streaming.

**Arreglo:**
- Envolver en `try/except Exception` cada línea, es decir, desde `json.loads` hasta el extractor y `_maximo`, y anotar el *tipo* de la excepción, nunca su texto.
- En `a_epoch_utc`, capturar `OverflowError`.
- Normalizar los contadores en el extractor: `None` pasa a 0, y cualquier valor que no sea un entero convierte la línea en anomalía antes de llegar a `_maximo`.
- Añadir un test con dos pasadas que compruebe que el turno bueno de detrás entra.

### 2. El desplazamiento no es un inicio de línea garantizado: reescritura en sitio, carreras y `troceadas`
**Dónde:**
- `ingesta.py:71-74`: para reanudar solo se comparan el inodo, `tam ≥ desplazamiento` y el hash de los primeros `cab_len` ≤ 4 KB.
- `ingesta.py:69`, `:77` y `:94`: `stat`, la lectura y el hash de cabecera usan **tres aperturas distintas**.
- `ingesta.py:84-85`: `troceadas` deja el desplazamiento a mitad de línea a propósito.

**Contraejemplos:**
- **c4:** un fichero tiene 20 líneas comunes (más de 4 KB) seguidas de 20 líneas viejas. Se lee entero y después se reescribe **en sitio** (mismo inodo) con las mismas 20 líneas comunes y 45 líneas nuevas. La pasada siguiente arranca en el desplazamiento viejo: guarda **25 de 45** turnos nuevos y aterriza a mitad de línea (`rotas: 1`). Los 20 restantes se pierden para siempre, porque el estado ya apunta al final.
- **Carrera (por lectura de código):** si el fichero se trunca y se reescribe en sitio entre la lectura (`:77`) y el hash (`:94`), se guarda el hash del contenido nuevo junto con el desplazamiento del viejo. La pasada siguiente salta el comienzo del contenido nuevo.
  - Un *rename* encima del fichero no reproduce el fallo: el inodo guardado sale del `stat` previo y no coincide, así que se relee desde 0. Eso está bien.
  - En ext4 los inodos se reutilizan enseguida, y en FAT/exFAT `st_ino` puede valer 0. En esos sistemas solo protege la cabecera.
- **c5:** con una línea de 3 KB y `max_bytes=1000`, el resto de la línea troceada se anota además como `lineas_no_json`. Es una doble anomalía y, con un resto alineado a propósito, podría colarse como JSON válido.

En Claude Code no se ha visto reescritura en sitio (0 ficheros sin `\n` final en la muestra). En 1.2b sí es plausible: sesiones que se reescriben enteras.

**Arreglo:** imponer un único invariante: *el desplazamiento guardado es siempre un inicio de línea*.
1. Abrir el fichero una sola vez y usar `os.fstat`, la lectura y el hash sobre el mismo descriptor.
2. Para reanudar, exigir además que el byte en `desplazamiento-1` sea `\n`; si no lo es, releer desde 0. Lo releído es inocuo porque el almacén es monótono.
3. Una línea mayor que `max_bytes` se salta **en la misma pasada**, leyendo por bloques hasta el siguiente `\n` sin acumularla, para que nunca quede guardado un desplazamiento a mitad de línea.

No basta con el punto 2 si `troceadas` sigue como está: se releería desde 0 en bucle.

## Medio

### 3. Proveedor fijo `"anthropic"` que además queda congelado
**Dónde:**
- `ingesta_claude.py:55`.
- `almacen.py:414-417`: el proveedor guardado nunca se corrige; solo se anota `cambio_proveedor`.

**Evidencia de la muestra real:** hay unas **18.500 líneas con uso de modelos que no son de Anthropic** escritas por Claude Code:

| Modelo | Líneas |
|---|---|
| glm-5.3 | 7.007 |
| deepseek-v4-flash | 3.611 |
| glm-5.3-flash | 3.406 |
| qwen3.8-27b | 2.819 |
| qwen3.8-flash | 1.169 |
| glm5.3-flash | 463 |
| gemma4 | 19 |

glm usa ids con forma `msg_…`, así que el proveedor no se puede deducir de la forma del id.

Con el almacén monótono, ese `anthropic` erróneo queda para siempre en `turnos` y en `horas`, que agrupa por proveedor. Afecta a los totales por plan y a las tarifas.

**Arreglo:** guardar `proveedor=""` (desconocido) y resolverlo por modelo al consultar (en 1.3 `consultar()`), con el `proveedor_de` que ya existe en `planes_y_tarifas`.

### 4. Las anomalías de ingesta se deduplican entre ficheros y pasadas, y se pierden
**Dónde:**
- `ingesta.py:148` y `:150`: se anotan con `clave=""` y `detalle=f"{n} líneas"`.
- `anomalias` es `UNIQUE(tipo, clave, detalle)` y no incluye la fuente.

**Contraejemplo c3:** 4 líneas rotas, repartidas en 2 ficheros y 4 pasadas, producen **1 sola fila**: `('lineas_no_json', fuente 1, '1 líneas')`. La fuente 2 ni aparece.

**Arreglo:** usar como clave `f"{fid}:{desde}"`, es decir, la fuente y el inicio del trozo. Releer el mismo trozo sigue deduplicando, y los trozos nuevos ya no se pierden.

### 5. `proyecto_de_ruta` filtra el usuario
**Dónde:** `ingesta_claude.py:24-27`. Quita siempre exactamente 2 trozos después de `Users`/`home`, pero Claude Code convierte `-`, `.` y `\` en `-`, de modo que el usuario puede ocupar varios trozos.

**Contraejemplo c7:**

| Usuario o sistema | Carpeta | Resultado |
|---|---|---|
| `jean-luc` | `-Users-jean-luc-Proyectos-app` | `luc/Proyectos/app` |
| `ana.garcia` | `-Users-ana-garcia-x` | `garcia/x` |
| Windows | `C--Users-ana-Proyectos-app` | `ana/Proyectos/app` (no empieza por `users`) |

Solo se filtra cuando quedan menos de 3 trozos después del usuario. En esta máquina no afecta: 7 de 42 carpetas empiezan por el home codificado y ninguna filtra.

**Arreglo:** quitar el prefijo del **home codificado** con la misma regla de Claude Code, es decir, aplicar `re.sub(r"[^A-Za-z0-9]", "-", home)` y comprobar el prefijo, en lugar de contar trozos. Añadir tests con `-` y `.` en el usuario y con la ruta de Windows.

### 6. Un contador `null` descarta todos los tokens del turno
**Dónde:** el extractor pasa `None` tal cual (`ingesta_claude.py:56-57`), y `_problema` (`almacen.py:350-353`) rechaza el turno entero. El parser actual hace `or 0` (`lectores_logs.py:131-134`).

**Contraejemplo c2c:** un mensaje de una línea con `cache_read_input_tokens: null` da `nuevos: 0` y la anomalía `cr fuera de rango`. Se pierden también `gi`, `go` y `cw`.

En la muestra real hay 0 `null`, pero sí 1.359 líneas sin los campos de caché, que se resuelven bien con el valor por defecto 0. Se arregla con la normalización del punto 1.

### 7. `a_epoch_utc` acepta desplazamientos de zona absurdos
**Dónde:** el regex de `ingesta.py:20` y el cálculo de `:44`.

**Contraejemplo c6:** `+99:99` y `-2400` se aceptan y desplazan la hora hasta unos 4 días. El resultado pasa `HORA_MIN/MAX`.

**Arreglo:** exigir minutos < 60 y |desplazamiento| ≤ 14:00; si no se cumple, devolver `None`.

## Bajo

### 8. Un campo descriptivo inválido descarta todos los tokens del turno
**Contraejemplos c9a y c9b:** una carpeta de proyecto de 240 caracteres, o con `%0A`, produce `nuevos: 0` y la anomalía `proyecto_raw no válido`.

El `urllib.parse.unquote` de `ingesta_claude.py:23` es lo que convierte `%0A` en un carácter de control. Claude Code nunca escribe `%` en esos nombres; el `unquote` está ahí solo para imitar al panel.

**Arreglo:** que el extractor sanee y trunque `proyecto_raw` (y el modelo, si procede) en lugar de dejar que invaliden el turno.

### 9. Plataforma: FIFO, otra unidad en Windows e inodos
- **FIFO:** un FIFO con nombre `.jsonl` **cuelga la ingesta**. En la prueba, `timeout 5` terminó con código 124, porque `open()` bloquea (`ingesta.py:77`). Se evita comprobando `stat.S_ISREG` sobre el `fstat` y abriendo con `O_NONBLOCK`.
- **Windows:** `ntpath.relpath('D:\\a','C:\\b')` lanza `ValueError`, así que `id_fuente` (`ingesta.py:54`) revienta si la base y el log están en unidades distintas.
- **Inodos:** en FAT/exFAT `st_ino` puede ser 0 o no ser estable. Eso solo provoca relecturas, que son inocuas, salvo en combinación con el punto 2.

### 10. `ESQUEMA_V1` editado en sitio sin subir la versión
**Contraejemplo c8:** una base creada por 1.1 (`user_version=1`, sin la tabla `lecturas`) da `OperationalError: no such table: lecturas` en `estado_lectura`. `_migrar` no comprueba qué tablas existen (`almacen.py:179-180`).

Hoy el riesgo es bajo: 1.1 no está en `main`, y un checkout de desarrollo no escribe datos reales (`almacen.py:108`). Pero desde que exista una base real, cualquier cambio de esquema tiene que ir en una v2 con migración.

### 11. Equivalencia con el parser actual: divergencias que la sombra preliminar no menciona
Además de los forks (−102 turnos) y la atribución de modelo, la muestra real da:

- **114 mensajes cruzan de hora y 7 cruzan de día local.** La base los pone en la hora de la *primera* línea; el parser actual usa la *última*. Los totales no cambian, pero sí el reparto por hora y por día.
- **5 ids donde la última línea no es el máximo de cada contador.** La base suma **+16.307 tokens** respecto al parser, que se queda con la última línea.
- **5 líneas `user` con uso en `mcpMeta.structuredContent.usage`.** El parser actual las cuenta como turnos de Claude porque `_hallar_uso` busca hasta profundidad 6; la base no. Probablemente la base acierta.
- **Contadores `null`:** ver el punto 6.

Comprobado sin divergencia (0 casos):
- usos con forma de Codex en los logs de Claude;
- ficheros sin `\n` final;
- ids no Anthropic repetidos en varias sesiones;
- líneas con uso sin `message.id`.

Las tres primeras divergencias hay que firmarlas en la sombra (1.4).

### 12. Tests que no prueban lo que dicen
- **`test_lineas_rotas_o_hostiles_no_tumban_la_pasada`:**
  - La línea "enorme" de 3 MB está por debajo de `MAX_LECTURA` (8 MB), así que nunca pasa por `troceadas`.
  - La línea con `"input_tokens": "uno"` no lleva `timestamp`, así que se descarta antes de llegar a comprobar los tipos.
  - Ninguna línea repite un mensaje con tipos mixtos, que es el caso del punto 1.
- **`test_fichero_reescrito_o_truncado_se_relee_sin_duplicar`:** la cabecera cambia, así que no ejercita la reescritura con la misma cabecera (punto 2).
- **Huecos sin ningún test:**
  - anomalías repetidas entre ficheros (punto 4);
  - la frontera exacta de 3600/3601 s en la ingesta;
  - un mensaje que cruza de hora;
  - un usuario con `-` o `.`, o una ruta de Windows.
- **`EquivalenciaTest`:** es sintético y monótono. Todas las líneas llevan modelo y crecen, así que no puede detectar ninguna de las divergencias del punto 11.

## Bien resuelto (una línea cada uno)
- **Tolerancia de 3600 s:**
  - El máximo real entre líneas del mismo mensaje es 725 s; 0 casos superan 3600 s y ningún id tiene varios modelos.
  - El delta se suma a la hora ya guardada (`previo`, `almacen.py:428`), así que no hay doble conteo entre horas.
  - No abre ninguna capacidad nueva de ataque: quien escribe el log ya puede crear claves nuevas.
- **Colisión entre `claude:msg_…` y `claude:<sesion>:<id>`:** es imposible, porque los ids `msg_` no llevan `:`.
  - Entre dos claves no Anthropic sí podrían chocar si un `sessionId` lleva `:`, pero los ids reales son UUID.
  - Los ids no Anthropic reales tienen la forma `msg_<13 dígitos>_<sufijo aleatorio>`.
- **Fallo entre lotes o pérdida del puesto de escritor:** es idempotente. No se guarda `lecturas`, se relee y el upsert monótono deja todo `iguales`.
- **Segundos frente a milisegundos:** el umbral de 1e11 es correcto.
- **Valores absurdos en `a_epoch_utc`:** años como 0099, epochs negativos y `1e20` los paran `HORA_MIN/MAX` en `_problema`.
- **Robustez básica:**
  - `RecursionError` está capturado.
  - Una línea a medias no avanza el desplazamiento.
  - El pico de memoria es de unos 3 × 8 MB por pasada.
  - La línea con uso más larga de la muestra ocupa 160 KB, muy lejos de `MAX_LINEA`.
- **Régimen estacionario:**
  - Una segunda pasada sin cambios tarda 0,06 s para 500 ficheros, pero hace 2 escrituras por fichero: `visto_en` y `lecturas`. `tam` y `mtime_ns` se guardan y nunca se usan.
  - Se podría salir sin escribir cuando inodo, tamaño, mtime y desplazamiento coinciden. Es una mejora menor.
  - El latido solo se refresca en `ingerir_turnos`, no en `fuente`, `anotar` ni `guardar_lectura`. Hay que tenerlo en cuenta en el bucle del CLI.

## Acciones recomendadas (por orden)
1. Frontera `try/except` por línea, captura de `OverflowError` y normalización de los contadores (puntos 1 y 6), con un test de dos pasadas.
2. Invariante "el desplazamiento es inicio de línea": un único descriptor, comprobar `\n` en `desplazamiento-1` y saltar las líneas gigantes en la misma pasada (punto 2), con un test de reescritura en sitio que conserve la cabecera.
3. `proveedor=""` y resolución por modelo al consultar (punto 3).
4. Clave de anomalía con la fuente y el trozo (punto 4).
5. Quitar el home codificado en `proyecto_de_ruta` (punto 5).
6. Validar el desplazamiento de zona (punto 7) y sanear `proyecto_raw` (punto 8).
7. Antes de la primera base real: esquema v2 con migración (punto 10).
8. Documentar en la sombra las divergencias del punto 11 y reforzar los tests del punto 12.

## Preguntas abiertas
- ¿Alguno de los agentes de 1.2b (pi, Hermes, OpenCode) reescribe sus `jsonl` en sitio? Si es así, el punto 2 deja de ser teórico.
- ¿Se quiere conservar la hora de la primera línea (lo que hace hoy la base) o la de la última (lo que hace el parser actual)? Afecta a 114 turnos de la muestra.
- ¿Los usos de `mcpMeta.structuredContent.usage` son consumo real que haya que contar, y de qué agente?
