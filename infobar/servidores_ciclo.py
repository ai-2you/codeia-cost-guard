"""Ciclo de recogida de los servidores y su cache (logs/servidores.json).

· Normal: cada `intervalo_normal_s` (300 s) y al pulsar Refrescar.
· Modo incidencia: el servidor en ambar/rojo, o el activado a mano (30 min), cada
  `intervalo_incidencia_s` (60 s), solo el.
· Nunca dos ciclos a la vez; cada servidor con su tope (servidores_ssh.TOPE_S) y el ciclo entero
  con el suyo, asi que un servidor colgado no frena a los demas.
· La cache se fusiona: un fallo conserva el ultimo dato bueno y su hora; la interfaz lo pinta gris
  ("obsoleto") si es viejo.
"""
import concurrent.futures, math, os, threading, time

from . import rutas
from .config_y_estado import _bloqueo, _escribe_json, _lee_json, cfg, leer_ui, log
from .servidores import errores_ips, errores_servidores
from .servidores_estado import bastionado, peor, resumen_bastionado, tarjetas
from .servidores_externo import host_de_ssh, medir_web, puertos_expuestos
from .servidores_ssh import leer

INTERVALO_NORMAL = 300
INTERVALO_INCIDENCIA = 60
INCIDENCIA_MANUAL_S = 30 * 60
# Si la clave no entra, reintentar a menudo solo suma intentos fallidos y fail2ban acaba bloqueando
# la IP del Mac (tambien para tu propio ssh). Esos errores esperan media hora o a Refrescar.
ERRORES_SIN_REINTENTO = ("autenticacion", "clave de host no confiada", "falta la clave ~/.ssh/infobar_ed25519")
ESPERA_TRAS_RECHAZO = 30 * 60
CACHE_IPS_VIDA_S = 48 * 3600       # las IPs de intentos de acceso y de sesiones no se conservan en la caché más de 48 h
HILOS = 4
TOPE_POR_TANDA = 32         # s: ssh (20) + webs y puertos en paralelo; con N servidores, ceil(N/4) tandas
_EN_MARCHA = threading.Lock()


def intervalos(c=None):
    c = c or cfg()

    def entero(clave, defecto, minimo):
        try:
            return max(minimo, int(c.get(clave) or defecto))
        except (TypeError, ValueError):
            return defecto
    # minimos para no martillear los servidores (ni a fail2ban) con una config descuidada
    return entero("intervalo_normal_s", INTERVALO_NORMAL, 60), entero("intervalo_incidencia_s", INTERVALO_INCIDENCIA, 30)


def leer_cache():
    return _lee_json(rutas.SERVIDORES)


def rechazado(previo):
    return (previo or {}).get("error") in ERRORES_SIN_REINTENTO


def en_incidencia(nombre, previo, ui, ahora):
    if rechazado(previo):
        return False                    # rojo por clave rechazada: no es un ataque, no se insiste
    manual = (ui.get("incidencia") or {}).get(nombre) or 0
    # solo la salud VIVA (carga, webs, servicios, intentos, puertos) acelera la recogida: lo que
    # falta de proteccion o un reinicio pendiente no cambia por mirarlo cada minuto
    return ahora < manual or (previo or {}).get("estado_vivo") in ("ambar", "rojo")


def activar_incidencia(nombre, ahora=None):
    """Modo incidencia a mano para un servidor, durante INCIDENCIA_MANUAL_S."""
    from .config_y_estado import guardar_ui
    ahora = ahora or time.time()
    inc = {k: v for k, v in (leer_ui().get("incidencia") or {}).items() if v > ahora}
    inc[nombre] = ahora + INCIDENCIA_MANUAL_S
    guardar_ui({"incidencia": inc})


def recoger_uno(servidor, conocidas):
    """Lee un servidor (SSH + webs + puertos desde fuera) y calcula su estado."""
    datos, error, dur = leer(servidor)
    webs = [medir_web(u) for u in (servidor.get("webs") or [])[:4]]
    host = host_de_ssh(servidor["ssh"], servidor.get("puerto") or 22)      # IP o nombre real (se muestra)
    expuestos = None
    if datos and datos.get("puertos"):
        expuestos = puertos_expuestos(host, datos["puertos"])
    if error:
        return {"error": error, "duracion": dur, "webs": webs, "host": host, "estado": "rojo", "estado_vivo": "rojo"}
    permitidos = {int(servidor.get("puerto") or 22)}       # el ssh en otro puerto no es "raro"
    t = tarjetas(datos, webs, expuestos, conocidas, permitidos)
    checks = bastionado(datos, expuestos, permitidos)
    res = resumen_bastionado(checks)
    vivo = peor(*[x["estado"] for k, x in t.items() if k != "mantenimiento"])
    return {"error": "", "duracion": dur, "datos": datos, "webs": webs, "expuestos": expuestos, "host": host,
            "tarjetas": t, "bastionado": checks, "bastionado_resumen": res,
            "estado_vivo": vivo, "estado": peor(vivo, _estado_t(t, "mantenimiento"), res["estado"])}


def _estado_t(t, clave):
    return (t.get(clave) or {}).get("estado")


def toca(nombre, previo, ui, ahora, forzar, normal, incidencia):
    if forzar or not previo:
        return True
    hace = ahora - (previo.get("intento_en") or 0)
    if rechazado(previo):
        return hace >= ESPERA_TRAS_RECHAZO
    return hace >= (incidencia if en_incidencia(nombre, previo, ui, ahora) else normal) - 5


