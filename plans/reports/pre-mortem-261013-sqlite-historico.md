# Pre-mortem — plan «histórico permanente en SQLite» (261013-2350)

Fecha: 2026-10-14 · Alcance: solo el plan (todavía no hay código) · Método: suponemos que en octubre de 2027
el proyecto ha fracasado y buscamos por qué. Cada causa se ha contrastado con el código actual
(`infobar/lectores_logs.py`, `agregacion.py`, `config_y_estado.py`, `servidores_ciclo.py`, `servidores_estado.py`,
`servidor_local.py`, `trabajador_barra.py`, `infobar/remoto/*.sh`, `tests/`) y, cuando hacía falta, con una medición
de solo lectura sobre los logs reales. No se reproducen nombres de proyecto, hosts ni IPs: [redactado].

## Mediciones hechas para este informe

| Medida | Resultado | Nota |
|---|---|---|
| Líneas con `usage` en los 400 jsonl de Claude Code más recientes | 82.321 líneas, 42.201 `message.id` distintos | Claude Code escribe una línea por bloque de contenido. Todas llevan la misma entrada y caché, y `output_tokens` va creciendo |
| Tokens con la clave de deduplicación actual frente a uno por `message.id` (la última línea gana) | **1,74×** (20,6 mil millones frente a 11,9 mil millones) | Es una muestra de 400 ficheros. La clave actual lleva `ts[:19]` y las líneas del mismo mensaje caen en segundos distintos |
| Mismo `message.id` en varios ficheros (forks o reanudaciones) | 6 de 42.201 | Hoy no se deduplica entre ficheros |
| Velocidad de `parsear()` | ~357 MB/s (1,2 GB en 3,4 s) | **Caché de disco caliente**. En frío será bastante menos |
| Ficheros de Claude Code y Codex / antigüedad del más viejo | 1.406 / 45 días | `cleanupPeriodDays: 30` en `~/.claude/settings.json` |
| Permisos | `state.json` 0600, `logs/costbar.log` **0644** | — |
| Bases de OpenCode / perfiles de Hermes | 2 `.db` (solo se lee la primera que existe) / 4 perfiles | — |

---

## Causas ordenadas por riesgo

### 1. El criterio «resultado idéntico» congeló para siempre unos totales que hoy ya están mal
- **Qué pasó.** La fase 1 se validó comparando con la salida actual y salió idéntica. Pero la salida actual
  infla los tokens de Claude Code en torno a un 74 % (en la muestra): `parsear()` deduplica por
  `modelo|segundo|gi|go|cr|cw`, y las 2 o 3 líneas de un mismo mensaje caen en segundos distintos, así que la entrada y
  la caché se cuentan varias veces. Además:
  - la deduplicación es solo dentro de cada fichero (`vistos` es local), aunque la tabla de riesgos del plan dice que
    los forks «hoy se deduplican»;
  - la fórmula del coste resta la caché de nuevo (`max(gi - cr, 0)`): en Claude `gi` ya la excluye, y en Codex
    `gi = input - cached`, así que allí se resta dos veces;
  - `agregar()` aplica `CLIFF` al total diario mientras que `horas` lo aplica por turno, así que el USD del día y el de
    la ventana de 5 h ya no cuadran entre sí.

  Cuando alguien lo arregló meses después, los logs de origen ya no existían (Claude los borra a los 30 días) y el
  histórico permanente quedó mal para siempre. Los presupuestos de la fase 2 avisaban de más y se dejaron de usar.
- **Señal temprana.** La cuota real del proveedor (p. ej. el `/usage` de NaN o el panel de Anthropic) no cuadra con
  la de CostGuard. La suma de horas no da la cifra del día.
- **Probabilidad / impacto.** Alta / Alta.
- **Medida.**
  - Cambiar el criterio de salida de «idéntico» a una **prueba diferencial con lista de divergencias aceptadas y
    firmadas**: deduplicación por `message.id` (input y caché una vez, la última `output_tokens`), deduplicación entre
    ficheros, fórmula del coste y `CLIFF` por turno.
  - Arreglar el parser **antes** de empezar a acumular histórico permanente. Si no, el histórico nace con el error.

