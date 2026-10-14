"""Ingesta de logs al almacén: lectura incremental, tiempo UTC y el bucle común de los extractores por agente.

Lo que llega de un log es hostil (un `jsonl` lo puede escribir cualquier proceso, o una sesión con inyección de
prompt): se acota todo, nada de lo leído se interpreta como SQL ni como ruta, y lo que no cuadra no se guarda: se
anota en `anomalias`. Releer es inocuo porque el almacén es monótono, así que perder el desplazamiento solo cuesta tiempo.
"""
import datetime
import hashlib
import json
import os
import re
import stat

from . import almacen

MAX_LECTURA = 8 * 1024 * 1024          # bytes por pasada y fichero: la ingesta es reanudable y no monopoliza la barra
MAX_LINEA = 2 * 1024 * 1024            # una línea mayor no es un turno
LOTE = 500                             # turnos por transacción (lotes cortos: ≤ 200 ms)
TAM_CABECERA = 4096

_ISO = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.(\d+))?(Z|[+-]\d\d(?::?\d\d)?)$")


def a_epoch_utc(v):
    """Segundos UTC de una marca de tiempo, o None. EXIGE zona: sin ella no se puede saber el instante y se
    cuarentena (hora local y UTC se mezclarían). Acepta ISO con Z/offset y epoch en segundos o milisegundos."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d{9,13}(\.\d+)?", v)):
        try:
            n = float(v)
        except OverflowError:                           # un entero de cientos de cifras
            return None
        if n != n or n in (float("inf"), float("-inf")):
            return None
        return int(n / 1000) if n > 1e11 else int(n)
    if not isinstance(v, str):
        return None
    m = _ISO.match(v.strip())
    if not m:
        return None
    anio, mes, dia, h, mi, se, _frac, zona = m.groups()
    if zona == "Z":
        desplazamiento = 0
    else:
        z = zona.replace(":", "")
        signo = -1 if z[0] == "-" else 1
        horas_z, minutos_z = int(z[1:3]), int(z[3:5] or "0")
        if horas_z > 14 or minutos_z > 59:              # las zonas reales van de -12:00 a +14:00
            return None
        desplazamiento = signo * (horas_z * 3600 + minutos_z * 60)
    try:
        dt = datetime.datetime(int(anio), int(mes), int(dia), int(h), int(mi), int(se), tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp()) - desplazamiento


def id_fuente(ruta, base):
    """Id estable y portable de una fuente: hash de la ruta RELATIVA a `base` (nunca la ruta absoluta con el usuario)."""
    try:
        rel = os.path.relpath(os.path.realpath(ruta), os.path.realpath(base)).replace("\\", "/")
    except ValueError:                                  # Windows: otra unidad
        rel = os.path.realpath(ruta).replace("\\", "/")
    return hashlib.sha256(rel.encode("utf-8", "replace")).hexdigest()[:32]


TOPE_LINEA_ENORME = 64 * 1024 * 1024    # una línea de más de esto se da por perdida y se salta hasta el final del fichero
LEN_COLA = 64                           # bytes justo antes del desplazamiento: comprueban que seguimos en el mismo contenido


def _hash(datos):
    return hashlib.sha256(datos).hexdigest()


def _comprueba_continuidad(f, estado, st):
    """True si el fichero abierto sigue siendo el que se leyó: mismo inodo, cabecera, cola y un salto de línea justo antes del
    desplazamiento. Si el fichero se reescribió en el mismo inodo conservando el principio, la cola lo delata."""
    if not estado or estado["desplazamiento"] <= 0 or estado["inodo"] != st.st_ino or st.st_size < estado["desplazamiento"]:
        return False
    try:
        f.seek(0)
        if _hash(f.read(estado["cab_len"])) != estado["hash_cab"]:
            return False
        if estado["cola_len"]:
            f.seek(estado["desplazamiento"] - estado["cola_len"])
            cola = f.read(estado["cola_len"])
            return cola.endswith(b"\n") and _hash(cola) == estado["hash_cola"]
        return True
    except OSError:
        return False


def leer_nuevas(ruta, estado, max_bytes=MAX_LECTURA):
    """Líneas completas nuevas de `ruta` desde donde se quedó (`estado` de `almacen.estado_lectura`, o None).

    Todo sobre UN descriptor abierto (stat, lectura y hashes): el fichero no puede cambiar de inodo entre medias. Se relee
    desde el principio si cambió el inodo, la cabecera o la cola, o se truncó. Una línea sin terminar (se está escribiendo)
    no avanza. Una línea mayor que `max_bytes` se salta ENTERA (se busca su salto de línea) para no dejar un resto que se
    lea como JSON roto. Devuelve (lineas, info) con info lista para `almacen.guardar_lectura`."""
    if not stat.S_ISREG(os.stat(ruta).st_mode):         # antes de abrir: abrir un FIFO sin escritor cuelga la ingesta
        raise OSError("no es un fichero regular")
    with open(ruta, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise OSError("no es un fichero regular")
        desde = estado["desplazamiento"] if _comprueba_continuidad(f, estado, st) else 0
        f.seek(desde)
        trozo = f.read(max_bytes)
        ultimo = trozo.rfind(b"\n")
        enormes = 0
        if ultimo >= 0:
            avance, lineas = ultimo + 1, trozo[:ultimo + 1].split(b"\n")[:-1]
        elif len(trozo) >= max_bytes:                   # una sola línea mayor que max_bytes: buscar su final sin acumularla
            enormes, lineas, avance = 1, [], len(trozo)
            while avance < TOPE_LINEA_ENORME:
                bloque = f.read(1 << 20)
                if not bloque:
                    avance = 0 if desde + avance >= st.st_size else avance      # sin final aún: no avanzar (se está escribiendo)
                    enormes = 0 if avance == 0 else 1
                    break
                i = bloque.find(b"\n")
                if i >= 0:
                    avance += i + 1
                    break
                avance += len(bloque)
        else:
            avance, lineas = 0, []
        nuevo = desde + avance
        cab_len, cola_len = min(TAM_CABECERA, nuevo), min(LEN_COLA, nuevo)
        f.seek(0)
        hash_cab = _hash(f.read(cab_len)) if cab_len else ""
        f.seek(nuevo - cola_len)
        cola = f.read(cola_len) if cola_len else b""
    info = {"desplazamiento": nuevo, "tam": st.st_size, "inodo": st.st_ino, "mtime_ns": st.st_mtime_ns,
            "cab_len": cab_len, "hash_cab": hash_cab, "cola_len": len(cola), "hash_cola": _hash(cola) if cola else "",
            "completo": nuevo >= st.st_size, "enormes": enormes}
    return lineas, info


def _maximo(a, b):
    """Dos líneas del mismo mensaje: máximo de cada contador y la primera hora vista."""
    return {**a, "hora_utc": min(a["hora_utc"], b["hora_utc"]), **{c: max(a[c], b[c]) for c in almacen.CONTADORES}}


def ingerir_fichero(con, quien, espacio_id, ruta, agente, extractor, base, contexto=None, max_bytes=MAX_LECTURA, ahora=None):
    """Una pasada de ingesta de un fichero `jsonl`. `extractor(dict, ctx)` devuelve un turno (dict), None o ("anomalia", texto).

    Una línea que falla (JSON roto, tipos raros, desbordes, errores del extractor) se cuenta y se anota, y la pasada sigue:
    una sola línea hostil no puede bloquear un fichero para siempre. Devuelve {nuevos, subidos, iguales, anomalias, lineas,
    rotas, completo}. Reanudable: si no termina, la siguiente pasada sigue donde quedó."""
    fid = almacen.fuente(con, quien, espacio_id, agente, id_fuente(ruta, base), ahora=ahora)
    try:
        lineas, info = leer_nuevas(ruta, almacen.estado_lectura(con, fid), max_bytes)
    except OSError as e:
        almacen.anotar(con, quien, "ingesta", "", fid, f"fichero no legible: {type(e).__name__}", ahora=ahora)
        return {"nuevos": 0, "subidos": 0, "iguales": 0, "anomalias": 1, "lineas": 0, "rotas": 0, "completo": True}
    ctx = dict(contexto or {})
    turnos, rotas, grandes, otras = {}, 0, info["enormes"], {}

    def cuenta(texto):
        otras[texto] = otras.get(texto, 0) + 1

    for crudo in lineas:
        if len(crudo) > MAX_LINEA:
            grandes += 1
            continue
        if not crudo.strip():
            continue
        try:
            d = json.loads(crudo)
        except (ValueError, RecursionError):
            rotas += 1
            continue
        if not isinstance(d, dict):
            continue
        try:
            r = extractor(d, ctx)
            if r is None:
                continue
            if isinstance(r, tuple):
                cuenta(r[1])
                continue
            motivo = almacen.problema(r)
            if motivo:                                  # se valida AQUÍ para que un tipo raro no rompa la fusión del lote
                cuenta(f"turno descartado: {motivo}")
                continue
            previo = turnos.get(r["clave"])
            if previo is None:
                turnos[r["clave"]] = r
            elif previo.get("modelo") == r.get("modelo") and previo.get("agente") == r.get("agente") \
                    and abs(previo["hora_utc"] - r["hora_utc"]) <= almacen.TOLERANCIA_HORA_S:
                turnos[r["clave"]] = _maximo(previo, r)
            else:                                      # misma clave con otra identidad en el mismo fichero: manda la primera
                cuenta("clave repetida con otra identidad")
        except Exception as e:                         # noqa: BLE001  (una línea hostil no bloquea el fichero)
            cuenta(f"línea con error: {type(e).__name__}")
    total = {"nuevos": 0, "subidos": 0, "iguales": 0, "anomalias": 0}
    lote = list(turnos.values())
    for i in range(0, len(lote), LOTE):
        res = almacen.ingerir_turnos(con, quien, espacio_id, fid, lote[i:i + LOTE], ahora=ahora)
        for k in total:
            total[k] += res[k]
    # el id de fuente va en el detalle: la anomalía es única por fuente y no se funde entre ficheros distintos
    for tipo, n in (("lineas_no_json", rotas), ("lineas_enormes", grandes)):
        if n:
            almacen.anotar(con, quien, tipo, "", fid, f"fuente {fid}: {n} líneas", ahora=ahora)
    for texto, n in otras.items():
        almacen.anotar(con, quien, "ingesta", "", fid, f"fuente {fid}: {_ctrl_fuera(texto)}: {n}"[:200], ahora=ahora)
    almacen.guardar_lectura(con, quien, fid, info["inodo"], info["tam"], info["mtime_ns"], info["desplazamiento"],
                            info["cab_len"], info["hash_cab"], info["cola_len"], info["hash_cola"], ahora=ahora)
    total.update(lineas=len(lineas), rotas=rotas, completo=info["completo"])
    return total


def _ctrl_fuera(texto):
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", str(texto))[:120]
