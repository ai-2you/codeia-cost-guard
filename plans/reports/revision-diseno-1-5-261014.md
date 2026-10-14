# Revisión del diseño 1.5 — export cifrado (pre-mortem + red team), 2026-10-14

Objeto: sección «1.5 — diseño del export cifrado» de `plans/261013-2350-sqlite-historico/plan.md` (líneas 160-170).
Contexto leído: `infobar/almacen.py` (`copia()` 552-577, `mantenimiento()` 580-583, escritor por latido 246-282),
`infobar/ingestor.py` (hilo de ingesta, `_abre` 49-68, `_mantenimiento` 166-176), `infobar/plataforma.py`,
`infobar/permisos.py`, `costbar.py` (CLI por `sys.argv`), `infobar/servidor_local.py` + `ajustes.normalizar`
(el panel no puede escribir claves de config fuera de su lista), `requirements.txt`, `install.sh` y los dos
informes previos (red team 2.1/2.2/2.4 y pre-mortem 7/8/15: cifrar copias y exports con frase, no Time Machine).

Lo ya cerrado y que no se repite: necesidad del cifrado, descarte del Llavero/SQLCipher, copias con la API de backup,
exclusión de Time Machine, retención de IPs en copias.

**Mediciones propias (scratchpad):**
- SQLite 3.53 (Python 3.14 del sistema): base A en WAL con una fila solo en el `-wal`; `os.replace` de otra base sobre
  `consumo.sqlite` sin tocar `-wal`/`-shm`; al abrir, la base «restaurada» muestra **las filas de la base antigua**
  (`['vieja-base-checkpointed', 'vieja-base-SOLO-EN-WAL']`) y `quick_check` responde `ok`. Sin el `-wal`, muestra lo
  restaurado.
- `cryptography` 46.0.7 (`/usr/bin/python3`, 3.9.6; no está en el Python 3.14 del proyecto): `ChaCha20Poly1305`
  exige nonce de exactamente 12 bytes; texto vacío → 16 bytes de etiqueta; etiqueta truncada o AAD distinto →
  `InvalidTag`. `Scrypt` N=2^15/r=8: 0,19 s y 59 MiB; **N=2^17: 0,67 s y 187 MiB; N=2^20: 5,4 s y 1,2 GiB**; rechaza N
  no potencia de 2, r=0, p=0; **acepta N=2**. `X25519PrivateKey.exchange` con punto todo ceros o de orden bajo →
  `ValueError`. `BestAvailableEncryption` para PKCS8 = PBES2 + **PBKDF2 2.048 iteraciones** + AES-256-CBC.
  `hashlib.scrypt` **no existe** en ese Python (no sirve de reserva sin `cryptography`).

---

## BLOQUEANTE

### B1. La restauración deja los `-wal`/`-shm` de la base anterior y la base antigua «resucita» sobre la restaurada
- **Evidencia.** Diseño 1.5.4: «descifra a un temporal junto al destino … y solo entonces `os.replace` atómico». No
  menciona el `-wal` ni el `-shm`. La barra trabaja siempre en WAL (`almacen.py:138-141`) con `synchronous=NORMAL`, así
  que casi siempre hay un `-wal` con páginas sin volcar. Medido arriba: el WAL huérfano se aplica sobre el fichero
  nuevo y `quick_check` dice `ok`.
- **Escenario.** El usuario cierra la barra (o no: ver A3), ejecuta `--importar`, la restauración pasa todas las
  comprobaciones, reemplaza el fichero; al abrir, SQLite replaya el WAL antiguo encima: páginas de otra base, con
  otro `freelist` y otro `user_version`. Resultado: base mezclada, en el mejor caso `database disk image is malformed`
  al rato; en el peor, cifras cruzadas sin error.
- **Arreglo.** Antes del `os.replace`: (1) abrir la base actual en escritura, `PRAGMA wal_checkpoint(TRUNCATE)`,
  cerrar; (2) borrar `consumo.sqlite-wal` y `-shm` si siguen ahí; si no se pueden borrar (Windows: en uso) →
  abortar sin tocar nada; (3) `os.replace`; (4) comprobar tras el reemplazo que no existe ningún `-wal`/`-shm` y
  abrir con `almacen.abrir()` para que fije WAL y migre. El temporal descifrado se escribe con `journal_mode=DELETE`
  (sin WAL) y se cierra antes del reemplazo. La copia de seguridad previa con `--forzar` se hace con `con.backup()`
  (nunca copiando el fichero: pre-mortem 7). Test obligatorio: reproducir el experimento de arriba y exigir que
  la restaurada no contenga la fila que solo vivía en el WAL antiguo.