### 2. En la clave permanente se guardaron datos derivados que ya no se podían recalcular
- **Qué pasó.** El esquema mete `proyecto`, `plan` y `usd` en la fila, y `proyecto` y `plan` además en la PK.
  - `proyecto` es un slug con pérdida: los 3 últimos trozos del nombre de la carpeta.
  - `plan` sale de `plan_ccs(fecha_ini)`, pero `fecha_ini` nunca se asigna, así que hoy la atribución CCS es código
    muerto y todo Claude cae en «Claude (Max)».
  - `usd` depende de la tabla de tarifas del día en que se parseó.
  - Hoy esto da igual porque `olvidar_cache()` borra `state.json` y se recalcula desde los logs. Con logs borrados ya
    no se puede: al arreglar `plan_ccs` o cambiar una tarifa, el pasado se quedó con lo viejo y el presente con lo
    nuevo, mezclado dentro de la misma consulta.
- **Señal temprana.** Alguien propone «reparsear todo» o adapta `olvidar_cache()` como `DELETE FROM consumo`.
- **Probabilidad / impacto.** Alta / Alta.
- **Medida.**
  - Guardar **hechos crudos**: `cwd` o la ruta original, la cadena de modelo tal cual, el proveedor declarado, la hora
    UTC y los cuatro contadores.
  - `proyecto`, `plan`, alias y `usd` se derivan **al leer**, como ya pide la regla 4 del plan pero sin aplicarla al
    esquema.
  - Si se quiere guardar `usd`, hacerlo en una columna aparte con `tarifa_version` y en enteros (micro-USD).
  - **Regla única de borrado**, igual en todo el plan y vigilada por test: el único `DELETE` permitido es el de filas
    de una versión de productor marcada como mala, limitado a fuentes que todavía existen y siempre después de un
    `backup()`. Ningún otro código borra.

### 3. La identidad de la fuente era la ruta o la base entera, y eso borró y duplicó histórico
- **Qué pasó.** Fallaron cuatro cosas distintas:
  - **Borrado.** Para OpenCode y Hermes la «fuente» era el `.db` completo. Al aplicar la regla 1 (DELETE + INSERT por
    fuente), cuando OpenCode podó sesiones viejas sus filas desaparecieron del histórico. Contradice la regla 2 y la
    fila «OpenCode poda sesiones» de la tabla de riesgos.
  - **Duplicado.** Al mover o renombrar una carpeta de logs, restaurar una copia o copiar `~/.claude` a otro Mac, el
    mismo contenido apareció con otra ruta. Se insertó como fuente nueva y las filas viejas «permanentes» se quedaron:
    doble conteo para siempre.
  - **Truncado.** Un log truncado o reescrito más corto reemplazó sus filas por menos.
  - **Hermes.** Solo expone totales por sesión y los fecha todos en `started_at`, el mismo defecto que ya se corrigió en
    OpenCode. Todo el consumo de una sesión de varios días queda en el día en que empezó, y un upsert por sesión
    conservaría ese error.
- **Señal temprana.** El total de un mes cerrado cambia de un día para otro. El número de fuentes crece sin que haya
  sesiones nuevas.
- **Probabilidad / impacto.** Alta / Alta.
- **Medida.**
  - Clave de **contenido**, no de ruta:
    - Claude: `message.id` + `requestId`;
    - Codex: id de sesión + número de turno o marca de tiempo;
    - pi: id de sesión + id de mensaje;
    - OpenCode: id de mensaje;
    - Hermes: id de sesión + lectura, guardando un **delta** por lectura (total nuevo menos el último total visto)
      fechado en la hora de la lectura.
  - `INSERT OR REPLACE` por esa clave y **nunca borrar por fuente** (regla única de borrado: ver la causa 2).
  - Si un fichero mengua (tamaño menor que el visto), tratarlo como generación nueva: solo se añade, nunca se quita.
  - Averiguar primero qué es la segunda base de OpenCode (`opencode-.db`): copia u otro canal. Leer las dos solo si
    hay deduplicación por id de mensaje; si no, se duplica.
  - Tests: renombrar un log no duplica, podar sesiones en OpenCode no borra y truncar un log no resta.

### 4. Horas locales sin zona en una base permanente: cambio de hora, viajes y «día» mal partido
- **Qué pasó.** Las claves `AAAA-MM-DDTHH` son hora local naíf (`a_local()` y `time.localtime` en OpenCode).
  - El domingo **25-10-2026** (cambio de hora en Madrid) la hora 02 ocurrió dos veces y se fundió en una sola fila.
  - `time.mktime` con `isdst` ambiguo colocó mal la ventana de 5 h durante ese tramo.
  - Tras un viaje o un cambio de zona, los logs que aún existían se reparsearon en la zona nueva mientras las filas de
    logs ya borrados se quedaban en la vieja: mismo día, dos husos.
  - OpenCode agrupa por `time_created / 3600000`, es decir, por hora UTC, que no coincide con la hora local en zonas de
    media hora.
