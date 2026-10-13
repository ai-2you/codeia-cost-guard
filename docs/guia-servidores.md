# Guía de servidores

Cómo dar de alta un VPS en infobar, qué hace en él, qué significa cada aviso y cómo arreglar lo
que suele salir. Pensada para Ubuntu (24.04) con Dokploy, Easypanel o docker a secas.

## Cómo funciona (en corto)

- Cada pocos minutos, infobar entra por SSH como el usuario **`infobar`** con una clave propia
  (`~/.ssh/infobar_ed25519`). Esa clave **solo** puede ejecutar `/usr/local/bin/infobar-recoger`,
  un script de lectura instalado en el servidor (`restrict,command=` en `authorized_keys`).
- Lo que necesita root (docker y fail2ban, la configuración real de sshd, el cortafuegos) lo lee
  `/usr/local/lib/infobar/recoger-root`. Es lo único que `infobar` puede ejecutar con `sudo`, y sin
  argumentos.
- Desde el Mac se miden también la latencia de las webs y **qué puertos se alcanzan de verdad desde
  Internet**, porque desde dentro del servidor no se ve el cortafuegos del proveedor.
- La recogida se repite cada 5 min y al pulsar ⟳. Si un servidor está en ámbar o en rojo, pasa a
  **modo incidencia** y se revisa cada minuto. También se puede activar a mano desde el selector
  (dura 30 min).

## Dar de alta un servidor

Requisitos: entras por SSH con tu usuario (p. ej. `victor`) y ese usuario puede usar `sudo`.

```bash
cd infobar        # la carpeta donde clonaste el repo
infobar/remoto/alta-servidor.sh victor@IP_DEL_SERVIDOR        # [puerto] si no es el 22
```

El script:
1. crea la primera vez la clave `~/.ssh/infobar_ed25519` (sin passphrase, para que la app arranque
   sola);
2. copia los scripts al servidor y ejecuta `instalar-en-servidor.sh` con `sudo`, que crea el
   usuario `infobar`, instala los scripts, la regla de `sudo` y la clave limitada (léelo antes si
   quieres; no toca nada más);
3. prueba la conexión y te dice qué línea añadir.

Después añádelo desde el panel → **Ajustes → Servidores**, o a mano en **`~/.cost-guard/servidores.json`**
(fuera de git: lleva hosts y usuarios; la plantilla es `servidores.example.json`):

```json
"servidores": [
  {"nombre": "produccion", "ssh": "infobar@203.0.113.10", "ssh_admin": "victor@203.0.113.10",
   "panel": "dokploy", "panel_url": "https://dokploy.example.com", "webs": ["https://example.com"]}
]
```

| Campo | Para qué |
|---|---|
| `nombre` | Lo que sale en el selector |
| `ssh` | Siempre `infobar@<host>` |
| `ssh_admin` | Tu usuario: lo abre el botón **Terminal** |
| `puerto` | Solo si SSH no está en el 22 |
| `panel`, `panel_url` | `dokploy` o `easypanel` y su URL **https**: la abre el botón **Panel** |
| `webs` | Hasta 4 URLs para medir la latencia; la primera la abre el botón **Abrir** |

Opcional, a nivel general: `"ips_conocidas": ["tu IP"]` hace que una sesión SSH desde otra IP salga
en ámbar. `intervalo_normal_s` (300) e `intervalo_incidencia_s` (60) cambian los ritmos.

## Qué significa cada tarjeta

| Tarjeta | Verde | Ámbar / rojo |
|---|---|---|
| Webs | responde en menos de 1,5 s | lenta / error o código 5xx |
| Procesador, Memoria, Disco | por debajo del 80-85 % | más del 80-85 % / más del 95 % |
| Servicios | todos con sus réplicas | alguno caído (rojo) |
| Dentro ahora | nadie, o desde IPs conocidas | sesión desde una IP desconocida |
| Intentos 1 h | lo normal del día | pico: más del triple de la media (posible ataque) |
| Bloqueadas | fail2ban activo | sin fail2ban |
| Puertas abiertas | solo 22, 80 y 443 desde Internet | otro puerto alcanzable (rojo) |
| Protección | 12 de 12 | algo de la lista de abajo |
| Mantenimiento | al día | reinicio o parches de seguridad pendientes |

Gris significa que el dato es viejo (más de 3 ciclos sin poder leerlo). Si no hay conexión, la
tarjeta dice por qué: `timeout`, `red`, `autenticacion` o `clave de host no confiada`.

## Protección: qué se revisa y cómo arreglarlo

