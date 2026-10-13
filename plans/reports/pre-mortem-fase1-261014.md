# Pre-mortem de la fase 1 (almacén de turnos en SQLite), plan v2

Fecha: 2026-10-14 (Europe/Madrid). Alcance: la fase 1 y la 1b del plan `plans/261013-2350-sqlite-historico/plan.md`
(v2), con la fase 0 ya fusionada en `main`. Es la segunda ronda: no se repite lo que ya recogen
`pre-mortem-261013-sqlite-historico.md`, `red-team-261013-sqlite-historico.md` y `revision-fase0-261014.md`. Aquí solo
entra lo que introduce la v2 o lo que deja abierto.

Método: suponemos que en octubre de 2027 la fase 1 ha fracasado o ha hecho daño. Cada causa se ha contrastado con el
código de `main` y con mediciones de solo lectura sobre los logs reales de esta máquina. Los scripts están en el
scratchpad y no tocan el repo. No se reproducen rutas, proyectos, ids ni hosts: [redactado].

## Mediciones (solo lectura, una máquina)

| Medida | Resultado |
|---|---|
| Claude Code: líneas con `message.usage` | 235.765, de las que 124.279 (53 %) están en `subagents/*.jsonl`. Los subagentes suman el **38,6 % de los tokens** |
| Claude Code: `message.id` únicos | 122.990. **Líneas sin `message.id`: 0**, todas de la versión 2.1. Solo hay 1 id sintético (UUID, `<synthetic>`) |
| Claude Code: `requestId` | **Falta en 19.719 líneas (8,4 %)**: 16.643 del hilo principal y 3.076 de subagentes |
| Claude Code: mismo id con entrada o caché distinta entre líneas | 973 ids, todos dentro del mismo fichero. La entrada siempre **sube** y en 468 de ellos también la caché |
| Claude Code: mismo id en varios ficheros | 64, todos de subagentes, con valores idénticos (0,064 % de los tokens) |
| Claude Code: mismo id en varias sesiones | 2, ambos de subagentes, con el mismo modelo, entrada y caché y **distinta salida**: son copias tomadas a mitad de la respuesta, no colisiones |
| Claude Code: forma de los ids | 216.090 de 28 caracteres (Claude). 11.582 de 34 (GLM y Claude). **8.080 de 21 a 25 caracteres, de modelos servidos por proxy** (qwen, deepseek, glm), con `_` en el cuerpo |
| `*.json` sueltos en `~/.claude/projects` | 1.263, que hoy también se parsean. 0 de 300 llevan uso (son metadatos de agentes) |
| Codex | 45 ficheros, 7.561 líneas `event_msg/token_count`, **sin `turn_id`**. 20 ficheros tienen 2 `session_meta`. La clave (`session_id`, `total_token_usage.total_tokens`) da 7.299 valores y 0 repetidos entre ficheros |
| pi | 9.620 turnos. El `id` de línea es único (0 repetidos) y la cabecera de sesión lleva `id` en los 22 ficheros |
| OpenCode | `opencode.db`: 4.350 mensajes con modelo (de enero a octubre de 2026). `opencode-.db`: **esquema más nuevo** (`workspace_id`, `path`, `project_directory`) con 1 mensaje. 0 ids en común entre las dos |
| Hermes | `messages.token_count` es **NULL en todos** los mensajes del asistente, así que no hay uso por mensaje. `session_model_usage` existe en los 4 perfiles (`first_seen`, `last_seen`, `task`) y **suma más que `sessions`**: entrada +22 %, salida +47 % y caché +17 % en el perfil principal. 25 de 63 sesiones usan varios modelos y 5 duran más de un día |
| Marcas de tiempo con zona | **100 %** en Claude, Codex y pi. OpenCode y Hermes usan epoch, que ya es UTC |
| Volumen | Claude: unos 3.000 mensajes al día de media (máximo 9.527) en 41 días con datos. Codex: unos 800 turnos al día en 9 días. **Total: unos 1,4 millones de filas al año** |
| Parseo completo de los JSONL | 14,4 s con la caché de disco caliente, para 4,4 GB de Claude y 2,5 GB de Codex. En frío se acerca a los ~16 s ya conocidos |
| Ficheros tocados en la última hora | 21 ficheros, 268 MB. El mayor mide **257 MB**. Reparsearlos lleva 0,73 s |
| Base sintética de 3 años (4,38 M filas, PK `TEXT`) | 702 MB. Insertar: 39 s. Ventana de 5 h: 0,5 ms. 31 días por día, modelo y proyecto: 100 ms. **Histórico completo por mes: 2,0 s**. `quick_check`: 1 s. **`integrity_check`: 11 s**. `VACUUM INTO`: 1,7 s. 3.000 upserts: 5 ms |
| Cifrado en el venv | `cryptography` **no está instalado**. `requirements.txt` solo trae pillow y pyobjc. La biblioteca estándar tiene `hashlib.scrypt` pero **ningún AEAD** |

