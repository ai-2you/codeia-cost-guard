# Revisión del paso 1.2b (Codex) — fase 1

Fecha: 2026-10-14 · Rama: `feat/fase1-ingesta-codex` (diff `feat/fase1-ingesta-claude...feat/fase1-ingesta-codex`) · Modo: solo lectura.
Ficheros revisados: `infobar/ingesta_codex.py`, `infobar/ingesta.py`, `infobar/almacen.py` (columna `lecturas.contexto`), `infobar/ingesta_claude.py`, `tests/test_ingesta_codex.py`, `plan.md` (sin tocar).

## Resultado

La explicación del −3,26 % es correcta: las 212 repeticiones son re-emisiones de verdad. El paso tiene, sin embargo, dos fallos reales:

1. **Faltan las respuestas de compactación.** Son 52 respuestas y 13,9 M de tokens, un 1,2 %. Ni la base ni el parser las ven, así que la sombra no puede detectarlo.
2. **Un `turn_context` hostil bloquea su fichero en cada pasada.** El fallo está en la serialización del contexto y es una regresión de la garantía de 1.2a.

Además hay dos puntos de severidad media: el espacio de claves usa la sesión raíz y no el hilo, y se añadió una columna sin migración.

La suite pasa: `python3 -m unittest discover tests` → **206 tests OK** (3,1 s).

Los rollouts reales son 46 ficheros. Uno se estaba escribiendo durante la revisión, así que los recuentos derivan entre 7.503 y 7.508. Salvo que se indique otra cosa, las cifras son de una misma ejecución. Las sesiones aparecen con alias (S00…S46).

Todos los scripts de comprobación son de solo lectura y usan bases temporales en el scratchpad.

## Alta

### A1. Las respuestas de compactación se pierden y quedan filas de 0 tokens
`ingesta_codex.py:84-100`

**Qué ocurre.** En cada compactación Codex escribe un `token_usage_record` con el uso real de la respuesta que resume. El `token_count` que la sigue lleva `last_token_usage` a cero (solo `total_tokens` > 0) y su `total_token_usage` **no** incluye esa respuesta. Como el extractor solo lee `token_count`:

- se pierde el consumo de la compactación;
- se crea un turno con los cuatro contadores a 0, lo que infla el número de turnos.

**Evidencia** (scripts `a5.py` y `a6.py`):

- Hay 64 líneas `compacted` y 56 `token_count` con uso a cero; 52 de ellos aparecen justo tras un `compacted`.
- Hay 52 `token_usage_record` sin ningún `token_count` con el mismo uso, que suman **13.908.351 tokens**. Los 52 tienen un `compacted` a ≤ 8 líneas.
- El `thread_token_usage` del record sube exactamente ese uso. El acumulado de `token_count` no lo hace (por ejemplo, va 225.409 tokens por detrás desde ese punto). Es consumo facturado.
- Referencia independiente: los `response_id` de los records son únicos al 100 % (7.506 records, 0 repetidos) y suman 1.138.937.827 tokens. La base suma 1.125.029.476, un **−1,22 %**.
- Ningún uso de la base falta en los records: "en base y no en records" = 0.
- La simulación completa sobre una base temporal deja 7.508 turnos, de los cuales **52 tienen 0 tokens**.

**Por qué la sombra no lo ve.** El parser actual tampoco lee los records: `_hallar_uso` descarta los dicts con `total_tokens`. Lo que `plan.md` llama "explicado y firmable" es cierto frente al parser, pero no frente al consumo real.

**Opciones** (decisión del usuario, porque cambia la clave):

- **(a) Fuente = `token_usage_record`, con clave `codex:<hilo>:<response_id>`.**
  - Elimina el truco acumulado + huella, la caída a la hora (B1) y la sensibilidad de la huella a campos extra.
  - `token_count` quedaría solo para rollouts sin records (versiones antiguas). El modo se decide por fichero y se guarda en el contexto, para no contar dos veces si en un mismo fichero hay de ambos.
  - Obliga a reconstruir la base, porque las claves viejas se duplicarían. Hoy no hay base desplegada (véase M2).
- **(b) Arreglo mínimo.**
  - Ingerir también los records cuyo uso no tenga `token_count`, con clave por `response_id`.
  - Descartar los `token_count` con los cuatro contadores a 0.

### A2. Un sustituto suelto en el contexto bloquea el fichero en cada pasada (regresión de 1.2a)
`ingesta.py:229-233` (`_serializa_contexto`) y `ingesta.py:214-216`. Origen: `ingesta_codex.py:27-28` y `:80-82`.

**Qué ocurre.**

