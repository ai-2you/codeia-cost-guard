"""Hilo de copias automáticas de la barra: independiente del de ingesta (una copia lenta no retrasa el latido del escritor).

Solo necesita la clave PÚBLICA del destinatario y la de firma del dispositivo; abre la base en solo lectura (`backup()` funciona así).
Ni frases ni claves privadas pasan jamás por aquí. Un fallo se registra, se reintenta tras una hora y nunca borra copias buenas.
"""
import threading
import time
import traceback

from . import almacen, cgx, claves_copias, exportacion
from .config_y_estado import cfg, log

REVISA_CADA_S = 900
REINTENTO_TRAS_FALLO_S = 3600


def una_vez(ahora=None):
    """Una revisión: hace la copia si toca. Devuelve 'sin_destinatario' | 'no_toca' | resumen de la copia. Lanza si falla."""
    ahora = ahora if ahora is not None else time.time()
    c = cfg().get("copias") or {}
    destinatarios = c.get("destinatario") or []
    if isinstance(destinatarios, str):
        destinatarios = [destinatarios]
    if not destinatarios:
        return "sin_destinatario"
    if almacen.marcador_activo():
        return "restaurando"
    if not exportacion.toca_copia(ahora):
        return "no_toca"
    publica = cgx.decodificar_clave("publica", destinatarios[0])
    firma_priv, firma_pub = claves_copias.clave_de_firma()
    carpeta = c.get("carpeta") or exportacion.carpeta_por_defecto()
    con = almacen.abrir(solo_lectura=True)
    try:
        r = exportacion.hacer_copia(con, carpeta, publica, firma_priv, firma_pub, ahora=int(ahora))
    finally:
        con.close()
    exportacion.registrar_copia(r)
    return r


def _anota_error(mensaje):
    try:
        exportacion.registrar_error(mensaje)
    except OSError:
        pass


class HiloCopias(threading.Thread):
    def __init__(self):
        super().__init__(name="infobar-copias", daemon=True)
        self.parar = threading.Event()

    def run(self):
        exportacion.barrer_tmp()
        while not self.parar.is_set():
            espera = REVISA_CADA_S
            try:
                r = una_vez()
                if isinstance(r, dict):
                    log(f"copias: {r['archivo']} verificada ({r['bytes']} bytes, rotadas {r['rotadas']}"
                        + (", DE UNA BASE DAÑADA" if r["danada"] else "") + ")")
            except (cgx.ErrorCgx, almacen.ErrorAlmacen, FileNotFoundError, OSError) as e:
                log(f"copias: no se hizo la copia ({type(e).__name__}): {e}")
                _anota_error(f"{type(e).__name__}: {e}")
                espera = REINTENTO_TRAS_FALLO_S
            except Exception:                                # noqa: BLE001
                log("copias: " + traceback.format_exc()[-400:])
                _anota_error("fallo inesperado (mira el log)")
                espera = REINTENTO_TRAS_FALLO_S
            self.parar.wait(espera)
