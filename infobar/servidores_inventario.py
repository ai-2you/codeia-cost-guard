"""Inventario de contenedores de un servidor (sección `inventario` de recoger-root v2), convertido en recursos con identidad estable.

Todo viene de un servidor que puede estar comprometido y los nombres/etiquetas los eligen los clientes de ese servidor: cada campo sale de
`docker inspect` con `{{json}}` (un valor con saltos de línea, tabuladores o «### algo» no puede romper líneas ni secciones) y aquí se valida con
juegos de caracteres y conjuntos cerrados. Una línea que no encaja se descarta entera y se cuenta; nunca se arregla. Si falta alguna línea que el
servidor dijo enviar, la lectura no es completa y de ella no se concluye que algo haya desaparecido."""
import calendar
import json
import re

NOMBRE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")      # las tareas de Swarm (servicio.slot.tarea) y los proyectos de Compose pasan de 63
ESTADOS_DOCKER = frozenset({"created", "running", "paused", "restarting", "removing", "exited", "dead"})
SALUD = frozenset({"healthy", "unhealthy", "starting", "none"})
POLITICAS = frozenset({"", "no", "always", "on-failure", "unless-stopped"})
_ENTERO = re.compile(r"[0-9]{1,12}")
_ENTERO_CON_SIGNO = re.compile(r"-?[0-9]{1,6}")
_FECHA = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]{1,9})?Z")
MAX_CONTENEDORES = 200
CAMPOS = 12                 # «c» + 11 valores
ORDEN_ESTADO = {"ok": 0, "terminado": 1, "otro": 2, "reiniciando": 3, "mal": 4, "caido": 5}
TIPOS = ("contenedor", "compose", "servicio")


def _entero(texto, maximo):
    return int(texto) if _ENTERO.fullmatch(texto) and int(texto) <= maximo else None


def _texto(crudo):
    """Cadena JSON de una sola línea -> str, o None si no es una cadena JSON válida."""
    try:
        v = json.loads(crudo)
    except (ValueError, RecursionError):
        return None
    return v if isinstance(v, str) else None


def _etiqueta(crudo, vacio_ok=True):
    v = _texto(crudo)
    if v is None:
        return None
    if v == "":
        return "" if vacio_ok else None
    return v if NOMBRE.fullmatch(v) else None


def _epoch(texto):
    m = _FECHA.fullmatch(texto)
    if not m:
        return None
    a, mes, d, h, mi, s = (int(x) for x in m.groups())
    if a < 2000 or a > 2100 or not (1 <= mes <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 61):
        return None                                    # «0001-01-01T00:00:00Z» = nunca arrancó
    try:
        return calendar.timegm((a, mes, d, h, mi, s, 0, 0, 0))
    except (ValueError, OverflowError):
        return None


def _contenedor(linea):
    """dict del contenedor o None si la línea no cumple el formato."""
    p = linea.split("\t")
    if len(p) != CAMPOS or p[0] != "c":
        return None
    nombre = _texto(p[1])
    nombre = nombre[1:] if isinstance(nombre, str) and nombre.startswith("/") else nombre       # docker inspect da «/nombre»
    estado, salud, politica = _texto(p[2]), _texto(p[3]), _texto(p[7])
    reinicios = _entero(p[4], 100_000_000)
    salida = int(p[5]) if _ENTERO_CON_SIGNO.fullmatch(p[5]) and -65535 <= int(p[5]) <= 65535 else None       # algunos fallos de arranque dan -1
    proyecto, servicio, swarm, tarea = _etiqueta(p[8]), _etiqueta(p[9]), _etiqueta(p[10]), _etiqueta(p[11])
    inicio = _texto(p[6])
    if (not isinstance(nombre, str) or not NOMBRE.fullmatch(nombre) or estado not in ESTADOS_DOCKER or salud not in SALUD
            or politica not in POLITICAS or reinicios is None or salida is None or inicio is None
            or None in (proyecto, servicio, swarm, tarea)):
        return None
    return {"nombre": nombre, "estado": estado, "salud": salud, "reinicios": reinicios, "salida": salida, "inicio": _epoch(inicio),
            "politica": politica, "proyecto": proyecto, "servicio": servicio, "swarm": swarm, "tarea": tarea}


