# Revisión de código de la Fase 2 (histórico, presupuestos, CSV): seguridad y corrección

Fecha: 2026-10-14 · Rango: `git diff 64a41d8..HEAD` (7 commits, 24 ficheros, +1850/−43) · Rama `feat/fase1-ingesta-hermes`.
Solo lectura: no se ha modificado código ni tests y no se ha escrito en `~/.cost-guard` ni en `Application Support`. Tests:
`python3 -m unittest discover -s tests` → **521 tests OK** (46 s, Python 3.14.6, macOS). Las pruebas de concepto se hicieron con
copias en el directorio temporal de la sesión.

Marcas: **[V]** verificado (lectura del código con `fichero:línea`, ejecución o prueba de concepto); **[S]** supuesto o no
comprobable en este equipo.

## Resultado

La fase está bien construida en casi todo lo que pedía la revisión anterior: `avisos.json` aparte y con un solo escritor,
precondición `activo`, `ignorar` aplicado, fechas con la librería C, CSV neutralizado y cifrado con un tipo propio, panel sin
persistir nada, semáforo y validación del rango antes de consultar. **Pero el XSS del panel (RT1) sigue abierto por un camino
que ni el escapado ni la CSP cubren:** el JSON del panel se inserta también dentro de un comentario `/* … */` de `app.js`, y un
nombre de modelo con `*/…/*` se ejecuta dentro del script con nonce. Lo he reproducido. Mientras eso siga así, el endpoint
`/historico.csv` y `POST /clave` quedan al alcance de cualquier herramienta que pueda escribir en los logs. Es lo único que yo
bloquearía antes de fusionar; el resto son hallazgos medios o bajos y pruebas que faltan.

## 1. Los 12 cambios obligatorios del diseño v2

