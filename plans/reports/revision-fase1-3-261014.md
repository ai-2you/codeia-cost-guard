# Revisión de la fase 1.3 (consulta, ingestor, sombra, interruptor)

Fecha: 2026-10-14 (Europe/Madrid). Rama: `feat/fase1-ingesta-hermes`. Alcance: `git diff 94c252e..HEAD`. Se ha contrastado con
el «1.3 — diseño v2» de `plans/261013-2350-sqlite-historico/plan.md` (líneas 139-150) y con el pre-mortem
`plans/reports/revision-diseno-1-3-261014.md`. Solo lectura, salvo este informe. Las pruebas se han hecho en una copia del
repositorio en el scratchpad (Python 3.14.6). La suite completa pasa: 286 tests OK.

## Resultado

La lectura (`consulta.py`), el punto único `informe()` y el escritor único están bien encaminados: solo lectura con `mode=ro`
y `query_only`, una conexión por llamada, ninguna importación de la ingesta (ni directa ni transitiva) en los lectores, y
`construccion_completa` como requisito para leer. Hay dos defectos que conviene corregir antes de dar por buena la sombra.
El primero: la sombra etiqueta una pérdida de datos de la base con la misma clase que los forks, así que puede dar falsa
seguridad. El segundo: una línea sin terminar de más de 8 MiB al final de un fichero deja la ingesta girando para siempre, y
`activo` no llega a entrar nunca. Además, ocho mutaciones sobreviven a la suite, entre ellas la del arreglo central de B2
(readquirir el puesto de escritor en cada vuelta).

---

## ALTO

### A1. La sombra confunde una pérdida de la base con las divergencias firmadas (falsa seguridad)
- **Dónde.** `infobar/sombra.py:72-91`, función `comparar()`.
  - `:90`: toda diferencia negativa sale como `el_antiguo_cuenta_de_mas`.
  - `:85-86`: con el mismo total, se clasifica como `atribucion_de_modelo`.
  - `:87-88`: `cruce_de_dia`.
- **Verificado** con `comparar()` en la copia:
  - **Codex 100 → 1 en la base** da `el_antiguo_cuenta_de_mas`. Es la misma etiqueta que el plan explica como «forks y
    re-emisiones» (líneas 152 y 6 de la docstring). Una pérdida real en la ingesta, o una base que va por detrás, se lee
    como una divergencia aceptada. No existe la clase `residuo`.
  - **Trasvase de `gi` a `cr` con el mismo total** (`[100,0,0,0]` frente a `[0,0,100,0]`) da `atribucion_de_modelo`. La
    comparación usa la suma escalar (`:81`) y no el vector. En Codex, entrada y caché tienen precios muy distintos.
  - **Pareja de cruce de día** (10/+6, 11/−6): solo se etiqueta el primer día. El segundo sale como
    `la_base_cuenta_de_mas`, que es una falsa alarma. Además exige que las diferencias se anulen exactamente.
- **Incumple el diseño v2 §7:**
  - Las categorías tenían que **calcularse** a partir de los turnos: forks = claves vistas en más de una fuente; cruce =
    turnos a menos de 1 h del límite. Lo que no entrase en ninguna categoría era residuo.
  - Faltan la dimensión `proveedor`, el registro de `cinco_h` y `ritmo` en el mismo instante, y el «no baja» de Hermes, que
    hoy se descarta sin más (`:74`).
  - La docstring dice que el fichero guarda el modelo, pero las filas no lo llevan.
- **Escenario.** Un fallo de 1.4 o 1.5 hace que la base deje de ingerir un 5 % de los turnos de Codex. Durante siete días la
  sombra registra «el antiguo cuenta de más», el dueño lo da por re-emisiones y cierra la sombra. `activo` muestra un 5 % de
  menos para siempre.
