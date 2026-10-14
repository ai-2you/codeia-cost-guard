# Revisión de las correcciones de Codex (1.2b) y del extractor de pi (1.2c) — fase 1

Fecha: 2026-10-14 · Rama: `feat/fase1-ingesta-pi` · Modo: solo lectura (no se ha tocado ningún fichero del repo salvo este informe, ni `~/.cost-guard` ni `~/Library`).

**Alcance**

- **(A)** `git diff 2e6c501..ea8a4c1`, que cubre:
  - `ingesta_codex.py`, que pasa a usar `token_usage_record` con la clave `codex:<response_id>`;
  - `ingesta.py`, en `texto_seguro` y `_serializa_contexto`;
  - `ingesta_claude.py`;
  - la comprobación de esquema en `almacen.py`;
  - `tests/test_ingesta_codex.py`.
- **(B)** `git diff ea8a4c1..HEAD`: `infobar/ingesta_pi.py` y `tests/test_ingesta_pi.py`.

**Método**

- Los scripts están en el scratchpad, se ejecutan con `python -I` y trabajan sobre snapshots copiados a una carpeta temporal.
- Para no tocar los datos reales se usan un HOME falso y bases SQLite temporales, que se borran al terminar.
- Los ficheros y los proyectos aparecen con alias (F00…, P00…, w0…).

**Suite:** `python3 -m unittest discover tests` → **221 tests OK** (3,4 s).

## Resultado

**(A) Codex: corrección buena, verificada de forma independiente.** Hice un recuento propio, sin usar el extractor:

- deduplico por `response_id`;
- atribuyo el modelo por `turn_id` → `turn_context.model`.

Sobre el mismo snapshot (47 rollouts, 7.647 registros en esa ejecución, porque los ficheros siguen creciendo), ese recuento coincide **al token** con el extractor ejecutado contra una base temporal: 1.159.755.623 tokens en ambos, diferencia 0, sin turnos a cero y sin anomalías de ingesta (`c7.py`).

Por modelo (gi = input − cached, go, cr, cw, n):

| Modelo | Independiente | Extractor − independiente |
|---|---|---|
| gpt-5.6-luna | 991.717 / 118.141 / 15.879.424 / 0 / 155 | 0 |
| gpt-5.6-sol | 116.609 / 18.901 / 1.731.328 / 0 / 21 | 0 |
| gpt-6-astra | 4.678.236 / 715.157 / 202.609.920 / 0 / 1.472 | −250.916 / −6.699 / −12.032 / 0 / −1 |
| gpt-6-luna | 65.926 / 4.194 / 492.032 / 0 / 10 | 0 |
| gpt-6.1-sol | 18.700.493 / 3.000.329 / 910.633.216 / 0 / 5.989 | +250.916 / +6.699 / +12.032 / 0 / +1 |

La única diferencia es un turno que se atribuye a otro modelo (B1).

**(B) pi: tiene un fallo grave de cifra.** El `reasoning` ya va **dentro** de `output`, así que sumarlo infla la salida en un +52 %. El parser actual hace lo mismo, de modo que ni la sombra ni `EquivalenciaTest` pueden detectarlo. Además, la clave es un id de 32 bits usado como si fuera global.

## Respuestas a las preguntas (con datos reales)

### Codex (47 ficheros, 7.637 `token_usage_record`, 7.853 `token_count`)

- **¿Cubren los registros todas las respuestas?** Sí. Hay 0 `token_count` con uso mayor que 0 sin un registro del mismo uso en el fichero.
- **¿Hay `response_id` repetidos?**
  - 0 repetidos: 7.637 registros dan 7.637 ids únicos.
  - Por tanto, no hay reintentos ni streaming parcial con usos distintos, ni un mismo id con otra hora u otro modelo.
- **Forks:**
  - Los 20 forks tienen dos `session_meta`, y en los 20 casos el `cwd` y el proveedor coinciden con los del padre.
  - Ningún fork vuelve a escribir registros del padre: 0 `response_id` compartidos entre ficheros.
  - El `turn_context.cwd` nunca difiere del `cwd` de `session_meta`.
