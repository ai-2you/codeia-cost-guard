"""Webs detectadas en un servidor (sección `webs` de recoger-root v2) y su selección por el dueño.

· Lo que dice el servidor es hostil: cada nombre se valida con la forma de un dominio público (etiquetas DNS, minúsculas, sin comodines, sin IP, sin dominios internos) y lo
  que no encaja se descarta y se cuenta.
· Nada se vigila hasta que el dueño lo marca. Se le ofrecen primero los dominios que RESUELVEN a la IP pública del servidor (los de ese servidor); los que apuntan a otro sitio
  (CDN, otro servidor) van aparte, sin marcar y con aviso: un cliente del servidor podría haber puesto en sus etiquetas el dominio de un tercero.
· Las candidatas viven solo en la caché de servidores (48 h: son datos personales) y se resuelven a petición, no en cada ciclo."""
import concurrent.futures
import ipaddress
import re
import socket
import threading
import time

from .config_y_estado import modificar_servidores
from .servidores import _DNS_INTERNOS as DNS_INTERNOS, ip_no_publica, url_valida

MAX_HOSTS = 200
MAX_WEBS_POR_SERVIDOR = 100
RESOLVER_S = 10
CACHE_S = 600
_ETIQUETA = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_CACHE = {}
_CERROJO = threading.Lock()


def host_valido(h):
    """True si `h` es un nombre DNS público razonable: 2+ etiquetas, minúsculas, ≤ 253, sin comodines, sin IP, sin TLD numérico ni dominio interno."""
    if not isinstance(h, str) or not 3 <= len(h) <= 253 or not h.isascii() or h != h.lower():
        return False
    etiquetas = h.split(".")
    if len(etiquetas) < 2 or not all(_ETIQUETA.fullmatch(e) for e in etiquetas) or etiquetas[-1].isdigit():
        return False
    if any(h == d.lstrip(".") or h.endswith(d) for d in DNS_INTERNOS) or h in ("localhost", "localhost.localdomain"):
        return False
    return url_valida(f"https://{h}/") == ""


def interpretar(lineas, marcada):
    """{"hosts": [...], "total", "truncado", "descartados"} de la sección `webs`, o None si no vale (sin marco, claves repetidas) o sin dato (fuente != ok)."""
    if not marcada:
        return None
    kv, hosts, descartados, vistos = {}, [], 0, set()
    for l in lineas:
        p = l.split()
        if len(p) == 2 and p[0] in ("fuente", "total", "truncado"):
            if p[0] in kv:
                return None
            kv[p[0]] = p[1]
        elif len(p) == 2 and p[0] == "web":
            if host_valido(p[1]) and p[1] not in vistos and len(hosts) < MAX_HOSTS:
                vistos.add(p[1])
                hosts.append(p[1])
            else:
                descartados += 1
        elif l:
            return None
    if kv.get("fuente") != "ok" or kv.get("truncado") not in ("si", "no"):
        return None
    total = int(kv["total"]) if kv.get("total", "").isdigit() and len(kv["total"]) <= 7 else None
    if total is None:
        return None
    return {"hosts": sorted(hosts), "total": total, "truncado": kv["truncado"] == "si", "descartados": descartados}


# ---------------------------------------------------------------- resolución y clasificación

_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _canonica(texto):
    """La IP en su forma comparable: una IPv4 mapeada (::ffff:a.b.c.d), NAT64 (64:ff9b::/96) o 6to4 es la IPv4 que lleva dentro."""
    ip = ipaddress.ip_address(texto)
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if ip.sixtofour is not None:
            return ip.sixtofour
    return ip


def _ips(host, resolver):
    try:
        return {_canonica(i[4][0]) for i in resolver(host)}
    except (OSError, ValueError):
        return set()


def _resolver_real(host):
    return socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)


def resolver_todos(hosts, resolver=None, tope=RESOLVER_S):
    """{host: {ips}} con un presupuesto global; lo que no llega a resolverse queda fuera (sin_resolver). `getaddrinfo` no tiene tiempo máximo propio: hilos daemon."""
    out = {}
    resolver = resolver or _resolver_real
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="infobar-dns")
    futuros = {pool.submit(_ips, h, resolver): h for h in hosts}
    try:
        for f in concurrent.futures.as_completed(futuros, timeout=tope):
            out[futuros[f]] = f.result()
    except concurrent.futures.TimeoutError:
        pass
    pool.shutdown(wait=False, cancel_futures=True)
    return out