### B2. El modo destinatario no autentica el origen: quien escriba en la carpeta de copias fabrica una copia «válida»
- **Evidencia.** Diseño 1.5.2: X25519 efímero + pública del destinatario → HKDF → clave; la pública «no es secreta y
  va en `config.json`». Objetivo 1.5: «cualquier manipulación se detecte». 1.5.6: la carpeta de copias «sí puede estar
  en una carpeta sincronizada o de red». Un AEAD con clave derivada de una pública solo autentica *que quien cifró
  conocía la pública*: cualquiera.
- **Escenario.** Un tercero con acceso a la carpeta sincronizada (cuenta de nube comprometida, compañero con acceso al
  recurso de red, el propio proveedor) sustituye `copia-2026-10-20.cgx` por un fichero cifrado por él a la misma
  pública con una base SQLite a su gusto: cifras falsas, nombres de proyecto que le interesen, una base
  construida para explotar el analizador (SQLite se fuzzea mucho, pero `quick_check` + restaurar un fichero hostil es
  superficie que hoy no existe). El usuario restaura tras un fallo de disco: todo pasa, firma y bandera incluidas.
- **Arreglo.** Autenticación del productor con Ed25519: clave de firma del dispositivo generada al primer uso en
  `DATOS` (0600, `clave_firma.cgx`), su pública se imprime junto a la clave privada del destinatario (el «paquete de
  restauración» que el usuario guarda fuera) y se incluye en el fichero de clave privada. El fichero `.cgx` termina
  con un tráiler: `Ed25519(cabecera || SHA-256 acumulado de todos los trozos cifrados)`. El lector descifra al
  temporal, acumula el hash y verifica la firma **antes** del `os.replace`; sin firma válida contra una pública
  conocida, se borra el temporal. Un mismo paquete puede llevar varias públicas de firma (varios dispositivos,
  compatible con «fundir dispositivos»). Modo frase: la frase ya autentica al productor; no hace falta firma.
  Si el dueño decide no firmar, hay que rebajar el objetivo escrito («se detecta la manipulación de una copia
  legítima, no la sustitución por otra») y avisar en `--importar` cuando la copia viene de una carpeta no fiable
  (`plataforma.ruta_no_fiable`).
- Nota: un proceso del mismo usuario en la máquina ya lee la base en claro; ese atacante está fuera de alcance
  (red team 2.1) y no es el que cubre B2. B2 cubre al que solo ve la carpeta de copias, que es justamente el que el
  cifrado a destinatario pretende neutralizar.

### B3. La máquina no puede comprobar que sus propias copias son recuperables y la rotación borra las buenas
- **Evidencia.** Diseño 1.5.6: «rotación de las 14 últimas». 1.5.2: «la clave privada … nunca queda en la máquina».
  Luego ningún proceso local puede descifrar una copia. El diseño no dice nada de verificar la pública al configurarla
  ni de verificar el fichero escrito.
- **Escenarios.** (a) El usuario pega la pública con una errata o la de otro par (la generó dos veces y guardó la
  privada equivocada): 14 días después las 14 copias son indescifrables y la última buena ya no existe. (b) El disco
  de red devuelve escrituras cortas o la cuota de la nube corta el fichero: copia truncada, el lector la rechazará, y
  la rotación la cuenta como una más. (c) La privada se pierde (ordenador robado con la única copia impresa en el
  cajón): el usuario no se entera hasta que la necesita.