- **Anomalía "token_count sin token_usage_record previo".**
  - Hay 0 `token_count` antes del primer registro de su fichero, así que no salta en el uso normal.
  - El flag `registros` sobrevive a la reanudación. Con pasadas de 32 KB (8.552 pasadas) el resultado es idéntico al de una sola pasada, con 0 anomalías.
  - El contexto no puede superar los 1000 caracteres: proyecto ASCII ≤ 200, modelo y proveedor ≤ 120 y sin caracteres de control. Por eso no se pierde el flag por B3.
- **Líneas enormes.** Hay 3 líneas de más de 2 MB y **no** llevan uso. Lo demuestra la diferencia 0 frente al recuento independiente, que sí lee esas líneas.
- **Esquema obsoleto.** La comprobación de `almacen.py:182-184` es correcta y el test la cubre.
- **Claude.** Se revisaron 237.714 líneas con `usage` en 1.377 ficheros. Hay 0 modelos vacíos, no textuales, con caracteres de control o de más de 120 caracteres, así que el nuevo rechazo no descarta datos reales.

### pi (22 ficheros, 14 carpetas, 9.620 turnos `assistant` con uso)

- **¿Es `id` único?** Sí, hay 0 repetidos entre 20.266 líneas.
  - Todos los ids de mensaje son de **8 hexadecimales**; los de 36 caracteres son los de las cabeceras.
  - No existe ningún fork en los datos, así que la afirmación "las ramas/forks copian las líneas con el mismo id" no está medida. Véase M1.
- **Roles y proveedor.**
  - El uso solo aparece en `assistant`: los 9.887 `toolResult`, los 460 `user` y los 21 `system` van sin `usage`.
  - Los 9.620 turnos llevan `provider` de tipo `str`.
- **¿Qué marca de tiempo manda?** La ISO de la línea, que es la que se usa, va entre 0 y 328 s por detrás de `message.timestamp` (en epoch, ms), con una mediana de 7,6 s. Por esa diferencia, 23 turnos caen en otra hora UTC y 1 en otro día. Véase B5.
- **`reasoning`.** Va DENTRO de `output`. Véase A1.
- **Líneas de más de 2 MB.** Las 2 líneas de 2,68 MB son mensajes **sin** uso. La línea con uso más larga ocupa 56 KB.
- **Patrón de id.** `^[A-Za-z0-9_-]{4,64}$` no descarta ningún id real.
- **Reanudación.** Con pasadas de 128 KB el resultado es idéntico.
  - Con 48 KB se pierde 1 turno, pero se debe a que mi trozo de prueba era menor que la línea de 56 KB, que se salta como enorme. Con `MAX_LECTURA` = 8 MB no ocurre.

## Alta

### A1. pi suma el `reasoning` a la salida, pero ya va dentro de `output`: +3,4 M tokens (+52 %)

**Dónde está.** `ingesta_pi.py:74`, el docstring de `ingesta_pi.py:4-5`, y `lectores_logs.py:190-191` y `:220` (el parser hace lo mismo).

**Hay tres señales sobre los 9.620 turnos reales** (`p1.py`, `p2.py`). El coste probablemente lo calcula pi con su tabla de precios, así que confirma cómo cuenta pi, no una factura; las dos primeras señales bastan por sí solas:

1. **`totalTokens`.** Es igual a `input + output + cacheRead + cacheWrite` en **9.620 de 9.620** turnos, incluidos los 9.127 que tienen `reasoning > 0`. Si el razonamiento fuera aparte, `totalTokens` lo incluiría.
   - El docstring del parser dice "totalTokens = input+output", y eso no se cumple en ningún turno.
2. **Relación con `output`.** `reasoning ≤ output` en el 100 % de los casos. Nunca es mayor, cosa que sí pasaría si fueran contadores independientes.
3. **Coste.** El precio implícito `cost.output / output` es **constante por modelo**: el coeficiente de variación es 0,0 en los 4 modelos con más de un turno con coste.
   - Si se calcula como `cost.output / (output + reasoning)`, varía: el CV es de alrededor de 0,16-0,20 en 3 de esos 4 modelos.
   - La muestra con coste es pequeña: unos 175 turnos con `cost.output > 0` en 5 modelos.

**Impacto medido** (`p3.py`, extractor contra recuento independiente):

- `go` sale **+3.403.460** por encima de lo real, sobre una salida real de 6.547.996.
- `gi`, `cr`, `cw` y el número de turnos coinciden exactamente.

