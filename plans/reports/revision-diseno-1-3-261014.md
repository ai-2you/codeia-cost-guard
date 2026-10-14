# Pre-mortem y red team del diseño 1.3 (consultar desde la base, ingesta en hilo, interruptor)

Fecha: 2026-10-14 (Europe/Madrid). Rama: `feat/fase1-ingesta-hermes`. Alcance: la sección «1.3 — diseño» de
`plans/261013-2350-sqlite-historico/plan.md` (líneas 139-148). Solo lectura. No se repite lo ya cerrado en
`pre-mortem-fase1-261014.md` ni `red-team-fase1-261014.md`; se cita cuando el diseño 1.3 lo contradice o lo deja caer.

Mediciones propias (scratchpad, Python 3.14.6 / SQLite 3.53.4; cifras sin nombres):

| Medida | Resultado |
|---|---|
| Lector `mode=ro` con cursor a medias durante `wal_checkpoint(TRUNCATE)` (busy_timeout 300 ms) | el checkpoint devuelve `(busy=1, 91, 91)` tras 0,33 s; el lector termina y lee todo; sin lectores: `(0,0,0)` y WAL a 0 bytes |
| Lector `mode=ro` que **abre** durante un checkpoint | lee bien (40.000 filas) |
| `set_authorizer` que deniega INSERT/UPDATE/DELETE | bloquea los tres (`DatabaseError: not authorized`), **deja pasar** `PRAGMA wal_checkpoint` |
| Base truncada a la mitad abierta en `mode=ro` | `DatabaseError: database disk image is malformed` en la primera consulta (no al abrir) |
| `abrir(solo_lectura=True)` | no lee `user_version` ni `version_minima_escritor` (`infobar/almacen.py:121-149`: `_migrar` solo en escritura) |
| `config.json` instalado (`~/.cost-guard`) | 13 claves en `proyectos` (13 alias, 0 ocultos): 12 con forma `a/b/c` sin usuario, **1 con prefijo `pi · `**; 22 `aliases` (3 claves con `://`, 0 con `?` o `@`); alias `claude (max)` presente |
| `plan_ccs` | **código muerto**: `fecha_ini` nunca se asigna (`infobar/lectores_logs.py:75-76,157`), así que el plan de Claude es siempre `"Claude (Max)"` aunque `~/.ccs/backups` tenga 65 ficheros |
| `VERSION_ESTADO` | 12; el último cambio fue en `43ea0ea` (arreglo de reasoning de pi), así que la caché de `escanear()` es coherente con el parser actual |
| Latido del escritor | solo lo refresca `ingerir_turnos` (`almacen.py:439`) y `adquirir_escritura` (`:245-246`); `guardar_lectura`, `guardar_marca`, `anotar`, `fuente` **no** lo tocan |
| `fuente()` | hace `BEGIN IMMEDIATE` + `UPDATE visto_en` **por fichero y por pasada** (`almacen.py:300-309`, llamado en `ingesta.py:155`) |
| Llamadores de `escanear()` | `trabajador_barra.py:48`, `costbar.py:51,63,106`, `servidor_local.py:223,231`, `panel.py:41`, `popover.py:18`, `analizar_gasto.py:11` (8 ficheros, 10 sitios); la instalación arranca `barra.py` (`install.sh:59`), `costbar.py` queda como CLI y app heredada |
| Base instalada | `~/Library/Application Support/CostGuard/consumo.sqlite` **no existe** todavía; `state.json` existe en el checkout y en la instalación |

---

## BLOQUEANTE

### B1. La sombra, tal como está escrita, no puede dar «residuo 0» con sentido
- **Evidencia.** 1.3 §5: «compara `consultar()` con `escanear()` en días cerrados». Las dos devuelven el **informe de
  `agregar()`**, que no tiene dimensión de agente: `dias`, `modelos_mes`, `cruce[plan][modelo][día]`, `proyectos`
  (`agregacion.py:322-338`). Con eso no se puede aplicar «tolerancia 0 en Codex, pi y OpenCode y categorías firmadas en Claude
  y Hermes» (plan línea 128): una bajada de Claude por forks y una subida de Hermes por tareas auxiliares se compensan dentro
  de la misma celda de día.
