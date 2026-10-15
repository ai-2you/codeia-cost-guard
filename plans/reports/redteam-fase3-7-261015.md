# Red team del diseño 3.7 (alta guiada con contraseña, detección y seguimiento de webs)

Fecha: 2026-10-15 · Objeto: `plans/261013-2350-sqlite-historico/fase3-7-alta-y-webs-diseno.md` (v1) contrastado con el código de
`feat/fase1-ingesta-hermes`. Solo lectura: no se ha tocado ningún servidor ni la red; los ejemplos usan direcciones de documentación (RFC 5737).
Marcas: **[V]** verificado leyendo código, manuales del Mac o fuentes primarias (OpenSSH, fail2ban); **[S]** supuesto. Gravedad: **C** crítica ·
**A** alta · **M** media · **B** baja. No se repiten los hallazgos de `redteam-fase3-3a-261015.md` (H1–H20 de aquel informe); se citan cuando aplican.

## Modelo de atacante
(a) web maliciosa en el navegador del dueño · (b) otro proceso del mismo usuario del Mac, extensión del navegador incluida · (c) otro usuario local del
Mac · (d) servidor monitorizado comprometido (parcial: usuario sin privilegios; total: root) · (e) cliente del servidor que controla etiquetas de
Docker/Traefik y dominios · (f) MITM entre el Mac y el servidor en la primera conexión.

## TL;DR
1. **El método contraseña no debería entrar en 3.7a.** Tal como está diseñado (FIFO + `SSH_ASKPASS`) tiene una carrera que entrega la contraseña a
   cualquier proceso del mismo usuario (b), se cuelga 90 s si ssh pregunta dos veces, y «bytearray a ceros» es teatro: la contraseña vive en tres
   objetos inmutables de Python antes de llegar al bytearray [V]. Si el dueño lo exige, el diseño mínimo es un **pseudoterminal** (como `sshpass`):
   sin FIFO, sin script askpass, sin fichero, un solo prompt, y las mismas opciones endurecidas de `orden_ssh`.
2. **TOFU**: antes de enseñar ninguna huella, comprobar con `ssh-keygen -F` si el host ya está en `~/.ssh/known_hosts` (el dueño ya se conectó a mano:
   es el caso normal y no hay TOFU); `ssh-keyscan` debe pedir **un** tipo de clave (abre una conexión por tipo [V]) y escribir en el **mismo** fichero
   que lee el monitor (hoy el por defecto [V]); nunca un botón «reemplazar huella».
3. **Webs detectadas**: proponer solo los dominios que **resuelven a la IP pública del servidor**. Cierra de golpe el sondeo de webs de terceros
   (e), el SSRF por dominios hostiles (d) y la mayor parte del spam de 200 dominios. Falta rechazar el prefijo NAT64 `64:ff9b::/96`, que Python
   da por global [V].
4. **Seguimiento**: el presupuesto diario por (servidor, evento, tipo) deja que diez dominios de un cliente que parpadean agoten los 100 `cayo` del
   día y silencien la caída real del dueño [V]; y una caída de red del Mac fabrica hasta 100 «cayó» y 100 «volvió». Hacen falta un canario de red y
   presupuesto por clave con prioridad para el primer `cayo` del día.
5. **Migración 5**: hay que reconstruir `eventos` además de `recursos` (las dos tienen `CHECK` sobre `tipo`, y `eventos` sobre `evento`) [V].

---

## 1. La contraseña en la página y en el Mac

### H1 (A) El FIFO entrega la contraseña al primero que lo abra, y el askpass lo ejecuta cualquiera
- Evidencia [V]: el diseño (§A.3) pone el FIFO en un directorio 0700 y «lo escribe una vez». Un directorio 0700 frena a (c), no a (b): un proceso del
  mismo usuario que haga `open(fifo, O_RDONLY)` antes que el askpass recibe la escritura de la app (el núcleo entrega a un lector cualquiera); el
  script askpass es ejecutable por el usuario, así que (b) también puede lanzarlo. No hay `closefrom`-proof: `ssh` descarta todos los descriptores
  heredados al arrancar (`closefrom(STDERR_FILENO + 1)` en `ssh.c`, «Discard other fds that are hanging around») [V fuente OpenSSH], por eso el
  diseño recurrió a un FIFO con nombre; esa es justo la parte que lo rompe.