Salvedades: una sola máquina, 41 días de Claude (por `cleanupPeriodDays: 30` más ficheros vivos) y 9 de Codex. El
volumen anual es una extrapolación.

---

## Causas ordenadas por riesgo

### 1. `INSERT OR REPLACE` por clave dejó que el orden de lectura decidiera la cifra
- **Qué pasó.** La v2 fija la regla «`INSERT OR REPLACE` por clave de turno». Pero la misma clave llega con valores
  distintos según cuándo y desde dónde se lea:
  - en 973 mensajes la entrada sube entre líneas;
  - la salida crece mientras la respuesta se escribe;
  - hay 2 copias en subagentes tomadas a mitad de la respuesta.

  Con `REPLACE` gana la última fila procesada, no la más completa:
  - Un escaneo que pilla una línea a medias y después lee otra copia rebaja el turno para siempre.
  - Al fundir dos dispositivos (`INSERT OR IGNORE`, como dice la v1) se queda la primera, que puede ser la parcial.

  Además:
  - La clave «`message.id` + `requestId`» que proponía el pre-mortem v1 no es estable, porque `requestId` falta en el
    8,4 % de las líneas.
  - Los ids de los modelos servidos por proxy (8.080, con longitud variable) no tienen ninguna garantía de unicidad
    entre dispositivos ni entre proxies.
- **Señal temprana.** Un mismo día da totales distintos según el orden de reconstrucción. Aparecen turnos de Claude con
  salida menor que en el log.
- **Probabilidad / impacto.** Alta / Alta: es silencioso y permanente.
- **Cambio al plan.**
  - Sustituir `INSERT OR REPLACE` por un upsert **monótono e independiente del orden**:
    `ON CONFLICT(clave) DO UPDATE SET gi=max(gi,excluded.gi), go=max(go,excluded.go), cr=max(…), cw=max(…)`.
  - Si cambia el modelo o baja algún contador, la fila no se toca y suma 1 a un contador de conflictos que se ve en el
    panel.
  - Clave de Claude: `claude:` + `message.id` cuando el id tiene la forma de Anthropic (`msg_` y 24 o 30 caracteres).
    En cualquier otro caso, `claude:` + `sessionId` + `message.id`. Nunca `requestId`.
  - Prohibido filtrar `isSidechain`: los subagentes son el 38,6 % de los tokens.
- **Test.**
  - Ingerir los mismos turnos en orden directo e inverso, y con una copia truncada al final, debe dar contadores
    idénticos.
  - Dos dispositivos con el mismo id de proxy y valores incompatibles no se funden: queda un conflicto registrado.

### 2. Hermes: todo su histórico cayó en la hora de la primera construcción y luego en saltos
- **Qué pasó.** La v2 pide un «delta por lectura fechado en la lectura». Hermes no guarda uso por mensaje
  (`token_count` es NULL siempre), así que:
  - en la primera lectura, el delta es el total histórico: meses de Hermes quedaron sumados en una sola hora de
    octubre;
  - cada hueco de la barra (Mac apagado o de viaje) quedó sumado en la lectura siguiente;
  - un `rewind` o una compresión que hace bajar el total de una sesión dio deltas negativos;
  - el perfil se identifica por el nombre de su carpeta: al renombrarlo se duplicó todo.

  Aparte, la fuente: `session_model_usage` suma un 17–47 % más que `sessions`, porque tiene una columna `task` (tareas
  auxiliares). Cambiar de una a otra mueve las cifras de Hermes sin que nadie lo decida.
