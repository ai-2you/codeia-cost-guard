# Revisión del paso 1.1 de la fase 1 (almacén SQLite)

**Alcance:** `git diff main...feat/fase1-almacen`. Ficheros: `infobar/almacen.py` (365 líneas), `infobar/plataforma.py` (42),
`tests/test_almacen.py` (270, 31 tests), `infobar/rutas.py`, `tests/aislamiento.py` y `.gitignore`. La revisión es de solo
lectura: no se ha cambiado código ni el plan. Los contraejemplos se han ejecutado sobre bases temporales con
Python 3.14.6 y SQLite 3.53.4, y no tocan datos reales. No se tratan la ingesta por agente, `consultar()`, la sombra ni
el export cifrado, que corresponden a los pasos 1.2 a 1.5.

**Tests:** `python3 -m unittest discover tests` ejecuta 141 tests y todos pasan (OK).

## Resultado

La base es sólida en lo esencial. El agregado `horas` cuadra con `turnos` también clave a clave, incluidos los casos de
repeticiones en el mismo lote, delta cero y primer turno de una hora. La comprobación del escritor se hace dentro de
`BEGIN IMMEDIATE`, todo el SQL va parametrizado y WAL y los PRAGMA se verifican.

Aun así hay tres fallos que conviene cerrar antes del paso 1.2, porque es cuando entran datos hostiles de los logs:

- Un solo turno con NUL o con un *surrogate* suelto revierte el lote entero, y lo seguirá haciendo en cada relectura.
- Una línea cuya identidad no coincide (otro agente, modelo u hora con la misma clave) sigue subiendo los contadores.
- La clave global, junto con el `espacio_id` que se pasa por parámetro, descuadra `horas` entre espacios.

## Hallazgos (por gravedad)

### 1. ALTA: un NUL o un *surrogate* actúa como píldora venenosa y revierte el lote entero en cada relectura
- **Dónde:** `infobar/almacen.py:240` y `:249-251`. `_problema` mide con `len()` de Python, pero los CHECK
  (`:52`, `:58`) usan `length()` de SQLite, que **cuenta solo hasta el primer NUL**.
- **Contraejemplo:** `modelo="\x00"` pasa `_problema` (longitud 1). Después el CHECK `length(modelo) BETWEEN 1 AND 120`
  falla con `IntegrityError` y el `ROLLBACK` se lleva también el turno bueno del mismo lote (`filas=0`). Ocurre lo mismo
  con `clave="\x00abcd"`.
- **Surrogate:** `json.loads('"claude:\\ud800x"')` produce un `str` que SQLite no puede codificar. Falla con
  `UnicodeEncodeError` **incluso por la ruta de anomalía** (`_anomalia`, `:259`), es decir, también cuando el turno ya se
  había rechazado.
- **Impacto:** un log con una línea así bloquea para siempre la ingesta de su lote. En la práctica deja la fuente
  callada.
- **Caracteres de control:** se aceptan tal cual. Por ejemplo, `modelo="m\x1b[31mROJO\r\n"` se guarda. El plan pide
  «sin caracteres de control» (plan, líneas 162-163) y el red team, «caracteres imprimibles» (red team, línea 79).
- **Arreglo:** en `_problema`, rechazar cualquier texto que cumpla
  `any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in v)`, aplicado a todos los campos de texto,
  incluida la clave. Para `clave` conviene además una lista blanca al estilo del red team, `[A-Za-z0-9_.:-]`. En
  `_anomalia`, guardar `repr(clave)[:300]` o escapar el valor antes de insertarlo, para que la ruta de rechazo no pueda
  fallar.

### 2. ALTA: si la identidad no coincide, los contadores se aplican igual, y la clave no exige el prefijo del agente
- **Dónde:** `infobar/almacen.py:298-313`. Cuando cambian `hora_utc`, `agente` o `modelo` se anota la anomalía, pero
  después se aplica igualmente `max()`. En la inserción (`:290-297`) no se comprueba que `clave` empiece por
  `agente + ":"`.