- Escenario: (b) es cualquier binario que el dueño haya instalado (npm global, un plugin de editor, una extensión nativa del navegador). Hoy ese
  atacante ya puede leer `~/.ssh/infobar_ed25519` (solo lectura en el servidor) y `config.json`; con el método 2 se lleva **root del servidor**.
- Cambio: ver «Diseño mínimo» más abajo (pty). Si se mantiene askpass: el askpass debe recibir un nonce de un solo uso en `SSH_ASKPASS` (ruta del
  script con el nonce en el nombre, en un directorio recién creado con `mkdtemp`) y responder por un socket Unix `SOCK_STREAM` que la app abre **antes**
  de lanzar ssh y en el que acepta una sola conexión; sigue sin resistir a (b), solo elimina la carrera.
- Prueba: lanzar en paralelo un lector del FIFO y el alta en un entorno de pruebas; el lector debe recibir la contraseña (demuestra H1); con pty, no
  existe nada que leer.

### H2 (A) «Escribe una vez» cuelga el alta cuando ssh pregunta más de una vez
- Evidencia [V man ssh_config del Mac]: `NumberOfPasswordPrompts` vale 3 por defecto; con `SSH_ASKPASS_REQUIRE=force` **cada** prompt (contraseña,
  `keyboard-interactive` de PAM, que en Ubuntu es la vía normal, y cualquier confirmación de clave de host si `StrictHostKeyChecking` no es `yes`)
  invoca de nuevo el askpass. La segunda invocación abre el FIFO para leer y se queda bloqueada hasta que aparezca un escritor: nadie escribe, así que
  el alta muere a los 90 s con «timeout» y sin diagnóstico. Lo mismo con `scp` (modo SFTP: lanza `ssh` con el entorno heredado) y con la prueba final.
- `SSH_ASKPASS_REQUIRE` existe desde OpenSSH 8.4; el Mac tiene `OpenSSH_10.3p1` en `/usr/bin/ssh` [V]. No es problema de versión.
- Cambio: `-o NumberOfPasswordPrompts=1 -o PreferredAuthentications=keyboard-interactive,password -o PubkeyAuthentication=no -o IdentityAgent=none`
  + `StrictHostKeyChecking=yes` siempre. Con pty no hace falta FIFO, pero el tope de un prompt sigue siendo necesario por fail2ban (H23).
- Prueba: servidor con `PasswordAuthentication no` + `KbdInteractiveAuthentication yes`; con contraseña mala debe fallar en < 10 s con mensaje
  «contraseña rechazada», no «timeout».

### H3 (M) El bytearray a ceros no borra nada: la contraseña ya está en tres objetos inmutables
- Evidencia [V `infobar/servidor_local.py:356`]: `json.loads(self.rfile.read(n).decode("utf-8"))` → `bytes` del cuerpo, `str` decodificado y `str` del
  campo; los tres son inmutables y viven hasta que el recolector los libere, sin sobrescritura. Copiarlos a un `bytearray` y ponerlo a cero
  añade una cuarta copia, no quita ninguna. Lo mismo vale para la `str` que se pase a `sudo -S` por stdin.
- Qué sí protege de verdad: `ps` no la ve (ni argv ni entorno) [V diseño]; `Cache-Control: no-store` y `no-referrer` ya están [V :197-198]; el log solo
  dice el paso [V diseño]; los volcados de memoria están desactivados por defecto en macOS (`ulimit -c 0`) [S alta]; leer la memoria de otro proceso
  del mismo usuario exige `task_for_pid`, que el *hardened runtime* de `/usr/bin/python3` niega sin entitlements [S media; si la app corre con un
  Python de Homebrew sin hardened runtime, (b) puede leerla con `lldb`].
- Cambio: quitar la frase del diseño (es una promesa falsa) y sustituirla por lo que es cierto: «no se persiste, no se registra, no pasa por argv ni
  entorno; vive en la memoria del proceso de la barra durante el alta». Si se quiere acotar de verdad: leer el cuerpo con `readinto` en un
  `bytearray`, extraer el campo con un parseador propio y no construir `str` nunca; no compensa salvo que el método 2 sea el principal.

