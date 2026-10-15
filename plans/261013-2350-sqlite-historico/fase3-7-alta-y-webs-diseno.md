# Fase 3.7 — Alta guiada de servidores desde Ajustes y seguimiento de las webs que corren en ellos (diseño v2, tras pre-mortem y red team)

Estado: **diseño v2, listo para codificar por entregas**. Fecha: 2026-10-15. Sustituye al borrador v1 con `../reports/premortem-fase3-7-261015.md` (C1–C5, A1–A8, M1–M9) y
`../reports/redteam-fase3-7-261015.md` (H0–H23). Petición del dueño: (1) dar de alta un servidor por SSH desde la página de configuración; (2) que la app **detecte las webs
levantadas** y las vigile (caída, 200, 500…). Decisiones del dueño (2026-10-15): **primero sin contraseña en la app** (la contraseña en la página queda como posible mejora posterior,
con pseudoterminal y no con FIFO/askpass). Parte de D-A (sin API de paneles) y de la 3.3a.

## Resultado buscado
1. **Alta guiada sin contraseñas en la app**: un diagnóstico de solo lectura del servidor, un alta con la clave SSH del dueño cuando el usuario es root o tiene `sudo` sin contraseña, y para
   los demás casos la línea exacta para pegar en el Terminal más un botón «Comprobar conexión».
2. **Webs detectadas**: el servidor dice qué dominios sirve su proxy; la app **propone** los que apuntan a ese servidor, el dueño marca los que quiere y solo esos se vigilan.
3. **Seguimiento por web**: arriba / responde con error / caída, último código y latencia, eventos y resumen por hora y por día (disponibilidad observada).

## No objetivos
Guardar ni manejar contraseñas (ni en disco, ni en Llavero, ni en logs, ni en la app) · actuar sobre el servidor más allá de instalar los dos scripts de lectura y el usuario `infobar` ·
vigilar webs sin que el dueño las confirme · seguir redirecciones · medir contenido · Windows en la primera entrega (solo la línea de Terminal en PowerShell).

## Fundamentos (bloquean todo lo demás)
- **F1 · Guardado por revisión (C1).** `GET /ajustes` devuelve `servidores_rev` (hash de la lista de servidores sin secretos). `POST /guardar` debe traerla: si no coincide con la actual,
  **409** («la lista de servidores cambió: recarga la página») y no se guarda nada. Las altas, las bajas y la aceptación de webs son operaciones **por `id`** dentro del cerrojo de la
  configuración (leer–modificar–escribir), nunca «sustituir la lista». Test: alta + guardar con la copia vieja → 409 y el servidor sigue.
- **F2 · Campos y secretos (C2).** Los servidores aceptan una **lista cerrada de campos** (`nombre, ssh, puerto, id, webs, panel, panel_url, guardar_ips, retencion_ips_dias, detectar_webs`);
  cualquier otro (p. ej. `contrasena`) invalida la configuración y no toca el disco. `SECRETOS` incorpora `contrasena`, `contraseña`, `passwd`, `pass`. Test: `/guardar` con una contraseña
  en un servidor → 400 y `config.json` byte a byte igual.
- **F3 · Sondeador de webs aparte (C3, H15, H19).** Las webs dejan de medirse dentro de `recoger_uno`: un módulo propio (`webs_sondeo`) con **pool de 4**, **presupuesto global por pasada
  (60 s)**, caché propia (`logs/webs.json`) y calendario por web (5 min; 60 s mientras una web está en primer fallo o caída, con tope de 20 aceleradas). El ciclo SSH lee la caché de webs, no sondea.
  **Canario de red**: si en una pasada fallan por red (DNS/conexión) ≥ 3 webs y también fallan los tres canarios TCP públicos (1.1.1.1:443, 8.8.8.8:443, 9.9.9.9:443), la pasada es «sin red»: no se
  cambia ningún estado ni se anota ningún evento. `ip_no_publica` rechaza también `64:ff9b::/96` (NAT64) y cualquier IPv6 con IPv4 embebida privada.
- **F4 · Una web caída no acelera SSH (A6).** La tarjeta de webs cuenta para el estado global y el icono, pero **no** para `estado_vivo` (como `mantenimiento` y `certificado`).

## 3.7a — Alta guiada sin contraseña
### Flujo (todo con la puerta del panel: sesión, Host/Origin, JSON, tamaño; respuestas con `id` de alta y mensajes de un **conjunto cerrado**)
1. **Diagnóstico de solo lectura** (`POST /servidores/alta/diagnostico`, 1 conexión SSH con `orden_ssh` endurecida y `BatchMode`): alcanzable, huella conocida, autenticación con la clave del dueño,
   ¿root o `sudo -n true`?, distribución (Debian/Ubuntu con systemd; Alpine/RHEL/sin sudo → «no soportado todavía»), `AllowUsers/AllowGroups`, ¿ya existe `infobar`?
   Cada resultado es una de estas respuestas: *listo para instalar con tu clave* · *necesita contraseña de sudo: usa el Terminal* · *no entra con clave: usa el Terminal* · *no soportado* · *huella
   desconocida* · *la huella cambió*.