**Por qué ningún test ni la sombra lo detectan:**

- El fixture `mensaje()` construye `totalTokens = inp + out` (`test_ingesta_pi.py:20`), una forma que no existe en los datos reales.
- `test_turno_basico_…` (`:62`) da por buena la suma.
- `EquivalenciaTest` (`:130`) compara contra un parser que tiene el mismo error.

**Arreglo.** `go = output`, sin sumar nada. Así desaparecen también `_suma` y su caso hostil (B4).

**Decisión del usuario:**

- **Opción a:** corregir también `lectores_logs.parsear_pi`.
- **Opción b:** firmar en la sombra una divergencia de −3,4 M de salida.

En los dos casos hay que corregir los docstrings y el fixture: `totalTokens` debería ser `inp + out + cr + cw`.

## Media

### M1. Clave `pi:<id de línea>`: un id de 32 bits usado como global

**Dónde está.** `ingesta_pi.py:5-6` y `:75`.

**El problema.** Los ids son 8 hexadecimales, únicos dentro de una sesión, pero la clave los mezcla entre todas las sesiones para siempre. Suponiendo ids aleatorios, la probabilidad de al menos una colisión entre turnos con uso es:

| Turnos con uso | Probabilidad de colisión |
|---|---|
| 9.620 (hoy) | 1,1 % |
| 50.000 | 25 % |
| 100.000 | 69 % |

**Qué pasa si chocan dos turnos reales:**

- Si tienen el mismo modelo y menos de una hora de diferencia, `ingerir_turnos` aplica el máximo de cada contador (`almacen.py:427`). El segundo turno se pierde **sin dejar anomalía**.
- En el resto de casos, se anota una anomalía y el segundo turno se pierde igualmente.

**La justificación tampoco se sostiene.** El docstring dice que los forks se deduplican, pero no hay ningún fork en los datos.

**Alternativa: `pi:<message.responseId>`.**

- Está en 9.598 turnos y los 9.598 son únicos. Tienen entre 25 y 41 caracteres y todos cumplen `[A-Za-z0-9_.:-]`.
- Los 22 turnos sin `responseId` son todos `stopReason: error` con uso 0. Omitirlos elimina además 22 filas a cero (B3).
- Si algún fork copiara mensajes, el `responseId` también los deduplicaría.
- Es un cambio de clave. Hoy no hay base desplegada, así que conviene decidirlo ahora.

### M2. El test de regresión del sustituto suelto no prueba la regresión

**Dónde está.** `test_ingesta_codex.py:139`, `test_un_modelo_con_sustituto_suelto_no_bloquea_el_fichero`.

**El problema.** El test pone un `turn_context` válido **después** del hostil. La revisión anterior ya avisó de que así el fallo no se reproduce.

**Lo he comprobado** con el código de `2e6c501` en una copia del scratchpad:

- Este test **pasa con el código defectuoso**.
- Mi variante, con el `turn_context` hostil como **último** contexto, falla en `2e6c501` (`UnicodeEncodeError`) y pasa en HEAD.

La corrección sí queda cubierta por el test unitario de `_serializa_contexto` (`:145`), pero el test de extremo a extremo no prueba lo que dice su nombre.

**Arreglo.** Mover el `turn_context("gpt\ud800x")` detrás de `respuesta(0, …)`.

## Baja

- **B1. Codex: un cambio de modelo seguido de una compactación al empezar el turno.**
  - En F10, línea 2622, el `token_usage_record` de la compactación (269.647 tokens) llega antes que el `turn_context` del turno nuevo. Por eso se atribuye al modelo anterior (gpt-6.1-sol en lugar de gpt-6-astra). Afecta a 1 turno de 7.637.
  - Leer `thread_settings_applied.thread_settings.model` **no** sirve como arreglo. Lo simulé en un solo snapshot con 7.682 registros (`c10.py`), comparando con la atribución por `turn_id`:
    - solo `turn_context`: 1 discrepancia;
    - `turn_context` más `thread_settings`: 107 discrepancias.
  - Recomendación: documentarlo como límite conocido, o resolver el modelo por `turn_id` difiriendo el turno hasta ver su `turn_context`.