### H4 (M) El gestor de contraseñas del navegador ofrecerá guardar «la contraseña de 127.0.0.1»
- Evidencia [S alta]: Chrome y Safari ignoran `autocomplete=off` en campos `type=password`; al vaciarse el campo o cambiar la vista tras un `fetch`,
  Chrome aplica su heurística de «formulario enviado» y propone guardar. La contraseña de root del servidor acabaría en iCloud Keychain o en la
  sincronización de Chrome con la etiqueta «127.0.0.1:47391», fuera del control del diseño («no se guarda en ningún sitio» dejaría de ser cierto).
- Cambio: no usar `<form>`; `autocomplete="new-password"` (reduce, no elimina); vaciar y **retirar del DOM** el campo antes del `fetch`; texto junto
  al campo: «si tu navegador te ofrece guardarla, di que no». Y, sobre todo, H0.
- Prueba: manual en Chrome y Safari del Mac: completar un alta y comprobar que no aparece la burbuja de guardar.

### H5 (B) La puerta del panel resiste (a) y (c); (b) con extensión es inatacable por diseño
- Evidencia [V `servidor_local.py:208-215, 283-288, 341-347, 361-366`]: `Host` exacto, `Sec-Fetch-Site` solo `same-origin`/`none` (el `GET /entrar`
  también pasa por `_host_ok`), `Origin` exacto en POST, `Content-Type` `application/json`, cookie `HttpOnly; SameSite=Strict` canjeada por ticket de
  60 s. Un POST «simple» desde otra web no puede falsear ni `Origin` ni el tipo; con rebinding el `Host` no cuadra; otro usuario del Mac no tiene la
  cookie. Los 7 tests de `tests/test_config_y_servidor_local.py` cubren `Sec-Fetch-Site`/`Origin` [V recuento].
- Huecos menores: (i) el `/iniciar` del alta debe devolver un **id de alta** aleatorio que `/estado` y la confirmación de huella exijan, para que
  una pestaña vieja o un segundo panel no interfieran; (ii) `GET /servidores/alta/estado` debe ser idempotente y no devolver más que el paso y un
  código de error del conjunto cerrado; (iii) la URL `/entrar?t=` queda en el historial, pero es un ticket gastado [V `_canjea`].
- Una extensión con permisos sobre `127.0.0.1` lee el DOM, el campo y la respuesta: no hay mitigación. Es el argumento de peso para H0.

### H6 (B) `sudo -S -p ''` por stdin: correcto, con dos detalles
- Si el usuario de alta es root (`id -u` = 0 en el remoto), no invocar `sudo` (algunos servidores no tienen `sudo` instalado para root y el alta fallaría
  por un motivo falso). Con contraseña mala, `sudo -S` reintenta leyendo stdin: llega EOF y falla con código 1, no se cuelga [S alta]. El `sudo` del
  servidor puede tener `use_pty` o `requiretty`: con pty del lado del Mac (`ssh -tt`) ambos funcionan; con `-T` y `requiretty`, no.

### H0 (A) Decisión: ¿debe existir el método contraseña?
- Hoy `alta-servidor.sh` ya resuelve el caso en el Terminal (método 3) y el método 1 cubre a quien tiene su clave en el agente. El método 2 añade:
  superficie en el navegador (H4, extensiones), una carrera local (H1), una promesa falsa (H3) y el riesgo de fail2ban (H23), a cambio de ahorrar
  pegar una línea. Recomendación: **3.7a sin método 2**; si el dueño lo exige, implementarlo con pty y solo en macOS/Linux, como ya propone el diseño.
  Evidencia que cambiaría la recomendación: usuarios finales que no son el dueño (hoy no los hay).

