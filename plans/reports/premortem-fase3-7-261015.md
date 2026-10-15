# Pre-mortem — Fase 3.7 (alta guiada de servidores y seguimiento de webs)

Fecha: 2026-10-15 · Rama: `feat/fase1-ingesta-hermes` · Objeto: `plans/261013-2350-sqlite-historico/fase3-7-alta-y-webs-diseno.md` (diseño v1).
Revisión de solo lectura: no se ha editado código ni ejecutado nada contra servidores ni con red.
Leyenda: **[V]** comprobado leyendo el código o el diseño (con `fichero:línea`) · **[S]** supuesto razonable sobre comportamiento externo (OpenSSH, sudo, Traefik, Cloudflare, distros) no comprobable sin red.

## Escenario del pre-mortem

Tres semanas después de publicar la 3.7, el dueño tiene esto:

- Dio de alta un servidor desde Ajustes y, al rato, pulsó «Guardar ajustes» en la misma pestaña para cambiar un intervalo. El servidor nuevo desapareció de la configuración y, en el mantenimiento siguiente, su historial se retiró.
- En el servidor de un cliente, el alta se quedó a medias: se crearon el usuario `infobar` y la regla de sudoers, pero la prueba final falló por `AllowUsers` y la página solo dijo «la prueba como infobar falló».
- Para dos servidores bien bastionados (sin contraseña por SSH y con sudo que pide contraseña, justo lo que recomienda la propia app) ningún método funcionó.
- Al reintentar, fail2ban bloqueó la IP de la oficina durante una hora, también para su SSH de siempre.
- Con 60 webs aceptadas, cada vez que el portátil despertaba sin red todas las webs pasaban a «cayó». Los presupuestos de eventos del día se agotaban por la mañana y el icono estuvo rojo casi siempre.
- El servidor con más webs salía como «timeout» en cada ciclo, con lo que se perdían justo sus datos de SSH. Además, los hilos huérfanos del ciclo se iban acumulando.
- Una web con autenticación básica en Traefik y otra detrás de Cloudflare con protección contra bots salieron «arriba» todo un día con el backend caído.
- La migración 5 cambió la tabla `recursos`, pero no la de `eventos`, que también tiene un `CHECK` sobre `tipo`. El primer evento `cayo` violó el `CHECK` y el `SAVEPOINT` lo descartó en silencio, en cada lectura.

Los hallazgos de abajo explican por qué pasa cada una de estas cosas.

---

## CRÍTICOS

### C1. Escritura perdida: el alta guarda el servidor y la página de Ajustes lo borra al guardar con su copia vieja [V]
- **Evidencia:** la página guarda `A.config` entero (`panel_assets/ajustes.js:237-242`), con la lista `servidores` cargada al abrir Ajustes (`ajustes.js:218-229`). En el servidor, `servidores` pasa tal cual (`infobar/ajustes.py:51`, `:138-140`), y `_guarda_servidores` hace `nuevo.update(parche)`, que sustituye la lista completa (`infobar/config_y_estado.py:128-136`). El diseño guarda el servidor desde el alta, en paralelo y en el lado del servidor (diseño §A.4, paso final).
- **Escenario:** el alta termina con éxito y escribe `servidores.json`. Sin recargar, el usuario pulsa «Guardar ajustes» por cualquier otro motivo y envía la lista sin el servidor nuevo, que desaparece. `mantener()` retira los servidores que ya no están en la configuración y les borra las IPs (`servidores_historico.py:371-373`). Si luego se vuelve a añadir, `asegurar_ids` ya no tiene de quién heredar el id (`servidores_registro.py:22-40`) y el historial queda partido. Lo mismo pasa al aceptar webs desde la página y al tener dos procesos (barra y `--demonio`), porque `_EN_MARCHA` y el estado del alta viven en la memoria de cada proceso.
- **Cambio propuesto:** el alta, la retirada y la aceptación de webs tienen que ser **operaciones por servidor** (añadir, quitar o parchear por `id`) dentro del cerrojo de `servidores.json`, nunca una sustitución de la lista entera. Además, `/guardar` debe aplicar la lista con control de versión: la página manda la `generado_en` o un hash de la lista que leyó, y el servidor responde 409 si ha cambiado. Al terminar el alta, la página recarga `/ajustes`.

### C2. La contraseña puede acabar en disco por `/guardar`: «contrasena» no figura entre los nombres secretos y los servidores aceptan cualquier campo [V + S]
- **Evidencia:** `SECRETOS` contiene `password` y `clave`, pero no `contrasena`, `contraseña` ni `passphrase` (`infobar/servidor_local.py:53-54`). `errores_servidores` no rechaza campos desconocidos (`infobar/servidores.py:70-114`). `guardar_config(sin_secretos(...))` (`servidor_local.py:381`) dejaría pasar `contrasena`.
- **Escenario [S]:** si el formulario de alta se pinta como una fila más de `servidores` (el patrón actual de `ajustes.js:77-84` y `:176`), basta con que `leer()` recoja `data-k="contrasena"` para que «Guardar ajustes» escriba la contraseña en claro en `servidores.json` (0600, pero con copias `.bak`).
- **Cambio propuesto:** añadir `contrasena`, `contraseña`, `passphrase` y `pass` a `SECRETOS`. Hacer que `errores_servidores` acepte solo una lista cerrada de campos. Poner el formulario de alta fuera de `A.config`, con su propio estado y sin `data-k`. Añadir un test que envíe a `/guardar` un servidor con `contrasena` y compruebe que se rechaza.

