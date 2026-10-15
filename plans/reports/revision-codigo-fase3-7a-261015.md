# Revisión de código independiente — 3.7a alta guiada (commit f088b4c)

Fecha: 2026-10-15 · Alcance: `f088b4c` frente a su padre (`infobar/servidores_alta.py`, `servidores_ssh.py`, `servidor_local.py`, `panel_assets/ajustes.js`,
`remoto/desinstalar-del-servidor.sh`, README, `tests/test_servidores_alta.py`). Solo lectura; no se ha tocado red, servidores ni ssh real.
Leyenda: [V] verificado leyendo código o ejecutando en local · [S] supuesto (conocimiento de OpenSSH/sudo/fail2ban, sin ejecutarlo aquí).

## Resultado

No he encontrado nada crítico ni alto. El núcleo de seguridad aguanta: las entradas se validan con regex antes de llegar a `ssh`, el comando remoto tiene
comillas correctas y lo he ejecutado en local con `sh -c` y un tar falso, el directorio temporal es 0700 y la huella va antes que cualquier autenticación. No
llega a la página nada crudo del servidor. Hay cinco hallazgos medios: una carrera que puede perder el servidor recién dado de alta, un servidor «listo» que
se queda instalado a medias si no tiene `sudo`/`visudo`, falsas alarmas de «huella cambiada» con alias o con un `known_hosts` distinto, un tope de altas que
bloquea la sexta y cuatro mutaciones que sobreviven (una toca la propiedad de seguridad del `mktemp`).

Tests: `python3 -m unittest test_servidores_alta` → 32 OK en 8,5 s [V].

## Crítico
Ninguno.

## Alto
Ninguno.

## Medio

### M1. La escritura del alta puede colarse entre la comprobación de revisión y el guardado de `/guardar` y perderse [V código, S ventana]
- Evidencia: `/guardar` comprueba la revisión bajo `_CERROJO_GUARDADO` (en proceso) y escribe después con `guardar_config` → `_guarda_servidores`
  (`infobar/servidor_local.py:399-408`; `config_y_estado.py:128-136`). El hilo del alta escribe con `modificar_servidores`, que solo toma el cerrojo de
  fichero y no `_CERROJO_GUARDADO` (`servidores_alta.py:309`, `config_y_estado.py:139-151`).
- Escenario: el dueño pulsa «Guardar» en Ajustes mientras el alta está en «probando». La revisión coincide porque el servidor aún no está escrito. Justo
  después, el hilo del alta añade el servidor y `/guardar` escribe la lista vieja encima. La página dice «Servidor añadido», pero el servidor no está.
  La ventana dura milisegundos, pero las dos acciones salen de la misma página.
- Cambio: comprobar la revisión y escribir dentro del mismo `_bloqueo(CONFIG_SERVIDORES)`, como un compare-and-swap en `config_y_estado`. Otra opción es
  que `_guardar_servidor` tome también `_CERROJO_GUARDADO` (hay que evitar la importación circular, por ejemplo moviendo el cerrojo a `config_y_estado`).
  Test: un `fn` de `modificar_servidores` que se bloquee en un `Event` mientras se lanza `/guardar`.

### M2. Un root sin `sudo`/`visudo` sale «listo» y la instalación lo deja a medias (A3/A4 del pre-mortem, sin cerrar) [V código, S frecuencia]
- Evidencia: `DIAGNOSTICO` (`servidores_alta.py:69-79`) no comprueba `command -v sudo visudo useradd bash`. `_clasifica` (`:194-204`) da «listo» con
  `privilegio root`. `instalar-en-servidor.sh:24-41` crea el usuario e instala los scripts antes de `visudo`, que con `set -e` aborta. Además, `recoger-root`
  necesita `sudo` cuando ya está en marcha.
- Escenario: una Debian mínima de VPS con login de root y sin el paquete `sudo` (habitual). Queda el usuario `infobar` sin clave y con los scripts, y la
  página dice «La instalación no terminó bien» sin decir por qué ni cómo deshacerlo. La salida del instalador se descarta (`:279`).
- Cambio: añadir al diagnóstico `command -v sudo >/dev/null && command -v visudo >/dev/null && echo "sudo_bin si" || echo "sudo_bin no"` y devolver un mensaje
  cerrado `sin_sudo` («instala sudo: apt-get install sudo») antes de tocar nada. Si el usuario no es root y no tiene `sudo`, decir «sin sudo» en vez de
  `terminal_sudo`. Opcional (A3): que el instalador emita marcas cerradas (`paso usuario`, `paso sudoers`…) para traducirlas a mensajes, y que el mensaje
  de fallo enlace `desinstalar-del-servidor.sh`.

