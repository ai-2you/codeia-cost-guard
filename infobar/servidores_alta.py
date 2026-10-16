"""Alta guiada de un servidor desde Ajustes, SIN contraseñas en la app.

El dueño elige el camino que el propio servidor permite:
  · diagnóstico de solo lectura (1 conexión con su clave SSH de siempre, `BatchMode`): ¿entra?, ¿root o `sudo` sin contraseña?, ¿Debian/Ubuntu con systemd?,
    ¿`AllowUsers`?, ¿ya está instalado?;
  · «listo» → instalación con su clave (una conexión: los tres scripts viajan por stdin en un tar y se instalan con `sudo -n`), prueba como `infobar` y, solo entonces, se
    guarda el servidor;
  · «terminal» → la página enseña la línea de `alta-servidor.sh` para pegar en el Terminal (ahí tecleará su contraseña de sudo) y un botón «Comprobar conexión».
La huella del servidor (primera conexión) NO se acepta sola: se enseña y el dueño confirma; todas las conexiones llevan `StrictHostKeyChecking=yes`.
Nada de lo que dice el servidor llega a la página: solo mensajes de un conjunto cerrado y la huella. Un alta a la vez, y un enfriamiento tras un fallo para no hacer saltar
fail2ban en el servidor del dueño."""
import io
import os
import re
import secrets
import shlex
import subprocess
import tarfile
import threading
import time

from . import plataforma, servidores_ssh
from .config_y_estado import log, modificar_servidores
from .servidores import servidores_validos
from .servidores_registro import nuevo_id

REMOTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remoto")
SCRIPTS = ("instalar-en-servidor.sh", "recoger.sh", "recoger-root.sh")
CONOCIDAS = os.path.expanduser("~/.ssh/known_hosts")
_USUARIO = re.compile(r"[A-Za-z_][A-Za-z0-9._-]{0,31}")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
_NOMBRE = re.compile(r"[\w .-]{1,40}")
_CLAVE_PUBLICA = re.compile(r"ssh-ed25519 [A-Za-z0-9+/=]{60,80}( [A-Za-z0-9@._-]{1,60})?")
_LINEA_HOST = re.compile(r"(\[[A-Za-z0-9.-]{1,253}\]:[0-9]{1,5}|[A-Za-z0-9][A-Za-z0-9.-]{0,252}) ssh-ed25519 [A-Za-z0-9+/=]{60,100}")
_HUELLA = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
ENFRIAMIENTO_S = 60
VIDA_S = 15 * 60
MAX_ALTAS = 5

MENSAJES = {
    "diagnosticando": "Comprobando el servidor (solo lectura)…",
    "huella": "Primera conexión a este servidor: compara esta huella con la del propio servidor antes de aceptarla.",
    "listo": "El servidor admite la instalación con tu clave SSH.",
    "listo_actualiza": "El servidor ya tiene la lectura instalada: se actualizarán los scripts.",
    "terminal_sudo": "Tu usuario necesita contraseña para sudo. Haz el alta en el Terminal (la contraseña no pasa por esta página).",
    "terminal_auth": "No se pudo entrar con tu clave SSH. Haz el alta en el Terminal.",
    "no_soportado": "De momento solo se admiten Debian y Ubuntu (y derivadas con apt) con systemd.",
    "falta_sudo": "El servidor no tiene sudo instalado (la lectura de Docker y fail2ban lo necesita). Instálalo (apt install sudo) y repite.",
    "demasiadas": "Hay demasiadas altas sin terminar: espera unos minutos o repite la última.",
    "allowusers": "El servidor limita quién puede entrar por SSH (AllowUsers/AllowGroups): añade «infobar» allí antes del alta.",
    "huella_cambio": "La huella de este servidor ha cambiado respecto a la guardada. No sigas: puede ser un cambio legítimo o un ataque. Revisa tu known_hosts.",
    "sin_red": "No se pudo conectar con el servidor (red, host o puerto).",
    "timeout": "El servidor tardó demasiado en responder.",
    "instalando": "Instalando la lectura en el servidor…",
    "probando": "Probando la lectura como «infobar»…",
    "fallo_instalacion": "La instalación no terminó bien. El servidor no se ha añadido.",
    "fallo_prueba": "Se instaló pero la prueba de lectura como «infobar» falló (¿AllowUsers? ¿otro puerto?). El servidor no se ha añadido.",
    "fallo_huella": "No se pudo obtener la huella del servidor.",
    "hecho": "Servidor añadido. La barra lo leerá en el próximo ciclo (unos 5 minutos).",
    "nombre_repetido": "Ya hay un servidor con ese nombre.",
    "espera": "Espera unos segundos antes de reintentar (así no se bloquea tu servidor por intentos seguidos).",
    "ocupado": "Ya hay un alta en marcha.",
    "entrada_invalida": "Revisa los datos: nombre, usuario@host y puerto.",
    "desconocida": "Esa alta ya no existe: empieza de nuevo.",
    "estado_incorrecto": "Esa alta no está en el paso que se pide.",
    "sin_clave": "No se pudo preparar la clave SSH dedicada.",
    "interno": "Fallo interno del alta (queda anotado en el log).",
}