- **Señal temprana.** Picos o huecos de una hora a finales de octubre y de marzo. El total del día cambia tras un
  viaje.
- **Probabilidad / impacto.** Media / Alta. Ocurre seguro dos veces al año; el viaje depende del uso.
- **Medida.**
  - Guardar la hora como **entero UTC** (epoch / 3600) y derivar el día y la hora locales al consultar, con la zona
    del usuario.
  - Añadir fixtures con el 25-10-2026 y el 29-03-2026, y un test que parsee el mismo log con TZ distintas y compruebe
    que el total no cambia.

### 5. «Resultado idéntico» resultó no ser comprobable (y la referencia filtró datos)
- **Qué pasó.** La captura de `costbar.py --print` en `tests/datos/` dependía de:
  - `time.time()` (ventanas de hoy, 5 h, semana y mes);
  - los logs vivos, que cambian cada minuto;
  - `cfg()`, la API de NaN (`uso_nan`) y la traza de CCS;
  - el orden de suma de floats.

  El test fallaba al día siguiente sin que nada hubiera cambiado, así que se «actualizó la referencia» y dejó de
  proteger nada. Además, `.gitignore` no cubre `tests/datos/`: la captura subió a git nombres de proyectos y volúmenes
  de uso.
- **Señal temprana.** Un test que solo pasa si se ejecuta el mismo día que se generó la referencia.
- **Probabilidad / impacto.** Alta / Media.
- **Medida.**
  - **Reloj inyectable** en `agregar()` (parámetro `ahora`) y TZ fijada, como ya hace `TZFix` en `tests/test_costbar.py`.
  - Usar un **corpus sintético o seudonimizado** en `tests/datos/`.
  - Comparar contadores enteros exactos y USD en micro-USD.
  - La comparación sobre datos reales se hace en local con un script que ejecuta las dos rutas sobre la misma
    instantánea, y su salida va a una carpeta ignorada por git.
  - Prueba de humo en CI: `tests/datos/` no contiene rutas de `$HOME` reales.

### 6. La primera construcción la lanzaron varios hilos y procesos a la vez, y los datos de la barra se pararon
- **Qué pasó.** La interfaz de AppKit no se congela (`barra.py` manda todo lo lento al trabajador), pero sí dejan de
  llegar los datos y el monitor de servidores. `escanear()` se llama desde:
  - el hilo trabajador de la barra;
  - `/origenes` y `/ajustes` de `servidor_local.py`, en hilos del mismo proceso;
  - el subproceso `panel.py`, con un tope de 120 s;
  - el CLI.

  Con la base vacía, todos empezaron a construir: cuatro parseos de unos 7 GB y transacciones largas compitiendo por
  el bloqueo de escritura. El trabajador hace también el ciclo de servidores **en el mismo hilo**: mientras esperaba
  al `busy_timeout`, el monitor de VPS se paró. El panel agotó sus 120 s.
- **Señal temprana.** «database is locked» en `costbar.log`. El punto de servidores se queda gris tras actualizar.
- **Probabilidad / impacto.** Media / Media.
- **Medida.**
  - **Un único constructor** con bloqueo de fichero.
  - La construcción es **reanudable por fuente** (marca de «hecho» por fuente) y se hace con transacciones cortas por
    lotes.
  - Mientras dura, los lectores usan la ruta actual (`state.json`) y la barra muestra «construyendo histórico».
  - `busy_timeout` corto en la barra; si se agota, se usa el último resultado.
  - Los endpoints HTTP **no escriben nunca**: solo leen.
  - Medir la primera construcción en frío (tras `purge`) y fijar un tope como criterio de salida.

### 7. La transición y la marcha atrás no estaban ensayadas
- **Qué pasó.**
  - El «respaldo» `state.json` desapareció a la primera, porque `olvidar_cache()` lo borra al cambiar un alias.
  - La copia `consumo.sqlite.bak.<fecha>` se hizo copiando el fichero con WAL activo y salió incoherente.
  - Tras un fallo se restauró una copia de 3 días antes y se perdieron esos 3 días, que ya no estaban en los logs.
  - Mientras tanto, una barra antigua sin reiniciar seguía escribiendo `state.json` (versión 11) y el panel nuevo leía
    SQLite: dos cifras distintas en pantalla.