- **Arreglo.** (1) **Ensayo obligatorio al configurar**: `--claves-generar` escribe la privada donde diga el usuario
  y, en el mismo paso, cifra un fichero de prueba a la pública y exige descifrarlo con la privada recién guardada
  **leída desde ese fichero**; solo entonces se escribe `copias.destinatario` en `config.json` (con su huella). Sin
  ensayo no hay copias automáticas. Mensaje en pantalla, sin ambigüedad: «Sin esta clave las copias son
  irrecuperables. No existe recuperación. Guárdala en dos sitios fuera de este equipo» y exige escribir `SI`. (2)
  **Verificación al escribir**: la clave simétrica del fichero sigue en memoria al terminar; se relee el fichero
  recién escrito del destino (no del búfer), se comprueban todas las etiquetas, el tráiler y el SHA-256 del texto en
  claro; si no cuadra, se borra y se anota el fallo. (3) **Rotación solo tras verificación**: nunca se borra una copia
  antigua si la nueva no pasó (2). (4) **Retención escalonada**: 14 diarias + la primera de cada mes de los últimos 6
  (un error silencioso de datos que lleve 15 días no deja sin copia anterior). (5) `--probar-restauracion RUTA.cgx
  --clave-privada RUTA`: descifra a un directorio temporal y cuenta turnos; la barra recuerda hacerlo cada 90 días
  (`meta.ultimo_ensayo`).

## ALTO

### A1. El tope de scrypt al leer (N ≤ 2^20) permite a una cabecera hostil consumir 1,2 GiB y 5 s por intento
- **Evidencia.** Diseño 1.5.2: «N entre 2^15 y 2^20, memoria acotada». Medido: N=2^20, r=8 → 1.211 MiB de RSS y
  5,4 s. `cryptography` no acota memoria por sí sola y acepta N=2. El coste de scrypt es 128·N·r·p bytes.
- **Escenario.** Un `.cgx` recibido o sustituido con N=2^20 y r=32 (el diseño no acota r) pide 4 GiB en el
  descifrado: `MemoryError` o el sistema se arrastra; en un portátil con 8 GiB cierra la barra. No roba nada, pero
  convierte el «abrir un fichero» en un disparo.
- **Arreglo.** Escritura fija (N=2^17, r=8, p=1; 187 MiB, 0,7 s, adecuado para una frase humana). Lectura: calcular
  `128·N·r·p` antes de derivar y rechazar si pasa de **256 MiB** (N ≤ 2^18 con r=8), p ≤ 2, r en [1, 16], N ≥ 2^14
  (rechazar por abajo también: una cabecera con N=2 abarata la fuerza bruta al que convenza al usuario de recifrar).
  Test con los cuatro bordes.

### A2. El diseño omite el formato de las claves, su protección, su copia y su rotación
- **Evidencia.** 1.5.2 solo dice «se muestra o guarda una sola vez donde elija el usuario (0600)». No hay formato de
  huella, ni codificación, ni frase opcional, ni rotación. Si se tira de lo que ofrece `cryptography`
  (`BestAvailableEncryption`), la privada queda bajo PBKDF2 de 2.048 iteraciones (medido): fuerza bruta trivial.
- **Escenarios.** El usuario guarda la privada en Notas de iCloud (texto largo, sin formato reconocible) y en un
  año no sabe cuál de tres cadenas es; o la imprime y una errata al teclearla da «clave no válida» sin pista; o la
  protege con frase usando el cifrado por defecto y es papel mojado; o cambia de clave y las copias antiguas, aún
  dentro de los 14 días, ya no abren con la nueva.
- **Arreglo.** (1) Codificación textual de una línea con prefijo y suma de control, estilo age/Bech32:
  `CGX-SECRET-KEY-1…` (privada, 32 bytes) y `cgx1…` (pública); detecta erratas, se imprime en papel y es
  inconfundible. (2) **Huella** = primeros 16 bytes de SHA-256(pública cruda), hex en grupos de 4; la cabecera
  lleva la **pública completa** (32 bytes, no es secreta) y la huella es solo presentación; el lector elige la
  privada por huella si hay varias. (3) Protección opcional: el fichero de clave privada **es un `.cgx` modo 1 de un
  solo trozo** (mismo código, mismo scrypt); nunca `BestAvailableEncryption`. (4) Rotación: `copias.destinatario`
  admite lista; las copias ya hechas no se recifran; la privada antigua se conserva hasta que la retención expulse la
  última copia que la usa, y `--probar-restauracion` lo dice por huella. (5) `--claves-generar` imprime pública,
  huella y la pública de firma del dispositivo (B2) en un bloque pensado para copiar o imprimir.

### A3. Carrera entre la restauración y la barra (launchd la relanza, el CLI de ingesta espera turno)
- **Evidencia.** 1.5.4: «se niega si hay un escritor vigente». El latido caduca a los 90 s (`almacen.py:24`); la barra
  reabre por inodo (`ingestor.py:49-60`), y `costbar.py --ingerir` reintenta cada 30 s esperando turno (`costbar.py:46-49`).
  La comprobación «no hay escritor» y el `os.replace` no son atómicos entre sí.
