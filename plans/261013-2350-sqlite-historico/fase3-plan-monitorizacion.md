# Fase 3 ampliada — monitorización completa de servidores, paneles, servicios y bases de datos (plan v3, con la revisión `../reports/revision-plan-fase3-ampliada-261014.md` aceptada)

Estado: **plan v3**; ver «Decisiones y orden definitivos» al final. Antes: plan v2 sin código (incorpora las respuestas del dueño y una exploración de solo lectura del Dokploy real). Fecha: 2026-10-14. Sustituye el alcance de `fase3-diseno.md` (historial de servidores e IPs): ese diseño y su
revisión (`../reports/revision-diseno-fase3-261014.md`) pasan a ser la **fase 3.1** de este plan, con sus correcciones. Lo nuevo es todo lo
que hay *dentro* de los servidores.

## Resultado buscado
Una vista única y con historial de **todo lo que corre en tus servidores**, sin tocarlo (monitorizar, no actuar):
1. El servidor: carga, memoria, disco, red, accesos, puertos, actualizaciones, protección (lo que ya hay) **con historial**.
2. Los **orquestadores y paneles** que lleven: Dokploy y EasyPanel (y Docker Swarm / Compose a secas): proyectos, aplicaciones, servicios,
   despliegues y su estado, copias de seguridad programadas y cuándo salió la última buena.
3. Los **contenedores** y servicios de cualquier tipo, también los que no gestiona ningún panel.
4. Las **bases de datos** (PostgreSQL, MySQL/MariaDB, MongoDB, Redis…): viva o caída, expuesta o no, tamaño, conexiones, retraso de
   réplica y antigüedad de la última copia.
5. Las **webs y certificados** (ya se miden; falta caducidad del certificado y el histórico).
6. **Reglas y avisos** que se ven venir: disco que se llena en N días, copia que lleva demasiado sin hacerse, contenedor que se reinicia en
   bucle, puerto o servicio nuevo que antes no estaba, certificado a punto de caducar.
Todo funciona en **macOS y Windows**, sin dar nuevos permisos de escritura a la aplicación sobre ningún servidor.

## No objetivos
Actuar sobre los servidores (reiniciar, desplegar, borrar, cerrar puertos desde la app) · enviar datos fuera del equipo · sustituir a Grafana
o a un SIEM · guardar logs de aplicaciones · guardar contenido de bases de datos · agentes instalados de forma permanente en los servidores
· geolocalizar IPs · exigir una cuenta de pago de ningún panel.

## Lo que ya existe (verificado en el código)
- `servidores_ciclo` + `servidores_ssh` + `recoger.sh` / `recoger-root.sh`: lectura por SSH con clave dedicada restringida (`command=`), sección a
  sección (sistema, memoria, disco, sesiones, puertos, intentos, actualizaciones, bastionado, servicios Swarm, contenedores, fail2ban).
- `servidores_externo`: medida de webs con IP fijada tras resolver (anti DNS-rebinding), `https` obligatorio y host público.
- Paneles: solo el botón «Panel» y una **clave API guardada en el llavero** (`claves.py`, cuenta `dokploy:<host>`/`easypanel:<host>`). **No hay
  ninguna llamada a la API del panel.** Los textos dicen «solo lectura», pero las claves de Dokploy y EasyPanel heredan los permisos del
  usuario que las crea: hay que comprobarlo, no suponerlo (riesgo principal de 3.3).
- Ciclo de servidores en el hilo trabajador de la barra; no arranca en Windows (`fcntl`, `os.killpg`).

## Lo que se ha visto en el Dokploy real (exploración de solo lectura, 2026-10-14)
Se usaron solo `GET` de listado y resumen (el detalle de una aplicación o base de datos se evitó a propósito: trae variables de entorno y
contraseñas). Sin nombres de clientes en este documento.
- **Versión y salud:** Dokploy v0.30.7; `settings.health` → `ok`. Un solo servidor local (`server.all` vacío): hoy no hay servidores remotos
  registrados en el panel, así que «todo el servidor» es el propio host de Dokploy.
- **`project.homeStats`** (una llamada, sin parámetros): 7 proyectos, 7 entornos, 7 aplicaciones, 11 composes, 6 bases de datos, 24 servicios;
  estado agregado `running 16 / error 1 / idle 7`.
