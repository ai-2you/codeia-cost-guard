"""Apertura segura de una base SQLite del proyecto, compartida por el almacén de consumo y el de servidores.

Fija en cada conexión los PRAGMA de seguridad (`foreign_keys`, `secure_delete`; las conexiones de escritura, además, WAL con límite de journal),
rechaza rutas en la nube o de red, deja el directorio y los ficheros en 0600 y migra con la función que le pasa cada almacén.
Solo-lectura es `mode=ro` con `query_only`. No sabe nada de ningún esquema."""
import os
import pathlib
import sqlite3
import time

from . import plataforma

SQLITE_MIN = (3, 37, 0)                      # STRICT


class ErrorAlmacen(Exception):
    """La base no se puede abrir o usar con seguridad (ruta no fiable, versión, WAL...)."""


def _privado(ruta, modo):
    if os.name == "nt":
        return
    try:
        os.chmod(ruta, modo)
    except OSError:
        pass


def abrir_base(ruta, migrar, solo_lectura=False, espera_ms=2000):
    """Conexión con los PRAGMA de seguridad puestos. Escritura: crea la base y llama a `migrar(con, ruta)`. Lectura: `mode=ro`."""
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
        con = sqlite3.connect(f"{pathlib.Path(ruta).as_uri()}?mode=ro", uri=True, isolation_level=None, timeout=espera_ms / 1000)
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
            modo = _con_reintentos(lambda: con.execute("PRAGMA journal_mode=WAL").fetchone()[0], espera_ms)
            if str(modo).lower() != "wal":
                raise ErrorAlmacen(f"este volumen no admite WAL (journal_mode={modo}): usa un disco local")
            con.execute("PRAGMA journal_size_limit=67108864")
            con.execute("PRAGMA synchronous=NORMAL")
            _con_reintentos(lambda: migrar(con, ruta), espera_ms)
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



def _con_reintentos(accion, espera_ms):
    """Cambiar a WAL o migrar mientras otro proceso crea la base a la vez puede devolver «locked» sin pasar por el busy_timeout:
    se reintenta unos instantes antes de rendirse."""
    limite = time.monotonic() + max(espera_ms, 1000) / 1000
    while True:
        try:
            return accion()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or time.monotonic() > limite:
                raise
            time.sleep(0.02)


def _tablas(con):
    return {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _sentencias(sql):
    """Sentencias de un script, cortadas con el propio analizador de SQLite (un ';' en un comentario o un literal no corta)."""
    actual = ""
    for linea in sql.splitlines(keepends=True):
        actual += linea
        if sqlite3.complete_statement(actual):
            if actual.strip():
                yield actual.strip()
            actual = ""
    if actual.strip():
        raise ErrorAlmacen("esquema incompleto")


def _version(con):
    return con.execute("PRAGMA user_version").fetchone()[0]


def _rollback(con):
    """ROLLBACK que no tapa el error original si SQLite ya deshizo la transacción (disco lleno, etc.)."""
    try:
        con.execute("ROLLBACK")
    except sqlite3.Error:
        pass