- **Escenario.** El usuario cierra la barra, la comprobación pasa, descifrar y `quick_check` tardan 40 s, launchd (o
  el propio usuario) relanza la barra, esta toma el arrendamiento y escribe en el inodo antiguo; el reemplazo llega, la
  barra detecta el cambio de inodo en la siguiente vuelta y reabre: la ingesta de esos segundos se pierde en silencio
  (inocuo por el upsert monótono: se relee) **pero** el `-wal` de la vieja queda ahí (B1) y, en Windows, el
  `os.replace` falla por fichero en uso tras agotar reintentos a mitad del proceso.
- **Arreglo.** La restauración **toma el arrendamiento** con su propia identidad (`almacen.identidad("restauracion")`
  + `adquirir_escritura`) y lo refresca durante el descifrado (`guardar_meta` no lo toca; usar `adquirir_escritura`
  de nuevo, que sí); si no lo consigue, se niega con el nombre del escritor. Con el arrendamiento en mano la barra
  recibe `SinEscritura` y no escribe. El `os.replace` ocurre dentro del TTL. Tras el reemplazo, `meta.escritor` de la
  base restaurada es el de otro momento/máquina: queda caducado y cualquiera lo toma. Documentar que `lecturas` y
  `fuentes.dispositivo_id` vendrán de la máquina origen: la primera vuelta relee todo (inodos distintos), lo que
  tarda ~20 s con los 1.370 logs y es inocuo.

### A4. La frase de paso en el proceso de la barra (diálogo nativo) y el `getpass` con eco
- **Evidencia.** 1.5.5: «`getpass` (CLI) o con un diálogo nativo de la barra». La barra es un proceso de larga vida
  con hilos, servidor HTTP local (`servidor_local.py`), renderizado de PNG y acceso al Llavero; en ella conviven
  tickets de sesión y la frase. `getpass` en Python, sin terminal controlable (lanzado desde la barra, desde un IDE o
  en Windows sin consola), **cae a `fallback_getpass` y lee con eco** tras un `GetPassWarning`.
- **Escenario.** El usuario pide un export con frase desde el icono: la frase y la clave derivada viven en la memoria de
  la barra el tiempo que dure la exportación; un volcado de la barra (WER en Windows, `systemd-coredump` en Linux)
  o una excepción con `locals` en el log se la lleva. En el CLI bajo un terminal sin TTY la frase sale en claro en
  pantalla y en el registro del terminal.
- **Arreglo.** **La barra nunca toca un secreto**: solo hace copias en modo destinatario (le basta la pública). El
  export con frase es exclusivo del CLI. En el CLI: `warnings.simplefilter("error", getpass.GetPassWarning)` y, sin
  TTY (`sys.stdin.isatty()` falso), negarse con un mensaje. Pedir la frase dos veces al cifrar. `resource.setrlimit
  (RLIMIT_CORE, (0, 0))` en POSIX durante el export (barato, no protege en Windows: documentarlo). Sin diálogo nativo
  en v1.

### A5. El enmarcado de los trozos no está especificado y una cabecera hostil decide tamaños
- **Evidencia.** 1.5.1: «tamaño de trozo (1 MiB)» en la cabecera; «el último trozo lleva la bandera y puede estar
  vacío». No dice cómo sabe el lector dónde acaba un trozo, cuántos hay, ni qué hace con bytes sobrantes o con cero
  trozos.
- **Escenarios.** Cabecera con tamaño de trozo 2^40 → el lector reserva 1 TiB o lee «hasta el final» en memoria.
  Fichero con cero trozos y bandera en ninguna parte → según la implementación, «base vacía válida». Bytes añadidos
  tras el último trozo → ignorados sin error (un vehículo para esconder datos o confundir). Contador que empieza en 1
  o salta → según cómo se calcule el AAD, pasa.