# Lo único que se ejecuta en el servidor para diagnosticar: texto fijo, sin nada del usuario. Cada línea es «clave valor» de un conjunto cerrado.
DIAGNOSTICO = (
    'u=$(id -u); echo "uid $u"; '
    'if [ "$u" = 0 ]; then echo "privilegio root"; '
    'elif id -nG | tr " " "\\n" | grep -qxE "sudo|wheel|admin" && sudo -n true 2>/dev/null; then echo "privilegio sudo"; else echo "privilegio ninguno"; fi; '
    '. /etc/os-release 2>/dev/null; echo "so ${ID:-x} ${VERSION_ID:-0}"; '
    '[ -d /run/systemd/system ] && echo "systemd si" || echo "systemd no"; '
    'command -v apt-get >/dev/null 2>&1 && echo "apt si" || echo "apt no"; '
    'command -v visudo >/dev/null 2>&1 && echo "visudo si" || echo "visudo no"; '
    'id infobar >/dev/null 2>&1 && echo "infobar si" || echo "infobar no"; '
    '[ -x /usr/local/bin/infobar-recoger ] && echo "scripts si" || echo "scripts no"; '
    '( sshd -T 2>/dev/null || cat /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null ) | grep -qiE "^[[:space:]]*(allowusers|allowgroups)[[:space:]]" '
    '&& echo "allowusers si" || echo "allowusers no"; '
    'echo "fin"')
_SO = re.compile(r"so ([a-z0-9._-]{1,32}) ([0-9][0-9.]{0,15}|0)")

_ALTAS = {}
_CERROJO = threading.Lock()
_ENFRIA_HASTA = [0.0]


class AltaError(Exception):
    """Un fallo del alta con su mensaje de MENSAJES."""
    def __init__(self, clave):
        super().__init__(clave)
        self.clave = clave


class Alta:
    def __init__(self, nombre, usuario, host, puerto):
        self.id = secrets.token_urlsafe(12)
        self.nombre, self.usuario, self.host, self.puerto = nombre, usuario, host, puerto
        self.estado, self.mensaje = "en_curso", "diagnosticando"
        self.huella, self.linea_host, self.ya_instalado, self.como_root = None, None, False, False
        self.creado = self.actualizado = time.time()
        self.hilo = None

    def pon(self, estado, mensaje):
        self.estado, self.mensaje, self.actualizado = estado, mensaje, time.time()

    def publico(self):
        d = {"id": self.id, "estado": self.estado, "mensaje": MENSAJES[self.mensaje], "codigo": self.mensaje, "ya_instalado": self.ya_instalado}
        if self.estado == "huella":
            d["huella"] = self.huella
            d["comprobar_en_servidor"] = "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"
        if self.estado in ("terminal", "listo", "error"):
            d["linea_terminal"] = self.linea_terminal()
        return d

    def linea_terminal(self):
        destino = f"{self.usuario}@{self.host}"
        return f"bash {shlex.quote(os.path.join(REMOTO, 'alta-servidor.sh'))} {destino}" + (f" {self.puerto}" if self.puerto != 22 else "")


# ---------------------------------------------------------------- utilidades

def _ejecuta(orden, entrada=None, tope=30):
    """(código, salida, error) de un programa, con tope de tiempo y de tamaño. Nunca lanza: un fallo de ejecución es código None."""
    try:
        # sin entrada, stdin es /dev/null: ssh no hereda (ni se queda leyendo) el stdin del proceso que lanza el alta
        r = subprocess.run(orden, input=entrada, stdin=subprocess.DEVNULL if entrada is None else None, capture_output=True, timeout=tope,
                           **plataforma.sin_ventana_kwargs())
    except subprocess.TimeoutExpired:
        return None, b"", b"timeout"
    except OSError as e:
        return None, b"", type(e).__name__.encode()
    return r.returncode, r.stdout[:65536], r.stderr[:8192]