| # | Cambio | Estado | Evidencia |
|---|---|---|---|
| 1 | Escape del JSON, CSP con nonce y nada de `innerHTML` con datos | **Parcial** | Hecho: `panel.py:67-72` (escapa `< > & U+2028/2029`), `servidor_local.py:34-39` (nonce por respuesta), `:191` (CSP cerrada en el resto de respuestas), `app.js:6-13` (`escapar(D)`) y `app.js:34-42` (`data-a`/`data-v` en vez de `onclick`). Probado en `test_panel_seguro.py:27-116`. **Sin cerrar:** H1, que permite ejecutar código igualmente. |
| 2 | Avisos fuera de `state.json`, un solo escritor | **Sí** | `avisos.py:14-68`, escritura atómica 0600 `:30-45`, solo la llama `trabajador_barra.py:52-58`. Probado en `test_presupuestos.py:132-166`, también que sobrevive a `/guardar` (`:144-147`). |
| 3 | Precondición `activo`, `ignorar` y coincidencia en tres fechas que crucen el 25-10 | **Parcial** | `historico.py:31-35` y `:101`. Probado en `test_historico.py:97-117` y `:171-174`. **Falta:** el test de coincidencia (`test_historico.py:135-150`) usa la fecha de hoy y no inyecta las tres fechas. Con la de hoy (14-10) no cruza el cambio de hora. |
| 4 | Presupuesto por nombre mostrado y plan final post-alias de una lista cerrada | **Parcial** | Agrupar por nombre mostrado y por plan final: `historico.py:181-196` con `agregacion.proyecto_mostrado`/`plan_del_consumo` (`agregacion.py:127-136`). Probado en `test_presupuestos.py:82-100`. **Falta:** el nombre del plan es texto libre con un `datalist` de sugerencias (`ajustes.js:119,131`), no una lista cerrada. Lo atenúa la marca "sin consumo con ese nombre" (`ajustes.js:116`). |
| 5 | Fechas con la librería C, aritmética por fechas y fixtures del 25-10 y del 29-03 | **Sí (solo POSIX)** | `historico.py:50-89`, sin `zoneinfo`. Fixtures del 25-10-2026 y del 28-03-2027 en `test_historico.py:46-73`. Toda la clase se salta sin `time.tzset` (`:20`), así que **en Windows no hay ninguna prueba de fechas**. |
| 6 | CSV cifrado por frase con tipo propio; el panel no persiste nada; el CLI escribe en una carpeta con nombre generado | **Sí** | `cgx.py:27-28` (`TIPO_CSV=4`, comprobado por `_exige_tipo`, `cgx.py:341`), `exportacion_csv.py:93-143`, `servidor_local.py:248-252` (en memoria). Probado en `test_exportacion_csv.py:145-170` y `:224-235`. |
| 7 | Neutralización en todas las columnas de texto, limpieza de bidi e invisibles y validación de alias y nombres | **Parcial** | CSV: `exportacion_csv.py:27-34` (también ancho completo y NFKC); nombres de presupuesto: `presupuestos.py:18-19` (`isprintable` rechaza Cf/bidi). **Falta:** los alias siguen sin validarse (`ajustes.py:117`, solo `strip`), y el panel no limpia los caracteres bidi (RT2 lo pedía "también en el panel"). |
| 8 | Regex estricta, rango calculado antes de consultar, semáforo y "otros"/"(ocultos)" | **Sí, con huecos menores** | `historico.py:24,38-47,79-89`, semáforo en `servidor_local.py:175,242`, `(otros)`/`(ocultos)` en `historico.py:114-117,161-166`. Probado en `test_historico.py:153-173` y `:255-277`. Huecos (H8 y H9): no hay tope superior de año (el diseño decía 2100) y `parse_qs` puede lanzar fuera del `try`. |
| 9 | Migración una vez en la barra, campo antiguo fuera de la UI y `presupuestos` en `normalizar` | **Sí** | `presupuestos.py:55-70`, `trabajador_barra.py:52-55`, `ajustes.py:117-131`, columna quitada de `ajustes.js`. Probado en `test_presupuestos.py:64-80,211-224`. `errores_config` no valida `presupuestos`, pero `normalizar` lanza `ValueError` y el servidor responde 400 con el mismo efecto. Ver H7. |
| 10 | `CLASES` sobre las columnas reales y test de cobertura | **Sí (definición)** | `almacen.py:32-54` y `test_almacen.py:478-489`. Ni `historico()` ni el exportador consultan `CLASES` (lo pedía el diseño 2.4). Hoy no hay fuga porque solo devuelven agregados, pero la tabla no protege nada todavía. |
| 11 | `datos_desde`, cubos `null`, un solo aviso con el umbral más alto y evaluación pospuesta si la vuelta de ingesta no fue completa | **Parcial** | `datos_desde` y `null`: `historico.py:156-170`, probado en `test_historico.py:87-94`. Umbral más alto: `presupuestos.py:102` + `avisos.py:56-63`, probado en `test_presupuestos.py:179-186`. **Falta:** posponer la evaluación cuando la vuelta no es completa (no hay ninguna referencia a `completo` en `presupuestos.py` ni en `trabajador_barra.py`). Tampoco se omite la comparación cuando el periodo anterior es incompleto (PM7): ver H5. |
| 12 | Recarga suspendida en Histórico y hash `#historico` | **Sí** | `ajustes.js:21-30,244` e `historico.js:113`. Test estático en `test_panel_seguro.py:46-55`. "Semana (lunes)" en `ajustes.js:120`. |

Del diseño 2.2 tampoco está el color ámbar/rojo del presupuesto en el icono y el desplegable de la barra, ni la fila destacada en
Histórico: `presupuesto` no aparece en `desplegable_*.py` ni en `historico.js` [V].

## 2. Hallazgos

### H1 · Crítica [V, reproducido] · El JSON del panel sale de un comentario y se ejecuta dentro del script con nonce

