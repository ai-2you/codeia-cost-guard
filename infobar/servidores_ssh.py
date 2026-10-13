"""Conexion SSH a un servidor y lectura de la salida de infobar-recoger.

La clave dedicada solo puede ejecutar el script del servidor (command= en authorized_keys), asi que
aqui no se envia ninguna orden: se conecta y se lee. Todo con tope de tiempo y de tamano, y sin
heredar nada peligroso de ~/.ssh/config (ProxyCommand, LocalCommand, reenvios).
"""
import math, os, re, signal, subprocess, time

from .servidores import errores_servidores

CLAVE = os.path.expanduser("~/.ssh/infobar_ed25519")
TOPE_S = 20                 # segundos por servidor, conexion incluida
TOPE_BYTES = 64 * 1024
SECCIONES = {"version", "sistema", "memoria", "disco", "sesiones", "puertos", "intentos",
             "actualizaciones", "bastionado", "servicios", "contenedores", "bastionado_root", "fail2ban"}


def orden_ssh(servidor):
    """La orden ssh (lista, sin shell). Revalida el servidor justo antes de usarlo."""
    if errores_servidores([servidor]):
        raise ValueError("servidor con configuracion no valida")
    puerto = int(servidor.get("puerto") or 22)
    if not 0 < puerto < 65536:
        raise ValueError("puerto no valido")
    return ["ssh", "-T", "-p", str(puerto),
            "-i", CLAVE, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=3", "-o", "ServerAliveCountMax=2",
            "-o", "StrictHostKeyChecking=yes", "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
            "-o", "ClearAllForwardings=yes", "-o", "PermitLocalCommand=no", "-o", "ProxyCommand=none",
            "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "LogLevel=ERROR",
            "--", servidor["ssh"]]


def clasifica_error(codigo, stderr):
    """Motivo legible de un fallo de ssh, para la tarjeta del servidor."""
    e = (stderr or "").lower()
    if "host key verification failed" in e or "no matching host key" in e or "host key for" in e:
        return "clave de host no confiada"
    if "permission denied" in e or "too many authentication failures" in e:
        return "autenticacion"
    if "timed out" in e or "timeout" in e:
        return "timeout"
    if "could not resolve" in e or "no route" in e or "network is unreachable" in e or "refused" in e:
        return "red"
    if "identity file" in e and "not accessible" in e:
        return "falta la clave ~/.ssh/infobar_ed25519"
    return f"ssh salio con {codigo}"


TOPE_ERR = 8 * 1024


def _lee_con_tope(flujo, tope, destino, excedido):
    """Lee `flujo` por trozos y se queda con `tope` bytes como mucho. Si el otro lado manda mas,
    marca `excedido` (el hilo principal mata ssh) y sigue leyendo para descartar, sin acumular."""
    total = 0
    for trozo in iter(lambda: flujo.read(4096), b""):
        if total < tope:
            destino.append(trozo[:tope - total])
        total += len(trozo)
        if total > tope:
            excedido.set()
    flujo.close()


def _mata(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)       # el grupo entero: no quedan ssh huerfanos
    except OSError:
        pass