- **`overview.services`** (una llamada = **todo el inventario**): por servicio `id`, `name`, `appName`, `status` (`done|idle|error`), `createdAt`,
  `serverId/serverName` (null = local), `projectId/Name`, `environmentId/Name`, `type` (`application|compose|postgres|…`), `lastDeployAt`
  y `icon`. **`icon` puede ser un PNG en base64 de ~10 KB**: la lista de campos permitidos lo descarta (tamaño y datos no fiables).
- **`overview.backups`** (una llamada): los últimos ~20 trabajos de copia (`kind` = `backup` de base de datos o `volumeBackup`), con `status`,
  `createdAt`, `destinationName`, `databaseType`, `serviceName` y `serviceOwnerId`. Sirve para «antigüedad de la última copia buena por
  servicio». **Ojo:** en la muestra solo 2 de las 6 bases de datos aparecen con copias recientes. Puede ser real (sin copia programada) o un
  efecto de que la lista es corta; **no se avisará de «sin copia» sin consultar la configuración de copias de cada base de datos** (endpoint por
  servicio que hay que revisar con cuidado de no leer contraseñas).
- **`docker.getServerHealth`** (una llamada): contenedores y servicios Swarm (36 y 17), memoria total y usada (5,6 de 8,3 GB), CPUs, disco
  (68,4 de 102,9 GB), uso de cada red Docker y errores del demonio. Es casi todo lo que `recoger-root` obtiene por SSH, ya agregado.
- Hallazgos reales del recuento: 1 aplicación en `error` desde su último despliegue; 7 servicios `idle`; 6 bases de datos, todas PostgreSQL.
- Las **IDs son estables** (`serviceOwnerId`, `projectId`): son la identidad correcta del recurso, no el nombre, que el dueño puede cambiar.

**Consecuencia de diseño:** el colector de Dokploy necesita **cuatro o cinco `GET` por ciclo**, no cientos. La mayor parte de la fase 3.2 es una lista
de campos permitidos, validación estricta de tipos y estados, y comparación entre ciclos (apariciones, desapariciones, cambios de estado).

## Arquitectura propuesta

### A. Modelo canónico y colectores intercambiables
Todo lo que se monitoriza es un **recurso** con un tipo cerrado: `host`, `contenedor`, `servicio`, `aplicacion`, `base_datos`, `web`,
`certificado`, `despliegue`, `copia`. Cada **colector** es un módulo pequeño con un contrato fijo:
`recoger(servidor, ahora) -> lista de observaciones` con campos tipados (tipo, identidad estable, estado de un conjunto cerrado,
métricas numéricas acotadas, etiquetas de un conjunto cerrado). Nada de diccionarios libres que acaben en la base.

Colectores, de menos a más riesgo:
1. `host_ssh` (lo que ya hay; v2 del script con histograma de intentos por hora y campos de confianza).
2. `docker_ssh`: contenedores y servicios Swarm/Compose con estado, reinicios, salud y límites, vía `recoger-root` v2 (solo lectura, sin
   secretos: nunca `docker inspect` completo, que incluye variables de entorno con contraseñas).
3. `web_http` (ya hay) + caducidad de certificado.
4. `bd_local`: **la consulta a la base de datos la hace un script en el propio servidor** (`recoger-bd`, root solo-lectura, autenticación local
   por socket/peer), de modo que **la aplicación nunca guarda contraseñas de bases de datos**. Devuelve números (conexiones, tamaño,
   retraso de réplica, transacciones largas), nunca datos.
5. `panel_api` (Dokploy primero, EasyPanel después): proyectos, aplicaciones, despliegues y copias programadas por HTTPS con una clave en el
   llavero. **Solo `GET`, con lista blanca de rutas fijada en código** y test que impide cualquier otra.
6. `orquestador_extra` (Kubernetes u otros): mismo contrato, más adelante (pregunta abierta 2).