- Además, tres cosas hacen que el residuo nunca sea 0 aunque todo esté bien:
  1. **Hermes no tiene días cerrados.** La base fecha cada fila en `first_seen` y los contadores suben con el uso posterior
     (`ingesta_hermes.py:6-8`); el lector antiguo hace lo mismo con `started_at` (`lectores_logs.py:395-398`). Tokens
     consumidos hoy caen en un día de hace semanas en las dos rutas, pero leídas en instantes distintos (la base en la
     ingesta, `escanear()` al comparar) difieren. Y el lector antiguo lee `sessions` y no la base raíz; la base lee
     `session_model_usage` y la raíz (+17,9 % y +120,5 M de entrada, plan línea 63). No hay forma de calcular «residuo
     explicado» para Hermes sin reimplementar el lector antiguo dentro de la sombra.
  2. **Claude borra logs a los 30 días** (`cleanupPeriodDays: 30` verificado). Un día cerrado cuyo fichero desaparece
     baja en `escanear()` y no en la base: divergencia legítima que la sombra contaría como residuo.
  3. **Calendario.** Hoy es 14-10. Para que el 25-10 y el 26-10 entren como *días cerrados* la sombra tiene que seguir
     comparando al menos hasta el 27-10, y «7 días seguidos» son 7 días cerrados, es decir, 8-9 días de calendario.
- **Escenario de fallo.** Día 7: residuo de +3 % en el día 22-10 porque Hermes acumuló una sesión vieja y un log de Claude
  del 22-09 se borró. Se reinicia el contador tres veces, decide el dueño «a ojo» (causa 5 del pre-mortem, otra vez).
- **Arreglo.**
  - Comparar **antes de `agregar()`**, por `(agente, día local, proveedor, modelo)` en contadores enteros. Del lado antiguo
    sale de `vistos` (`escanear()` ya lo construye por fichero; la clave `f` dice el agente: ruta `.claude/`, `.codex/`,
    `pi:`, `sqlite:`); del lado nuevo, de `horas`. Guardar el resultado por agente y día en el fichero de sombra.
  - **Hermes fuera de la comparación numérica**: se da por validado con la coincidencia exacta con la suma directa de la
    tabla (plan línea 63) y se registra solo que no baja.
  - Comparar solo días cerrados con **edad ≤ 27 días** y, para Claude, excluir los días cuyo fichero ya no existe (la
    base sabe la fuente de cada turno: `turnos.fuente_id` → `fuentes.id_fuente`; basta con marcar las fuentes cuyo
    fichero desapareció entre dos pasadas).
  - Categorías firmadas **calculadas, no declaradas**: forks = turnos con la misma clave vistos en más de una fuente;
    atribución de modelo = celdas donde solo cambia `modelo` con el mismo total; cruce de hora/día = turnos cuya
    `hora_utc` cae a menos de 1 h del límite. Lo que no entra en una categoría calculada es residuo.
  - Fechas: arrancar la sombra en cuanto 1.3 esté en `main` y **no cerrarla antes del 28-10** (día 25 y 26 cerrados).
  - Registrar también, **sin que bloquee**, la diferencia de `cinco_h` y `ritmo` tomadas en el mismo instante (lo que
    mira el usuario en la barra): tolerancia = turnos ingeridos en los últimos 60 s. Ver A6 para el 25-10.