### M3. La huella que comprueba la app no siempre es la que comprueba `ssh`: falsas alarmas de «ataque» y callejón sin salida (M3 y H9 sin cerrar) [V código, S OpenSSH]
- Evidencia: `_conocida` mira solo `~/.ssh/known_hosts` (`servidores_alta.py:29,150-152`) y `ssh-keyscan` no lee `~/.ssh/config` (`:157`). En cambio, `ssh`
  respeta `HostName`, `HostKeyAlias`, `CanonicalizeHostname`, `UserKnownHostsFile`, `GlobalKnownHostsFile` y `KnownHostsCommand` del dueño, porque
  `OPCIONES_SEGURAS` no los fija (`servidores_ssh.py:23-26`).
- Escenarios:
  - (a) El dueño escribe su alias (`root@prod`, con `HostName` en su config). `keyscan` resuelve `prod` por DNS: da `fallo_huella` o, peor, la huella de
    otra máquina, que se añade. Después, `ssh` va a la IP real y falla.
  - (b) Hay un `UserKnownHostsFile` propio o `/dev/null` en `Host *`. Se añade la línea, pero `ssh` no la lee.
  - En los dos casos se ve `huella_cambio` («puede ser un ataque»), que es falso.
- No es una vía para saltarse la verificación: `StrictHostKeyChecking=yes` sigue verificando contra lo que `ssh` lee, y sin verificación no hay
  autenticación. Respuesta a la pregunta del encargo: las claves del agente solo se ofrecen después de que el KEX haya verificado la huella (RFC 4252/4253),
  así que el orden «huella conocida → diagnóstico» se cumple [V código `:221-226`, S protocolo].
- Cambio: resolver antes en local con `ssh -G -p <puerto> -- usuario@host` (no conecta). Si `hostname` difiere de lo tecleado, o hay `hostkeyalias`,
  `proxyjump`, `knownhostscommand` o un `userknownhostsfile` distinto de `~/.ssh/known_hosts …`, contestar con un mensaje cerrado nuevo («este destino usa
  un alias o un bastión de tu ~/.ssh/config: escribe usuario@IP» / «no soportado»). Eso es lo que el diseño promete en «Peor caso aceptado» y hoy no se
  cumple: un bastión acaba en `sin_red`. Añadir también `-o KnownHostsCommand=none` a `OPCIONES_SEGURAS`.

### M4. Tope de 5 altas en 15 minutos con un mensaje que no es verdad [V]
- Evidencia: `_limpia` solo olvida las altas que no están `en_curso` tras `VIDA_S = 900 s` (`servidores_alta.py:331-333`), y `MAX_ALTAS = 5` cuenta también
  las que están en `hecho`, `huella` (incluidas las de «No es esta», que solo borra en el cliente: `ajustes.js:109`), `error` y `terminal` (`:363-364`).
  `test_tope_de_altas_guardadas` lo confirma.
- Escenario: el dueño da de alta seis servidores seguidos, o se equivoca al teclear varias veces. La sexta alta recibe «Ya hay un alta en marcha» durante
  15 minutos sin que haya ninguna en marcha.
- Cambio: olvidar enseguida las altas en `hecho` y en `error` al empezar una nueva (o que no cuenten para el tope), añadir una acción `olvidar` para «No es
  esta», y usar un código distinto (`demasiadas`) con su propio texto.

### M5. Mutaciones que sobreviven (comprobadas con monkeypatch, sin tocar el código) [V]
Script en el scratchpad (`mutar.py`). Resultados:
| Mutación | Resultado |
|---|---|
| `_conocida` sin `-f CONOCIDAS` | sobrevive |
| `_limpia` borra también las `en_curso` | sobrevive |
| `comando_instalacion` sin `trap … EXIT` | sobrevive |
| `comando_instalacion` con ruta fija `/tmp/infobar-alta` en vez de `mktemp -d` | **sobrevive** |
| `_escanea` sin `-t ed25519` ni comprobaciones | muere |
| `_guardar_servidor` sin `nombre_repetido` | muere |
| `_error_ssh` siempre `sin_red` | muere |

- La cuarta es la que importa: una ruta predecible en `/tmp` que luego ejecuta root permite que otro usuario del servidor plante el script (enlace simbólico
  o directorio creado antes). El falso `ssh` nunca ejecuta la orden remota, así que ningún test comprueba la orden más allá de una subcadena.