### Diseño mínimo más seguro para el método 2 (si se mantiene)
1. `POST /servidores/alta/iniciar` con la puerta actual; la contraseña en el JSON (no hay canal mejor dentro del navegador).
2. La app abre un **pseudoterminal** (`pty.openpty`), lanza `ssh` con `start_new_session=True` y el esclavo como stdin/stdout/stderr (así el pty es la
   terminal de control y `ssh` lee la contraseña de `/dev/tty`, que es el pty); opciones: las de `orden_ssh` (`ProxyCommand=none`,
   `PermitLocalCommand=no`, `ClearAllForwardings=yes`, `ForwardAgent=no`, `IdentityAgent=none`, `IdentitiesOnly=yes`, `StrictHostKeyChecking=yes`,
   `LogLevel=ERROR`) más `NumberOfPasswordPrompts=1`, `PubkeyAuthentication=no`, `PreferredAuthentications=keyboard-interactive,password`,
   `ConnectTimeout=8`, `-tt` solo en el paso de `sudo`. La app lee del maestro, reconoce `assword:` al final del flujo y escribe la contraseña +
   `\n` **una vez**; cualquier segundo prompt se responde cerrando el pty (fallo limpio). Nada de `SSH_ASKPASS`, FIFO, ficheros ni scripts.
3. `scp` igual (hereda `-o`); `sudo -S -p ''` por stdin del canal, o por el mismo pty.
4. Tras el alta, la contraseña se descarta; en el diseño se describe como «en memoria del proceso durante el alta», sin prometer borrado.
5. Bloqueo por host tras un fallo de autenticación: 10 min (H23).

## 2. TOFU y MITM

### H7 (A) Primero `ssh-keygen -F`: en el caso normal no hay TOFU, y el monitor lee el fichero por defecto
- Evidencia [V `infobar/servidores_ssh.py:30-36`]: `orden_ssh` no fija `UserKnownHostsFile`, luego el monitor usa `~/.ssh/known_hosts`; la guía dice
  «entra una vez a mano, comprueba la huella y acéptala» [V `docs/guia-servidores.md:132-133`]. Si el alta escribiera en un fichero propio, el
  monitor diría «clave de host no confiada» y esperaría 30 min entre intentos [V `servidores_ciclo.py:26-27`].
- Cambio: el alta consulta `ssh-keygen -F '[host]:puerto'` (o `host` si es 22) y, si hay entrada, **no enseña huella ni hace keyscan**: la confianza ya
  la dio el dueño desde el Terminal, con su propio cliente. Solo si no hay entrada, keyscan + confirmación. Escribir con `ssh-keygen`/append atómico
  bajo cerrojo en el mismo fichero; respetar `HashKnownHosts` del usuario si está activo (añadir con `-H`).

### H8 (A) `ssh-keyscan` abre una conexión por tipo de clave, y la huella «del proveedor» casi nunca existe
- Evidencia [V fuente `ssh-keyscan.c`, `do_one_host`: `for (j = KT_MIN; j <= KT_MAX; j *= 2) ... conalloc(...)`]: `-t ed25519,ecdsa,rsa` son tres
  conexiones TCP preauth. En fail2ban con `mode = normal` no cuentan (son «NOFAIL») [V `filter.d/sshd.conf`]; con `mode = ddos`/`aggressive` sí
  cuentan las desconexiones `[preauth]`. Con tiempo de espera de 5 s por defecto [V].
