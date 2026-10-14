# Pre-mortem y red team del plan v2 de la Fase 3 ampliada (paneles, contenedores, bases de datos, Windows)

Fecha: 2026-10-14 (Europe/Madrid) · Objeto: `plans/261013-2350-sqlite-historico/fase3-plan-monitorizacion.md` (v2), con
`fase3-diseno.md` (3.1) y su revisión `revision-diseno-fase3-261014.md`, y las secciones "Seguridad transversal", "Privacidad y
legal" y decisiones de `plan.md`. Contrastado con el código de `feat/fase1-ingesta-hermes`. Solo lectura; sin MCP de paneles; sin
hosts, IPs, nombres de clientes ni claves. Marcas: **[V]** verificado en código (`fichero:línea`) o en documentación primaria;
**[S]** supuesto o fuente secundaria no comprobada aquí. Lo que ya dijo la revisión de 3.1 no se repite: solo se comprueba si v2 lo
incorpora (sección "Coherencia con la revisión de 3.1").

## TL;DR

El plan v2 acierta en el modelo (recurso tipado + colectores con contrato cerrado + inventario como historial + lista blanca de
`GET`) y en que Windows va primero. Lo que lo hundiría a los seis meses no está en el modelo sino en cuatro supuestos que el código
desmiente:

1. **La clave del panel es omnipotente y la lee cualquier proceso del usuario sin diálogo** (`claves.py:26`, reconocido en
   `plan.md:217-218`). En Dokploy gratuito no hay rol de solo lectura (roles fijos Owner/Admin/Member; roles a medida son
   Enterprise [S, docs oficiales]). La lista blanca de rutas protege contra *nuestros* bugs, no contra un `npm install` malicioso
   en el Mac que lea el Llavero y haga `DELETE` en servidores de clientes. Esto es una decisión del dueño, no un detalle técnico.
2. **En Windows no hay quien ejecute el ciclo**: el ciclo vive en el hilo de la barra `rumps` (`trabajador_barra.py:69`), que solo
   existe en macOS (`costbar.py:95-100`). "Portar `fcntl` y `os.killpg`" (3.0) no basta: hace falta un *runner* (servicio, bandeja
   o `--daemon`) o la frase "funciona en Windows" es falsa. Además `import fcntl` a nivel de módulo (`config_y_estado.py:8`) impide
   importar **todo el paquete** en Windows, no solo el ciclo.
3. **La infraestructura SQLite "reutilizable" no lo es tal cual**: `abrir()` siempre llama a `_migrar()` con el esquema de
   consumo (`almacen.py:167,214-241`), así que `abrir(ruta="servidores.sqlite")` crearía las tablas de *consumo* dentro de la base
   de servidores; y `copia()` nombra `consumo.sqlite.<prefijo>.<fecha>` y rota por prefijo (`:620,636-639`): las copias
   premigración de las dos bases se pisarían. Hay que extraer un núcleo (`PRAGMA`, ruta, WAL, copia) parametrizado por esquema.
4. **"Sin copia" a partir de `overview.backups` (≈20 trabajos globales) no es una medida, es un muestreo**: una base con copia
   semanal desaparece de la lista en dos días y dispararía la alarma; una con la programación desactivada también. La honestidad
   exige convertir el muestreo en *nuestro* historial monótono ("última copia buena observada por `serviceOwnerId`"), con carencia
   desde que se empieza a observar y silencio por recurso.

Orden recomendado: **3.0 → 3.1 (ya con el esquema genérico de recursos) → 3.3a (docker_ssh, sin `recoger-bd`) → 3.2 (panel, solo
`overview.*` + `settings.health`) → 3.5 → 3.4 → 3.6**, y `recoger-bd` como **3.3b** separada y condicionada a la pregunta 3.

## Lo que está bien y no debe tocarse

- Modelo canónico de recursos con tipos, estados y etiquetas de conjunto cerrado; "nada de diccionarios libres" (§A). Es la
  única defensa real contra respuestas hostiles: mantenerla como regla de revisión de código.
- Inventario como historial (eventos de aparición/desaparición/cambio) y `ts` del Mac (§B). Correcto; solo hay que definir
  cuándo *no* se emiten eventos (ver PM4).
