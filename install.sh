#!/usr/bin/env bash
# Instala codeia-cost-guard.
#   macOS  -> barra de menus con estilo propio (icono + desplegable) y panel
#   Otros  -> medidor y panel (solo necesita Python 3.9+)
set -euo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINO="${COSTGUARD_HOME:-$HOME/.cost-guard}"
REGLAS=0; [ "${1:-}" = "--reglas" ] && REGLAS=1

echo "-> Copiando a $DESTINO"
mkdir -p "$DESTINO/logs"
# todo lo que guarda la app es sensible (proyectos, hosts, IPs): solo tu usuario lo lee
chmod 700 "$DESTINO" "$DESTINO/logs"
for f in costbar.py barra.py popover.py panel.py analizar_gasto.py \
         precio_astra.py instalar_reglas_globales.py GUIA.md AGENTS-snippet.md; do
  [ -f "$AQUI/$f" ] && cp "$AQUI/$f" "$DESTINO/$f"
done
# el motor vive en el paquete infobar/ (costbar.py solo es la entrada): sin el, nada arranca
mkdir -p "$DESTINO/infobar"
cp "$AQUI"/infobar/*.py "$DESTINO/infobar/"
# scripts que se instalan en los servidores (alta-servidor.sh los copia desde aqui)
mkdir -p "$DESTINO/infobar/remoto"
cp "$AQUI"/infobar/remoto/*.sh "$DESTINO/infobar/remoto/" && chmod 755 "$DESTINO"/infobar/remoto/*.sh
if [ -d "$AQUI/panel_assets" ]; then
  mkdir -p "$DESTINO/panel_assets"
  for f in "$AQUI"/panel_assets/*.css "$AQUI"/panel_assets/*.js "$AQUI"/panel_assets/*.html; do
    [ -f "$f" ] && cp "$f" "$DESTINO/panel_assets/"
  done
fi
# config.json no se pisa: si ya existe trae tus cuotas y notas por proveedor (como state.json)
# (config.json no esta en el repo: si no tienes uno propio, se parte de config.example.json)
if [ ! -f "$DESTINO/config.json" ]; then
  if [ -f "$AQUI/config.json" ]; then cp "$AQUI/config.json" "$DESTINO/config.json"
  else cp "$AQUI/config.example.json" "$DESTINO/config.json"; fi
  chmod 600 "$DESTINO/config.json"
fi
# servidores.json (hosts y usuarios) tampoco se pisa ni se copia del repo: es solo tuyo
[ -f "$DESTINO/state.json" ] || echo "{}" > "$DESTINO/state.json"

if [ "$(uname -s)" = "Darwin" ]; then
  echo "-> Python + pillow + pyobjc"
  python3 -m venv "$DESTINO/venv" 2>/dev/null || true
  "$DESTINO/venv/bin/pip" install --quiet --upgrade pip
  "$DESTINO/venv/bin/pip" install --quiet pillow pyobjc-framework-Cocoa

  if [ ! -e "/Applications/Google Chrome.app" ] && ! command -v chromium >/dev/null 2>&1; then
    echo "  aviso: sin Chrome/Chromium el desplegable no se pinta (el icono si funciona)."
  fi

  echo "-> Arranque automatico al iniciar sesion"
  PLIST="$HOME/Library/LaunchAgents/dev.codeia.costguard.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.codeia.costguard</string>
  <key>ProgramArguments</key><array>
    <string>$DESTINO/venv/bin/python</string><string>$DESTINO/barra.py</string>
  </array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>Umask</key><integer>63</integer>
  <key>StandardOutPath</key><string>$DESTINO/logs/barra.log</string>
  <key>StandardErrorPath</key><string>$DESTINO/logs/barra.err</string>
</dict></plist>
PLIST
  # parar la copia anterior y esperar a que macOS la descargue de verdad: si se vuelve a cargar
  # enseguida, launchctl responde "Bootstrap failed: 5" y la app se quedaba sin arrancar
  SERV="gui/$(id -u)/dev.codeia.costguard"
  launchctl bootout "$SERV" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do launchctl print "$SERV" >/dev/null 2>&1 || break; sleep 1; done
  for intento in 1 2 3 4 5; do
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null && break
    sleep 2
  done
  if launchctl print "$SERV" >/dev/null 2>&1; then
    echo "-> arrancada (y arrancara sola al iniciar sesion)"
  else
    echo "AVISO: no se pudo arrancar. Prueba: launchctl bootstrap gui/$(id -u) $PLIST"
  fi
  echo ""
  echo "Listo: mira la barra de menus (icono con la cifra de las ultimas 5 h)."
  echo "  Log:     $DESTINO/logs/barra.log"
  echo "  Parar:   launchctl bootout gui/$(id -u)/dev.codeia.costguard"
  echo "  Arrancar launchctl bootstrap gui/$(id -u) $PLIST"
  echo "  Prueba:  $DESTINO/venv/bin/python $DESTINO/barra.py --abre"
else
  echo "Listo (sin barra de menus en este sistema)."
fi

if [ "$REGLAS" = 1 ] && [ -f "$DESTINO/instalar_reglas_globales.py" ]; then
  python3 "$DESTINO/instalar_reglas_globales.py" || true
fi
echo ""
echo "Panel: python3 \"$DESTINO/panel.py\"  (genera panel.html)"