- Pocos proveedores muestran la huella en el panel; el dueño pulsará «Es esta» sin mirar. Eso convierte el TOFU en teatro frente a (f).
- Cambio: pedir solo `-t ed25519` (si no hay, `ecdsa`, y por último `rsa`): una conexión. Mostrar la huella SHA256 **y** la orden exacta para
  obtenerla desde la consola del proveedor (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`), con el botón «Es esta» deshabilitado 10 s y un
  texto honesto: «si no puedes comprobarla, el riesgo es que alguien en la red se haga pasar por tu servidor y reciba tu contraseña». Tras
  autenticar, `UpdateHostKeys` (por defecto en 10.x cuando no hay `ControlMaster`) aprende el resto de tipos de forma autenticada [S alta].
- Cambio de huella: si `-F` existe y keyscan da otra, abortar con «la clave del servidor ha cambiado: si lo reinstalaste, ejecuta en el Terminal
  `ssh-keygen -R host` y vuelve a intentarlo». Nunca un botón «reemplazar»: es exactamente lo que (f) necesita.
- MITM entre keyscan y ssh: no hay ventana, porque ssh fija la clave con `StrictHostKeyChecking=yes` contra lo recién escrito [V diseño §A.2]; un DNS
  que cambie entre ambos (rebinding) termina en «host key mismatch», que aborta sin enviar la contraseña (la autenticación va después del KEX).
- Prueba: VM con dos claves de host distintas detrás de un balanceador; el alta debe abortar sin que `sshd` registre ningún intento de contraseña.

## 3. Inyección

### H9 (M) El alta debe usar la lista cerrada de opciones de `orden_ssh`, también en `scp` y en la prueba final
- Evidencia [V `servidores.py:11`]: `_SSH` admite un alias de `~/.ssh/config` como host (sin `@`). El `ProxyCommand`/`LocalCommand` que el dueño (o (b)
  escribiendo en su `~/.ssh/config`) tenga ahí se ejecutaría en el alta si no se neutraliza como hace el monitor [V `servidores_ssh.py:33-35`].
  `scp` acepta las mismas `-o`. Mostrar en la página el host real resuelto con `ssh -G` (como `host_de_ssh` [V `servidores_externo.py:94-102`]).
- `--` antes del destino en `ssh`, `scp` y `ssh-keyscan` (getopt lo respeta [V]); el regex ya impide `-` inicial [V `servidores.py:11`].

### H10 (M) Clave pública: generar con comentario fijo y validar en el Mac antes de componer la orden remota
- Evidencia [V `alta-servidor.sh:21`, `instalar-en-servidor.sh:18`]: el comentario es `infobar@$(hostname -s)` y el instalador exige
  `[A-Za-z0-9@._-]{1,60}`; un nombre de Mac con caracteres fuera de ese conjunto rompe el alta con «clave publica no valida». En la app, `-C infobar`
  y, antes de incrustarla entre comillas simples en `sudo bash ... '<clave>'`, validar con **el mismo** regex en el Mac (una clave manipulada por (b)
  con `'` dentro sería inyección de shell como root en el servidor). Mejor aún: pasar la clave por stdin del script, no por argv.

### H11 (B) `mktemp -d` remoto y TOCTOU: aceptable, con el regex ya existente
- Evidencia [V `alta-servidor.sh:26-28`]: `tail -n 1` + regex `^/tmp/infobar-alta\.[A-Za-z0-9]{6}$`. Un (d) parcial no puede sustituir el directorio
  (0700 del usuario de alta, `/tmp` con sticky bit); un (d) total ya es root. Mantener el regex y el `rm -rf` solo sobre esa ruta.

### H12 (B) Salida del servidor hacia la página: ya pasa por DOM o `esc()`
- Evidencia [V `panel_assets/ajustes.js:7-14` (constructor DOM), `app.js:6` (`esc`), `infobar/desplegable_servidores.py:50` (error de web escapado),
  `servidores_ssh.py:39-52` (`clasifica_error` cerrado salvo el código numérico)]. Los nombres `web <host>` deben revalidarse en el Mac con `_DNS`
  [V `servidores.py:16`] antes de entrar en la caché; con eso, aunque alguien usara `innerHTML` en el futuro, no hay payload posible.
- Prueba: línea `web <script>alert(1)</script> https` y `web a.example ### version https` en un test de `analizar`+parser de webs → cero
  propuestas, sección no invalidada por eso (la línea se descarta y se cuenta).

## 4. Detección de webs

### H13 (A) Proponer solo dominios que resuelven a la IP pública del servidor
- Escenario (e): un cliente pone `traefik.http.routers.x.rule=Host(\`banco.example\`)` en su contenedor; el Mac propone «banco.example»; si el dueño la
  marca, su Mac sondea una web ajena cada 5 min (cada 60 s cuando «cae») y anota «cayó/volvió» de un sitio que no es suyo. Volumen bajo (no es DoS),
  pero es tráfico no consentido desde la IP del dueño y datos falsos en su historial. Escenario (d): 200 dominios inventados.
- Cambio: en el Mac, para cada dominio detectado, `ip_publica(dominio)` [V `servidores_externo.py:18-26`] y comparar con `ip_publica(host_de_ssh(...))`;
  coincide → casilla disponible; no coincide → listada en gris con «no apunta a este servidor (¿Cloudflare/otro proxy?): añádela a mano si es
  tuya»; resuelve a IP privada o no resuelve → no se muestra. Esto convierte la lista de (d)/(e) en ruido inofensivo y elimina el SSRF de la propuesta.
- Prueba: etiqueta `Host(\`127.0.0.1.nip.io\`)` y `Host(\`localhost.example\`)` → nada que marcar.

### H14 (M) SSRF residual en `medir_web`: NAT64 pasa por global
- Evidencia [V Python 3.14.6 del proyecto]: `ipaddress.ip_address('64:ff9b::7f00:1').is_global` → `True`; `::ffff:127.0.0.1` → `False` (bien).
  Un dominio con `AAAA 64:ff9b::7f00:1` pasa `ip_no_publica`; en una red con NAT64 la conexión acaba en `127.0.0.1` de la pasarela. Riesgo bajo en la
  práctica, pero gratis de cerrar: rechazar `64:ff9b::/96` y `64:ff9b:1::/48` en `ip_no_publica` [V `servidores.py:20-23`].
- Lo demás está cerrado [V `servidores_externo.py:36-39, 47-48`]: se conecta a la IP ya comprobada (sin rebinding), cualquier IP no global en la
  respuesta de `getaddrinfo` rechaza el dominio entero, el puerto lo deriva el Mac del esquema (80/443) y la URL la construye el Mac
  (`https://{host}/`): sin userinfo ni puertos raros. Punycode: mostrar `xn--` tal cual en la lista (nunca decodificar a Unicode en la UI) y marcarlo.

### H15 (M) Agotamiento: 100 webs no caben en el ciclo actual
- Evidencia [V `servidores_ciclo.py:29, 74`]: hoy las webs se miden **en serie dentro del hilo del servidor** (`[medir_web(u) for u in ...[:4]]`) y la
  tanda tiene 32 s; con `TIMEOUT_WEB = 5` [V externo:12], 100 webs caídas son 500 s por servidor y 4 en paralelo son 125 s. El diseño (§C) habla de
  un sondeo por web cada 5 min y cada 60 s si está caída: 100 caídas con concurrencia 4 no entran en 60 s y el calendario se retrasa sin aviso.
- Cambio: sondeador propio fuera del ciclo SSH, cola con prioridad, 4 en vuelo, **presupuesto global de 60 sondeos/min** y modo «caída» limitado a 10
  webs a la vez por servidor (el resto sigue a 5 min); si no toca, se anota «sin dato», no «caída». `TOPE_BYTES` (128 KiB [V ssh.py:14]) soporta la
  sección nueva (200 líneas ≈ 15 KiB) junto al inventario (230 líneas).

### H16 (M) Ficheros de Traefik leídos como root: `grep -o` basta para no filtrar, pero el patrón y el glob necesitan ajuste
- Evidencia [V diseño §B]: solo sale la coincidencia; un hash de auth básica no coincide con ``Host(`[a-z0-9.-]+`)``. Ajustes: (i) `grep -oiE` (las
  etiquetas pueden ir en mayúsculas) y pasar a minúsculas; (ii) ``Host(`a.example`, `b.example`)`` solo captura la primera: extraer con
  ``grep -oE 'Host\([^)]{1,1024}\)' | grep -oE '`[A-Za-z0-9.-]{1,253}`' | tr -d '`'``; (iii) no seguir enlaces ni leer FIFOs/ficheros enormes:
  `find DIR -maxdepth 1 -type f -size -1M -name '*.yml'` y `head -c 1M` por fichero, dentro de `t` (3 s) [V `recoger-root.sh:15-21`]; (iv) rutas de
  EasyPanel: confirmar la real antes de codificarla [S]. Un cliente con acceso a Dokploy puede escribir cualquier fichero dinámico, pero su efecto
  es solo la lista de hosts (H13 lo neutraliza).
- `docker service inspect --format '{{json .Spec.Labels}}'`: una etiqueta de 1 MiB pasa por la tubería, pero solo el `grep -o` sale; `cut -b -1200`
  por línea como en el inventario [V root.sh:79].

### H17 (B) Qué ejecuta root con entradas controlables
- Nada nuevo respecto a 3.3a: ids de contenedor (no nombres) en `docker inspect` [V root.sh:73-79]; la sección `webs` debe seguir igual (ids), sin
  globs sobre nombres de contenedor ni de servicio. La única ruta con glob es la de ficheros de Traefik (H16 iii).

## 5. Seguimiento

### H18 (A) Diez dominios de un cliente que parpadean agotan los `cayo` del día del servidor entero
- Evidencia [V `servidores_recursos.py:50-63`]: `_evento` corta a 100 por (servidor, evento, tipo) y día y a 10 por clave y día; `tipo='web'` sería un
  cubo único. (e) hace que sus 10 dominios alternen 5xx/200 cada minuto (o retira sus DNS): 10 × 10 = 100 `cayo` + 100 `volvio` antes del mediodía;
  la caída real de la web del dueño esa tarde no genera evento (`_evento` devuelve `False` y el diseño no dice que `cayo` se reintente como
  `aparecio` [V :126-128]).
- ¿Puede (d) fabricar eventos de otra web o borrar su historial? No: las sondas salen del Mac y las claves son `sha256(sal, tipo, nombre)` con sal
  local [V :43-44]; el servidor solo influye en la lista propuesta (H13).
- Cambio: presupuesto por clave (10/día → `inestable`, ya existe) **y**, cuando el cubo diario esté lleno, garantizar el primer `cayo` del día de cada
  clave (reservar 20 % del cubo para «primer evento de la clave»). Marcar `cayo` como reintentable igual que `aparecio`.

### H19 (A) La red del Mac es un punto único de fallo que fabrica caídas masivas
- Escenario: Wi-Fi caído, VPN que corta, portal cautivo (devuelve 200/302 a todo con un certificado ajeno → `ssl.SSLError` → «caída» [V externo:87]).
  Con «dos lecturas seguidas ≥ 60 s» bastan dos minutos sin red para 100 `cayo` y luego 100 `volvio`, más `horas_recurso` con «caídas» falsas.
- Cambio: canario de red: si en la misma ronda fallan ≥ 80 % de las webs de **todos** los servidores, o falla también el SSH del servidor dueño de la
  web (`entrada.error` [V `servidores_historico.py:307`]), la ronda es «sin dato» (no evento, no caída en la hora). El diseño ya trata el Mac apagado
  como «sin dato»; esto cubre el Mac encendido sin red.

### H20 (M) Migración 5: reconstruir `recursos` **y** `eventos`; cuidar claves foráneas e índices
- Evidencia [V `servidores_historico.py:82, 97-98`]: `recursos.tipo` y `eventos.tipo`/`eventos.evento` llevan `CHECK IN (...)`; añadir `web`, `cayo`,
  `volvio`, `empezo_5xx` exige recrear las dos tablas (`CREATE ... _nueva; INSERT ... SELECT; DROP; ALTER RENAME`), reponer `WITHOUT ROWID`, `STRICT`,
  el índice `eventos_por_clave` [V :101] y las columnas nuevas (`codigo INTEGER CHECK(codigo BETWEEN 100 AND 599)`, `ms`, `cert_dias`, todas NULL
  para los tipos antiguos). Va dentro del `BEGIN IMMEDIATE` de `_migrar` [V :148-161], con `foreign_keys=ON` [V `sqlite_base.py:53`]: ninguna tabla
  referencia a `recursos`/`eventos`, así que `DROP` y `RENAME` son seguros; `PRAGMA foreign_keys` no se puede cambiar dentro de la transacción (no
  hace falta). La copia previa excluye `recursos` y `eventos` [V :179]: si la migración falla, el `ROLLBACK` es la única red; el test debe migrar una
  base v4 con filas y comprobar recuento y `CHECK`.
- `etiqueta` admite dominios [V :84, `[A-Za-z0-9_./-]`]; la clase «personal» ya está declarada [V :124].

### H21 (M) Dominios como dato personal: borrar al desmarcar, no a los 30 días
- Evidencia [V `servidores_recursos.py:162`]: los inactivos se purgan a los 30 días. Una web desmarcada por el dueño debe borrarse de `recursos` (y de
  la caché 48 h y de `servidor.webs`) en el mismo guardado; sus eventos y `horas_recurso` quedan como hashes sin etiqueta (aceptable, ya es la regla
  de los recursos). `copia_sin_ips` ya excluye `recursos` [V :179]; `horas_recurso` sin texto puede quedarse en la copia.

## 6. fail2ban y el servidor del dueño

### H22 (M) Presupuesto de conexiones de un alta
- Recuento con el diseño actual: keyscan 3 + `mktemp` 1 + `scp` 1 + `sudo` 1 + prueba como `infobar` 1 = **7** conexiones en ~30 s; con H8, 5. Con
  `MaxStartups 10:30:100` por defecto no hay problema [S alta]. En `mode = normal` solo cuentan autenticaciones fallidas [V filtro]; en
  `ddos`/`aggressive` cuentan también las `[preauth]` de keyscan → una sola conexión de keyscan.

### H23 (A) Dos altas con contraseña mala = IP del dueño bloqueada
- Evidencia [V filtro sshd de fail2ban + man ssh_config]: `Failed password for ...` cuenta siempre; con 3 prompts por conexión, una contraseña mal
  escrita son 3 fallos; `maxretry` por defecto 5 en `findtime` 10 min → el segundo intento bloquea la IP del Mac (también para el `ssh` del dueño y
  para el monitor, que entonces marca el servidor en rojo). Los `Failed publickey` de un agente con muchas claves son «NOFAIL» para usuarios válidos
  [V], pero cuentan para usuario inválido (p. ej. prueba como `infobar` si `useradd` falló).
- Cambio: `NumberOfPasswordPrompts=1` y `PubkeyAuthentication=no` en el alta por contraseña; tras **un** fallo de autenticación, bloquear el alta para
  ese host 10 min (no 30 s) y como mucho 3 altas por host y hora; mensaje: «contraseña rechazada; para no bloquear tu IP, espera 10 min». No lanzar
  la prueba como `infobar` si el paso de instalación no terminó con «Listo».

---

## Cambios al diseño, por orden
1. 3.7a sin método 2 (H0); si se mantiene, pty en vez de FIFO/askpass, un prompt, opciones de `orden_ssh`, bloqueo 10 min tras fallo (H1, H2, H6, H23).
2. Quitar «bytearray a ceros» del diseño; describir el ciclo de vida real de la contraseña (H3). Campo sin `<form>`, retirado del DOM, aviso (H4).
3. TOFU: `ssh-keygen -F` primero; keyscan de un tipo; mismo `known_hosts` que el monitor; sin botón de reemplazo; huella con la orden de consola (H7, H8).
4. Id de alta en las respuestas; `/estado` con conjunto cerrado (H5). Clave pública con comentario fijo y validada en el Mac, o por stdin (H10).
5. Proponer solo dominios que resuelven a la IP del servidor; NAT64 rechazado; `xn--` visible (H13, H14).
6. Sondeador de webs propio con presupuesto global y canario de red (H15, H19). Presupuesto por clave con reserva para el primer `cayo` (H18).
7. Migración 5 sobre `recursos` y `eventos`, con test de datos (H20). Borrado inmediato al desmarcar (H21). Patrón y glob de Traefik (H16).

## Supuestos
- [S alta] Chrome/Safari ofrecen guardar contraseñas ignorando `autocomplete=off`; verificar a mano en el Mac.
- [S alta] `UpdateHostKeys` aprende el resto de tipos tras autenticar; si no, añadir con keyscan los tres tipos pero mostrar y pedir confirmación
  solo de ed25519 (sigue siendo una conexión por tipo).
- [S media] El Python que ejecuta la barra tiene hardened runtime; si es de Homebrew, (b) puede leer la memoria del proceso con `lldb`.
- [S] Rutas de Traefik de EasyPanel; `MaxStartups` por defecto; `sudo -S` con EOF falla sin colgarse.
- El dueño es el único usuario del Mac (memoria del proyecto: instalación en `~/.cost-guard`, volumen EVO990 sin propiedad por usuario: ahí 0700 no
  separa nada, otro motivo para no apoyarse en permisos de directorio para el FIFO).

Status: DONE_WITH_CONCERNS
Summary: El diseño está bien orientado (puerta del panel sólida, SSRF cerrado por IP fijada, claves por hash) pero el método contraseña con FIFO + askpass es inseguro y frágil tal cual, el TOFU necesita apoyarse en `known_hosts` existente y un solo tipo de clave, y la detección debe filtrar por «resuelve a la IP del servidor». Quedan por verificar a mano cuatro supuestos (gestor de contraseñas del navegador, `UpdateHostKeys`, hardened runtime del Python de la barra y rutas de EasyPanel).
