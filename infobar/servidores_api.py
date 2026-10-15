"""Lo que el panel y la línea de comandos piden al historial de servidores: respuestas ya validadas, sin tocar HTTP.

`atiende(ruta, q)` devuelve (código, dict) para `/servidores/historia` y `/servidores/ips`. Las IPs solo salen de la segunda, con `servidor=` y
`con_ips=1`, y cada vez queda una línea de auditoría con el NÚMERO de IPs servidas (nunca las IPs). `olvidar` borra ya las IPs (orden `--olvidar-ips`)."""
import json
import os
import time

from . import historico, rutas, servidores_historico as sh, servidores_lectura as sl
from .config_y_estado import log
from .servidores_registro import politicas

PARAMS = {"/servidores/historia": {"servidor", "desde", "hasta", "paso"}, "/servidores/ips": {"servidor", "con_ips"}}
NO_HAY = {"disponible": False, "motivo": "Aún no hay historial de servidores: se crea con la primera lectura buena."}


def auditar_ips(servidor_id, n, origen, dato="ips"):
    """Una línea por consulta o borrado de IPs (o de recursos): cuándo, qué servidor (su id opaco) y cuántos. Nunca las IPs ni los nombres."""
    linea = json.dumps({"ts": int(time.time()), "servidor": servidor_id, dato: n, "origen": origen})
    os.makedirs(rutas.DATOS, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(rutas.DATOS, "servidores_ips.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def atiende(ruta, q, ahora=None):
    """`q` = {parámetro: [valores]} ya parseado. Rechaza parámetros desconocidos o repetidos."""
    if ruta not in PARAMS or set(q) - PARAMS[ruta] or any(len(v) != 1 for v in q.values()):
        return 400, {"error": "parámetros no permitidos"}
    g = {k: v[0] for k, v in q.items()}
    if ruta == "/servidores/ips" and ("servidor" not in g or g.get("con_ips") != "1"):
        return 400, {"error": "las IPs se piden de un servidor y con con_ips=1"}
    ahora = ahora or time.time()
    try:
        con = sh.abrir(solo_lectura=True)
    except sh.SinDatos:
        return 200, dict(NO_HAY)
    except Exception as e:
        log(f"servidores/api: {type(e).__name__}")
        return 503, {"error": "la base de servidores no se puede abrir"}
    try:
        if ruta == "/servidores/historia":
            if "servidor" not in g:
                return 200, {"disponible": True, "servidores": sl.servidores(con)}
            return 200, {"disponible": True, **sl.historia(con, g["servidor"], g.get("desde"), g.get("hasta"), g.get("paso", "dia"))}
        pol = politicas().get(g["servidor"])          # un servidor que ya no está en la config no devuelve IPs
        lista = sl.ips(con, g["servidor"], ahora, (pol or {}).get("retencion_dias"), bool(pol and pol["guardar_ips"]))
        auditar_ips(g["servidor"], len(lista), "panel")
        return 200, {"disponible": True, "ips": lista, "retencion_dias": sh.retencion_dias((pol or {}).get("retencion_dias"))}
    except historico.ErrorHistorico as e:
        return 400, {"error": str(e)}
    except sh.SinDatos:
        return 404, {"error": "servidor desconocido"}
    except Exception as e:
        log(f"servidores/api: {type(e).__name__}")
        return 500, {"error": "no se pudo leer el historial"}
    finally:
        con.close()


def olvidar(nombre=None, que="ips"):
    """Borra ya las IPs (`que="ips"`) o los recursos y eventos (`que="recursos"`, nombres incluidos) de un servidor (por nombre o id, también
    retirado) o de todos. Devuelve (código de salida, mensaje)."""
    try:
        con = sh.abrir(solo_lectura=True)
    except sh.SinDatos:
        return 0, "No hay historial de servidores: nada que borrar."
    try:
        sid = None
        if nombre:
            fila = con.execute("SELECT id FROM servidores WHERE nombre=? OR id=? ORDER BY retirado_en IS NOT NULL, creado_en DESC", (nombre, nombre)).fetchone()
            if fila is None:
                return 1, "No hay ningún servidor con ese nombre en el historial."
            sid = fila[0]
    finally:
        con.close()
    con = sh.abrir()
    try:
        if que == "recursos":
            (n, eventos), consolidado = sh.olvidar_recursos(con, sid)
        else:
            n, consolidado = sh.olvidar_ips(con, sid)
    finally:
        con.close()
    pendiente = "" if consolidado else " El borrado se consolidará en cuanto no haya lectores abiertos (vuelve a ejecutarlo)."
    if que == "recursos":
        auditar_ips(sid or "todos", n, "orden", "recursos")
        return 0, f"Recursos borrados: {n}; eventos: {eventos}." + pendiente
    auditar_ips(sid or "todos", n, "orden")
    return 0, f"IPs borradas: {n}." + pendiente