def clasificar(host_servidor, hosts, vigiladas=(), resolver=None, tope=RESOLVER_S):
    """Reparte los dominios candidatos: «apuntan» (resuelven a la IP del servidor), «otros» (resuelven a otro sitio público), «internos» (resuelven a una IP
    local o privada: no se ofrecen), «sin_resolver» y «vigiladas» (ya en la lista)."""
    ya = {url_host(u) for u in vigiladas}
    pendientes = [h for h in hosts if h not in ya]
    todo = resolver_todos([host_servidor, *pendientes], resolver, tope)
    del_servidor = todo.get(host_servidor, set())
    out = {"apuntan": [], "otros": [], "internos": [], "sin_resolver": [], "vigiladas": sorted(h for h in hosts if h in ya)}
    for h in pendientes:
        ips = todo.get(h)
        if not ips:
            out["sin_resolver"].append(h)
        elif any(ip_no_publica(str(i)) for i in ips):
            out["internos"].append(h)
        elif ips & del_servidor:
            out["apuntan"].append(h)
        else:
            out["otros"].append(h)
    return out


def url_host(u):
    m = re.fullmatch(r"https?://([^/:?#]+)(?::\d+)?(?:[/?#].*)?", str(u or ""))
    return m.group(1).lower() if m else ""


def decodificado(h):
    """El dominio como lo escribiría una persona (los `xn--` se ven en Unicode junto al original: un homógrafo no pasa desapercibido); None si no es punycode."""
    if "xn--" not in h:
        return None
    try:
        return h.encode("ascii").decode("idna")
    except (UnicodeError, ValueError):
        return None


def detectadas(servidor, entrada_cache, host_real, ahora=None, resolver=None):
    """La respuesta de «Buscar webs» para un servidor: candidatas clasificadas, con la antigüedad del dato. Se guarda 10 min (resolver 200 nombres no es gratis)."""
    ahora = ahora if ahora is not None else time.time()
    if servidor.get("detectar_webs") is False:
        return {"estado": "desactivada"}
    det = ((entrada_cache or {}).get("datos") or {}).get("webs_detectadas")
    if not isinstance(det, dict) or not isinstance(det.get("hosts"), list):
        return {"estado": "sin_dato"}
    clave = (servidor.get("id"), tuple(det["hosts"]), tuple(servidor.get("webs") or []), host_real)
    with _CERROJO:
        previo = _CACHE.get(servidor.get("id"))
        if previo and previo[0] == clave and ahora - previo[1] < CACHE_S:
            return previo[2]
    c = clasificar(host_real, det["hosts"], servidor.get("webs") or [], resolver)
    r = {"estado": "ok", "truncado": bool(det.get("truncado")), "total": det.get("total"), "edad_s": max(0, int(ahora - ((entrada_cache or {}).get("ok_en") or ahora))),
         **{k: [{"host": h, "unicode": decodificado(h)} for h in v] for k, v in c.items()}}
    with _CERROJO:
        _CACHE[servidor.get("id")] = (clave, ahora, r)
    return r


# ---------------------------------------------------------------- aceptar / quitar (por id, dentro del cerrojo de servidores.json)

class ErrorWebs(Exception):
    pass


def cambiar(servidor_id, aceptar, quitar, permitidos):
    """Añade y quita webs de un servidor por `id`. `aceptar` = dominios (solo los que estaban en la detección: `permitidos`); `quitar` = dominios o URLs ya vigilados.
    Devuelve (lista nueva de webs, lista de servidores). Lanza ErrorWebs con un mensaje cerrado."""
    if not (isinstance(aceptar, list) and isinstance(quitar, list) and len(aceptar) <= MAX_HOSTS and len(quitar) <= MAX_WEBS_POR_SERVIDOR + MAX_HOSTS):
        raise ErrorWebs("entrada_invalida")
    if not all(isinstance(x, str) for x in [*aceptar, *quitar]):
        raise ErrorWebs("entrada_invalida")
    if any(h not in permitidos or not host_valido(h) for h in aceptar):
        raise ErrorWebs("no_detectada")
    resultado = {}

    def f(lista):
        for s in lista:
            if isinstance(s, dict) and s.get("id") == servidor_id:
                webs = [u for u in s.get("webs") or [] if url_host(u) not in {url_host(q) or q for q in quitar}]
                for h in aceptar:
                    u = f"https://{h}/"
                    if u not in webs and h not in {url_host(w) for w in webs}:
                        webs.append(u)
                if len(webs) > MAX_WEBS_POR_SERVIDOR:
                    raise ErrorWebs("demasiadas")
                s = dict(s)
                if webs:
                    s["webs"] = webs
                else:
                    s.pop("webs", None)
                resultado["webs"] = webs
                return [s if (isinstance(x, dict) and x.get("id") == servidor_id) else x for x in lista]
        raise ErrorWebs("servidor_desconocido")
    nueva = modificar_servidores(f)
    return resultado["webs"], nueva


