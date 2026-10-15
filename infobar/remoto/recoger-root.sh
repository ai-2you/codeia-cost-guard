#!/bin/bash
# infobar: la parte que necesita root (docker y fail2ban). Se instala en
# /usr/local/lib/infobar/recoger-root (root:root 0755) y sudoers solo permite ESTE programa, sin
# argumentos, al usuario infobar. Solo lee.
export LC_ALL=C PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# como root, nada del hogar de infobar (plugins de ~/.docker, etc.)
export HOME=/root DOCKER_CONFIG=/root/.docker
# Presupuesto de tiempo: ningun comando arranca pasados LIMITE s y cada uno dura 3 s como mucho, asi que el programa acaba en LIMITE + 3 s,
# antes del tope de 12 s del sudo de recoger.sh (y este, antes de su vigilante de 14 s y del tope de 20 s del Mac).
INICIO=$(date +%s); LIMITE=7
t() { [ $(( $(date +%s) - INICIO )) -ge $LIMITE ] && return 1; timeout 3 "$@" </dev/null 2>/dev/null; }

echo "### servicios"
# Dokploy y Easypanel despliegan servicios de Swarm; si el nodo no es manager, contenedores sueltos
if [ "$(t docker info --format '{{.Swarm.ControlAvailable}}')" = "true" ]; then
  t docker service ls --format '{{.Name}}\t{{.Replicas}}' | head -n 60
fi

echo "### contenedores"
t docker ps -a --format '{{.Names}}\t{{.State}}\t{{.Status}}' | head -n 100

echo "### bastionado_root"
# configuracion EFECTIVA de sshd (sshd -T necesita root) y cortafuegos
t sshd -T | awk '$1 ~ /^(passwordauthentication|permitrootlogin|permitemptypasswords|kbdinteractiveauthentication|maxauthtries|x11forwarding)$/ {print "sshd_" $1, $2}'
if command -v ufw >/dev/null 2>&1; then
  echo "ufw $(t ufw status | awk 'NR==1{print $2}')"
fi
# politica por defecto de entrada: DROP = se filtra (ufw la pone asi); las reglas de docker no cuentan
echo "input_politica $(t iptables -S INPUT | awk '$1=="-P"{print $3; exit}')"
echo "fail2ban_activo $(t systemctl is-active fail2ban)"
# reglas de sudo SIN contrasena para usuarios normales. No cuentan: la de infobar (un solo programa)
# ni las de root (root ya lo puede todo: cloud-init pone "root ALL=(ALL) NOPASSWD:ALL" y es inocua)
NOPW="$(grep -rHsE '^[^#]*NOPASSWD' /etc/sudoers /etc/sudoers.d/ --exclude=infobar | grep -vE ':[[:space:]]*root[[:space:]]')"
echo "sudo_nopasswd $(printf '%s' "$NOPW" | grep -c .)"
echo "sudo_nopasswd_ficheros $(printf '%s' "$NOPW" | cut -d: -f1 | xargs -r -n1 basename | sort -u | tr '\n' ',' | tr -cd 'A-Za-z0-9._,-')"

echo "### fail2ban"
if command -v fail2ban-client >/dev/null 2>&1; then
  t fail2ban-client status sshd | awk -F: '/Currently banned/{gsub(/[ \t]/,"",$2); print "ahora", $2}
                                         /Total banned/{gsub(/[ \t]/,"",$2); print "total", $2}'
else
  echo "no-instalado"
fi
