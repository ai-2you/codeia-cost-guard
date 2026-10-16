"""Lectura periódica del panel (Dokploy) de los servidores que lo tienen configurado, y lo que de ella queda en la caché y en la base.

· Corre en su propio hilo (como el sondeo de webs): un panel lento o caído nunca toca el ciclo SSH ni fabrica un «sin conexión». Sus fallos van a `entrada["panel"]["error"]`
  con un código cerrado, jamás a `entrada["error"]`.
· Sin clave guardada no se hace ninguna petición. La clave se lee del Llavero en cada pasada y no se conserva.
· Una lectura buena actualiza la caché (árbol de servicios y dominios, con tope) y la tabla `copias`. Una lectura mala deja lo anterior donde estaba y apunta el motivo. Si el
  alcance de la clave se encoge de golpe (un miembro al que le quitan servicios recibe una lista vacía válida), la lectura no se aplica: se avisa como «alcance» y se conserva lo
  anterior, para que «no hay nada» no parezca una buena noticia.
· Cadencia fija de 5 minutos (no sigue el modo incidencia ni el botón de refrescar); una clave rechazada espera 30 minutos."""
import threading
import time
import urllib.parse

from . import claves, panel_dokploy, rutas
from .config_y_estado import _bloqueo, _escribe_json, cfg, log
from .servidores import servidores_validos
from . import servidores_ciclo

CADA_S = 300
ESPERA_RECHAZO_S = 1800
MAX_SERVICIOS_CACHE = 300
MIN_PREVIOS_PARA_ALCANCE = 4
PANELES = ("dokploy",)
ESPERA_PRUEBA_S, MAX_PRUEBAS_HORA = 30, 10
_PRUEBA = threading.Lock()
_PRUEBAS = []
SIN_ESPERA_LARGA = frozenset({"red", "tls", "timeout", "http_5xx", "http_otro", "plazo", "no_json", "demasiado_grande", "contrato", "redireccion"})
_EN_MARCHA = threading.Lock()


def cuenta(s):
    """Cuenta del Llavero de la clave del panel de un servidor, o ''. El nombre del host va en minúsculas y sin el puerto 443, para que dos grafías de la misma URL
    no sean dos cuentas distintas (la clave «perdida» sin explicación)."""
    if not isinstance(s, dict) or s.get("panel") not in PANELES:
        return ""
    try:
        u = urllib.parse.urlsplit(str(s.get("panel_url") or ""))
        host, puerto = (u.hostname or "").lower(), u.port
    except ValueError:
        return ""
    if not host:
        return ""
    destino = host if puerto in (None, 443) else f"{host}:{puerto}"
    c = f"{s['panel']}:{destino}"
    return c if claves.cuenta_valida(c) else ""


def _resumen_de(previo, base, r, ahora, historial, s):
    if not r["ok"]:
        espera = ESPERA_RECHAZO_S if r["error"] in ("clave_rechazada", "clave_invalida", "url_invalida", "version_antigua", "sin_clave") else CADA_S
        return {**previo, **base, "error": r["error"], "proximo_en": ahora + espera}
    n, previos = len(r["servicios"]), previo.get("n_servicios") or 0
    if previos >= MIN_PREVIOS_PARA_ALCANCE and n < previos * 0.5 or (previos and n == 0):
        return {**previo, **base, "error": "alcance", "proximo_en": ahora + CADA_S}
    copias = historial.registrar_panel(s, r, ahora) if historial is not None else None
    return {**base, "ok_en": ahora, "error": "", "proximo_en": ahora + CADA_S, "version": r["version"], "version_no_probada": r["version_no_probada"], "n_servicios": n,
            "servicios": r["servicios"][:MAX_SERVICIOS_CACHE], "dominios": r["dominios"], "truncadas": r["copias_truncadas"],
            "copias_resumen": copias if copias is not None else previo.get("copias_resumen")}