- **Contraejemplo:** se ingiere `claude:a` y después llega `{"clave": "claude:a", "agente": "codex", "modelo": "otro",
  "gi": 10**12}`. El resultado es `subidos=1` y la fila queda como `('claude', 'm', 1000000000000)`. También se aceptan
  como nuevos `clave="codex:x"` con `agente="claude"` y `clave="sin-prefijo"`.
- **Impacto:** una línea inyectada en el log de un agente puede inflar de forma permanente un turno de otro agente. El
  `LIMITE_CONTADOR` de 10^12 (`:23`) es varios órdenes de magnitud más laxo que el «millones» que cita el propio
  comentario.
- **Decisión del dueño:** no se propone revertir la regla «el máximo gana» (decisión 1 de la fase 1). Se proponen tres
  ajustes compatibles con ella:
  1. Si la identidad no coincide, se registra la anomalía y **no** se aplican contadores (`continue`).
  2. Se exige `clave.startswith(agente + ":")`.
  3. Se baja el límite a algo del orden de 10^9 o 10^10 por contador.

  Lo decide el dueño.

### 3. MEDIA (latente, multiespacio): la clave global y el `espacio_id` por parámetro descuadran `horas` entre espacios
- **Dónde:** la PK de `turnos` es solo `clave` (`:52`). La búsqueda de `previo` no filtra por espacio ni lo lee
  (`:288`). `_suma_hora` usa el `espacio_id` **del parámetro**, no el de la fila (`:312`).
- **Contraejemplo:** se ingiere `claude:a` en el espacio 1 y luego `gi=50` en el espacio 2. Resultado:
  `turnos=[(1, 50, 1)]` y `horas=[(1, 10, 1), (2, 40, 0)]`. El espacio 2 suma 40 tokens de un turno que no tiene, y el
  espacio 1 descuadra.
- **Impacto:** hoy solo hay un espacio, pero el plan prepara `espacio_id` para la aplicación multiinquilino. Tal como
  está, un inquilino puede alterar las cifras de otro.
- **Arreglo:** usar la PK `(espacio_id, clave)`, o filtrar `WHERE clave=? AND espacio_id=?` y anotar una anomalía si
  existe en otro espacio. En cualquier caso, usar `previo["espacio_id"]` en `_suma_hora`.

### 4. MEDIA: `ON DELETE CASCADE` desde `fuentes` borra turnos de otra fuente y deja `horas` descuadrado
- **Dónde:** `turnos.fuente_id ... ON DELETE CASCADE` (`:54`). El `UPDATE` reasigna `fuente_id` a la última fuente que
  sube el turno (`:310`).
- **Contraejemplo:** la fuente s1 crea `claude:k` y la fuente s2 lo sube. Al hacer `DELETE FROM fuentes WHERE id=s2`
  queda `turnos=0` y `horas.turnos=1`.
- **Impacto:** contradice «nunca se borra por fuente» (plan, línea 81; docstring en `:6`), y el agregado deja de cuadrar
  sin dejar rastro. Hoy nadie borra fuentes, pero la purga de la fase 3 o una limpieza futura lo haría.
- **Arreglo:** usar `ON DELETE RESTRICT` (o no declarar acción) en `turnos.fuente_id` y `anomalias`. Valorar también si
  conviene no reasignar `fuente_id` en el `UPDATE`, es decir, conservar la fuente que creó el turno.

### 5. MEDIA: la URI de solo lectura no se escapa, y con `#` o `?` en la ruta se pierde `mode=ro` y se abre otra base
- **Dónde:** `infobar/almacen.py:111`, con `f"file:{ruta}?mode=ro"`.
- **Contraejemplo con `#`:** con `.../a#b/consumo.sqlite`, SQLite trata `#b/...` como fragmento. Abre **otra** base vacía
  en `.../a` y la crea (0 objetos, el fichero espurio existe). El lector ve una base vacía en lugar de un error claro.