2. **Huella (TOFU) sin atajos (H7, H8, M3).** Se mira primero el `known_hosts` por defecto (el mismo que usa el monitor) con `ssh-keygen -F`. Si el host no está: **una** conexión
   `ssh-keyscan -t ed25519`, huella SHA256 en pantalla y, junto a ella, la orden para sacarla en el propio servidor (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`); solo con «Es esta» se añade.
   Nunca hay un botón para sustituir una huella: si cambia, el alta se aborta con instrucciones.
3. **Instalación con la clave** (solo si el diagnóstico dijo «listo»): ≤ 3 conexiones en total (preparar, copiar con `scp`, instalar). Usa la **misma lista cerrada de opciones** que `orden_ssh`
   también en `scp` y en la prueba final (H9). La clave pública se genera con comentario fijo y se valida con regex en el Mac antes de componer nada; se pasa al servidor por stdin.
   Después, prueba como `infobar` (BatchMode, `### version`), y **solo entonces** se guarda el servidor (por `id`, F1). Cualquier fallo no deja nada en la configuración.
4. **Terminal**: la página genera la línea con `alta-servidor.sh` (que ya existe) y un botón «Comprobar conexión» (otra lectura como `infobar`); al comprobar, guarda el servidor.
5. **Cierre de fallos**: un alta a la vez; tras un fallo, enfriamiento de 60 s; ningún reintento automático; presupuesto de conexiones por alta visible en el diseño (A2).
6. **Desinstalar (A3)**: `infobar/remoto/desinstalar-del-servidor.sh` (quita usuario, sudoers, clave y scripts) y su explicación en la página.
### Peor caso aceptado
Un servidor con `AuthenticationMethods` raros o bastión/ProxyJump: se dice «no soportado» antes de tocarlo; nada queda a medias.

## 3.7b — Detección de webs
- **En el servidor** (`recoger-root.sh`, sección `webs` con marco y presupuesto propio como `inventario`): de las etiquetas de Traefik de servicios Swarm y contenedores (`{{json ...Labels}}`
  filtradas) y de `/etc/dokploy/traefik/dynamic/*.yml` y las rutas de EasyPanel/Coolify solo salen los nombres dentro de `Host(...)`. Dos pasos de `grep -o` (primero ``Host\([^)]*\)``, luego
  los literales entre comillas inversas), todo a minúsculas, sin comodines, y ningún otro byte de esos ficheros (pueden tener hashes de autenticación) sale del proceso. Máximo 200, `truncado` declarado.
- **En el Mac**: validación estricta (etiquetas DNS, ≤ 253, sin IP ni `localhost`, `xn--` se enseña decodificado y marcado) y **resolución**: se proponen **primero** los dominios que resuelven a la IP
  pública del servidor (H13); los que apuntan a otro sitio (CDN, otro servidor) van en una segunda lista, plegada y sin marcar, con aviso. Siempre https (D-D). Nada se sondea hasta que el dueño marca.
- Por servidor, `detectar_webs` (por defecto sí; el dueño lo apaga en servidores de clientes, D-E). Las candidatas viven solo en la caché 48 h (datos personales).
- **Aceptar/quitar** por `id` (`POST /servidores/webs` {servidor, aceptar[], quitar[]}). Quitar una web **borra de inmediato** su estado, eventos y resúmenes (H21). Hasta 100 por servidor.
- Una web que deja de aparecer en la detección se avisa («ya no aparece en el servidor»); no se borra sola.

## 3.7c — Seguimiento
- **Medida**: `HEAD` y, si responde 405/501, `GET` con `Range: bytes=0-0`. Clases: **ok** (200–399, y 401/403: la app está viva), **mal** (resto de 4xx, entre ellos el 404 de Traefik cuando un host ya no
  tiene router, y lentas > 1,5 s), **caída** (5xx, timeout, fallo de conexión/DNS, TLS inválido). Sin seguir redirecciones; el 3xx cuenta como ok.
- **Máquina propia por web** (C4): una web pasa a caída tras **dos sondeos seguidos** fallidos (separados ≥ 60 s); vuelve a ok con un sondeo bueno; `ok→mal` también se confirma con dos. Línea base la primera
  vez. El «Mac sin red» (F3) no cuenta. Presupuesto de eventos **por web** (20 al día, el primer `cayo` del día siempre cabe) más un tope de 1000 al día por servidor, **separado** del de los contenedores (H18, M7).
- **Almacén (migración 5, solo tablas nuevas; no se reconstruye nada)**: `webs(servidor_id, clave, etiqueta, activo, estado, codigo, ms, cert_dias, desde, …)`, `eventos_web(servidor_id, ts, clave,
  evento, codigo)` con eventos `cayo`, `volvio`, `degradada`; `horas_web` (90 días) y `dias_web` (permanente) para la disponibilidad observada (M6). Los huecos con el Mac apagado son «sin dato», no caída.
  El dominio (etiqueta) es **dato personal**: misma clase, retención y borrado que los nombres de recursos; la copia previa a migrar no lo lleva.
- Los avisos (notificación de escritorio de «web caída desde hace N minutos») van en la 3.5.

## Orden de entrega (cada paso con revisión de código independiente y mutaciones)
**F1+F2** (guardado seguro) → **F3+F4** (sondeador) → **3.7a** alta → **3.7b** detección → **3.7c** seguimiento.

## Preguntas resueltas
Contraseña en la app: no, por ahora (decisión del dueño). Tablas de webs propias (no se amplía `recursos`). Siempre https. Distribuciones soportadas al inicio: Debian/Ubuntu con systemd y sudo.
Pendiente de comprobar a mano (supuestos del red team): `UpdateHostKeys`, rutas de Traefik de EasyPanel y Coolify, y qué devuelve el proxy de Dokploy a `HEAD /` de un host sin router.
