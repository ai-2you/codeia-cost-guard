# codeia-cost-guard

Mide (y ayuda a frenar) el gasto de tokens de tu flota de agentes: **Claude Code, Codex, OpenCode, pi**.
Lee sus logs locales, descarta los turnos duplicados, y te lo enseña en un panel y en un icono de la
barra de menús. Todo en **tokens**: la **ventana de 5 horas** (la que se agota en las suscripciones), la
**semana**, el día, los turnos y la caché. Los dólares van solo como equivalente API informativo.

Hecho por [Codeia](https://codeia.dev) — el sitio donde se aprenden estas tecnologías.

## Cómo se ve

El icono de la barra de menús lleva la cifra de la ventana de 5 h; al pulsarlo se abre el desplegable
(tema claro, oscuro o el del sistema):

![Icono en la barra de menús](docs/img/icono.png)

![Desplegable, claro y oscuro](docs/img/desplegable.png)

Y «Abrir panel» sirve el panel completo en el navegador, con filtros por periodo y por plan:

![Panel](docs/img/panel.png)

## Por qué

No te arruina el *effort* del modelo: te arruina **cuántas veces le mandas el contexto**. El 75 % del
gasto típico es lectura de caché — o sea, el mismo contexto reenviado turno tras turno. Y por encima de
**272.000 tokens** toda la petición pasa a facturarse a 2× entrada / 1,5× salida. Con esto delante, un
día de trabajo se ve de un vistazo. El detalle completo, en [GUIA.md](GUIA.md).

## Reglas de ahorro (Claude Code y Codex)

Además de medir, el repo trae las **cinco palancas** de `GUIA.md` ya escritas para pegar en las
instrucciones globales de tus agentes: contexto por debajo de 200K (nunca cruzar los 272K),
parches en vez de reescribir el fichero entero, fan-out máximo 3 y solo para tareas ambiguas
(dispersando entre modelos, no copias del mismo), no tocar el prompt de sistema a mitad de sesión
(rompe la caché), y Batch para lo asíncrono con Fast apagado. `python3 instalar_reglas_globales.py`
(o `./install.sh --reglas`) las pega solo en `~/.codex/AGENTS.md` y `~/.claude/CLAUDE.md` — nunca
en la memoria de un agente, que se inyecta en cada turno y encarecería el propio remedio. El bloque
exacto que escribe está en [AGENTS-snippet.md](AGENTS-snippet.md). Es idempotente: puedes
ejecutarlo cuantas veces quieras y respeta lo que ya tengas alrededor (con copia de seguridad).

Las tarifas de `costbar.py` (para el equivalente USD y el precipicio de los 272K) están puestas al
día con los modelos frontera actuales de ambos servicios (Claude Opus/Sonnet/Fable 5, GPT-5.6);
cuando salgan modelos nuevos, hay que añadir su fila en `TARIFAS` dentro de `costbar.py`.

## Instalación

```bash
git clone https://github.com/yosnap/codeia-cost-guard.git
cd codeia-cost-guard

# macOS: instala todo (app de barra + panel + reglas opcionales)
./install.sh

# Cualquier sistema: solo el medidor y el panel (necesita únicamente Python 3.9+)
python3 analizar_gasto.py      # informe en el terminal
python3 panel.py               # genera panel.html y lo abres en el navegador
```

En macOS, `install.sh` deja la app arrancando sola al iniciar sesión. Para pegar además las reglas de
ahorro en `~/.codex/AGENTS.md` y `~/.claude/CLAUDE.md`: `./install.sh --reglas`.

### Windows

`install.sh` no corre en Windows (es un script de shell que instala un icono de barra de menús y un
LaunchAgent, ambos exclusivos de macOS). Lo que sí funciona igual que en macOS y Linux es el motor de
medición, con Python 3.9+ desde `cmd.exe`, PowerShell o la terminal de Git Bash:

```powershell
git clone https://github.com/yosnap/codeia-cost-guard.git
cd codeia-cost-guard
python analizar_gasto.py
python panel.py
```

`panel.py` genera `panel.html` en la carpeta del proyecto; ábrelo haciendo doble clic o con
`start panel.html`. Si quieres el informe cada día sin lanzarlo a mano, prográmalo con el
**Programador de tareas** de Windows apuntando a `python.exe` con el script como argumento.

Nota: si usas OpenCode en Windows, esta herramienta busca su base de datos en la ruta de Linux/macOS
(`~/.local/share/opencode/`). En Windows, OpenCode guarda sus datos en otra carpeta y de momento no se
lee ahí — Claude Code y Codex sí se detectan igual en los tres sistemas.

## Compatibilidad por sistema

| Componente | macOS | Linux | Windows |
|---|---|---|---|
| `analizar_gasto.py` (informe en terminal) | ✅ | ✅ | ✅ |
| `panel.py` (panel HTML) | ✅ | ✅ | ✅ |
| `precio_astra.py` (calculadora de precios) | ✅ | ✅ | ✅ |
| Icono de barra de menús + desplegable (`install.sh`, `barra.py`) | ✅ | ❌ (no hay barra de menús equivalente) | ❌ |
| Arranque automático al iniciar sesión | ✅ (LaunchAgent) | — | — (usa el Programador de tareas si quieres algo similar) |
| Lectura de logs de Claude Code / Codex | ✅ | ✅ | ✅ |
| Lectura de consumo de OpenCode | ✅ | ✅ | ⚠️ ruta de datos distinta, no detectado todavía |
| **Modo sin interfaz** `python3 costbar.py --demonio` (ingesta, copias cifradas, ciclo de servidores, presupuestos y panel local, sin icono ni Chrome) | ✅ | ✅ | ✅ (código portable; **pendiente de probar en un Windows real**: ponlo en una tarea programada «al iniciar sesión») |
| Cerrojos, procesos hijos y exclusión de copias del sistema | ✅ Time Machine | ✅ | ✅ sin `fcntl`/`killpg`; los datos van a `%LOCALAPPDATA%\CostGuard` marcados como «no indexar» (el Historial de archivos y OneDrive no los cubren por defecto: la copia soportada es la cifrada de la aplicación) |
| Secretos (claves de proveedores y paneles) | ✅ Llavero | ❌ | ⚠️ Administrador de credenciales (probado con simulación, sin Windows real) |

Las IPs de intentos de acceso y de sesiones de la caché de servidores se borran a las 48 h y las imágenes del desplegable cada hora.
Verificado ejecutando el medidor con `$HOME` vacío (instalación limpia, sin historial previo) además
de con historial real.

## Contribuir

Ver [CONTRIBUTING.md](CONTRIBUTING.md): cómo preparar el entorno, qué tipo de cambios encajan en el
repo y el estilo de commits y de PRs que se usa aquí.

## Qué trae

- **`costbar.py`** — la app de barra de menús (macOS). Muestra el gasto de hoy y, al clicarla, el ritmo
  de la última hora, ayer, 7 días y el desglose por modelo y por proyecto. Avisa por notificación si el
  ritmo pasa de un límite (`config.json`).
- **`panel.py`** — el panel visual: `panel.html` con barras por día, modelo y proyecto. Se refresca
  solo cada minuto.
- **`analizar_gasto.py`** — el informe de texto, para terminal o para cron.
- **`precio_astra.py`** — calculadora de precios con *preflight*: estima el coste de una petición antes
  de enviarla (incluido el precipicio de los 272K, caché, Batch y Fast).
- **`instalar_reglas_globales.py`** — escribe las reglas de ahorro en los ficheros de instrucciones
  globales de los agentes CLI. Idempotente y con copia de seguridad.
- **`GUIA.md`** — la guía: las cinco palancas y por qué el contexto manda.

## Qué te muestra

- **Ventana de 5 horas** y **semana** en tokens, separando Claude de OpenAI (son suscripciones distintas).
- **Hoy**: entrada, salida, caché leída (con su % sobre la entrada) y caché escrita.
- **Turnos**: cada ida y vuelta con el modelo reenvía el contexto, y ahí está el gasto.
- **Ritmo de la última hora**, con aviso si pasa del límite.
- Por día, por modelo y por proyecto.

## Panel

Modo claro y oscuro con un boton (recuerda tu eleccion), filtros por periodo (5 h / hoy / mes) y por
plan, tarjetas de 5 h, hoy, semana y mes, barras por dia y por proyecto, y una matriz
**modelo x dia** para ver que modelo consume cada dia. Todo el calculo va en Python y los datos se
embuten en el HTML: sin red, sin dependencias, sin librerias de graficos.

El desplegable del icono de la barra de menus lleva su propio selector (Claro / Oscuro / Sistema)
que guarda la eleccion en la clave `"tema"` de `config.json`; `"sistema"` (por defecto) sigue la
apariencia de macOS. El boton «Abrir panel» sirve el panel en `http://127.0.0.1:47391`.

## Configurar tus planes (proveedores)

Los logs dicen qué modelo se usó, nunca qué cuota tienes: eso solo lo sabe tu proveedor. Declara cada
plan en `config.json` (mira `config.example.json`) y el panel y el icono medirán **por plan**, con el
**% de la ventana de 5 h y de la semana** que llevas consumido:

```bash
python3 costbar.py --proveedores    # lista tus modelos por proveedor y te da la config lista para pegar
```

```json
{"proveedores": [
  {"nombre": "Claude (Max)", "modelos": ["claude", "sonnet", "opus", "haiku", "fable"],
   "limite_5h_tokens": 0, "limite_semana_tokens": 0},
  {"nombre": "z.ai (GLM)", "modelos": ["glm"], "limite_5h_tokens": 0, "limite_semana_tokens": 0}
]}
```

Con los límites a 0 se muestran tokens en crudo; al ponerlos aparece el porcentaje.

### Ajustes visuales

Desde el icono de la barra abre el panel y pulsa **Ajustes**: ahí editas sin tocar `config.json` los
proveedores (modelos que cuentan, cuotas de 5 h, semana y mes), a qué plan va cada origen del log, los
proyectos (renombrar, ocultar de las listas, presupuesto mensual), los servidores VPS que monitoriza por SSH
(`docs/guia-servidores.md`) y la alerta de ritmo. Las **claves de API** se
guardan en el Keychain de macOS, nunca en `config.json` ni en git; de momento solo NaN tiene lector de
consumo por API, para el resto la clave se guarda pero el consumo sigue saliendo de los logs. Solo
funciona abriendo el panel desde la barra, no como fichero suelto ni con la URL escrita a mano: el panel
pide una **sesión** (la barra abre un enlace de un solo uso que el servidor local cambia por una cookie), así
que otro proceso o usuario del Mac no puede leer tus proyectos ni tus servidores por `http://127.0.0.1` sin esa
sesión. Límite conocido: un programa que ya corra con **tu mismo usuario** y vea el navegador, o el servidor local
de otra app en `127.0.0.1` (las cookies no se separan por puerto), queda fuera de esta defensa. Todo lo que
guarda la app queda legible solo para tu usuario (`~/.cost-guard` en 0700, ficheros en 0600).

### Cuotas de NaN, en vivo

NaN publica tu consumo real en `GET https://api.nan.builders/v1/usage`. Con `"uso_api": "nan"` en el
proveedor NaN (junto con `cuotas_mes`; cópialos de `config.example.json`, tu `config.json` no los trae por
defecto), el panel y el icono de la barra leen de ahí el consumo
**mensual por modelo** —incluido lo que corrió en la nube o en otra máquina, que no deja log local— y
le aplican las cuotas publicadas (DeepSeek V4-Flash 3 B, GLM 5.3 Flash 2 B, MiMo V2.6 Flash 1 B, Qwen
3.8 Flash 500 M; se resetean el día 1). La API key se coge de la variable de entorno `NAN_API_KEY` (lo
recomendado; **no** la pongas en `config.json`, que este repo sí versiona). Si no hay clave o no hay
red, cae sin ruido a los logs locales.

```bash
export NAN_API_KEY=sk-tu-clave    # en tu shell (o ~/.zshrc); config.example.json lo documenta
```

## Cómo cuenta

Dos formatos distintos, y confundirlos infla los totales (lo aprendimos midiendo):

- **Claude Code**: Claude Code escribe **varias líneas por mensaje** (una por bloque: pensamiento, texto,
  herramienta) con la misma entrada y caché y la salida creciendo; el mensaje real es la última línea
  (`message.id`). Se cuenta **una vez por mensaje**. Hasta la versión de 2026-10-14 se contaban todas las líneas
  y el consumo de Claude Code salía inflado en torno a un 40 %; si ves que las cifras bajan al actualizar,
  es esa corrección. La entrada y la caché ya vienen separadas.
- **Codex** (rollouts): cada turno se escribe **dos veces** (`token_usage_record` y
  `event_msg/token_count`, en el mismo segundo) y el fichero lleva además `total_token_usage`, que es
  **acumulado**. Hay que usar el uso del turno ignorando el acumulado, y tener en cuenta que
  `cached_input_tokens` va **dentro** de `input_tokens`.

Lee los logs de sesión de `~/.claude`, `~/.codex` y `~/.opencode`, se queda con los turnos **únicos**
(los forks de Claude Code copian los turnos de sus padres y contarían doble) y aplica los precios de
lista de cada modelo.

**Los dólares son equivalente API**, no tu factura: si vas con suscripción no pagas por token, pero lo
que se agota es lo mismo que aquí se mide — *tokens × turnos*. Los agentes que corren en la nube no
dejan log en tu máquina: ejecuta el medidor **en esas máquinas** y suma los totales.

## Base de datos de consumo (en transición)

Además de leer los logs, la barra guarda cada turno de Claude Code, Codex, pi, OpenCode y Hermes en una base SQLite
(`consumo.sqlite`) para conservar el histórico aunque los agentes borren sus logs (Claude Code los borra a los 30 días).

- **Dónde:** en el directorio de datos de tu sistema, aparte del código y de la configuración:
  macOS `~/Library/Application Support/CostGuard`, Windows `%LOCALAPPDATA%\CostGuard`, Linux `~/.local/share/costguard`
  (`COSTGUARD_DATOS` lo sustituye). No puede estar en una carpeta sincronizada (iCloud, Dropbox, OneDrive) ni de red.
  La base solo la lee tu usuario (0600); si está en un volumen sin propiedad de ficheros, la barra lo avisa en el log.
- **Quién escribe:** solo la barra (en su propio hilo) o `python3 costbar.py --ingerir [--una]` si no usas la barra.
  El panel, el servidor local y el temporizador de la barra solo leen.
- **Interruptor `almacen` en `config.json`:** `sombra` (por defecto: las cifras siguen saliendo de los logs, la base se llena y se
  compara cada hora en `sombra.jsonl`), `activo` (las cifras salen de la base; si no se puede leer, vuelven a los logs),
  `apagado` (lee los logs pero sigue guardando) y `desactivado` (ni guarda). Volver atrás nunca borra la base.
- **Nombres de proyecto:** un directorio es un solo proyecto en todos los agentes; los alias que ya tenías siguen valiendo.
- **Codex con otro proveedor:** se muestra en la cuenta de ese proveedor, no en tu suscripción de Codex.

### Copias cifradas y exportación

Requiere la librería `cryptography` (la instala `install.sh`; sin ella solo se desactiva esto). Todo se hace desde la terminal:

- **Copias automáticas (recomendado):** `python3 costbar.py --claves-generar RUTA_DE_LA_CLAVE --carpeta CARPETA_DE_COPIAS` genera una
  clave **privada** que guardas **fuera de este equipo** y una pública que se queda en `config.json` (`copias.destinatario`).
  Elige una `--carpeta` en **otro disco** o en una carpeta de nube (va cifrada): por defecto es una carpeta del mismo disco que la
  base, y un fallo del disco se llevaría base y copias. Sin la privada las copias son irrecuperables y no existe recuperación:
  el comando te lo advierte, la vuelve a leer del fichero que guardaste y ensaya un descifrado antes de activar nada. El fichero de
  la clave privada lleva también la **firma pública** de este equipo, que hace falta para restaurar en otro. La barra hace una copia
  al día, la **relee y verifica** antes de rotar y conserva las últimas 14 diarias y la primera de cada uno de los últimos 6 meses
  **de este equipo** (varios equipos pueden compartir carpeta: cada uno rota solo las suyas). Si la base falla su comprobación se
  guarda una copia `.danada` (como mucho una al día, las 3 últimas). `--copia-ahora` fuerza una.
- **Exportar a mano:** `--exportar RUTA.cgx` (te pide una frase de paso, mínimo 12 caracteres; `--frase-sugerir` propone una).
- **Restaurar:** `--importar RUTA.cgx [--clave-privada RUTA] [--firma-publica cgxs1…] [--forzar]` descifra, comprueba, te muestra
  fecha y número de turnos y pide confirmar. Guarda antes una copia de la base actual (si no se puede abrir, la aparta en
  `copias/`). La barra se aparta sola mientras restauras (no hace falta cerrarla); espera hasta un minuto y medio a que suelte el
  puesto de escritor. En un equipo nuevo basta con el fichero de la clave privada y la carpeta de copias.
  `--probar-restauracion RUTA.cgx` ensaya sin tocar nada: hazlo cada pocos meses **desde otro equipo o con otra carpeta de datos**,
  que es lo que de verdad comprueba que podrás restaurar.
- **Windows:** `python costbar.py --autoprueba-copias` hace la ida y vuelta completa en un directorio temporal y dice si todo
  funciona en tu máquina; pega su salida si algo falla.
- Las frases y las claves privadas **nunca** van por la línea de órdenes, el panel web ni la barra: solo se piden en la terminal.
- El estado de las copias (`al_dia`, `atrasada`, `danada`, `nunca`, `reloj`, último error y si toca ensayar) se ve en **Ajustes →
  Copias cifradas**, con un aviso si están atrasadas, salieron de una base dañada, la carpeta está en este mismo equipo o hace
  tiempo que no pruebas a restaurar. Todavía no se pinta en el icono de la barra.

### Histórico, presupuestos y CSV

Necesitan `almacen: activo` en `config.json` (con `sombra` el panel sale de los logs y las cifras no coincidirían; la aplicación lo
avisa en vez de enseñar otras).

- **Pestaña Histórico:** consumo por día, semana (de lunes a domingo) o mes en tu hora local, agrupado por proyecto, plan o agente, con
  la comparación con el periodo anterior y una proyección lineal de fin de mes (estimación, desde el día 3). Usa el mismo criterio de
  proyecto mostrado, plan y modelos ignorados que el panel. Los proyectos ocultos cuentan en el total y salen como «(ocultos)». La
  recarga automática del panel se suspende mientras lo miras.
- **Presupuestos (Ajustes → Presupuestos):** un tope de tokens totales (entrada, salida y caché, igual que tus cuotas) por proyecto o por
  plan, por mes o por semana natural. El nombre es el que ves en el panel (si renombras el alias, la fila avisa de que ya no hay consumo
  con ese nombre). Avisa al 80 % y al 100 %, **una vez por umbral y periodo**, con una notificación del sistema en macOS (sin el
  nombre del proyecto en el texto, porque se vería en la pantalla de bloqueo; se puede desactivar). En Windows solo hay aviso visual por
  ahora. Lo ya avisado vive en `avisos.json`, en el directorio de datos. Los presupuestos mensuales por proyecto que tuvieras pasan solos a
  la lista nueva (el campo antiguo se conserva en `config.json` pero ya no se edita).
- **CSV:** en la pestaña Histórico, «Descargar CSV» (sin cifrar, se genera al momento y no se guarda en el servidor), o desde la terminal
  `python3 costbar.py --exportar-csv [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD] [--paso dia|semana|mes] [--agrupa ninguno|proyecto|plan|agente] [--cifrar]`.
  Sin `--cifrar` queda en `exportaciones/` (carpeta 0700, fichero 0600, nombre generado) y te avisa de que está en claro; con `--cifrar`
  pide una frase y deja un `.csv.cgx` que se abre con `--descifrar-csv RUTA.csv.cgx`. Separador `;` y UTF-8 con BOM (Excel en español lo
  abre directo); todo entrecomillado; los nombres de proyecto salen sin caracteres de control ni invisibles y con una comilla delante si
  una hoja de cálculo los tomaría por fórmula. Solo contiene periodo, grupo, tokens y turnos: nunca hosts, IPs ni rutas completas.
  Cada exportación deja una línea en `exportaciones.log` (cuándo, rango y número de filas; nunca contenido).

### Historial de servidores

La barra guarda en `servidores.sqlite` (misma carpeta de datos, permisos 0600, **no entra en las copias cifradas**) cómo ha ido cada servidor:
una muestra por lectura buena (carga, memoria, disco, intentos de acceso, puertos, servicios, webs y protección; 14 días) y un resumen por hora
que no caduca. Los intentos de acceso de un servidor con el script antiguo son **picos de la ventana de 24 h que mide el servidor**, no sumas.
Con el script nuevo (`recoger.sh` v2, se instala con `alta-servidor.sh`; el Mac sigue leyendo el antiguo) además se guardan los intentos
**exactos de cada hora natural**, que nunca bajan aunque el servidor reinicie su registro, y solo cuentan las líneas que escribió sshd de
verdad (filtro por la unidad de systemd, no por el nombre que pone el proceso). Las IPs son «según ese servidor» y **solo se guardan si el
script v2 comprueba que las sesiones de usuario no viven en el cgroup de sshd**: con el script antiguo se muestran pero no se guardan.
Si un servidor no tiene registro de sshd en el journal, la tarjeta dice «sin dato», no 0.

- **IPs de quien intenta entrar** (dato personal): solo las públicas; nunca las de `ips_conocidas`. Se olvidan a los **90 días** desde la última
  vez que se vieron (`retencion_ips_dias`, de 7 a 90, global o por servidor), al quitar el servidor de la lista y al poner `guardar_ips: false`
  (global o por servidor; recomendado en servidores de clientes). La caducidad no se almacena: se calcula con la retención vigente, de modo que
  bajarla oculta al instante lo que ya sobraba.
- `python3 costbar.py --olvidar-ips [--servidor NOMBRE]` las borra ya, compacta la base y deja una línea en `servidores_ips.log` con el número
  de IPs borradas (nunca las IPs). Con `--servidor` sin nombre no hace nada.
- **Contenedores y servicios** (script v2 de `recoger-root`): inventario de lo que corre en el servidor, con estado, salud y reinicios, sin tocar
  variables de entorno ni montajes (plantilla fija de `docker inspect`). Un servicio de Swarm solo se reconoce si aparece en `docker service ls`.
  Se guarda cuándo **apareció**, **desapareció**, **cambió de estado** o **se reinicia en bucle**: nunca por un solo fallo de lectura, un contenedor
  efímero o una lectura recortada (se exigen dos lecturas completas separadas en el tiempo). Los **nombres son datos personales** (pueden ser de
  clientes): solo viven en la etiqueta del recurso, los inactivos se olvidan a los 30 días, los eventos a los 180, y se borran al quitar el servidor.
  `python3 costbar.py --olvidar-recursos [--servidor NOMBRE]` los borra ya. Si el script de root tuvo que saltarse algo por falta de tiempo, esa
  lectura no cuenta («sin dato»).
- **Certificados de las webs**: se guarda cuántos días faltan para que caduque el certificado que sirve cada web https (el de la hoja, ya verificado: uno caducado
  o no válido no se conecta y aparece como error) y, por muestra, el mínimo de todas las webs; si a alguna no se le pudo leer, no se guarda mínimo para no esconderla.
  La tarjeta de webs pasa a ámbar con menos de 14 días y a rojo con menos de 3.
- **Alta guiada desde Ajustes → Servidores → «Dar de alta un servidor»**, sin contraseñas en la app. «Comprobar servidor» hace un diagnóstico de solo lectura (una conexión con tu clave SSH
  de siempre): ¿entra?, ¿root o `sudo` sin contraseña?, ¿Debian/Ubuntu con systemd?, ¿`AllowUsers`?, ¿ya instalado? Si es la primera conexión te enseña la **huella** del servidor y
  hasta que no confirmas que es la suya no se guarda en `known_hosts` (nunca hay un botón para sustituir una huella que cambió). Con «listo», «Instalar con mi clave» manda los tres scripts
  por stdin en una sola conexión, prueba la lectura como `infobar` y solo entonces guarda el servidor. Si el servidor pide contraseña de sudo, te da la línea de `alta-servidor.sh` para pegar
  en el Terminal (ahí tecleas tu contraseña) y un botón «Comprobar conexión». Un alta a la vez, 60 s de enfriamiento tras un fallo, y nada crudo del servidor llega a la página.
  `infobar/remoto/desinstalar-del-servidor.sh` (como root en el servidor) lo deshace.
- **Detección de webs.** Con el script de root v2, el servidor dice qué dominios sirve su proxy (Traefik de Dokploy, EasyPanel y Coolify: etiquetas de los servicios de Swarm y de los
  contenedores y los ficheros dinámicos del proxy); solo salen los nombres dentro de `Host(...)`, nunca el resto de las etiquetas ni de los ficheros (pueden llevar hashes de
  autenticación). En Ajustes, «Buscar webs de este servidor» propone primero los dominios que **resuelven a la IP del servidor**; los que apuntan a otro sitio (CDN, otro servidor)
  van plegados y con aviso, y los que resuelven a una IP local o privada no se ofrecen. Tú marcas las que quieres vigilar: nada se sondea sin marcarlo (hasta 100 por servidor). Los
  `xn--` se ven decodificados junto al original. `detectar_webs: false` en un servidor (por ejemplo, de un cliente) la desactiva; las candidatas viven solo 48 h en la caché.
- **Webs de cada servidor** (`webs` en la configuración): se sondean en un hilo aparte, no dentro de la lectura SSH. Cada 5 minutos, y cada 60 s mientras una web falla
  (como mucho 20 a la vez). Un 4xx cuenta como «responde»; un 5xx, un fallo de conexión o un timeout, como fallo. Si el Mac no tiene red (fallan todas y también los
  tres canarios públicos) esa pasada se descarta entera y no anota ninguna caída. Una web caída pone el servidor en rojo pero no acelera la lectura SSH.
- **Seguimiento de cada web** (`servidores.sqlite`, tablas `webs`, `eventos_web`, `horas_web`, `dias_web`): cada sondeo se clasifica en *ok* (200–399, y 401/403: la aplicación
  está viva), *mal* (otros 4xx o más de 5 s) o *caída* (5xx, sin respuesta, TLS inválido). El estado confirmado cambia con dos sondeos seguidos (≥ 45 s entre ellos) para pasar a
  caída o a «mal», y con uno para volver a «ok»; la primera vez que se ve una web solo fija la línea base. Eventos `cayo`, `volvio` y `degradada` (hasta 20 por web y día).
  La disponibilidad observada se cuenta por hora (90 días) y por día (permanente) con los sondeos del Mac: los huecos con el Mac apagado son «sin dato», no caída.
  Dejar de vigilar una web, quitar el servidor o `--olvidar-recursos` borran su dominio, sus eventos y sus resúmenes al instante.
- **Avisos de servidores** (notificación del sistema en macOS; en Windows quedan solo en pantalla): web que no responde correctamente desde hace más de 5 minutos, servidor
  sin conexión (ventana de 15 minutos o 3 intervalos), acceso SSH rechazado (clave retirada o huella cambiada), contenedor en bucle de reinicios, disco al 90 % o más durante
  10 minutos y certificado con 7 días o menos. Cada situación avisa **una vez**, se rearma cuando lleva un rato resuelta y tiene un enfriamiento mínimo; con varias a la
  vez sale una sola notificación. El texto nunca lleva nombres, dominios ni IPs. Lo que ya ocurre al arrancar no avisa; un Mac que acaba de despertar o sin red no
  inventa avisos. Se apagan todos con `avisos_servidores: false` y los de un servidor con `avisos: false` (casilla en Ajustes): lo que pase mientras están apagados no
  cae al reactivarlos. Estado en `avisos_servidores.json`. Límites conocidos: no hay previsión de disco ni aviso de «copia sin hacer» (necesita la API del panel), el disco es
  el que mide el script, y un servidor comprometido puede mentir a la baja.
- Cada servidor tiene un `id` propio en `servidores.json`: renombrarlo no parte su historial; quitarlo y volver a añadirlo con el mismo nombre
  empieza uno nuevo.
- Lectura: `GET /servidores/historia` (lista, o serie por día/hora con `servidor`, `desde`, `hasta`, `paso`) y `GET /servidores/ips?servidor=…&con_ips=1`
  (cada consulta deja su recuento en `servidores_ips.log`). La pantalla llega con la pestaña Servidores.
- Si la base falla, el ciclo de servidores sigue como siempre y se reintenta una hora después. Un downgrade de versión congela la purga.

## Tests

```bash
python3 -m unittest discover tests   # sin dependencias: unittest de la libreria estandar
```

Cubren `a_local()` (UTC con Z a hora local, verano/invierno, offsets, cadenas rotas), la
conversion de horas/dias a local en `parsear()`, el doble registro de Codex (mismo segundo,
un turno) y el lector de pi (reasoning, dedup, claves locales). Se fija `TZ=Europe/Madrid`
para que los resultados sean deterministas en cualquier maquina.

## Licencia

MIT.