### C3. Con muchas webs, el ciclo de servidores se rompe: la medida es secuencial y va dentro del tope de 32 s por tanda, y quedan hilos huérfanos [V]
- **Evidencia:** `recoger_uno` mide las webs **una tras otra** dentro de la lectura SSH (`servidores_ciclo.py:74`, con el tope `[:4]` de hoy). Cada medida puede tardar 5 s de conexión, más un DNS sin tope (`servidores_externo.py:12`, `:20`). La tanda tiene un tope de `ceil(N/4)*32` s (`servidores_ciclo.py:29`, `:128`). Lo que no termina se marca `timeout` y se descarta **también la lectura SSH** (`:135-138`). Los hilos en marcha siguen vivos tras `shutdown(wait=False)` (`:141`). Todo corre en el único hilo trabajador de la barra (`trabajador_barra.py:79`).
- **Escenario:** un servidor con 100 webs y el backend caído con los SYN descartados da 100 × 5 s / 4 = 125 s, aunque haya 4 sondeos en paralelo. El servidor sale como «timeout» y en rojo, y se pierden CPU, contenedores y eventos justo durante la incidencia. Como el estado vivo es rojo, el modo incidencia relanza la lectura cada 60 s (`:53-59`, `:101`) mientras la anterior sigue viva, y los hilos huérfanos se acumulan. Hoy ya puede pasar el peor caso: 20 s de SSH + 4×5 s de webs + 5 s de `ssh -G` + 6 s de puertos = 51 s, por encima de 32 s.
- **Cambio propuesto:** sacar las webs del ciclo SSH. Un **programador de sondeos aparte**, con su hilo propio, la próxima hora de cada web, una concurrencia global fija (4–8), presupuesto por vuelta, `getaddrinfo` con tope (en un ejecutor con timeout) y una caché propia que el ciclo SSH solo lee. El ciclo SSH no debe esperar nunca a las webs.

### C4. La «máquina de estados» que se quiere reutilizar no sirve para una web: el cambio de estado no se confirma y las ventanas son de 10 min [V]
- **Evidencia:** en `anota_inventario`, la confirmación con dos lecturas y `ESPERA_S=600` solo se aplica a **aparecer o desaparecer** (`servidores_recursos.py:16`, `:123-128`, `:151-156`). `cambio_estado` se anota **en la primera lectura distinta**, sin histéresis (`:131-132`). La desaparición se deduce de la ausencia en un inventario completo. Las webs vienen de la configuración, así que quitar una web acabaría generando un `desaparecio`, y una web recién aceptada en un servidor que ya tiene línea base genera un `aparecio` a los 10 min (`:102-119`, `:123-128`). Además, la tarjeta y el icono cambian en el acto (`servidores_estado.py:37-40`): la histéresis del diseño solo afectaría a los eventos.
- **Escenario:** un fallo puntual pone el servidor en rojo y lo mete en modo incidencia. El evento `cambio_estado` se anota sin confirmar. «Dos lecturas seguidas (≥ 60 s)» no tiene equivalente en el código.
- **Cambio propuesto:** una máquina pequeña y propia por web: `estado_confirmado`, `candidato`, `n` y `desde`. Una transición se confirma con N lecturas consecutivas **sin hueco mayor que 2× el intervalo**; si el hueco es mayor (Mac dormido), se reinicia. El color de la tarjeta debe salir del estado **confirmado**, no de la última lectura. La ausencia en la configuración no genera ningún evento: es una baja.

### C5. Mac sin red o recién despertado: todas las webs de todos los servidores se dan por «caídas» [V mecanismo + S frecuencia]
- **Evidencia:** cualquier `error` cuenta como caída (`servidores_estado.py:38`, `servidores_datos.py:120`). No hay ninguna sonda de control que distinga «la web cae» de «este Mac no tiene red». El SSH falla con «red», que no está en `ERRORES_SIN_REINTENTO` (`servidores_ciclo.py:25`), así que se reintenta cada 60 s.
- **Escenario:** el portátil despierta en un hotel con portal cautivo o con la VPN levantándose. Dos lecturas fallidas seguidas confirman `cayo` para cientos de webs. El presupuesto `EVENTOS_DIA_POR_TIPO=100` por servidor, evento y tipo (`servidores_recursos.py:18`, `:51-54`) se agota y los eventos reales de ese día se pierden. Lo mismo pasa con un `getaddrinfo` que devuelve primero IPv6 en una red con IPv6 roto, porque solo se prueba `ips[0]` (`servidores_externo.py:26`), y con el DNS filtrado del Mac (Pi-hole o NextDNS devuelven `0.0.0.0`): «resuelve a una IP local o privada» acaba contando como caída (`:24-25`).
- **Cambio propuesto:** que la ronda de webs valga solo si una sonda de control (la resolución y conexión de un host neutro, o la propia conexión SSH) va bien. Si casi todas las webs fallan a la vez, se anota «sin dato desde este Mac», nunca caída. Separar los motivos «no medible desde aquí» (DNS privado o filtrado) y «caída». Probar IPv4 e IPv6 al estilo *happy eyeballs* o, al menos, todas las IPs antes de declarar el fallo.

