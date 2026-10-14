"""Restaurar una copia o exportación `.cgx` sobre la base. Nada se toca hasta que el fichero está descifrado, autenticado y comprobado.

Orden: (1) toma el puesto de escritor con identidad propia (si la barra está escribiendo, se niega); (2) descifra a un temporal
privado junto al destino; (3) comprueba `user_version`, `quick_check` y que sea una base de consumo; (4) pide confirmación con lo
que trae; (5) copia de seguridad en claro de la base actual (`backup()`, nunca copiando ficheros), checkpoint, cierre y borrado de
`-wal`/`-shm` — SQLite aplicaría el WAL de la base anterior encima de la restaurada, y `quick_check` diría `ok`; si no se pueden
borrar, se aborta sin tocar nada —; (6) `os.replace` con reintentos; (7) reabre con `almacen.abrir()` para comprobarla.
"""
import os
import sqlite3
import tempfile
import time

from . import almacen, cgx, exportacion, rutas


class ErrorRestauracion(Exception):
    """No se restauró nada (o se explica qué quedó). El mensaje es apto para el usuario."""


def _borra_wal(destino):
    for sufijo in ("-wal", "-shm"):
        ruta = destino + sufijo
        if os.path.exists(ruta):
            try:
                os.remove(ruta)
            except OSError as e:
                raise ErrorRestauracion(f"no se puede borrar {os.path.basename(ruta)} ({type(e).__name__}): cierra la barra y reintenta; "
                                        "no se ha cambiado nada") from None
    return not any(os.path.exists(destino + s) for s in ("-wal", "-shm"))


def _comprueba(ruta):
    """Información de la base descifrada, o ErrorRestauracion. Se abre en solo lectura (el fichero está en journal_mode=DELETE)."""
    try:
        con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    except sqlite3.Error:
        raise ErrorRestauracion("el contenido descifrado no es una base SQLite") from None
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version > almacen.VERSION_ESQUEMA:
            raise ErrorRestauracion(f"la copia es de una versión más nueva ({version}) que esta aplicación; actualízala primero")
        if version < 1:
            raise ErrorRestauracion("el contenido descifrado no es una base de consumo")
        estado = str(con.execute("PRAGMA quick_check(1)").fetchone()[0])
        if estado != "ok":
            raise ErrorRestauracion(f"la copia descifrada está dañada (quick_check: {estado[:80]})")
        turnos = con.execute("SELECT COUNT(*) FROM turnos").fetchone()[0]
        return {"version": version, "turnos": turnos}
    except sqlite3.Error:
        raise ErrorRestauracion("el contenido descifrado no es una base de consumo válida") from None
    finally:
        con.close()


def restaurar(ruta_cgx, descifrar, confirmar, destino=None, forzar=False, ahora=None):
    """`descifrar(entrada, salida)` hace el descifrado que toque (frase o clave privada) y lanza ErrorCgx si no cuadra.
    `confirmar(info)` recibe {fecha, tipo, turnos, version, sustituye} y devuelve True para seguir. Devuelve un resumen."""
    destino = os.path.abspath(destino or rutas.ALMACEN)
    ahora = int(ahora if ahora is not None else time.time())
    existe = os.path.exists(destino)
    if existe and not forzar:
        raise ErrorRestauracion("ya hay una base en el destino: usa --forzar si quieres sustituirla (se guarda antes una copia)")
    os.makedirs(os.path.dirname(destino), mode=0o700, exist_ok=True)
    with open(ruta_cgx, "rb") as f:
        try:
            _modo, tipo, fecha, *_ = cgx.leer_cabecera(f)
        except cgx.ErrorCgx as e:
            raise ErrorRestauracion(str(e)) from None
    quien, con, temporal = almacen.identidad("restauracion"), None, None
    try:
        if existe:
            try:
                con = almacen.abrir(destino)
            except (almacen.ErrorAlmacen, sqlite3.Error) as e:
                raise ErrorRestauracion(f"no se puede abrir la base actual ({type(e).__name__}); no se ha cambiado nada") from None
            if not almacen.adquirir_escritura(con, quien, ahora=ahora):
                raise ErrorRestauracion("la barra (u otro proceso) está escribiendo en la base: ciérrala y reintenta")
        fd, temporal = tempfile.mkstemp(prefix=".restaurando-", suffix=".tmp", dir=os.path.dirname(destino))
        try:
            with os.fdopen(fd, "wb") as salida, open(ruta_cgx, "rb") as entrada:
                descifrar(entrada, salida)
        except cgx.ErrorCgx as e:
            raise ErrorRestauracion(str(e)) from None
        info = _comprueba(temporal)
        info.update(fecha=fecha, tipo=tipo, sustituye=existe)
        if not confirmar(info):
            raise ErrorRestauracion("restauración cancelada; no se ha cambiado nada")
        previa = None
        if existe:
            if not almacen.adquirir_escritura(con, quien, ahora=int(time.time())):          # el puesto sigue siendo nuestro
                raise ErrorRestauracion("se perdió el puesto de escritor durante la restauración; no se ha cambiado nada")
            previa = almacen.copia(con, rutas.COPIAS, prefijo="prerrestauracion")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            almacen.soltar_escritura(con, quien)
            con.close()
            con = None
        if not _borra_wal(destino):
            raise ErrorRestauracion("quedan ficheros -wal/-shm de la base anterior; no se ha cambiado nada")
        exportacion.reemplazar(temporal, destino)
        temporal = None
        _borra_wal(destino)
        try:
            almacen.abrir(destino).close()
        except (almacen.ErrorAlmacen, sqlite3.Error) as e:
            raise ErrorRestauracion(f"la base restaurada no se abre ({type(e).__name__})") from None
        return {"turnos": info["turnos"], "fecha_copia": fecha, "sustituyo": existe, "destino": destino, "copia_previa": previa}
    finally:
        if con is not None:
            try:
                almacen.soltar_escritura(con, quien)
            except Exception:                                # noqa: BLE001
                pass
            con.close()
        if temporal:
            exportacion._borra(temporal)