### B2. El puesto de escritor caduca con la barra viva, y entonces «el panel» puede escribir
- **Evidencia.** `LATIDO_TTL_S = 90` (`almacen.py:24`). El latido solo se refresca al aplicar turnos
  (`ingerir_turnos`, `:439`) o al readquirir (`:245`). Una barra sin consumo nuevo durante 90 s (reposo, noche) deja de
  ser escritor vigente: `_exige_escritor` (`:267-269`) lanzará `SinEscritura` en su siguiente `fuente()`/`guardar_marca`.
  1.3 §4 dice que «el CLI y el panel pueden tomar el relevo». `panel.py` es un subproceso que la barra lanza **cada
  60 s** (`costbar.py:166-167`) y en cada `GET /` (`servidor_local.py:217`).
- **Escenario de fallo.** 02:00, nadie usa agentes. A los 90 s el panel regenerado por la barra ve latido caducado, toma el
  puesto e ingiere 20 s en el hilo de la petición HTTP (tope de 120 s). La barra despierta, falla con `SinEscritura`, lo
  registra, readquiere a los 90 s… ping-pong permanente entre dos procesos, ninguno dañino (BEGIN IMMEDIATE y
  `_exige_escritor` lo garantizan) pero el criterio «panel.py y el servidor no ejecutan ningún INSERT» (1.3 §7) es falso
  por diseño.
- **Arreglo.**
  - El hilo de ingesta llama a `adquirir_escritura` **al principio de cada vuelta** (con `quien` propio refresca el latido
    aunque no haya nada que escribir) y la vuelta ocurre al menos cada 30 s (TTL/3).
  - **Solo dos procesos pueden ser escritores**: la barra (`barra.py`) y el CLI con `--ingerir` explícito. `panel.py`,
    `popover.py`, `servidor_local.py` y `analizar_gasto.py` abren únicamente `abrir(solo_lectura=True)`. En Windows o sin
    barra, el relevo lo hace un `costbar.py --servicio` (o el CLI en cron), nunca el proceso del panel web.
  - Test estático: ningún módulo de los llamadores importa `ingestor`; `consulta.py` no importa `ingesta*`. Test dinámico
    del subproceso: lanzar `panel.py` con `COSTGUARD_DATOS` apuntando a una base cuyo directorio es **de solo lectura**
    (`chmod 500`): si intentase escribir fallaría al crear `-wal`. El autorizador in-process no cubre un subproceso.

---

## ALTO

### A1. `informe()` no puede detectar «versión mayor» ni «corrupta» con lo que hay
- **Evidencia.** `abrir(solo_lectura=True)` no comprueba `user_version` (`almacen.py:121-124,147-149`); la corrupción solo
  aparece como `DatabaseError` en la primera consulta (medido). 1.3 §6 promete caer a `escanear()` en ambos casos.
- **Escenario.** Fase 2 sube el esquema a v2 y cambia la semántica de `horas`; el usuario vuelve a la fase 1: `consultar()`
  abre bien, consulta columnas con otro significado y pinta cifras falsas sin anotar nada.
- **Arreglo.** En `consulta.py`: `PRAGMA user_version` > `VERSION_ESQUEMA` → `ErrorAlmacen`; envolver la consulta en
  `except sqlite3.DatabaseError` → fallback con anotación **una vez por proceso** (no cada minuto: en el checkout de
  desarrollo la base no existe nunca sin `COSTGUARD_DATOS`, `almacen.py:110-111`, y llenaría `costbar.log`).
  `quick_check` nunca en `consultar()` (1 s a 3 años).

### A2. Una base existente pero a medio construir se muestra como si fuera completa
- **Evidencia.** 1.3 §6 solo cubre «no existe, corrupta, versión mayor». La construcción inicial dura ~20 s (plan línea 63)
  y la lectura es **8 MiB por fichero y pasada** (`ingesta.py:19`); 1.3 §4 dice «un lote acotado por vuelta» sin decir
  cuántas vueltas seguidas. Si la vuelta es una por minuto, 4,4 GB de Claude son 550 pasadas del fichero mayor: horas.
- **Escenario.** Instalación nueva (o vuelta de `apagado` a `activo` tras borrar la base): el panel muestra 3 días de 31
  durante la primera hora; el usuario cree que ha perdido histórico.