El menú **···** → «Copiar cómo protegerlo» copia los comandos de lo que falta en el servidor elegido.

| Comprobación | Arreglo |
|---|---|
| SSH sin contraseñas | `echo 'PasswordAuthentication no' \| sudo tee /etc/ssh/sshd_config.d/00-sin-contrasena.conf && sudo sshd -t && sudo systemctl reload ssh` |
| SSH sin login interactivo | `KbdInteractiveAuthentication no` en el mismo fichero |
| root no entra con contraseña | `PermitRootLogin prohibit-password` (o `no`) |
| sin contraseñas vacías | `PermitEmptyPasswords no` |
| fail2ban vigilando SSH | `sudo apt install -y fail2ban` (en Ubuntu ya vigila SSH) |
| cortafuegos activo | `sudo ufw default deny incoming && sudo ufw allow 22,80,443/tcp && sudo ufw enable` |
| solo 22/80/443 abiertos | ver «Cerrar un puerto que publica docker» |
| parches automáticos | `sudo apt install unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades` |
| sin parches de seguridad pendientes | `sudo apt update && sudo apt full-upgrade -y` |
| sin reinicio pendiente | `sudo reboot` (a una hora tranquila; los servicios de docker vuelven solos) |
| sudo pide contraseña | quitar las reglas `NOPASSWD` de `/etc/sudoers.d/` (ver abajo) |
| solo root con UID 0 | revisar `/etc/passwd` |
| hora sincronizada (NTP) | `sudo timedatectl set-ntp true` (no avisa los 15 primeros minutos tras arrancar) |

**Por qué el `00-`:** Ubuntu en la nube trae `50-cloud-init.conf` con `PasswordAuthentication yes`,
y en sshd **gana el primer valor que se lee**. Un fichero que empieza por `00-` se lee antes y manda.

## Cerrar un puerto que publica docker

Docker publica los puertos de sus servicios **saltándose ufw**. Si un panel (p. ej. Easypanel en el
3000) se ve desde Internet, y ya entras a él por un dominio con https:

```bash
scp infobar/remoto/cerrar-puerto-docker.sh victor@IP:/tmp/
ssh -t victor@IP 'sudo bash /tmp/cerrar-puerto-docker.sh 3000'
```

El script pone una regla en la cadena `DOCKER-USER` (la que docker reserva para esto) y la deja fija
con un servicio systemd. Deshacer: `sudo systemctl disable --now infobar-cerrar-puerto-3000`.
Si el proveedor tiene cortafuegos de red (Hetzner Cloud Firewall, Scaleway Security Groups…), también
sirve cerrarlo allí. Contabo no lo trae.

## Problemas frecuentes

**«La prueba como infobar falló» con `AllowUsers` en el aviso.** El servidor solo deja entrar a los
usuarios de esa lista. Añade `infobar` en la misma línea (en `/etc/ssh/sshd_config` o en
`sshd_config.d/`) y luego `sudo sshd -t && sudo systemctl reload ssh`.

**De repente no puedes entrar por SSH («Connection refused») pero la web sí va.** fail2ban ha
bloqueado tu IP por intentos fallidos. Espera (en Ubuntu, 10 min por defecto) o desbloquéala desde
otra IP o desde la consola web del proveedor:
`sudo fail2ban-client set sshd unbanip TU_IP`. infobar no insiste cuando su clave es rechazada,
precisamente para no provocarlo.

**`sudo` sin contraseña (`NOPASSWD`).** Muchas imágenes de nube lo traen
(`/etc/sudoers.d/90-cloud-init-users`). Es cómodo, pero quien entre como tu usuario es root al
instante. Antes de quitarlo, **ponte una contraseña** (`sudo passwd victor`), comprueba en otra
sesión que `sudo -k; sudo true` te la pide, y después edita el fichero con
`sudo visudo -f /etc/sudoers.d/<fichero>`.

**«clave de host no confiada».** El servidor ha cambiado de clave (reinstalación) o es la primera
vez. Entra una vez a mano (`ssh victor@IP`), comprueba la huella y acéptala.

**El servidor sale gris.** infobar no ha podido leerlo en un rato: mira el motivo en la tarjeta y en
`~/.cost-guard/logs/costbar.log`.

## Quitar un servidor

Quítalo en **Ajustes → Servidores** (o de `servidores` en `~/.cost-guard/servidores.json`). En el servidor, si quieres limpiarlo:
```bash
sudo userdel -r infobar; sudo rm -f /usr/local/bin/infobar-recoger /etc/sudoers.d/infobar
sudo rm -rf /usr/local/lib/infobar
```