- **Señal temprana.** Un pico de Hermes en el día de la migración. Barras de Hermes en días en que no se usó.
- **Probabilidad / impacto.** Alta / Media: el volumen de Hermes es pequeño, pero el error es permanente y se ve.
- **Cambio al plan.**
  - Fuente: `session_model_usage`, por (`session_id`, `model`, `task`). Si no existe, `sessions`.
  - **El dueño elige** si las tareas auxiliares cuentan y queda escrito. Si cuentan, la subida de Hermes entra en la
    lista de divergencias aceptadas.
  - La construcción inicial fecha cada fila en su `last_seen` (o en `last_activity_at`), nunca en la hora de la
    lectura.
  - Los deltas posteriores se fechan en el `last_seen` nuevo, acotado a `[last_seen previo, ahora]`.
  - Delta negativo: se descarta y se registra.
  - Clave: `hermes:` + `session_id` + `model` + `task` + total acumulado. Sin el nombre del perfil.
  - El `CLIFF` por turno es imposible en Hermes: va a la lista de divergencias aceptadas.
- **Test.**
  - Construir sobre una base sintética de Hermes con sesiones de 3 días no deja ninguna fila en la hora de la
    construcción.
  - Bajar el total no resta.
  - Renombrar el perfil no duplica.

### 3. «Solo la barra escribe» no existía: ocho llamadores de `escanear()` construyeron a la vez
- **Qué pasó.** Hoy `escanear()` lee y además escribe `state.json`. La llaman:
  - el trabajador;
  - `/origenes` y `/ajustes` (en hilos HTTP);
  - `panel.py`, como subproceso de `GET /` con un tope de 120 s;
  - `popover.py`;
  - la CLI (`--print`, `--proveedores`);
  - `analizar_gasto.py`;
  - **el `rumps.Timer` de `costbar.py`, en el hilo principal**.

  La fase 1 colgó la ingesta de `escanear()`. Con la base vacía, cada llamador empezó una construcción de unos 15 s:
  - la app rumps se congeló;
  - el panel agotó sus 120 s;
  - el ciclo de servidores, que va en el mismo hilo que el escaneo (`trabajador_barra.py:47-58`), se paró;
  - en Windows no hay barra, así que nadie era «el dueño» y no se escribió nada.

  El cerrojo previsto era `fcntl`, y `config_y_estado.py` lo importa en la cabecera: en Windows el paquete ni siquiera
  carga.
- **Señal temprana.** «database is locked» en el log. Un punto de servidores gris durante decenas de segundos. Una
  CLI que tarda 15 s.
- **Probabilidad / impacto.** Alta / Media-alta.
- **Cambio al plan.**
  - Separar `ingerir()`, el único que escribe, de `consultar()`, de solo lectura y que nunca parsea. Los ocho
    llamadores pasan a `consultar()`.
  - El escritor lo decide un **arrendamiento en la propia base**: fila `meta('escritor', id, caduca_en)` tomada con
    `BEGIN IMMEDIATE`. No vale la identidad del proceso ni `fcntl`.
  - Si no hay escritor vivo, la CLI puede ingerir solo con `--ingerir`.
  - La ingesta corre en un hilo propio, separado del ciclo de servidores, en lotes de ≤ 200 ms por transacción.
  - Mientras la base está a medio construir, `consultar()` sirve la ruta de `state.json`.
- **Test.**
  - 4 procesos llaman a `consultar()` con la base vacía: solo uno ingiere y ninguno tarda más de 1 s.
  - El ciclo de servidores sigue entregando mientras dura la construcción.
  - Importar `infobar` con `fcntl` ausente (simulado) no falla.

### 4. El export cifrado era inservible o inseguro
- **Qué pasó.** La v2 decide un «export cifrado propio» portable con passphrase, KDF y AEAD. Pero:
  - el venv no tiene ninguna biblioteca de AEAD y la estándar tampoco;
  - para no añadir dependencias se hizo con `hashlib` y XOR o HMAC-CTR casero, sin autenticar la cabecera;
  - la copia automática no puede pedir una passphrase, así que, o se guardaba la passphrase en el Llavero (el
    red team ya lo descartó en §2.1), o no había copias automáticas. Sin Time Machine (decisión 2), volvió la causa 8
    de la v1: el disco murió y no había copia;
  - la base de 3 años (700 MB) se cifró leyéndola entera en memoria o pasando por un temporal en claro;
  - nadie probó a restaurar: el día que hizo falta, los parámetros de la KDF no estaban en el fichero, o la passphrase
    se había perdido.
- **Señal temprana.** Un PR con «cifrado» que no importa `cryptography`. No existe un test de restauración. El export
  necesita un temporal.