- **Mecanismo:** `panel.py:79` hace `_leer("app.js").replace("__DATOS__", json_para_script(d))`, y `str.replace` sustituye **todas**
  las apariciones. `__DATOS__` aparece dos veces: en `app.js:13` (`const D = escapar(__DATOS__);`) y **dentro del comentario de
  `app.js:1`** (`/* … ya vienen resueltos en __DATOS__ (D); …`). `json_para_script` escapa `< > &` y U+2028/2029, pero no `*` ni `/`,
  y `_sana` (`panel.py:20-33`) tampoco toca esos caracteres. Un nombre de modelo (texto libre de hasta 120 caracteres en los logs)
  como `*/globalThis.PWNED=1/*` cierra el comentario, su contenido queda como código y el `/*` final se traga el resto del JSON hasta
  el `*/` original.
- **Prueba de concepto:** copié `json_para_script` tal cual, construí `app.js` con `{"modelos": {"*/globalThis.PWNED=1/*": 1}}` y lo
  ejecuté con `node` (`vm`): `PWNED = 1`. Con un nombre inocuo, `PWNED = undefined`.
- **Impacto:** el código corre en el `<script nonce>` legítimo, así que la CSP no lo para. `escapar(D)` llega tarde porque actúa sobre
  el objeto, no sobre el texto fuente. Con la sesión del panel puede hacer `POST /clave` (sobrescribir claves del Llavero),
  `POST /guardar` y leer `/historico.csv`. Para sacar los datos no necesita `connect-src`: le basta navegar a otro sitio
  (`location=…`), y la CSP no limita la navegación. `eval` sí está bloqueado (no hay `unsafe-eval`), pero 120 caracteres sin
  comillas bastan, y se pueden encadenar varios nombres. El `panel.html` en disco que se abre por `file://` no lleva CSP y queda
  igual de expuesto.
- **Arreglo:**
  1. Quitar `__DATOS__` del comentario y sustituir un marcador único que se compruebe (`assert app.count(MARCA) == 1`, `replace(…, 1)`).
  2. Mejor todavía: llevar los datos en `<script type="application/json" id="datos">` y leerlos con
     `JSON.parse(document.getElementById("datos").textContent)`, un bloque de datos que el navegador no ejecuta. Mantener el escape
     de `<`.
  3. Como defensa en profundidad, escapar también `/` como `\/` en `json_para_script`: es JSON válido y anula tanto `*/` como `</`.
  4. Test: construir el panel con el modelo `*/globalThis.X=1/*` y ejecutarlo en `node` → `X` sin definir. Otro test: `app.js`
     contiene el marcador exactamente una vez.

### H2 · Media [V] · «Quitar» un presupuesto puede borrar otra fila

- **Mecanismo:** el botón (`ajustes.js:123-124`) llama primero a `leer()`, que reconstruye `c.presupuestos` a partir del DOM y
  **filtra** las filas sin nombre o con 0 tokens (`ajustes.js:215`). Después borra por el índice que la fila tiene en el DOM. Ejemplo
  con las filas [A, B vacía (recién añadida), C]: quitar C borra nada y hace desaparecer B; quitar B borra C. El usuario lo ve
  después de repintar, pero si no se fija, guarda sin el presupuesto C.
- **Arreglo:** guardar el índice en la fila (`data-i`) y borrar sobre una lista sin filtrar, o filtrar solo al guardar. Quitar a
  mano a mitad de edición no debería descartar filas incompletas.

### H3 · Media [V] · La CSP del panel no impide la exfiltración, y `style-src` va con `'unsafe-inline'`

- `servidor_local.py:37-38`: `style-src 'unsafe-inline'` (el diseño pedía nonce). Es entendible, porque `h()` y las plantillas usan
  atributos `style` y un nonce no los cubre. Con `img-src 'self'`, una inyección de CSS no puede sacar datos. Riesgo bajo por sí
  mismo.
- La CSP no puede bloquear la navegación de nivel superior (`location`, `window.open`), ni WebRTC en Chromium [S]. Es una barrera
  contra la inyección, no contra la exfiltración. Por eso H1 es crítica, y el código no debería decir que la CSP «no permite nada
  externo» (`test_panel_seguro.py:93`).
