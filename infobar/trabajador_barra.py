"""Hilo trabajador de la barra: todo lo lento fuera del hilo principal de AppKit.

Escanea los logs de los agentes (cada minuto), recoge los servidores (el ciclo decide a cuales les
toca: 5 min, o 1 min en modo incidencia) y repinta los PNG del desplegable cuando algo cambia.
El resultado se entrega con `entregar(tipo, dato)`; barra.py lo pasa al hilo principal, que solo
cambia imagenes. Una excepcion se registra y el bucle sigue: el hilo no muere.
"""
import threading, time, traceback

from . import cuota_nan, desplegable_png, permisos, presupuestos, servidores_avisos, servidores_ciclo, servidores_panel, webs_sondeo
from . import servidores_registro, sombra
from .agregacion import ULTIMOS, informe
from .config_y_estado import cfg, leer_ui, log

CADA_AGENTES = 60           # s entre escaneos de logs (icono)
CADA_RENDER_AGENTES = 60    # s entre repintados de la pestana Agentes (al ritmo del escaneo)
DESPIERTA = 15              # s maximos dormido: el ciclo de servidores decide si toca


class Trabajador(threading.Thread):
    def __init__(self, entregar, pintar=True):
        """`pintar=False` es el modo sin interfaz (`costbar.py --demonio`): hace todo salvo renderizar el desplegable con Chrome."""
        super().__init__(name="infobar-trabajador", daemon=True)
        self.entregar = entregar
        self.pintar = pintar
        self._evento = threading.Event()
        self._cerrojo = threading.Lock()
        self._pedidos = set()
        self.conjunto = None
        self.historial = servidores_registro.Historial()      # único escritor de servidores.sqlite
        self.avisos_servidores = servidores_avisos.Vigilante()      # único escritor de avisos_servidores.json
        self._evento.set()                  # la primera vuelta, nada mas arrancar
        self._webs = threading.Thread(target=self._sondea_webs, name="infobar-webs", daemon=True)
        self._paneles = threading.Thread(target=self._lee_paneles, name="infobar-paneles", daemon=True)

    def _sondea_webs(self):
        """Hilo propio: las webs se sondean con su calendario (5 min; 60 s mientras fallan), sin esperar al ciclo SSH ni frenarlo."""
        while True:
            try:
                r = webs_sondeo.pasada(cfg().get("servidores") or [], registrar=self.historial.registrar_webs)
                if r and r[0] and servidores_ciclo.reaplicar_webs(r[1]):
                    self.pide("render")                      # el desplegable enseña las webs al momento, sin esperar al ciclo SSH
            except Exception:
                log("webs: " + traceback.format_exc()[-300:])
            time.sleep(DESPIERTA)

    def _lee_paneles(self):
        """Hilo propio: la API del panel de cada servidor se lee con su calendario (5 min) sin esperar al ciclo SSH ni frenarlo."""
        while True:
            try:
                if servidores_panel.pasada(self.historial):
                    self.pide("render")                      # el desplegable enseña lo nuevo (copias, despliegues) sin esperar al ciclo SSH
            except Exception:
                log("panel: fallo inesperado")
            time.sleep(DESPIERTA)

    def pide(self, *cosas):
        """'refrescar' (todo ya, servidores incluidos) o 'render' (solo repintar)."""
        with self._cerrojo:
            self._pedidos.update(cosas)
        self._evento.set()

    def run(self):
        self._webs.start()
        self._paneles.start()
        r, ultimo_scan, ultimo_render, gen_srv = None, 0.0, 0.0, None
        migrado, ultima_purga, ultimo_aviso = False, 0.0, 0.0
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
                    if ahora - ultima_purga >= 3600:
                        ultima_purga = ahora
                        try:
                            permisos.purgar_render()            # las imágenes con IPs no esperan al próximo arranque
                        except Exception:
                            log("purga de imágenes: " + traceback.format_exc()[-300:])
                    try:
                        if not migrado:
                            presupuestos.migrar()
                            migrado = True
                        presupuestos.vigilar()          # solo este hilo escribe avisos.json
                    except Exception:
                        log("presupuestos: " + traceback.format_exc()[-400:])
                    if str(cfg().get("almacen") or "sombra").lower() == "sombra":
                        sombra.registrar_si_toca(ULTIMOS.get("vistos"))
                    lim = r.get("limites") or {}
                    self.entregar("agentes", {"cinco_h": r["cinco_h"]["tok"], "hoy": r["hoy"]["tok"],
                                              "turnos": r["hoy"]["turnos"], "pct_5h": r.get("pct_5h", 0),
                                              "ritmo": (r.get("ritmo") or {}).get("tok", 0),      # la bandeja de Windows lo enseña en su menú
                                              "limite_5h": lim.get("limite_5h_tokens") or 0, "limite_hora": lim.get("limite_hora_tokens") or 0})
                if self.conjunto is None:
                    # primera imagen cuanto antes, con la ultima cache de servidores; la recogida
                    # (que puede tardar segundos por SSH) repinta despues
                    previa = servidores_ciclo.leer_cache()
                    self._pinta(r, previa)
                    gen_srv, ultimo_render = previa.get("generado_en"), ahora
                if "plegar" in pedidos and self.conjunto is not None:
                    previa = servidores_ciclo.leer_cache()
                    self._pinta(None, previa, solo_servidor=leer_ui().get("servidor_activo"))       # una sección se abrió o se cerró: primero el que se está viendo…
                    if len(cfg().get("servidores") or []) > 1:
                        self._pinta(None, previa)                                                   # …y luego los demás, para que no enseñen el pliegue viejo
                self.historial.preparar()
                cache = servidores_ciclo.ciclo(forzar=forzar, historial=self.historial) or servidores_ciclo.leer_cache()
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
            if time.time() - ultimo_aviso >= 60:        # aparte y tras pintar: un fallo al pintar no deja sin avisos ni la notificación (osascript, hasta 5 s) retrasa el repintado
                ultimo_aviso = time.time()
                self.avisos_servidores.vigilar()
            if "refrescar" in pedidos:
                self.entregar("refresco_hecho", None)       # quita la ruedita del boton

    def _pinta(self, r, cache, solo_servidor=None):
        if not self.pintar:
            return False
        nuevo = desplegable_png.renderizar(r, cache, cfg().get("servidores") or [], solo_servidor=solo_servidor, previo=self.conjunto)
        if nuevo:
            self.conjunto = nuevo
            self.entregar("conjunto", nuevo)
        return bool(nuevo)
