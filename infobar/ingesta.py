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
        n = float(v)
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
        desplazamiento = signo * (int(z[1:3]) * 3600 + int((z[3:5] or "0")) * 60)
    try:
        dt = datetime.datetime(int(anio), int(mes), int(dia), int(h), int(mi), int(se), tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp()) - desplazamiento


def id_fuente(ruta, base):
    """Id estable y portable de una fuente: hash de la ruta RELATIVA a `base` (nunca la ruta absoluta con el usuario)."""
    rel = os.path.relpath(os.path.realpath(ruta), os.path.realpath(base)).replace("\\", "/")
    return hashlib.sha256(rel.encode("utf-8", "replace")).hexdigest()[:32]


def _hash_cabecera(ruta, n):
    with open(ruta, "rb") as f:
        return hashlib.sha256(f.read(n)).hexdigest()


def leer_nuevas(ruta, estado, max_bytes=MAX_LECTURA):
    """Líneas completas nuevas de `ruta` desde donde se quedó (`estado` de `almacen.estado_lectura`, o None).

    Se relee desde el principio si cambió el fichero (inodo, cabecera) o se truncó. Una línea sin terminar (se está
    escribiendo) no avanza. Devuelve (lineas, info) con info = {desplazamiento, tam, inodo, mtime_ns, cab_len, hash_cab,
    completo, troceadas} para guardar con `almacen.guardar_lectura`."""
    st = os.stat(ruta)
    desde = 0
    if estado and estado["inodo"] == st.st_ino and st.st_size >= estado["desplazamiento"] and estado["desplazamiento"] > 0:
        try:
            if _hash_cabecera(ruta, estado["cab_len"]) == estado["hash_cab"]:
                desde = estado["desplazamiento"]
        except OSError:
            desde = 0
    with open(ruta, "rb") as f:
        f.seek(desde)
        trozo = f.read(max_bytes)
    ultimo = trozo.rfind(b"\n")
    troceadas = 0
    if ultimo < 0:
        # sin ningún salto en `max_bytes`: o se está escribiendo, o es una línea enorme (se salta por entero)
        if len(trozo) >= max_bytes:
            troceadas, avance, lineas = 1, len(trozo), []
        else:
            avance, lineas = 0, []
    else:
        avance = ultimo + 1
        lineas = trozo[:avance].split(b"\n")[:-1]
    nuevo = desde + avance
    cab_len = min(TAM_CABECERA, nuevo)
    info = {"desplazamiento": nuevo, "tam": st.st_size, "inodo": st.st_ino, "mtime_ns": st.st_mtime_ns,
            "cab_len": cab_len, "hash_cab": _hash_cabecera(ruta, cab_len) if cab_len else "",
            "completo": nuevo >= st.st_size, "troceadas": troceadas}
    return lineas, info


def _maximo(a, b):
    """Dos líneas del mismo mensaje: máximo de cada contador y la primera hora vista."""
    return {**a, "hora_utc": min(a["hora_utc"], b["hora_utc"]), **{c: max(a[c], b[c]) for c in almacen.CONTADORES}}


def ingerir_fichero(con, quien, espacio_id, ruta, agente, extractor, base, contexto=None, max_bytes=MAX_LECTURA, ahora=None):
    """Una pasada de ingesta de un fichero `jsonl`. `extractor(dict, ctx)` devuelve un turno (dict), None o ("anomalia", texto).

    Devuelve {nuevos, subidos, iguales, anomalias, lineas, rotas, completo}. Reanudable: si no termina, la siguiente
    pasada sigue donde quedó."""
    fid = almacen.fuente(con, quien, espacio_id, agente, id_fuente(ruta, base), ahora=ahora)
    lineas, info = leer_nuevas(ruta, almacen.estado_lectura(con, fid), max_bytes)
    ctx = dict(contexto or {})
    turnos, rotas, grandes, otras = {}, 0, 0, {}
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
        r = extractor(d, ctx)
        if r is None:
            continue
        if isinstance(r, tuple):
            otras[r[1]] = otras.get(r[1], 0) + 1
            continue
        previo = turnos.get(r["clave"])
        if previo is None:
            turnos[r["clave"]] = r
        elif previo.get("modelo") == r.get("modelo") and previo.get("agente") == r.get("agente") \
                and abs(previo["hora_utc"] - r["hora_utc"]) <= almacen.TOLERANCIA_HORA_S:
            turnos[r["clave"]] = _maximo(previo, r)
        else:                                          # misma clave con otra identidad en el mismo fichero: manda la primera
            otras["clave repetida con otra identidad"] = otras.get("clave repetida con otra identidad", 0) + 1
    total = {"nuevos": 0, "subidos": 0, "iguales": 0, "anomalias": 0}
    lote = list(turnos.values())
    for i in range(0, len(lote), LOTE):
        res = almacen.ingerir_turnos(con, quien, espacio_id, fid, lote[i:i + LOTE], ahora=ahora)
        for k in total:
            total[k] += res[k]
    for tipo, n in (("lineas_no_json", rotas), ("lineas_enormes", grandes + info["troceadas"])):
        if n:
            almacen.anotar(con, quien, tipo, "", fid, f"{n} líneas", ahora=ahora)
    for texto, n in otras.items():
        almacen.anotar(con, quien, "ingesta", "", fid, f"{texto}: {n}"[:200], ahora=ahora)
    almacen.guardar_lectura(con, quien, fid, info["inodo"], info["tam"], info["mtime_ns"], info["desplazamiento"],
                            info["cab_len"], info["hash_cab"], ahora=ahora)
    total.update(lineas=len(lineas), rotas=rotas, completo=info["completo"])
    return total