- **Arreglo.**
  1. Comparar vectores de 4 contadores.
  2. `atribucion_de_modelo` solo si la suma por agente y día coincide **contador a contador** y difiere solo la partición
     por modelo.
  3. Calcular los forks a partir de las claves. La base no basta: `turnos.fuente_id` solo guarda la última fuente de cada
     turno. Hace falta que el lado antiguo diga cuántos turnos vio duplicados entre ficheros, o contar las claves
     `message.id` repetidas en una pasada de solo lectura. Calcular también los turnos a menos de 1 h de medianoche, y dejar
     como `residuo` lo que no quede explicado.
  4. Que el cierre de la sombra exija `residuo == 0` en Codex, pi y OpenCode.
  5. Como mínimo inmediato, renombrar las clases a `base_por_debajo` y `base_por_encima`, para que no sugieran una causa.

### A2. Una línea de 8 MiB o más sin terminar al final de un fichero deja la ingesta girando para siempre
- **Dónde.** `infobar/ingesta.py:118-125` y `:141`. En la rama de «línea enorme sin final», `avance` vuelve a 0 y
  `completo = nuevo >= st.st_size or len(trozo) < max_bytes` da False. Por eso `ingestor.py:97-100` deja `pendiente`, y
  `HiloIngesta` (`ingestor.py:158`) repite la vuelta cada 50 ms.
- **Verificado** (demo en la copia): un fichero con una línea válida seguida de 9 MiB sin `\n`.
  - Tres vueltas seguidas devuelven `ficheros=1, completo=False`.
  - `construccion_completa` sigue en `None`.
- **Escenario.** Claude o pi mueren mientras escriben una línea con una imagen grande en base64. El fichero queda así para
  siempre. Las consecuencias:
  - la barra lee más de 9 MiB y hace 3 transacciones de escritura unas 20 veces por segundo (`adquirir_escritura`,
    `fuente()` y `guardar_lectura`);
  - el WAL y el SSD trabajan sin pausa;
  - `activo` cae siempre a `escanear()`;
  - `costbar.py --ingerir --una` no termina.
- **Arreglo.** Definir `completo` como «se ha leído hasta el EOF observado», y no como «se ha avanzado hasta el EOF».
  - En la rama enorme, cuando `bloque` llega vacío, devolver `completo=True`.
  - En general: `completo = desde + bytes_leídos >= st.st_size`.
  - Test: el mismo fichero de 9 MiB, comprobando que la segunda vuelta devuelve `completo=True` y `ficheros=0`.

---

## MEDIO

### M1. Tests que no prueban lo que dicen (mutaciones en la copia)

| Mutación | Fichero:línea | Resultado |
|---|---|---|
| Readquirir el escritor solo en la primera vuelta | `ingestor.py:63` | **sobrevive**: el arreglo central de B2 no tiene test |
| `except SinEscritura: continue` en lugar de `raise` | `ingestor.py:91-92` | **sobrevive** |
| Ventana de `consultar()` de 2 días en vez de `dias+1` | `consulta.py:85` | **sobrevive** |
| Sin recargo `CLIFF` en las horas | `consulta.py:105` | **sobrevive** |
| `_mantenimiento()` eliminado | `ingestor.py:128` | **sobrevive** |
| Llamada a la sombra quitada del trabajador | `trabajador_barra.py:51-52` | **sobrevive** |
| `sombra.py` importa `ingestor` | `sombra.py:14` | **sobrevive**: `TestSoloLectura` (`test_ingestor.py:103-119`) no incluye `sombra.py` ni `costbar.py` y no es transitivo |
| Escritura por otra conexión rw dentro de `consulta._abrir` | `consulta.py:62` | **sobrevive**: `test_lectura_no_toca_el_fichero` (`test_consulta.py:106-115`) solo calcula la huella del fichero principal, y con WAL la escritura va al `-wal` |
| Quitar `completo` por línea a medias, versión, alias antiguo, `construccion_completa`, `al_dia`, `cruce_de_dia`, `RECIENTE_S`, exclusión de Hermes | varias | detectadas (el test falla) |

Faltan además los dos tests que pide el diseño (§5 y §9):
- el autorizador de SQLite en los tests de `consulta.py` (no hay `set_authorizer` en `tests/`);
- el subproceso de `panel.py` con el directorio de datos bloqueado.