---

## ALTOS

### A1. El método 1 (mi clave) no sirve en un servidor que sigue los consejos de la propia app; el método 2 tampoco [V + S]
- **Evidencia:** la app marca como fallo el sudo sin contraseña (`servidores_estado.py:117-122`) y como **crítico** el SSH con contraseña (`:83-85`, `:137`). El método 1 usa `BatchMode=yes` «sin contraseña» (diseño §A, método 1), pero `instalar-en-servidor.sh` necesita root (`instalar-en-servidor.sh:17`) y el script actual pide la contraseña de sudo por tty (`alta-servidor.sh:32`). El método 2 da por hecho que la contraseña de entrada y la de sudo son la misma.
- **Escenario:** en un servidor bastionado (entrada por clave y sudo con contraseña), el método 1 falla en el sudo y el método 2 falla porque el servidor no admite contraseña. En Windows (sin método 2, y con el método 3 dependiendo de un script bash) no queda **ningún** camino.
- **Cambio propuesto:** añadir el método «mi clave + contraseña de sudo», que debería ser el **principal**, con un campo opcional de contraseña de sudo distinta de la de entrada. Si entra root (uid 0), no usar sudo; antes, comprobar `id -u` y si hay `sudo`, todo en la misma sesión. Ofrecer un método 3 para Windows: un `.ps1`, o el bloque que hay que pegar en la consola web del proveedor.

### A2. Demasiadas conexiones y demasiados intentos de contraseña: fail2ban o faillock bloquean al propio dueño [V + S]
- **Evidencia:** el flujo de `alta-servidor.sh` abre **4 conexiones** (`mktemp` en `:26`, `scp` en `:29`, `sudo` en `:32` y la prueba en `:37`), tres de ellas con autenticación. El diseño añade `ssh-keyscan` [S: una conexión por tipo de clave, 3 tipos] y el botón «Comprobar». OpenSSH prueba la contraseña hasta 3 veces por conexión (`NumberOfPasswordPrompts`) [S], y antes ofrece las claves del agente [S].
- **Escenario:** una contraseña mal escrita da 3 fallos. El enfriamiento de 30 s no impide un segundo intento: 6 fallos, por encima de `maxretry=5` [S]. El bloqueo afecta a la IP del dueño (o a la NAT de la oficina) y a la monitorización de ese servidor. En RHEL, `pam_faillock` cuenta también los fallos de `sudo` y puede **bloquear la cuenta** del dueño [S].
- **Cambio propuesto:** **una sola conexión autenticada** para todo el alta: una única sesión `ssh` que reciba por stdin la contraseña de sudo y, a continuación, los tres scripts empaquetados (base64 o tar), sin `mktemp` ni `scp`. Así se evita además que el `scp` moderno use SFTP [S] y falle en servidores sin el subsistema `sftp`. Usar `-o NumberOfPasswordPrompts=1 -o PreferredAuthentications=password -o PubkeyAuthentication=no` en el método 2. Enfriamiento creciente (30 s, 5 min, 15 min) y aviso explícito: «otro fallo puede hacer que fail2ban bloquee tu IP».

### A3. El servidor queda «a medias» sin diagnóstico: los avisos de `instalar` se pierden y hay casos que ni se avisan [V + S]
- **Evidencia:** `instalar-en-servidor.sh` solo **escribe en pantalla** un aviso para `AllowUsers` y `AllowGroups` (`:56-60`), y el diseño reduce la salida a un conjunto cerrado de mensajes (§A.5). No se comprueban `DenyUsers`, `Match User/Group`, `AuthenticationMethods` (2FA para todos), un `AuthorizedKeysFile` distinto del habitual ni SELinux: `.ssh` se crea con `install -d` sin `restorecon` (`:47-53`) [S: con SELinux en modo estricto, sshd no lee `authorized_keys` sin la etiqueta `ssh_home_t`]. No existe un script de desinstalación (`infobar/remoto/` no lo trae).
- **Escenario:** el usuario, sudoers y los scripts quedan instalados, la prueba como `infobar` falla y la página dice «la prueba como infobar falló». El dueño no sabe qué tocar ni cómo deshacerlo.
- **Cambio propuesto:** que `instalar` termine con unas **marcas de diagnóstico** legibles por la máquina (`aviso allowusers`, `aviso match`, `aviso authmethods`, `aviso authorizedkeysfile`, `aviso selinux`, `sin_sudo`, `sin_visudo`), sin valores, que el Mac traduzca a mensajes cerrados y concretos. Ejecutar `restorecon -R /home/infobar/.ssh` si existe. Añadir `desinstalar-de-servidor.sh` y enlazarlo desde el mensaje de error. Hacer una **comprobación previa sin modificar nada** (`sshd -T` y `/etc/os-release`) y abortar antes de tocar el servidor si la combinación no está soportada.