def pasada(historial=None, ahora=None, leer=panel_dokploy.leer, clave_de=claves.leer_clave, conf=None):
    """Una vuelta sobre todos los paneles que toca leer. Devuelve True si cambió algo en la caché. No se solapa consigo misma."""
    if not _EN_MARCHA.acquire(blocking=False):
        return False
    try:
        ahora = time.time() if ahora is None else ahora
        conf = conf if conf is not None else cfg()
        entradas = servidores_ciclo.leer_cache().get("servidores") or {}
        nuevos = {}
        for s in servidores_validos(conf.get("servidores")):
            cta = cuenta(s)
            if not cta or not isinstance(entradas.get(s["nombre"]), dict):
                continue                                    # sin panel configurado, o aún sin la primera lectura SSH de ese servidor
            previo = entradas[s["nombre"]].get("panel") if isinstance(entradas[s["nombre"]].get("panel"), dict) else {}
            proximo = previo.get("proximo_en") or 0
            if ahora < proximo <= ahora + ESPERA_RECHAZO_S:
                continue
            clave = clave_de(cta)
            if not clave:
                continue                                    # sin clave no se hace ninguna petición
            try:
                r = leer(s["panel_url"], clave, ahora)
            finally:
                clave = None
            base = {"intento_en": ahora, "empezo_en": previo.get("empezo_en") or ahora}
            nuevos[s["nombre"]] = _resumen_de(previo, base, r, ahora, historial, s)
        if not nuevos:
            return False
        with _bloqueo(rutas.SERVIDORES):
            cache = servidores_ciclo.leer_cache()
            vivas = cache.get("servidores") if isinstance(cache.get("servidores"), dict) else {}
            for nombre, p in nuevos.items():
                if isinstance(vivas.get(nombre), dict):
                    vivas[nombre]["panel"] = p
            _escribe_json(rutas.SERVIDORES, cache)
        return True
    except Exception as e:
        log(f"panel: {type(e).__name__}")
        return False
    finally:
        _EN_MARCHA.release()


def probar(cuerpo, conf=None, leer=panel_dokploy.leer, clave_de=claves.leer_clave, reloj=time.monotonic):
    """POST /servidores/panel/probar {servidor: id} → (código, dict). Hace las cinco consultas de lectura con la clave guardada y dice la versión y CUÁNTAS cosas ve, sin enseñar ninguna.
    La URL sale de la configuración, nunca del cuerpo. Una a la vez, 1 cada 30 s y 10 por hora para toda la aplicación (el intento cuenta aunque falle)."""
    import re
    if not isinstance(cuerpo, dict) or set(cuerpo) != {"servidor"} or not (isinstance(cuerpo["servidor"], str) and re.fullmatch(r"[0-9a-f]{32}", cuerpo["servidor"])):
        return 400, {"ok": False, "error": "entrada no válida"}
    conf = conf if conf is not None else cfg()
    s = next((x for x in servidores_validos(conf.get("servidores")) if x.get("id") == cuerpo["servidor"]), None)
    if s is None or not cuenta(s):
        return 404, {"ok": False, "error": "servidor sin panel configurado"}
    if not _PRUEBA.acquire(blocking=False):
        return 429, {"ok": False, "espera_s": ESPERA_PRUEBA_S}
    try:
        ahora = reloj()
        _PRUEBAS[:] = [t for t in _PRUEBAS if ahora - t < 3600]
        if _PRUEBAS and ahora - _PRUEBAS[-1] < ESPERA_PRUEBA_S:
            return 429, {"ok": False, "espera_s": int(ESPERA_PRUEBA_S - (ahora - _PRUEBAS[-1])) + 1}
        if len(_PRUEBAS) >= MAX_PRUEBAS_HORA:
            return 429, {"ok": False, "espera_s": int(3600 - (ahora - _PRUEBAS[0])) + 1}
        _PRUEBAS.append(ahora)
        clave = clave_de(cuenta(s))
        if not clave:
            return 200, {"ok": False, "error": "sin_clave"}
        try:
            r = leer(s["panel_url"], clave, time.time())
        finally:
            clave = None
        if not r["ok"]:
            return 200, {"ok": False, "error": r["error"]}
        return 200, {"ok": True, "version": r["version"], "version_no_probada": r["version_no_probada"], "servicios": len(r["servicios"]), "dominios": len(r["dominios"]),
                     "copias": len(r["copias"]), "copias_truncadas": r["copias_truncadas"]}
    finally:
        _PRUEBA.release()