- **Arreglo.** `meta.construccion_completa = <epoch>` que el ingestor escribe cuando **todas** las fuentes devuelven
  `completo` y `sin_cambios`; `informe()` en `activo` cae a `escanear()` mientras falte. El hilo encadena vueltas sin
  dormir mientras alguna fuente no esté `completo` (cediendo ≥ 50 ms entre lotes para la UI) y duerme 60 s solo cuando
  todo está al día.

### A3. Una transacción de escritura por fichero y por minuto (1.370 ficheros de Claude)
- **Evidencia.** `ingerir_fichero` llama a `almacen.fuente()` antes de saber si el fichero cambió (`ingesta.py:155`), y
  `fuente()` siempre hace `BEGIN IMMEDIATE` + `UPDATE visto_en` + `COMMIT` (`almacen.py:300-309`). Hay 1.370 logs de
  Claude (plan línea 63): 1.370 commits/min en WAL con `secure_delete`, más lo mismo en Codex.
- **Escenario.** El `-wal` crece sin consumo nuevo, SSD escribiendo cada minuto, y el criterio «≤ 300 ms por vuelta»
  (plan línea 110) se incumple por el puro paso por los ficheros sin cambios.
- **Arreglo.** El ingestor carga una vez `lecturas ⋈ fuentes` en memoria (solo lectura), hace `os.stat` por fichero y
  solo entra en `ingerir_fichero` si `(inodo, tam, mtime_ns)` difiere. `visto_en` se actualiza en lote al final de la
  vuelta, en una sola transacción, o solo cuando cambia el día.

### A4. Nombres de proyecto: el diseño promete reproducir los antiguos, 1.2c decidió unificarlos, y la marcha atrás pierde las claves migradas
- **Evidencia.** 1.3 §2: `proyecto_de(agente, proyecto_raw)` reproduce «OpenCode · dir», «Hermes/<perfil> · cwd», «pi ·
  a/b». Pero la base guarda un `proyecto_raw` **unificado** (`ingesta.py:248-260`, «un proyecto se llama igual en Claude
  Code y en Codex»), sin `Users/<usuario>` y con 3 trozos, y 1.2c lo dio por hecho (plan línea 63, «proyecto unificado»).
  Reproducir «pi · b/c» a partir de «a/b/c» es posible (últimos 2 trozos), pero «Hermes/<perfil>» no (ver M1), y
  «OpenCode · <directory>» salía de `session.directory` mientras la base usa `message.path.cwd` (`ingesta_opencode.py:30,59`).
  Impacto medido en la instalación real: 13 claves en `config.proyectos`, 12 ya con la forma unificada y **1** con `pi · `.
- **Escenario.** Se migran las claves (plan línea 124) a los nombres nuevos; a los 3 días se vuelve a `apagado`:
  `ajustes.nombre_proyecto(raw, conf)` (`ajustes.py:25-28`) busca el nombre antiguo y el alias del proyecto de pi
  desaparece; el usuario lo vuelve a poner, y al volver a `activo` se pierde el nuevo. Además, el mismo directorio sigue
  siendo tres proyectos (uno por agente) si se reproducen los prefijos: se desaprovecha la unificación y `proyectos` del
  panel sigue partido.
- **Arreglo (recomendación: unificar, no reproducir).**
  - `activo` usa el nombre unificado; la sombra compara por `(agente, día, proveedor, modelo)` (B1), **no** por proyecto,
    y la divergencia de nombres se firma como cambio de producto (nota de versión: «un directorio es un solo proyecto
    en todos los agentes»).
  - Migración **aditiva**: `config.proyectos` conserva la clave antigua y añade la nueva con el mismo valor;
    `nombre_proyecto` prueba las dos. Así `apagado` y `activo` leen la misma configuración. Se limpia en la fase 2.
  - `plan_de()` sí tiene que ser **byte a byte** igual que hoy, porque es la clave de los 22 `aliases`:
    Claude → `"Claude (Max)"` constante (no `plan_ccs`, que es código muerto hoy; activarlo sería una divergencia nueva y
    una decisión aparte); Codex → `alias_de(proveedor)` (`openai` → `"OpenAI (Codex)"`; otro `model_provider` es una
    mejora a firmar); pi → `proveedor` tal cual; OpenCode → `providerID` tal cual; Hermes → URL pública o `billing_provider`
    (los 3 alias con `://` no llevan `?` ni `@`, así que el recorte de credenciales de 1.2e no los rompe).

