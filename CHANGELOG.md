# Historial de cambios

Las versiones anteriores a la 0.3.0 están en las etiquetas de git (`v0.1.1`, `v0.1.2`, `v0.2.0`).

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