def ciclo(forzar=False, historial=None):
    """Recoge los servidores a los que les toca. Devuelve la cache (o None si ya habia un ciclo).
    `historial(servidores, cache, nuevos, ahora)` anota lo recogido en la base de servidores: solo lo pasa el hilo trabajador de la barra
    (el unico escritor), se llama fuera del cerrojo de la cache y un fallo suyo no afecta al ciclo."""
    if not _EN_MARCHA.acquire(blocking=False):
        return None
    try:
        c = cfg()
        servidores = c.get("servidores") or []
        if errores_servidores(servidores):
            log("servidores: configuracion no valida, no se recoge nada")
            return leer_cache()
        conocidas = [] if errores_ips(c.get("ips_conocidas")) else (c.get("ips_conocidas") or [])
        normal, incidencia = intervalos(c)
        ui, ahora = leer_ui(), time.time()
        cache = leer_cache().get("servidores") or {}
        pendientes = [s for s in servidores
                      if toca(s["nombre"], cache.get(s["nombre"]), ui, ahora, forzar, normal, incidencia)]
        if not pendientes and set(cache) == {s["nombre"] for s in servidores}:
            return leer_cache()                 # nada que recoger: la cache (y su hora) no cambian
        nuevos = {}
        if pendientes:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=HILOS)
            futuros = {pool.submit(recoger_uno, s, conocidas): s["nombre"] for s in pendientes}
            tope = math.ceil(len(pendientes) / HILOS) * TOPE_POR_TANDA
            try:
                for f in concurrent.futures.as_completed(futuros, timeout=tope):
                    try:
                        nuevos[futuros[f]] = f.result()
                    except Exception as e:
                        nuevos[futuros[f]] = {"error": f"{type(e).__name__}: {e}"[:120], "estado": "rojo"}
            except concurrent.futures.TimeoutError:
                for f, n in futuros.items():
                    if n not in nuevos:
                        nuevos[n] = {"error": "timeout", "estado": "rojo"}
            # lo que no arranco no arranca ya (si no, lanzaria ssh tras soltar el cerrojo del ciclo);
            # lo que esta en marcha tiene su propio tope de ssh
            pool.shutdown(wait=False, cancel_futures=True)
        datos = _guardar(servidores, cache, nuevos, ahora)
        if historial and nuevos:
            try:
                historial(servidores, datos["servidores"], set(nuevos), ahora)
            except Exception as e:
                log(f"servidores: el historial falló ({type(e).__name__})")
        return datos
    finally:
        _EN_MARCHA.release()


def podar_ips(entrada, ahora, vida_s=CACHE_IPS_VIDA_S):
    """Quita de una entrada de la caché las IPs (de intentos de acceso y de sesiones) cuando su último dato bueno es más viejo que `vida_s`.
    Devuelve la misma entrada, modificada. La caché guarda el último dato bueno sin límite; las IPs son datos personales y no deben vivir más."""
    if ahora - (entrada.get("ok_en") or 0) <= vida_s:
        return entrada
    datos = entrada.get("datos")
    if isinstance(datos, dict):
        it = datos.get("intentos")
        if isinstance(it, dict):
            it["top"] = []
        for s in datos.get("sesiones") or []:
            if isinstance(s, dict):
                s["desde"] = ""
        inv = datos.get("inventario")
        if isinstance(inv, dict):
            inv["contenedores"] = []              # los nombres de contenedores (pueden ser de clientes) tampoco viven en la caché más de 48 h
        datos.pop("swarm_servicios", None)
    return entrada


def _guardar(servidores, cache, nuevos, ahora):
    with _bloqueo(rutas.SERVIDORES):
        salida = {}
        for s in servidores:                   # solo los que siguen en config, en su orden
            n = s["nombre"]
            previo = dict(cache.get(n) or {})
            r = nuevos.get(n)
            if r is not None:
                previo["intento_en"] = ahora
                previo["error"] = r.get("error", "")
                previo["estado"] = r["estado"]
                previo["estado_vivo"] = r.get("estado_vivo", r["estado"])
                previo["host"] = r.get("host") or previo.get("host")
                if not r.get("error"):
                    previo.update(r)
                    previo["ok_en"] = ahora
            salida[n] = podar_ips(previo, ahora)
        datos = {"generado_en": ahora, "servidores": salida}
        _escribe_json(rutas.SERVIDORES, datos)
    return datos


def estado_visible(entrada, ahora=None, normal=INTERVALO_NORMAL):
    """Estado para pintar: gris si el ultimo dato bueno es de hace mas de 3 ciclos."""
    ahora = ahora or time.time()
    if not entrada:
        return "gris"
    if entrada.get("error") and not entrada.get("ok_en"):
        return "rojo"
    if ahora - (entrada.get("ok_en") or 0) > 3 * normal:
        return "gris" if not entrada.get("error") else "rojo"
    return entrada.get("estado") or "gris"


def peor_estado(cache=None, ahora=None):
    """El peor estado de todos los servidores (para el punto del icono de la barra)."""
    cache = cache if cache is not None else leer_cache()
    normal, _ = intervalos()
    return peor(*[estado_visible(e, ahora, normal) for e in (cache.get("servidores") or {}).values()])


if __name__ == "__main__":
    import json, sys
    r = ciclo(forzar="--forzar" in sys.argv)
    print(json.dumps(r, indent=1, ensure_ascii=False)[:4000] if r else "ciclo ya en marcha")
    os._exit(0)