### A5. `apagado` «todo como hoy» contradice la marcha atrás que el plan ya acordó
- **Evidencia.** Plan línea 133: «ingesta mínima también en `main` mientras dure la fase» (para que una vuelta atrás no
  deje huecos cuando Claude borra a los 30 días). 1.3 §5: `apagado` = «todo como hoy», que se lee como «sin ingesta».
- **Escenario.** Marcha atrás el día 3, se queda en `apagado` 5 semanas, se vuelve: hay un hueco permanente de una
  semana en Claude.
- **Arreglo.** Separar los dos ejes: `lectura ∈ {escanear, consultar}` e `ingesta ∈ {sí, no}`. `apagado` = escanear +
  ingesta sí; `sombra` = escanear + ingesta + comparación; `activo` = consultar + ingesta. Un cuarto valor
  `desactivado` (ingesta no) solo a mano en `config.json`, para incidencias. Y que la vuelta atrás **no borre la base**.

### A6. «Mismo informe en el día de 25 h» no se cumple sin tocar `agregar()`
- **Evidencia.** `agregar()` recibe `horas` con claves `"%Y-%m-%dT%H"` locales y las vuelve a epoch con `time.mktime`
  (`agregacion.py:177`). El 25-10-2026 las horas 00Z y 01Z son las dos «T02» local: `consulta.py` tendría que fundirlas en
  una clave y `mktime` resuelve la ambigüedad a una de las dos; `ritmo` (edad < 3.600 s, `:181`) se equivoca durante una
  hora. El criterio 1.3 §7 lo exige y el diseño dice «sin cambiar `agregar()`».
- **Arreglo.** Cambio mínimo en `agregar()`: aceptar claves de `horas` como **epoch entero de inicio de cubo** (y seguir
  aceptando el texto para `escanear()`); `consulta.py` entrega epoch. Firmar que el 25-10 entre 02:00 y 03:00 la ruta
  antigua es la equivocada (su `ritmo` y su `cinco_h` de esa hora) para que la sombra no lo cuente como residuo.

---

## MEDIO

### M1. Perfil de Hermes: se puede recuperar (por diccionario), pero no desde `horas`
- **Evidencia.** `fuentes.id_fuente` es `sha256(ruta relativa)[:32]` (`ingesta.py:62-68`); el universo de rutas es
  `profiles/<perfil>/state.db` y `state.db`: calcular la huella de cada perfil presente en disco y casarla con
  `fuentes` da el nombre mientras el perfil exista (el red team ya lo dijo: el hash «se invierte por diccionario»). Pero
  `horas` no lleva `fuente_id` (`almacen.py:70-75`), solo `turnos`.
- **Arreglo.** Para Hermes (127-261 filas) leer de `turnos` ⋈ `fuentes`, no de `horas`, y mostrar el perfil en
  `origenes`/Ajustes, no en el nombre del proyecto (A4). Un perfil borrado sale como `hermes/<huella corta>`: firmado.

### M2. `horas` por hora UTC no parte el día local en zonas de media hora
- **Evidencia.** El cubo `hora` es `hora_utc - hora_utc % 3600` (`almacen.py:378`); en `Asia/Kolkata` (+05:30) el cubo
  18:00Z contiene 23:30-00:30 locales: dos días. El plan exige fixtures con «una de media hora» (línea 104).
