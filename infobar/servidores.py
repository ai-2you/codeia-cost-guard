"""Servidores (VPS) que monitoriza infobar: validacion de su configuracion.

La misma validacion se aplica al guardar desde el panel y justo antes de cada conexion: un valor
que llega a `ssh` o a una peticion HTTP nunca se usa sin pasar por aqui.
"""
import ipaddress, re, urllib.parse

PANELES = ("dokploy", "easypanel")
# usuario@host o alias de ~/.ssh/config; sin '-' inicial (seria una opcion de ssh: -oProxyCommand=...)
_SSH = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]*(@[A-Za-z0-9._][A-Za-z0-9._-]*)?$")
_NOMBRE = re.compile(r"^[\w .-]{1,40}$")
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
# nombre DNS: etiquetas de letras/digitos/guiones, y el ultimo nivel con alguna letra (asi '127.1',
# '2130706433' o '0x7f.0.0.1', que el sistema resuelve a 127.0.0.1, no pasan por nombres)
_DNS = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")
_DNS_INTERNOS = (".local", ".localhost", ".internal", ".lan", ".home.arpa")


def ip_no_publica(ip):
    ip = ipaddress.ip_address(ip)
    # is_global deja fuera tambien 100.64/10 (CGNAT, Tailscale), que is_private no cubre
    return not ip.is_global or ip.is_multicast


def _host_local(host):
    """True si el host no es una IP publica en forma canonica ni un nombre DNS publico.

    Solo valida la forma. El recolector (fase de servidores) vuelve a comprobar cada IP RESUELTA
    justo antes de conectar, porque un nombre publico puede resolver a 127.0.0.1 (nip.io y similares).
    """
    host = (host or "").lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if ":" in host:                                     # IPv6
        try:
            return ip_no_publica(host)
        except ValueError:
            return True
    if _IPV4.fullmatch(host):
        try:
            return ip_no_publica(host)
        except ValueError:
            return True
    if not _DNS.fullmatch(host) or host == "localhost" or host.endswith(_DNS_INTERNOS):
        return True
    return False


def url_valida(url, solo_https=False):
    """http(s) a un host publico. Devuelve el motivo del rechazo o "" si vale."""
    url = str(url or "")
    if any(c.isspace() for c in url) or len(url) > 300 or not url.isascii():
        return "URL con espacios, caracteres no ASCII o demasiado larga (usa la forma codificada %xx)"
    try:
        u = urllib.parse.urlsplit(url)
        u.port                                          # lanza ValueError con puertos imposibles
    except ValueError:
        return "URL mal formada"
    esquemas = ("https",) if solo_https else ("http", "https")
    if u.scheme not in esquemas:
        return f"esquema no permitido (solo {' / '.join(esquemas)})"
    if not u.hostname or u.username or u.password:
        return "sin host o con credenciales en la URL"
    if _host_local(u.hostname):
        return "host local o privado"
    return ""


def errores_servidores(lista):
    """Lista de errores (texto) de la seccion `servidores` de config.json; vacia si es valida."""
    if lista is None:
        return []
    if not isinstance(lista, list):
        return ["servidores debe ser una lista"]
    errores, nombres = [], set()
    for i, s in enumerate(lista):
        donde = f"servidores[{i}]"
        if not isinstance(s, dict):
            errores.append(f"{donde}: no es un objeto")
            continue
        nombre = s.get("nombre")
        if not (isinstance(nombre, str) and _NOMBRE.fullmatch(nombre)):
            errores.append(f"{donde}: nombre vacio o con caracteres no admitidos")
        elif nombre in nombres:
            errores.append(f"{donde}: nombre repetido ({nombre})")
        else:
            nombres.add(nombre)
        if not (isinstance(s.get("ssh"), str) and _SSH.fullmatch(s["ssh"])):
            errores.append(f"{donde}: ssh debe ser usuario@host o un alias (sin '-' inicial ni espacios)")
        if "ssh_admin" in s and not (isinstance(s["ssh_admin"], str) and _SSH.fullmatch(s["ssh_admin"])):
            errores.append(f"{donde}: ssh_admin debe ser usuario@host o un alias (sin '-' inicial ni espacios)")
        puerto = s.get("puerto", 22)
        if not (isinstance(puerto, int) and not isinstance(puerto, bool) and 0 < puerto < 65536):
            errores.append(f"{donde}: puerto debe ser un numero entre 1 y 65535")
        panel = s.get("panel")
        if panel is not None and panel not in PANELES:
            errores.append(f"{donde}: panel debe ser {' o '.join(PANELES)}")
        if panel is not None:
            motivo = url_valida(s.get("panel_url"), solo_https=True)
            if motivo:
                errores.append(f"{donde}: panel_url {motivo}")
        webs = s.get("webs") or []
        if not isinstance(webs, list):
            errores.append(f"{donde}: webs debe ser una lista")
        else:
            for w in webs:
                motivo = url_valida(w)
                if motivo:
                    errores.append(f"{donde}: web {str(w)[:60]!r} {motivo}")
    return errores


def errores_ips(ips):
    if ips is None:
        return []
    if not isinstance(ips, list):
        return ["ips_conocidas debe ser una lista"]
    malas = []
    for ip in ips:
        try:
            ipaddress.ip_address(str(ip))
        except ValueError:
            malas.append(str(ip)[:45])
    return [f"ips_conocidas: no son IPs validas: {', '.join(malas)}"] if malas else []
