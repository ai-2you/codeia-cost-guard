# Chuleta de infobar

Lo mínimo para recordar dónde está cada cosa y qué comando usar.

## Dónde vive cada cosa

| Qué | Dónde |
|---|---|
| El código (para cambiarlo y hacer `git pull`) | la carpeta donde clonaste el repo |
| La app instalada (la que corre) | `~/.cost-guard/` |
| **Tu configuración** (planes) | `~/.cost-guard/config.json` |
| **Tus servidores** (hosts, usuarios; fuera de git) | `~/.cost-guard/servidores.json` |
| Log de la app | `~/.cost-guard/logs/costbar.log` |
| Últimos datos de los servidores | `~/.cost-guard/logs/servidores.json` |
| Tema, pestaña y servidor elegido | `~/.cost-guard/logs/ui_estado.json` |
| Clave de NaN | Llavero de macOS → servicio `infobar`, cuenta `nan` |
| Clave SSH de infobar | `~/.ssh/infobar_ed25519` (solo puede leer, en cada servidor) |
| Arranque automático | `~/Library/LaunchAgents/dev.codeia.costguard.plist` |

## Lo de cada día

```bash
cd infobar        # la carpeta donde clonaste el repo

./install.sh                               # tras un git pull: actualiza la app (no pisa tu config)
python3 costbar.py --clave nan             # guardar o cambiar la clave de NaN (sin eco)
python3 costbar.py --print                 # informe de tokens en la terminal
python3 analizar_gasto.py 7                # informe de los ultimos 7 dias
infobar/remoto/alta-servidor.sh victor@IP  # dar de alta un servidor
python3 -m unittest discover tests         # tests
```

Parar o arrancar la app:
```bash
launchctl bootout gui/$(id -u)/dev.codeia.costguard                                       # parar
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.codeia.costguard.plist        # arrancar
```

## En el desplegable

- **⟳**: recoge todo ya (servidores y NaN incluidos). La ruedita indica que está trabajando.
- **Selector** (nombre del servidor): cambiar de servidor, o activar el **modo incidencia**
  (revisión cada minuto durante 30 min).
- **Terminal**: abre `ssh` con tu usuario en el servidor elegido. **Panel**: abre Dokploy o
  Easypanel. **Abrir**: abre la web.
- **···**: tema (claro, oscuro o el del sistema), refrescar, «Copiar cómo protegerlo», panel web y
  salir.
- **Punto de color tras el icono**: algún servidor no está en verde.

## Tus servidores

Apunta los tuyos (nombre, proveedor, panel, notas) en `docs/chuleta-personal.md`: está en
`.gitignore`, así que no se sube al repo.

## Si algo falla

- **NaN no sale «en vivo»**: clave mal guardada (error 401 en el log). Vuelve a guardarla con
  `--clave nan`.
- **Un servidor en rojo «autenticacion»**: la clave de infobar no entra. infobar espera 30 min
  antes de reintentar; arréglalo y pulsa ⟳.
- **No puedes entrar por SSH pero la web va**: fail2ban ha bloqueado tu IP. Espera 10 min.
- **El icono no aparece**: la barra está llena y macOS lo esconde tras la muesca. Sácalo con
  Bartender o Ice, o quita algún icono arrastrándolo con ⌘ pulsado.
- Más en [guia-servidores.md](guia-servidores.md).
