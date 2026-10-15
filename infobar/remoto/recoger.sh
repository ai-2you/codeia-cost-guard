#!/bin/bash
# infobar: lectura del estado del servidor. Se instala en /usr/local/bin/infobar-recoger (root:root
# 0755) y es lo UNICO que puede ejecutar la clave SSH de infobar (command= en authorized_keys).
# Solo lee. Cada comando con tiempo maximo y sin stdin; lo que falle sale vacio.
# Salida: secciones "### <nombre>" que el Mac interpreta (las desconocidas se ignoran).
# Las secciones con marco ("n <lineas>" al principio y "fin <lineas> <codigo>" al final) solo se dan por
# completas si traen el cierre; el script termina con "### fin".
export LC_ALL=C PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
t() { timeout 5 "$@" </dev/null 2>/dev/null; }
# Vigilante: pase lo que pase el script termina antes del tope del Mac (20 s con la conexion). Lo que llegue sin "### fin" se da por cortado.
( sleep 14; kill -TERM $$ ) </dev/null >/dev/null 2>&1 &
VIGILANTE=$!
trap 'kill $VIGILANTE 2>/dev/null' EXIT

echo "### version"
echo 2

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
# Intentos de acceso SSH rechazados. Solo cuentan las lineas que escribio sshd de verdad: se filtra por la
# unidad de systemd (campo que pone journald; `logger -t sshd` no lo falsea, a diferencia de -t y de _COMM)
# y las IPs solo se dan por fiables si las sesiones de usuario no viven en el cgroup de sshd (aislamiento).
# Los cubos son horas naturales (hora de epoca); "-" = el journal no llega a esa hora (desconocido, no 0).
UNIDADES="_SYSTEMD_UNIT=ssh.service _SYSTEMD_UNIT=sshd.service"
ahora=$(date +%s)
cuerpo=$(
  echo "ahora $ahora"
  if [ -n "$(t journalctl -q -n 1 -o cat $UNIDADES)" ]; then
    echo "fuente unit"
    grep -qE 'session-[0-9]+\.scope' /proc/self/cgroup 2>/dev/null && echo "aislamiento si" || echo "aislamiento no"
    cob=$(timeout 3 journalctl -q -o short-unix </dev/null 2>/dev/null | head -n 1 | cut -d. -f1 | tr -cd '0-9')
    echo "cobertura ${cob:-0}"
    timeout 6 journalctl -q -o short-unix --since "-25h" $UNIDADES </dev/null 2>/dev/null | cut -b -400 | awk -v ahora="$ahora" -v cob="${cob:-0}" '
      # un intento deja varias lineas (Invalid user, Disconnected from invalid user...): se cuenta
      # solo la linea canonica de cada uno para no multiplicar
      /Invalid user .* from |Connection closed by authenticating user|Failed password for |maximum authentication attempts/ {
        if ($0 ~ /Failed password for invalid user/) next
        ts = int($1)
        # la IP es el campo anterior al ULTIMO "port": el usuario (que elige el atacante) va antes
        ip = ""
        for (i = NF; i > 1; i--) if ($i == "port" && $(i + 1) ~ /^[0-9]+$/) {
          cand = $(i - 1)
          if (cand ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ || cand ~ /^[0-9a-fA-F:]+:[0-9a-fA-F:]+$/) ip = cand
          break
        }
        if (ip == "" || ts > ahora + 60) next
        h[int(ts / 3600)]++
        if (ahora - ts <= 86400) { total++; n[ip]++ }
        if (ahora - ts <= 3600) hora++
      }
      END {
        print "total", total + 0; print "hora", hora + 0
        act = int(ahora / 3600)
        for (k = act - 24; k <= act; k++) { if (cob == 0 || k * 3600 < cob) print "h", k, "-"; else print "h", k, h[k] + 0 }
        for (c = 0; c < 12; c++) {
          mejor = ""; max = 0
          for (x in n) if (n[x] > max) { max = n[x]; mejor = x }
          if (mejor == "") break
          print "ip", mejor, max; delete n[mejor]
        }
      }'
    echo "parcial $([ "${PIPESTATUS[0]}" = 124 ] && echo si || echo no)"
  else
    echo "fuente ninguna"
  fi
)
cuerpo=${cuerpo:0:6000}
nlin=$(printf '%s\n' "$cuerpo" | grep -c '')
echo "n $nlin"; printf '%s\n' "$cuerpo"; echo "fin $nlin 0"

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
  timeout 12 sudo -n /usr/local/lib/infobar/recoger-root </dev/null 2>/dev/null
fi

echo "### fin"
