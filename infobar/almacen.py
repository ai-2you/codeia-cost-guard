"""Almacén SQLite del consumo medido: turnos de solo añadir, con escritura monótona y un único escritor.

Es el ÚNICO sitio que habla SQL con la base. Reglas (plans/261013-2350-sqlite-historico/plan.md):
  · un turno es una fila con clave de contenido (prefijo de agente); releerlo nunca cambia el resultado y su
    contador solo puede subir (max por contador). Un cambio de modelo u hora, una cifra fuera de rango o un
    contador que baja NO se aplican: quedan en `anomalias`, visibles. Nunca se borra por fuente.
  · la hora va en segundos UTC; lo local se deriva al consultar.
  · `abrir()` fija los PRAGMA en cada conexión (son por conexión) y comprueba WAL; solo-lectura es `mode=ro`.
  · un solo escritor por arrendamiento con latido en `meta`; los demás procesos solo leen.
  · todo parametrizado: los nombres de columna de las agrupaciones salen de una lista cerrada.
"""
import os
import sqlite3
import time
import uuid

from . import plataforma, rutas

VERSION_ESQUEMA = 1
VERSION_PRODUCTOR = 1
SQLITE_MIN = (3, 37, 0)                      # STRICT
LATIDO_TTL_S = 90                            # sin latido en este tiempo, otro proceso puede tomar el relevo
LIMITE_CONTADOR = 10 ** 12                   # un turno real no pasa de millones; más es un log hostil o roto
HORA_MIN, HORA_MAX = 946_684_800, 4_102_444_800     # 2000-01-01 .. 2100-01-01
MAX_ANOMALIAS_POR_LOTE = 50
COLUMNAS_AGRUPABLES = {"hora", "agente", "proveedor", "modelo", "proyecto_raw"}
CONTADORES = ("gi", "go", "cr", "cw")


class ErrorAlmacen(Exception):
    """La base no se puede abrir o usar con seguridad (ruta no fiable, versión, WAL...)."""


class SinEscritura(ErrorAlmacen):
    """Este proceso no es el escritor vigente."""


ESQUEMA_V1 = """
CREATE TABLE meta(clave TEXT PRIMARY KEY, valor TEXT NOT NULL) STRICT;
CREATE TABLE espacios(
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE CHECK(length(nombre) BETWEEN 1 AND 80)) STRICT;
CREATE TABLE fuentes(
    id INTEGER PRIMARY KEY,
    espacio_id INTEGER NOT NULL REFERENCES espacios(id) ON DELETE CASCADE,
    dispositivo_id TEXT NOT NULL CHECK(length(dispositivo_id) BETWEEN 8 AND 64),
    agente TEXT NOT NULL CHECK(length(agente) BETWEEN 1 AND 40),
    id_fuente TEXT NOT NULL CHECK(length(id_fuente) BETWEEN 1 AND 200),
    visto_en INTEGER NOT NULL,
    UNIQUE(espacio_id, dispositivo_id, agente, id_fuente)) STRICT;
CREATE TABLE turnos(
    clave TEXT PRIMARY KEY CHECK(length(clave) BETWEEN 3 AND 300),
    espacio_id INTEGER NOT NULL REFERENCES espacios(id) ON DELETE CASCADE,
    fuente_id INTEGER NOT NULL REFERENCES fuentes(id) ON DELETE CASCADE,
    hora_utc INTEGER NOT NULL CHECK(hora_utc BETWEEN 946684800 AND 4102444800),
    agente TEXT NOT NULL CHECK(length(agente) BETWEEN 1 AND 40),
    proveedor TEXT NOT NULL DEFAULT '' CHECK(length(proveedor) <= 120),
    modelo TEXT NOT NULL CHECK(length(modelo) BETWEEN 1 AND 120),
    gi INTEGER NOT NULL CHECK(gi BETWEEN 0 AND 1000000000000),
    go INTEGER NOT NULL CHECK(go BETWEEN 0 AND 1000000000000),
    cr INTEGER NOT NULL CHECK(cr BETWEEN 0 AND 1000000000000),
    cw INTEGER NOT NULL CHECK(cw BETWEEN 0 AND 1000000000000),
    proyecto_raw TEXT NOT NULL DEFAULT '' CHECK(length(proyecto_raw) <= 200),
    productor_version INTEGER NOT NULL,
    actualizado_en INTEGER NOT NULL) STRICT;
CREATE INDEX turnos_espacio_hora ON turnos(espacio_id, hora_utc);
CREATE TABLE horas(
    espacio_id INTEGER NOT NULL REFERENCES espacios(id) ON DELETE CASCADE,
    hora INTEGER NOT NULL,
    agente TEXT NOT NULL, proveedor TEXT NOT NULL, modelo TEXT NOT NULL, proyecto_raw TEXT NOT NULL,
    gi INTEGER NOT NULL, go INTEGER NOT NULL, cr INTEGER NOT NULL, cw INTEGER NOT NULL, turnos INTEGER NOT NULL,
    PRIMARY KEY(espacio_id, hora, agente, proveedor, modelo, proyecto_raw)) STRICT, WITHOUT ROWID;
CREATE TABLE anomalias(
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    tipo TEXT NOT NULL CHECK(length(tipo) <= 40),
    clave TEXT NOT NULL DEFAULT '' CHECK(length(clave) <= 300),
    fuente_id INTEGER,
    detalle TEXT NOT NULL DEFAULT '' CHECK(length(detalle) <= 200)) STRICT;
"""