- **Contraejemplo con `?`:** con `.../x?mode=rwc&y/...`, la ruta reinyecta `mode=rwc` y crea el fichero `.../x`. Solo
  `PRAGMA query_only=ON` impide la escritura.
- **Arreglo:** construir la URI con `pathlib.Path(ruta).as_uri() + "?mode=ro"`, que escapa `#`, `?` y `%`. En Windows
  esto también resuelve la forma `file:///C:/...`; **no verificado en Windows**.

### 6. MEDIA: carrera en la primera creación y migración frágil
- **Dónde:** `_migrar` lee `user_version` (`:149`) y las tablas (`:157`) **antes** de `BEGIN IMMEDIATE` (`:160`).
- **Contraejemplo:** se simula que otro proceso migra entre la lectura y el cerrojo. El segundo `abrir()` falla con
  `OperationalError: table meta already exists`, que es un error sin tipo `ErrorAlmacen`. Ocurre en la práctica si la
  barra y el CLI arrancan a la vez con la base todavía inexistente.
- **Arreglo:** tras `BEGIN IMMEDIATE`, volver a leer `user_version` y, si ya es `VERSION_ESQUEMA`, hacer `COMMIT` y
  salir.
- **Otros puntos menores del mismo bloque:**
  - `ESQUEMA_V1.split(";")` (`:162`) se rompe en cuanto un CHECK o un DEFAULT lleve `;`.
  - La copia previa a la migración se hace fuera del cerrojo.
  - `meta.version_esquema` y `user_version` pueden divergir, y solo se consulta el segundo.
  - Una conexión de solo lectura no comprueba la versión: abre sin queja una base con `user_version=99`.

### 7. MEDIA: el escritor único depende de que `quien` sea único, deja escribir fuera del arrendamiento y el reloj puede bloquear el relevo
- **Bien resuelto:** la comprobación del escritor se hace dentro de la misma transacción `IMMEDIATE` que escribe, así que
  no hay ventana entre comprobar y hacer `COMMIT`. Con dos conexiones, cuando el CLI toma el relevo la barra recibe
  `SinEscritura`.
- **`quien` es un nombre compartido:** `"barra"` o `"cli"` (`:188`). Dos procesos con el mismo nombre son escritores a
  la vez; el contraejemplo devuelve `True`. Los datos siguen siendo correctos gracias a `IMMEDIATE` y a `max()`, pero se
  incumple la decisión 6. **Arreglo:** usar `quien = f"{rol}:{os.getpid()}:{uuid4().hex[:8]}"`.
- **Escritura sin arrendamiento:** `espacio()` y `fuente()` (`:214-228`) escriben sin exigirlo. Una segunda conexión
  que no es escritora crea fuentes (el contraejemplo pasa de 1 a 2 fuentes).
- **Reloj que retrocede:** si el latido quedó en el futuro (`t+3600`), `ahora - latido` sale negativo y el CLI no puede
  tomar el relevo hasta `t+3690`. **Arreglo:** tratar `latido > ahora + ttl` como caducado.
- **Nota para 1.2:** tras un reposo de más de 90 s, el propio dueño recibe `SinEscritura` y tiene que volver a llamar a
  `adquirir_escritura`. Es el diseño previsto, pero el bucle de ingesta debe contemplarlo.
- **Nota sobre `ahora`:** que `ahora` se pueda pasar como parámetro no es una frontera de seguridad. Cualquier proceso
  con acceso de escritura puede modificar `meta` directamente.

### 8. MEDIA: las anomalías no se deduplican y los cambios de `proveedor` o `proyecto_raw` se pierden en silencio
- **Anomalías:** releer el mismo turno malo cinco veces deja **5** filas en `anomalias` (`:255-259`). Cada relectura
  completa (arranque en frío o cambio de inodo) añade hasta 50 filas por lote, sin límite de crecimiento ni retención.
  **Arreglo:** usar `UNIQUE(tipo, clave, detalle)` con `INSERT OR IGNORE`, o un contador por `(tipo, clave)`.
