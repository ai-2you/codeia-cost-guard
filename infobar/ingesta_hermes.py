"""Ingesta de Hermes (~/.hermes/profiles/<perfil>/state.db) al almacén.

Hermes no guarda el uso por mensaje: solo contadores ACUMULADOS por (sesión, modelo, tarea) en `session_model_usage`, con
`first_seen` y `last_seen`. Decisión del dueño: cuentan también las tareas auxiliares (títulos, resúmenes...), que suman entre
un 17 % y un 47 % más que `sessions`. Cada fila es UN turno con clave `hermes:<sesión>:<huella de la clave primaria: modelo, proveedor, URL, modo y tarea>`: los
contadores acumulados suben con el tiempo y el almacén se queda con el máximo (idempotente). La hora es `first_seen`, que no
cambia (con `last_seen` la identidad se movería en cada lectura): todo el consumo de una sesión larga cae en su primera hora,
igual que el lector anterior (que usaba `started_at`); repartirlo por tiempo exigiría guardar deltas y perdería la
idempotencia. Jamás se usa la hora de la lectura. El proyecto sale del `cwd` de la sesión. `reasoning_tokens` NO se suma: en
Hermes ya va dentro de `output_tokens`. Cada base es una fuente: la raíz `~/.hermes/state.db` (perfil por defecto, la que más
consume y que el lector anterior no leía) y una por perfil; el nombre del perfil no entra en la clave.
"""
import hashlib
import re
import sqlite3

from . import almacen
from .ingesta import a_epoch_utc, abrir_sqlite_solo_lectura, aplicar_filas, firma_sqlite, id_fuente, proyecto_de_slug

AGENTE = "hermes"
_SESION = re.compile(r"^[A-Za-z0-9_.:-]{4,100}$")

CONSULTA = """
SELECT u.session_id, u.model, u.task, u.billing_provider, u.billing_base_url, u.billing_mode,
       u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens, u.first_seen, s.cwd
FROM session_model_usage u LEFT JOIN sessions s ON s.id = u.session_id"""


def bases(home):
    """Bases de Hermes: la raíz (perfil por defecto) y las de cada perfil."""
    import glob
    import os
    raiz = os.path.join(home, ".hermes", "state.db")
    return ([raiz] if os.path.exists(raiz) else []) + sorted(glob.glob(os.path.join(home, ".hermes", "profiles", "*", "state.db")))


def _url_publica(url):
    """`proveedor` sin credenciales ni consulta: solo esquema, host(:puerto) y ruta. Una URL con usuario, contraseña o `?token=` no
    puede acabar en la base (ni, por tanto, en exportaciones). Si no cabe, esquema y host; si no es una URL, el texto recortado."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url)
        if u.scheme and u.hostname:
            host = u.hostname + (f":{u.port}" if u.port else "")
            completa = f"{u.scheme}://{host}{u.path.rstrip('/')}"
            return completa if len(completa) <= 120 else f"{u.scheme}://{host}"
    except ValueError:
        pass
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", url)[:120]


def _n(v):
    return 0 if v is None else v


def _huella(*campos):
    """Huella de la clave primaria REAL de la tabla (sesión, modelo, proveedor, URL, modo de facturación y tarea): la misma
    sesión y modelo con dos proveedores distintos son filas distintas, y si se fundieran por el máximo se perdería consumo."""
    return hashlib.sha1("\x1f".join(str(c or "") for c in campos).encode("utf-8", "replace")).hexdigest()[:12]


def _turno(fila):
    sesion, modelo, tarea, bprov, burl, modo, ti, to, tcr, tcw, primera, cwd = fila
    if not (isinstance(sesion, str) and _SESION.match(sesion)):
        return ("anomalia", "sesión sin id válido")
    if not (isinstance(modelo, str) and modelo):
        return ("anomalia", "fila sin modelo")
    hora = a_epoch_utc(primera)
    if hora is None:
        return ("anomalia", "first_seen ilegible")
    proveedor = _url_publica(burl) if isinstance(burl, str) and burl else (re.sub(r"[\x00-\x1f\x7f-\x9f]", "", bprov)[:120] if isinstance(bprov, str) else "")
    proyecto = proyecto_de_slug(re.sub(r"[^A-Za-z0-9]", "-", cwd)) if isinstance(cwd, str) and cwd else "hermes/otros"
    return {"clave": f"{AGENTE}:{sesion}:{_huella(modelo, bprov, burl, modo, tarea)}", "hora_utc": hora, "agente": AGENTE, "proveedor": proveedor,
            "modelo": modelo, "gi": _n(ti), "go": _n(to), "cr": _n(tcr), "cw": _n(tcw), "proyecto_raw": proyecto}


def ingerir_base(con, quien, espacio_id, ruta, base, ahora=None):
    """Una pasada sobre la base de un perfil de Hermes. La tabla es pequeña: se lee entera cuando la base cambia."""
    total = {"nuevos": 0, "subidos": 0, "iguales": 0, "anomalias": 0, "filas": 0, "sin_cambios": False}
    fid = almacen.fuente(con, quien, espacio_id, AGENTE, id_fuente(ruta, base), ahora=ahora)
    firma = firma_sqlite(ruta)
    if almacen.leer_marca(con, fid, "firma") == firma:
        total["sin_cambios"] = True
        return total
    try:
        origen = abrir_sqlite_solo_lectura(ruta)
        try:
            filas = origen.execute(CONSULTA).fetchall()
        finally:
            origen.close()
    except (sqlite3.Error, ValueError, OSError) as e:
        almacen.anotar(con, quien, "ingesta", "", fid, f"fuente {fid}: base no legible o sin session_model_usage: {type(e).__name__}", ahora=ahora)
        total["anomalias"] += 1
        return total
    res = aplicar_filas(con, quien, espacio_id, fid, filas, _turno, ahora=ahora)
    for k in ("nuevos", "subidos", "iguales", "anomalias"):
        total[k] += res[k]
    total["filas"] = len(filas)
    almacen.guardar_marca(con, quien, fid, "firma", firma, ahora=ahora)
    return total
