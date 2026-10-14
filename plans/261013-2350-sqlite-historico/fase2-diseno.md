# Fase 2 — histórico, presupuestos y exportación a CSV (diseño v2, tras pre-mortem y red team)

Estado: **v2 sin código**; la revisión está en `../reports/revision-diseno-fase2-261014.md` y los cambios que obliga están al final de este fichero. Fecha: 2026-10-14. Parte de `plan.md` (sección "Fase 2") y de lo ya construido en la fase 1
(tabla `horas`, `consulta.consultar()`, `plan_de()`, `nombre_proyecto()`, `exportacion` cifrada `.cgx`).

## Resultado buscado
1. Ver el consumo **más allá de los 30 días** de los logs: por mes y por día, agrupado por proyecto, plan o agente, con la comparación
   con el periodo anterior y una proyección de fin de mes.
2. **Presupuestos** por proyecto y por plan (mes o semana) con aviso al 80 % y al 100 %, visibles en la barra y en Ajustes.
3. **Exportar a CSV** lo que se ve, sin filtrar datos sensibles por accidente.

## No objetivos
Dinero (euros/dólares) como unidad del presupuesto · presupuestos por modelo o por agente · enviar avisos fuera del equipo (correo,
Telegram) · editar o borrar histórico desde la interfaz · servidores (fase 3).

## Lo que ya existe y se reutiliza
- `horas(espacio_id, hora, agente, proveedor, modelo, proyecto_raw, gi, go, cr, cw, turnos)`: agregado por hora en UTC, permanente.
- `consulta.plan_de(agente, proveedor)` y `ajustes.nombre_proyecto(raw, config, antiguos)`: el plan y el nombre a mostrar **se calculan al
  leer**, no se guardan. Cambiar un alias reetiqueta el pasado; es deliberado.
- `config.proyectos[raw].presupuesto_mes_tokens` y `.oculto`; Ajustes ya pinta una barra de progreso por proyecto (30 d, no del mes).
- Lectura solo con `mode=ro`; solo la barra y `--ingerir` escriben.

## Diseño

### 2.1 Histórico (solo lectura)
- Nueva función `consulta.historico(desde, hasta, paso, agrupa)` sobre `horas`. `paso` ∈ {dia, semana, mes}; `agrupa` ∈ {proyecto, plan,
  agente, ninguno}. Los límites de día/semana/mes se calculan en **la zona horaria local del usuario** (con `zoneinfo`), convirtiendo a
  rangos UTC de `hora`; una hora UTC nunca se parte entre dos días.
- Total = `gi + go + cr + cw`, la misma suma que ya muestra el panel (decisión abierta 1).
- Proyección de fin de mes: consumo acumulado del mes dividido por los días transcurridos (incluido el actual, parcial) × días del mes.
  Se oculta antes del día 3 y se marca como estimación.
- Endpoint `GET /historico?desde=AAAA-MM-DD&hasta=AAAA-MM-DD&paso=&agrupa=` con validación estricta de los cuatro parámetros y un tope de
  400 puntos y 200 series. Devuelve números crudos; los nombres salen de `nombre_proyecto` y respetan `oculto`. El JS lo pinta con el DOM
  (nunca `innerHTML` con datos).
- Pestaña nueva "Histórico" en el panel: selector de rango, tabla y gráfico de barras sobre `graficos.js`.

### 2.2 Presupuestos
- Config nueva `presupuestos: [{ambito: "proyecto"|"plan", nombre, periodo: "mes"|"semana", tokens}]`. `nombre` es el `proyecto_raw`
  unificado (proyecto) o el nombre del plan (plan). Máximo 100 filas, `tokens` entero 1..10^13.
- Migración única: cada `proyectos[x].presupuesto_mes_tokens > 0` pasa a una fila `{proyecto, x, mes}`; se conserva el campo antiguo hasta
  la versión siguiente para poder volver atrás, y gana la lista nueva si ambos existen.
- Evaluación en la barra, una vez por ciclo, con una consulta agregada por periodo (no por proyecto). Estado en `state.json`:
  `{clave: {periodo: "2026-10", avisado: 0|80|100}}`; un umbral se avisa **una vez por periodo** y se reinicia al cambiar de periodo.
  Bajar el umbral o subir el presupuesto no repite el aviso ya dado.
- Aviso: ámbar al 80 %, rojo al 100 % en el icono/desplegable de la barra y fila destacada en Ajustes y en Histórico. No hay bloqueo.
- Un proyecto `oculto` **sigue contando** y avisando (ocultar no cambia los totales, como hoy).

### 2.3 Exportación CSV
- Orden `costbar.py --exportar-csv RUTA --desde --hasta --paso --agrupa` y botón en Histórico. Solo exporta lo que `historico()` puede
  devolver: **no hay columnas de IPs, hosts ni rutas completas** (no existen aún en la base).
- CSV con `QUOTE_ALL`, UTF-8 con BOM para Excel, `\r\n`, y neutralización de fórmulas: toda celda de texto que empiece por
  `= + - @ \t \r` se prefija con `'`. Las cifras se escriben como enteros, nunca como texto.