1. `_texto()` acepta cualquier `str`, incluido `"gpt\ud800"`, así que ese valor entra en `ctx["modelo"]`.
2. El turno de ese modelo se descarta bien: `almacen.problema` rechaza los sustitutos.
3. Pero `_serializa_contexto` lo vuelca con `ensure_ascii=False`, y `guardar_lectura` revienta con `UnicodeEncodeError`.
4. Esto ocurre **fuera** del aislamiento por línea. Los turnos ya se han confirmado, pero `lecturas` no se guarda, así que el fichero se relee entero y vuelve a fallar en todas las pasadas.

Lo mismo pasa con `session_meta.model_provider`. `cwd` no es vía de ataque, porque `_slug` lo deja en ASCII.

**Contraejemplo** (`a11.py`).

- **Fichero** (en este orden): `turn_context(gpt-5)`, `token_count`, `turn_context("gpt\ud800")`, `token_count`.
- **Resultado:** `pasada 0 EXCEPCION: UnicodeEncodeError`, `pasada 1 EXCEPCION: UnicodeEncodeError`, `lecturas 0`.
- **Condición:** el fallo solo se da si el valor hostil es el **último** contexto vigente al cerrar la pasada. Con un `turn_context` válido detrás, la primera versión del script pasó sin error.

Hoy ningún código de `infobar/` llama a `ingerir_fichero`, solo los tests. El bucle de 1.2/1.3 tendrá que aislar las excepciones por fichero o se caerá toda la ingesta.

**Arreglo.**

- En `_serializa_contexto`, quedarse solo con valores que pasen la misma validación de texto del almacén (`almacen._texto_ok`: sin NUL, control ni sustitutos), o como mínimo `ensure_ascii=True`.
- Que `_texto()` de Codex aplique ese mismo filtro.
- Añadir un test de regresión con el valor hostil como último contexto.

## Media

### M1. La clave usa el espacio de la sesión raíz, pero el acumulado es por hilo
`ingesta_codex.py:32` y `:97`

- **El acumulado es por hilo.** Todos los subagentes (27 ficheros) llevan `session_id` = raíz; su hilo está en `payload.id`. En 42 de 42 ficheros con uso, el primer acumulado coincide con el primer `last`, es decir, el acumulado reinicia en cada hilo.
- **Riesgo.** Dos hilos hermanos con el mismo acumulado y el mismo `last` (típico en el primer turno de dos forks del mismo padre) comparten clave: el segundo se pierde o se anota como otra identidad.
  - En los datos reales hay **0 colisiones entre ficheros** (`a3.py`), así que el riesgo es estructural y no se ha observado.
- **Incoherencia actual.** Sin `session_meta` legible, la sesión sale del UUID del nombre, que es el hilo. Con él, sale del `session_id`, que es la raíz. El mismo fichero puede generar claves distintas según lea o no la cabecera.
  - Pasa si la primera línea supera los 256 KB de `MAX_CABECERA`. Hoy el máximo de `session_meta` es 23 KB, así que no ocurre.
- **Suplantación.** Un `session_meta` con el `session_id` de otra sesión entra en su espacio de claves. Exige poder escribir en `~/.codex`, así que es baja.
- **Arreglo.** Usar el `id` del **primer** `session_meta`, que coincide con el UUID del nombre en 46 de 46 ficheros.
  - **Cuidado:** los 20 forks tienen un segundo `session_meta` en la línea 1, que es el del **padre**. `extractor` lo vuelve a aplicar en el flujo (`ingesta_codex.py:76-78`). Hoy es inocuo porque comparte `session_id` y `cwd` con el hijo (0 ficheros con `cwd` distinto). Con `id` sobrescribiría el hilo, así que hay que ignorar cualquier `session_meta` que no sea el primero del fichero.

### M2. Se añadió `lecturas.contexto` sin migración
`almacen.py:82`

`VERSION_ESQUEMA` sigue en 1. Una base creada con el código de 1.2a se abre sin migrar y `estado_lectura` falla con `OperationalError: no such column: contexto` (`a10.py`, que reproduce la tabla `lecturas` de 1.2a).

Solo importa si existe alguna base de 1.2a; no se comprobó para no tocar `~/.cost-guard`. **Decisión del usuario:**

- subir a v2 con `ALTER TABLE lecturas ADD COLUMN contexto …` dentro de la migración bajo cerrojo que ya existe; o
- dejar escrito que no hay ninguna base de 1.2a desplegada.

### M3. Atribución de modelo: unos 34 M de tokens cambian de modelo
Ya figura en `plan.md`, pero sin magnitud.

| Modelo | Base − parser (tokens) |
|---|---|
| gpt-6.1-sol | +33,7 M |
| gpt-5.6-luna | −33,2 M |
| gpt-6-astra | −21,9 M |
| gpt-5.6-sol | −12,7 M |
| gpt-6-luna | −3,8 M |