- **Señal temprana.** Nadie ha ensayado «volver a la versión anterior» antes de fusionar.
- **Probabilidad / impacto.** Media / Alta.
- **Medida.**
  - Copias con `sqlite3.Connection.backup()` o `VACUUM INTO`, nunca copiando el fichero.
  - Cada fila lleva la **versión del productor** (versión del parser). Un arreglo borra solo las filas de una versión
    mala **cuyas fuentes siguen existiendo** y las reconstruye; las demás se quedan.
  - Durante la transición, el código nuevo sigue escribiendo `state.json` (doble escritura) para que volver atrás sea
    solo cambiar de rama.
  - `meta` guarda la versión mínima de código capaz de leer la base; el código viejo que la ve, no escribe.
  - Ensayo de marcha atrás como criterio de salida de la fase 1.

### 8. La base se excluyó de Time Machine y el histórico «permanente» murió con el disco
- **Qué pasó.** El control 1 de seguridad (`tmutil addexclusion`) dejó como únicas copias los `.bak` del mismo
  disco. Un fallo del SSD, o borrar `~/.cost-guard` a mano, se llevó todo el histórico. El plan quiere «para siempre»
  y a la vez excluye las copias.
- **Señal temprana.** No hay ningún procedimiento escrito de «restaurar el histórico en un Mac nuevo».
- **Probabilidad / impacto.** Baja / Alta.
- **Medida.**
  - Decisión explícita del dueño: o se permite Time Machine (cifrado) para `consumo` y se excluyen solo los datos de
    servidores, o hay un **export cifrado periódico** a un destino elegido.
  - Separar en dos ficheros `consumo.sqlite` y `servidores.sqlite`, con políticas de copia y retención distintas.

### 9. Fase 3: el histórico de servidores contaba de más y guardaba datos rancios
- **Qué pasó.**
  - Los campos de `recoger.sh` son **calibres** y no eventos. `intentos.hora` (la columna `intentos_1h` del plan)
    cubre la última hora, y sumar sus muestras cada 5 min la multiplicó por unas 12 (por 60 en modo incidencia).
    `intentos.total` cubre 24 h móviles: sumado, da unas 288 veces más al día. `fail2ban total` es un contador desde
    que arrancó el servicio y se reinicia con él.
  - `_guardar()` fusiona con el último dato bueno. Tomar la muestra de esa caché volvió a grabar valores viejos con
    hora nueva cuando el SSH fallaba.
  - «Una muestra por ciclo» con retención «por hora para siempre» no casan: en modo incidencia son 1.440 filas al día
    por servidor.
- **Señal temprana.** Gráficas de intentos con escalones idénticos durante una caída de SSH. Totales mensuales de
  intentos absurdos.
- **Probabilidad / impacto.** Alta / Media.
- **Medida.**
  - Grabar solo `nuevos` (lo recogido en el ciclo, con `ok_en`), nunca la caché fusionada.
  - Esquema con tipo de métrica (calibre o contador con reinicios) y agregación horaria definida: máximo, media y
    último para calibres; deltas para contadores.
  - Muestra cruda N días y después compactada por hora.

### 10. Datos personales de terceros (y del dueño) sobrevivieron a su caducidad
- **Qué pasó.**
  - Las IPs de atacantes caducaban a los 90 días en la tabla, pero seguían en los `.bak`, en las páginas libres del
    fichero y en el WAL.
  - El plan solo hablaba de IPs de atacantes; `sesiones.desde` (de `who`) guarda **las IPs del propio dueño**, es
    decir, su ubicación y sus horarios.
  - `fuentes.ruta` guardaba rutas completas con nombres de cliente.
  - `costbar.log` seguía en 0644 con rutas en los mensajes de error.
- **Señal temprana.** Un `strings consumo.sqlite.bak.*` muestra IPs de hace más de 90 días.
- **Probabilidad / impacto.** Media / Alta.
- **Medida.**
  - `PRAGMA secure_delete=ON`.
  - La caducidad se aplica también a las copias: rotarlas a menos de 90 días, o generarlas sin IPs.
  - IPs de sesiones propias **no se guardan** o se seudonimizan con HMAC.
  - En `fuentes` se guarda un hash de la ruta.
  - Test: ninguna IP vieja aparece en la base ni en sus copias.