No hay ningún test de `HiloIngesta` ni de `costbar.py --ingerir`.

**Arreglo.**
- Un test que avance el reloj más allá de `LATIDO_TTL_S` entre dos vueltas sin trabajo y compruebe que otro proceso no puede
  tomar el puesto.
- Un test de `SinEscritura` a mitad de vuelta: que se propague y que no deje `al_dia` marcado.
- La huella de `test_lectura_no_toca_el_fichero` debe incluir `-wal`, o mejor comparar `PRAGMA data_version` y el recuento
  de filas desde otra conexión.
- El test de imports debe ser transitivo: en un subproceso, `import panel, popover, analizar_gasto, infobar.servidor_local`
  y comprobar que `infobar.ingest*` no aparece en `sys.modules`. Lo he verificado a mano y hoy está limpio.

### M2. `dias_cerrados()` mete el día abierto o se salta uno justo en los cambios de hora
- **Dónde.** `infobar/sombra.py:67-69`: `ahora - n * 86400`.
- **Verificado** con `TZ=Europe/Madrid`:
  - el 25-10-2026 a las 23:30, el día más reciente que se compara es **el 25, que aún está abierto** (falsa alarma por
    retraso de ingesta);
  - el 30-03-2026 a las 00:30, **falta el 29-03**, el día de 23 h;
  - el 01-11 a las 23:30 salen 26 días en vez de 27.
- Son justo los días que el plan exige comparar (25 y 26 de octubre).
- **Arreglo.** Aritmética de fechas sobre el día local: `hoy = date.fromtimestamp(ahora)` y
  `{(hoy - timedelta(days=n)).isoformat() for n in 1..27}`, con la zona si se pasa.

### M3. Una fuente que lanza una excepción no deja trabajo pendiente: «construida» con datos que faltan, y log cada 30 s
- **Dónde.** `infobar/ingestor.py:93-95` (jsonl) y `:115-117` (OpenCode y Hermes): `continue` sin `pendiente = True`.
  Después, `:121-124` escribe `construccion_completa`.
- **Escenario.** La `state.db` raíz de Hermes da `DatabaseError` (bloqueada o dañada).
  - La primera vuelta completa marca la base como construida sin Hermes.
  - `activo` pasa a leer de la base, aunque la ingesta de esa fuente nunca ha terminado.
  - `costbar.log` recibe una línea cada 30 s mientras dure el fallo.
- **No verificado** con una base real dañada. Es una deducción directa del código.
- **Arreglo.**
  - Llevar un indicador `fallos` por vuelta.
  - No escribir `construccion_completa` mientras haya fallos.
  - No acelerar la vuelta por ello (seguir con `PAUSA_AL_DIA_S`).
  - Registrar el error una vez por fuente y tipo.

### M4. Al borrar o restaurar la base con la barra viva, el ingestor sigue escribiendo en un inodo borrado
- **Dónde.** `infobar/ingestor.py:44-47`: la conexión se abre una vez y no se vuelve a abrir. `al_dia` (`:40`) tampoco se
  vacía.
- **Verificado:** tras borrar la base y sus `-wal`/`-shm`, la vuelta siguiente devuelve `escritor=True, completo=True` y el
  fichero no existe.
- **Escenario.** El propio `almacen._migrar` (`almacen.py:184`) aconseja «bórrala y se recreará». Lo mismo pasa al restaurar
  una copia. La barra escribe en un fantasma, los lectores caen a los logs hasta reiniciar la barra, y al reabrir `al_dia`
  saltaría todos los ficheros sin cambios: la base nueva quedaría a medias.
- **Arreglo.**
  - Al empezar cada vuelta, comparar `os.stat(ruta).st_ino` y `st_dev` con los de la apertura.
  - Si cambian o el fichero no existe, cerrar, reabrir, poner `al_dia = {}` y `espacio = None`.

