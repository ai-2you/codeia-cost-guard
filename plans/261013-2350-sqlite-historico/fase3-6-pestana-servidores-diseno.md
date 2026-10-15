# Fase 3.6 — Pestaña «Servidores» del panel web

## Resultado esperado
Una pestaña nueva en el panel (junto a Panel, Histórico y Ajustes) donde el dueño ve de un servidor, sin salir de la aplicación: sus **webs** con estado, código, latencia, disponibilidad observada y certificado; sus **contenedores y servicios** agrupados por proyecto con estado y reinicios; y una **línea de tiempo** de lo que ha pasado (web caída o recuperada, contenedor que desaparece o entra en bucle). Incluye un botón **«Probar aviso»** para comprobar que las notificaciones de escritorio llegan.

## No objetivos
Acciones sobre servidores (reiniciar, desplegar), edición de configuración (sigue en Ajustes), gráficos nuevos (los de carga, memoria y disco ya existen en Histórico), API de paneles (3.2/3.4), IPs de intentos de acceso (siguen en su endpoint con `con_ips=1`), exportación.

## Datos (todo ya existe en `servidores.sqlite`; solo lectura)
- Webs: `seguimiento.lista()` (estado confirmado, código, ms, certificado, disponibilidad 24 h / 7 d / 30 d) y `seguimiento.eventos()`.
- Contenedores y servicios: tabla `recursos` (tipo, etiqueta, estado, salud, reinicios, activo, primera/última vez) agrupada por proyecto con el prefijo de Swarm/Compose; `eventos` para la línea de tiempo.
- La línea de tiempo mezcla `eventos_web` y `eventos` de los últimos 14 días, más recientes primero, con tope.

## Contrato nuevo
- `GET /servidores/detalle?servidor=<id 32 hex>` → `{disponible, webs:[…], recursos:[…], eventos:[…], generado_en}`. Parámetro único, validado; sesión y Host como el resto; conexión `mode=ro`; sin IPs. Los nombres de contenedores y las URLs son los que el dueño ya ve en el desplegable y en Ajustes.
- `POST /servidores/avisos/probar` (sin cuerpo útil, solo JSON con Origin exacto) → `{ok}`; envía una notificación fija y genérica por `plataforma.avisar`; como mucho una cada 10 s.
- Pestaña: HTML construido por DOM (nunca `innerHTML` con datos), un `fetch` al abrir y al cambiar de servidor, recarga a mano, selector de servidor tomado de `/ajustes` (nombre + id).

## Aceptación
1. La pestaña lista los servidores configurados y, al elegir uno, enseña webs, recursos agrupados y línea de tiempo con datos reales.
2. Todo dato de servidor se pinta por DOM y se acota (longitud, número de filas); un nombre hostil no ejecuta nada ni rompe el diseño.
3. Servidor sin historial, base ausente o ocupada: mensaje claro, sin error 500.
4. `Probar aviso` avisa en macOS y dice claramente cuándo no ha podido (Windows/permiso); no se puede usar para spamear.
5. Suite completa verde, revisión de código independiente.