def _orden_ssh(job, comando, usuario=None, clave=None):
    # -F /dev/null: se ignora el ~/.ssh/config del dueño (alias, ProxyJump, Match exec, otro known_hosts...). Así la huella que se enseña es EXACTAMENTE la que ssh aplica
    # y nada de esa configuración puede cambiar a dónde se conecta; si el servidor necesita esa configuración, el camino del Terminal sigue usándola.
    base = ["ssh", "-F", os.devnull, "-T", "-p", str(job.puerto), *servidores_ssh.OPCIONES_SEGURAS]
    if clave:
        base += ["-i", clave, "-o", "IdentitiesOnly=yes"]
    return base + ["--", f"{usuario or job.usuario}@{job.host}", comando]


def _spec_host(job):
    return job.host if job.puerto == 22 else f"[{job.host}]:{job.puerto}"


def _error_ssh(stderr):
    c = servidores_ssh.clasifica_error(255, stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr)
    return {"clave de host no confiada": "huella_cambio", "autenticacion": "terminal_auth", "timeout": "timeout", "red": "sin_red"}.get(c, "sin_red")


def _conocida(job):
    codigo, salida, _ = _ejecuta(["ssh-keygen", "-F", _spec_host(job), "-f", CONOCIDAS], tope=10)
    return codigo == 0 and bool(salida.strip())


def _escanea(job):
    """(línea de known_hosts, huella SHA256) de la clave ed25519 del servidor, con UNA conexión; AltaError si no se puede."""
    codigo, salida, _ = _ejecuta(["ssh-keyscan", "-T", "5", "-t", "ed25519", "-p", str(job.puerto), "--", job.host], tope=15)
    lineas = [l.strip() for l in salida.decode("ascii", "replace").splitlines() if l.strip() and not l.startswith("#")]
    if codigo != 0 or len(lineas) != 1 or not _LINEA_HOST.fullmatch(lineas[0]):
        raise AltaError("fallo_huella")
    c2, h, _ = _ejecuta(["ssh-keygen", "-lf", "-", "-E", "sha256"], entrada=(lineas[0] + "\n").encode(), tope=10)
    m = _HUELLA.search(h.decode("ascii", "replace")) if c2 == 0 else None
    if not m:
        raise AltaError("fallo_huella")
    return lineas[0], m.group(0)


def _anade_conocida(linea):
    if not _LINEA_HOST.fullmatch(linea):
        raise AltaError("fallo_huella")
    os.makedirs(os.path.dirname(CONOCIDAS), mode=0o700, exist_ok=True)
    try:
        with open(CONOCIDAS, encoding="ascii", errors="replace") as f:
            if linea in (l.strip() for l in f):
                return                                            # ya estaba: no se duplica
    except OSError:
        pass
    fd = os.open(CONOCIDAS, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="ascii") as f:
        f.write(linea + "\n")


def _parsea_diagnostico(texto):
    d = {}
    for l in texto.decode("utf-8", "replace").splitlines():
        p = l.split()
        if l == "fin":
            d["fin"] = True
        elif len(p) == 2 and p[0] == "uid" and p[1].isascii() and p[1].isdigit() and len(p[1]) <= 10:
            d["uid"] = int(p[1])
        elif len(p) == 2 and p[0] == "privilegio" and p[1] in ("root", "sudo", "ninguno"):
            d["privilegio"] = p[1]
        elif l.startswith("so ") and _SO.fullmatch(l):
            d["so"] = _SO.fullmatch(l).group(1)
        elif len(p) == 2 and p[0] in ("systemd", "apt", "visudo", "infobar", "scripts", "allowusers") and p[1] in ("si", "no"):
            d[p[0]] = p[1] == "si"
    return d


def _clasifica(d):
    """(estado, mensaje) a partir del diagnóstico."""
    if not d.get("fin") or "privilegio" not in d:
        raise AltaError("sin_red")
    if not (d.get("systemd") and d.get("apt")):
        return "no_soportado", "no_soportado"
    if not d.get("visudo"):
        return "no_soportado", "falta_sudo"                       # sin sudo/visudo la instalación fallaría a medias (usuario y scripts sí, la regla de sudo no)
    if d["privilegio"] == "ninguno":
        return "terminal", "terminal_sudo"
    if d.get("allowusers"):
        return "terminal", "allowusers"
    return "listo", "listo_actualiza" if d.get("infobar") and d.get("scripts") else "listo"