- **Cambios silenciosos:** cuando cambian `proveedor` o `proyecto_raw` (`:298` solo mira hora, agente y modelo) ni se
  aplica el cambio ni se anota nada. Esto choca con la decisión 8: el recorte de `Users/<x>` llegará en 1.2 y los turnos
  ya guardados conservarán para siempre el `proyecto_raw` antiguo, de modo que `horas` quedará partido en dos proyectos
  sin ningún aviso. Hace falta una migración explícita o, como mínimo, anotar la anomalía.

### 9. BAJA: en las rutas de error, `ROLLBACK` tapa el error original o deja la transacción abierta
- **`ROLLBACK` sobre un rollback ya hecho:** si SQLite ya ha deshecho la transacción por su cuenta (por ejemplo, con el
  disco lleno), el `ROLLBACK` explícito de `:171`, `:196` y `:321` lanza `cannot rollback - no transaction is active`.
  Ese error sustituye al original, que en el contraejemplo era `database or disk is full`. **Arreglo:** usar
  `if con.in_transaction: con.execute("ROLLBACK")`.
- **`soltar_escritura` sin protección:** no tiene `try` (`:200-204`). Un fallo dentro deja la transacción abierta
  (`in_transaction=True`), y el siguiente `BEGIN` falla.
- **`BaseException`:** el `except Exception` no cubre `KeyboardInterrupt`, que también deja la transacción abierta.

### 10. BAJA: falsos negativos y positivos en las rutas no fiables, y requisito del plan sin hacer
- **Dónde:** `infobar/plataforma.py:13` y `:31-37`.
- **Falsos negativos:** `~/Library/CloudStorage/Box-Box` (Box actual), SMB montado en `/Volumes/<share>` y la unidad
  mapeada `Z:/` devuelven `""`.
- **Falsos positivos por subcadena:** `.../dropbox-clone/...` y un usuario `onedriver` se rechazan.
- **Arreglo:** rechazar `/library/cloudstorage/` entero y comparar por componentes de ruta, no por subcadena. Para red,
  usar `os.statvfs` o `mount` en macOS y `GetDriveTypeW` en Windows. Esto último no se ha verificado.
- **Requisito del plan sin hacer:** el aviso «si el volumen ignora la propiedad de ficheros» (plan, líneas 98-99) no
  está en `abrir()`.
- **`es_checkout_de_desarrollo`:** depende de que la instalación se llame exactamente `.cost-guard` y lee
  `COSTGUARD_DATOS` al llamarse, mientras que `rutas.DATOS` se fija al importar (`infobar/rutas.py:23`). Si la variable
  se define después de importar, la comprobación se da por buena pero la escritura va a los datos reales.

### 11. BAJA: permisos y rotación de las copias
- **Permisos:** los puntos de entrada (`costbar.py:38`, `barra.py:444` y `panel.py:89`) fijan `umask 077`, y por eso
  esto es baja. Fuera de ellos, en tests o en uso como biblioteca:
  - El `.tmp` de la copia nace con 0644 durante todo el backup.
  - Un directorio `copias/` que ya existía en 0755 nunca se endurece (`:347`).
  - Una base existente en 0644 no se corrige (`:118`).
- **Rotación (`:357-359`):** cuenta cualquier fichero con el prefijo. Una copia manual
  `consumo.sqlite.copia.manual-no-borrar` desplazó una copia real (quedaron 4 en vez de 5).
- **Nombre con hora local (`:348`):** el 25-10-2026 se repite la hora, y una copia más nueva se ordena como más antigua.
  Además, dos copias en el mismo segundo se sobrescriben. **Arreglo:** usar `time.gmtime()` y añadir un sufijo único.
- **Otros:** los `.tmp` huérfanos no se limpian, y falta el reintento de `os.replace` en Windows (plan, líneas 120-121).