def ejecutar(servidor):
    """(salida, error): salida es el texto del script; error "" si fue bien.

    La salida se lee mientras llega y con tope (TOPE_BYTES): un servidor que mande de mas no puede
    llenar la memoria antes de recortar. Si se pasa del tope, se corta la conexion."""
    import threading
    try:
        orden = orden_ssh(servidor)
    except ValueError as e:
        return "", str(e)
    try:
        p = subprocess.Popen(orden, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        return "", f"no se pudo lanzar ssh: {e}"
    salida, err, excedido = [], [], threading.Event()
    hilos = [threading.Thread(target=_lee_con_tope, args=(p.stdout, TOPE_BYTES, salida, excedido), daemon=True),
             threading.Thread(target=_lee_con_tope, args=(p.stderr, TOPE_ERR, err, threading.Event()), daemon=True)]
    for h in hilos:
        h.start()
    limite = time.time() + TOPE_S
    while p.poll() is None and time.time() < limite and not excedido.is_set():
        time.sleep(0.05)
    if p.poll() is None:
        _mata(p)
        p.wait()
        for h in hilos:
            h.join(2)
        return "", ("salida demasiado grande" if excedido.is_set() else "timeout")
    for h in hilos:
        h.join(2)
    texto = b"".join(salida).decode("utf-8", "replace")
    if excedido.is_set():
        return "", "salida demasiado grande"
    if p.returncode != 0 and "### version" not in texto:
        return "", clasifica_error(p.returncode, b"".join(err).decode("utf-8", "replace"))
    return texto, ""


def secciones(texto):
    """'### nombre' -> lineas. Solo las secciones conocidas; el resto se ignora."""
    out, actual = {}, None
    for linea in (texto or "").splitlines():
        if linea.startswith("### "):
            nombre = linea[4:].strip()
            actual = nombre if nombre in SECCIONES else None
            if actual:
                out[actual] = []
        elif actual:
            out[actual].append(linea.rstrip())
    return out


def _num(x, defecto=0.0):
    """Numero finito o el defecto (float() acepta 'nan' e 'inf', que luego rompen int())."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return defecto
    return v if math.isfinite(v) else defecto


def _clave_valor(lineas):
    d = {}
    for l in lineas:
        k, _, v = l.partition(" ")
        if k:
            d[k] = v.strip()
    return d


_IP = re.compile(r"^[0-9a-fA-F.:]{3,45}$")


def interpretar(sec):
    """Secciones -> datos del servidor (numeros y listas cortas, listos para pintar)."""
    d = {}
    s = sec.get("sistema") or []
    if len(s) >= 2:
        carga = _num(s[0].split()[0]) if s[0].split() else 0.0
        nucleos = int(_num(s[1], 1)) if s[1].strip().isdecimal() else 0
        if 0 < nucleos <= 1024:                     # si nproc no salio, la linea es otra cosa
            d["cpu"] = {"pct": round(min(100.0, 100.0 * carga / nucleos), 1), "carga": carga, "nucleos": nucleos}
    if len(s) >= 3 and "cpu" in d:
        d["uptime_s"] = int(_num(s[2]))
    m = (sec.get("memoria") or [""])[0].split()
    if len(m) == 2:
        total, disponible = _num(m[0]), _num(m[1])
        d["memoria"] = {"total": int(total), "usada": int(max(0, total - disponible))}
    k = (sec.get("disco") or [""])[0].split()
    if len(k) == 2:
        d["disco"] = {"total": int(_num(k[0])), "usado": int(_num(k[1]))}
    if "sesiones" in sec:
        ses = []
        for l in sec["sesiones"]:
            partes = l.split()
            if not partes:
                continue
            ip = re.search(r"\(([^)]*)\)", l)
            ses.append({"usuario": partes[0][:32], "desde": (ip.group(1) if ip else "")[:64]})
        d["sesiones"] = ses
    if "puertos" in sec:
        d["puertos"] = sorted({int(p) for p in " ".join(sec["puertos"]).split()
                               if p.isascii() and p.isdecimal() and 0 < int(p) < 65536})
    if "intentos" in sec:
        it = {"total": 0, "hora": 0, "top": []}
        for l in sec["intentos"]:
            p = l.split()
            if len(p) == 2 and p[0] in ("total", "hora"):
                it[p[0]] = int(_num(p[1]))
            elif len(p) == 3 and p[0] == "ip" and _IP.match(p[1]):
                it["top"].append([p[1], int(_num(p[2]))])
        it["top"] = sorted(it["top"], key=lambda x: -x[1])[:4]
        d["intentos"] = it
    a = sec.get("actualizaciones") or []
    if a:
        d["actualizaciones"] = {"pendientes": int(_num(a[0])), "reinicio": len(a) > 1 and a[1] == "reinicio"}
    if "servicios" in sec or "contenedores" in sec:
        d["servicios"] = _servicios(sec.get("servicios") or [], sec.get("contenedores") or [])
    if "fail2ban" in sec:
        f = _clave_valor(sec["fail2ban"])
        d["fail2ban"] = ({"instalado": False} if "no-instalado" in sec["fail2ban"] else
                         {"instalado": True, "ahora": int(_num(f.get("ahora"))), "total": int(_num(f.get("total")))})
    if "bastionado" in sec or "bastionado_root" in sec:
        b = _clave_valor(sec.get("bastionado") or [])
        b.update(_clave_valor(sec.get("bastionado_root") or []))
        d["bastionado"] = {k[:40]: v[:40] for k, v in b.items()}
    d["con_root"] = "bastionado_root" in sec
    return d


def _servicios(servicios, contenedores):
    """Servicios de Swarm (nombre, replicas 'n/m') o, si no hay, contenedores (nombre, estado)."""
    out = []
    for l in servicios:
        nombre, _, rep = l.partition("\t")
        r = re.match(r"(\d+)/(\d+)", rep.strip())
        if nombre and r:
            # n/n bien; 0/0 es escalado a cero a proposito; "(1/1 completed)" es una tarea terminada
            ok = r.group(1) == r.group(2) or "completed" in rep
            out.append({"nombre": nombre[:64], "ok": ok, "detalle": rep.strip()[:16]})
    if out:
        return out
    for l in contenedores:
        partes = l.split("\t")
        if len(partes) >= 2 and partes[0]:
            # un contenedor que termino bien (migraciones, builds) no esta caido
            termino_bien = partes[1] == "exited" and len(partes) > 2 and partes[2].startswith("Exited (0)")
            out.append({"nombre": partes[0][:64], "ok": partes[1] == "running" or termino_bien,
                        "detalle": partes[1][:16]})
    return out


def leer(servidor):
    """Conecta, lee e interpreta. Devuelve (datos, error, segundos)."""
    t0 = time.time()
    if not os.path.exists(CLAVE):
        return None, "falta la clave ~/.ssh/infobar_ed25519", 0.0     # sin intentos contra fail2ban
    salida, error = ejecutar(servidor)
    if error:
        return None, error, round(time.time() - t0, 2)
    try:
        return interpretar(secciones(salida)), "", round(time.time() - t0, 2)
    except Exception as e:                          # una salida inesperada no tumba el ciclo
        return None, f"salida no reconocida ({type(e).__name__})", round(time.time() - t0, 2)