- **Arreglo.** O bien `dia` sale de `turnos` para la ventana consultada (31 días ≈ 120.000 filas, ~50 ms por el índice
  `(espacio_id, hora_utc)`) y `horas` solo para `ritmo`/`cinco_h`; o bien se documenta que las zonas de media hora se
  aproximan al cubo y el test de 3 zonas usa solo zonas enteras. Para el dueño (Europe/Madrid) no cambia nada; para la
  «aplicación» sí.

### M3. USD: dos fuentes de coste que la base no tiene
- **Evidencia.** pi trae `usage.cost.total` y el lector lo usa en `horas[..]["usd"]` (`lectores_logs.py:241-243`); la
  base no guarda dinero (1.3 §3). Hermes no tiene turnos reales: el `CLIFF` por turno se aplica a una fila acumulada
  (ya en divergencias, plan línea 137). `TARIFAS` por subcadena de modelo (`planes_y_tarifas.py:208-213`) es igual en
  las dos rutas.
- **Arreglo.** Firmar «USD de pi en proveedores de pago» como divergencia de `cinco_h.usd` y `ritmo.usd` (solo
  informativo en la barra, `costbar.py:155`). No añadir columna de coste: el equivalente API cambia con las tarifas y
  se recalcula al consultar.

### M4. Campos del informe que la base no produce solos
- `fuentes` y `bases` («Claude Code (1370)», `agregacion.py:86-87`, usados en `panel.py:63`): salir de `fuentes` con
  `visto_en` reciente, contados por `agente`. `nuevos` (`costbar.py:170`): el resumen de la última vuelta de ingesta,
  leído de `meta`. `anomalias` «visible en el panel» (esquema, plan línea 73): 1.3 no lo incluye; al menos un contador
  en el informe y en `/ajustes`.
- Conexiones: `sqlite3` es por hilo; `servidor_local` es `ThreadingTCPServer` (`:325`) y `trabajador_barra` otro hilo.
  `consultar()` abre y cierra su conexión en cada llamada (coste < 1 ms); prohibido compartir una conexión global.
- `mantenimiento()` (checkpoint `TRUNCATE`, `almacen.py:549-551`) y el `quick_check` diario: nadie los llama en 1.3.
  Van en el hilo de ingesta, con `busy_timeout` propio y tolerando `busy=1` (medido: un lector largo lo deja a medias y
  no pasa nada; el WAL se trunca en el siguiente intento).

### M5. Marcha atrás tras 3 días en `activo` (respuesta directa a la pregunta 4)
- Lo que se pierde: nada de datos. `state.json` lleva 3 días sin escribirse pero sigue con `version == 12`
  (`config_y_estado.py:186-195`): `escanear()` reparsea solo los ficheros con `mtime:size` distinto (`agregacion.py:31-41`),
  segundos, no 15 s. Si el interruptor se cambia desde Ajustes, `olvidar_cache()` borra `state.json` (`servidor_local.py:298`)
  y la primera vuelta de `apagado` paga los ~15 s en el hilo trabajador (aceptable) o en el hilo principal si alguien usa
  `costbar.py` como app (`costbar.py:78,106`): ese camino heredado debería retirarse o pasar por el trabajador.
- Lo que sí se rompe sin A4 y A5: alias de proyecto con claves migradas y el hueco de ingesta. Con ambos, la vuelta es
  un cambio de un valor en `config.json`. Ensayo que falta en 1.3: el plan pide «ida, 48 h de vuelta, ida, comparado con
  un control» (línea 134); 1.3 no dice quién lo hace ni cuándo. Hacerlo dentro de la sombra (que ya corre con `escanear()`
  como lectura): apagar la ingesta 48 h y comprobar que al volver no hay turnos nuevos con `hora_utc` dentro del hueco
  que `escanear()` no tenga.

---

## BAJO

