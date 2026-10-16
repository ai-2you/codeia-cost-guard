# Inventario del panel (v0.4.0)

Base: `panel_assets/` en `93b83a6`. Qué pinta cada vista y de qué dato sale. El rediseño conserva todo.

## Panel (`app.js`, datos incrustados `D`)

| Bloque | Contenido | Dato |
|---|---|---|
| Filtros | Periodo (Hoy / 7 / 30 días) · Plan (Todos / cada plan) | `D.dias`, `D.proveedores` |
| KPIs grandes | Últimas 5 h (+ % de cuota) · total del periodo · caché leída hoy · días con actividad | `cinco_h`, `limites`, `cache_pct`, `hoy.cr` |
| KPIs detalle | Hoy · Semana · Mes (30 d) con turnos · Ritmo última hora vs límite | `hoy`, `semana`, `mes`, `ritmo` |
| Aplicaciones | Sesiones por app (Claude Code, Codex, OpenCode, pi, Hermes) | `fuentes` |
| Por plan | Tabla ordenable: 5 h, semana, mes, hoy, modelos, etiqueta `auto` | `proveedores` |
| Cuotas mensuales | Modelo, plan, mes, tope, % (en vivo / solo logs) | `proveedores[].modelos_cuota`, `cuota_fuente` |
| Por día | Barras / barras verticales / líneas / calendario; clic o arrastre = detalle | `dias`, `cruce` |
| Por modelo | Barras / tarta / donut + tabla con % y plan; filtros propios | `cruce`, `modelo_prov` |
| Matriz modelo × día | Matriz / barras apiladas | `cruce` |
| Por proyecto | Barras / tarta / donut; filtros propios | `proy_prov` |
| Pie | Nota de turnos, equivalente USD, fuentes | `mes.usd` |

## Histórico (`historico.js`, `GET /historico`)

Rango (30 d / 90 d / 12 meses / todo) · paso (día / semana / mes) · agrupar (sin / proyecto / plan / agente).
Tarjetas: total del rango, periodo anterior, mes en curso (proyección), datos desde. Gráfico de consumo
por periodo y tabla. Botón «Descargar CSV».

## Servidores (`servidores.js`, endpoints del servidor local)

Por servidor: webs vigiladas (estado, código, respuesta, últimas 24 h, certificado), contenedores y
servicios (Compose / Swarm / sueltos, reinicios), línea de tiempo de eventos, panel Dokploy
(despliegues, copias por base de datos y volumen, última lectura buena).

## Ajustes (`ajustes.js`, POST al servidor local)

Claves de API por proveedor (Llavero), cuota en vivo por API, modelos que cuentan, límites 5 h /
semana / mes, cuotas mensuales por modelo, proyectos (renombrar, ocultar), presupuestos, copias
cifradas, servidores (alta guiada con huella, webs con autocompletado, Dokploy y su clave, prueba de
conexión), alerta de ritmo.

**Conclusión para la fase 5:** casi todo lo que antes era terminal ya está en Ajustes. Queda por
comprobar si faltan `aliases`, `ignorar` y `tema`.

## Lenguaje visual de referencia (desplegable, `desplegable_comun.py`)

- Tarjeta `.tj`: cristal (`rgba(255,255,255,.09)` oscuro / `.55` claro), borde `.13`, radio 14, etiqueta con
  icono de trazo + punto de estado a la derecha, cifra 20 px 700.
- Cifra destacada monoespaciada 26 px; color por magnitud: K tenue, M verde terminal, B naranja Opus.
- Sección: 11 px, mayúsculas, `letter-spacing .12em`, nota a la derecha en minúscula.
- Lista `.lista` con filas separadas por borde; barra de 6 px.
- Control segmentado `.seg` para pestañas; píldoras `.pil`; botones de pie de 38 px.
- Estados: verde `#34c759`, ámbar `#ff9f0a`, rojo `#ff453a`, gris `#8e8e93`.