### B. Almacén
Una sola base `servidores.sqlite` (fase 3.1) generalizada: `recursos(id, servidor_id, tipo, clave_estable, etiqueta, primera, ultima, activo)`,
`observaciones` crudas 14 días y `horas_recurso` permanentes (picos y medias), `eventos(ts, recurso, tipo, severidad)` para cambios de
estado, apariciones y desapariciones. **El inventario es histórico**: que algo aparezca o desaparezca es un evento. Cardinalidad acotada por
servidor (p. ej. 500 recursos) y por tipo. Las etiquetas (nombres de proyectos, aplicaciones, contenedores) son **datos personales/
confidenciales** (pueden ser nombres de clientes): clasificadas como `personal`, nunca en exportaciones por defecto.

### C. Reglas, previsión y avisos
Reglas declarativas sobre el almacén (no código por regla): umbral, ausencia (no hay observación nueva), cambio de inventario, tendencia
(regresión lineal del disco → «lleno en N días»), antigüedad (última copia). Resultado: estado `verde/ámbar/rojo/gris` por recurso y
un único aviso por transición (mecánica de `avisos.json` de la fase 2, con `plataforma.avisar`). Sin texto de aviso con nombres.

### D. Portabilidad (Windows es requisito)
- **Fase 3.0, antes de nada:** el ciclo de servidores no arranca en Windows. Hay que sustituir `fcntl` por un cerrojo portable
  (fichero con creación exclusiva y caducidad), `os.killpg` por matar el árbol de procesos (`taskkill /T` o `subprocess` con tiempo máximo),
  y comprobar el cliente OpenSSH de Windows 10/11 (`ssh.exe`) con la clave dedicada y `StrictHostKeyChecking`.
- **Secretos:** `claves.py` hoy habla con el Llavero de macOS. Una interfaz por sistema: macOS Keychain, **Windows Credential Manager**
  (vía `ctypes`/`CredRead`/`CredWrite`, sin dependencias nuevas), y fallo cerrado si no hay almacén seguro.
- **Sin Time Machine en Windows:** la exclusión de copias del sistema pasa a `plataforma.excluir_de_copias_del_sistema(ruta)`: macOS usa
  `tmutil addexclusion` (hoy el plan lo da por hecho y **no existe**); en Windows los datos viven en `%LOCALAPPDATA%\CostGuard`, que ni el
  Historial de archivos ni OneDrive (carpetas conocidas) cubren por defecto; se marca `NOT_CONTENT_INDEXED`, se rechaza cualquier ruta bajo
  OneDrive (ya lo hace `ruta_no_fiable`) y se documenta que **la única copia soportada es la cifrada de la aplicación**.
- **Avisos:** `plataforma.avisar` en Windows (aviso de sistema) cuando se porte la barra; mientras tanto, aviso visual.
- Rutas, finales de línea, permisos (ACL en lugar de `chmod`), `time.mktime` y zonas horarias: ya resueltos en las fases 1 y 2 o con tests.

### E. Seguridad y privacidad (transversal)
- Todo valor que llega de un servidor, de un panel o de una base de datos es **hostil**: se parsea con función estricta, se acota y, si no
  encaja, se descarta y se cuenta (nada de cadenas libres en la base; etiquetas con juego de caracteres y longitud cerrados).
- **Mínimo privilegio verificable:** el colector de panel solo conoce rutas `GET` de una lista blanca; el script de bases de datos solo ejecuta
  consultas fijas de solo lectura; ningún colector acepta como entrada un nombre de ruta, de tabla ni de comando.
- Clave de panel: documentar y **comprobar al dar de alta** el alcance real de la clave (qué rutas responde); si puede escribir, avisar y
  ofrecer la alternativa sin API (`docker_ssh`, sin credenciales nuevas). Ver pregunta abierta 4.
- Red: `https` obligatorio, host público, IP fijada tras resolver, certificado verificado, sin redirecciones, tamaño y tiempo máximos
  (reutiliza `servidores_externo`). Semáforo global de peticiones por servidor para no ahogar un panel pequeño.
- Retención: IPs de intentos de acceso 90 días (decisión cerrada) y, para el resto, crudo 14 días y agregados permanentes; borrado real
  (`secure_delete`, `VACUUM` periódico) y orden `--olvidar-servidor`. La caché actual con IPs (`logs/servidores.json`) y los PNG del
  desplegable pasan a 48 h (hallazgo de la revisión de 3.1: hoy incumplen el plazo).