### A4. Distros y entornos no soportados se descubren a mitad del alta [V + S]
- **Evidencia:** los scripts dependen de `bash`, `useradd`, `visudo`, `journalctl`, `apt` y `df --output` (`instalar-en-servidor.sh:24`, `:41`; `recoger.sh:30`, `:54`, `:104-113`). La orden SSH desactiva `ProxyCommand` y, con ello, `ProxyJump`, porque gana la primera opción [S] (`servidores_ssh.py:34`). `_SSH` no admite IPv6 literal (`servidores.py:11`).
- **Escenario:** en Alpine no hay bash ni `useradd` [S]. En una Debian mínima sin sudo, `visudo` falla con `set -e` después de haber creado el usuario y los scripts. En RHEL, las comprobaciones de `apt` dan «parches automáticos: no» aunque no sea cierto [S]. Un servidor detrás de un bastión no se puede ni dar de alta ni monitorizar, y el mensaje será «red» o «autenticación».
- **Cambio propuesto:** declarar en el diseño qué está soportado (Debian/Ubuntu con systemd y sudo; RHEL como «parcial»; Alpine, dropbear y bastión no soportados). Detectarlo **antes** de escribir nada (`ssh -G` para `proxyjump`/`proxycommand` en local, y `/etc/os-release` y `command -v` en el servidor) con mensajes cerrados del tipo «este servidor usa un bastión: aún no está soportado».

### A5. Contraseña en memoria mientras se compara la huella; el askpass por FIFO puede bloquearse [V diseño + S OpenSSH]
- **Evidencia:** la contraseña llega en `iniciar` (paso 1) y la huella se confirma después (paso 2), así que la contraseña espera en memoria mientras el usuario compara, o indefinidamente si cierra la pestaña. Abrir un FIFO para escribir **se bloquea** hasta que haya un lector, y viceversa. El diseño escribe la contraseña «una vez» (§A.3).
- **Escenario:** (a) `ssh` no llega a llamar al askpass porque falla el host o entra con una clave del agente: el hilo que escribe se queda bloqueado para siempre con la contraseña en memoria. (b) `ssh` vuelve a preguntar (contraseña errónea o 2FA del tipo «Verification code:»): el segundo askpass se bloquea leyendo el FIFO y el alta se cuelga hasta el tope de 90 s. Si escribiera la contraseña en el aviso del código 2FA, la estaría mandando a donde no toca. (c) `SSH_ASKPASS_REQUIRE` necesita OpenSSH ≥ 8.4 [S]: el cliente de Ubuntu 20.04 (8.2) y el de macOS 11 [S] lo ignoran y `ssh` intentaría leer de `/dev/tty`.
- **Cambio propuesto:** el orden debe ser TOFU **antes** de pedir la contraseña (la huella no la necesita), y la contraseña se envía en un segundo `POST` ya con el host confirmado. El tope de 90 s debe empezar a contar después de confirmar la huella. Abrir el FIFO con `O_NONBLOCK` y un tiempo máximo, y que el askpass compruebe el texto del aviso (solo `password:`; con cualquier otro aviso sale con error y el mensaje «el servidor pide un segundo factor: usa el método 3»). Comprobar `ssh -V` ≥ 8.4 al ofrecer el método 2. Documentar con honestidad que poner a ceros el `bytearray` no borra las copias inmutables (`bytes` del cuerpo y `str` de `json.loads`, en `servidor_local.py:356`): hay que reducir su vida al mínimo, no prometer que se borran.

### A6. Una web caída mete al servidor en incidencia permanente y el icono acaba rojo siempre [V]
- **Evidencia:** el estado vivo incluye la tarjeta `webs` (solo se excluyen `mantenimiento` y `certificado`, `servidores_ciclo.py:85`). La tarjeta se pone en rojo con **una sola** web mala (`servidores_estado.py:38-40`). En incidencia se lee el servidor entero cada 60 s (`servidores_ciclo.py:53-59`).
- **Escenario:** con 100 webs, siempre hay alguna web abandonada de un cliente que da 5xx. El servidor queda en rojo y en incidencia para siempre: SSH cada 60 s y, con la arquitectura actual, las 100 webs cada 60 s. El rojo deja de significar algo.
- **Cambio propuesto:** que el intervalo de 60 s se aplique **solo a la web caída** (programador propio, C3) y que las webs no aceleren el SSH. La tarjeta debe distinguir «n de m webs caídas». Permitir silenciar o reconocer una web caída y que deje de pintar el servidor en rojo hasta que vuelva o pase una ventana de tiempo.

