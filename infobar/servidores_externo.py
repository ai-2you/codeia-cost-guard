"""Lo que se mide DESDE el Mac, no desde el servidor: latencia de las webs y puertos que de verdad
se alcanzan desde Internet (dentro del servidor no se ve el cortafuegos del proveedor).

Antes de conectar se resuelve el nombre y se rechaza cualquier IP no publica; despues se conecta a
ESA IP (con SNI y verificacion del certificado por nombre). Asi un DNS que cambia entre la
comprobacion y la conexion (rebinding, nip.io) no lleva la peticion a 127.0.0.1 ni a la red local.
"""
import http.client, socket, ssl, subprocess, time, urllib.parse

from .servidores import ip_no_publica, url_valida

TIMEOUT_WEB = 5
TIMEOUT_PUERTO = 1.5
# puertos que se esperan abiertos al publico; el resto, si responde desde fuera, es un aviso
PUERTOS_PUBLICOS = {22, 80, 443}


def ip_publica(host, puerto):
    """Primera IP publica a la que resuelve host, o error si alguna no lo es."""
    infos = socket.getaddrinfo(host, puerto, proto=socket.IPPROTO_TCP)
    ips = [i[4][0] for i in infos]
    if not ips:
        raise OSError("no resuelve")
    if any(ip_no_publica(ip) for ip in ips):
        raise OSError("resuelve a una IP local o privada")
    return ips[0]


class _HTTPSFijo(http.client.HTTPSConnection):
    """HTTPS contra una IP ya comprobada, validando el certificado del nombre original."""

    def __init__(self, host, ip, puerto, timeout):
        super().__init__(host, puerto, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _HTTPFijo(http.client.HTTPConnection):
    def __init__(self, host, ip, puerto, timeout):
        super().__init__(host, puerto, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


def medir_web(url):
    """{"url", "ms", "codigo", "error"}. HEAD sin seguir redirecciones (una 301 tambien es 'arriba')."""
    res = {"url": url[:120], "ms": None, "codigo": None, "error": ""}
    motivo = url_valida(url)
    if motivo:
        res["error"] = motivo
        return res
    u = urllib.parse.urlsplit(url)
    puerto = u.port or (443 if u.scheme == "https" else 80)
    try:
        ip = ip_publica(u.hostname, puerto)
        clase = _HTTPSFijo if u.scheme == "https" else _HTTPFijo
        c = clase(u.hostname, ip, puerto, TIMEOUT_WEB)
        t0 = time.time()
        c.request("HEAD", (u.path or "/") + (f"?{u.query}" if u.query else ""), headers={"User-Agent": "infobar/1.0"})
        r = c.getresponse()
        res["ms"] = int((time.time() - t0) * 1000)
        res["codigo"] = r.status
        c.close()
    except ssl.SSLError as e:
        res["error"] = f"certificado: {getattr(e, 'reason', '') or 'no valido'}"
    except (OSError, ValueError, http.client.HTTPException) as e:
        res["error"] = str(e)[:80] or type(e).__name__
    return res


def host_de_ssh(destino, puerto=22):
    """Host real (y puerto) de usuario@host o de un alias de ~/.ssh/config, via `ssh -G`."""
    try:
        r = subprocess.run(["ssh", "-G", "-p", str(puerto), "--", destino], capture_output=True,
                           text=True, timeout=5)
        cfg = dict(l.split(" ", 1) for l in r.stdout.splitlines() if " " in l)
        return cfg.get("hostname") or destino.split("@")[-1]
    except Exception:
        return destino.split("@")[-1]


MAX_PUERTOS = 64


def puertos_expuestos(host, puertos):
    """De los puertos que escuchan en el servidor, cuales responden desde aqui (Internet).
    En paralelo y con tope: la lista viene del servidor y no debe poder alargar el ciclo."""
    import concurrent.futures
    try:
        ip = ip_publica(host, 22)
    except (OSError, ValueError):
        return None

    def abierto(p):
        try:
            with socket.create_connection((ip, p), TIMEOUT_PUERTO):
                return p
        except OSError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        return sorted(p for p in pool.map(abierto, list(puertos)[:MAX_PUERTOS]) if p)