- **Arreglo.** En v1 el tamaño de trozo es **constante** (1 MiB) y la cabecera no lo lleva (o lo lleva y el lector
  rechaza cualquier otro valor). Lectura: leer exactamente `1 MiB + 16`; si llegan menos bytes o el siguiente byte es
  EOF (lectura de 1 byte de anticipación), es el último y se descifra con bandera=1; si no, bandera=0. Cero trozos →
  error. Tras el último trozo no puede quedar nada → error. El contador del AAD es el índice real (0, 1, 2…), así
  que saltos, duplicados y reordenaciones fallan por construcción. Al escribir, rechazar el trozo 2^32−1 (4 PiB:
  teórico, pero el test es barato). El «último trozo vacío» solo cuando el tamaño es múltiplo exacto de 1 MiB, para
  que la bandera no quede sin trozo que la lleve.

## MEDIO

### M1. «`cryptography` fijada con hashes» choca con el instalador actual
- **Evidencia.** `requirements.txt` tiene 2 paquetes sin hashes, solo macOS; `install.sh:42-44` instala `pillow` y
  `pyobjc-framework-Cocoa` a mano, no desde `requirements.txt`. `pip --require-hashes` exige hash de **todas** las
  líneas y de cada wheel por plataforma.
- **Escenario.** Se añade `cryptography==46.0.7 --hash=…` y `pip` falla en cuanto `pillow` no lleva hash; o se ponen
  hashes de macOS arm64 y el instalador de Windows no encuentra el wheel `win_amd64`.
- **Arreglo.** Un `requirements.txt` con marcadores (`pyobjc-framework-Cocoa; sys_platform == "darwin"`) y un
  `requirements.lock` por plataforma generado con `pip-compile --generate-hashes` (o, mínimo, versión exacta de
  `cryptography` y hashes de sus wheels abi3 para macOS arm64/x86_64, win_amd64 y manylinux); `install.sh` instala
  desde el lock. El import perezoso ya está en el diseño; añadir que `--copia-ahora` y la copia automática fallan con
  mensaje de «instala cryptography» en vez de excepción.

### M2. `serialize()` hasta 512 MiB de texto en claro en memoria, dentro de la barra
- **Evidencia.** 1.5.3. En macOS la memoria virtual va cifrada por defecto; en Windows el `pagefile` no (sin
  BitLocker) y en Linux el swap depende de la instalación. El tamaño real hoy es 24 MB, pero el umbral de 512 MiB
  significa que un proceso de barra de menús puede pasar a 1 GiB (texto + cifrado) sin avisar.
- **Arreglo.** Siempre **fichero temporal con `con.backup()` en `DATOS/tmp` (0700/0600)** y cifrado en streaming por
  trozos de 1 MiB: ese directorio ya contiene la base en claro, así que no añade una clase de exposición nueva, y
  la memoria queda en unos MiB. `serialize()` solo si el dueño quiere evitar el temporal en bases pequeñas (<64 MiB);
  no merece dos caminos: un solo camino, el del temporal. El temporal se crea con `O_EXCL` y nombre aleatorio, se
  borra en `finally`, y al arrancar se barre `DATOS/tmp` (un cierre en seco deja restos). Journal de APFS/NTFS, Time
  Machine y File History del directorio de datos: fuera de alcance explícito (ya lo está la base viva).

### M3. Metadatos en claro: tamaño total, fecha, versión del esquema y nombre del fichero
- **Evidencia.** 1.5.1 lleva «versión del esquema de la base y fecha UTC» en la cabecera, y el tamaño del cifrado
  revela el de la base a ±1 MiB. Nombre con «consumo» y fecha.
- **Escenario.** Quien ve la carpeta sincronizada aprende: producto usado, versión aproximada, ritmo diario de
  crecimiento (≈ actividad con agentes), y cuándo está encendido el equipo. Es poco, pero gratis para el observador.
- **Arreglo.** Quitar «tamaño total» de la cabecera (no hace falta: la bandera de último trozo cierra el flujo) y la
  versión del esquema (se lee del `user_version` tras descifrar: la cabecera no es de fiar para eso). Dejar la fecha
  (ya está en el nombre). Relleno opcional del último trozo a múltiplo de 64 KiB con bytes aleatorios y longitud
  real en los primeros 4 bytes del texto en claro: barato y enmascara el crecimiento diario. Nombre `copia-<fecha>.cgx`.

