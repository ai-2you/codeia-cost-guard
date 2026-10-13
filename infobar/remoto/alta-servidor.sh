#!/bin/bash
# infobar: da de alta un servidor desde el Mac. Ejecutalo en Terminal (te pedira tu contrasena de
# sudo en el servidor una vez):
#
#   infobar/remoto/alta-servidor.sh victor@203.0.113.10 [puerto]
#
# 1. Crea (solo la primera vez) la clave dedicada ~/.ssh/infobar_ed25519, sin passphrase: la usa la
#    app al arrancar sola, y en el servidor solo puede ejecutar el script de lectura.
# 2. Copia los scripts al servidor y ejecuta instalar-en-servidor.sh con sudo (leelo antes).
# 3. Prueba la conexion como infobar y te dice que poner en config.json.
set -euo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINO="${1:-}"; PUERTO="${2:-22}"
[[ "$DESTINO" =~ ^[A-Za-z0-9._][A-Za-z0-9._-]*@[A-Za-z0-9._][A-Za-z0-9._-]*$ ]] || { echo "uso: $0 usuario@host [puerto]"; exit 1; }
[[ "$PUERTO" =~ ^[0-9]{1,5}$ ]] && [ "$((10#$PUERTO))" -gt 0 ] && [ "$((10#$PUERTO))" -lt 65536 ] || { echo "puerto no valido"; exit 1; }
HOST="${DESTINO#*@}"
CLAVE="$HOME/.ssh/infobar_ed25519"

if [ ! -f "$CLAVE" ]; then
  echo "-> creando la clave dedicada $CLAVE"
  ssh-keygen -q -t ed25519 -N "" -C "infobar@$(hostname -s)" -f "$CLAVE"
fi
PUB="$(cat "$CLAVE.pub")"

echo "-> copiando scripts a $DESTINO"
REMOTO="$(ssh -p "$PUERTO" -- "$DESTINO" 'mktemp -d /tmp/infobar-alta.XXXXXX' | tail -n 1)"
# lo que sigue se usa con sudo y rm -rf: solo una ruta de mktemp exacta
[[ "$REMOTO" =~ ^/tmp/infobar-alta\.[A-Za-z0-9]{6}$ ]] || { echo "respuesta inesperada del servidor: $REMOTO"; exit 1; }
scp -q -P "$PUERTO" "$AQUI/instalar-en-servidor.sh" "$AQUI/recoger.sh" "$AQUI/recoger-root.sh" "$DESTINO:$REMOTO/"

echo "-> instalando (te pedira la contrasena de sudo)"
ssh -t -p "$PUERTO" "$DESTINO" "sudo bash $REMOTO/instalar-en-servidor.sh '$PUB'; rm -rf $REMOTO"

echo "-> probando como infobar"
# la salida se guarda antes de buscar en ella: con pipefail, un grep -q que corta la tuberia
# haria fallar a ssh (SIGPIPE) aunque la conexion fuera bien
PRUEBA="$(ssh -T -p "$PUERTO" -i "$CLAVE" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=8 \
     "infobar@$HOST" </dev/null || true)"
if grep -q '^### version' <<<"$PRUEBA"; then
  echo ""
  echo "OK. Anade esto a \"servidores\" en config.json (y panel/panel_url/webs si quieres):"
  echo "  {\"nombre\": \"$HOST\", \"ssh\": \"infobar@$HOST\"$( [ "$PUERTO" != 22 ] && echo ", \"puerto\": $PUERTO")}"
else
  echo "La prueba como infobar fallo: revisa los avisos de arriba."
  exit 1
fi
