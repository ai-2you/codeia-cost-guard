#!/bin/bash
# infobar: la parte que necesita root (docker y fail2ban). Se instala en
# /usr/local/lib/infobar/recoger-root (root:root 0755) y sudoers solo permite ESTE programa, sin
# argumentos, al usuario infobar. Solo lee.
export LC_ALL=C PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# como root, nada del hogar de infobar (plugins de ~/.docker, etc.)
export HOME=/root DOCKER_CONFIG=/root/.docker
# Presupuesto de tiempo: ningun comando arranca pasados LIMITE s y cada uno dura 3 s como mucho, asi que el programa acaba en LIMITE + 3 s,
# antes del tiempo que le da recoger.sh. Lo que se salta o se pasa de tiempo se apunta en MARCAS (un fichero: `t` casi siempre corre dentro
# de $(...) y una variable no volveria) y al final se declara en "### fin_root": con omitidos > 0 el Mac descarta TODAS las secciones de
# root («sin dato»), nunca las toma por ceros.
INICIO=$(date +%s); LIMITE=7
MARCAS=$(mktemp) || exit 0
trap 'rm -f "$MARCAS"' EXIT
t() {
  if [ $(( $(date +%s) - INICIO )) -ge $LIMITE ]; then echo . >>"$MARCAS"; return 1; fi
  timeout 3 "$@" </dev/null 2>/dev/null
  local rc=$?
  [ $rc = 124 ] && echo . >>"$MARCAS"
  return $rc
}

echo "### version_root"
echo 2

echo "### servicios"
# Dokploy y Easypanel despliegan servicios de Swarm; si el nodo no es manager, contenedores sueltos
if [ "$(t docker info --format '{{.Swarm.ControlAvailable}}')" = "true" ]; then
  t docker service ls --format '{{.Name}}\t{{.Replicas}}' | head -n 300
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

echo "### inventario"
# Contenedores con estado, salud y reinicios. UNA llamada a `docker inspect` con una plantilla FIJA que solo nombra los campos de abajo (nunca
# Config.Env, Cmd, Mounts, Image ni `.State.Health` entero: su .Log lleva la salida del healthcheck). Cada texto sale con {{json}}, asi un
# valor con saltos de linea, tabuladores o "### algo" no puede romper lineas ni abrir secciones. Los mas antiguos primero (produccion antes
# que los efimeros) y como mucho 200: lo que sobra se declara en "truncado" y el Mac no concluye que lo que no ve haya desaparecido.
# Marco "n <lineas>" ... "fin <lineas> <codigo>" como en intentos; "pedidos" = ids que se inspeccionan, el Mac exige tantas lineas "c".
# PRESUPUESTO PROPIO: solo arranca si no se han gastado ya 5 s y no usa `t`, asi que si el inventario no llega a tiempo se pierde el inventario
# («fuente error»), no las secciones de seguridad de root ni el cierre de fin_root.
PLANTILLA='c{{"\t"}}{{json .Name}}{{"\t"}}{{json .State.Status}}{{"\t"}}{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}}{{"\t"}}{{.RestartCount}}{{"\t"}}{{.State.ExitCode}}{{"\t"}}{{json .State.StartedAt}}{{"\t"}}{{json .HostConfig.RestartPolicy.Name}}{{"\t"}}{{json (index .Config.Labels "com.docker.compose.project")}}{{"\t"}}{{json (index .Config.Labels "com.docker.compose.service")}}{{"\t"}}{{json (index .Config.Labels "com.docker.swarm.service.name")}}{{"\t"}}{{json (index .Config.Labels "com.docker.swarm.task.id")}}'
cuerpo=$(
  echo "ahora $(date +%s)"
  if ! command -v docker >/dev/null 2>&1; then
    echo "fuente ninguna"
  elif [ $(( $(date +%s) - INICIO )) -ge 5 ]; then
    echo "fuente error"
  elif ids=$(timeout 2 docker ps -aq --no-trunc </dev/null 2>/dev/null); then
    total=$(printf '%s\n' "$ids" | grep -c .)
    pedidos=$(printf '%s\n' "$ids" | grep . | tac | head -n 200 | tr '\n' ' ')
    n=$(printf '%s\n' $pedidos | grep -c .)
    echo "fuente docker"; echo "total $total"; echo "pedidos $n"; echo "truncado $([ "$total" -gt "$n" ] && echo si || echo no)"
    # shellcheck disable=SC2086
    [ "$n" -gt 0 ] && timeout 3 docker inspect --format "$PLANTILLA" $pedidos </dev/null 2>/dev/null | cut -b -1200
  else
    echo "fuente error"
  fi
)
cuerpo=$(printf '%s\n' "$cuerpo" | head -n 230)
nlin=$(printf '%s\n' "$cuerpo" | grep -c '')
echo "n $nlin"; printf '%s\n' "$cuerpo"; echo "fin $nlin 0"

echo "### fin_root"
echo "omitidos $(grep -c . "$MARCAS")"
