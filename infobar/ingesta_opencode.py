"""Ingesta de OpenCode (~/.local/share/opencode/opencode*.db) al almacén.

OpenCode guarda un mensaje por fila en `message` (`data` JSON con `role`, `modelID`, `providerID`, `tokens`, `path.cwd`).
Se leen las DOS bases (`opencode.db` y `opencode-.db`, con el mismo formato) como fuentes distintas, solo `assistant` con
modelo y `tokens`. Clave `opencode:<id de mensaje>`. Avance por clave (time_updated, id): un mensaje que OpenCode actualiza
al terminar (los tokens llegan al completarse) vuelve a salir y el almacén se queda con el máximo. Si la base no ha cambiado
(tamaño y fecha de la base y su WAL) ni se abre. Las filas que OpenCode poda con el tiempo NO se borran de aquí: el
histórico sobrevive. `tokens.reasoning` NO se suma a la salida: en OpenCode va aparte (y en 550 mensajes reales supera a
`output`), pero no se sabe si algún proveedor ya lo incluye en `output`; queda pendiente de decisión del dueño.
"""
import os
import pathlib
import re
import sqlite3

from . import almacen
from .ingesta import a_epoch_utc, id_fuente, proyecto_de_slug

AGENTE = "opencode"
LOTE_FILAS = 2000
_ID = re.compile(r"^[A-Za-z0-9_-]{4,100}$")

CONSULTA = """
SELECT id, time_created, coalesce(time_updated, time_created, 0),
       json_extract(data, '$.role'), json_extract(data, '$.modelID'), json_extract(data, '$.providerID'),
       json_extract(data, '$.tokens.input'), json_extract(data, '$.tokens.output'),
       json_extract(data, '$.tokens.cache.read'), json_extract(data, '$.tokens.cache.write'),
       json_extract(data, '$.path.cwd')
FROM message
WHERE CASE WHEN json_valid(data) THEN json_extract(data, '$.modelID') END IS NOT NULL   -- una fila con JSON roto no puede tumbar la consulta
  AND (coalesce(time_updated, time_created, 0) > ? OR (coalesce(time_updated, time_created, 0) = ? AND id > ?))
ORDER BY coalesce(time_updated, time_created, 0), id LIMIT ?"""


def _firma(ruta):
    """Tamaño y fecha de la base y de su WAL (donde SQLite escribe primero)."""
    partes = []
    for f in (ruta, ruta + "-wal"):
        try:
            st = os.stat(f)
            partes.append(f"{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            partes.append("-")
    return "|".join(partes)


def _abrir_solo_lectura(ruta):
    con = sqlite3.connect(f"{pathlib.Path(ruta).as_uri()}?mode=ro", uri=True, timeout=2)
    con.execute("PRAGMA query_only=ON")
    return con


def _n(v):
    return 0 if v is None else v


def _turno(fila):
    mid, creado, _upd, rol, modelo, proveedor, ti, to, tcr, tcw, cwd = fila
    if rol != "assistant":
        return None
    if not (isinstance(mid, str) and _ID.match(mid)):
        return ("anomalia", "mensaje sin id válido")
    hora = a_epoch_utc(creado)
    if hora is None:
        return ("anomalia", "marca de tiempo ilegible")
    proyecto = proyecto_de_slug(re.sub(r"[^A-Za-z0-9]", "-", cwd)) if isinstance(cwd, str) and cwd else "sin-proyecto"
    return {"clave": f"{AGENTE}:{mid}", "hora_utc": hora, "agente": AGENTE, "proveedor": proveedor if isinstance(proveedor, str) else "",
            "modelo": modelo, "gi": _n(ti), "go": _n(to), "cr": _n(tcr), "cw": _n(tcw), "proyecto_raw": proyecto}


def ingerir_base(con, quien, espacio_id, ruta, base, max_filas=LOTE_FILAS, ahora=None):
    """Una pasada sobre una base de OpenCode. Devuelve {nuevos, subidos, iguales, anomalias, filas, completo, sin_cambios}."""
    total = {"nuevos": 0, "subidos": 0, "iguales": 0, "anomalias": 0, "filas": 0, "completo": True, "sin_cambios": False}
    fid = almacen.fuente(con, quien, espacio_id, AGENTE, id_fuente(ruta, base), ahora=ahora)
    firma = _firma(ruta)
    if almacen.leer_marca(con, fid, "firma") == firma:
        total["sin_cambios"] = True
        return total
    try:
        origen = _abrir_solo_lectura(ruta)
    except sqlite3.Error as e:
        almacen.anotar(con, quien, "ingesta", "", fid, f"fuente {fid}: base no legible: {type(e).__name__}", ahora=ahora)
        total["anomalias"] += 1
        return total
    try:
        marca = almacen.leer_marca(con, fid, "marca")
        t, _, ultimo_id = marca.partition(":")
        t = int(t) if t.lstrip("-").isdigit() else 0
        try:
            maximo = origen.execute("SELECT max(coalesce(time_updated, time_created, 0)) FROM message").fetchone()[0]
        except sqlite3.Error as e:
            almacen.anotar(con, quien, "ingesta", "", fid, f"fuente {fid}: sin tabla message: {type(e).__name__}", ahora=ahora)
            total["anomalias"] += 1
            return total
        if maximo is not None and maximo < t:                    # la base se recreó o retrocedió: se relee entera (es inocuo)
            t, ultimo_id = 0, ""
        filas = origen.execute(CONSULTA, (t, t, ultimo_id, max_filas)).fetchall()
        rotas = origen.execute("SELECT count(*) FROM message WHERE NOT coalesce(json_valid(data), 0)").fetchone()[0]
    except sqlite3.Error as e:
        almacen.anotar(con, quien, "ingesta", "", fid, f"fuente {fid}: consulta fallida: {type(e).__name__}", ahora=ahora)
        total["anomalias"] += 1
        return total
    finally:
        origen.close()
    turnos, otras = {}, ({"filas con JSON inválido": rotas} if rotas else {})
    for fila in filas:
        try:
            r = _turno(fila)
        except Exception as e:                                   # noqa: BLE001  (una fila rara no bloquea la fuente)
            otras[f"fila con error: {type(e).__name__}"] = otras.get(f"fila con error: {type(e).__name__}", 0) + 1
            continue
        if r is None:
            continue
        if isinstance(r, tuple):
            otras[r[1]] = otras.get(r[1], 0) + 1
            continue
        motivo = almacen.problema(r)
        if motivo:
            otras[f"turno descartado: {motivo}"] = otras.get(f"turno descartado: {motivo}", 0) + 1
            continue
        turnos[r["clave"]] = r
    lote = list(turnos.values())
    for i in range(0, len(lote), 500):
        res = almacen.ingerir_turnos(con, quien, espacio_id, fid, lote[i:i + 500], ahora=ahora)
        for k in ("nuevos", "subidos", "iguales", "anomalias"):
            total[k] += res[k]
    for texto, n in otras.items():
        almacen.anotar(con, quien, "ingesta", "", fid, f"fuente {fid}: {texto}: {n}"[:200], ahora=ahora)
    total["filas"] = len(filas)
    if filas:
        almacen.guardar_marca(con, quien, fid, "marca", f"{filas[-1][2]}:{filas[-1][0]}", ahora=ahora)
    total["completo"] = len(filas) < max_filas
    if total["completo"]:
        almacen.guardar_marca(con, quien, fid, "firma", firma, ahora=ahora)
    return total