- **Probabilidad / impacto.** Alta / Alta.
- **Cambio al plan.**
  - Dependencia explícita: `cryptography` (ruedas para macOS y Windows), con versión fijada en `requirements.txt`.
    Prohibido el cifrado hecho a mano.
  - Formato versionado:
    - cabecera con magia, versión y parámetros de scrypt, autenticada como AAD;
    - AES-256-GCM o ChaCha20-Poly1305 **en trozos** (por ejemplo, de 1 MiB) con nonce derivado del número de trozo y
      una marca de último trozo.
  - Se exporta un `VACUUM INTO` a un temporal privado que se borra, nunca el fichero vivo.
  - Para las copias automáticas: **cifrado a destinatario**. La clave pública vive en la app y la privada, protegida
    con passphrase, queda fuera de la máquina (estilo age). La passphrase solo se pide al restaurar.
  - Rotación de copias con la misma caducidad que los datos (v1, causa 10).
- **Test.**
  - Vector de prueba sintético en `tests/datos/`.
  - Ida y vuelta en `HOME_FALSO` que compare los contadores enteros.
  - Un byte alterado en la cabecera o en un trozo hace fallar el descifrado.
  - Un fichero truncado se rechaza.
  - Restauración probada en Windows (CI o máquina) antes de dar la fase por cerrada.

### 5. La fase en sombra nunca convergió y se cerró «a ojo»
- **Qué pasó.** El criterio de la v2 es «prueba diferencial con lista de divergencias aceptadas y firmadas», durante
  7 días. Pero:
  - el cálculo viejo y el nuevo leían los logs en momentos distintos, mientras los ficheros crecían, y cada minuto
    había diferencias espurias;
  - se compararon días abiertos (hoy) y la ventana de 5 h, que dependen de la hora;
  - la lista de divergencias no tenía tolerancias numéricas: «Claude baja por deduplicación entre ficheros» acabó
    tapando cualquier bajada;
  - a los 7 días seguía habiendo ruido y se cerró igualmente.
- **Señal temprana.** Diferencias distintas de cero en días que ya no reciben logs. Una lista de divergencias que
  crece cada día.
- **Probabilidad / impacto.** Media-alta / Media.
- **Cambio al plan.** Criterio medible:
  - Las dos rutas se calculan **en la misma vuelta y desde la misma lectura**: el parser produce una sola vez la lista
    de turnos y de ahí salen `agregar()` y el almacén.
  - Solo se comparan días cerrados que siguen en los logs.
  - Tolerancia por agente, en contadores enteros:
    - **Codex, pi y OpenCode**: 0, salvo las categorías firmadas;
    - **Claude**: solo la deduplicación entre ficheros, explicada id por id;
    - **Hermes**: solo la categoría que elija el dueño (causa 2).
  - Cada divergencia lleva categoría, agente, días y tokens. El residuo sin explicar tiene que ser 0.
  - **7 días seguidos** sin residuo. Cada residuo devuelve el contador a cero. A los 3 reinicios se para y decide el
    dueño.
  - Los 7 días deben incluir al menos un reinicio de la barra, un guardado de Ajustes (`olvidar_cache`), una sesión
    de reposo del Mac y uso de al menos 3 agentes.
  - El cambio de hora del **25-10-2026** probablemente caerá dentro de la sombra: aprovecharlo, o cubrirlo con
    fixtures (ya previstos).