def interpretar(lineas, marcada):
    """dict del inventario, o None si la sección no vale (sin marco validado no hay inventario).

    {"fuente", "ahora_srv", "total", "pedidos", "truncado", "contenedores": [...], "descartados", "completo"}.
    `completo` = llegaron todas las líneas que el servidor dijo enviar. Un contenedor cuya línea no vale se descarta SOLO él (y se cuenta): la lectura sigue
    sirviendo para lo demás, pero con `descartados` > 0 no se concluye que nada haya desaparecido (el descartado podría ser cualquiera)."""
    if not marcada:
        return None
    kv, filas, repetido = {}, [], False
    for l in lineas:
        if l.startswith("c\t"):
            filas.append(l)
            continue
        p = l.split()
        if len(p) == 2 and p[0] in ("fuente", "total", "pedidos", "truncado", "ahora"):
            repetido = repetido or p[0] in kv
            kv[p[0]] = p[1]
        elif l:
            return None                                # una línea que no es del protocolo: el servidor no cumple
    if repetido or kv.get("fuente") not in ("docker", "ninguna", "error"):
        return None
    inv = {"fuente": kv["fuente"], "ahora_srv": _entero(kv.get("ahora", ""), 4_102_444_800), "total": 0, "pedidos": 0, "truncado": False,
           "contenedores": [], "descartados": 0, "completo": False}
    if kv["fuente"] != "docker":
        return inv
    total, pedidos = _entero(kv.get("total", ""), 10_000_000), _entero(kv.get("pedidos", ""), MAX_CONTENEDORES)
    if total is None or pedidos is None or kv.get("truncado") not in ("si", "no") or pedidos > total or len(filas) > MAX_CONTENEDORES:
        return None
    vistos, descartados = set(), 0
    for l in filas:
        c = _contenedor(l)
        if c is None or c["nombre"] in vistos:
            descartados += 1
            continue
        vistos.add(c["nombre"])
        inv["contenedores"].append(c)
    inv.update(total=total, pedidos=pedidos, truncado=kv["truncado"] == "si" or total > pedidos, descartados=descartados)
    inv["completo"] = len(filas) == pedidos
    return inv


def recursos(inv, swarm=None):
    """{(tipo, nombre): {estado, salud, reinicios, inicio, n}} de un inventario completo o parcial.

    Identidad: servicio Swarm solo si el contenedor lo declara (etiqueta de servicio y de tarea, y su nombre es `servicio.<slot>.<tarea>`) **y** el
    servicio está en `docker service ls` (`swarm` = {nombre: ok}); si no, `proyecto/servicio` de Compose; si no, el nombre. Un contenedor con
    etiquetas inventadas no puede ocupar la identidad de un servicio que no existe. Varios contenedores con la misma identidad se agregan
    (el peor estado manda); el estado de un servicio Swarm es el de `service ls`, no el de sus tareas viejas."""
    swarm = swarm or {}
    out = {}
    for c in (inv or {}).get("contenedores") or []:
        e = ("ok" if c["salud"] != "unhealthy" else "mal") if c["estado"] == "running" else (
            "reiniciando" if c["estado"] == "restarting" else (("terminado" if c["salida"] == 0 else "caido") if c["estado"] == "exited" else (
                "caido" if c["estado"] == "dead" else "otro")))
        if (c["swarm"] and c["tarea"] and c["swarm"] in swarm and c["nombre"].startswith(c["swarm"] + ".") and c["nombre"].endswith("." + c["tarea"])):
            clave = ("servicio", c["swarm"])
            r = out.setdefault(clave, {"estado": "ok" if swarm[c["swarm"]] else "caido", "salud": None, "reinicios": None, "inicio": None, "n": 0})
            r["n"] += 1
            continue
        if c["proyecto"] and c["servicio"]:
            clave = ("compose", f'{c["proyecto"]}/{c["servicio"]}')
        else:
            clave = ("contenedor", c["nombre"])
        r = out.get(clave)
        if r is None:
            out[clave] = {"estado": e, "salud": c["salud"], "reinicios": c["reinicios"], "inicio": c["inicio"], "n": 1}
            continue
        r["n"] += 1
        if ORDEN_ESTADO[e] > ORDEN_ESTADO[r["estado"]]:
            r["estado"] = e
        r["reinicios"] = max(r["reinicios"], c["reinicios"])
        if c["inicio"] is not None and (r["inicio"] is None or c["inicio"] > r["inicio"]):
            r["inicio"] = c["inicio"]
        if c["salud"] == "unhealthy" or r["salud"] is None:
            r["salud"] = c["salud"]
    # servicios Swarm que están en `service ls` pero de los que no llegó ningún contenedor (réplicas a 0 o inventario recortado): no se inventan
    return out


def reclama_swarm(inv):
    """True si algún contenedor se declara tarea de Swarm (etiquetas de servicio y de tarea y nombre con su forma)."""
    return any(c["swarm"] and c["tarea"] and c["nombre"].startswith(c["swarm"] + ".") and c["nombre"].endswith("." + c["tarea"])
               for c in (inv or {}).get("contenedores") or [])
