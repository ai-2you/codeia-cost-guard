"""Ingesta de OpenCode (~/.local/share/opencode/opencode*.db) al almacén.

OpenCode guarda un mensaje por fila en `message` (`data` JSON con `role`, `modelID`, `providerID`, `tokens`, `path.cwd`).
Se leen las DOS bases (`opencode.db` y `opencode-.db`, con el mismo formato) como fuentes distintas, solo `assistant` con
modelo y `tokens`. Clave `opencode:<id de mensaje>`. Avance por clave (time_updated, id): un mensaje que OpenCode actualiza
al terminar (los tokens llegan al completarse) vuelve a salir y el almacén se queda con el máximo. Si la base no ha cambiado
(tamaño y fecha de la base y su WAL) ni se abre. Las filas que OpenCode poda con el tiempo NO se borran de aquí: el
histórico sobrevive. `tokens.reasoning` SÍ se suma a la salida: en OpenCode va aparte de `output` (en 550 mensajes reales lo
supera, en todos los proveedores que lo usan) y los totales de sesión de OpenCode tampoco lo incluyen en `output`. Las filas se
re-leen con un margen de 5 minutos desde la marca (un mensaje confirmado tarde por otro proceso de OpenCode con un
`time_updated` menor que la marca no se pierde); durante un drenado paginado se usa la marca exacta.
"""
import re
import sqlite3

from . import almacen
from .ingesta import a_epoch_utc, abrir_sqlite_solo_lectura, aplicar_filas, firma_sqlite, id_fuente, proyecto_de_slug

AGENTE = "opencode"
LOTE_FILAS = 2000
SOLAPE_MS = 5 * 60 * 1000                    # margen de relectura al empezar un drenado nuevo
TU = "CAST(coalesce(time_updated, time_created, 0) AS INTEGER)"      # siempre un entero, aunque la base guarde texto o decimales
_ID = re.compile(r"^[A-Za-z0-9_-]{4,100}$")

CONSULTA = """
SELECT id, time_created, CAST(coalesce(time_updated, time_created, 0) AS INTEGER),
       json_extract(data, '$.role'), json_extract(data, '$.modelID'), json_extract(data, '$.providerID'),
       json_extract(data, '$.tokens.input'), json_extract(data, '$.tokens.output'), json_extract(data, '$.tokens.reasoning'),
       json_extract(data, '$.tokens.cache.read'), json_extract(data, '$.tokens.cache.write'),
       json_extract(data, '$.path.cwd')
FROM message
WHERE CASE WHEN json_valid(data) THEN json_extract(data, '$.modelID') END IS NOT NULL   -- una fila con JSON roto no puede tumbar la consulta
  AND (CAST(coalesce(time_updated, time_created, 0) AS INTEGER) > ? OR (CAST(coalesce(time_updated, time_created, 0) AS INTEGER) = ? AND id > ?))
ORDER BY CAST(coalesce(time_updated, time_created, 0) AS INTEGER), id LIMIT ?"""


def _n(v):
    return 0 if v is None else v


def _suma_salida(salida, razonamiento):
    """output + reasoning (van aparte en OpenCode). Un tipo raro en cualquiera de los dos se deja pasar tal cual para que la
    validación del almacén descarte el turno en vez de adivinar."""
    salida, razonamiento = _n(salida), _n(razonamiento)
    if all(isinstance(x, int) and not isinstance(x, bool) for x in (salida, razonamiento)):
        return salida + razonamiento
    return salida if isinstance(salida, str) or isinstance(salida, float) else razonamiento


