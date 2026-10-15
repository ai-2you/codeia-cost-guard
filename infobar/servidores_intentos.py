"""Intentos de acceso SSH leídos de un servidor: sección `intentos` en formato v1 (sin marco) y v2 (con marco, ya validado por `servidores_ssh.analizar`).

Todo lo que llega del servidor es hostil: se parsea con funciones estrictas y lo que no encaja descarta la parte afectada (el histograma, las IPs o
la sección entera), nunca se arregla. v1 no dice de dónde salen las líneas del journal (cualquier proceso local puede escribir como `sshd`), así que
sus IPs se muestran pero no se guardan en la base (`origen_fiable` False); v2 solo las da por fiables si el script comprobó que las sesiones de
usuario no viven en el cgroup de sshd (`aislamiento si`)."""
import re
import time

_IP = re.compile(r"[0-9a-fA-F.:]{3,45}")
_ENTERO = re.compile(r"[0-9]{1,12}")
TS_MAX = 4_102_444_800
MAX_CUENTA = 10_000_000
MAX_IPS = 12
CUBOS = 25                  # horas naturales: la actual (parcial) y las 24 anteriores
DESFASE_MAX_S = 900         # con más desfase entre el reloj del servidor y el del Mac, el histograma no se coloca
CLAVES = ("total", "hora", "ahora", "fuente", "aislamiento", "cobertura", "parcial")


def _entero(texto, maximo=MAX_CUENTA):
    return int(texto) if _ENTERO.fullmatch(texto) and int(texto) <= maximo else None


def _lineas_v1(lineas):
    it = {"total": 0, "hora": 0, "top": [], "origen_fiable": False}
    for l in lineas:
        p = l.split()
        if len(p) == 2 and p[0] in ("total", "hora"):
            it[p[0]] = _num(p[1])
        elif len(p) == 3 and p[0] == "ip" and _IP.fullmatch(p[1]):
            it["top"].append([p[1], _num(p[2])])
    it["top"] = sorted(it["top"], key=lambda x: -x[1])[:4]
    return it


def _num(texto):
    try:
        v = float(texto)
    except (TypeError, ValueError):
        return 0
    return int(v) if v == v and abs(v) != float("inf") else 0


def _descarte(it, motivo):
    it.setdefault("descartes", []).append(motivo)


def _horas(cubos, ahora_srv, total, parcial, ahora_mac):
    """([[hora_en_segundos, n o None], ...], motivo de descarte o None). Exactamente los 25 cubos consecutivos que acaban en la hora del servidor."""
    if parcial:
        return None, "parcial"
    if abs(ahora_mac - ahora_srv) > DESFASE_MAX_S:
        return None, "reloj"
    actual = ahora_srv // 3600
    if len(cubos) != CUBOS or [k for k, _ in cubos] != [str(h) for h in range(actual - CUBOS + 1, actual + 1)]:
        return None, "cubos"
    salida = []
    for k, v in cubos:
        n = None if v == "-" else _entero(v)
        if v != "-" and n is None:
            return None, "cubos"
        salida.append([int(k) * 3600, n])
    if sum(n for _, n in salida if n) < total:
        return None, "incoherente"
    return salida, None


def interpretar(lineas, marcada, ahora=None):
    """dict de intentos listo para la caché, o None si el servidor dijo que no tiene fuente (sin dato: nunca un 0) o la sección no vale."""
    if not marcada:
        return _lineas_v1(lineas)
    ahora = int(ahora if ahora is not None else time.time())
    kv, cubos, ips = {}, [], []
    for l in lineas:
        p = l.split()
        if len(p) == 2 and p[0] in CLAVES:
            if p[0] in kv:
                return None                             # una clave repetida no es de un servidor que cumple el protocolo
            kv[p[0]] = p[1]
        elif len(p) == 3 and p[0] == "h":
            cubos.append((p[1], p[2]))
        elif len(p) == 3 and p[0] == "ip" and _IP.fullmatch(p[1]):
            n = _entero(p[2])
            if n is not None:
                ips.append([p[1], n])
    if kv.get("fuente") != "unit":
        return None
    total, hora, ahora_srv = _entero(kv.get("total", "")), _entero(kv.get("hora", "")), _entero(kv.get("ahora", ""), TS_MAX)
    if None in (total, hora, ahora_srv) or kv.get("aislamiento") not in ("si", "no") or kv.get("parcial") not in ("si", "no"):
        return None
    it = {"total": total, "hora": hora, "v2": True, "origen_fiable": kv["aislamiento"] == "si", "parcial": kv["parcial"] == "si",
          "desfase": ahora - ahora_srv, "ahora_srv": ahora_srv}
    horas, motivo = _horas(cubos, ahora_srv, total, it["parcial"], ahora)
    if motivo:
        _descarte(it, "histograma_" + motivo)
    else:
        it["horas"] = horas
    ips.sort(key=lambda x: -x[1])
    if sum(n for _, n in ips) > total:
        _descarte(it, "ips_incoherentes")
        ips = []
    it["top"] = ips[:MAX_IPS]
    return it