La base (por `turn_context`) es más defendible que el parser, que usa el último `"model"` visto en cualquier línea. Conviene firmarlo en la sombra con estas cifras, porque cambia el coste por tarifa.

## Baja

- **B1. Clave sin acumulado.** Si falta `total_tokens`, la clave cae a la hora (`:97`). Las re-emisiones ocurren en segundos distintos (mediana 15 s, máximo 1.991 s), así que en ese formato se contarían dos veces. En los datos reales el acumulado está siempre. Con la opción A1(a) desaparece.
- **B2. Huella frágil.** La huella se calcula sobre el dict entero. Si una versión futura re-emite el mismo uso con un campo nuevo, cuenta doble. El orden no afecta (`sort_keys`).
- **B3. Contexto que no cabe.** Con escapes JSON el contexto puede pasar de 1000 caracteres. Entonces `_serializa_contexto` guarda `{}` entero y la pasada siguiente pierde el modelo (queda `sin-modelo`) hasta el próximo `turn_context`. Mejor descartar valores sueltos que todo el contexto.
- **B4. Proveedor o modelo con caracteres de control.** Si `model_provider` o el modelo traen caracteres de control, se descartan **todos** los turnos del fichero en vez de caer a `openai` o `sin-modelo`.
- **B5. Escritura en caché.** `cache_write_input_tokens` es 0 en todos los datos reales. Si algún día no lo es y va dentro de `input_tokens` (como `cached`), `gi` lo contaría dos veces. El parser hace lo mismo. Pregunta abierta.

## Bien resuelto

- **Re-emisión verificada de forma independiente.**
  - Por fichero, el número de records es igual al de claves únicas (46/46), y `token_count − records` = 212 = repeticiones.
  - Nunca hay un record entre una emisión y su repetición.
  - Separación máxima 1.991 s (por debajo de `TOLERANCIA_HORA_S`); el modelo no cambia y no hay repeticiones entre ficheros.
  - No hay casos legítimos de dos turnos con el mismo acumulado y el mismo `last`.
- **Mismo `last` con distinto acumulado:** son dos claves y dos turnos, como debe ser.
- **Razonamiento dentro de `output`:** `reasoning ≤ output` siempre, y `total = input + output` en los 7.664 usos no nulos.
- **Reanudación:** pasadas de 64 KB dan exactamente las mismas filas, modelos y tokens que una sola pasada (`a7.py`).
- **Contexto entre pasadas:** se ignora si cambia el inodo, la cabecera o la cola. Solo se reutiliza dentro del mismo contenido y de la misma fuente, así que un log no puede envenenar otro fichero.
- **`proyecto_de_slug`:** `..`, unicode y rutas largas (> 1000 → `codex/otros`) dan una etiqueta inocua, sin traversal; el criterio es el mismo que en Claude.
- **No hay rollouts con solo `token_usage_record`:** todos los ficheros tienen `session_meta` en la línea 0.

## Tests

- **`EquivalenciaTest`** usa datos sintéticos sin re-emisión, sin compactación y con un único modelo. Pasa, pero no prueba ninguna de las tres divergencias reales. Debería incluir una re-emisión en otro segundo (base < parser) y una compactación (record sin `token_count`).
- **`test_lineas_hostiles`** solo comprueba `nuevos == 0`, no que se anoten las anomalías.
- **Faltan:**
  - fork con dos `session_meta` (padre en la línea 1);
  - sustituto suelto como último contexto (A2);
  - base con la tabla `lecturas` de 1.2a (M2);
  - contexto que supera los 1000 caracteres (B3).
- **`test_el_contexto_sobrevive_entre_pasadas`** sí prueba lo que dice: sin persistencia, la tercera pasada daría `sin-modelo`.

## Acciones recomendadas (por prioridad)

1. **A2:** validar los valores del contexto antes de serializar (o `ensure_ascii=True`) y añadir el test de regresión.
2. **A1:** decidir entre (a) records por `response_id` y (b) añadir los records huérfanos y descartar los usos a 0. Corregir en `plan.md` la frase "explicado y firmable" para añadir la infracuenta del 1,2 % por compactación.
3. **M1:** sesión = `id` del primer `session_meta`, ignorando los siguientes.
4. **M2:** migración a v2 o declarar que no hay bases de 1.2a.
5. **M3:** firmar en la sombra el cambio de modelo con sus magnitudes.

## Preguntas abiertas

- ¿Hay alguna base creada con el código de 1.2a (sombra u otra) que obligue a migrar?
- ¿`cache_write_input_tokens` va dentro de `input_tokens` en Codex, como `cached`?
- ¿Se acepta reconstruir la base si se cambia la clave a `response_id`?