### M4. `quick_check` antes de cada copia desde el hilo de ingesta caduca el latido y una base dañada queda sin copias
- **Evidencia.** 1.5.3 «antes se ejecuta `quick_check`; un export de una base dañada se rechaza». Hoy `comprobar()`
  corre en el hilo de ingesta una vez al día (`ingestor.py:171-176`) y la vuelta tiene presupuesto de 5 s con TTL de
  90 s. `comprobar()` se documenta como «lento a tres años de datos» (`almacen.py:587`).
- **Escenarios.** (a) La copia diaria en el mismo hilo: `quick_check` + backup + cifrado + verificación de un fichero
  de cientos de MB en un disco de red pasan de 90 s → el latido caduca → el CLI `--ingerir` que espera turno
  (`costbar.py:46`) toma el puesto; la barra vuelve con `SinEscritura` hasta que el otro suelte. No corrompe, pero
  es la clase de comportamiento que la revisión de 1.3 cerró como bloqueante. (b) La base falla `quick_check` por un
  sector malo: el diseño **deja de hacer copias**; 14 días después no hay ninguna copia de los días anteriores al daño.
- **Arreglo.** Las copias viven en **su propio hilo con conexión `mode=ro`** (`con.backup()` funciona desde solo
  lectura y no necesita el arrendamiento), nunca en el hilo de ingesta. Reutilizar `meta.ultimo_quick_check` si tiene
  menos de 24 h y fue `ok` en vez de repetirlo. Si `quick_check` falla: **hacer la copia igual**, con sufijo
  `.danada.cgx`, excluirla de la rotación (las buenas no se borran por ella) y aviso persistente en la barra; lo
  recuperable de una base dañada vale más que nada. Registrar `meta.ultima_copia` (hora + nombre + huella del
  destinatario) en la base, no solo en la carpeta.

### M5. Copias automáticas: quién, cuándo y qué pasa cuando el destino no está
- **Evidencia.** 1.5.6 «diarias» sin decir qué proceso decide ni con qué criterio.
- **Escenarios.** Carpeta de red desmontada o OneDrive sin sesión: la copia falla a diario y nadie lo ve; criterio
  «diaria» basado en el fichero más nuevo de una carpeta que no está montada → o copia cada vuelta o nunca. Dos
  procesos (barra + `--copia-ahora`) a la vez: nombres con el mismo segundo.
- **Arreglo.** Criterio en la base (`meta.ultima_copia`), no en la carpeta. Lo lanza la barra (hilo de copias) y el CLI
  con `--copia-ahora`; el panel, nunca. Nombre con fecha-hora UTC y 4 hex aleatorios (`copia-20261020-031500-9f3a.cgx`),
  creado con `O_EXCL`. Aviso persistente en la barra si la última copia buena tiene más de 48 h o si falta
  `copias.destinatario` (ya lo dice el diseño para lo segundo). La carpeta de copias cifradas es **distinta** de
  `rutas.COPIAS` (ahí van las copias en claro de premigración, `almacen.py:201`, que deben quedarse en `DATOS`), y a la
  cifrada **no** se le aplica `ruta_no_fiable` (es la única ruta donde la nube está permitida).

### M6. Entrada de la clave privada en `--importar` y rutas peligrosas de la clave
- **Evidencia.** 1.5.5 enumera `--importar RUTA` pero no cómo llega la privada en modo 2.
- **Escenario.** Alguien implementa `--importar copia.cgx CGX-SECRET-KEY-1…`: la clave queda en `ps`, en el historial
  de la shell y en el log de la terminal. O el usuario deja `clave.cgx` en la misma carpeta de OneDrive que las copias:
  el cifrado a destinatario no cubre nada.
- **Arreglo.** `--clave-privada RUTA` (fichero) o lectura por `getpass` si no se da; nunca el valor en argumentos (test:
  cualquier argumento que empiece por `CGX-SECRET` aborta con mensaje). Al cargar la privada: rechazar si el fichero
  no es 0600 (POSIX), **avisar** si la ruta resuelve dentro de la carpeta de copias o cae en `ruta_no_fiable`, y
  negarse a **generarla** ahí. `--importar` sin confirmación interactiva de la huella del destinatario y del número
  de turnos que trae la copia no reemplaza nada.

