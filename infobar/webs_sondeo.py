"""Sondeo de las webs de los servidores, aparte del ciclo SSH.

· Las webs ya no se miden dentro de la lectura de cada servidor (donde, en serie, 100 webs rompían el tope del ciclo y tiraban también la lectura SSH):
  un hilo propio las sondea con un pool pequeño y un presupuesto por pasada, y guarda el resultado en `logs/webs.json`; el ciclo SSH solo lo lee.
· Calendario por web: cada 5 min; cada 60 s mientras falla (sin pasar de `MAX_ACELERADAS` a la vez). Una web con error de cliente (4xx) no acelera nada.
· Un Mac sin red (recién despertado, wifi caído) no fabrica caídas: si en una pasada fallan TODAS las webs sondeadas (≥ 3) y también los canarios TCP públicos,
  la pasada se descarta entera y no cambia ningún estado.
· Todo lo que se sondea ha pasado `url_valida`/`medir_web` (https o http a un host público, IP fijada tras resolver, sin redirecciones)."""
import concurrent.futures
import math
import os
import socket
import threading
import time

from . import rutas
from .config_y_estado import _bloqueo, _escribe_json, _lee_json, log
from .servidores import servidores_validos, url_valida
from .servidores_externo import medir_web

CADA_S = 300
CADA_FALLO_S = 60
MAX_ACELERADAS = 20
MAX_WEBS_POR_SERVIDOR = 100
HILOS = 4
PRESUPUESTO_S = 60
MIN_PARA_CANARIO = 3
CANARIOS = (("1.1.1.1", 443), ("8.8.8.8", 443), ("9.9.9.9", 443))
MAX_DESCARTES_SEGUIDOS = 3          # tras tres pasadas «sin red» seguidas se deja de dudar: si es una caída real de todas las webs, se anota
ANTIGUA_S = 3 * CADA_S              # una medida más vieja que esto ya no dice cómo está la web ahora: no se enseña
_EN_MARCHA = threading.Lock()
_CERROJO_VUELO = threading.Lock()
_EN_VUELO = set()                   # URLs con un sondeo todavía en marcha (puede colgarse en un DNS lento): no se vuelven a lanzar
_POOL = None


def _pool():
    global _POOL
    if _POOL is None:
        _POOL = concurrent.futures.ThreadPoolExecutor(max_workers=HILOS, thread_name_prefix="infobar-web")
    return _POOL


def _lanza(medir, u):
    """Envía un sondeo al pool permanente y lo apunta como «en vuelo» hasta que termine o se cancele."""
    with _CERROJO_VUELO:
        _EN_VUELO.add(u)
    f = _pool().submit(medir, u)

    def terminado(_f, u=u):
        with _CERROJO_VUELO:
            _EN_VUELO.discard(u)
    f.add_done_callback(terminado)
    return f


def ruta():
    return os.path.join(os.path.dirname(rutas.LOG), "webs.json")


