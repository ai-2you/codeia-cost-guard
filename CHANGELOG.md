# Historial de cambios

Las versiones anteriores a la 0.3.0 están en las etiquetas de git (`v0.1.1`, `v0.1.2`, `v0.2.0`).

## 0.5.0 — 2026-10-16

Esta versión añade la bandeja de Windows, a partir de la contribución de @ai-2you (PR #6), integrada con el motor actual.

### Windows
- **Icono en la bandeja del sistema** (`bandeja_windows.py`): hace lo mismo que la barra de macOS salvo el desplegable con imágenes
  (ingesta, copias cifradas, servidores, presupuestos, avisos y panel local), con el mismo hilo trabajador. El icono es un anillo con
  la parte consumida de las 5 h; las cifras salen al pasar el ratón y en el menú del clic derecho.
- **Panel en una ventana nativa** (WebView2) junto al icono, sin abrir el navegador: se coloca bien con cualquier escala de pantalla,
  se cierra al pulsar fuera (no con sus propios diálogos), oculta no recarga el panel y Alt+F4 la oculta sin perderla. Si WebView2
  no está, cae a Edge en modo aplicación o al navegador.
- **Una sola bandeja por sesión**, y el servidor local pide su puerto en exclusiva: dos copias ya no se reparten las conexiones.
- **Instalador** (`instalar_windows.py`): copia lo mismo que `install.sh`, crea su entorno con `pystray`, `pywebview`, `pythonnet`
  y `cryptography`, registra el arranque con la clave `Run` del usuario (sin administrador, sin las condiciones de batería de una
  tarea programada) y arranca la bandeja.
- `ssh` y `taskkill` se lanzan sin ventana de consola.

### Correcciones
- Las rutas con `/` y `\` mezcladas (lo que devuelve `glob` en Windows) ya no pierden el proyecto ni el plan en el lector antiguo
  ni en la comparación en sombra.
- El panel recuerda el tipo de gráfica elegido entre recargas, y los modelos de cada plan pasan al título de la fila.
- `requirements.txt` instala `pyobjc` solo en macOS y las dependencias de la bandeja solo en Windows.
- `.gitattributes` normaliza los finales de línea a LF.

### Seguridad
- La ventana del panel solo carga el panel local o contenido HTML: nunca una dirección que haga a pywebview levantar su propio
  servidor web (sin sesión). Un test lo vigila.
- Cuando la bandeja cae al navegador, el ticket de entrada va en la línea de órdenes del navegador; en Windows solo la leen tus
  propios procesos y los administradores, y el ticket es de un solo uso y caduca a los 60 s.

### Límites conocidos
- **Sin probar en un Windows real** tras la integración: la escala de pantalla, Alt+F4, los diálogos y el arranque están razonados
  sobre el código de pywebview 6.2.1 y pystray 0.19.5, y probados con tests en macOS.
- Los avisos de escritorio todavía no salen en Windows (solo se ven en el panel); OpenCode sigue sin leerse en Windows.

## 0.4.0 — 2026-10-16

Esta versión añade la lectura opcional de la API de Dokploy: copias de seguridad, despliegues y el árbol real de proyectos.

### Servidores
- **API de Dokploy (opcional, solo consultas).** Con `panel: "dokploy"`, `panel_url` (https con nombre público) y la clave guardada en el Llavero desde Ajustes, un hilo propio lee cada 5 minutos
  cinco rutas `GET` de Dokploy (0.30.0 o posterior): el árbol de proyectos y aplicaciones con el estado de su último despliegue, los dominios y las ejecuciones de copia. Un panel lento o caído nunca
  toca el ciclo SSH ni fabrica un «sin conexión».
- **Última copia buena** de cada base de datos y volumen, guardada en la base (tabla nueva `copias`, migración 6, sin reconstruir nada): aprende la cadencia de cada copia, de modo que una semanal no
  salta a los 3 días, y sobrevive a la rotación de ejecuciones del panel. La copia de la base de datos del propio Dokploy cuenta como una más.
- **Tres avisos nuevos:** copia antigua (solo de las que tuvieron una buena y la perdieron), despliegue fallido (entre 10 minutos y 7 días) y panel sin lectura (clave caducada o rechazada, tras 6 horas),
  con línea base por panel: lo que ya estaba mal en la primera lectura no avisa.
- **Pestaña Servidores del panel:** secciones «Copias de seguridad» y «Proyectos del panel» (con las webs vigiladas enlazadas a su aplicación). El desplegable de la barra añade los motivos «N copias de
  seguridad sin hacer» y «N aplicaciones con el último despliegue fallido».
- **Ajustes:** campo de clave de miembro de Dokploy con un texto honesto sobre lo que puede esa clave, y botón «Probar conexión» que dice la versión y cuántas cosas ve sin enseñar nombres. EasyPanel queda
  indicado como «sin API» (no tiene clave de solo lectura).
- Dos servidores no pueden compartir el mismo `panel_url`, y quitar un servidor o cambiar su panel borra su clave del Llavero y su caché.

### Seguridad
- La garantía de solo lectura está en el código, no en la clave (Dokploy solo ofrece claves de solo lectura con licencia Enterprise, así que la de un miembro puede escribir): el cliente tiene una única llamada
  HTTP, de método GET y a cinco rutas fijas, y un test recorre el código y falla si aparece cualquier otra forma de escribir.
- Solo https con certificado válido, sin redirecciones ni proxy, IP pública fijada tras resolver, plazo total real y topes de tamaño; lista blanca de campos con estados cerrados (nada de `env`, contraseñas ni iconos);
  la clave nunca sale en un error ni en un log y sin clave guardada no se hace ninguna petición.
- Los nombres de proyectos, servicios y dominios del panel no viven en la caché más de 48 horas sin una lectura buena, y las copias se borran con el servidor y con `--olvidar-recursos`.

### Límites conocidos
- Pendiente de la primera prueba contra un Dokploy real: los esquemas salen del código fuente oficial.
- EasyPanel y Coolify siguen sin API.
- La clave de un miembro de Dokploy puede desplegar, parar y borrar copias en los servicios que se le asignen; cualquier programa que se ejecute con tu usuario puede leerla del Llavero.

## 0.3.0 — 2026-10-16

Esta versión añade una base de datos permanente de consumo, copias cifradas, histórico y presupuestos, y convierte el monitor de servidores en una herramienta completa:
alta guiada, seguimiento de webs, contenedores, avisos de escritorio y una pestaña propia en el panel.

### Consumo de tokens
- **Base SQLite permanente** (`consumo.sqlite`) con los turnos de Claude Code, Codex, pi, OpenCode y Hermes, de escritor único y con las comprobaciones de integridad de SQLite activas.
  El informe se lee de la base en solo lectura y hay una comparación «en sombra» con el lector anterior (`almacen: sombra` por defecto; pasar a `activo` es decisión del dueño).
- **Conteo corregido:** pi ya no suma `reasoning` dos veces, Codex se lee por `token_usage_record`, OpenCode lee sus dos bases y suma su `reasoning`, y Hermes incluye su base raíz.
- **Copias cifradas** (`.cgx`): formato propio con claves del destinatario, firma del dispositivo, rotación, restauración en equipo nuevo y estado en Ajustes.
- **Histórico** por día, semana y mes locales en el panel, con exportación a CSV (fórmulas neutralizadas, fichero privado y cifrado opcional).
- **Presupuestos** por proyecto y por plan, con avisos de color en Ajustes y notificación del sistema en macOS.
- El panel escapa los nombres que vienen de los logs y se sirve con política de contenido (CSP) y nonce.

### Servidores
- **Historial** permanente en `servidores.sqlite`: muestras, resumen por hora, IPs de intentos de acceso con caducidad y retención configurable.
- **Lectura v2 del servidor:** protocolo con marcos de sección, presupuesto de tiempo medido, intentos de acceso filtrados por unidad de systemd e inventario de contenedores
  (Docker, Compose y Swarm) con sus eventos de aparición, desaparición y bucle de reinicios.
- **Webs:** sondeo en un hilo propio con canario de red, estado confirmado (ok, con error o caída), código, latencia, certificado y disponibilidad observada por hora y por día.
  Detección de las webs que sirve el proxy de cada servidor (Traefik de Dokploy, EasyPanel, Coolify) y selección por el dueño.
- **Alta guiada** desde Ajustes sin contraseñas en la aplicación: diagnóstico de solo lectura, huella con confirmación, instalación con tu clave SSH o línea de Terminal, y script de desinstalación.
- **Avisos de escritorio:** web que no responde, servidor sin conexión, acceso rechazado, contenedor en bucle, disco casi lleno y certificado a punto de caducar,
  con confirmación, rearme, enfriamiento, línea base y silencio global o por servidor. El texto nunca lleva nombres ni dominios.
- **Pestaña «Servidores» del panel:** webs (con enlace), contenedores y servicios agrupados, línea de tiempo de 14 días y botón «Probar aviso».
- **Desplegable de la barra:** lista todas las webs con su estado, explica por qué un servidor no está en verde, pliega las secciones pesadas, se desplaza si es más alto que la pantalla
  y se cierra al pulsar sus botones. El botón Guía abre la guía como página del panel.
- **Ajustes:** las webs a medir se editan como píldoras con autocompletado, y la lista de servidores se guarda con revisión para no pisar un alta hecha entre medias.

### Portabilidad
- Cerrojos y procesos hijos sin `fcntl` ni `killpg`, exclusión de copias del sistema (Time Machine y equivalente en Windows) y almacén de secretos de Windows
  además del Llavero de macOS. Probado con simulaciones; falta una prueba en un Windows real.

### Seguridad
- Sesión obligatoria en el servidor local, permisos privados, cookie por puerto y ticket de entrada fuera de la lista de procesos.
- Los datos de un servidor se tratan como hostiles: se acotan, se descartan los inválidos y los nombres personales (webs, contenedores) se borran al dejar de vigilar.

### Límites conocidos
- No hay previsión de disco ni aviso de «copia sin hacer» (necesita la API del panel).
- La API de Dokploy y EasyPanel, y la lectura de bases de datos, quedan para una versión posterior.
- Un servidor comprometido puede mentir a la baja sobre disco y contenedores.
