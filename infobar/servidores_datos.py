"""Lo que sale de un servidor, convertido en cifras seguras para guardar.

Todo valor viene de un servidor que puede estar comprometido: se parsea con función estricta, lo ilegible es None (dato ausente, nunca un cero
honesto) y lo fuera de rango descarta la muestra entera (`FueraDeRango`). Las IPs se canonizan con `ipaddress`: solo globales, nunca las propias.
No se guarda nada de texto libre: ni nombres de servicios, ni dominios, ni usuarios, ni la salida cruda."""
import ipaddress

ESTADOS = ("verde", "ambar", "rojo", "gris", "error")
MAX_INTENTOS = 1_000_000
MAX_IPS_POR_MUESTRA = 4


class FueraDeRango(ValueError):
    """Un número del servidor no cabe en lo que un servidor sano puede decir: la muestra no se guarda."""


def entero(valor, minimo, maximo):
    """int en [minimo, maximo]; None si falta o no es un número; FueraDeRango si es un número imposible. Los booleanos no valen."""
    if valor is None or isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    if valor != valor or valor in (float("inf"), float("-inf")):
        return None
    if not minimo <= valor <= maximo:
        raise FueraDeRango(f"{valor!r} fuera de {minimo}..{maximo}")
    return int(valor)


def porcentaje(parte, total):
    """0..100 con un decimal, o None si no se puede calcular. Un uso por encima del 100 % o negativo es un dato imposible."""
    if parte is None or total is None or isinstance(parte, bool) or isinstance(total, bool):
        return None
    if not isinstance(parte, (int, float)) or not isinstance(total, (int, float)) or total <= 0:
        return None
    p = 100.0 * parte / total
    if not 0 <= p <= 100.5:
        raise FueraDeRango(f"porcentaje {p:.1f}")
    return round(min(p, 100.0), 1)


def canonica(texto):
    """IP en forma canónica o None si no es una IP bien formada. Desenvuelve las IPv4 mapeadas antes de decidir nada, para que
    `::ffff:8.8.8.8` y `8.8.8.8` sean la misma IP en todas las versiones de Python."""
    if not isinstance(texto, str) or not 2 < len(texto) <= 45 or "%" in texto or not texto.isascii():
        return None
    try:
        ip = ipaddress.ip_address(texto.strip())
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip)


def ip_global(texto):
    """La IP canónica solo si es pública y atribuible: ni privada, ni CGNAT, ni loopback, ni local de enlace, ni multicast, ni reservada."""
    c = canonica(texto)
    if c is None:
        return None
    ip = ipaddress.ip_address(c)
    if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or ip.is_link_local
            or ip.is_private):
        return None
    return c


def conjunto_canonico(ips):
    return {c for c in (canonica(str(i)) for i in ips or ()) if c}


def _recuento(lista, pred=None):
    if not isinstance(lista, list):
        return None
    return entero(sum(1 for x in lista if pred is None or pred(x)), 0, 1_000_000)