def _num(v, defecto=0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else defecto


def _entrada_sana(e):
    """La entrada tal cual se guardó, con cada campo del tipo que toca; None si no es una entrada. El fichero puede estar estropeado o tocado a mano."""
    if not isinstance(e, dict):
        return None
    codigo = e.get("codigo")
    ms = e.get("ms")
    cert = e.get("cert_dias")
    return {"ms": int(ms) if isinstance(ms, (int, float)) and not isinstance(ms, bool) and math.isfinite(ms) and 0 <= ms < 10 ** 8 else None,
            "codigo": int(codigo) if isinstance(codigo, int) and not isinstance(codigo, bool) and 100 <= codigo <= 599 else None,
            "error": str(e.get("error") or "")[:80], "cert_dias": cert if isinstance(cert, int) and not isinstance(cert, bool) and abs(cert) <= 36500 else None,
            "intento_en": _num(e.get("intento_en")), "ok_en": _num(e.get("ok_en"), None), "fallos": max(0, min(int(_num(e.get("fallos"))), 10 ** 6)),
            "proximo_en": _num(e.get("proximo_en"))}


def leer():
    d = _lee_json(ruta())
    webs = d.get("webs") if isinstance(d.get("webs"), dict) else {}
    sanas = {u: e for u, e in ((u, _entrada_sana(e)) for u, e in webs.items() if isinstance(u, str) and len(u) <= 300 and u.startswith(("http://", "https://"))) if e is not None}
    return {"webs": sanas, "descartes_seguidos": int(_num(d.get("descartes_seguidos"))), "sin_red_en": d.get("sin_red_en") if isinstance(d.get("sin_red_en"), (int, float)) else None}


def urls_de(servidores):
    """[(nombre del servidor, url)] de lo configurado: válidas, sin repetir y como mucho MAX_WEBS_POR_SERVIDOR por servidor."""
    vistas, out = set(), []
    for s in servidores_validos(servidores):
        n = 0
        for u in s.get("webs") or []:
            if n >= MAX_WEBS_POR_SERVIDOR:
                break
            if isinstance(u, str) and u not in vistas and not url_valida(u):
                vistas.add(u)
                out.append((s.get("nombre"), u))
                n += 1
    return out


def sin_red(canarios=CANARIOS, conectar=socket.create_connection, tope=2.0):
    """True si no se alcanza NINGÚN canario (puerto 443 de tres resolvers públicos conocidos): el Mac no tiene salida a Internet."""
    for host, puerto in canarios:
        try:
            conectar((host, puerto), tope).close()
            return False
        except OSError:
            continue
    return True


def _falla(r):
    """Una web falla (y se vuelve a mirar pronto) si no responde o responde 5xx; un 4xx es «responde»."""
    return r.get("codigo") is None or (r.get("codigo") or 0) >= 500


def _sin_respuesta(r):
    """Fallo de RED: no llegó ninguna respuesta HTTP. Un 5xx demuestra que hay red (el servidor contestó) y nunca cuenta para el canario."""
    return r.get("codigo") is None


def _entrada(r, previa, ahora):
    fallos = (previa.get("fallos", 0) + 1) if _falla(r) else 0
    return {"ms": r.get("ms"), "codigo": r.get("codigo"), "error": str(r.get("error") or "")[:80], "cert_dias": r.get("cert_dias"),
            "intento_en": ahora, "ok_en": previa.get("ok_en") if _falla(r) else ahora, "fallos": fallos,
            "proximo_en": ahora + (CADA_FALLO_S if fallos else CADA_S)}


def pasada(servidores, ahora=None, medir=medir_web, red=sin_red):
    """Sondea las webs a las que les toca. Devuelve (cambiaron, estado): `cambiaron` = hubo resultados nuevos que guardar. None si ya había una pasada."""
    if not _EN_MARCHA.acquire(blocking=False):
        return None
    try:
        ahora = ahora if ahora is not None else time.time()
        todas = urls_de(servidores)
        estado = leer()
        webs = estado["webs"]
        with _CERROJO_VUELO:
            en_vuelo = set(_EN_VUELO)

        def toca(u):
            # un `proximo_en` más lejos que un intervalo es un reloj que estuvo adelantado: se toma como «ya toca»
            p = (webs.get(u) or {}).get("proximo_en", 0)
            return p <= ahora or p > ahora + CADA_S
        debidas = [u for _, u in todas if toca(u) and u not in en_vuelo]
        # las que fallan se miran primero (la que lleva más sin mirarse, primero) y, de ellas, solo MAX_ACELERADAS; el resto espera al turno siguiente
        falladas = sorted((u for u in debidas if (webs.get(u) or {}).get("fallos")), key=lambda u: webs[u].get("intento_en", 0))
        debidas = falladas[:MAX_ACELERADAS] + [u for u in debidas if u not in falladas]
        if not debidas and set(webs) == {u for _, u in todas}:
            return False, estado
        resultados = {}
        if debidas:
            futuros = {_lanza(medir, u): u for u in debidas}
            try:
                for f in concurrent.futures.as_completed(futuros, timeout=PRESUPUESTO_S):
                    try:
                        resultados[futuros[f]] = f.result()
                    except Exception as e:
                        resultados[futuros[f]] = {"codigo": None, "error": type(e).__name__}
            except concurrent.futures.TimeoutError:
                for f in futuros:
                    f.cancel()                             # lo que no llegó a empezar se cancela; lo que está en marcha sigue «en vuelo» y no se relanza
        if (len(resultados) >= MIN_PARA_CANARIO and all(_sin_respuesta(r) for r in resultados.values())
                and estado["descartes_seguidos"] < MAX_DESCARTES_SEGUIDOS and red()):
            # el Mac no tiene red: nada de esto es información sobre las webs. Se apunta (y se cuenta) para que, si el canario se equivocara, no se
            # esconda una caída real más de unas pasadas
            estado["sin_red_en"], estado["descartes_seguidos"] = ahora, estado["descartes_seguidos"] + 1
            with _bloqueo(ruta()):
                _escribe_json(ruta(), estado)
            return False, estado
        for u, r in resultados.items():
            webs[u] = _entrada(r, webs.get(u) or {}, ahora)
        for u in [u for u in webs if u not in {x for _, x in todas}]:
            del webs[u]                                    # ya no están configuradas
        if resultados:
            estado["sin_red_en"], estado["descartes_seguidos"] = None, 0
        estado["generado_en"] = ahora
        with _bloqueo(ruta()):
            _escribe_json(ruta(), estado)
        return True, estado
    finally:
        _EN_MARCHA.release()


def medidas(servidor, estado=None, ahora=None):
    """Lo último medido de las webs de un servidor, con la forma de siempre: [{url, ms, codigo, error, cert_dias}]. Una web sin medir aún, o cuya medida es
    demasiado vieja para decir cómo está ahora (pasadas descartadas por falta de red, sondeo atascado), no aparece: mejor «sin dato» que un verde falso."""
    webs = (estado if estado is not None else leer())["webs"]
    ahora = ahora if ahora is not None else time.time()
    out = []
    lista = servidor.get("webs") if isinstance(servidor, dict) and isinstance(servidor.get("webs"), list) else []
    for u in lista[:MAX_WEBS_POR_SERVIDOR]:
        e = webs.get(u) if isinstance(u, str) else None
        if e and ahora - e.get("intento_en", 0) <= ANTIGUA_S:
            out.append({"url": u[:120], "ms": e.get("ms"), "codigo": e.get("codigo"), "error": e.get("error") or "", "cert_dias": e.get("cert_dias")})
    return out
