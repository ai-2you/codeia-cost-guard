# Fase 3 ampliada — monitorización completa de servidores, paneles, servicios y bases de datos (plan v1, pendiente de pre-mortem y red team)

Estado: **plan v1 sin código**. Fecha: 2026-10-14. Sustituye el alcance de `fase3-diseno.md` (historial de servidores e IPs): ese diseño y su
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
| Fase | Contenido | Depende de |
|---|---|---|
| **3.0** | Portabilidad Windows del ciclo y de los secretos; retención fuera de la base (caché 48 h, PNG, exclusión Time Machine real/Windows) | — |
| **3.1** | `servidores.sqlite`, historial de host e IPs (diseño revisado), dedupe por `ok_en`, `id` estable, `guardar_ips` | 3.0 |
| **3.2** | `docker_ssh` y `recoger` v2: inventario y salud de contenedores y servicios, eventos de aparición/desaparición | 3.1 |
| **3.3** | `bd_local` (`recoger-bd`): bases de datos en el propio servidor, sin credenciales en el Mac; certificados | 3.2 |
| **3.4** | `panel_api` Dokploy (solo lectura, lista blanca) y luego EasyPanel: despliegues y copias programadas | 3.2 |
| **3.5** | Reglas, previsión de disco y avisos | 3.1–3.4 |
| **3.6** | Pestaña Servidores completa, árbol y línea de tiempo | 3.5 |

## Preguntas abiertas para el dueño
1. ¿Qué paneles usas de verdad en tus servidores y en los de clientes: solo Dokploy y EasyPanel, o también otros (Coolify, Portainer, CapRover…)?
2. Dijiste «cualquier servicio orquestado, igual que Google»: ¿te refieres a **Kubernetes**? ¿Algún servidor tuyo lo usa hoy?
3. ¿Qué motores de bases de datos hay y cuáles importan más? (PostgreSQL, MySQL/MariaDB, MongoDB, Redis, otros). ¿Corren en contenedores o instaladas
   directamente en el servidor?
4. Las claves API de los paneles suelen **poder escribir** (no existe «solo lectura» salvo con roles de pago en Dokploy). ¿Prefieres
   empezar sin API de panel (todo por SSH y Docker, sin credenciales nuevas) y dejar la API como opción posterior con riesgo aceptado?
5. ¿Aceptas instalar scripts nuevos de solo lectura en los servidores (`recoger` v2 y `recoger-bd`), también en los de clientes, o en esos
   solo lo que ya existe?
6. ¿Orden de prioridad? Propuesta: 3.0 → 3.1 → 3.2 (contenedores) → 3.3 (bases de datos) → 3.4 (panel) → 3.5 → 3.6.

## Verificación
Cada fase: pre-mortem y red team independientes antes de escribir código; tests con servidores, paneles y bases de datos **simulados y hostiles**
(respuestas gigantes, JSON con tipos cambiados, etiquetas con control y HTML, IPs privadas, cardinalidad explosiva, relojes del futuro);
borrado verificado leyendo los bytes del fichero; mutaciones sobre los filtros de seguridad; prueba en Windows con `--autoprueba` ampliada; y
revisión independiente del código con foco en privacidad y en mínimo privilegio.