### 6. Windows: la fase 1 «funcionaba» y hubo que reescribirla para el producto final
- **Qué pasó.** Todo se diseñó y se probó en macOS. En Windows:
  - `config_y_estado.py` no importa (`fcntl`);
  - `"/projects/" in ruta` y `"/.claude/" in ruta` (`lectores_logs.py:81,153`) fallan con `\`, así que el proyecto
    cae en «codex/otros» y el plan desaparece;
  - `cwd.split("/")` en pi no parte nada;
  - `id_fuente` era el hash de una ruta sin normalizar (mayúsculas, separadores y `\\?\`): las fuentes salían
    distintas en cada lectura;
  - `TZFix` usa `TZ`/`tzset`, que no existen en Windows, así que el test de «otra TZ» no probaba nada;
  - `zoneinfo` sin el paquete `tzdata` lanzó `ZoneInfoNotFoundError`;
  - `os.replace` de `state.json` falló con `PermissionError` mientras otro proceso lo tenía abierto (doble escritura de
    la transición);
  - la base quedó en una carpeta sincronizada por OneDrive, y el antivirus bloqueó `-wal` y `-journal` con «disk I/O
    error».
- **Señal temprana.** Nadie ha ejecutado `python -m unittest` en Windows antes de cerrar la fase.
- **Probabilidad / impacto.** Alta (cuando se porte) / Alta: reescritura.
- **Cambio al plan.**
  - Módulo `infobar/plataforma.py` con cerrojo, rutas de datos, permisos y almacén de secretos.
    - Datos en `%LOCALAPPDATA%\CostGuard` en Windows y en `~/.cost-guard` en macOS.
    - Aviso si la ruta cuelga de OneDrive, iCloud o Dropbox.
  - Toda ruta de log se trata con `pathlib`/`PurePath`. `id_fuente` es el hash de `tipo` + la ruta **relativa al
    origen**, con `/` y en minúsculas en Windows.
  - Hora con `zoneinfo` y `tzdata` en `requirements.txt` (solo Windows). La zona se **inyecta** a las funciones, en
    lugar de cambiar `TZ`.
  - Reintentos acotados en `os.replace` y en `SQLITE_BUSY` o `SQLITE_IOERR` transitorios.
- **Test.**
  - Los lectores, con rutas `ntpath` (`C:\Users\x\.claude\projects\C--proj\…`), sacan el mismo proyecto y la misma
    clave que con rutas POSIX.
  - CI con un job de Windows solo para la suite del almacén; si no, un ensayo manual documentado.

### 7. OpenCode cambió de base y el histórico de OpenCode se paró sin avisar
- **Qué pasó.** El lector recorre `opencode.db` y `opencode-.db` y hace `break` tras **la primera que existe**
  (`lectores_logs.py:348-386`). La segunda tiene un esquema más nuevo y ya contiene 1 mensaje. Cuando OpenCode pasó a
  escribir solo en la nueva:
  - la vieja seguía existiendo y era la que se leía;
  - no entró nada nuevo y no hubo ningún error.

  Además, la consulta agrupa por hora (`group by … time_created/3600000`):
  - no hay clave por mensaje;
  - el `CLIFF` se aplica a la suma de la hora;
  - la caché `_OPENCODE` solo guarda una base.
- **Señal temprana.** El último turno de OpenCode se queda fijo mientras OpenCode se sigue usando.
- **Probabilidad / impacto.** Media / Alta: pérdida total y silenciosa de un agente.
- **Cambio al plan.**
  - Leer **todas** las `opencode*.db` y deduplicar por `message.id` (0 ids en común, verificado).
  - Una fila por mensaje con `time.completed` (o `time_created` si no hay `completed`).
  - Caché por base.
  - Aviso de «fuente callada» cuando un agente con uso en los últimos 7 días lleva más de 48 h sin turnos nuevos y su
    base ha cambiado de firma.
- **Test.** Una base con el esquema nuevo y ninguna vieja se lee. Con las dos, se suman sin duplicar.

### 8. Codex: la clave de turno no existía y se inventó con la marca de tiempo
- **Qué pasó.** Codex no tiene `turn_id`, y el docstring de `parsear` describe un doble registro
  (`token_usage_record`) que ya no aparece: el 100 % de los turnos actuales son `event_msg/token_count`. La clave se
  hizo con `ts[:19]` y el contenido. Hay 262 líneas que repiten el total acumulado dentro del mismo fichero: en las que
  el segundo cambiaba entre las dos copias, el turno salió duplicado. No se ha medido cuántas son. 20 ficheros llevan 2 `session_meta` (sesiones reanudadas): se cogió el id de la segunda y la misma sesión
  quedó con dos claves.
- **Señal temprana.** Turnos de Codex con la misma salida separados por un segundo.
- **Probabilidad / impacto.** Media / Media.
- **Cambio al plan.** Clave `codex:` + `session_id` de la **primera** `session_meta` + `total_token_usage.total_tokens`.
  Medido: 7.299 claves y 0 colisiones entre ficheros. Contadores tomados de `last_token_usage`. Actualizar el
  docstring.
- **Test.** Fixture con líneas `token_count` repetidas en segundos distintos, con dos `session_meta`, y un fork en
  otro fichero: un solo turno por total acumulado.

### 9. Reparsear el fichero entero cada minuto, ahora con escrituras: disco y WAL
- **Qué pasó.** El caché actual reparsea entero cualquier fichero cuyo `mtime:size` cambie. Un fichero vivo de 257 MB y
  21 ficheros con 268 MB en la última hora se reparseaban cada minuto. Con el almacén, cada vuelta reescribía miles de
  filas idénticas:
  - WAL inflado;
  - escrituras en el SSD;
  - con `secure_delete=ON`, cada página reescrita se ponía a cero.

  Sumado al GIL, la interfaz de PyObjC se notaba más lenta en cada vuelta.
- **Señal temprana.** El `-wal` crece cada minuto sin uso nuevo. El escaneo del minuto supera 1 s.
- **Probabilidad / impacto.** Media / Media.
- **Cambio al plan.**
  - Lectura **incremental por desplazamiento**: estado por fuente con `(inodo o file-id, tamaño, desplazamiento, hash
    de los primeros 4 KiB)`.
  - Si el hash o el inodo cambian, o el tamaño baja, se trata como generación nueva y se relee desde el principio.
    Como el upsert es monótono, no resta.
  - Una línea JSON incompleta al final no avanza el desplazamiento.
  - El upsert omite filas sin cambios (`WHERE` con valores distintos).
- **Test.**
  - Añadir líneas a un fichero solo procesa las nuevas.
  - Truncar y reescribir no resta.
  - Una línea partida a medias se procesa entera en la vuelta siguiente.
  - Criterio: la vuelta del minuto, con un fichero vivo de 250 MB, dura ≤ 300 ms.

### 10. Con 3 años, el panel histórico tardaba segundos y las comprobaciones de integridad, más
- **Qué pasó.** Con unos 1,4 M de filas al año, a los 3 años:
  - la base medía unos 700 MB;
  - una consulta de histórico completo, 2 s;
  - `integrity_check`, 11 s;
  - un export, 700 MB.

  La v2 dice «las agregaciones por hora/día/ventana son vistas o caché derivada». Se hicieron como **vistas** y la
  fase 2 congeló el panel. Las copias crecieron sin control.
- **Señal temprana.** La consulta mensual supera los 500 ms con la base sintética.
- **Probabilidad / impacto.** Media / Media.
- **Cambio al plan.**
  - Tabla `horas` **materializada e incremental** (`espacio_id`, `hora_utc`, `agente`, `modelo`, `proyecto_raw`,
    contadores), mantenida en la misma transacción que el upsert de turnos y reconstruible desde `turnos`.
  - Índice `(espacio_id, hora_utc)` en `turnos`.
  - Clave compacta: valorar `WITHOUT ROWID` o un hash de 16 bytes más el id crudo, y medir.
  - `integrity_check` nunca en la barra; solo `quick_check` diario.
  - Base sintética de 3 años (generador en `tests/`, no en `datos/`) con estos criterios:
    - ventana de 5 h ≤ 5 ms;
    - 31 días ≤ 50 ms;
    - histórico mensual ≤ 100 ms;
    - tamaño ≤ 1 GB.

### 11. La hora «UTC» se calculó con las funciones de hora local
- **Qué pasó.** Para no duplicar el parser, el almacén reutilizó `a_local()`, `time.localtime` (OpenCode y Hermes) y
  `time.mktime` (`agregacion.py:177`), y pasó a UTC con `time.timezone`. Resultado:
  - en el cambio de hora la conversión se desplazó una hora;
  - una marca sin zona (el fallback de `a_local` «se devuelve tal cual») se guardó como si fuera UTC.

  Hoy el 100 % de las marcas tienen zona, pero un formato futuro sin ella entró en silencio.
- **Señal temprana.** Un test de «otra TZ» que solo pasa en Europe/Madrid.
- **Probabilidad / impacto.** Media / Alta: el error queda escrito en el histórico permanente.
- **Cambio al plan.**
  - Camino UTC propio: `a_epoch_utc(ts)`, que exige zona; OpenCode y Hermes usan epoch directo.
  - Una marca sin zona o que no se entiende **no se guarda**: va a un contador `cuarentena` por agente, visible en el
    panel.
  - Lo local solo existe en `consultar()`, con la zona inyectada.
- **Test.**
  - El mismo corpus con 3 zonas inyectadas, incluida una de media hora, da los mismos `hora_utc`.
  - Una marca sin zona cae en cuarentena.
  - Fixtures del 29-03-2026 y del 25-10-2026 (ya previstos).

### 12. La marcha atrás se ensayó en el papel y falló en la práctica
- **Qué pasó.**
  - Se subió `VERSION_ESTADO` en la fase 1, y volver a `main` obligó a reescanear entero.
  - `olvidar_cache()` borró el `state.json` de la doble escritura.
  - Una vuelta atrás de más de 30 días perdió lo que Claude ya había borrado: la base estaba, pero el código viejo no
    la leía. Al volver a la fase 1, el hueco era real.
  - Un esquema v2 (fase 2) abierto por el código de la fase 1 escribió filas con columnas viejas.
- **Señal temprana.** No hay un procedimiento escrito ni un test de ida, vuelta e ida.
- **Probabilidad / impacto.** Media / Alta.
- **Cambio al plan.**
  - `VERSION_ESTADO` **no cambia** en la fase 1.
  - `olvidar_cache()` no toca `turnos`.
  - Mientras dure la fase, la ingesta **también corre en `main`**: un parche mínimo que solo escribe turnos y no
    cambia lo que se muestra. Así, una vuelta atrás no deja huecos.
  - `meta.version_esquema` y `version_minima_escritor`: un código más viejo abre en solo lectura y avisa.
- **Criterio de salida.**
  - Ensayo de ida (fase 1), vuelta a `main` durante 48 h e ida otra vez.
  - Resultado igual a un control sin vuelta atrás: los mismos turnos y contadores sobre los días cerrados, y 0 huecos.
  - Un esquema futuro simulado no se modifica.

### 13. Dos bases «permanentes»: la del checkout y la de la instalación
- **Qué pasó.** `rutas.RAIZ` es el directorio del código, y el checkout de desarrollo tiene su propio `state.json`
  (existe hoy). La base se puso en `RAIZ`:
  - el checkout creó un segundo histórico, con otro parser, que divergió del instalado;
  - `.gitignore` no cubre `*.sqlite*`, y un `git add -A` subió la base con proyectos y uso.

  Al revés, si la ruta apuntaba siempre a `~/.cost-guard`, una rama de pruebas escribía en el histórico real
  (v1, causa 13).
- **Señal temprana.** `git status` muestra `consumo.sqlite`. Dos bases con totales distintos.
- **Probabilidad / impacto.** Media / Media.
- **Cambio al plan.**
  - `rutas.DATOS` es independiente de `RAIZ` y viene de `plataforma.py`. El checkout solo escribe si
    `COSTGUARD_DATOS` apunta a otra carpeta. Si no, abre en solo lectura.
  - `consumo.sqlite*` en `.gitignore` **antes** del primer commit de la fase.
  - `meta.dispositivo_id` y `productor` (ruta del código y versión) registrados en cada ingesta.

---

## Lo que la v2 ya cubre bien (no se repite)
- Hora UTC, datos crudos, `espacio_id` y `dispositivo_id`.
- Regla única de borrado.
- `VACUUM INTO` y `backup()`.
- `secure_delete`.
- Reloj inyectable y corpus sintético.
- Test de aislamiento.
- Aviso de huecos.

Las mediciones confirman que estas bases son viables: el 100 % de las marcas llevan zona o son epoch, y hay
`message.id` en el 100 % de las líneas de Claude.

## Cambios concretos al plan (accionables)

1. **Regla de escritura:** sustituir `INSERT OR REPLACE` por un upsert monótono (`max()` por contador), con un contador
   de conflictos cuando cambia el modelo o baja algún contador. Test en orden directo e inverso y con copia truncada
   (causa 1).
2. **Claves por agente** (escribirlas en el plan):
   - Claude: `message.id`, más `sessionId` si el id no tiene la forma de Anthropic. Nunca `requestId`.
   - Codex: `session_id` de la primera `session_meta` más `total_tokens` acumulado.
   - pi: `id` de sesión más `id` de línea.
   - OpenCode: `message.id` en todas las `opencode*.db`.
   - Hermes: `session_id` más `model`, `task` y el total acumulado.
   - Todas con prefijo de agente. Prohibido filtrar `isSidechain` (causas 1, 2, 7, 8).
3. **Hermes:**
   - fuente `session_model_usage`;
   - fecha `last_seen` / `last_activity_at`, nunca la de la lectura;
   - deltas negativos descartados;
   - sin el nombre del perfil en la clave;
   - el dueño decide si cuentan las tareas auxiliares (+17–47 %) (causa 2).
4. **`ingerir()` / `consultar()`:** separar las dos. Los 8 llamadores pasan a `consultar()`. Escritor único por
   arrendamiento en `meta` con `BEGIN IMMEDIATE`, sin `fcntl`. Ingesta en un hilo propio, separado del ciclo de
   servidores, con lotes de ≤ 200 ms (causa 3).
5. **Criterio de no bloqueo:**
   - con la base vacía y 4 consultores a la vez, ninguno tarda más de 1 s;
   - el ciclo de servidores entrega durante la construcción;
   - el `rumps.Timer` de `costbar.py` nunca ingiere (causa 3).
6. **Export y copias:**
   - `cryptography` fijada como dependencia;
   - formato versionado con scrypt, AEAD por trozos y cabecera en AAD;
   - cifrado a destinatario para las copias automáticas;
   - nunca un temporal en claro fuera de un directorio privado;
   - vector de prueba, test de ida y vuelta, de manipulación y de truncado;
   - restauración ensayada en Windows (causa 4).
7. **Sombra medible:**
   - misma lectura para las dos rutas y solo días cerrados;
   - tolerancia 0 en Codex, pi y OpenCode, y solo categorías firmadas en Claude y Hermes;
   - residuo sin explicar igual a 0 durante 7 días seguidos, con contador que vuelve a cero;
   - escalado al dueño tras 3 reinicios;
   - eventos obligatorios dentro de la ventana: reinicio, guardado de Ajustes, reposo y 3 agentes (causa 5).
8. **Portabilidad:**
   - `infobar/plataforma.py` con cerrojo, rutas, permisos y secretos;
   - datos en `%LOCALAPPDATA%` en Windows y aviso si están en una carpeta sincronizada;
   - `pathlib` en los lectores;
   - `id_fuente` como ruta relativa normalizada;
   - `tzdata`;
   - zona inyectada en lugar de `tzset`;
   - reintentos en `os.replace` y en `BUSY`;
   - tests con rutas `ntpath` (causa 6).
9. **OpenCode:** leer todas las bases, una fila por mensaje, caché por base y aviso de «fuente callada» (causa 7).
10. **Lectura incremental** por desplazamiento con inodo y hash de cabecera. Ni la generación nueva ni el truncado
    restan. Una línea partida no avanza. Criterio: ≤ 300 ms por vuelta con un fichero vivo de 250 MB (causa 9).
11. **Agregado horario materializado**, mantenido en la misma transacción. Base sintética de 3 años con estos topes:
    - 5 h ≤ 5 ms;
    - 31 días ≤ 50 ms;
    - mensual ≤ 100 ms;
    - ≤ 1 GB;
    - nada de `integrity_check` en la barra (causa 10).
12. **Camino UTC propio** (`a_epoch_utc`) que exige zona. Cuarentena visible para las marcas sin zona. Lo local solo en
    `consultar()`. Test con 3 zonas inyectadas, incluida una de media hora (causa 11).
13. **Marcha atrás:**
    - `VERSION_ESTADO` congelado;
    - `olvidar_cache()` sin tocar la base;
    - ingesta mínima también en `main` mientras dure la fase;
    - `version_minima_escritor`;
    - ensayo de ida, 48 h de vuelta e ida, comparado con un control (causa 12).
14. **Ruta de datos:**
    - `rutas.DATOS` independiente de `RAIZ`;
    - el checkout no escribe sin `COSTGUARD_DATOS`;
    - `consumo.sqlite*` en `.gitignore` antes del primer commit;
    - `productor` y `dispositivo_id` en `meta` (causa 13).
15. **Limpieza menor:**
    - no recorrer los `*.json` sueltos de `~/.claude/projects` (1.263 ficheros sin uso);
    - corregir el docstring de Codex;
    - el `CLIFF` por turno en Hermes va a la lista de divergencias aceptadas.

## Preguntas para el dueño
- Hermes: ¿cuentan las tareas auxiliares de `session_model_usage` (+17–47 % frente a `sessions`)?
- Copias automáticas: ¿aceptas el cifrado a destinatario, con la clave privada fuera del equipo, en lugar de una
  passphrase pedida en cada copia?
- ¿Se añade `cryptography` (y `tzdata` en Windows) como dependencia?
- ¿Se programa la sombra para que incluya el 25-10-2026?