# ---------------------------------------------------------------- conexión

def _privado(ruta, modo):
    if os.name == "nt":
        return
    try:
        os.chmod(ruta, modo)
    except OSError:
        pass


def abrir(ruta=None, solo_lectura=False, espera_ms=2000):
    """Conexión con los PRAGMA de seguridad ya puestos. Escritura: crea y migra la base. Lectura: `mode=ro`."""
    por_defecto = ruta is None
    if por_defecto:
        if not solo_lectura and plataforma.es_checkout_de_desarrollo(rutas.RAIZ):
            raise ErrorAlmacen("un checkout de desarrollo no escribe en los datos reales: define COSTGUARD_DATOS")
        ruta = rutas.ALMACEN
    ruta = os.path.abspath(ruta)
    if sqlite3.sqlite_version_info < SQLITE_MIN:
        raise ErrorAlmacen(f"hace falta SQLite >= 3.37 (esta es {sqlite3.sqlite_version}); usa Python 3.11 o superior")
    motivo = plataforma.ruta_no_fiable(ruta)
    if motivo:
        raise ErrorAlmacen(f"la base no puede estar en {motivo}: WAL puede corromperse y los datos se copiarían fuera")
    directorio = os.path.dirname(ruta)
    if solo_lectura:
        if not os.path.exists(ruta):
            raise FileNotFoundError(ruta)
        con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True, isolation_level=None, timeout=espera_ms / 1000)
        con.execute("PRAGMA query_only=ON")
    else:
        os.makedirs(directorio, mode=0o700, exist_ok=True)
        _privado(directorio, 0o700)
        nueva = not os.path.exists(ruta)
        con = sqlite3.connect(ruta, isolation_level=None, timeout=espera_ms / 1000)
        if nueva:
            _privado(ruta, 0o600)
    con.row_factory = sqlite3.Row
    try:
        con.execute(f"PRAGMA busy_timeout={int(espera_ms)}")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA secure_delete=ON")
        if not solo_lectura:
            modo = con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(modo).lower() != "wal":
                raise ErrorAlmacen(f"este volumen no admite WAL (journal_mode={modo}): usa un disco local")
            con.execute("PRAGMA journal_size_limit=67108864")
            con.execute("PRAGMA synchronous=NORMAL")
            _migrar(con, ruta)
            for sufijo in ("-wal", "-shm"):
                if os.path.exists(ruta + sufijo):
                    _privado(ruta + sufijo, 0o600)
        else:
            if con.execute("PRAGMA foreign_keys").fetchone()[0] != 1 or con.execute("PRAGMA secure_delete").fetchone()[0] != 1:
                raise ErrorAlmacen("PRAGMA de seguridad no aplicados")
    except Exception:
        con.close()
        raise
    return con