- Cambio: añadir un test que ejecute `comando_instalacion(...)` con `sh -c` en local, sustituyendo `/tmp` por un directorio temporal, con el tar de
  `_paquete()` y un `instalar-en-servidor.sh` de prueba dentro. Debe comprobar: que el script recibe exactamente un argumento con la clave, que el
  directorio es 0700 y que desaparece al terminar, también cuando el instalador falla. Lo he hecho a mano [V]: con root y con `sudo` falso,
  `ARG1=[ssh-ed25519 … infobar@mac-1] N=1`, directorio `drwx------` y limpiado por el `trap`. Para `-f`: que el falso `ssh-keygen -F` exija `-f <CONOCIDAS>`.

## Bajo

- **B1. Opciones que el `~/.ssh/config` del dueño aún aplica** [V lista, S efecto]: `GSSAPIDelegateCredentials` (delega tickets Kerberos al servidor),
  `Tunnel`, `AddKeysToAgent` (en la prueba con la clave dedicada), `RemoteCommand` (con orden en la línea, `ssh` aborta y acaba en `sin_red`), `SetEnv`/
  `SendEnv`. Se pueden añadir a `OPCIONES_SEGURAS` `GSSAPIDelegateCredentials=no`, `Tunnel=no`, `KnownHostsCommand=none`, `AddKeysToAgent=no` y
  `RemoteCommand=none`. `ProxyJump` queda anulado porque `ProxyCommand=none` va primero [S, `readconf.c`]. `Match exec` sustituye `%h`/`%r`, pero llegan
  ya filtrados por `_HOST`/`_USUARIO` y no traen metacaracteres [V].
- **B2. Claves ofrecidas en el diagnóstico** [S]: sin `IdentitiesOnly`, el servidor (ya verificado) ve todas las claves públicas del agente. Con más de 6 llega
  `Too many authentication failures`, que se muestra como `terminal_auth` y arma el enfriamiento. fail2ban, en modo `normal`, puede contar «maximum
  authentication attempts exceeded». Es lo mismo que pasaría con el `ssh` habitual del dueño, así que se acepta. Mejora posible: avisarlo en el texto de
  `terminal_auth`.
- **B3. `HashKnownHosts`** [V]: `_anade_conocida` escribe el nombre en claro (`:168-174`) aunque el dueño tenga hashing activado (H7 pedía respetarlo).
  Cambio: escribir `|1|sal|HMAC-SHA1(host)` si `ssh -G` dice `hashknownhosts yes`.
- **B4. `sudo -n true` en el diagnóstico de «solo lectura»** [S]: si el usuario no está en sudoers, sudo registra «NOT in sudoers… This incident will be
  reported» y, con `mail_no_user` por defecto, manda un correo a root. Cambio: llamar a `sudo -n true` solo si `id -nG` incluye `sudo`/`admin`/`wheel`.
- **B5. `fallo_instalacion` tapa los fallos de ssh** [V `:281`]: un 255 (autenticación o huella entre el diagnóstico y la instalación) se muestra como
  «la instalación no terminó bien». Cambio: si `codigo == 255`, usar `_error_ssh(err)`.
- **B6. El nombre repetido se descubre después de instalar** [V `:302-308`]: comprobarlo también en `diagnostico` (leyendo `cfg()`) ahorra una instalación
  inútil. Hoy tampoco se detecta el mismo host con otro nombre (se vigilaría dos veces). `int(s.get("puerto") or 22)` lanza con un puerto no numérico que
  ya esté guardado y termina en `interno`.
- **B7. Página** [V `ajustes.js:86-89,109`]: los botones no se desactivan mientras el alta está `en_curso`. Un doble clic en «Instalar» devuelve
  `estado_incorrecto`, `dibuja` llama a `parar()` y el sondeo de la instalación real se pierde. El `catch(() => {})` del sondeo deja «Instalando…» quieto si
  hay un 401 o un reinicio de la barra. No hay retraso de 10 s en «Es esta» (H8), y el mensaje de huella cambiada no da el paso concreto
  (`ssh-keygen -R '[host]:puerto'`). Todo pinta con `createTextNode` y no hay `innerHTML` [V `ajustes.js:7-14`].
- **B8. Lectura no atómica de `estado`/`mensaje`** [V `:103-107`]: `pon` asigna tres atributos sin cerrojo y `publico()` los lee desde otro hilo. Se puede ver
  `estado` nuevo con `mensaje` viejo (por ejemplo «listo» con «Comprobando…»). Es cosmético. Cambio: guardar una tupla única o leer bajo un cerrojo
  del alta.