### M7. Windows: lo que no se puede probar aquí y cómo acotarlo
- **Evidencia.** Sin máquina Windows en este entorno; `permisos.py` no actúa en Windows; `os.replace` sobre un fichero
  abierto por otro proceso da `PermissionError` (sharing violation); Defender y el indexador abren ficheros recién
  escritos; `MAX_PATH` 260 sin «long paths»; OneDrive «Files On-Demand» deja marcadores que no son ficheros hasta
  que se descargan; `getpass` sin consola Win32 (mintty/Git Bash) hace eco o cuelga.
- **Cómo acotarlo sin Windows.** (1) Lógica de rutas y nombres con `ntpath` en tests (ya previsto). (2) Inyección de
  `PermissionError` en `os.replace` y `os.remove` N veces para probar los reintentos y el abort limpio al agotarlos.
  (3) Rutas largas: test con una carpeta de 250 caracteres y aviso al configurar `copias.carpeta` si la ruta final
  pasa de 200. (4) **`costbar.py --autoprueba-copias`**: en un directorio temporal genera par, hace copia de una base
  de prueba, la verifica, la restaura y compara turnos; un solo comando que el dueño ejecuta en Windows y pega la
  salida. Es el criterio de salida realista; una máquina virtual (UTM/Parallels con Windows 11 ARM) es la vía sin
  gastar minutos remotos. (5) Documentar que en Windows la privacidad de los ficheros descansa en la ACL de
  `%LOCALAPPDATA%` (no hay 0600) y que `getpass` exige Windows Terminal/cmd/PowerShell.

## BAJO

- **L1. Compromiso de clave (key commitment).** ChaCha20-Poly1305 no es comprometedor; el ataque de oráculo de partición
  necesita un oráculo en línea que aquí no existe. Opcional y barato: 32 bytes `HKDF(clave, "cgx1 compromiso")` en la
  cabecera comparados con `hmac.compare_digest` antes de descifrar; da «frase incorrecta» sin leer el primer trozo y
  cierra el caso multi-clave. Las huellas de públicas se comparan con `==` (no son secretas).
- **L2. Prefijo de nonce aleatorio.** Con clave nueva por fichero no aporta seguridad; no estorba. Lo que importa es
  que el lector use el índice real como contador (A5). Contador de 4 bytes = 4 PiB por fichero: suficiente.
- **L3. Sustitución o rebaja de modo/parámetros.** La cabecera está en el AAD y en `info` de HKDF (modo 2); en modo 1
  la clave no depende de la cabecera, pero el AAD la cubre: cualquier cambio da `InvalidTag`. El coste del scrypt
  con parámetros ajenos se paga antes de saber que es falso: por eso A1. Test: cambiar el byte de modo y comprobar
  `InvalidTag` sin haber escrito nada.
- **L4. Puntos X25519 de orden bajo.** `cryptography` los rechaza (medido). Test de regresión con la pública todo
  ceros por si cambia la biblioteca.
- **L5. Sin secreto hacia adelante en modo 2.** Quien obtenga la privada lee todas las copias pasadas. Es la
  naturaleza del modo; documentarlo y recordar la rotación (A2.4).
- **L6. Frase débil.** Mínimo 12 caracteres y no igual al nombre del fichero ni todo el mismo carácter; mejor que
  una lista negra: `--frase-sugerir` con 6 palabras de un diccionario (`secrets.choice`), que es lo que un usuario
  acabará apuntando en papel.
- **L7. Versión del esquema al restaurar.** Mayor que la conocida → `almacen.abrir` ya lo rechaza (`almacen.py:192`),
  pero debe comprobarse en el temporal **antes** del reemplazo; menor → `_migrar` copia a `premigracion` y migra:
  correcto. El valor de la cabecera no cuenta (M3).
- **L8. Memoria al descifrar un fichero hostil.** Con A5 (trozo fijo) el lector nunca reserva más de ~2 MiB; sin
  compresión no hay bomba; el único coste controlable por el atacante es scrypt (A1).
- **L9. Historial de shell y `ps`.** Solo rutas en argumentos; ya cubierto con A4/M6. Añadir el test «ni frase ni
  clave en logs, argv, ficheros» que el diseño ya pide, ejecutado con un `grep` sobre `DATOS`, `logs/` y el temporal.

---

## Plan de implementación por pasos pequeños (orden y tests)