### A7. Las clases HTTP del diseño dan falsos «arriba» de forma sistemática [S comportamiento externo; V clasificación]
- **Evidencia:** 200–399 cuenta como arriba y 400–499 como «viva» (diseño §C). No se siguen las redirecciones (`servidores_externo.py:65-66`). Hay una sola URL por dominio (la raíz).
- **Escenarios [S]:**
  - Si se detecta `http`, Traefik responde 301/308 desde el middleware de redirección **sin consultar el backend**, así que la web sale siempre «arriba».
  - La autenticación básica, ForwardAuth o Cloudflare Access en el proxy dan 401, 403 o 302 antes de llegar al backend: siempre «viva».
  - El Bot Fight Mode o los desafíos de Cloudflare a un `User-Agent: infobar/1.0` dan 403, o 503 en el modo antiguo, que se cuenta como caída: falso positivo.
  - El 404 de Traefik por «router inexistente» (aplicación borrada o mal desplegada) sale como «viva».
  - Un dominio enrutado solo con `PathPrefix(/api)` da 404 en `/` aunque el backend esté muerto.
  - 429 (límite de peticiones): «mal».
- **Cambio propuesto:** medir siempre en **https** (coherente con el plan, `fase3-plan-monitorizacion.md:28`, y con D-D). Si la URL es http, aceptar el 3xx solo como «proxy vivo, aplicación sin comprobar». Distinguir el 404 de Traefik (cuerpo `404 page not found` en el GET de respaldo) y tratarlo como «sin aplicación». Permitir por web una **ruta y un código esperados** (por ejemplo `/health` → 200) y avisar en Ajustes de que con 401/403 la app solo comprueba que el proxy responde. Para 403/503 con cabeceras de Cloudflare (`cf-ray`, `server: cloudflare`), poner el rótulo «protegida por CDN: resultado no concluyente», no caída.

### A8. La migración 5 se queda corta: `eventos` también tiene `CHECK` sobre `tipo` y `evento`; la copia previa no guarda `recursos` ni `eventos` [V]
- **Evidencia:** `eventos.tipo CHECK IN ('contenedor','compose','servicio')` y `eventos.evento CHECK IN (...)` (`servidores_historico.py:97-98`). El diseño solo reconstruye `recursos` (§C, «Almacén»). La copia previa a la migración **vacía** `recursos` y `eventos` (`:179-181`). Las migraciones se hacen en una sola transacción con `foreign_keys=ON` (`:148-161`, `sqlite_base.py:53`), y `PRAGMA foreign_keys` no se puede cambiar dentro de una transacción. Los fallos del inventario se tragan con `ROLLBACK TO` + `descartar` (`servidores_historico.py:348-355`).
- **Escenario:** si no se reconstruye `eventos`, cada `cayo` o `volvio` viola el `CHECK`. El `SAVEPOINT` lo descarta en silencio y solo queda un contador `descartes:inventario_error`. Si la reconstrucción de `recursos` se equivoca (orden de columnas en el `INSERT … SELECT`, índice `eventos_por_clave` sin recrear, perder `STRICT` o `WITHOUT ROWID`), no hay vuelta atrás: la copia previa no tiene esas tablas. Una vez en `user_version=5`, volver a la versión anterior de la app deja el historial inservible (`servidores_historico.py:142-143`).
- **Cambio propuesto:** reconstruir **ambas tablas** con el procedimiento de 12 pasos de SQLite. Al final, `PRAGMA foreign_key_check` e `integrity_check` antes de `COMMIT`, y `COUNT(*)` antes y después comparado. Recrear los índices. Test de migración con una base v4 **poblada**, con contenedores, servicios y eventos de los tres tipos. Valorar si las webs necesitan de verdad una tabla aparte (`webs` y `eventos_web`), que evita reconstruir nada. Documentar que el retroceso a la versión anterior exige restaurar la copia y que se pierden recursos y eventos.

---

## MEDIOS

### M1. La detección de dominios es incoherente con su propio filtro y se deja casos habituales [V diseño + S proxies]
- **Evidencia:** solo sale lo que pasa por `grep -o 'Host\(`[a-z0-9.-]+`\)'` (diseño §B), pero el esquema se quiere deducir de `websecure` o `tls` del **mismo router**, un dato que ese filtro elimina: no se puede hacer tal como está escrito.
- **No detecta [S]:**
  - mayúsculas en la etiqueta;
  - `Host(`a`,`b`)` (sintaxis de Traefik v2 con varios argumentos);
  - `HostRegexp` y los comodines (que no avise de ello también es un fallo);
  - Coolify con Caddy (etiquetas `caddy_*`);
  - nginx o Caddy fuera de Docker;
  - routers de Traefik definidos en la configuración estática o en otro directorio.