### M5. El diseño §5 pedía cargar `lecturas ⋈ fuentes` y actualizar `visto_en` en lote; el código solo usa memoria
- **Dónde.** `infobar/ingestor.py:40` y `:82`. Con `al_dia` vacío al arrancar, cada arranque de la barra pasa los ~1.400
  ficheros por `ingerir_fichero`. Son 2 transacciones por fichero (`fuente()` y `guardar_lectura`) aunque nada haya
  cambiado, que es el A3 del pre-mortem.
- `visto_en` ya solo se actualiza al cambiar un fichero. Por eso `bases` en `consulta.py:108-109`, que cuenta todas las
  `fuentes` sin filtrar por recientes, crece con logs ya borrados («Claude Code (N)» frente a los ficheros vivos de
  `escanear()`).
- `r["fuentes"]` (`consulta.py:124`) es el número de agentes, mientras que en `escanear()` es el número de ficheros y bases;
  `panel.py:63` lo muestra.
- **Arreglo.**
  - Al abrir, sembrar `al_dia` desde `lecturas` (inodo, tamaño, `mtime_ns`) cuando el desplazamiento guardado sea igual al
    tamaño.
  - Un `UPDATE fuentes SET visto_en` en lote al final de la vuelta.
  - `bases` filtrado por `visto_en` reciente.
  - `fuentes` con el mismo significado que en `escanear()`.

### M6. Un lector `mode=ro` sin escritor abierto crea `-wal` y `-shm`, y con el directorio bloqueado falla
- **Verificado:**
  - con la base cerrada por el escritor, la carpeta solo tiene `c.sqlite`;
  - `consultar()` con la carpeta en `0500` lanza `SinAlmacen (OperationalError)` y cae a los logs;
  - con la carpeta escribible, `consultar()` funciona y crea `c.sqlite-wal` y `c.sqlite-shm`, los dos con `0600` (SQLite
    copia los permisos de la base, así que no hay fuga).
- **Impacto.**
  - No escribe datos.
  - El test de subproceso con `chmod 500` que propone el diseño fallaría por el motivo equivocado si no hay una conexión
    escritora abierta. Hay que montarlo con el escritor vivo.
  - `panel.py` lanzado sin barra crea esos dos ficheros. Es inocuo, pero conviene saberlo.

---

## BAJO

- **CLI.** `costbar.py:37-55` no captura `SinEscritura` ni `ErrorAlmacen`. Sin `--una`, el bucle de servicio (Windows,
  cron) muere con una traza la primera vez que otro proceso le quita el puesto. Debería registrarlo y seguir esperando.
- **Hilo daemon.** `ingestor.py:148`: al salir la app el hilo muere sin `soltar_escritura`. Si la barra se reinicia en menos
  de 90 s, no ingiere durante ese tiempo. Es aceptable, pero merece una línea en `applicationWillTerminate`.
- **`desactivado` solo se lee al arrancar** (`barra.py:150`). Cambiarlo en Ajustes no para la ingesta hasta reiniciar, y la
  interfaz no lo dice.
- **Ventana de `consultar()` en el día de 25 h** (`consulta.py:85`): `ahora - (dias+1)*86400` puede dejar fuera la primera
  hora del día más antiguo cuando ese día es el 25-10 y se consulta en su última hora. Usar `dias+2` (el filtro por día de
  `agregar()` recorta igual). No verificado con datos; es aritmético.
- **Log en cada `GET /` en `activo` con la base sin construir.** `informe()` avisa «una vez por proceso» (`agregacion.py:101`),
  pero `panel.py` es un proceso nuevo en cada `GET /` (`servidor_local.py:217`), así que se escribe una línea por petición.
- **Hermes: `plan_de` no es byte a byte cuando hay URL.** `_url_publica` (`ingesta_hermes.py:43-45`) pone el host en
  minúsculas y quita la `/` final. El lector antiguo usaba `burl` tal cual (`lectores_logs.py:398`). En la configuración
  instalada no afecta: 3 alias con `://`, ninguno con `/` final ni mayúsculas (contado sin imprimir valores). Queda latente
  para otras instalaciones.