- Colector de panel con **cuatro o cinco `GET` por ciclo** y lista de campos permitidos que descarta `icon` (§"Consecuencia de
  diseño"). Correcto y suficiente en carga.
- `recoger-bd` en el servidor para que el Mac nunca tenga contraseñas de bases de datos (§A.4). El principio es el bueno; el
  mecanismo necesita precisión (RT7).
- Red: `https` obligatorio, host público, IP fijada tras resolver, certificado verificado por nombre, sin redirecciones
  (`servidores.py:49-66`, `servidores_externo.py:18-38`). Reutilizar `ip_publica` y `_HTTPSFijo` tal cual.
- Secretos por sistema con fallo cerrado; Time Machine real; caché e imágenes a 48 h; retención y `guardar_ips` por servidor;
  `--olvidar-servidor`; etiquetas clasificadas como `personal` y fuera de exportaciones por defecto (§D, §E).
- "Disponibilidad observada", no uptime; Mac dormido = sin datos, no caída.
- Puerta de verificación: pre-mortem + red team por fase, fixtures hostiles, borrado comprobado por bytes, mutaciones.
- Panel web por DOM sin `innerHTML` (§F); `exportacion_csv.neutralizar` ya quita controles, invisibles y bidi y antepone `'` a
  fórmulas (`exportacion_csv.py:27-34`, test `test_exportacion_csv.py:22-30`): **reutilizarla** para cualquier CSV de servidores.
- Sesión + `Host` + `Sec-Fetch-Site` + semáforo + `max_num_fields` en el servidor local (`servidor_local.py:207-221,238-242`):
  los endpoints nuevos deben pasar por `do_GET` después de `_sesion_ok` y copiar el patrón de `_historico`.

## Coherencia con la revisión de 3.1 (`revision-diseno-fase3-261014.md`)

v2 dice que 3.1 es "el diseño revisado con sus correcciones" y recoge de forma explícita: precondición Windows (D8 → 3.0), caché y
PNG a 48 h (D9), Time Machine real (D10), `id` estable (D1/D5), `guardar_ips` y retención por servidor (D4), `recoger.sh` v2 con
histograma y campos de confianza (D2/RT1), copias sin `servidores.sqlite` (D3). **Quedan implícitos y deberían listarse como
aceptados en el plan**, porque varios contradicen el texto de v2 o se perderían al implementar:

| Hallazgo previo | Estado en v2 | Riesgo si se olvida |
|---|---|---|
| PM1 dedupe por `ok_en`/`intento_en`, no `generado_en` | No se menciona | Muestras rancias como nuevas (v2 §B "una fila por ciclo con dato nuevo" lo necesita) |
| PM2 registrar fuera de `_bloqueo`, registrador inyectado, `__main__` sin escritor | No se menciona; v2 sigue diciendo "ciclo en el hilo trabajador" | Dos escritores y ciclo bloqueado por `VACUUM` |
| PM4 `caduca_en` calculado y filtro al leer | No se menciona | Retención falsa al cambiar el plazo |
| PM6 `horas_vista` en vez de `veces`; una muestra por franja | No se menciona | Cifras ×5 en incidencia |
| PM8 tope de altas/día y expulsión por relevancia | v2 dice "cardinalidad acotada (500 recursos)" sin política de expulsión | Churn borra lo relevante |
| PM9 downgrade congela purga; panel "sin datos" si no existe | No se menciona | `FileNotFoundError` en el panel (`almacen.py:145-146`) |
| PM10 checkpoint reintentado; test de bytes con lector abierto | No se menciona | WAL conserva IPs borradas |
| PM11/D7 `ips_conocidas` fuera de atacantes | No se menciona | El Mac como atacante 90 días |
| RT3 `NULL` para lo ilegible, descartar muestra entera | v2 §E dice "se descarta y se cuenta" (compatible) | — |
| RT5 `con_ips=1` tras clic con auditoría | v2 §F "solo tras un clic" ✓ | Falta la auditoría de recuento |
| D6 techo 90/180 días | v2 "90 días (decisión cerrada)", pero el diseño admite máximo 365 | Contradicción abierta |
| PM3.4 copia premigración sin IPs | No se menciona | Primera migración deja IPs en claro |

**Contradicción nueva:** `fase3-diseno.md:14-15` ponía como no objetivo "guardar nombres de contenedores"; v2 los guarda (como
`personal`). Es un cambio de producto legítimo pero debe constar como decisión del dueño con su consecuencia de privacidad (PM9).
Hoy ya se guardan en caché (`servidores_ssh.py:213,221`): lo nuevo es hacerlos **permanentes** vía `recursos`/`horas_recurso`.

## PRE-MORTEM — "se implementó tal cual y a los seis meses…"

Severidad: C crítica · A alta · M media · B baja. Cada punto: mecanismo → cambio al plan → prueba.

### PM1 (C) Windows: no hay runner y el paquete no importa
- [V] El ciclo solo lo ejecuta `Trabajador.run` (`trabajador_barra.py:69`), hilo de la app `rumps` (`costbar.py:95-107`);
  sin `rumps` el programa imprime que solo sirven `analizar_gasto.py` y `panel.py`. `import fcntl` (`config_y_estado.py:8`) es
  transitivo desde `servidor_local`, `servidores_ciclo` y casi todo. `os.killpg` (`servidores_ssh.py:68`) y `start_new_session`
  (`:85`) no existen en Windows. `purgar_render` y `endurecer` **retornan en `nt` sin hacer nada** (`permisos.py:36-37,55-56`): los
  PNG con IPs no se purgan nunca en Windows. `plataforma.avisar` devuelve `False` fuera de macOS (`:87-88`).
- Cambio: 3.0 debe incluir (a) un runner sin barra (`costbar.py --daemon` o servicio de usuario / bandeja) que ejecute el ciclo y
  el mantenimiento con el mismo latido de escritor; (b) cerrojo por sistema con **bloqueo del SO** (`msvcrt.locking` en Windows,
  `fcntl.flock` en POSIX) en lugar de "fichero con creación exclusiva y caducidad": el del SO se libera solo si el proceso muere y
  no tiene el problema de dos procesos que creen a la vez que el cerrojo caducó; (c) `CREATE_NEW_PROCESS_GROUP` + `taskkill /T /F`
  para `ssh.exe`; (d) `purgar_render` también en `nt` (sin `chmod`, solo borrar); (e) `-o ControlMaster/ControlPath` y `ProxyCommand=none`
  comprobados con el OpenSSH de Windows [S: acepta las opciones pero conviene test]; `known_hosts` en `%USERPROFILE%\.ssh`.
- Prueba: `python -c "import infobar.servidores_ciclo, infobar.servidor_local"` en Windows; `--autoprueba` que lance el ciclo
  contra un servidor simulado, mate un `ssh.exe` colgado y purgue `logs/render`.

### PM2 (C) La infraestructura SQLite no se reutiliza "parametrizando la ruta"
- [V] `abrir(ruta)` → `_migrar(con, ruta)` siempre (`almacen.py:167`), y `_migrar` aplica `ESQUEMA_V1` de consumo y comprueba
  `lecturas.contexto` (`:214-241`). `copia()` nombra `consumo.sqlite.<prefijo>` y rota por ese patrón (`:620,636-639`).
  `CLASES`/`columnas_hasta` son de consumo (`:35-54`). `mantenimiento`/`comprobar` sí son neutros (`:643-651`).
- Cambio: extraer `almacen_base.py` (ruta no fiable, PRAGMA, WAL, `_privado`, copia con nombre de la base, checkpoint) y que cada
  base aporte esquema, versión, migrador y `CLASES`. Hacerlo en 3.1, antes de escribir el esquema de servidores. Ponerlo en el plan
  como entregable con test: abrir `servidores.sqlite` no crea `turnos`; las copias premigración de las dos bases no se rotan entre sí.
- Prueba: test que abre ambas bases en el mismo directorio y comprueba `sqlite_master` y los nombres de copia.

### PM3 (A) El esquema de 3.1 queda obsoleto en 3.2 (migración inmediata)
- v2 ordena 3.1 (host + IPs) y después "generalizar" a `recursos`. Con `horas_servidor` permanente y luego `horas_recurso`,
  la segunda migración llega en semanas y arrastra la copia premigración con IPs (PM3.4 previo).
- Cambio: 3.1 crea ya el esquema genérico (`recursos` con el host como recurso tipo `host`; `observaciones`; `horas_recurso`;
  `eventos`; `ips_atacantes` aparte). Lo que se aplaza es el *colector*, no el esquema.

### PM4 (A) Falsos eventos: panel caído, reinicio, despliegue, Mac dormido
- Mecanismo: "desapareció X" se emite al comparar inventarios. Si `overview.services` falla (502 al reiniciar Dokploy, timeout,
  respuesta > tope), el inventario vacío parecería "todo desapareció". Durante un despliegue `status` pasa por `running`
  (Dokploy: `idle|running|done|error` [S]) y vuelve: dos eventos por despliegue. Tras dormir el Mac, una regla de "ausencia"
  (última observación vs ahora) dispararía para todo.
- Cambio al plan (§B/§C): (1) solo se comparan inventarios **de respuestas completas y válidas**; fallo = observación de estado
  `gris` del *panel*, sin eventos de recursos; (2) desaparición solo tras **dos ciclos consecutivos** sin el recurso (o 10 min);
  (3) cambio de estado con histéresis: `running` no genera evento, `error` sí, `done` tras `error` cierra; (4) "ausencia" se
  evalúa **solo** al recibir una colección buena, nunca con el reloj; (5) eventos idempotentes por `(recurso, tipo, franja)`.
- Prueba: fixture con secuencia [inventario, 502, inventario] → 0 eventos; [A,B] → [A] → [A] → 1 evento de B al tercer ciclo.

### PM5 (A) Identidad: ids estables pero recreación, `appName` y servidores remotos
- `id` de Dokploy sobrevive al renombrado pero **no** a borrar-y-recrear, a restaurar en otro servidor ni a clonar un proyecto:
  mismo `name`, id nuevo → "desapareció X" + "apareció X" el mismo día, y el histórico se parte. `appName` (lo que usa Docker)
  es la única clave que enlaza con `docker_ssh` (contenedores `<appName>.1.<task>` en Swarm, `<appName>-<servicio>-1` en compose
  [S]) y también cambia al recrear. Con `serverId` ≠ null el recurso corre en otro host que el del panel.
- Cambio: `recursos.clave_estable = <panel>:<tipo>:<id>`; guardar `app_name` como campo de enlace (no identidad); heurística
  explícita "recreado" (desaparición + aparición con mismo `(tipo, proyecto, name)` en el mismo ciclo → un evento `recreado` y
  enlace `sucesor_de`); `servidor_panel_id` nullable desde 3.1 (pregunta 5), sin UI hasta que exista uno; contenedores sin
  `appName` conocido = "sin panel", nunca emparejamiento difuso.
- Prueba: fixture con id nuevo y mismo nombre → 1 evento `recreado`, el histórico de horas se enlaza.

### PM6 (A) Semántica de `status` de Dokploy ≠ lo que corre
- `done` = el último despliegue terminó bien, no "el contenedor está vivo": un compose de 5 servicios con uno en bucle sigue `done`;
  `idle` es "nunca desplegado" **y** "parado a mano" [S]. Si 3.2 pinta verde por `done`, el panel mentirá exactamente en el caso que
  importa, y 3.3 (docker) llega después.
- Cambio: el estado "vivo" de aplicación/compose/base de datos sale de **contenedores** (`docker.getServerHealth` o `docker_ssh`),
  y `status` del panel se guarda como "estado de despliegue", otra columna. Es la razón principal para adelantar 3.3a o incluir
  `docker.getServerHealth` en 3.2 con el mismo parser que `docker_ssh`. En pantalla, dos luces: *despliegue* y *en ejecución*.
- Prueba: fixture `done` + contenedor `restarting` → recurso rojo.

### PM7 (A) "Sin copia" desde una lista corta
- [V en el plan] 20 trabajos globales; con 6 bases + copias de volumen, 20 entradas cubren 2-3 días. Una copia semanal o mensual
  no aparece casi nunca; una programación desactivada o una copia hecha fuera de Dokploy (cron `pg_dump`, snapshots del proveedor)
  no aparecen jamás. Dos fallos opuestos: falsa alarma crónica (y se ignora la alerta: muere la función) o falsa tranquilidad si la
  regla mira solo "hay algún `done` reciente".
- Cambio: (1) tabla propia `copias_vistas(recurso, ultima_buena_ts, ultima_fallida_ts, visto_en)` con semántica **monótona**: cada
  lectura solo puede adelantar `ultima_buena`; (2) regla "sin copia" = `ahora_panel − ultima_buena > umbral` **y** `vigilado_desde`
  más antiguo que el umbral (carencia); (3) umbral por recurso (diario/semanal/"gestionada fuera" = silencio); (4) la referencia de
  tiempo es la cabecera `Date` del panel, no el reloj del Mac (resiste reloj mal puesto, PM11); (5) si existe un endpoint de
  programación por base sin secretos, consultarlo en 3.2b, no bloquear 3.2.
- Prueba: 30 días simulados con copia semanal → 0 alarmas; programación que deja de producir → alarma al día 8.

### PM8 (A) Carga del ciclo: ya hoy supera su presupuesto, y el panel lo agrava
- [V] `recoger_uno` encadena SSH (≤ 20 s, `servidores_ssh.py:12`) + hasta 4 webs **en serie** a 5 s (`servidores_ciclo.py:73`,
  `servidores_externo.py:12`) + puertos (≤ 6 s) = hasta 46 s, con `TOPE_POR_TANDA = 32` (`servidores_ciclo.py:28,125`): con un
  servidor lento, `as_completed` vence y **todos** los pendientes quedan en `timeout` aunque su SSH fuese bien (`:132-135`).
  Añadir 4-5 `GET` al panel dentro de `recoger_uno` lo hace sistemático.
- Cambio: colectores con presupuesto propio y en paralelo dentro del servidor (SSH, webs, panel), tope por colector, y el
  resultado parcial se guarda por colector (un panel lento no tumba el host). El panel se consulta a cadencia normal **nunca en
  incidencia** (la incidencia acelera SSH, no la API) y con un solo `GET` en vuelo por panel.
- Prueba: panel que tarda 30 s → host y webs se registran; `panel` queda gris con motivo `timeout`.

### PM9 (A) Retención de etiquetas (nombres de clientes) permanentes
- v2: `horas_recurso` permanentes y `recursos.etiqueta` personal. Un cliente que se va deja sus nombres de proyecto/app para
  siempre, salvo `--olvidar-servidor`. Recursos que desaparecen (apps borradas) también conservan etiqueta indefinidamente.
  Hoy la caché ya guarda nombres de contenedores (`servidores_ssh.py:213,221`) pero caduca con la entrada (`servidores_ciclo.py:147`).
- Cambio: recursos inactivos > 90 días → etiqueta sustituida por `(retirado)` (o fila borrada, conservando solo agregados a
  nivel de host); al quitar un servidor en Ajustes → borrado en la siguiente purga **y** de su clave del Llavero (hoy nada borra
  `dokploy:<host>` al quitar el servidor o cambiar `panel_url`: `servidor_local.py:375-392` solo actúa sobre cuentas presentes en
  config; las antiguas quedan huérfanas con permisos de escritura). Texto en Ajustes: "servidor de un cliente" con retención.
- Prueba: inyectar reloj +91 días tras retirar un recurso → `grep` de bytes del nombre en base, WAL y copias devuelve nada.

### PM10 (M) Previsión de disco con regresión lineal
- Huecos (Mac dormido), picos de `docker build`, `docker system prune` (caída brusca), rotación de logs, y Dokploy que limpia
  imágenes: una recta por mínimos cuadrados sobre 14 días da "lleno en 3 días" tras un build y "nunca" tras un prune.
- Cambio: pendiente robusta (mediana de pendientes, Theil–Sen) sobre la última **segmentación** (una caída > 5 % abre segmento
  nuevo), mínimo 48 h de cobertura real y 20 puntos, sin previsión si la pendiente no es claramente positiva; aviso con
  histéresis (entra en < 14 días, sale en > 21). Rotular "a este ritmo".
- Prueba: serie sintética con un prune y un build → sin alarma; crecimiento constante del 1 %/día al 70 % → "lleno en ~30 días".

### PM11 (M) Relojes y zonas
- `lastDeployAt`/`createdAt` vienen del panel (ISO, UTC [S]); `ts` es del Mac. Si el Mac va mal (ya hay detección en copias:
  `ajustes.js:97`) las edades mienten. Compose/Swarm reportan "Up 3 hours" relativo al reloj del servidor.
- Cambio: guardar los instantes del panel como enteros UTC tras parseo estricto (rechazar sin zona, fuera de 2000-2100, > 5 min
  en el futuro respecto a `Date` del panel); edades siempre entre dos instantes de la **misma fuente**. Franjas horarias en UTC
  (como `horas` en consumo), presentación local.

### PM12 (M) Versiones de la API: Dokploy y EasyPanel cambian
- Dokploy v0.30.7 hoy; `environmentId/Name` y el router `overview` son recientes [S]. EasyPanel ≥ 2.31 sustituyó tRPC por
  `/api/rpc/*` con OpenAPI [S, fuente comunitaria]: "mismo contrato" (3.4) no significa mismas rutas ni mismo formato de token.
- Cambio: cada colector anuncia `version_api_vista` en `meta` (de `settings.health`/versión), parsea por **campos presentes**
  (campo ausente = degradación contada, nunca excepción), fixtures sanitizados de la versión real en `tests/datos`, y una prueba
  manual "contrato" documentada por versión. EasyPanel: exploración de solo lectura propia antes de 3.4, no asumir.

### PM13 (M) Cardinalidad y crecimiento
- Estimación [S]: ~70 recursos/servidor × 288 ciclos × 14 días ≈ 280 k filas crudas por servidor (×5 en incidencia); con 10
  servidores > 100 MB antes de compactar. Contenedores efímeros (builds, tareas `completed`, `<appName>.1.<taskid>` que cambia en
  cada redeploy) inflan `recursos` sin límite y agotan el tope de 500 expulsando lo útil.
- Cambio: crudo solo para métricas numéricas y **una fila por recurso y franja de 5 min** (máximo), inventario/estado solo por
  cambio (eventos); identidad de tareas Swarm por **servicio**, no por tarea; contenedores `exited(0)` de corta vida no se
  registran como recurso; expulsión: inactivos primero, luego menos horas vistas.
- Prueba: base sintética 10 servidores × 1 año < 200 MB; 1 000 contenedores efímeros/día no superan el tope.

### PM14 (M) El colector falla, tarda o devuelve algo enorme
- `overview.services` con `icon` ≈ 10 KB/servicio: 200 servicios = 2 MB. `servidores_externo` **no lee cuerpos ni acota tamaño**
  (`medir_web` usa `HEAD`, `:64-68`): "reutiliza `servidores_externo`" cubre resolución y TLS, no la lectura.
- Cambio: lectura por trozos con tope (p. ej. 4 MiB; superado → respuesta descartada entera, estado gris "respuesta demasiado
  grande"), `Content-Type` `application/json` exigido, sin `Accept-Encoding`, `json.loads` sobre bytes acotados, profundidad y
  nº de elementos acotados (RecursionError capturado), tiempo máximo por petición 10 s y por colector 25 s.
- Prueba: cuerpo de 50 MB en streaming → memoria estable y gris; JSON de 10 000 niveles → gris.

### PM15 (B) Migraciones, downgrade y mantenimiento
- `user_version` propio, `temp_store=MEMORY` en la conexión de `VACUUM`, purga en el primer ciclo, panel "sin datos" si la base no
  existe (PM9 previo). Mantenimiento con presupuesto de tiempo fuera del ciclo (PM2 previo).

## RED TEAM — vectores

### RT1 (C) Clave del panel con permisos de escritura, legible por cualquier proceso
- [V] `leer_clave` usa `security find-generic-password -w` (`claves.py:26`): un ítem creado por `security add-generic-password`
  es legible por `security` sin diálogo para cualquier proceso del usuario; `plan.md:217-218` lo reconoce para descartar SQLCipher,
  pero no lo aplica a las claves de panel. Dokploy: la clave hereda el rol del creador (Owner/Admin/Member), limitada a la
  organización; "Access API/CLI" es un permiso que da el admin; roles a medida = Enterprise [S, docs.dokploy.com]. Un Member sin
  crear/borrar aún puede desplegar, parar y editar variables [S]. En Windows, `CredRead` con DPAPI de usuario es igual de legible.
- Qué impide `POST`/`DELETE`: solo nuestro código. Lista blanca + test es **necesaria pero no suficiente**: no protege de otro
  proceso, de un XSS en el panel local (no: la clave nunca sale por HTTP, bien), ni de un bug en una librería. El MCP del dueño
  (`dokploy-mcp`, presente en este entorno) ya guarda otra clave omnipotente en claro en la config del cliente MCP [S]; CostGuard
  añadiría una segunda copia.
- Cambios al plan: (1) **decisión del dueño explícita**: aceptar clave de usuario Member dedicado (mínimo: sin crear/borrar, sin
  SSH keys, sin Git providers, sin Traefik files, sin Docker) o ir sin API (`docker_ssh`) — el plan hoy dice "comprobar al dar de alta
  el alcance real" como si pudiera ser de solo lectura; (2) la comprobación al alta debe ser **negativa**: probar que una ruta de
  escritura inocua responde 403 con esa clave (p. ej. `user.*`/`settings.*` de admin), y si responde 200 → aviso rojo "esta clave puede
  escribir" y no guardarla salvo confirmación; (3) código: una sola función `_get(nombre_ruta)` con `RUTAS = frozenset({...})`,
  sin parámetro `method`, cuerpo nunca enviado, `x-api-key` nunca en logs; test estático que falla si el módulo contiene `POST`,
  `request(` genérico o `urlopen`; (4) rotación: documentar revocar la clave al quitar el servidor y borrarla del Llavero (PM9);
  (5) nunca una opción "ignorar certificado": la clave viaja a quien presente un certificado válido para ese nombre; dominio caducado
  del cliente = clave regalada. Opcional: huella SPKI fijada al alta (TOFU, como `known_hosts`), con aviso al cambiar.
- Prueba: test de mutación: añadir una ruta de escritura a `RUTAS` hace fallar el test de lista blanca; alta con clave de Owner →
  rechazada; `grep` del log tras un ciclo no contiene la clave.

### RT2 (A) Panel comprometido: respuesta hostil
- Vectores: tipos cambiados (`status: {}`, `id: 123`, `name: [..]`), `name`/`projectName` con HTML, `‮`, NUL, 10 000
  caracteres, 50 000 servicios, `lastDeployAt: "9999-12-31"`, `icon` de 50 MB, `Content-Type: text/html` con JSON, 301 a otro
  host, `Set-Cookie`, respuesta lenta gota a gota (slowloris), gzip bomb si se aceptara compresión, `Date` absurdo.
- [V] Hoy el único renderizado de nombres de servidor pasa por `esc()` (`desplegable_servidores.py:77`) y CSP sin `unsafe-inline`
  de script (`servidor_local.py:38`); `img-src data:` está permitido (`:38`): si alguien pintara `icon`, la CSP no lo frenaría.
  `sin_secretos` es una lista negra por nombre (`:53-54,123-134`): no cubre `env`, `databasePassword`, `postgresPassword` ni
  `connectionUrl` (camelCase sin `_`): **no sirve** como filtro de respuestas de panel.
- Cambios: parser por lista blanca de campos y tipos; `id` `^[A-Za-z0-9_-]{6,64}$`; `name` NFC, ≤ 64, juego `[\w .:/-]`, sin
  categorías `C*`/`Z[lp]`, y si no encaja se guarda `(nombre no válido)` y se cuenta; `status` de enum; fechas estrictas; tope de
  elementos (2 000) y de cuerpo; redirección = error (no seguir); `Date` solo si está dentro de ±1 día del Mac; `icon` ni se
  deserializa en el modelo. Regla de revisión: ningún `str(e)` ni fragmento de respuesta en `log()`, en `cache["error"]`
  (`servidores_ciclo.py:131` interpola `{e}`), ni en el traceback que el trabajador escribe (`trabajador_barra.py:79`): errores de
  colector = enum cerrado. Quitar `data:` de `img-src` si la pestaña nueva no lo necesita.
- Prueba: fixture `panel-hostil.json` con todo lo anterior → 0 excepciones, 0 bytes hostiles en la base, log sin controles.

### RT3 (A) Servidor comprometido: `recoger.sh`/`recoger-root` v2 y `recoger-bd`
- [V] Secciones repetidas: gana la última (`servidores_ssh.py:117-120`) y `recoger-root` imprime después (`recoger.sh:64-66`):
  root puede pisar cualquier sección; `_num` admite `1e308` (`:126-132`). Nombres de contenedor/servicio limitados por Docker a
  `[a-zA-Z0-9][a-zA-Z0-9_.-]` [S], así que tab y HTML no entran por `docker ps --format`; pero **etiquetas y nombres de compose**
  (`com.docker.compose.project`) y de volúmenes/imágenes sí admiten más (no usarlos, o tratarlos como hostiles). Salida > 64 KiB
  se descarta entera (`:105-106`): un servidor puede "ocultarse"; con el inventario histórico eso debe ser "gris", no "todo desapareció".
- Cambios: v2 del script con **sección única y numerada** (`### contenedores 2`), tope por sección en el Mac (100 líneas, ya en el
  script), rechazar muestra entera si una sección se repite; números fuera de rango descartan la muestra; estados de contenedor de
  enum (`running|exited|restarting|paused|created|dead`); `RestartCount` y `Health` por `docker ps --format` (sin `inspect`).
  `recoger-bd`: ver RT7.
- Prueba: fixture con `### contenedores` duplicada y `restarting` ×1000 líneas → muestra descartada, contador en `meta`.

### RT4 (M) Cliente que pone nombres (sin comprometer nada)
- Nombres de proyecto/app en Dokploy son libres (UI); contenedores de compose heredan `appName` (sanitizado). Riesgos: HTML en
  panel local (cubierto por DOM, pero `app.js:275-278` sigue usando `innerHTML` con texto no escapado de bases locales: cerrar
  antes de 3.6), fórmulas en CSV (`neutralizar` lo cubre si se usa), bidi para disfrazar `prod` como `dorp` (quitar `Cf`), nombres
  enormes (tope), homógrafos (dos proyectos "cliente-a" con `а` cirílica → NFC no basta; mostrar id corto junto al nombre),
  cardinalidad (un cliente crea 5 000 apps: tope por servidor y por tipo, el resto "(+N no listados)").
- Prueba: `test_panel_seguro` con nombre `<img src=x onerror=…>` y `‮`; CSV con `=1+1`.

### RT5 (M) Fuga de credenciales de bases de datos por el camino del panel
- El detalle por aplicación/base de datos trae `env`, contraseñas y URLs de conexión [V en la exploración]. El riesgo no es solo
  "no las guardamos": es que un `GET` de detalle entre en `RUTAS` "para leer la programación de copias" (PM7.5) y el cuerpo entero
  pase por `json.loads`, por un traceback o por una fixture de test copiada de la respuesta real.
- Cambios: `RUTAS` solo con listados/resúmenes sin secretos; si hace falta un detalle, lista blanca de campos aplicada **antes** de
  cualquier log/fixture y test que afirme que el parser ignora `env`, `*assword*`, `*Url`; prohibición de volcar respuestas reales
  a `tests/datos` sin sanitizar (script de sanitizado); `costbar.log` nunca recibe cuerpos.
- Prueba: respuesta con `databasePassword` → no aparece en base, caché, log ni `anomalias`.

### RT6 (M) Llavero, caché, logs, base: atacante con lectura local
- [V] `logs/servidores.json` 0600 con host, usuarios, IPs y nombres (`config_y_estado.py:98-104`); base 0600 (`almacen.py:150-155`);
  en Windows nada (`:122-123`, `permisos.py:36-37`). FileVault/ACL son la base; nada más es razonable (ya decidido en `plan.md:216`).
  Lo nuevo: inventario completo de clientes + clave omnipotente en el mismo equipo (RT1). Copias cifradas excluyen `servidores.sqlite`
  por construcción (`hilo_copias.py:33-35`) ✓.
- Cambios: documentar en el README el modelo de amenaza real ("un proceso de tu usuario puede leer todo esto"); Windows: comprobar
  al arrancar que la ACL de `%LOCALAPPDATA%\CostGuard` no concede `Users`/`Everyone` (lectura de `icacls` o `GetNamedSecurityInfo`),
  avisar si no; `VACUUM` con `temp_store=MEMORY`.

### RT7 (A) `recoger-bd`: ejecución de consultas como superusuario dentro de contenedores de clientes
- Supuesto del plan: "autenticación local por socket/peer". En Dokploy/EasyPanel las bases de datos **son contenedores**: no hay
  socket en el host. La vía real es `docker exec <contenedor> psql -U <usuario>` (la imagen oficial confía al socket interno [S]),
  es decir: root del host ejecuta SQL como superusuario de la base del cliente, eligiendo el contenedor por imagen o por nombre
  (ambos controlados por el cliente). Nombres de base (`datname`) admiten espacios, tabuladores y saltos → inyección en la salida
  de texto; `.psqlrc` del contenedor se carga si no se pasa `-X`; un contenedor comprometido puede sustituir `psql` (no escapa del
  contenedor, pero cuelga o inunda). MySQL/Mongo/Redis multiplican el problema.
- Cambios: (1) separar `recoger-bd` como **3.3b** y condicionarla a la pregunta 3 (en servidores de clientes, solo con permiso
  escrito); (2) lo que no necesita entrar en la base sale gratis de Docker: viva/caída, reinicios, salud, **expuesta o no**
  (puertos publicados en `docker ps --format '{{.Ports}}'` + `puertos_expuestos` desde el Mac), tamaño del volumen (`docker system
  df -v`); (3) si se hace: solo PostgreSQL, consultas fijas con `-X -A -t`, salida **solo numérica agregada** (nº de bases, suma de
  tamaños, conexiones, retraso de réplica, transacciones > 5 min), nunca `datname`; contenedor elegido por `label` fija de Dokploy
  [S] y nombre validado por regex antes de `docker exec`; `timeout` por consulta; usuario con rol `pg_monitor` si existe, no
  superusuario; (4) un solo programa en sudoers sin argumentos, como hoy (`instalar-en-servidor.sh:40`).
- Prueba: base llamada `x\ty\n### contenedores` → salida válida; contenedor con `psql` que imprime 100 MB → cortado.

### RT8 (M) Servidor local 127.0.0.1 y exfiltración por los endpoints nuevos
- [V] Escucha solo en 127.0.0.1 (`servidor_local.py:398`); `Host` exacto + `Sec-Fetch-Site` (`:207-214`); sesión en todo
  (`:216-221,275`); POST solo `/guardar` y `/clave` con `Origin` (`:325-350`). Red local y DNS rebinding: cubiertos. Lo que queda es
  **XSS en el propio panel** (`app.js:278`) leyendo `/servidores/*` con la sesión y sacándolo por `location=` (la CSP no bloquea
  navegaciones). La clave de panel nunca sale por HTTP (solo `clave_guardada`, `:309`) ✓.
- Cambios: endpoints nuevos con parámetros de lista cerrada, `servidor=` obligatorio, sin "todos", paginación con tope, auditoría de
  recuento para IPs; cerrar `innerHTML` de `app.js` antes de 3.6; `Cache-Control: no-store` ya está (`:196`).

### RT9 (B) SSRF/DNS rebinding contra `panel_url`
- [V] `url_valida(solo_https=True)` rechaza credenciales, hosts locales/privados e IPs literales no públicas (`servidores.py:49-66`);
  `ip_publica` revalida al conectar y fija la IP (`servidores_externo.py:18-26`); `http.client` no sigue redirecciones ni usa proxy.
  Suficiente, con dos añadidos: construir la URL de la API solo con `scheme + netloc` de `panel_url` (ignorar ruta y query que el
  usuario haya pegado) y tratar cualquier 3xx como error. Un `panel_url` apuntando a un host público ajeno es decisión del usuario
  (y le entrega la clave): avisar al alta con el nombre resuelto.

## Orden de fases: evaluación

| Fase v2 | Veredicto | Cambio |
|---|---|---|
| 3.0 Windows + retención fuera de base | Primera, correcta, **incompleta** | Añadir runner sin barra, import del paquete, `purgar_render` en `nt`, cerrojo del SO; criterio de salida: `--autoprueba` en Windows con ciclo real contra servidor simulado |
| 3.1 base + host + IPs | Correcta | Esquema **genérico** ya (PM3) y núcleo SQLite extraído (PM2); lista explícita de los 15 cambios de la revisión previa |
| 3.2 Dokploy | Debe ir **después** de 3.3a o incluir `docker.getServerHealth` con parser compartido (PM6) | Condicionada a la decisión RT1; alcance: `settings.health`, `overview.services`, `overview.backups`, `docker.getServerHealth`; nada de detalle por servicio |
| 3.3 SSH v2 + `recoger-bd` | Dividir: **3.3a** (`recoger` v2 + `docker_ssh`, mismas credenciales, sin scripts nuevos en sudoers) antes de 3.2; **3.3b** `recoger-bd` al final y condicionada | RT7 |
| 3.4 EasyPanel + certificados | Certificados de webs: trivial y valioso, adelantarlo a 3.3a (ya se abre TLS en `medir_web`: leer `getpeercert()` `notAfter`) | EasyPanel tras exploración propia (PM12) |
| 3.5 reglas y avisos | Correcta tras 3.2/3.3a | Incluir histéresis, carencia y silencio por recurso (PM4, PM7, PM10) |
| 3.6 pestaña | Última, correcta | Cerrar `app.js:278` antes |

## Los riesgos que harían abandonar el plan si no se resuelven antes

1. **Clave de panel omnipotente en un almacén legible por cualquier proceso** (RT1). Sin un rol de solo lectura o sin aceptar el
   riesgo con un Member recortado, 3.2/3.4 no deberían existir: `docker_ssh` da casi todo sin credenciales nuevas.
2. **Windows sin runner** (PM1). Si el dueño no quiere un demonio/bandeja en Windows, la fase 3 es macOS y hay que decirlo.
3. **Alertas deshonestas** (PM4, PM6, PM7): si "desapareció", "sin copia" y "verde por `done`" no se definen con histéresis,
   carencia y verdad de contenedores, 3.5 genera ruido y se ignora; monitorizar que no se mira es peor que no monitorizar.

## Las cinco preguntas abiertas: recomendación

| # | Pregunta | Recomendación | Qué la cambiaría |
|---|---|---|---|
| 1 | Clave dedicada a CostGuard | **Sí, obligatoria**, de un usuario **Member** creado para ello, sin crear/borrar, sin SSH keys, Git providers, Traefik ni Docker, con acceso solo a los proyectos a vigilar; nunca la del MCP. Y aun así tratarla como "puede desplegar y parar". Si el dueño no acepta ese residual: sin API, solo `docker_ssh`. | Dokploy añadiendo rol de solo lectura en la edición gratuita |
| 2 | Kubernetes | No hay evidencia de ningún servidor con k8s: **fuera del plan** (el modelo de recursos ya es genérico; añadir un colector después no cuesta rediseño). | Un servidor real con k8s |
| 3 | Scripts nuevos en servidores de clientes | `recoger` v2 sí (mismo contrato, mismos privilegios, misma instalación idempotente: es una actualización, no un permiso nuevo). `recoger-bd` **solo en los propios** y en los de clientes solo con autorización escrita (ejecuta SQL como superusuario en sus bases). | Contrato de mantenimiento que lo cubra |
| 4 | 2 de 6 bases con copias | No se puede responder desde el plan ni hay que depender de la respuesta: el diseño debe funcionar con copia semanal, mensual o externa (PM7). Acción del dueño: revisar las 4 programaciones en Dokploy ahora, por su propio interés. | — |
| 5 | Servidores remotos de Dokploy | Modelar `servidor_panel_id` nullable desde 3.1; agrupar por host del panel hasta que exista uno; nada de UI. | Un servidor remoto real |

**Decisiones del dueño que faltan en el plan:**
- D-A: aceptar que la clave de panel es legible por cualquier proceso de su usuario y elegir *Member recortado* o *sin API* (RT1).
- D-B: qué es Windows en esta fase: ¿solo portar módulos, o un runner real sin barra? (PM1).
- D-C: retención de etiquetas (nombres de proyectos/apps/contenedores, potencialmente de clientes): 90 días tras desaparecer y
  borrado con el servidor, o permanentes (PM9). Y constancia de que "guardar nombres de contenedores" deja de ser no objetivo.
- D-D: paneles con certificado no válido: **no soportados** (sin opción de ignorar), con huella SPKI opcional (RT1.5).
- D-E: `recoger-bd` como 3.3b condicionada; alcance PostgreSQL solo y salida solo numérica (RT7).

## Qué probar (resumen por fase)

- 3.0: import en Windows; ciclo bajo runner; `ssh.exe` colgado matado por árbol; `purgar_render` en `nt`; dos procesos y el cerrojo del SO.
- 3.1: ambas bases en el mismo directorio sin mezclar esquema ni copias; los 15 puntos de la revisión previa con sus pruebas.
- 3.3a: fixtures hostiles de `docker ps` (secciones repetidas, estados fuera de enum, 1 000 líneas); certificados `notAfter`.
- 3.2: lista blanca con test de mutación; alta con clave de Owner rechazada; cuerpo de 50 MB; `panel-hostil.json`; log sin
  controles ni clave; secuencia [inventario, 502, inventario] sin eventos.
- 3.5: 30 días simulados con copia semanal sin alarma; prune/build sin previsión falsa; Mac "dormido" 8 h sin ausencias.
- 3.6: XSS con nombres hostiles; `con_ips` solo tras clic con auditoría; `app.js` sin `innerHTML` con datos.

## Métricas de éxito

- Falsas alarmas < 1 por servidor y semana en el primer mes (se anotan y revisan).
- 0 rutas no `GET` en el colector (test estático) y 0 altas con clave que responda 200 a una ruta de escritura.
- p95 del ciclo por servidor < 25 s con panel incluido; ningún "timeout" por tanda con un solo servidor lento.
- Base de servidores < 20 MB por servidor y año; `grep` de bytes limpio tras `--olvidar-servidor` (base, WAL, copias, caché, PNG).
- `--autoprueba` verde en Windows con ciclo real, antes de escribir 3.1.

## Supuestos

- Dokploy gratuito: roles Owner/Admin/Member fijos; roles a medida solo Enterprise; un Member sin crear/borrar puede seguir
  desplegando y parando (alta; docs oficiales y mirror, no probado contra el panel).
- Estados de aplicación de Dokploy `idle|running|done|error` y semántica "done = último despliegue OK" (media-alta; código
  abierto de Dokploy, no revisado en esta sesión).
- EasyPanel ≥ 2.31 usa `/api/rpc/*` con OpenAPI en lugar de tRPC (media; fuente comunitaria).
- Las bases de datos de Dokploy son contenedores con la imagen oficial, sin socket en el host (alta por diseño de Dokploy).
- Nombres de contenedor/servicio limitados por Docker a `[a-zA-Z0-9][a-zA-Z0-9_.-]` (alta); etiquetas y nombres de compose, no.
- Cifras de volumen: estimación, no medición (baja): medir con base sintética.
- Un ítem creado con `security add-generic-password` es legible por `security find-generic-password -w` sin diálogo para
  procesos del mismo usuario (alta; coherente con `plan.md:217-218`; no se ha ejecutado aquí).

Status: DONE_WITH_CONCERNS
Summary: El modelo del plan v2 es correcto; lo que lo haría fracasar es la clave de panel omnipotente legible localmente sin rol de
solo lectura en Dokploy gratuito, la ausencia de un runner del ciclo en Windows, una infraestructura SQLite que no se reutiliza
"por ruta" y alertas (desaparición, `done`, "sin copia") que mentirían sin histéresis, verdad de contenedores y carencia; se propone
reordenar 3.3a antes de 3.2 y separar `recoger-bd`.
Preguntas sin resolver: D-A (Member recortado o sin API), D-B (runner en Windows), D-C (retención de etiquetas de clientes y
reversión del no objetivo de 3.1), D-D (certificados no válidos no soportados), D-E (`recoger-bd` solo en servidores propios).