def muestra_de(entrada, conocidas=frozenset()):
    """Métricas de una lectura buena: ({columna: valor}, [(ip, intentos_24h)], intentos_propios). Lanza FueraDeRango si algo es imposible.
    `entrada` es la de la caché de servidores (datos, webs, expuestos, bastionado, estado)."""
    d = entrada.get("datos") if isinstance(entrada.get("datos"), dict) else {}
    cpu, mem, disco = d.get("cpu") or {}, d.get("memoria") or {}, d.get("disco") or {}
    it = d.get("intentos") if isinstance(d.get("intentos"), dict) else {}
    webs = entrada.get("webs") if isinstance(entrada.get("webs"), list) else []
    sesiones = d.get("sesiones") if isinstance(d.get("sesiones"), list) else None
    ajenas = None
    if sesiones is not None and conocidas:              # sin IPs conocidas no se puede decir qué es ajeno
        ajenas = entero(sum(1 for s in sesiones if isinstance(s, dict) and s.get("desde")
                            and canonica(s["desde"]) not in conocidas and s["desde"] not in conocidas), 0, 100_000)
    servicios = d.get("servicios") if isinstance(d.get("servicios"), list) else None
    ms = [w.get("ms") for w in webs if isinstance(w, dict) and isinstance(w.get("ms"), (int, float)) and not isinstance(w.get("ms"), bool)]
    checks = entrada.get("bastionado") if isinstance(entrada.get("bastionado"), list) else None
    estado = entrada.get("estado") if entrada.get("estado") in ESTADOS else "gris"
    exp = entrada.get("expuestos")
    f2b = d.get("fail2ban") if isinstance(d.get("fail2ban"), dict) else {}
    act = d.get("actualizaciones") if isinstance(d.get("actualizaciones"), dict) else {}
    m = {
        "estado": estado,
        "carga_pct": porcentaje(cpu.get("pct"), 100) if isinstance(cpu, dict) else None,
        "mem_pct": porcentaje(mem.get("usada"), mem.get("total")) if isinstance(mem, dict) else None,
        "disco_pct": porcentaje(disco.get("usado"), disco.get("total")) if isinstance(disco, dict) else None,
        "uptime_s": entero(d.get("uptime_s"), 0, 10 ** 9),
        "intentos_24h": entero(it.get("total"), 0, MAX_INTENTOS), "intentos_1h": entero(it.get("hora"), 0, MAX_INTENTOS),
        "baneadas": entero(f2b.get("ahora"), 0, 1_000_000),
        "sesiones": _recuento(sesiones), "sesiones_ajenas": ajenas,
        "puertos_n": _recuento(d.get("puertos")) if isinstance(d.get("puertos"), list) else None,
        "puertos_expuestos": _recuento(exp) if isinstance(exp, list) else None,
        "servicios_n": _recuento(servicios),
        "servicios_caidos": _recuento(servicios, lambda s: isinstance(s, dict) and not s.get("ok")),
        "webs_n": _recuento(webs) if webs else None,
        "webs_caidas": _recuento(webs, lambda w: isinstance(w, dict) and (w.get("error") or (w.get("codigo") or 0) >= 500)) if webs else None,
        "web_ms_max": entero(max(ms), 0, 10 ** 7) if ms else None,
        "faltas_proteccion": _recuento(checks, lambda c: isinstance(c, dict) and c.get("ok") is False),
        "reinicio_pendiente": (1 if act.get("reinicio") else 0) if act else None,
    }
    ips, propios = {}, 0
    # sin garantía de que las líneas del journal las escribiera sshd (formato v1, o sesiones dentro del cgroup de ssh) no se guarda ninguna IP
    candidatas = it.get("top") if isinstance(it.get("top"), list) and it.get("origen_fiable") is not False else []
    for par in candidatas[:MAX_IPS_POR_MUESTRA * 4]:
        if not (isinstance(par, (list, tuple)) and len(par) == 2):
            continue
        c = canonica(par[0])
        if c is None:
            continue
        if c in conocidas:
            propios += 1
            continue
        c = ip_global(c)
        n = entero(par[1], 0, MAX_INTENTOS) if not isinstance(par[1], bool) else None
        if c is None or n is None:
            continue
        ips[c] = max(n, ips.get(c, 0))
    top = sorted(ips.items(), key=lambda kv: -kv[1])[:MAX_IPS_POR_MUESTRA]      # se recorta tras validar, no antes
    return m, top, propios


def horas_de(entrada, ok_en):
    """[(hora, n, completa)] de los cubos horarios exactos de intentos de una lectura buena hecha en `ok_en` (reloj del Mac).
    Hora en segundos, alineada. Solo las horas cubiertas por el journal del servidor y no posteriores a la lectura, dentro de las últimas 26 h.
    Completa = la hora ya había terminado tanto para el Mac como para el servidor. Lista vacía si no hay histograma; FueraDeRango si algo es
    imposible (se descarta el histograma entero, no la muestra)."""
    d = entrada.get("datos") if isinstance(entrada.get("datos"), dict) else {}
    it = d.get("intentos") if isinstance(d.get("intentos"), dict) else {}
    horas = it.get("horas")
    if not isinstance(horas, list) or not horas:
        return []
    srv = it.get("ahora_srv")
    fin = min(ok_en, srv) if isinstance(srv, int) and not isinstance(srv, bool) else ok_en
    ultima, salida = None, []
    for par in horas:
        if not (isinstance(par, (list, tuple)) and len(par) == 2) or isinstance(par[0], bool) or not isinstance(par[0], int):
            raise FueraDeRango("cubo de intentos mal formado")
        if par[0] % 3600 or not 946_684_800 <= par[0] <= 4_102_444_800 or par[0] > ok_en + 900:
            raise FueraDeRango("hora de cubo fuera de rango")
        if ultima is not None and par[0] <= ultima:
            raise FueraDeRango("cubos desordenados o repetidos")
        ultima = par[0]
        n = entero(par[1], 0, MAX_INTENTOS)                   # None = hora que el journal no cubre: no se escribe
        if n is not None and par[0] <= ok_en and par[0] > ok_en - 26 * 3600:
            salida.append((par[0], n, par[0] + 3600 <= fin))
    return salida