- **Codex con un `model_provider` distinto de `openai`** sale con plan = proveedor (`consulta.py:29`). El lector antiguo
  ponía siempre «OpenAI (Codex)» (`lectores_logs.py:155-156`). El diseño lo acepta como mejora a firmar, pero no está en la
  lista de divergencias firmadas.
- **Contador de anomalías en el informe** (diseño §4): no está hecho y tampoco figura en «Pendiente de 1.3».

## Comprobado y descartado

- **Imports transitivos.** Importar `costbar`, `infobar.servidor_local`, `infobar.trabajador_barra`, `infobar.sombra`,
  `infobar.consulta` o `infobar.agregacion` en un proceso limpio no carga ningún `infobar.ingest*`. El ingestor solo se
  importa de forma diferida en `barra.py:151` y `costbar.py:40`.
- **Escritor único.**
  - La conexión del ingestor se crea y se cierra en el mismo hilo (`HiloIngesta.run` → `vuelta` → `cierra` en el `finally`).
  - `consulta` abre una conexión por llamada.
  - El latido se refresca al empezar cada vuelta, como mucho cada 30 s más la duración de la vuelta (unos 5 s más un
    fichero), lejos de los 90 s del TTL.
  - Un `SinEscritura` a mitad de vuelta deja el fichero en curso sin `guardar_lectura`. La siguiente pasada relee desde la
    marca anterior y el upsert por máximo lo hace idempotente.
- **Ficheros `.json` que `escanear()` recorre y el ingestor no.** Hay 1.286 en `~/.claude/projects` y 0 en Codex, ninguno con
  `input_tokens`: solo cambian el contador «Claude Code (N)», no las cifras.
- **`agregar()` con claves de `horas` en epoch** y la ventana de `RECIENTE_S = 6 h`: no pierden turnos de `cinco_h` ni de
  `ritmo`, porque un cubo con edad menor de 5 h no puede contener turnos de hace más de 6 h.
- **`sombra.jsonl`.** Se crea con `0600` y `O_APPEND`, con rotación a `.1` (que conserva el modo). Solo lleva agente, día,
  totales y clase: ni proyectos, ni URLs, ni modelos.
- **`proveedores_de()`** recibe en `escanear()` los modelos de todo el histórico y en `consultar()` los de unos 32 días. Solo
  añade entradas automáticas con límites a 0 para familias no declaradas. Por lectura del código no altera las cifras dentro
  de la ventana. **No verificado** con una configuración real con modelos antiguos de familias que ya no se usan.

## Acciones recomendadas, por orden

1. A2: `completo` en la rama de línea enorme, con su test de 9 MiB. Es corto y evita el giro infinito.
2. A1: clases neutras, comparación por vector de contadores y residuo calculado. La sombra no debería cerrarse sin esto.
3. M2: `dias_cerrados` con aritmética de fechas, antes del 25-10.
4. M1: tests del latido por vuelta, de `SinEscritura`, transitivo de imports y huella con `-wal`.
5. M3 y M4: fallos por fuente y reapertura si cambia el inodo de la base.
6. M5 y los puntos bajos, cuando toque.

## Preguntas abiertas

- ¿Se acepta firmar como divergencia que Codex con otro `model_provider` cambie de plan, o tiene que reproducir siempre
  «OpenAI (Codex)», como hacía el lector antiguo?
- Para calcular los forks del lado antiguo hace falta saber qué claves vio `parsear()` en más de un fichero. ¿Se acepta
  instrumentar `parsear()` (solo contar), o se prefiere una pasada de solo lectura aparte dentro de la sombra?

Status: DONE_WITH_CONCERNS
Summary: La lectura desde la base y el escritor único cumplen el diseño v2: no hay escrituras ni imports de la ingesta en los
lectores, y `construccion_completa` y la versión se respetan. Pero la sombra puede disfrazar una pérdida de datos como «el
antiguo cuenta de más», y una línea sin terminar de más de 8 MiB deja la ingesta girando para siempre. Además, ocho
mutaciones sobreviven, entre ellas la del latido por vuelta.
