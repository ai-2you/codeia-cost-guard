# Fase 3.5 — Avisos de servidores

## Resultado esperado
Una notificación de escritorio (macOS; en Windows queda solo visual, como los presupuestos) cuando algo de un servidor necesita atención, sin ruido: cada situación avisa **una vez**, vuelve a armarse solo cuando se ha resuelto de verdad, y el dueño puede apagarlo todo (`avisos_servidores: false`) o apagarlo en un servidor (`avisos: false` en ese servidor, p. ej. los de clientes).

## No objetivos
Previsión de disco (rechazada en esta fase: un `prune`/`build` produce picos y una recta sobre 7 días da falsos positivos; el umbral fijo cubre el caso real), «copia sin hacer» (no hay fuente: depende de la API de Dokploy, 3.2, condicionada), correo/Telegram, pantalla de reglas (3.6), acciones sobre servidores.

## Reglas (todas leen `servidores.sqlite`, solo lectura; nada nuevo en el servidor)
| Regla | Condición | Rearme |
|---|---|---|
| `web_caida` | web en estado confirmado `caida` desde ≥ 5 min **y** con medida de hace ≤ 15 min | la web vuelve a `ok`/`mal` ≥ 5 min |
| `sin_conexion` | servidor con `intento_en` reciente (≤ 15 min), `ok_en` de hace ≥ 15 min y `ok_en` > 0 (se excluyen errores de configuración: falta de clave, autenticación, huella) | lectura buena ≥ 10 min |
| `disco_lleno` | última muestra buena (≤ 15 min) con `disco_pct` ≥ 90 | `disco_pct` < 85 en la última muestra |
| `certificado` | última muestra buena con `cert_dias_min` ≤ 7 (o caducado) | `cert_dias_min` > 14 |
| `bucle` | evento nuevo `reinicios_en_bucle` en `eventos` (por evento, no por estado) | no aplica: cada evento nuevo avisa, máximo 1 por servidor y hora |

## Cómo avisa
- Un único módulo `servidores_avisos.py`, llamado desde el hilo trabajador de la barra una vez por ciclo de servidores (igual que `presupuestos.vigilar`); es el único que escribe `DATOS/avisos_servidores.json` (0600, escritura atómica).
- Estado por clave `regla|id de servidor[|id de web]`: `desde` (primer instante visto con la condición), `avisado` (ts), `libre_desde` (primer instante visto sin la condición). Se poda lo que ya no existe en la configuración.
- Texto **sin nombres, dominios ni IPs** (se ve en la pantalla de bloqueo): «Una web de uno de tus servidores lleva más de 5 minutos caída», «Un servidor no responde desde hace más de 15 minutos», etc. Con varios avisos en el mismo ciclo se agrupan en una notificación («3 avisos de servidores»).
- Tope global de 6 notificaciones por hora; lo que excede se anota en el estado y se resume en la siguiente.
- Un Mac que acaba de despertar no genera avisos viejos: si el último ciclo bueno del hilo fue hace > 10 min, ese ciclo solo actualiza el estado (`desde`/`libre_desde`) y no avisa.
- Nada se avisa en el primer ciclo tras instalar (línea base): las condiciones ya presentes se anotan como avisadas.
- Notificar nunca bloquea ni rompe el ciclo: todo bajo `try`, un solo `log()` sin valores.

## Aceptación
1. Cada regla avisa una vez al cumplirse la condición y no repite mientras dure; tras resolverse el tiempo de rearme, avisa de nuevo.
2. Un parpadeo (condición que aparece y desaparece antes del tiempo de confirmación) no avisa.
3. `avisos_servidores: false` o `avisos: false` en el servidor silencia, y reactivarlo no dispara avisos retroactivos de lo que ya estaba.
4. El texto de la notificación no contiene ningún nombre, URL ni IP (test con datos marcados).
5. Mac recién despertado, base ausente o corrupta, fichero de avisos hostil: sin avisos y sin excepciones.
6. Suite completa verde; revisión de código independiente; mutaciones.

## Cambios tras el pre-mortem y el red team (`../reports/premortem-redteam-fase3-5-261015.md`)
Implementado así: conexión y disco salen de la caché de servidores, webs y bucles de la base; certificado por web (clave hash); cada regla da sí/no/no sé; duración, rearme y enfriamiento por regla (tabla `REGLAS`); `avisado` solo si macOS aceptó la notificación (3 intentos cada 5 min); interruptor apagado evalúa en silencio; ventana `max(15 min, 3 × intervalo + 60 s)`; canario de red del Mac para `sin_conexion`; reposo detectado con reloj de pared menos monotónico (las confirmaciones empiezan de nuevo); línea base = primera pasada con algún dato; fichero corrupto se aparta; poda con 24 h de gracia; nueva regla `acceso_rechazado`; el texto agrupado nombra clases de regla, no servidores. Límites asumidos: sin «Probar aviso» en Ajustes (queda para la 3.6), un solo proceso escritor (sin cerrojo de fichero), y el hueco de un servidor comprometido que falsea a la baja.