### 12. BAJA: tests que no prueban lo que dicen
- `test_solo_lectura_no_puede_escribir` y `test_el_lector_en_otra_conexion...` pasarían aunque se perdiera `mode=ro`,
  porque `query_only=ON` también bloquea la escritura. Falta comprobar que la conexión es realmente de solo lectura (con
  `#` o `?` en la ruta, o leyendo el modo).
- `test_el_agregado_horario_siempre_cuadra_con_los_turnos` solo compara sumas globales. No habría detectado el hallazgo 3.
  Conviene comparar agrupando por `(espacio, hora, agente, proveedor, modelo, proyecto_raw)`.
- Todo `EscritorUnicoTest` usa **una sola conexión** (`self.con`) para la barra y para el CLI. No se prueba el relevo
  entre conexiones ni dos procesos con el mismo `quien`.
- `test_permisos_privados` no cubre `-wal`, `-shm`, una base existente ni la copia previa a la migración.
- `test_cifras_fuera_de_rango_se_descartan` no incluye NUL, *surrogates* ni caracteres de control.
- El 10^12 por contador permite, en teoría, desbordar `SUM` o `gi+excluded.gi` en `horas` a partir de unos 9,2 M turnos
  hostiles en un mismo cubo. No es realista hoy; el hallazgo 2 lo cubre al bajar el límite.

## Bien resuelto
- **Monotonía:** repeticiones de la misma clave en el mismo lote, delta cero, primer turno de una hora y un lote mixto
  cuadran **clave a clave** entre `horas` y `turnos`. `horas` usa siempre la identidad guardada, nunca la entrante.
- **Transacciones:** atomicidad del lote con un único `BEGIN IMMEDIATE`, y sin transacciones anidadas.
- **Enteros:** se validan con `_entero`, que excluye `bool`, y se acotan antes de llegar a SQLite. No hay desbordamiento
  de 2^63 por turno.
- **`totales()`:** la lista de agrupaciones es cerrada y todo lo demás va parametrizado. Los nombres hostiles se guardan
  tal cual.
- **WAL y PRAGMA:** WAL se verifica, `busy_timeout`, `foreign_keys` y `secure_delete` se fijan en cada conexión, y
  `journal_size_limit` va acompañado de checkpoint `TRUNCATE`.
- **Copias:** se hacen con la API de backup, coherente con WAL.
- **Aislamiento:** `.gitignore` y `tests/aislamiento.py` redirigen `rutas.ALMACEN` y `rutas.COPIAS`.

## Acciones recomendadas (orden)
1. Validar NUL, *surrogates* y caracteres de control en `_problema` y escapar en `_anomalia` (hallazgo 1).
2. Hacer que una identidad que no coincide no aplique contadores, exigir el prefijo de agente en la clave y bajar el
   límite (hallazgo 2, decisión del dueño).
3. Incluir el espacio en la clave o en la búsqueda, y usar `previo["espacio_id"]` en `_suma_hora` (hallazgo 3). Cambiar
   el CASCADE de `fuentes` a RESTRICT (hallazgo 4).
4. Construir la URI con `Path.as_uri()` (hallazgo 5) y volver a leer `user_version` dentro del cerrojo (hallazgo 6).
5. Hacer `quien` único por proceso, exigir el arrendamiento en `fuente()` y `espacio()`, tratar el latido en el futuro
   como caducado y usar `ROLLBACK` condicional (hallazgos 7 y 9).
6. Deduplicar las anomalías (hallazgo 8) y completar los tests del hallazgo 12.

## Preguntas abiertas
- ¿Una identidad que no coincide debe congelar los contadores o solo dejar la anomalía? Es una decisión del dueño, que
  matiza la decisión 1.
- La lista blanca de `clave` y el tope de hora `ahora+5 min` del red team, ¿van en 1.1 o en 1.2? La cota que depende del
  `mtime` del fichero solo puede ir en el lector.
- La forma de la URI y la detección de red en Windows no se han verificado en ese sistema.

Contraejemplos: `poc_almacen.py`, `poc_copia.py` y `poc2.py`, en el scratchpad de la sesión. Usan bases temporales.