- **B2. Los docstrings afirman que los forks copian las líneas, y no está medido.**
  - En Codex (`ingesta_codex.py:6`) hay 0 `response_id` compartidos entre los 20 forks y sus padres.
  - En pi (`ingesta_pi.py:5-6`) no hay ningún fork.
  - Los tests `test_un_fork_…` de los dos extractores prueban un escenario hipotético. Son inocuos, pero los docstrings deberían decir "no observado".
- **B3. pi convierte los 22 turnos con error y uso 0 en turnos.** Inflan el recuento de turnos, igual que el parser. Desaparece con M1.
- **B4. `_suma` acepta un `reasoning` negativo** (`ingesta_pi.py:50`). Por ejemplo, `output = 5000` con `reasoning = -1000` se guarda como `go = 4000` sin anomalía. Y un `reasoning` no numérico descarta un turno cuyo `output` es válido. Desaparece con A1.
- **B5. pi: hora ISO de la línea frente a `message.timestamp`** (`ingesta_pi.py:65`).
  - La diferencia mueve 23 turnos a otra hora UTC y 1 a otro día.
  - El parser usa el mismo criterio, así que no rompe la equivalencia, pero conviene documentar cuál manda.
- **B6. pi: el plan B (proyecto sacado de la carpeta) no coincide con el del `cwd`** (`ingesta_pi.py:26`).
  - pi solo cambia `/` por `-` y conserva los espacios, así que en 7 de 22 ficheros (4 de 14 carpetas) da otro nombre. Por ejemplo, `w2 w3/w4` frente a `w2/w3/w4`, o `␠w7/w8/w9` con un espacio inicial.
  - Hoy no tiene efecto, porque las 22 cabeceras traen `cwd`.
  - Arreglo: aplicar `_slug` también al nombre de la carpeta.
  - El test `test_sin_cabecera_…` (`test_ingesta_pi.py:79`) no usa espacios.
- **B7. Codex: casos límite del flag `registros`.** Hoy no hay ninguno en los datos y `archived_sessions` está vacío.
  - Un rollout sin registros (versiones antiguas de Codex) queda con **0 tokens** y solo deja una anomalía. El parser sí lo contaba.
  - El detalle de la anomalía incluye el contador (`ingesta.py:213`), así que un fichero que crece genera una fila nueva en cada pasada.
  - `ctx["registros"]` se activa antes de validar el uso (`ingesta_codex.py:81`). Una sola línea `token_usage_record` hostil silencia las anomalías de un fichero en formato antiguo.

## Tests que no prueban lo que dicen

- **M2:** prueba de regresión de Codex con el orden de líneas equivocado.
- **A1:** fixture de pi con un `totalTokens` irreal. Además, `test_turno_basico_…` y `EquivalenciaTest` dan por buena la suma del `reasoning`.
- **`test_lineas_hostiles` de pi** (`:103`): uno de sus casos se llama `"sin_id"`, pero es un id válido. Hace falta un caso con `id` ausente o de otro tipo.
- **B6:** el test del proyecto sacado de la carpeta no usa espacios.

## Bien resuelto

- `codex:<response_id>` es la clave correcta: es única, cuadra al token con el recuento independiente y no deja turnos a cero tras una compactación.
- `texto_seguro` está unificado en los tres extractores, y `_serializa_contexto` ya no puede bloquear un fichero.
- La comprobación de esquema obsoleto da un error claro en vez de un `OperationalError`.
- Rechazar un modelo o proveedor presente pero inválido, en lugar de cambiarlo por otro, es coherente entre Claude y pi.
- En pi, el proveedor se toma por turno y la reanudación es estable.

## Acciones recomendadas (por prioridad)

1. **A1:** decidir sobre el `reasoning` de pi (opciones a o b de A1); en cualquier caso, corregir el fixture y los docstrings.
2. **M1:** clave `pi:<responseId>` y omitir los turnos con error y uso 0.
3. **M2:** reordenar las líneas del test de regresión.
4. **B1, B2, B5 y B7:** documentar, y añadir el ajuste de B6 (carpeta con `_slug`).

## Preguntas abiertas

- ¿Se corrige también `lectores_logs.parsear_pi`, o se firma la divergencia de −3,4 M en la sombra?
- ¿Se acepta cambiar la clave de pi a `responseId` antes de que exista una base desplegada?
- ¿Qué hora manda en pi: la de escritura de la línea o el inicio de la petición?