# ---------------------------------------------------------------- interfaz para el servidor local (nunca lanza; mensajes cerrados)

MENSAJES = {
    "entrada_invalida": "Revisa los datos enviados.",
    "no_detectada": "Ese dominio no está entre los detectados en el servidor.",
    "demasiadas": f"Como mucho {MAX_WEBS_POR_SERVIDOR} webs por servidor.",
    "servidor_desconocido": "Ese servidor ya no está en la lista: recarga la página.",
    "ocupado": "Ya se está buscando en otro servidor: espera un momento.",
}
_ID = re.compile(r"[0-9a-f]{32}")
_BUSCANDO = threading.Lock()


def _servidor_por_id(servidor_id):
    from .config_y_estado import cfg
    from .servidores import servidores_validos
    return next((s for s in servidores_validos(cfg().get("servidores")) if s.get("id") == servidor_id), None)


def atiende_detectadas(servidor_id, ahora=None, resolver=None, host_real=None):
    """GET /servidores/webs/detectadas?servidor=<id> → (código, dict)."""
    if not isinstance(servidor_id, str) or not _ID.fullmatch(servidor_id):
        return 400, {"ok": False, "codigo": "entrada_invalida", "mensaje": MENSAJES["entrada_invalida"]}
    s = _servidor_por_id(servidor_id)
    if s is None:
        return 404, {"ok": False, "codigo": "servidor_desconocido", "mensaje": MENSAJES["servidor_desconocido"]}
    if not _BUSCANDO.acquire(blocking=False):
        return 429, {"ok": False, "codigo": "ocupado", "mensaje": MENSAJES["ocupado"]}
    try:
        from . import servidores_ciclo
        from .servidores_externo import host_de_ssh
        entrada = (servidores_ciclo.leer_cache().get("servidores") or {}).get(s["nombre"])
        host = host_real if host_real is not None else host_de_ssh(s["ssh"], s.get("puerto") or 22)
        return 200, {"ok": True, **detectadas(s, entrada, host, ahora, resolver)}
    finally:
        _BUSCANDO.release()


def atiende_cambio(cuerpo):
    """POST /servidores/webs {servidor, aceptar: [dominios], quitar: [dominios o URLs]} → (código, dict)."""
    from .config_y_estado import cfg
    from .servidores import revision_servidores
    from .servidor_local import sin_secretos
    try:
        if not isinstance(cuerpo, dict) or not isinstance(cuerpo.get("servidor"), str) or not _ID.fullmatch(cuerpo["servidor"]):
            raise ErrorWebs("entrada_invalida")
        s = _servidor_por_id(cuerpo["servidor"])
        if s is None:
            raise ErrorWebs("servidor_desconocido")
        from . import servidores_ciclo
        entrada = (servidores_ciclo.leer_cache().get("servidores") or {}).get(s["nombre"])
        det = ((entrada or {}).get("datos") or {}).get("webs_detectadas") or {}
        webs, nueva = cambiar(cuerpo["servidor"], cuerpo.get("aceptar") or [], cuerpo.get("quitar") or [], set(det.get("hosts") or []))
        with _CERROJO:
            _CACHE.pop(cuerpo["servidor"], None)
        return 200, {"ok": True, "webs": webs, "servidores_rev": revision_servidores(sin_secretos(nueva))}
    except ErrorWebs as e:
        return (404 if str(e) == "servidor_desconocido" else 400), {"ok": False, "codigo": str(e), "mensaje": MENSAJES.get(str(e), MENSAJES["entrada_invalida"])}
    except Exception:
        return 500, {"ok": False, "codigo": "interno", "mensaje": "Fallo interno (queda en el log)."}