### 11. La barra se volvió más lenta por las comprobaciones de integridad y las reescrituras de cada minuto
- **Qué pasó.**
  - `PRAGMA integrity_check` al abrir cuesta en proporción al tamaño, y se abría cada minuto y en cada petición HTTP.
  - Hermes no tiene firma de cambios (OpenCode sí): se releía y reescribía cada minuto.
  - La PK empieza por `fuente_id`, así que la ventana de 5 h recorría la tabla entera.
  - El WAL creció sin checkpoint.
  - Conexiones compartidas entre hilos (`check_same_thread`) dieron errores intermitentes.
- **Señal temprana.** El escaneo pasa de ~1 s a varios segundos a medida que crece la base.
- **Probabilidad / impacto.** Media / Media.
- **Medida.**
  - `quick_check` al arrancar y como mucho una vez al día.
  - Firma de cambios también para Hermes.
  - Índice por `hora_utc`.
  - Una conexión por hilo.
  - Checkpoint periódico.
  - Criterio: el escaneo del minuto no supera 1 s con una base sintética de 3 años.

### 12. La aplicación final hubo que reescribirla desde cero
- **Qué pasó.** El esquema no tenía identificador de dispositivo ni ids estables de turno, guardaba horas locales e
  identificaba las fuentes por ruta. Fusionar dos Macs, o llevar el histórico a un servidor multiusuario, era
  imposible sin rehacerlo. Las columnas `usuario` y `espacio` se añadieron, pero las claves seguían siendo locales.
- **Señal temprana.** «Qué pasa si tengo dos Macs» no tiene respuesta en el diseño.
- **Probabilidad / impacto.** Media / Alta.
- **Medida.** Lo cubren las causas 2–4: clave de contenido, hora UTC, datos crudos, más `dispositivo_id` y
  `espacio_id` en cada fila. Con eso la fusión es un `INSERT OR IGNORE`.

### 13. Un test o una rama de desarrollo escribió en el histórico real
- **Qué pasó.** Todo el estado actual se resuelve por `rutas.RAIZ` (el checkout) y los tests se aíslan redirigiendo
  `rutas.*` (`tests/aislamiento.py`). El plan pone la base en `~/.cost-guard/`. `almacen.py` calculó su propia ruta, y
  los tests, una rama o un worktree con un parser defectuoso escribieron filas malas en la base permanente. Al ser
  permanente, no se podían reconstruir.
- **Señal temprana.** Aparecen filas de proyectos de prueba en el histórico, o su fecha de modificación cambia al
  pasar los tests.
- **Probabilidad / impacto.** Media / Alta.
- **Medida.**
  - La ruta de la base vive en `rutas.py`, como las demás.
  - Test que falla si el almacén abre algo fuera de `HOME_FALSO`.
  - `meta` guarda la versión del productor y bloquea escrituras de un código más antiguo o distinto.

### 14. Se perdió el histórico que había antes de llegar a producción
- **Qué pasó.** Con `cleanupPeriodDays: 30` y el log más antiguo a 45 días, cada semana de retraso de la fase 1 fue
  histórico perdido para siempre. Además, si la barra pasaba más de 30 días sin ejecutarse (vacaciones, otro Mac),
  se perdía el tramo intermedio sin aviso.
- **Señal temprana.** Huecos en el histórico que coinciden con periodos sin la barra abierta.
- **Probabilidad / impacto.** Alta / Baja-Media.
- **Medida.**
  - Avisar en el panel de los huecos (días sin fuentes vistas).
  - El dueño decide si sube `cleanupPeriodDays` mientras dura la migración. Es su configuración, no la del producto.

---

## Cambio estructural propuesto (a decidir por el dueño)

Las causas 1, 2, 3, 4, 7 y 12 vienen de la misma decisión: guardar **agregados por hora con clave de ruta**. La
alternativa es una tabla **de turnos** de solo añadir:

- clave de contenido, hora UTC, datos crudos y versión del productor;
- `INSERT OR REPLACE` por clave y nunca `DELETE` por fuente;
- la tabla horaria pasa a ser una vista o caché derivada que se puede reconstruir;
- volumen estimado: en torno a un millón de filas al año, que SQLite maneja sin problema.

Además, añadir una **fase 1b en sombra**: N días con el cálculo nuevo en paralelo al actual, registrando las
diferencias, antes de cambiar lo que se muestra.

