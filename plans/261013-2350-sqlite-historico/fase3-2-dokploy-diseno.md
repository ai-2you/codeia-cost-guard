# Fase 3.2 — Colector de la API de Dokploy (solo consultas)

## Resultado esperado
Para un servidor con `panel: "dokploy"`, CostGuard lee cada pocos minutos, con la clave que el dueño guardó en el Llavero, **tres cosas que por SSH no se saben**: (1) el árbol real proyecto → aplicación / compose / base de datos con el estado del último despliegue; (2) los dominios de cada aplicación, para colgar de ella las webs vigiladas; (3) las ejecuciones de copias de seguridad, para saber la **última copia buena de cada base de datos y volumen** y avisar de «copia sin hacer» y de «último despliegue fallido».

## Decisión del dueño (2026-10-16) y su coste
Dokploy se usa con una **clave de miembro limitada a los proyectos que se le asignen**; EasyPanel queda sin API (por SSH); Coolify, diseñado sin activar. Esa clave **puede escribir** (desplegar, parar, borrar copias): no hay clave de solo lectura sin licencia Enterprise. Por eso la garantía de solo lectura pasa a estar **en el código**, no en la clave: el cliente solo sabe hacer `GET` a una lista cerrada de cuatro rutas, no existe ninguna función que haga otra cosa, y un test falla si aparece una llamada de escritura. Ajustes dirá la verdad: «Clave de miembro de Dokploy: CostGuard solo hace consultas, pero la clave puede más; si alguien la saca de tu Llavero podría usarla».

## No objetivos
EasyPanel y Coolify, acciones sobre el panel, logs de despliegues, configuración de copias (`backup.one` trae contraseñas), variables de entorno, `project.all` y cualquier ruta `*.one` (traen secretos), historial completo de despliegues.

## Contrato con Dokploy (investigación: `../reports/investigacion-apis-paneles-261016.md`)
Cabecera `x-api-key`. Cinco `GET` sin parámetros por ciclo: `settings.health` (público), `settings.getDokployVersion`, `overview.services`, `overview.domains`, `overview.backups`. Desde Dokploy v0.30.0. Sin paginación, hasta 500 ejecuciones de copia. Lista blanca de campos por ruta con tipos y estados de un conjunto cerrado; `icon` y todo lo demás se descarta.

## Cliente
- Solo `https` con certificado válido y a **`panel_url` exacto** (sin seguir redirecciones, host fijado al resolver, IP pública o la del servidor), tiempo máximo corto y tope de bytes por respuesta (2 MB); cabeceras fijas.
- Lista cerrada de rutas permitidas en una constante; el método es siempre GET y se afirma en el propio cliente.
- La clave se lee del Llavero en cada pasada, no se guarda en memoria entre ciclos, no se loguea ni sale en ningún error (mensajes cerrados).
- Un 401 se trata como «clave rechazada» (no se reintenta hasta que el dueño la cambie, igual que los errores de autenticación SSH).

## Datos
- Se guarda en la caché de servidores (`entrada["panel"]`): versión, hora de la lectura, proyectos y recursos validados, dominios y últimas ejecuciones de copia por recurso. Nada de lo que no esté en la lista blanca.
- **Tabla nueva `copias` (migración 6, solo tablas nuevas)**: por servidor y recurso (clave hash del id del recurso), tipo, etiqueta (dato personal, mismas reglas que `recursos`), `ultima_ok`, `ultimo_estado`, `ultimo_ts`, `primera`. Sirve porque Dokploy solo conserva las 10 últimas ejecuciones por configuración: la «última buena» tiene que sobrevivir a la rotación. Se borra con el servidor, con `--olvidar-recursos` y al dejar de existir el recurso (30 días).
- Nombres, dominios y proyectos son datos personales: nunca en logs ni en la notificación.

## Avisos nuevos (se integran en `servidores_avisos`)
`copia_antigua`: una base de datos o volumen sin copia buena desde hace más de 48 h (o sin ninguna y vista hace más de 24 h); `despliegue_fallido`: aplicación cuyo último despliegue terminó en error y lleva así más de 10 min. Ambas con confirmación, rearme, enfriamiento y silencio como las demás; texto genérico.

## Interfaz
- Ajustes: el campo de clave del panel ya existe (cuenta `dokploy:<host>`); se corrige su texto y se añade «Probar conexión» (hace las consultas de lectura y dice versión y cuántos servicios ve, sin enseñar nada más).
- Pestaña Servidores: sección «Proyectos» (árbol del panel con estado, último despliegue y webs colgadas por dominio) y sección «Copias» (última buena por base de datos y volumen, con «sin copia registrada» cuando no hay ninguna). Con frescura como el resto.
- Desplegable de la barra: el motivo «copia sin hacer» entra en «Por qué no está en verde».

## Aceptación
1. Con un panel Dokploy simulado que sigue su contrato, el colector devuelve el árbol, dominios y copias validados; un campo desconocido o secreto no pasa nunca; estados y tipos fuera del conjunto cerrado se descartan y se cuentan.
2. Un test falla si el módulo contiene cualquier método HTTP distinto de GET o una ruta fuera de la lista.
3. Respuesta enorme, JSON hostil, redirección, certificado inválido, 401, 5xx, tiempo agotado y panel caído: ciclo SSH intacto, mensaje cerrado, sin secretos en logs.
4. «Última copia buena» sobrevive a la rotación de ejecuciones y a un reinicio; «copia sin hacer» no avisa de un servicio recién creado.
5. Suite completa verde, revisión de código independiente.