- **Detecta de más:** dominios autogenerados (`*.traefik.me`, `*.sslip.io`), routers de aplicaciones borradas que siguen en `dynamic/` [S] y routers http de redirección.
- **Cambio propuesto:** no deducir el esquema (medir https). Normalizar a minúsculas antes de filtrar. Aceptar varios argumentos en `Host(...)`. Declarar en la salida `comodines N` y `otros_proxies si/no` para que la página diga «hay N reglas con comodín que no se pueden vigilar». Marcar como «autogenerado» lo que siga patrones conocidos. Leer los ficheros con `find -maxdepth 1 -type f -size -1M` y `timeout`: un contenedor hostil que monte `dynamic/` podría dejar ahí un FIFO o un enlace a `/dev/zero` [S].

### M2. La detección en cada ciclo cuesta tiempo de root y pone en riesgo las secciones de seguridad [V]
- **Evidencia:** `recoger-root` tiene `LIMITE=7` y el inventario solo arranca antes de 5 s (`recoger-root.sh:12`, `:71`). `recoger.sh` solo le da el resto si quedan ≥ 4 s (`recoger.sh:119-122`). Con `omitidos > 0`, el Mac descarta **todas** las secciones de root (`servidores_ssh.py:155-161`). `command=` y sudoers sin argumentos impiden pedir «solo webs» bajo demanda (`instalar-en-servidor.sh:40`, `:49`).
- **Escenario:** si la sección `webs` usa `t()` y se pasa de tiempo, se pierden fail2ban, el bastionado y el inventario. Si tiene presupuesto propio, en un servidor cargado (justo en una incidencia) casi siempre saldrá `fuente error`. En los dos casos, cada 5 min se hace un `docker service inspect` de hasta 300 servicios para obtener un dato que cambia una vez por despliegue.
- **Cambio propuesto:** que la detección corra **como mucho una vez al día** o cuando cambie la huella del inventario: el script la emite solo si un sello en `/var/lib/infobar` lo permite. Que sea la **última** sección de root, con presupuesto propio y sin `t()`. Que su fallo no cuente en `omitidos`.