- **Arreglo:** documentarlo como límite. La defensa real es H1 más el escape.

### H4 · Media [V] · El aviso puede saltar con la ingesta a medias (PM8 sin hacer)

- `presupuestos.vigilar` (`presupuestos.py:107-119`) evalúa en cada ciclo, sin mirar si la última vuelta del ingestor fue completa.
  Tras un arranque con atraso, el consumo puede ir por detrás varios ciclos. El aviso no se repite (`avisos.py:60`), pero puede
  llegar tarde, y Ajustes puede enseñar un porcentaje bajo que no es real.
- **Arreglo:** que el ingestor publique `completo` (ya lo devuelve: `ingesta.py:136,215`) y que `vigilar` no evalúe en esa vuelta.
  Test: con `completo=False` no se escribe `avisos.json`.

### H5 · Media [V] · La comparación con el periodo anterior engaña al principio (PM7 a medias)

- `historico.py:145-147` calcula `anterior` aunque el periodo anterior empiece antes de `datos_desde`. `historico.js:68,72` compara
  si `anterior > 0`: el primer mes con datos parciales enseña, por ejemplo, «+300 % ahora». La proyección (`historico.py:178`)
  también se calcula aunque el mes empiece después de `datos_desde`.
- **Arreglo:** devolver `anterior: null` cuando el inicio del periodo anterior es anterior a `datos_desde`, y marcar la proyección como
  "primer mes con datos".

### H6 · Baja-Media [V] · Los alias no se validan y el panel no limpia los caracteres bidi (RT2 a medias)

- `ajustes.py:117` sigue haciendo solo `strip()`. Un alias con RLO (U+202E) o invisibles se ve invertido o suplantado en el panel,
  en Ajustes y en el `datalist` de presupuestos. En el CSV sí se limpia.
- **Arreglo:** aplicar a los alias la misma regla que `presupuestos._nombre_ok` y rechazar con 400.

### H7 · Baja [V] · Migración: carrera con el panel y config ilegible

- `presupuestos.migrar` (`:55-70`) lee `cfg()` sin bloqueo y escribe después. Si el panel guarda antes de la primera migración,
  manda `presupuestos: []`: `seccionPresupuestos` siempre crea la lista (`ajustes.js:129`) y `leer()` la envía. Desde ese momento
  `"presupuestos" in conf` es cierto y los presupuestos antiguos no se migran nunca, sin aviso.
- Lo mismo pasa si `cfg()` devuelve los valores por defecto porque `config.json` no se pudo leer en ese momento (`config_y_estado.py:31-49`).
- Dos raws con el mismo alias generan dos filas iguales; `validar(…, estricto=False)` descarta la segunda (`presupuestos.py:45-46`)
  y su tope se pierde.
- **Arreglo:** migrar dentro de `_bloqueo(CONFIG)` leyendo del disco; marcar la migración con una clave propia (por ejemplo
  `migraciones: ["presupuestos"]`) en vez de usar la presencia de la lista; en duplicados, quedarse con el máximo y registrarlo.

### H8 · Baja [V] · Fechas extremas: sin tope superior y `mktime` fuera de rango

- `historico._fecha` (`:38-47`) no tiene tope superior (el diseño decía [2000, 2100]). Con `desde=hasta=9999-12-31&paso=mes`,
  `_siguiente` (`:65`) crea `date(10000,1,1)` y lanza un `ValueError` que no es `ErrorHistorico`: el servidor responde 500 y el CLI
  (`cli_copias.py:299`, que solo captura tres tipos) termina con una traza.
- En Windows, `time.mktime` falla antes de 1970 y después de 3000 [S, documentado en CPython]. `_anterior` (`historico.py:146`)
  retrocede tantos cubos como tenga el rango: con `paso=mes`, 400 meses desde el año 2000 llegan a 1966 y la respuesta sería 500 en
  Windows.