- Los servidores de clientes: aviso y ajuste por servidor (`guardar_ips`, retención), como pidió la revisión.

### F. Pantalla
Pestaña **Servidores** en el panel web: árbol *servidor → panel/orquestador → proyecto → aplicación/servicio/base de datos*, estado por nodo,
gráficos de histórico (picos), línea de tiempo de eventos («apareció el contenedor X», «la copia de Y lleva 3 días sin hacerse») y tabla de
IPs con caducidad (sólo tras un clic). Todo por DOM, sin `innerHTML` (regla de la fase 2). El desplegable de la barra muestra solo el peor
estado y el número de incidencias.

## Fases (cada una pasa pre-mortem + red team antes de codificar, y revisión de código al terminar)
Orden acordado con el dueño: lo que más valor da primero (proyectos y estado de lo que corre), usando la API del panel en solo lectura.
| Fase | Contenido | Depende de |
|---|---|---|
| **3.0** | Portabilidad Windows del ciclo y de los secretos; retención fuera de la base (caché 48 h, PNG, exclusión Time Machine real y equivalente en Windows) | — |
| **3.1** | `servidores.sqlite`, historial de host e IPs de intentos de acceso (diseño revisado en `fase3-diseno.md`), `id` estable, `guardar_ips` | 3.0 |
| **3.2** | Colector **Dokploy** de solo lectura: inventario (`overview.services`), copias (`overview.backups`), salud de Docker, estados, despliegues; eventos de aparición, desaparición y cambio de estado | 3.1 |
| **3.3** | Contenedores y bases de datos **por SSH** (`recoger` v2 y `recoger-bd`): sin credenciales en el Mac, cubre también servidores sin panel | 3.1 |
| **3.4** | Colector **EasyPanel** (mismo contrato) y certificados de las webs | 3.2 |
| **3.5** | Reglas, previsión de disco y avisos (copia sin hacer, contenedor en bucle, servicio nuevo, certificado) | 3.1–3.4 |
| **3.6** | Pestaña «Servidores» completa: árbol servidor → proyecto → servicio/base de datos, línea de tiempo, IPs con caducidad | 3.5 |

**Uptime:** se rotula **disponibilidad observada**. La app mide mientras el Mac está encendido; los huecos se muestran como «sin datos», no como caída.
Un porcentaje de uptime real exigiría medir desde algo siempre encendido (otra fase, si se quiere).

## Respuestas del dueño y preguntas que quedan
Respondido: hay **Dokploy y EasyPanel**, también en el mismo servidor; las **bases de datos están en el mismo servidor** y hay URLs de conexión por proyecto;
tiene los MCP de ambos paneles y puede administrar todo el servidor por SSH o por MCP; Windows es requisito (sin Time Machine); la API del panel se acepta.
Quedan abiertas:
1. **Clave de panel de CostGuard.** Las claves de Dokploy y EasyPanel heredan los permisos de quien las crea. ¿Creamos una clave **dedicada a CostGuard**
   (usuario de solo lectura si el panel lo permite) en lugar de reutilizar la del MCP? Recomendación: sí.
2. **«Orquestado, igual que Google».** ¿Kubernetes? ¿Algún servidor tuyo lo usa hoy?
3. **Scripts nuevos en los servidores** (`recoger` v2 y `recoger-bd`): ¿también en los de clientes, o solo en los tuyos?
4. **Copias de las bases de datos.** En la muestra solo 2 de 6 aparecen con copias recientes: ¿es lo esperado?
5. **Servidores remotos.** Dokploy puede gestionar servidores remotos (`serverId`); hoy no hay ninguno. ¿Habrá? Cambia cómo se agrupa el árbol.

## Verificación
Cada fase: pre-mortem y red team independientes antes de escribir código; tests con servidores, paneles y bases de datos **simulados y hostiles**
(respuestas gigantes, JSON con tipos cambiados, etiquetas con control y HTML, IPs privadas, cardinalidad explosiva, relojes del futuro);
borrado verificado leyendo los bytes del fichero; mutaciones sobre los filtros de seguridad; prueba en Windows con `--autoprueba` ampliada; y
revisión independiente del código con foco en privacidad y en mínimo privilegio.

