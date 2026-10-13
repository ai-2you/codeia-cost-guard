# Plan: histórico permanente en SQLite

Estado: borrador para revisar (no hay código todavía). Fecha: 2026-10-13.

## Resultado buscado
Que el consumo medido (y más adelante el estado de los servidores) se guarde en una base SQLite
local y permanente, para tener histórico más allá de lo que duren los logs, presupuestos con
alertas e histórico de servidores. La configuración (`config.json`, `servidores.json`) **no** se mueve:
sigue siendo JSON editable desde Ajustes.

Decisiones del dueño (2026-10-13): objetivos = histórico permanente, menos trabajo por minuto,
presupuestos y alertas, histórico de servidores; retención = todo por hora para siempre; abordaje =
por fases, empezando por consumo.

## No objetivos
- Mover la configuración a SQLite.
- Cambiar lo que muestran la barra y el panel en la fase 1 (el resultado tiene que salir igual).
- Subir nada a la nube ni a un servidor: la base es local y está fuera de git.

## Lo que ya se sabe (medido)
- Hoy `state.json` (1,3 MB) es una caché por fichero de log (2.742 ficheros + 22 de pi). Cada minuto
  `escanear()` hace `stat` de todos y reagrega. Con la caché caliente tarda **~1 s**: el rendimiento no
  es el problema. La ganancia real es el **histórico**: `DIAS = 31`, y los logs (Claude Code ocupa
  4,3 GB, Codex 2,5 GB) no están para siempre.
- Cada parser ya emite, por fuente: `dia{día: {modelo: [gi,go,cr,cw]}}`, `turnos{día: n}` y
  `horas{'AAAA-MM-DDTHH': {tok, usd, mod{modelo: tok}}}`, más `proyecto` y `plan`. Es decir: por hora hay
  tokens y dólares **por modelo**, pero el desglose entrada/salida/caché solo existe por día.
- OpenCode y Hermes vienen de SQLite ajenas (`opencode.db`, bases de Hermes): se releen enteras, no
  son un fichero que crece.

## Diseño propuesto

Fichero `~/.cost-guard/consumo.sqlite` (modo WAL, permisos 0600, fuera de git), escrito solo por un
módulo `infobar/almacen.py`; lo demás pide datos por funciones, no por SQL suelto.

```
fuentes(id, tipo, ruta, firma, visto_en)                         -- un log o una base ajena
consumo(fuente_id, hora, proyecto, plan, modelo,
        gi, go, cr, cw, turnos, usd,
        PRIMARY KEY (fuente_id, hora, proyecto, plan, modelo))   -- una fila por hora y modelo
servidores_muestra(servidor, ts, cpu, mem, disco, intentos_1h, bloqueadas, puertos, alertas)   -- fase 3
presupuestos(ambito, nombre, periodo, tokens)                    -- fase 2 (hoy vive en config.json)
meta(clave, valor)                                               -- versión del esquema, última migración
```

Reglas que lo hacen fiable:
1. **Idempotente.** Releer un fichero cambiado reemplaza sus filas (`DELETE` + `INSERT` en una
   transacción por `fuente_id`); nunca suma encima.
2. **Permanente.** Si un log desaparece o rota, sus filas **se quedan**. Es lo que da el histórico.
3. **Granularidad por hora y modelo** con el desglose `gi/go/cr/cw`. Para eso los parsers pasan a
   emitir ese desglose por hora (hoy solo por día); el día se deriva sumando horas.
4. Los agregados que hoy calcula `agregar()` (ventana de 5 h, semana, mes, por plan/modelo/proyecto/
   día) pasan a ser consultas SQL sobre `consumo`. La lógica de planes y alias se sigue aplicando al
   leer (cambiar un alias en Ajustes no obliga a reescribir el histórico).
5. Varios procesos (barra, panel, CLI) usan la base a la vez: WAL, `busy_timeout` y escrituras cortas.
6. **Copia de seguridad antes de cualquier cambio de esquema o de datos** (regla del proyecto):
   `consumo.sqlite.bak.<fecha>`, conservando las últimas N.

## Fases

### Fase 1 — consumo en SQLite, mismo resultado que hoy
- `infobar/almacen.py`: esquema, versión, conexión, copias, transacciones.
- Parsers (`parsear`, `parsear_pi`, `leer_bases`) con desglose por hora; bases ajenas releídas por fuente.
- `escanear()` lee de la base; `agregar()` consulta en lugar de recorrer registros.
- Migración inicial: construir la base desde los logs actuales (la caché `state.json` no tiene el
  desglose por hora, así que se reparsea una vez; hay que medirlo) y dejar `state.json` como respaldo hasta validar.
- **Validación (criterio de salida):** con los mismos logs, `costbar.py --print` y los datos del panel
  coinciden con la versión actual (ventana 5 h, semana, mes, hoy, por día, por modelo, por proyecto,
  por plan, cuotas). Se compara con una captura previa de la salida actual guardada como referencia
  en `tests/datos/`. Más tests de idempotencia (releer no duplica) y de permanencia (borrar el log no
  borra el histórico).
- Rendimiento: el escaneo por minuto no puede empeorar el ~1 s actual; la primera construcción se mide.

### Fase 2 — histórico y presupuestos
- Vista de histórico en el panel (meses, comparativas, tendencias) sin el límite de 31 días.
- Presupuestos por proyecto/plan (los que hoy se guardan en `config.json` como `presupuesto_mes_tokens`)
  con alertas en la barra y proyección de fin de mes.
- Ajustes sigue siendo el sitio donde se editan.

### Fase 3 — histórico de servidores
- Guardar una muestra por ciclo de `infobar/servidores_ciclo.py` (`servidores_muestra`), con retención
  por hora para siempre y compactación configurable si crece.
- Gráficas de CPU/memoria/disco/intentos y línea de tiempo de incidencias en el desplegable y el panel.

## Riesgos y cómo se tratan
| Riesgo | Tratamiento |
|---|---|
| Duplicados entre sesiones de Claude Code (forks) | Hoy se deduplican al parsear; la fase 1 los compara contra la salida actual. |
| Perder histórico por un error de migración | Copia antes de migrar; `state.json` se conserva hasta validar; no se borra nada automáticamente. |
| Corrupción por escritores concurrentes | WAL + transacciones por fuente; test con hilos como los de `guardar_config`. |
| Una fuente ajena (OpenCode) poda sesiones | Las filas ya guardadas se quedan; solo cambian las de la fuente releída. |
| Base creciendo sin control | Medir tras la fase 1 (cálculo previo: unas decenas de miles de filas por año). |

## Preguntas abiertas
1. ¿Se quiere exportar el histórico (CSV) desde el panel en la fase 2?
2. ¿Los presupuestos son por proyecto, por plan o por ambos? (hoy solo existe `presupuesto_mes_tokens` por proyecto)
3. Datos de servidores (fase 3): ¿guardar también la lista de IPs que intentaron entrar, o solo contadores?
   Esto último es dato personal de terceros y conviene decidirlo antes.
