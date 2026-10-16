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
    def __init__(self, entregar, pintar=True, tema=None):
        """`pintar=False` es el modo sin interfaz (`costbar.py --demonio`): hace todo salvo renderizar el desplegable con Chrome.
        `tema()`: 'claro' u 'oscuro'. Con él solo se pinta el tema que se ve (la mitad de Chrome por tanda); sin él, los dos."""
        super().__init__(name="infobar-trabajador", daemon=True)
        self.entregar = entregar
        self.pintar = pintar
        self._evento = threading.Event()
        self._cerrojo = threading.Lock()
        self._pedidos = set()
        self.conjunto = None
        self.tema = tema
        self._tema_pintado = None
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
        """'refrescar' (todo ya, servidores incluidos), 'render' (repintar los servidores), 'agentes' (repintar Agentes),
        'plegar' (repintar ya el servidor que se ve; corta la tanda en curso) o 'tema' (mirar si cambió el tema)."""
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
                temas = self._temas()                           # el mismo en toda la vuelta, aunque cambie a mitad
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
                if "plegar" in pedidos and self.conjunto is not None:
                    previa = servidores_ciclo.leer_cache()
                    self._pinta(None, previa, temas, solo_servidor=leer_ui().get("servidor_activo"))       # una sección se abrió o se cerró: primero el que se está viendo…
                    if len(cfg().get("servidores") or []) > 1:
                        self._pinta(None, previa, temas, cortable=True)                                   # …y luego los demás, para que no enseñen el pliegue viejo
                    pedidos.discard("render")              # ya están todos repintados (si la tanda se cortó, se ha vuelto a pedir)
                if self.conjunto is None or temas != self._tema_pintado:
                    # primera imagen (o la del tema nuevo) cuanto antes, con la ultima cache de servidores; la recogida
                    # (que puede tardar segundos por SSH) repinta despues
                    previa = servidores_ciclo.leer_cache()
                    primera = self.conjunto is None
                    if self._pinta(r, previa, temas, cortable=True) or primera:
                        gen_srv, ultimo_render, self._tema_pintado = previa.get("generado_en"), ahora, temas
                        pedidos -= {"agentes", "render"}   # recién pintado todo: no hace falta otra tanda en esta vuelta
                if self._clic_esperando(pedidos):
                    continue                               # un clic cortó una tanda: va antes que el ciclo SSH (lo pendiente se conserva)
                self.historial.preparar()
                cache = servidores_ciclo.ciclo(forzar=forzar, historial=self.historial) or servidores_ciclo.leer_cache()
                self.entregar("punto", servidores_ciclo.peor_estado(cache) if cache.get("servidores") else None)
                # un cambio en las webs o en el panel de un servidor ('render') no repinta Agentes: no sale ahí
                pintar_agentes = (self.conjunto is None or forzar or "agentes" in pedidos
                                  or ahora - ultimo_render >= CADA_RENDER_AGENTES)
                if pintar_agentes or "render" in pedidos or cache.get("generado_en") != gen_srv:
                    if self._pinta(r if pintar_agentes else None, cache, temas, cortable=True):
                        gen_srv = cache.get("generado_en")
                        if pintar_agentes:
                            ultimo_render, self._tema_pintado = ahora, temas
            except Exception:
                log("trabajador: " + traceback.format_exc()[-600:])
            if time.time() - ultimo_aviso >= 60:        # aparte y tras pintar: un fallo al pintar no deja sin avisos ni la notificación (osascript, hasta 5 s) retrasa el repintado
                ultimo_aviso = time.time()
                self.avisos_servidores.vigilar()
            if "refrescar" in pedidos:
                self.entregar("refresco_hecho", None)       # quita la ruedita del boton

    def _clic_esperando(self, pedidos):
        """True si hay un 'plegar' pendiente: devuelve los demás pedidos de esta vuelta a la cola y despierta el bucle."""
        with self._cerrojo:
            if "plegar" not in self._pedidos:
                return False
            self._pedidos |= pedidos - {"plegar"}
        self._evento.set()
        return True

    def _temas(self):
        try:
            return (self.tema(),) if self.tema else desplegable_png.TEMAS
        except Exception:
            return desplegable_png.TEMAS

    def _pinta(self, r, cache, temas=None, solo_servidor=None, cortable=False):
        """True si la tanda se pintó entera. Cortada por un clic ('plegar'), se vuelve a pedir y el clic va primero."""
        if not self.pintar:
            return False
        cortada = []

        def parar():
            with self._cerrojo:
                if "plegar" in self._pedidos:
                    cortada.append(1)
                    return True
            return False

        nuevo = desplegable_png.renderizar(r, cache, cfg().get("servidores") or [], solo_servidor=solo_servidor, previo=self.conjunto,
                                           temas=temas or self._temas(), parar=parar if cortable else None)
        if cortada:
            self.pide("render", *(("agentes",) if r is not None else ()))
        if nuevo and nuevo is not self.conjunto:          # Chrome no pintó nada: devuelve el previo y no hay nada que entregar
            self.conjunto = nuevo
            self.entregar("conjunto", nuevo)
        return bool(nuevo) and not cortada
