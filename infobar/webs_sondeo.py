"""Sondeo de las webs de los servidores, aparte del ciclo SSH.

· Las webs ya no se miden dentro de la lectura de cada servidor (donde, en serie, 100 webs rompían el tope del ciclo y tiraban también la lectura SSH):
  un hilo propio las sondea con un pool pequeño y un presupuesto por pasada, y guarda el resultado en `logs/webs.json`; el ciclo SSH solo lo lee.
· Calendario por web: cada 5 min; cada 60 s mientras falla (sin pasar de `MAX_ACELERADAS` a la vez). Una web con error de cliente (4xx) no acelera nada.
· Un Mac sin red (recién despertado, wifi caído) no fabrica caídas: si en una pasada fallan TODAS las webs sondeadas (≥ 3) y también los canarios TCP públicos,
  la pasada se descarta entera y no cambia ningún estado.
· Todo lo que se sondea ha pasado `url_valida`/`medir_web` (https o http a un host público, IP fijada tras resolver, sin redirecciones)."""
import concurrent.futures
import os
import socket
import threading
import time

from . import rutas
from .config_y_estado import _bloqueo, _escribe_json, _lee_json, log
from .servidores import url_valida
from .servidores_externo import medir_web

CADA_S = 300
CADA_FALLO_S = 60
MAX_ACELERADAS = 20
MAX_WEBS_POR_SERVIDOR = 100
HILOS = 4
PRESUPUESTO_S = 60
MIN_PARA_CANARIO = 3
CANARIOS = (("1.1.1.1", 443), ("8.8.8.8", 443), ("9.9.9.9", 443))
_EN_MARCHA = threading.Lock()


def ruta():
    return os.path.join(os.path.dirname(rutas.LOG), "webs.json")


def leer():
    d = _lee_json(ruta())
    return d if isinstance(d.get("webs"), dict) else {"webs": {}}


def urls_de(servidores):
    """[(nombre del servidor, url)] de lo configurado: válidas, sin repetir y como mucho MAX_WEBS_POR_SERVIDOR por servidor."""
    vistas, out = set(), []
    for s in servidores or []:
        n = 0
        for u in (s.get("webs") or []) if isinstance(s, dict) else []:
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
        debidas = [u for _, u in todas if (webs.get(u) or {}).get("proximo_en", 0) <= ahora]
        # las que fallan se miran primero y, de ellas, solo MAX_ACELERADAS (el resto espera a su turno normal)
        falladas = sorted((u for u in debidas if (webs.get(u) or {}).get("fallos")), key=lambda u: webs[u].get("intento_en", 0))      # la que lleva más sin mirarse, primero
        debidas = falladas[:MAX_ACELERADAS] + [u for u in debidas if u not in falladas]       # las falladas de más esperan al turno siguiente
        if not debidas and set(webs) == {u for _, u in todas}:
            return False, estado
        resultados = {}
        if debidas:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=HILOS)
            futuros = {pool.submit(medir, u): u for u in debidas}
            try:
                for f in concurrent.futures.as_completed(futuros, timeout=PRESUPUESTO_S):
                    try:
                        resultados[futuros[f]] = f.result()
                    except Exception as e:
                        resultados[futuros[f]] = {"codigo": None, "error": type(e).__name__}
            except concurrent.futures.TimeoutError:
                pass                                       # lo que no llegó a medirse conserva su turno para la próxima pasada
            pool.shutdown(wait=False, cancel_futures=True)
        if len(resultados) >= MIN_PARA_CANARIO and all(_falla(r) for r in resultados.values()) and red():
            estado["sin_red_en"] = ahora                   # el Mac no tiene red: nada de esto es información sobre las webs
            return False, estado
        for u, r in resultados.items():
            webs[u] = _entrada(r, webs.get(u) or {}, ahora)
        for u in [u for u in webs if u not in {x for _, x in todas}]:
            del webs[u]                                    # ya no están configuradas
        estado.pop("sin_red_en", None)
        estado["generado_en"] = ahora
        with _bloqueo(ruta()):
            _escribe_json(ruta(), estado)
        return True, estado
    finally:
        _EN_MARCHA.release()


def medidas(servidor, estado=None):
    """Lo último medido de las webs de un servidor, con la forma de siempre: [{url, ms, codigo, error, cert_dias}]. Una web sin medir aún no aparece."""
    webs = (estado if estado is not None else leer())["webs"]
    out = []
    for u in (servidor.get("webs") or [])[:MAX_WEBS_POR_SERVIDOR]:
        e = webs.get(u) if isinstance(u, str) else None
        if e:
            out.append({"url": u[:120], "ms": e.get("ms"), "codigo": e.get("codigo"), "error": e.get("error") or "", "cert_dias": e.get("cert_dias")})
    return out