def _destino_valido(cuerpo):
    if not isinstance(cuerpo, dict):
        raise AltaError("entrada_invalida")
    nombre, ssh, puerto = cuerpo.get("nombre"), cuerpo.get("ssh"), cuerpo.get("puerto", 22)
    if not (isinstance(nombre, str) and _NOMBRE.fullmatch(nombre) and isinstance(ssh, str) and "@" in ssh):
        raise AltaError("entrada_invalida")
    usuario, _, host = ssh.partition("@")
    if not (_USUARIO.fullmatch(usuario) and _HOST.fullmatch(host)) or isinstance(puerto, bool) or not isinstance(puerto, int) or not 0 < puerto < 65536:
        raise AltaError("entrada_invalida")
    return nombre, usuario, host, puerto


# ---------------------------------------------------------------- pasos (cada uno en el hilo del alta)

def _diagnostico(job):
    if not _conocida(job):
        job.linea_host, job.huella = _escanea(job)
        job.pon("huella", "huella")
        return
    codigo, salida, err = _ejecuta(_orden_ssh(job, DIAGNOSTICO), tope=30)
    if codigo != 0:
        raise AltaError(_error_ssh(err))
    d = _parsea_diagnostico(salida)
    estado, mensaje = _clasifica(d)
    job.ya_instalado = bool(d.get("infobar") and d.get("scripts"))
    job.como_root = d.get("privilegio") == "root"                     # el que dice el servidor (uid 0), no el nombre de usuario
    job.pon(estado, mensaje)


def _asegurar_clave():
    """Ruta de la clave dedicada y su parte pública validada; la crea (ed25519, sin passphrase, 0600) si falta."""
    clave = servidores_ssh.CLAVE
    if not os.path.exists(clave):
        os.makedirs(os.path.dirname(clave), mode=0o700, exist_ok=True)
        codigo, _, _ = _ejecuta(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "infobar", "-f", clave], tope=20)
        if codigo != 0:
            raise AltaError("sin_clave")
    try:
        with open(clave + ".pub", encoding="ascii") as f:
            publica = f.read().strip()
    except (OSError, UnicodeDecodeError):
        raise AltaError("sin_clave")
    if not _CLAVE_PUBLICA.fullmatch(publica):
        raise AltaError("sin_clave")
    return clave, publica


