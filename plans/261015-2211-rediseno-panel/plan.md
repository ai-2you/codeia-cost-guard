# Rediseño del panel de métricas

Estado: **fases 0 a 5 hechas; icono de la barra en curso** · Rama: `feat/rediseno-panel` → PR al repositorio original

`maqueta-panel.html` es el diseño aprobado (se abre en el navegador; datos de ejemplo). `inventario.md` recoge qué
muestra cada vista y de qué dato sale.

## Añadido durante el trabajo

- Títulos de sección fuera de las tarjetas en todas las vistas.
- `/servidores/detalle` devuelve `vivo`: resumen de la última lectura del ciclo (números y estados, sin IPs ni
  usuarios) para las tarjetas de Servidores.
- Icono de la barra (macOS): pastilla con contorno, relleno según el consumo real (cuota de 5 h o ritmo frente al
  límite; ámbar al pasarlo), separador y punto de estado de los servidores. Windows no tiene icono: no le afecta.

## Resultado buscado

El panel web (`http://127.0.0.1:47391`, «Abrir panel») muestra **la misma información** que hoy, pero
reestructurada, con mejores gráficos y con el lenguaje visual del desplegable de la barra (pestaña
Agentes): tarjetas de cristal, punto de estado por tarjeta, cabecera con icono y control segmentado,
píldoras y barras finas. Ajustes cubre lo que hoy todavía obliga a editar `config.json` a mano.

## Restricciones (del código actual, no negociables)

- **Sin dependencias ni red**: los gráficos son SVG/DOM hechos a mano (`graficos.js`). Nada de CDN.
- **CSP estricta** del servidor local: `script-src 'nonce-…'`, sin manejadores en línea (`onclick=`), estilos
  inline permitidos. Todo evento va por `data-a` + delegación, como hoy.
- **Datos incrustados** en `panel.html` vía `__DATOS__` y endpoints del servidor local (`/historico`,
  servidores, ajustes). El rediseño no cambia la forma de los datos, solo cómo se pintan.
- **Escapado** de nombres que vienen de logs (`esc()`/`_sana()`), tema claro/oscuro, versión móvil.
- El original avanza muy rápido (≈80 commits entre v0.2 y v0.4): el PR debe tocar lo mínimo fuera de
  `panel_assets/` para no chocar.

## Fuera de alcance

- El desplegable de la barra (ya está bien) y el motor de cálculo (`infobar/agregacion.py`, lectores).
- Nuevas métricas o fuentes de datos.
- Cambios en el servidor (VPS) o en `servidores.json`/`config.json` personales.

## Criterios de aceptación

1. Las cuatro vistas (Panel, Histórico, Servidores, Ajustes) muestran todos los datos que muestran hoy.
2. Los tokens de color, radios y tipografía son los del desplegable (`desplegable_comun.py`), en claro y oscuro.
3. Funciona a 375 px de ancho sin scroll horizontal de página.
4. Sin errores de CSP en consola; la suite de tests sigue en verde.
5. El diseño se aprobó antes en un Artifact; el código lo replica.

## Fases

| # | Fase | Salida |
|---|------|--------|
| 0 | **Base al día**: avanzar la rama a `origin/main` (v0.4.0, fast-forward), reinstalar, devolver `config.json` del repo al original, crear `feat/rediseno-panel` y sincronizar `main` del fork | Rama limpia sobre lo último |
| 1 | **Inventario**: qué muestra cada vista y de qué dato sale (`app.js`, `historico.js`, `servidores.js`, `ajustes.js`) | Tabla vista → bloque → dato |
| 2 | **Diseño en Artifact**: maqueta HTML autocontenida con datos de ejemplo (nombres de proyectos y dominios ficticios), claro/oscuro y móvil, con las 4 vistas | Enlace para revisar |
| 3 | **Iteración** hasta aprobar el diseño | Diseño aprobado |
| 4 | **Migración**: `estilos.css` (tokens del desplegable), `plantilla.html`, `app.js`/`graficos.js` por vista; mismas funciones de datos | Panel nuevo |
| 5 | **Ajustes**: revisar qué queda solo por terminal/`config.json` (p. ej. `cuotas_mes` y `uso_api` de NaN, `aliases`, `ignorar`, límites de ritmo) y añadirlo si falta | Ajustes completo |
| 6 | **Verificación**: tests, navegador en claro/oscuro/móvil, consola sin CSP | Evidencia |
| 7 | **PR**: primero al fork; luego al original, idealmente tras abrir un issue con capturas para que el autor dé el visto bueno al cambio visual | PR |

## Riesgos

- **Conflictos con el original**: el autor sigue tocando `panel_assets/`. Mitigación: fase 0 justo antes de
  migrar, PR pequeño por vista si hace falta, issue previo.
- **Gusto del autor**: un rediseño visual puede no aceptarse tal cual. Mitigación: issue con el Artifact antes del PR.
- **Datos personales en el Artifact**: se publica en claude.ai (privado, pero fuera del equipo). Mitigación: datos ficticios.

## Arreglo aparte (no es del rediseño)

Los botones **Panel** y **Abrir** del desplegable no hacen nada porque el servidor no tiene `panel_url` ni
`webs` en `~/.cost-guard/servidores.json` (`barra.py` solo abre si existen). Se arregla rellenándolos en
Ajustes → Servidores (en v0.4 con autocompletado de dominios) o en el fichero.

## Preguntas abiertas

- ¿Datos ficticios en el Artifact o prefieres tus números reales con nombres anonimizados?
- ¿Abrimos issue en el original antes de empezar a migrar, o directamente PR?