- `_FECHA` usa `\d` (acepta dígitos que no son ASCII) y `$` (acepta un `\n` al final). Ahora los rechaza `fromisoformat`
  (comprobado en 3.14), pero no conviene depender de eso.
- **Arreglo:** `re.fullmatch(r"\d{4}-\d{2}-\d{2}", s, re.ASCII)`, año ≤ 2100 y limitar `ant_lo` a `datos_desde` (o como mucho a 1970).

### H9 · Baja [V] · `parse_qs(max_num_fields=8)` lanza fuera del `try`

- `servidor_local.py:237`: con 9 parámetros o más se lanza `ValueError("Max number of fields exceeded")` antes del `try`.
  `socketserver` imprime la traza y cierra la conexión sin respuesta. No es explotable, pero rompe el contrato de responder 400.
- **Arreglo:** meter el `parse_qs` en el `try` y responder 400.

### H10 · Baja [V] · Auditoría y ficheros del CLI

- `exportaciones.log` crece sin tope (RT8 pedía rotarlo): `exportacion_csv.py:83-90`.
- `escribir_claro` (`:98-106`) escribe directamente en el destino: si la escritura falla queda un CSV a medias, y si falla
  `auditar` queda un CSV sin entrada en el registro. RT8 pedía que no hubiera export sin entrada.
- `descifrar` (`:130-143`) deja un CSV en claro sin auditarlo.
- El CLI no captura `OSError` (`cli_copias.py:299`).
- **Arreglo:** escribir en `.part` + `reemplazar` como en el cifrado, auditar antes del `replace` y rotar a N líneas.

### H11 · Baja [V/S] · Windows y concurrencia de `avisos.json`

- `avisos._guardar` (`:39`) usa `os.replace` directamente, sin el `exportacion.reemplazar` con reintentos que ya existe para el
  antivirus. Si falla, el error queda registrado y el aviso se reintenta en el ciclo siguiente: nunca se duplica, porque se escribe
  antes de avisar [V].
- Si llegan a convivir dos procesos de la barra (el servidor ya reintenta por puerto ocupado), los dos son "el único escritor" y
  pueden avisar dos veces [S].
- Los `chmod` 0600/0700 no hacen nada en Windows: la privacidad depende de la ACL del perfil [S].
- `os.path.islink` no detecta las *junctions* de Windows en `exportacion_csv.carpeta()` (`:75`) [S].

### H12 · Baja [V] · Otros

- `historico.py:152` convierte cualquier `TypeError` o `IndexError` (también un dato roto a mitad de consulta) en «la base no tiene
  datos» (503). Esto oculta fallos reales.
- `/origenes` evalúa presupuestos (`servidor_local.py:308`) fuera del semáforo.
- El README sigue diciendo «presupuesto mensual» en Proyectos (`README.md:161`).
- La cabecera de `cgx.py:4-5` no menciona el tipo 4.
- `entero()` de `ajustes.js:14` convierte «1.5M» en 15 tokens. El fallo ya existía, pero ahora afecta a los presupuestos.

### Revisado y sin hallazgos

- **`plataforma.avisar`:** se lanza con lista de argumentos (sin shell), escapa `\` y `"`, y los textos son de la aplicación y sin
  nombres (`presupuestos.py:118`). No hay inyección en osascript [V].
- **`<script nonce>` reemplazado solo en la primera aparición** (`servidor_local.py:36`): hay un único `<script>` en `construye`
  (`panel.py:89`) y el JSON no puede introducir otro porque `<` está escapado. Si algún día hubiera un `<script>` antes, el fallo
  sería cerrado: el script real quedaría bloqueado [V].