def _paquete():
    """tar en memoria con los tres scripts (0755, root) que van por stdin; nada se escribe en disco."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for nombre in SCRIPTS:
            with open(os.path.join(REMOTO, nombre), "rb") as f:
                datos = f.read()
            ti = tarfile.TarInfo(nombre)
            ti.size, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname, ti.mtime = len(datos), 0o755, 0, 0, "root", "root", int(time.time())
            t.addfile(ti, io.BytesIO(datos))
    return buf.getvalue()


def comando_instalacion(publica, como_root):
    """La orden remota: directorio temporal propio, desempaqueta, instala (con `sudo -n` si no es root) y limpia. La clave pública ya está validada con regex."""
    if not _CLAVE_PUBLICA.fullmatch(publica):
        raise AltaError("sin_clave")
    sudo = "" if como_root else "sudo -n "
    return ('sh -c \'set -e; d=$(mktemp -d /tmp/infobar-alta.XXXXXX); trap "rm -rf $d" EXIT; tar -xf - -C "$d"; '
            f'{sudo}bash "$d/instalar-en-servidor.sh" "{publica}"\'')


def _instalar(job, como_root):
    clave, publica = _asegurar_clave()
    job.pon("en_curso", "instalando")
    codigo, _, err = _ejecuta(_orden_ssh(job, comando_instalacion(publica, como_root)), entrada=_paquete(), tope=90)
    if codigo != 0:
        raise AltaError(_error_ssh(err) if codigo in (None, 255) else "fallo_instalacion")      # 255 = ssh no llegó a ejecutar nada (red, huella, autenticación)
    job.pon("en_curso", "probando")
    _probar_y_guardar(job)


def _probar_y_guardar(job):
    salida, error = servidores_ssh.ejecutar({"nombre": job.nombre, "ssh": f"infobar@{job.host}", "puerto": job.puerto})
    sec, _, _, _ = servidores_ssh.analizar(salida)
    if error or "version" not in sec:
        raise AltaError("fallo_prueba")
    _guardar_servidor(job)
    job.pon("hecho", "hecho")


def _guardar_servidor(job):
    nuevo = {"nombre": job.nombre, "ssh": f"infobar@{job.host}", "id": nuevo_id()}
    if job.puerto != 22:
        nuevo["puerto"] = job.puerto
    if not servidores_validos([nuevo]):
        raise AltaError("entrada_invalida")

    def anadir(lista):
        for s in lista:
            if isinstance(s, dict) and s.get("nombre") == job.nombre:
                if s.get("ssh") == nuevo["ssh"] and int(s.get("puerto") or 22) == job.puerto:
                    return lista                       # ya estaba: repetir el alta no lo duplica
                raise AltaError("nombre_repetido")
        return lista + [nuevo]
    modificar_servidores(anadir)


def _corre(job, paso, *args):
    """Ejecuta un paso en un hilo; un AltaError deja el alta en error con su mensaje y arma el enfriamiento si fue un fallo de conexión o de instalación."""
    def tarea():
        try:
            paso(job, *args)
        except AltaError as e:
            job.pon("error", e.clave)
            if e.clave in ("sin_red", "timeout", "terminal_auth", "fallo_instalacion", "fallo_prueba", "fallo_huella", "huella_cambio"):
                _ENFRIA_HASTA[0] = time.time() + ENFRIAMIENTO_S
        except Exception as e:                          # nada del servidor ni del entorno llega a la página
            log(f"alta de servidor: fallo interno ({type(e).__name__})")
            job.pon("error", "interno")
    job.pon("en_curso", job.mensaje)
    job.hilo = threading.Thread(target=tarea, name="infobar-alta", daemon=True)
    job.hilo.start()


# ---------------------------------------------------------------- interfaz para el servidor local

def _limpia(ahora):
    for i in [i for i, a in _ALTAS.items() if ahora - a.actualizado > VIDA_S and a.estado != "en_curso"]:
        del _ALTAS[i]


def _busca(cuerpo, *estados):
    a = _ALTAS.get(cuerpo.get("id")) if isinstance(cuerpo, dict) and isinstance(cuerpo.get("id"), str) else None
    if a is None:
        raise AltaError("desconocida")
    if estados and a.estado not in estados:
        raise AltaError("estado_incorrecto")
    return a


def _respuesta(a=None, error=None):
    if error:
        return (429 if error in ("espera", "ocupado", "demasiadas") else 400), {"ok": False, "codigo": error, "mensaje": MENSAJES[error]}
    return 200, {"ok": True, **a.publico()}


def atiende(accion, cuerpo, ahora=None):
    """(código HTTP, dict) para /servidores/alta/<accion>. Nunca lanza."""
    ahora = ahora if ahora is not None else time.time()
    try:
        with _CERROJO:
            _limpia(ahora)
            if accion == "diagnostico":
                nombre, usuario, host, puerto = _destino_valido(cuerpo)
                if any(a.estado == "en_curso" for a in _ALTAS.values()):
                    raise AltaError("ocupado")
                if ahora < _ENFRIA_HASTA[0]:
                    raise AltaError("espera")
                if sum(1 for x in _ALTAS.values() if x.estado in ("en_curso", "huella", "listo", "terminal")) >= MAX_ALTAS:
                    raise AltaError("demasiadas")                # las terminadas (hecho, error, no soportado) no cuentan
                a = Alta(nombre, usuario, host, puerto)
                _ALTAS[a.id] = a
                _corre(a, _diagnostico)
                return _respuesta(a)
            if accion == "huella":
                a = _busca(cuerpo, "huella")
                if cuerpo.get("acepto") is not True:
                    raise AltaError("entrada_invalida")
                _corre(a, _acepta_huella)
                return _respuesta(a)
            if accion == "instalar":
                a = _busca(cuerpo, "listo")
                if ahora < _ENFRIA_HASTA[0]:
                    raise AltaError("espera")
                _corre(a, _instalar_con_privilegio)
                return _respuesta(a)
            if accion == "comprobar":
                a = _busca(cuerpo, "terminal", "error", "listo")
                if ahora < _ENFRIA_HASTA[0]:
                    raise AltaError("espera")
                _corre(a, _probar_y_guardar)
                return _respuesta(a)
            if accion == "estado":
                return _respuesta(_busca(cuerpo))
        return 404, {"ok": False}
    except AltaError as e:
        return _respuesta(error=e.clave)
    except Exception as e:
        log(f"alta de servidor: fallo interno ({type(e).__name__})")
        return _respuesta(error="interno")


def _acepta_huella(job):
    """Añade EXACTAMENTE la línea cuya huella se enseñó y vuelve a diagnosticar (ya con la huella conocida)."""
    _anade_conocida(job.linea_host)
    job.linea_host = None
    _diagnostico(job)


def _instalar_con_privilegio(job):
    _instalar(job, como_root=job.como_root)