### M3. Huella TOFU: lo que hará el usuario de verdad, y cómo se usan `known_hosts` y los alias [V + S]
- **Evidencia:** la monitorización usa el `known_hosts` por defecto con `StrictHostKeyChecking=yes` (`servidores_ssh.py:33`). `_SSH` admite alias de `~/.ssh/config` (`servidores.py:11`).
- **Escenario [S]:** casi ningún proveedor muestra la huella en su panel, así que el usuario pulsará «Es esta» sin comprobar nada (lo mismo que escribir `yes`). `ssh-keyscan` no resuelve los alias (necesita el `hostname` de `ssh -G`) ni usa `[host]:puerto` si no se le pasa `-p`. Si la app añade claves de tres conexiones distintas y solo muestra una huella, un atacante que intercepte solo una de ellas cuela su clave. Con `HashKnownHosts`, `UserKnownHostsFile` o `HostKeyAlias` en la configuración del usuario, la app puede escribir en un fichero que `ssh` no consulta. Tras reinstalar un VPS con la misma IP, el alta se aborta y el dueño no sabe por qué.
- **Cambio propuesto:** guardar **solo** la clave del algoritmo que `ssh` va a negociar (ed25519) y mostrar su huella. Resolver con `ssh -G` antes del escaneo. Explicar cómo comprobarla («en la consola web de tu proveedor: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`»). Para la huella cambiada, mensaje cerrado con el paso a seguir (`ssh-keygen -R '[host]:puerto'` si de verdad se ha reinstalado). Decidir y documentar un único `known_hosts`, el mismo para el alta y para la monitorización.

### M4. `sudo -S` en casos reales [S]
- Con `NOPASSWD`, sudo no lee stdin y la contraseña queda disponible para el siguiente proceso que lo lea. Con `Defaults requiretty` (CentOS 7) falla con «you must have a tty». Si el usuario no está en sudoers, el fallo queda registrado en `auth.log`. Si sudo no está instalado (Debian mínima con root), falla.
- **Cambio propuesto:** probar antes con `sudo -n true` (sin contraseña) y solo después con `-S`. Si es root, no usar sudo. Cerrar stdin justo después de la contraseña (marco de longitud fija). Mensajes cerrados: «el usuario no tiene sudo», «sudo no está instalado», «sudo exige terminal».

### M5. Windows y modo `--demonio` [V + S]
- **Evidencia:** en Windows el panel y el trabajador corren en `costbar.py --demonio` (`costbar.py:61-70`). `CLAVE` es `~/.ssh/infobar_ed25519` (`servidores_ssh.py:12`).
- **Escenario [S]:** como servicio o tarea programada con otra cuenta, `~` es el perfil del servicio. La clave y `known_hosts` quedan donde el usuario no los ve, y su `ssh` habitual no ha guardado ahí la huella. El método 3 da una línea de bash que no funciona en PowerShell. Si conviven la barra y el demonio (o una instancia que se está cerrando), hay dos ciclos y dos escritores, y el «un alta a la vez» solo se cumple dentro de cada proceso.
- **Cambio propuesto:** documentar que en Windows el demonio debe ejecutarse con la cuenta del usuario. Añadir un método 3 para Windows (`.ps1`, o el bloque para la consola del proveedor). Un cerrojo de fichero para «alta en curso» (`plataforma.bloqueo_de_fichero`) en lugar de un cerrojo en memoria.

### M6. Volumen de `horas_recurso` y seudonimización reversible de los dominios [V + S]
- **Evidencia:** la tabla es permanente (diseño §C). La clave es `sha256(sal, tipo, nombre)[:32]` y la sal se guarda en la misma base (`servidores_recursos.py:34-44`).
- **Escenario:** 100 webs × 24 h × 365 días = **876.000 filas al año por servidor** (≈ 60–90 MB al año [S], sin contar índices). Con 10 servidores, del orden de 1 GB al año, con `secure_delete=ON` y un `VACUUM` cada 30 días. Los dominios son enumerables (registros públicos de certificados), así que con la sal en la base el hash se invierte por diccionario: borrar la etiqueta no anonimiza.
- **Cambio propuesto:** consolidar a días pasados 90 días y guardar horas solo de las horas con algún fallo. Usar un id entero local por recurso (tabla puente) en lugar de 32 caracteres por fila. Que «olvidar» y «retirar» borren también `horas_recurso` y las webs detectadas de la caché (hoy `podar_nombres` no conoce las webs, `servidores_ciclo.py:169-182`). Documentar que la clave es seudónima, no anónima.

### M7. El presupuesto de eventos y el de recursos se comparten con los contenedores [V]
- **Evidencia:** `MAX_RECURSOS=500` por servidor con expulsión de inactivos (`servidores_recursos.py:17`, `:66-79`). 10 eventos por clave y día, y luego un único `inestable` (`:19`, `:55-60`).
- **Escenario:** una web que parpadea agota sus 10 eventos y el último `volvio` del día no se anota, así que el historial la deja «caída». Con 100 webs y 400 contenedores, las webs inactivas pueden expulsar recursos o ser expulsadas.
- **Cambio propuesto:** presupuestos propios para el tipo `web`. El último cambio del día siempre se anota (sustituye al anterior si no cabe). Tope de recursos separado.

### M8. Incoherencias internas del diseño [V]
- El TLS inválido cuenta como «caída» (§C), pero el certificado ya tiene tarjeta propia y no cuenta en el estado vivo (commit 18d13d6, `servidores_estado.py:41-47`, `servidores_ciclo.py:85`).
- Las clases son «arriba / error de cliente / caída», pero los estados son `ok / mal (4xx o lenta) / caido`. `mal` está en `_MAL` y genera avisos (`servidores_recursos.py:25`, `:132`), así que una web con 401 queda en aviso para siempre. «Lenta» no tiene umbral (el actual es 1500 ms, `servidores_estado.py:39`).
- `empezo_5xx` se solapa con `cayo`, porque el 5xx ya es caída.
- «Dos lecturas ≥ 60 s»: con el despertar cada 15 s y la medida al final de la lectura SSH, dos lecturas pueden quedar a menos de 60 s (`servidores_ciclo.py:101`, `trabajador_barra.py:17`). Mejor exigir N lecturas que una separación mínima.
- `cert_dias_min` solo se guarda si **todas** las webs https dieron fecha (`servidores_datos.py:76-79`): con 100 webs casi nunca se guardará.
- Con 100 webs ya no se cumple el tope de 4 (`servidores_ciclo.py:74`).
- La etiqueta tiene ≤ 130 caracteres (`servidores_historico.py:84`) frente a dominios de ≤ 253.

### M9. Dominios que apuntan a otro servidor o a una CDN [S]
- Un router que sobra en el servidor A, con el DNS ya en el servidor B, mide B y se lo atribuye a A. El mismo dominio puede estar en dos servidores (staging y producción, o una migración).
- **Cambio propuesto:** al aceptar una web, comparar las IPs resueltas con el host del servidor (`host_de_ssh`) y mostrarlo: «apunta a este servidor», «pasa por CDN» u «apunta a otro sitio». Avisar de los duplicados entre servidores.

---

## BAJOS

- **B1 [V]** El diseño contradice el no objetivo del plan general («actuar sobre los servidores», `fase3-plan-monitorizacion.md:21-22`). Hay que dejar escrito que el alta es una excepción aceptada por el dueño.
- **B2 [V + S]** Leer la configuración del proxy de un **cliente** amplía los datos que salen de su servidor (su lista de dominios). Con D-E («mismos privilegios»), conviene la misma regla que para `recoger-bd`: autorización del cliente o una casilla por servidor.
- **B3 [S]** `autocomplete=off` no impide que el navegador ofrezca guardar el campo de contraseña: usar `autocomplete="new-password"`. Las extensiones con acceso a la página pueden leerlo; hay que decirlo en el aviso del método 2.
- **B4 [V]** El sondeo cada segundo pasa por `ThreadingTCPServer` con un hilo por petición y HTTP/1.0 (`servidor_local.py:410-412`). Es asumible, pero `GET /servidores/alta/estado` **no** debe compartir el semáforo `historiando` (2 plazas, `:177`, `:276`), o el historial y el alta se responderán 429 el uno al otro. Si la barra se reinicia a mitad del alta, la sesión y el estado se pierden (401): la página debe decir «la app se reinició durante el alta; repetirla es seguro».
- **B5 [V]** El nombre del servidor y el host duplicados se comprueban solo al guardar (`servidores.py:85-86`), es decir, **después** de modificar el servidor remoto. Hay que comprobarlo en `iniciar`.
- **B6 [S]** Un `GET` con `Range` de respaldo hace trabajar a la aplicación del cliente (renderizado completo en muchos frameworks). Con 1 cada 5 min es asumible, pero mejor documentarlo.
- **B7 [V]** El texto de error de `medir_web` es `str(e)[:80]` (`servidores_externo.py:90`) y puede incluir la IP resuelta. Ahora que las webs pueden ser de clientes, conviene usar motivos cerrados.

---

## Qué ve el usuario cuando algo falla (resumen de los mensajes que faltan)

Hoy el diseño solo garantiza «paso N correcto/fallido». Cada fallo previsible necesita su mensaje cerrado **y su acción**:

| Situación | Acción que debe proponer el mensaje |
|---|---|
| Contraseña rechazada | Quedan X intentos antes de que fail2ban pueda bloquearte |
| El servidor no admite contraseña | Usa «mi clave + sudo» o el método 3 |
| Sudo pide otra contraseña, no hay sudo o sudo exige terminal | Rellena el campo de sudo, o usa el método 3 |
| Segundo factor (2FA) | Usa el método 3 |
| Bastión o ProxyJump | No soportado (todavía) |
| La huella ha cambiado | ¿Reinstalaste el servidor? Pasos para retirar la huella vieja |
| `AllowUsers`, `Match` o `AuthenticationMethods` impiden entrar a `infobar` | La línea exacta que hay que añadir |
| SELinux | `restorecon` |
| Distro no soportada | Se detecta antes de tocar nada |
| El servidor quedó con `infobar` instalado | Repetir es seguro; para quitarlo, `desinstalar-de-servidor.sh` |

Para las webs, las situaciones que hay que distinguir son: «sin dato desde este Mac (sin red o DNS filtrado)», «protegida por CDN o autenticación: no concluyente», «sin aplicación (404 del proxy)» y «caída confirmada (N lecturas)».

## Orden recomendado antes de implementar

1. C1, C2 y C3 (contrato de guardado por servidor, nombres secretos y programador de webs aparte): sin esto, ninguna entrega es segura.
2. A1, A2 y A5 (métodos de acceso realistas, una sola conexión autenticada, TOFU antes de la contraseña) en la 3.7a.
3. C4, C5, A6 y A7 (máquina propia por web, guardia de «Mac sin red», clases HTTP) en la 3.7c, antes de anotar ningún evento.
4. A8 (migración con las dos tablas, o tablas nuevas) y M6 (volumen y olvido) antes de la migración 5.

## Preguntas abiertas para el dueño

1. ¿Se acepta que el método principal sea «mi clave + contraseña de sudo», con el método 2 (contraseña de entrada) reservado para VPS recién creados?
2. ¿Se mide siempre por https (coherente con D-D) aunque Traefik también sirva http?
3. ¿Hace falta autorización del cliente para leer los dominios de sus servidores (D-E)?
4. ¿Las webs en tablas propias (`webs` y `eventos_web`) en lugar de ampliar `recursos` con una reconstrucción?
5. Distros soportadas en la 3.7: ¿solo Debian/Ubuntu con systemd y sudo?

Status: DONE_WITH_CONCERNS
Summary: Hay cinco fallos críticos que conviene resolver antes de implementar: (1) la página de Ajustes puede borrar el servidor recién dado de alta al guardar con su copia vieja; (2) la contraseña podría guardarse en `servidores.json` porque `contrasena` no está entre los nombres secretos; (3) con muchas webs, el ciclo SSH se pasa de tiempo, pierde los datos del servidor y acumula hilos; (4) la máquina de estados que se quiere reutilizar no confirma los cambios de estado de una web; y (5) un Mac sin red da falsas caídas masivas. A eso se suman altos de producto: los métodos de acceso no sirven en servidores bastionados, el alta hace demasiadas conexiones con contraseña (riesgo de fail2ban), el servidor puede quedar a medias sin diagnóstico, hay falsos «arriba» con 3xx, 401/403 y CDN, y la migración 5 deja fuera la tabla `eventos`.
