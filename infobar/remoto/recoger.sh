#!/bin/bash
# infobar: lectura del estado del servidor. Se instala en /usr/local/bin/infobar-recoger (root:root
# 0755) y es lo UNICO que puede ejecutar la clave SSH de infobar (command= en authorized_keys).
# Solo lee. Cada comando con tiempo maximo y sin stdin; lo que falle sale vacio.
# Salida: secciones "### <nombre>" que el Mac interpreta (las desconocidas se ignoran).
export LC_ALL=C PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
t() { timeout 5 "$@" </dev/null 2>/dev/null; }

echo "### version"
echo 1

echo "### sistema"
t cat /proc/loadavg
t nproc
t cut -d' ' -f1 /proc/uptime

echo "### memoria"
t free -b | awk '/^Mem:/{print $2, $7}'            # total disponible

echo "### disco"
t df -B1 --output=size,used / | tail -n 1

echo "### sesiones"
t who                                              # la sesion de infobar no tiene tty: no sale aqui

echo "### puertos"
t ss -tlnH | awk '{print $4}' | grep -vE '^(127\.|\[::1\]|::1)' | sed 's/.*://' | sort -un | tr '\n' ' '
echo

echo "### intentos"
# Intentos de acceso SSH rechazados en 24 h: total, ultima hora y las 4 IPs que mas lo intentan.
# Se agregan aqui para que por la red solo viajen unas lineas.
ahora=$(date +%s)
t journalctl -t sshd -t sshd-session --since "-24h" -o short-unix -q | awk -v ahora="$ahora" '
  # un intento deja varias lineas (Invalid user, Disconnected from invalid user...): se cuenta
  # solo la linea canonica de cada uno para no multiplicar
  /Invalid user .* from |Connection closed by authenticating user|Failed password for |maximum authentication attempts/ {
    if ($0 ~ /Failed password for invalid user/) next
    ts = int($1); ip = ""
    for (i = NF; i > 0; i--) if ($i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ || $i ~ /^[0-9a-fA-F:]+:[0-9a-fA-F:]+$/) { ip = $i; break }
    if (ip == "") next
    total++; if (ahora - ts <= 3600) hora++; n[ip]++
  }
  END {
    print "total", total + 0; print "hora", hora + 0
    for (ip in n) print "ip", ip, n[ip]
  }' | { read -r a; echo "$a"; read -r b; echo "$b"; sort -k3 -nr | head -n 4; }

echo "### actualizaciones"
t apt list --upgradable 2>/dev/null | grep -c upgradable
[ -f /var/run/reboot-required ] && echo reinicio || echo sin-reinicio

echo "### bastionado"
# Comprobaciones de buenas practicas que no necesitan root (clave valor). El Mac las convierte en
# la lista de "que falta"; las que necesitan root van en recoger-root.
au=$(t cat /etc/apt/apt.conf.d/20auto-upgrades)
echo "auto_upgrades $(echo "$au" | grep -q 'Unattended-Upgrade "1"' && echo si || echo no)"
echo "unattended_instalado $(t dpkg-query -W -f='${Status}' unattended-upgrades | grep -q 'install ok installed' && echo si || echo no)"
echo "parches_seguridad $(t apt list --upgradable 2>/dev/null | grep -c -- '-security')"
echo "uid0_extra $(t awk -F: '$3==0 && $1!="root"' /etc/passwd | wc -l)"
echo "hora_sincronizada $(t timedatectl show -p NTPSynchronized --value)"

# docker y fail2ban necesitan root: un unico programa sin argumentos, permitido en sudoers
if [ -x /usr/local/lib/infobar/recoger-root ]; then
  timeout 15 sudo -n /usr/local/lib/infobar/recoger-root </dev/null 2>/dev/null
fi