def _turno(fila):
    mid, creado, _upd, rol, modelo, proveedor, ti, to, razonamiento, tcr, tcw, cwd = fila
    if rol != "assistant":
        return None
    if not (isinstance(mid, str) and _ID.match(mid)):
        return ("anomalia", "mensaje sin id válido")
    hora = a_epoch_utc(creado)
    if hora is None:
        return ("anomalia", "marca de tiempo ilegible")
    proyecto = proyecto_de_slug(re.sub(r"[^A-Za-z0-9]", "-", cwd)) if isinstance(cwd, str) and cwd else "sin-proyecto"
    return {"clave": f"{AGENTE}:{mid}", "hora_utc": hora, "agente": AGENTE, "proveedor": proveedor if isinstance(proveedor, str) else "",
            "modelo": modelo, "gi": _n(ti), "go": _suma_salida(to, razonamiento), "cr": _n(tcr), "cw": _n(tcw), "proyecto_raw": proyecto}


def ingerir_base(con, quien, espacio_id, ruta, base, max_filas=LOTE_FILAS, ahora=None):
    """Una pasada sobre una base de OpenCode. Devuelve {nuevos, subidos, iguales, anomalias, filas, completo, sin_cambios}."""
    total = {"nuevos": 0, "subidos": 0, "iguales": 0, "anomalias": 0, "filas": 0, "completo": True, "sin_cambios": False}
    fid = almacen.fuente(con, quien, espacio_id, AGENTE, id_fuente(ruta, base), ahora=ahora)
    firma = firma_sqlite(ruta)
    if almacen.leer_marca(con, fid, "firma") == firma:
        total["sin_cambios"] = True
        return total
    try:
        origen = abrir_sqlite_solo_lectura(ruta)
    except (sqlite3.Error, ValueError, OSError) as e:
        almacen.anotar(con, quien, "ingesta", "", fid, f"fuente {fid}: base no legible: {type(e).__name__}", ahora=ahora)
        total["anomalias"] += 1
        return total
    try:
        marca = almacen.leer_marca(con, fid, "marca")
        t, _, ultimo_id = marca.partition(":")
        t = int(t) if t.lstrip("-").isdigit() else 0
        guardada = t
        if t and almacen.leer_marca(con, fid, "drenando") != "1":       # drenado nuevo: se retrocede el margen; a mitad de uno, marca exacta
            t, ultimo_id = max(t - SOLAPE_MS, 0), ""
        try:
            maximo = origen.execute("SELECT max(CAST(coalesce(time_updated, time_created, 0) AS INTEGER)) FROM message").fetchone()[0]
        except sqlite3.Error as e:
            almacen.anotar(con, quien, "ingesta", "", fid, f"fuente {fid}: sin tabla message: {type(e).__name__}", ahora=ahora)
            total["anomalias"] += 1
            return total
        if maximo is not None and maximo < guardada:             # la base se recreó o retrocedió: se relee entera (es inocuo)
            t, ultimo_id = 0, ""
        filas = origen.execute(CONSULTA, (t, t, ultimo_id, max_filas)).fetchall()
        rotas = origen.execute("SELECT count(*) FROM message WHERE NOT coalesce(json_valid(data), 0)").fetchone()[0]
    except sqlite3.Error as e:
        almacen.anotar(con, quien, "ingesta", "", fid, f"fuente {fid}: consulta fallida: {type(e).__name__}", ahora=ahora)
        total["anomalias"] += 1
        return total
    finally:
        origen.close()
    res = aplicar_filas(con, quien, espacio_id, fid, filas, _turno, ahora=ahora, extra={"hay filas con JSON inválido": 1} if rotas else None)
    for k in ("nuevos", "subidos", "iguales", "anomalias"):
        total[k] += res[k]
    total["filas"] = len(filas)
    if filas:
        almacen.guardar_marca(con, quien, fid, "marca", f"{filas[-1][2]}:{filas[-1][0]}", ahora=ahora)
    total["completo"] = len(filas) < max_filas
    almacen.guardar_marca(con, quien, fid, "drenando", "0" if total["completo"] else "1", ahora=ahora)
    if total["completo"]:
        almacen.guardar_marca(con, quien, fid, "firma", firma, ahora=ahora)
    return total