def _tablas(con):
    return {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _migrar(con, ruta):
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version > VERSION_ESQUEMA:
        raise ErrorAlmacen(f"la base es de una versión más nueva ({version}); actualiza la aplicación")
    if version == VERSION_ESQUEMA:
        minima = con.execute("SELECT valor FROM meta WHERE clave='version_minima_escritor'").fetchone()
        if minima and int(minima[0]) > VERSION_PRODUCTOR:
            raise ErrorAlmacen("esta versión es demasiado antigua para escribir en la base")
        return
    if _tablas(con) - {"sqlite_sequence"}:
        # hay datos de una versión anterior: copia ANTES de cambiar el esquema (regla del proyecto)
        copia(con, os.path.join(os.path.dirname(ruta), "copias"), prefijo="premigracion")
    con.execute("BEGIN IMMEDIATE")
    try:
        for sentencia in [s.strip() for s in ESQUEMA_V1.split(";") if s.strip()]:
            con.execute(sentencia)
        ahora = str(int(time.time()))
        con.executemany("INSERT INTO meta(clave, valor) VALUES(?, ?)", [
            ("version_esquema", str(VERSION_ESQUEMA)), ("version_minima_escritor", "1"),
            ("productor", str(VERSION_PRODUCTOR)), ("dispositivo_id", uuid.uuid4().hex), ("creado", ahora)])
        con.execute(f"PRAGMA user_version={VERSION_ESQUEMA}")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def meta(con, clave, defecto=None):
    f = con.execute("SELECT valor FROM meta WHERE clave=?", (clave,)).fetchone()
    return f[0] if f else defecto


# ---------------------------------------------------------------- escritor único

def adquirir_escritura(con, quien, ahora=None, ttl=LATIDO_TTL_S):
    """True si `quien` es (o pasa a ser) el escritor. El anterior pierde el puesto solo si lleva `ttl` s sin latido."""
    ahora = int(ahora if ahora is not None else time.time())
    con.execute("BEGIN IMMEDIATE")
    try:
        actual, latido = meta(con, "escritor"), int(meta(con, "escritor_latido", "0"))
        if actual in (None, "", quien) or ahora - latido >= ttl:
            con.executemany("INSERT INTO meta(clave, valor) VALUES(?, ?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                            [("escritor", quien), ("escritor_latido", str(ahora))])
            con.execute("COMMIT")
            return True
        con.execute("ROLLBACK")
        return False
    except Exception:
        con.execute("ROLLBACK")
        raise


def soltar_escritura(con, quien):
    con.execute("BEGIN IMMEDIATE")
    if meta(con, "escritor") == quien:
        con.execute("UPDATE meta SET valor='' WHERE clave='escritor'")
    con.execute("COMMIT")


def _exige_escritor(con, quien, ahora):
    if meta(con, "escritor") != quien or ahora - int(meta(con, "escritor_latido", "0")) >= LATIDO_TTL_S:
        raise SinEscritura("este proceso no es el escritor vigente de la base")


# ---------------------------------------------------------------- escritura

def espacio(con, nombre="local"):
    con.execute("INSERT OR IGNORE INTO espacios(nombre) VALUES(?)", (nombre,))
    return con.execute("SELECT id FROM espacios WHERE nombre=?", (nombre,)).fetchone()[0]


def fuente(con, espacio_id, agente, id_fuente, dispositivo_id=None, ahora=None):
    """Id de la fuente (un log, una sesión, una base ajena), creándola si hace falta. `id_fuente` no lleva rutas absolutas."""
    dispositivo_id = dispositivo_id or meta(con, "dispositivo_id")
    ahora = int(ahora if ahora is not None else time.time())
    con.execute("INSERT OR IGNORE INTO fuentes(espacio_id, dispositivo_id, agente, id_fuente, visto_en) VALUES(?,?,?,?,?)",
                (espacio_id, dispositivo_id, agente, id_fuente, ahora))
    con.execute("UPDATE fuentes SET visto_en=? WHERE espacio_id=? AND dispositivo_id=? AND agente=? AND id_fuente=?",
                (ahora, espacio_id, dispositivo_id, agente, id_fuente))
    return con.execute("SELECT id FROM fuentes WHERE espacio_id=? AND dispositivo_id=? AND agente=? AND id_fuente=?",
                       (espacio_id, dispositivo_id, agente, id_fuente)).fetchone()[0]


def _entero(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _problema(t):
    """Texto del problema de un turno entrante (o "" si es válido). No se confía en nada que venga de un log."""
    if not isinstance(t, dict):
        return "no es un objeto"
    clave = t.get("clave")
    if not (isinstance(clave, str) and 3 <= len(clave) <= 300):
        return "clave no válida"
    if not (_entero(t.get("hora_utc")) and HORA_MIN <= t["hora_utc"] <= HORA_MAX):
        return "hora_utc fuera de rango"
    for c in CONTADORES:
        v = t.get(c, 0)
        if not (_entero(v) and 0 <= v <= LIMITE_CONTADOR):
            return f"{c} fuera de rango"
    for campo, maximo in (("agente", 40), ("modelo", 120), ("proveedor", 120), ("proyecto_raw", 200)):
        v = t.get(campo, "")
        if not isinstance(v, str) or len(v) > maximo or (campo in ("agente", "modelo") and not v):
            return f"{campo} no válido"
    return ""


def _anomalia(con, ts, tipo, clave, fuente_id, detalle, contador):
    contador[0] += 1
    if contador[0] <= MAX_ANOMALIAS_POR_LOTE:
        con.execute("INSERT INTO anomalias(ts, tipo, clave, fuente_id, detalle) VALUES(?,?,?,?,?)",
                    (ts, tipo, str(clave)[:300] if isinstance(clave, str) else "", fuente_id, str(detalle)[:200]))


def _suma_hora(con, espacio_id, hora_utc, agente, proveedor, modelo, proyecto, d, n):
    con.execute(
        "INSERT INTO horas(espacio_id, hora, agente, proveedor, modelo, proyecto_raw, gi, go, cr, cw, turnos) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(espacio_id, hora, agente, proveedor, modelo, proyecto_raw) DO UPDATE SET "
        "gi=gi+excluded.gi, go=go+excluded.go, cr=cr+excluded.cr, cw=cw+excluded.cw, turnos=turnos+excluded.turnos",
        (espacio_id, hora_utc - hora_utc % 3600, agente, proveedor, modelo, proyecto, d[0], d[1], d[2], d[3], n))


def ingerir_turnos(con, quien, espacio_id, fuente_id, turnos, ahora=None):
    """Aplica un lote de turnos en UNA transacción. Devuelve {nuevos, subidos, iguales, anomalias}.

    Monótono: un turno existente solo sube sus contadores (max); modelo/hora/agente no se mueven nunca. El
    agregado `horas` se mantiene con el delta, en la misma transacción. Exige ser el escritor vigente."""
    ahora = int(ahora if ahora is not None else time.time())
    res = {"nuevos": 0, "subidos": 0, "iguales": 0, "anomalias": 0}
    con.execute("BEGIN IMMEDIATE")
    try:
        _exige_escritor(con, quien, ahora)
        omitidas = [0]
        for t in turnos:
            motivo = _problema(t)
            if motivo:
                res["anomalias"] += 1
                _anomalia(con, ahora, "fuera_de_rango", (t or {}).get("clave") if isinstance(t, dict) else "", fuente_id, motivo, omitidas)
                continue
            nuevo = [t.get(c, 0) for c in CONTADORES]
            previo = con.execute("SELECT hora_utc, agente, proveedor, modelo, proyecto_raw, gi, go, cr, cw FROM turnos WHERE clave=?",
                                 (t["clave"],)).fetchone()
            if previo is None:
                con.execute("INSERT INTO turnos(clave, espacio_id, fuente_id, hora_utc, agente, proveedor, modelo, gi, go, cr, cw, "
                            "proyecto_raw, productor_version, actualizado_en) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (t["clave"], espacio_id, fuente_id, t["hora_utc"], t["agente"], t.get("proveedor", ""), t["modelo"],
                             *nuevo, t.get("proyecto_raw", ""), VERSION_PRODUCTOR, ahora))
                _suma_hora(con, espacio_id, t["hora_utc"], t["agente"], t.get("proveedor", ""), t["modelo"], t.get("proyecto_raw", ""), nuevo, 1)
                res["nuevos"] += 1
                continue
            for campo, valor in (("hora_utc", t["hora_utc"]), ("agente", t["agente"]), ("modelo", t["modelo"])):
                if previo[campo] != valor:
                    res["anomalias"] += 1
                    _anomalia(con, ahora, f"cambio_{campo}", t["clave"], fuente_id, f"{previo[campo]} -> {valor}", omitidas)
            viejo = [previo["gi"], previo["go"], previo["cr"], previo["cw"]]
            if any(n < v for n, v in zip(nuevo, viejo)):
                res["anomalias"] += 1
                _anomalia(con, ahora, "contador_baja", t["clave"], fuente_id, f"{viejo} -> {nuevo}", omitidas)
            maximo = [max(n, v) for n, v in zip(nuevo, viejo)]
            if maximo == viejo:
                res["iguales"] += 1
                continue
            con.execute("UPDATE turnos SET gi=?, go=?, cr=?, cw=?, fuente_id=?, actualizado_en=?, productor_version=? WHERE clave=?",
                        (*maximo, fuente_id, ahora, VERSION_PRODUCTOR, t["clave"]))
            _suma_hora(con, espacio_id, previo["hora_utc"], previo["agente"], previo["proveedor"], previo["modelo"], previo["proyecto_raw"],
                       [m - v for m, v in zip(maximo, viejo)], 0)
            res["subidos"] += 1
        if omitidas[0] > MAX_ANOMALIAS_POR_LOTE:
            con.execute("INSERT INTO meta(clave, valor) VALUES('anomalias_omitidas', ?) ON CONFLICT(clave) DO UPDATE SET "
                        "valor=CAST(valor AS INTEGER)+excluded.valor", (str(omitidas[0] - MAX_ANOMALIAS_POR_LOTE),))
        con.execute("UPDATE meta SET valor=? WHERE clave='escritor_latido'", (str(ahora),))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return res


# ---------------------------------------------------------------- lectura

def totales(con, espacio_id, desde, hasta, agrupar=()):
    """Tokens y turnos entre `desde` y `hasta` (segundos UTC, [desde, hasta)), agrupados por columnas de una lista cerrada."""
    cols = list(agrupar)
    if not set(cols) <= COLUMNAS_AGRUPABLES:
        raise ValueError("agrupación no permitida")
    sel = "".join(f"{c}, " for c in cols)
    sql = (f"SELECT {sel}SUM(gi) AS gi, SUM(go) AS go, SUM(cr) AS cr, SUM(cw) AS cw, SUM(turnos) AS turnos "
           f"FROM horas WHERE espacio_id=? AND hora>=? AND hora<?" + (f" GROUP BY {', '.join(cols)}" if cols else ""))
    return [dict(f) for f in con.execute(sql, (espacio_id, desde, hasta))]


def anomalias(con, limite=100):
    return [dict(f) for f in con.execute("SELECT ts, tipo, clave, detalle FROM anomalias ORDER BY id DESC LIMIT ?", (int(limite),))]


# ---------------------------------------------------------------- copias y mantenimiento

def copia(con, directorio, prefijo="copia", conservar=5):
    """Copia consistente en caliente (API de backup de SQLite, no copiar el fichero), privada y rotada."""
    os.makedirs(directorio, mode=0o700, exist_ok=True)
    destino = os.path.join(directorio, f"consumo.sqlite.{prefijo}.{time.strftime('%Y%m%d-%H%M%S')}")
    tmp = destino + ".tmp"
    out = sqlite3.connect(tmp)
    try:
        con.backup(out)
    finally:
        out.close()
    _privado(tmp, 0o600)
    os.replace(tmp, destino)
    viejas = sorted(f for f in os.listdir(directorio) if f.startswith(f"consumo.sqlite.{prefijo}.") and not f.endswith(".tmp"))
    for f in viejas[:-conservar]:
        os.remove(os.path.join(directorio, f))
    return destino


def mantenimiento(con):
    """Checkpoint que trunca el WAL (journal_size_limit solo recorta tras un checkpoint)."""
    return tuple(con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