- **Endpoints nuevos:** pasan por `_host_ok` (`Sec-Fetch-Site` solo `same-origin`/`none`, `servidor_local.py:208-215`) y por la
  sesión. Una página en otro puerto de `127.0.0.1` envía `same-site` y recibe 403. Parámetros por lista cerrada, sin repetir. El
  semáforo deja dos a la vez y la tercera recibe 429, y está probado. El `Content-Disposition` solo lleva fechas ISO y valores de
  conjuntos cerrados [V]. No hay límite de tiempo por consulta (solo el `timeout` del socket); con el tope de 400 cubos y PK sobre
  `hora`, es aceptable [S].
- **CSV:**
  - `QUOTE_ALL`, `;`, BOM y CRLF.
  - Neutralización tras NFC, sin invisibles (U+00AD, U+061C, U+180E, U+200B-200F, U+2028-202E, U+2060-206F, U+FEFF, U+FFF9-FFFB,
    más las categorías Cc/Cf/Cs/Co), espacios y `\t\r` iniciales ignorados y comprobación NFKC (`＝`, `﹢`).
  - Las cifras pasan por `_entero`.
  - Nombre generado y `O_EXCL|O_NOFOLLOW`.
  - No encontré ninguna variante Unicode que empiece por fórmula y sobreviva [V con la batería de `test_exportacion_csv.py:20-45`].
  - Los caracteres invisibles de categoría Lo (U+3164, relleno hangul) no se quitan: se pueden usar para suplantar nombres, pero no
    forman fórmulas [V].
- **Confusión de tipos en el cifrado:** `TIPO_CSV` se exige en las dos direcciones (`cgx.py:341-348,392-396`). Una exportación de
  la base no se abre como CSV y un CSV no se importa como base. Está probado en un sentido (`test_exportacion_csv.py:152-154`) [V].
- **`avisos.json`:**
  - La escritura es atómica (`mkstemp` + `fsync` + `replace`).
  - Si el fichero está corrupto se trata como «nada avisado» y se reavisa una vez.
  - Las entradas de periodos pasados se purgan.
  - Las claves llevan nombres de proyecto, pero el fichero es 0600 [V].
- **Cambio de hora:** probé `_medianoche` con `TZ` en Santiago, La Habana, Beirut, Asunción y Madrid (macOS). Cuando la medianoche
  no existe, `mktime` la lleva a la 01:00 del mismo día; cuando es doble, toma la primera. Por eso `indice[...]` (`historico.py:141`)
  no ve fechas fuera de los cubos [V]. En Windows y glibc no lo he comprobado. Si `mktime` llevara la medianoche al día anterior, el
  resultado sería un `KeyError` y un 500 [S].
- **Refactor de `agregacion.agregar`:** es una extracción literal de `modelos_ignorados`, `es_ignorado`, `proyecto_mostrado` y
  `plan_del_consumo` (diff de `agregacion.py:119-136,165-189`) que conserva el orden y la semántica. Además deja de llamar a `cfg()`
  dos veces. Los 521 tests pasan [V].
- **Coherencia del histórico con el informe:** las claves de registro (`agente`, `proveedor`, `proyecto_raw`) dan el mismo `plan_de`
  y las mismas alternativas que `consulta._registro`. La diferencia es que el histórico agrupa por hora y el informe por cubos de
  15 minutos: está documentado en `historico.py:6-7` y solo se nota en zonas de media hora [V]. Los proveedores «auto» dependen del
  conjunto de modelos de cada rango; no encontré un caso en que eso cambie el plan de un modelo [S].

## 3. Pruebas que faltan o que dejarían pasar una regresión

