"""Hilo trabajador de la barra: todo lo lento fuera del hilo principal de AppKit.

Escanea los logs de los agentes (cada minuto), recoge los servidores (el ciclo decide a cuales les
toca: 5 min, o 1 min en modo incidencia) y repinta los PNG del desplegable cuando algo cambia.
El resultado se entrega con `entregar(tipo, dato)`; barra.py lo pasa al hilo principal, que solo
cambia imagenes. Una excepcion se registra y el bucle sigue: el hilo no muere.
"""
import threading, time, traceback

from . import cuota_nan, desplegable_png, servidores_ciclo
from .agregacion import informe
from .config_y_estado import cfg, log

CADA_AGENTES = 60           # s entre escaneos de logs (icono)
CADA_RENDER_AGENTES = 60    # s entre repintados de la pestana Agentes (al ritmo del escaneo)
DESPIERTA = 15              # s maximos dormido: el ciclo de servidores decide si toca


class Trabajador(threading.Thread):
    def __init__(self, entregar):
        super().__init__(name="infobar-trabajador", daemon=True)
        self.entregar = entregar
        self._evento = threading.Event()
        self._cerrojo = threading.Lock()
        self._pedidos = set()
        self.conjunto = None
        self._evento.set()                  # la primera vuelta, nada mas arrancar

    def pide(self, *cosas):
        """'refrescar' (todo ya, servidores incluidos) o 'render' (solo repintar)."""
        with self._cerrojo:
            self._pedidos.update(cosas)
        self._evento.set()

    def run(self):
        r, ultimo_scan, ultimo_render, gen_srv = None, 0.0, 0.0, None
        while True:
            self._evento.wait(timeout=DESPIERTA)
            self._evento.clear()
            with self._cerrojo:
                pedidos, self._pedidos = self._pedidos, set()
            try:
                ahora = time.time()
                forzar = "refrescar" in pedidos
                if forzar:
                    cuota_nan.invalidar()
                if r is None or forzar or ahora - ultimo_scan >= CADA_AGENTES:
                    r = informe()
                    ultimo_scan = ahora
                    self.entregar("agentes", {"cinco_h": r["cinco_h"]["tok"], "hoy": r["hoy"]["tok"],
                                              "turnos": r["hoy"]["turnos"], "pct_5h": r.get("pct_5h", 0)})
                if self.conjunto is None:
                    # primera imagen cuanto antes, con la ultima cache de servidores; la recogida
                    # (que puede tardar segundos por SSH) repinta despues
                    previa = servidores_ciclo.leer_cache()
                    self._pinta(r, previa)
                    gen_srv, ultimo_render = previa.get("generado_en"), ahora
                cache = servidores_ciclo.ciclo(forzar=forzar) or servidores_ciclo.leer_cache()
                self.entregar("punto", servidores_ciclo.peor_estado(cache) if cache.get("servidores") else None)
                pintar_agentes = (self.conjunto is None or forzar or "render" in pedidos
                                  or ahora - ultimo_render >= CADA_RENDER_AGENTES)
                if pintar_agentes or cache.get("generado_en") != gen_srv:
                    if self._pinta(r if pintar_agentes else None, cache):
                        gen_srv = cache.get("generado_en")
                        if pintar_agentes:
                            ultimo_render = ahora
            except Exception:
                log("trabajador: " + traceback.format_exc()[-600:])
            if "refrescar" in pedidos:
                self.entregar("refresco_hecho", None)       # quita la ruedita del boton

    def _pinta(self, r, cache):
        nuevo = desplegable_png.renderizar(r, cache, cfg().get("servidores") or [], previo=self.conjunto)
        if nuevo:
            self.conjunto = nuevo
            self.entregar("conjunto", nuevo)
        return bool(nuevo)