- Fichero 0600 en `DATOS/exportaciones/` (carpeta 0700), nombre generado por la app (nunca por el cliente), tope de 100 000 filas. Si
  `exportacion.configurado` (hay destinatario), la orden ofrece `--cifrar` que produce un `.cgx` en vez de CSV claro.
- Registro de auditoría `DATOS/exportaciones.log` (fecha, rango, filas, cifrado sí/no; nunca contenido).
- El botón del panel hace `POST /exportar-csv` con la sesión y devuelve el fichero como descarga; el servidor no escribe en
  `~/Downloads`.

### 2.4 Clasificación por columna
Una tabla `CLASES` en `almacen.py` (columna → público / interno / personal): `proyecto_raw` y `ruta` son **personales**; el resto,
internos. `historico()` y el exportador consultan esa tabla para decidir qué columnas pueden salir, en vez de listas a mano.

## Decisiones abiertas para el dueño (v1; resueltas abajo)
1. ¿"Tokens" del presupuesto = `gi+go+cr+cw` (incluye caché leída, que domina) o solo entrada+salida? Cambia mucho las cifras.
2. ¿Presupuesto semanal desde el lunes (ISO) o desde el día que el plan se renueva?
3. ¿Aviso solo visual o también notificación de macOS?

## Verificación prevista
Tests con base sintética (sin tocar la real), cruce de día en el cambio de hora del 25-10-2026, mutaciones sobre la neutralización
de fórmulas y sobre el reinicio de avisos, prueba de abuso (nombre de proyecto `=HYPERLINK(...)`, `\t=1+1`, 10^6 series), y comparación
del total de `historico()` con el de `consultar()` para los últimos 30 días (deben coincidir).

## Cambios de la v2 (obligatorios antes de codificar)

Decisiones del dueño (2026-10-14): los tokens del presupuesto **incluyen la caché leída** (`gi+go+cr+cw`, como las cuotas de hoy); la semana
empieza el **lunes** (etiquetada "semana natural"); aviso nativo en macOS **con una interfaz por sistema** para dejar Windows enchufable;
por criterio del asistente (no contradichas): presupuesto por **nombre mostrado**, CSV con `;` y BOM.

Orden de ejecución, por severidad:
1. **Seguridad existente primero:** escapar el JSON incrustado en `panel.py`, CSP con nonce en el servidor local y pasar a DOM los
   `innerHTML` de `app.js` que pintan modelo, proveedor y proyecto. Sin esto no se añade ningún endpoint de exportación.
2. **Avisos fuera de `state.json`:** `DATOS/avisos.json`, un solo escritor (el hilo trabajador de la barra). `state.json` lo borran
   `/guardar` y cada subida de `VERSION_ESTADO`.
3. **Precondición `almacen: activo`** para histórico y presupuestos (con `sombra`, aviso en pantalla); `historico()` aplica `ignorar`;
   test de coincidencia con `consultar()` en tres fechas que crucen el 25-10-2026.
4. **Presupuesto por grupo de nombre mostrado** y plan **final post-alias** elegido de una lista cerrada.
5. **Fechas:** límites locales → UTC con la librería C (como `consulta.py`), aritmética por fechas, fixtures del 25-10 y del 29-03.
   Nada de `zoneinfo` ni `astimezone()` suelto (Windows no trae claves IANA; el desplazamiento fijo rompe el cambio de hora).
6. **CSV cifrado** por frase con tipo de contenido propio (`csv`), nunca con la clave de copias. El panel no persiste ningún CSV en claro;
   solo el CLI escribe en disco, en un directorio con nombre generado.
7. **Neutralización** de fórmulas en todas las columnas de texto (modelo, plan, alias, proyecto), limpieza de bidi e invisibles y
   validación de alias y nombres.
8. **Rango:** validación aritmética antes de consultar (regex estricta; `fromisoformat` acepta `20261014`), semáforo de consultas y cubo
   "otros / (ocultos)" para acotar series.
9. **Migración** de `presupuesto_mes_tokens` una sola vez en la barra, campo antiguo retirado de la UI, `presupuestos` en
   `normalizar`/`errores_config`.
10. **`CLASES`** sobre columnas reales, con `anomalias.detalle` e `id_fuente` como personales y un test que falle si aparece una columna sin
    clasificar.
11. **Datos incompletos:** `datos_desde`, cubos `null` donde no hay datos, umbral más alto por ciclo y evaluación pospuesta si la vuelta de
    ingesta no fue completa.
12. **Interfaz:** recarga automática suspendida en Histórico, hash `#historico`.

Aviso nativo: `plataforma.avisar(titulo, texto)` con implementación para macOS y un hueco para Windows; solo salta al **cruzar** un umbral,
una vez por periodo, sin nombre de proyecto en el texto (se vería en la pantalla de bloqueo), desactivable en Ajustes.
Windows: solo aviso visual en esta fase; la notificación de sistema se añade al portar la barra. Windows también queda bloqueado por
`import fcntl` en `config_y_estado.py`, a resolver en el trabajo de portabilidad, no aquí.