1. **`infobar/cgx.py` — formato puro, sin SQLite ni claves.** Cabecera `struct` fija (magia, versión, modo, sal o
   efímera, pública del destinatario, prefijo de nonce, fecha), `cifrar_flujo(entrada, salida, clave, cabecera)` y
   `descifrar_flujo` con trozo constante. Tests: vector conocido (clave, prefijo y texto fijos → bytes esperados,
   guardado en `tests/datos`), ida y vuelta con 0 bytes, 1 byte, 1 MiB exacto, 1 MiB + 1, 3 MiB; manipulación de cada
   byte de la cabecera, de cada trozo y de la etiqueta; truncado en frontera y a mitad; trozos reordenados,
   duplicados, saltados; bytes de cola; cero trozos.
2. **Claves.** `clave_de_frase` con límites (A1: cuatro bordes), par X25519 + codificación Bech32 con prefijo,
   huella, carga desde texto con erratas (debe fallar con mensaje), punto de orden bajo (L4).
3. **Modo destinatario + firma del productor (B2).** Efímera + HKDF con `info = cabecera`; tráiler Ed25519. Tests:
   destinatario distinto, efímera sustituida, firma de otra clave, copia válida de otro dispositivo autorizado.
4. **Fichero de clave privada con frase** = `.cgx` modo 1 de un trozo; `--claves-generar` con ensayo obligatorio (B3.1)
   y confirmación `SI`; la config solo se escribe tras el ensayo.
5. **`infobar/exportacion.py` — origen y copia.** Backup API a `DATOS/tmp` (M2), cifrado en streaming, verificación
   por relectura (B3.2), nombre con `O_EXCL`, rotación escalonada que no borra sin verificación (B3.3-4), sufijo
   `.danada`. Tests con una base real pequeña; fallo inyectado al escribir (`OSError` en el trozo 2): no queda
   fichero; disco de destino inexistente; dos copias en el mismo segundo.
6. **Restauración.** Arrendamiento propio (A3), `user_version` y `quick_check` en el temporal, checkpoint + borrado de
   `-wal`/`-shm` (B1), `os.replace` con reintentos e inyección de `PermissionError` (M7.2), copia previa con
   `backup()` en `--forzar`. Test de regresión: el experimento del WAL huérfano de este informe.
7. **CLI.** `--exportar`, `--importar --clave-privada`, `--copia-ahora`, `--probar-restauracion`, `--frase-sugerir`;
   `getpass` endurecido (A4); test de que un argumento con `CGX-SECRET` aborta; test «sin rastro» (L9).
8. **Hilo de copias en la barra** con conexión `mode=ro` y `meta.ultima_copia` (M4/M5); test de que una copia lenta
   (backup simulado de 3 s) no retrasa la vuelta de ingesta ni toca el latido.
9. **`--autoprueba-copias`** para el ensayo en Windows del dueño (M7.4) y lista de comprobación en el README.
10. **Dependencias.** `requirements.txt` con marcadores y lock con hashes por plataforma; `install.sh` desde el lock (M1).

Cada paso cierra con sus tests en verde antes de abrir el siguiente; 1-3 no tocan la base y se pueden revisar como
criptografía aislada.

## Lo que no se puede probar aquí
Windows real (reemplazo con fichero en uso, ACL, `getpass`, OneDrive bajo demanda, rutas largas), el swap/pagefile y
los volcados de proceso en Windows/Linux, y el comportamiento de un recurso de red concreto con escrituras cortas.
Se acota con M7 y con el ensayo del dueño; el resto de los hallazgos están verificados con código o medición.

## Supuestos
- El atacante que importa en B2 es quien ve la carpeta de copias sin ser usuario local de la máquina (confianza alta:
  es la premisa del modo destinatario). Si el dueño solo quiere copias en disco local privado, B2 baja a medio.
- La base en producción seguirá siendo pequeña (decenas de MB) durante meses (confianza media): M2/M4 pesan más
  si crece a GB.
- `cryptography` se instalará en el Python del proyecto (3.11+) con wheels oficiales; no se usa `hashlib.scrypt`
  (no está en el Python de Apple).

Status: DONE_WITH_CONCERNS
Resumen: la construcción por trozos con AAD es correcta en lo esencial, pero el diseño tal cual pierde datos al
restaurar (WAL huérfano, verificado), no autentica el origen de las copias a destinatario y puede rotar hasta quedarse
sin ninguna copia recuperable. Con B1-B3 y A1-A5 incorporados al texto del plan, 1.5 se puede codificar siguiendo los
diez pasos de arriba.