- **B9. `desinstalar-del-servidor.sh`** [V]: `userdel infobar 2>/dev/null || true` falla en silencio si `infobar` tiene procesos (una lectura en curso).
  Aun así se borra `/home/infobar` y se imprime «ya no están». Cambio: `pkill -u infobar || true` antes y comprobar `id infobar` después. La página no
  enlaza este script (el diseño A3 lo pedía).
- **B10. Código muerto** [V `:183-188`]: `so` y `uid` se parsean pero `_clasifica` no los usa. Raspbian o Mint con apt y systemd pasan como «Debian/Ubuntu».
  Si es lo que se quiere, conviene quitarlos o documentarlo.
- **B11. Instalación cortada a medias** [S]: sin pty, si se corta la conexión el instalador sigue hasta el primer `echo` (SIGPIPE con `set -e`). El estado a
  medias no es peligroso: `infobar` queda sin contraseña y sin clave, y la regla de sudo solo vale para ese usuario. Repetir el alta lo arregla porque el
  instalador es idempotente. `subprocess.run(timeout=)` mata solo a `ssh`, que no tiene hijos (`ProxyCommand=none`), y en POSIX hace `wait()`, no
  `communicate()`, así que no se cuelga [V CPython]. Todos los pasos tienen tope (≈35 s el diagnóstico, ≈130 s la instalación) salvo la espera del cerrojo
  de fichero.
- **B12. Fragilidad de tests** [V]: con una mutación que quite `DEVNULL`, el `dup2(…, 0)` deja un `ssh` falso bloqueado 30 s. Ese hilo huérfano arma después
  `_ENFRIA_HASTA` en otro test (solo pasa con mutantes). Mejora: en `tearDown`, esperar con `join` a los hilos `infobar-alta` vivos.

## Comprobado y correcto
- Entradas hasta `ssh` [V]: `_USUARIO`, `_HOST` y el puerto entero (sin `bool`) impiden empezar por `-`, los espacios, `;`, `%` y el salto de línea, y hay
  `--` antes del destino (`:134-138,207-216`). Además, el test con 22 entradas hostiles no lanza ningún proceso.
- Comando remoto [V ejecutado]: la clave pasa por `_CLAVE_PUBLICA` (sin `'`, `"`, `$` ni `` ` ``) en el Mac, de nuevo en `comando_instalacion` y otra
  vez en el instalador. `mktemp -d` da 0700, el `trap` limpia y otro usuario del servidor no puede entrar ni cambiar nada (sticky `/tmp` + 0700), y el
  `ssh` del alta ya tiene sudo, así que no gana privilegios. El tar es nuestro (3 nombres planos, 0755, uid 0).
- `linea_terminal` [V]: ruta con `shlex.quote`, usuario y host con regex compatible con la de `alta-servidor.sh:14`, y puerto entero. No hay inyección.
- TOFU [V]: solo `-t ed25519` (una conexión), una línea validada con `fullmatch` (sin `\n`), la huella sale de esa misma línea guardada en memoria, nunca
  se reemplaza una huella y el estado no deja aceptar dos veces porque `_corre` pasa a `en_curso` dentro del cerrojo.
- Puerta HTTP [V]: sesión, Host, Sec-Fetch-Site, JSON y `parse_qs` acotado en `estado`, con tests de 401/403.

## Acciones recomendadas (por orden)
1. M1: comprobar la revisión y escribir en un solo paso bajo el cerrojo de fichero.
2. M2: detectar `sudo`/`visudo` en el diagnóstico, con un mensaje cerrado `sin_sudo`.
3. M5: añadir el test que ejecute la orden remota con `sh` en local y que el falso `-F` exija `-f`.
4. M3: usar `ssh -G` antes del escaneo, con mensajes «alias/bastión no soportado», y `KnownHostsCommand=none`.
5. M4: que las altas terminadas no cuenten para el tope, con un mensaje propio.
6. Los bajos según convenga (B5 y B7 cuestan poco).

## Preguntas sin resolver
- ¿Se quiere aceptar alias de `~/.ssh/config` en el alta (resolviéndolos con `ssh -G`) o exigir `usuario@IP/host`? Esto decide la forma de M3.
- ¿Se mantiene en el diagnóstico la «Debian/Ubuntu» comprobada solo por `apt` + `systemd` (B10)?

Status: DONE_WITH_CONCERNS
Summary: La 3.7a es segura en lo esencial: no hay inyección hacia `ssh` ni hacia el comando remoto, la huella va antes de autenticar y no se filtra nada
crudo, y 32 tests pasan. Quedan cinco hallazgos medios: la carrera con `/guardar`, servidores sin sudo instalados a medias, falsas alarmas de huella con
alias o con otro known_hosts, el tope de altas y cuatro mutaciones vivas (una sobre el `mktemp`).