- **Fichero de divergencias** (1.3 §5): lleva nombres de proyecto, modelos y URLs de proveedor; crearlo 0600 con
  `os.open` (como `copia()`), sin la URL completa, y con rotación.
- **Autorizador** (1.3 §7): funciona para INSERT/UPDATE/DELETE (medido) y no cubre `PRAGMA wal_checkpoint` ni
  subprocesos. Úsese como cinturón en los tests in-process de `consulta.py`; la garantía real es `mode=ro` +
  `query_only` (`almacen.py:123-124`) y el test estático de imports de B2.
- **`escanear()` tras A4/A5 en `activo`** sigue escribiendo `state.json` en cada fallback (`agregacion.py:84`): es inocuo,
  pero conviene que `informe()` registre «fallback» con el motivo una vez por proceso para que la sombra no mezcle
  lecturas de las dos rutas sin saberlo.
- **Criterio «4 consultores con la base vacía ≤ 1 s»** es trivial cuando `consultar()` no ingiere; el criterio útil es
  «4 consultores **mientras** el escritor aplica lotes de 500» (WAL no bloquea lectores; medido con checkpoint a la vez).
- **Descartado tras verificar:** `mode=ro` sin `-shm` con el escritor abierto lee bien; un lector que abre durante
  el checkpoint lee bien; `VERSION_ESTADO` no hace falta tocarlo en 1.3.

---

## Lo que el diseño omite y debería decir (resumen accionable)

1. Nivel y universo de la comparación en sombra: por `(agente, día local, proveedor, modelo)`, días cerrados de ≤ 27 días
   con fichero vivo, Hermes fuera, categorías calculadas, calendario hasta el 28-10 (B1).
2. Quién puede ser escritor (barra y `--ingerir`), latido refrescado por vuelta, `panel.py` nunca (B2).
3. `user_version` y `DatabaseError` en `consulta.py`; fallback anotado una vez (A1).
4. `meta.construccion_completa` y la política de vueltas encadenadas hasta `completo` (A2).
5. `stat` antes de `fuente()`; `visto_en` en lote (A3).
6. Nombres unificados + migración aditiva de `config.proyectos` + `plan_de` byte a byte (A4).
7. Dos ejes en el interruptor; `apagado` sigue ingiriendo (A5).
8. `horas` con clave epoch en `agregar()` (A6); `dia` desde `turnos` o zonas enteras documentadas (M2).
9. Hermes desde `turnos` ⋈ `fuentes` para el perfil (M1); divergencia USD de pi (M3).
10. `fuentes`/`bases`/`nuevos`/anomalías en el informe; conexión por llamada; `mantenimiento()` y `quick_check` en el hilo
    de ingesta (M4). Ensayo de marcha atrás dentro de la sombra (M5).

## Supuestos
- Las 12 claves `a/b/c` de `config.proyectos` coinciden con el `proyecto_raw` unificado porque no llevan `Users/<usuario>`
  ni el nombre del usuario como primer trozo (medido 0 en ambos casos); confianza alta. Si alguna viniera de un directorio
  distinto con los mismos 3 últimos trozos, la migración aditiva de A4 la cubre igual.
- La instalación real arranca `barra.py` (`install.sh:59`), así que el `rumps.Timer` de `costbar.py` en el hilo principal
  solo afecta a quien lance `costbar.py` sin argumentos; confianza alta.
- Las cifras de volumen (1.370 logs, 20 s de construcción, 8 MiB por pasada) son las del plan y de `ingesta.py`; no las he
  vuelto a medir hoy.

Status: DONE_WITH_CONCERNS
Summary: El diseño 1.3 es correcto en lo estructural (lectura `mode=ro`, lo local solo al consultar, un punto `informe()`),
pero la sombra no puede medir lo que promete y el puesto de escritor caduca con la barra viva, lo que abre la puerta a que
el panel escriba. Con los dos bloqueantes y los seis altos resueltos en el texto del plan, 1.3 se puede codificar sin
cambiar el esquema de la base.