## Decisiones y orden definitivos (v3, 2026-10-14, tras la revisión independiente del plan)
Decisiones del dueño (aceptó las recomendaciones de la revisión): **D-A** CostGuard empieza **sin API de panel** (todo por SSH y Docker, sin credenciales
nuevas); la API de Dokploy/EasyPanel queda como segunda etapa y, si se hace, con una clave **dedicada** de un usuario Member recortado, nunca la del MCP, y
comprobando al dar de alta que una ruta de escritura responde 403. **D-B** el ciclo de servidores tendrá un modo headless `--demonio` que sirve para
Windows (servicio o tarea programada) y Linux. **D-C** los nombres de recursos se guardan como datos personales (etiqueta clasificada, retención y borrado al
quitar el servidor; se revierte el no objetivo «no guardar nombres de contenedores» de `fase3-diseno.md`). **D-D** certificados no válidos de los paneles se
rechazan; la huella SPKI fijada es opcional y posterior. **D-E** `recoger` v2 también en servidores de clientes (mismos privilegios); `recoger-bd` solo en los
propios o con autorización escrita. Kubernetes queda fuera del plan (el modelo es genérico). Servidores remotos de Dokploy: `servidor_panel_id` nullable desde 3.1.

Orden: **3.0** (portabilidad completa con ejecutor, retención fuera de la base) → **3.1** (núcleo SQLite extraído y parametrizado por esquema; esquema genérico de
recursos desde el primer día; aceptados explícitamente los cambios de la revisión de 3.1: `ok_en`, registrar fuera del cerrojo, `caduca_en` calculado,
`horas_vista`, `ips_conocidas` fuera, copia premigración sin IPs, 90 días como máximo) → **3.3a** (`recoger` v2 + `docker_ssh` + caducidad de certificados) →
**3.2** (API Dokploy, condicionada a D-A: solo `settings.health`, `overview.services`, `overview.backups`, `docker.getServerHealth`; historial propio de
«última copia buena» por servicio, histéresis y desaparición tras dos ciclos, cabecera `Date` del panel como reloj) → **3.5** (reglas honestas: el estado vivo sale
de contenedores, no de `done`) → **3.4** (EasyPanel tras exploración propia) → **3.6** (pestaña) → **3.3b** (`recoger-bd`, condicionada).

**Avance de 3.0 (2026-10-14):** hechos y probados en macOS: cerrojo portable (`plataforma.bloqueo_de_fichero`: `flock` y `msvcrt.locking` con tiempo máximo),
`matar_arbol`/`grupo_nuevo_kwargs` en lugar de `os.killpg`/`start_new_session`, ningún módulo importa `fcntl`/`resource` al cargar (test), exclusión de copias del sistema
verificada (`tmutil` en macOS, «no indexar» en Windows), poda de IPs de la caché a 48 h, purga horaria de las imágenes del desplegable (también en Windows),
`Trabajador(pintar=False)` y `costbar.py --demonio`. **Almacén de secretos (2026-10-15):** `claves.py` con dos almacenes (Llavero de macOS y Administrador de credenciales de Windows por ctypes,
persistencia solo de este equipo), validación común de la clave y 9 tests con un advapi32 simulado. **Pendiente de 3.0:** probar todo en un
Windows real con `--autoprueba-copias` ampliada; el código de Windows está probado con simulaciones, no en el sistema.

**Avance de 3.1 (2026-10-15):** hecho y probado en macOS: núcleo SQLite compartido (`sqlite_base.py`), `servidores.sqlite` con muestras, horas e IPs
(`servidores_datos/historico/lectura/registro/api`), ids estables, políticas `guardar_ips`/`retencion_ips_dias` global y por servidor, registrador
fuera del cerrojo con retroceso, `--olvidar-ips` y endpoints de lectura. Revisión de código independiente en `reports/revision-codigo-servidores-historico-261015.md`:
tres altas y las medias M2–M5 corregidas con tests (marcas de un reloj adelantado, lectura con la política completa, copia previa en memoria sin IPs en
disco). **No se hace en 3.1:** M1 (el recorte a 4 IPs ocurre en el script; se resuelve con `recoger.sh` v2 en la 3.3a), las tablas de recursos/eventos
(migración numerada en la 3.3a) y la pantalla y los ajustes de UI para `guardar_ips` (3.6).