| Mutación o regresión | ¿La detectan los tests? | Prueba que la detectaría |
|---|---|---|
| `__DATOS__` dentro de un comentario o cualquier contexto que no sea una expresión (H1) | **No**: `test_panel_seguro.py:33-37` solo cuenta los `</script>` | Construir el panel con `*/globalThis.X=1/*` y ejecutarlo en `node`; contar el marcador. |
| Quitar `pasados >= 3` o cambiar la fórmula de la proyección | **No**: `test_historico.py:186` comprueba `assertIn(x is None, (True, False))`, que siempre es cierto | Inyectar `hoy` (hoy usa `date.today()`) y comprobar `None` el día 2 y el valor exacto el día 10. |
| Romper `_anterior` con paso `dia` o `mes` | **No**: solo se prueba `semana` | Casos con `dia` y `mes`, incluido el cruce de enero a diciembre. |
| Que el histórico y el informe dejen de coincidir con el cambio de hora | **No**: la coincidencia solo se prueba con la fecha de hoy | Reloj inyectado en `consultar` e `historico` el 20-10, el 26-10 y el 10-11 (lo que pedía PM2). |
| Zonas de media hora (PM6) | **No** | `TZ=Asia/Kolkata`, con la diferencia esperada fijada en el test. |
| Ninguna prueba de fechas en Windows | Todas se saltan sin `tzset` | Inyectar las funciones `mktime`/`localtime`, o probar en Windows con `--autoprueba`. |
| Cambiar el divisor `28` del tope mensual de `_cubos` | **No** (solo se prueba el recuento de 322) | `paso=mes` con 401 meses → `ErrorHistorico` sin abrir la base. |
| Quitar `max_num_fields` o lanzar fuera del `try` (H9) | **No** | Petición con 9 parámetros → 400. |
| Quitar la llamada a `migrar()` o `vigilar()` del trabajador, o llamar a `vigilar` desde el panel | **No**: ningún test toca `trabajador_barra` | Test del ciclo con `informe` simulado: una migración y un `vigilar` por ciclo; ningún hilo HTTP escribe `avisos.json`. |
| Error de índice en «Quitar» (H2) y validación de `leer()` en Ajustes | **No** (no hay tests del JS de Ajustes) | Test en `node`/jsdom, o mover la lógica a una función pura y probarla. |
| Año > 2100 o 9999 (H8) | **No** | Esas fechas → 400. |
| `--importar` o `--probar-restauracion` con un `.csv.cgx` | **Parcial**: solo se prueba `descifrar_con_frase` con `TIPO_EXPORT` | Llamar al CLI y esperar un mensaje claro. |
| Evaluación con una vuelta de ingesta incompleta (H4) | No existe | Ver H4. |

## 4. Acciones recomendadas, por orden

1. **H1:** marcador único fuera de comentarios (o bloque `application/json`), escapar `/` y test de ejecución en `node`. Es lo que
   bloquea la fusión.
2. **H2:** índice estable al quitar presupuestos.
3. **H4 y H5:** posponer la evaluación con ingesta incompleta y no comparar con periodos anteriores a `datos_desde`.
4. **H8 y H9:** validar las fechas con `fullmatch` y `re.ASCII`, tope 2100, limitar `ant_lo` y meter `parse_qs` en el `try`.
5. **H6 y H7:** validar los alias; migrar bajo bloqueo con una marca propia.
6. Las pruebas de la sección 3, sobre todo la proyección (hoy es una tautología) y la coincidencia con reloj inyectado.
7. **H10 a H12:** rotar el registro de auditoría, usar `.part` en el CSV en claro, reintentar el `replace` en Windows y corregir la
   documentación.

## Preguntas sin resolver

- ¿Hay que añadir ahora el color del presupuesto en el icono y el desplegable de la barra, y la fila destacada en Histórico
  (diseño 2.2), o se deja para más adelante?
- ¿El plan del presupuesto tiene que ser una lista cerrada (`select`) como pedía D6, o basta el `datalist` con la marca «sin consumo
  con ese nombre»?
- ¿Se acepta la descarga por `GET /historico.csv` en lugar del `POST` que proponía el diseño 2.3? Con `Sec-Fetch-Site` y
  `SameSite=Strict` me parece suficiente, pero contradice el texto del diseño.
- El comportamiento de `mktime` con una medianoche que no existe en Windows y glibc, y si `historico` puede llegar a lanzar
  `KeyError` allí. No lo puedo comprobar en este equipo.