## Cambios concretos al plan

1. **Criterio de salida de la fase 1:** sustituir «resultado idéntico» por «prueba diferencial con lista de
   divergencias aceptadas y firmadas».
2. **Antes de acumular histórico:** arreglar la deduplicación por `message.id` (input y caché una vez, la última
   salida), la deduplicación entre ficheros y la fórmula del coste. El `CLIFF` se aplica por turno.
3. **Esquema:** pasar a una tabla de turnos de solo añadir con clave de contenido por agente. La tabla horaria es
   derivada.
4. **Datos crudos:** guardar `cwd`/ruta, modelo, proveedor y contadores. `proyecto`, `plan`, alias y `usd` se derivan al
   leer. `usd`, si se guarda, en micro-USD con `tarifa_version`.
5. **Hora:** guardar en entero UTC. Fixtures del 29-03-2026 y del 25-10-2026, y un test de «misma salida con otra TZ».
6. **Regla 1 reescrita:** «upsert por clave de turno. El único `DELETE` permitido es el de filas de una versión de
   productor mala, limitado a fuentes que siguen existiendo y tras `backup()`». Tests de renombrar, truncar y podar
   OpenCode.
7. **OpenCode y Hermes:**
   - averiguar qué es `opencode-.db` y leerla solo con deduplicación por id de mensaje;
   - Hermes con un delta por lectura (total nuevo menos el último visto, fechado en la lectura) y con firma de cambios.
8. **Reproducibilidad:** reloj inyectable en `agregar()`, corpus sintético o seudonimizado en `tests/datos/` y
   comprobación de que no contiene rutas reales.
9. **Construcción inicial:** un solo constructor con bloqueo, reanudable por fuente y en lotes cortos. Los lectores
   usan `state.json` mientras tanto. Medir en frío y fijar un tope.
10. **Endpoints HTTP y `panel.py`:** solo lectura. Solo la barra (o un único proceso dueño) escribe.
11. **Concurrencia:** una conexión por hilo, `busy_timeout` corto en la barra con vuelta al último resultado, y el ciclo
    de servidores separado del escaneo para que un bloqueo no lo pare.
12. **Copias:** con `backup()` o `VACUUM INTO`. Versión del productor por fila. `meta.version_minima_lector`.
13. **Transición:** doble escritura de `state.json` hasta cerrar la fase. `olvidar_cache()` no toca la base. Ensayo de
    marcha atrás como criterio de salida.
14. **Fase 1b:** N días en sombra comparando el cálculo viejo con el nuevo antes de cambiar la interfaz.
15. **Copia de seguridad del histórico:** decidir entre Time Machine y export cifrado. Separar `consumo.sqlite` de
    `servidores.sqlite`.
16. **Fase 3:**
    - grabar solo `nuevos`;
    - tipar las métricas (calibre o contador con reinicios) y definir su agregación horaria;
    - muestra cruda N días y después compactada por hora.
17. **Datos personales:**
    - `secure_delete=ON`;
    - caducidad de IPs aplicada también a las copias;
    - IPs propias (`sesiones.desde`) fuera de la base o seudonimizadas;
    - `fuentes` con hash de la ruta;
    - `costbar.log` en 0600 y sin rutas.
18. **Rendimiento:** `quick_check` diario en vez de `integrity_check` en cada apertura. Índice por hora UTC. Prueba de
    1 s con una base sintética de 3 años.
19. **Aplicación final:** `dispositivo_id` y `espacio_id` en cada fila desde la fase 1.
20. **Huecos:** aviso en el panel de días sin fuentes vistas. Recomendar al dueño revisar `cleanupPeriodDays` mientras
    dura la migración.
21. **Ruta de la base en `rutas.py`**, test de aislamiento (nada fuera de `HOME_FALSO`) y bloqueo de escritura por
    versión de productor en `meta`.
22. **Corregir la tabla de riesgos del plan:** los forks hoy **no** se deduplican entre ficheros, y la fila de
    OpenCode contradice la regla 1 tal como está escrita.

## Preguntas abiertas
- ¿Se acepta que las cifras mostradas **bajen** (en torno a un 40 % en Claude Code, según la muestra) al corregir la
  deduplicación? Habrá que explicárselo al usuario en la interfaz.
- Copias del histórico: ¿Time Machine cifrado o export propio?
- ¿Cuántos días dura la fase en sombra?
