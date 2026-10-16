#!/usr/bin/env python3
"""infobar: icono en la barra de menus y desplegable "glass" con dos pestanas (Agentes y Servidores).

El desplegable es un PNG (HTML pintado por Chrome, con tarjetas translucidas) sobre el material
nativo de macOS (NSVisualEffectView): el escritorio se ve desenfocado detras, como en los
desplegables del sistema. WKWebView no se usa: dentro del bucle de la app petaba.

Todo lo lento (escanear logs, SSH, Chrome) va en un hilo trabajador (infobar/trabajador_barra.py);
el hilo principal solo cambia imagenes, asi que el icono y los clics nunca se congelan. Los clics
se reparten por zonas fijas (infobar/geometria_desplegable.py); el selector de servidor y el menu
"···" son menus nativos.

Uso:
    ./venv/bin/python barra.py            # icono vivo, clic = desplegable
    ./venv/bin/python barra.py --abre     # ademas abre el desplegable al arrancar (pruebas)
"""
import os
import shlex
import subprocess
import sys

import objc
from AppKit import (
    NSAppearance, NSAppearanceNameAqua, NSAppearanceNameDarkAqua, NSApplication, NSApplicationActivationPolicyAccessory, NSAttributedString, NSColor,
    NSEvent, NSFont, NSFontAttributeName, NSForegroundColorAttributeName, NSImage, NSImageLeft,
    NSImageView, NSMakePoint, NSMakeRect, NSMakeSize, NSMenu, NSMenuItem, NSPopover, NSScreen,
    NSPasteboard, NSPasteboardTypeString, NSProgressIndicator, NSScrollView, NSStatusBar, NSVariableStatusItemLength,
    NSViewController, NSVisualEffectView, NSWorkspace,
)
from Foundation import NSData, NSObject, NSTimer, NSURL

import costbar
from infobar import desplegable_png, icono_barra, servidores_ciclo
from infobar import geometria_desplegable as G
from infobar.desplegable_agentes import BOTONES as BOTONES_AGENTES
from infobar.desplegable_servidores import BOTONES as BOTONES_SERVIDORES
from infobar.servidores import url_valida, errores_servidores
from infobar.trabajador_barra import Trabajador

AQUI = os.path.dirname(os.path.abspath(__file__))
MATERIAL_POPOVER = 6        # NSVisualEffectMaterialPopover


def log(txt):
    costbar.log(f"barra: {txt}")


def tema_preferido():
    """'claro' / 'oscuro' / 'sistema' de logs/ui_estado.json (migrado de config.json)."""
    try:
        return (costbar.leer_ui().get("tema") or "sistema").strip().lower()
    except Exception:
        return "sistema"


def tema_del_sistema():
    """Apariencia actual de macOS, sin lanzar procesos (se llama desde el hilo principal)."""
    try:
        from Foundation import NSUserDefaults
        estilo = NSUserDefaults.standardUserDefaults().stringForKey_("AppleInterfaceStyle")
        return "oscuro" if str(estilo or "").lower() == "dark" else "claro"
    except Exception:
        return "claro"


def tema_activo():
    pref = tema_preferido()
    return tema_del_sistema() if pref == "sistema" else pref


def tema_para_trabajador():
    """tema_activo() desde el hilo trabajador, que no tiene bucle de Cocoa: con su propio pool para no acumular objetos."""
    with objc.autorelease_pool():
        return tema_activo()


def barra_oscura(boton):
    """Apariencia REAL de la barra de menús (depende del fondo de pantalla, no del tema elegido para el desplegable)."""
    try:
        nombre = boton.effectiveAppearance().bestMatchFromAppearancesWithNames_([NSAppearanceNameAqua, NSAppearanceNameDarkAqua])
        return str(nombre) == str(NSAppearanceNameDarkAqua)
    except Exception:
        return tema_del_sistema() == "oscuro"


def imagen_icono(texto, fraccion, alerta, punto, oscuro):
    """Pastilla del icono (infobar/icono_barra.py). Con colores propios: no es plantilla, por el punto de los servidores."""
    datos, ancho = icono_barra.dibuja(texto, fraccion, alerta, punto, oscuro)
    img = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(datos, len(datos)))
    img.setSize_((ancho, icono_barra.ALTO))
    img.setTemplate_(False)
    return img


def nsimagen(ruta, ancho, alto):
    datos = open(ruta, "rb").read()
    img = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(datos, len(datos)))
    img.setSize_((ancho, alto))
    return img


class VistaDesplegable(NSImageView):
    """La imagen recibe los clics y los reparte por zonas (geometria_desplegable)."""

    def acceptsFirstMouse_(self, _e):
        return True

    def hitTest_(self, _punto):
        return self             # si no, NSImageView no editable deja pasar el clic y el popover se cierra

    def mouseDown_(self, evento):
        try:
            p = self.convertPoint_fromView_(evento.locationInWindow(), None)
            dueno = getattr(self, "dueno", None)
            if dueno is not None:
                dueno.clicEn_(p)
        except Exception as e:
            log(f"ERROR clic {type(e).__name__}: {e}")


class Barra(NSObject):
    def init(self):
        self = objc.super(Barra, self).init()
        if self is None:
            return None
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        boton = self.item.button()
        boton.setTarget_(self)
        boton.setAction_("pulsa:")
        boton.setImagePosition_(NSImageLeft)
        boton.setTitle_("…")
        self.pop, self.conjunto, self.escala, self.alto_mostrado = None, None, 1.0, 0
        self._clave_vista, self._alto_prev = None, 0         # qué se veía y cuánto medía, para conservar el scroll al repintar lo mismo
        self.punto = None
        from infobar import permisos
        permisos.excluir_datos_sensibles()                  # base y logs fuera de Time Machine
        self.trabajador = Trabajador(self.entregar, tema=tema_para_trabajador)
        self.trabajador.start()
        self.ingesta = None
        if str(costbar.cfg().get("almacen") or "sombra").lower() != "desactivado":
            from infobar.ingestor import HiloIngesta        # solo la barra (y costbar.py --ingerir) escribe en la base
            self.ingesta = HiloIngesta()
            self.ingesta.start()
        self.copias = None
        if self.ingesta is not None:
            from infobar.hilo_copias import HiloCopias      # copias cifradas a destinatario: solo claves públicas, sin frases
            self.copias = HiloCopias()
            self.copias.start()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(3.0, self, "vigilaApariencia:", None, True)
        log("icono listo, trabajador, ingesta y copias en marcha")
        return self

    # --- del trabajador al hilo principal ---
    @objc.python_method
    def entregar(self, tipo, dato):
        """Lo llama el hilo trabajador: se reenvia al principal sin esperar."""
        with objc.autorelease_pool():           # hilo sin bucle de Cocoa: su propio pool
            self.performSelectorOnMainThread_withObject_waitUntilDone_("recibe:", {"tipo": tipo, "dato": dato}, False)

    def recibe_(self, msg):
        try:
            tipo, dato = msg["tipo"], msg["dato"]
            if tipo == "agentes":
                self.agentes = dict(dato)
                self.pinta_icono()
            elif tipo == "refresco_hecho":
                self.rueda_para()
            elif tipo == "punto":
                self.punto = dato
                self.pinta_icono()
            elif tipo == "conjunto":
                self.conjunto = dato
                log(f"imagenes nuevas: gen {dato.get('gen')}, {len(dato.get('servidores') or {})} servidores")
                if self.pop is not None and self.pop.isShown():
                    self.muestra()
                desplegable_png.limpiar(dato)
        except Exception as e:
            log(f"ERROR recibe {type(e).__name__}: {e}")

    @objc.python_method
    def pinta_icono(self):
        """Pastilla con la cifra de 5 h, relleno según el consumo y, tras un separador, el punto de los servidores."""
        a = getattr(self, "agentes", None)
        boton = self.item.button()
        self._oscuro = barra_oscura(boton)
        if not a:
            boton.setImage_(None)
            boton.setTitle_("…")
            return
        fraccion, alerta = icono_barra.relleno(a.get("pct_5h") or 0, a.get("ritmo") or 0, a.get("limite_hora") or 0)
        txt = costbar.fmt_tok(a["cinco_h"])
        boton.setImage_(imagen_icono(txt, fraccion, alerta, self.punto, self._oscuro))
        boton.setTitle_("")
        ritmo = f" · ritmo {costbar.fmt_tok(a.get('ritmo') or 0)}/h" + (f" (límite {costbar.fmt_tok(a['limite_hora'])}/h)" if a.get("limite_hora") else "")
        servidores = {"verde": " · servidores bien", "ambar": " · un servidor pide atención", "rojo": " · un servidor tiene un problema",
                      "gris": " · servidores sin datos recientes"}.get(self.punto, "")
        boton.setToolTip_(f"5 h: {txt} · hoy {costbar.fmt_tok(a['hoy'])} · {a['turnos']} turnos{ritmo}{servidores}")

    def vigilaApariencia_(self, _t):
        """La barra cambia de claro a oscuro con el fondo de pantalla o el modo del sistema: se repinta solo si cambió."""
        try:
            if barra_oscura(self.item.button()) != getattr(self, "_oscuro", None):
                self.pinta_icono()
        except Exception as e:
            log(f"ERROR apariencia {type(e).__name__}: {e}")

    # --- desplegable ---
    @objc.python_method
    def monta_popover(self):
        if self.pop is not None:
            return
        self.pop = NSPopover.alloc().init()
        # comportamiento "definido por la app" (0): el cierre por clic fuera lo hace este monitor
        self.pop.setBehavior_(0)
        self.monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            (1 << 1) | (1 << 3), lambda _e: self.pop.performClose_(None) if self.pop.isShown() else None)
        vc = NSViewController.alloc().init()
        fondo = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, G.ANCHO, 600))
        fondo.setMaterial_(MATERIAL_POPOVER)
        fondo.setBlendingMode_(0)          # detras de la ventana: se ve el escritorio desenfocado
        fondo.setState_(1)                 # siempre activo, aunque la app no tenga el foco
        fondo.setAutoresizingMask_(2)
        vista = VistaDesplegable.alloc().initWithFrame_(NSMakeRect(0, 0, G.ANCHO, 600))
        vista.setImageScaling_(1)          # NSImageScaleAxesIndependently: el PNG llena la vista
        vista.setAutoresizingMask_(18)
        vista.dueno = self
        fondo.addSubview_(vista)
        # ruedita nativa encima del boton de refrescar mientras el trabajador recoge y repinta
        self.rueda = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, 18, 18))
        self.rueda.setStyle_(1)                 # spinning
        self.rueda.setControlSize_(1)           # small
        self.rueda.setDisplayedWhenStopped_(False)
        fondo.addSubview_(self.rueda)
        # si el desplegable es más alto que la pantalla, se desplaza (barra superpuesta que solo aparece al hacer scroll) en vez de encogerse
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, G.ANCHO, 600))
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(True)
        scroll.setScrollerStyle_(1)             # NSScrollerStyleOverlay
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(0)
        scroll.setDocumentView_(fondo)
        vc.setView_(scroll)
        self.pop.setContentViewController_(vc)
        self.vista = vista
        self.fondo = fondo
        self.scroll = scroll

    @objc.python_method
    def pestana(self):
        return costbar.leer_ui().get("pestana") or "agentes"

    @objc.python_method
    def servidores(self):
        return costbar.cfg().get("servidores") or []

    @objc.python_method
    def servidor_activo(self):
        nombres = [s["nombre"] for s in self.servidores()]
        activo = costbar.leer_ui().get("servidor_activo")
        return activo if activo in nombres else (nombres[0] if nombres else "")

    @objc.python_method
    def imagen_actual(self):
        """(ruta, ancho, alto, n_botones, con_selector, secciones) de la pestana/servidor/tema activos, o None."""
        if not self.conjunto:
            return None
        tema = tema_activo()
        if self.pestana() == "servidores":
            imgs = self.conjunto.get("servidores", {}).get(self.servidor_activo()) or {}
        else:
            imgs = self.conjunto.get("agentes") or {}
        img = imgs.get(tema)
        if img:
            self._tema_avisado = None
        else:
            # solo se pinta el tema que se ve: si acaba de cambiar, se enseña el otro mientras el trabajador repinta
            # (se le avisa una vez por tema: si Chrome falla, el trabajador lo reintenta a su ritmo, no a cada repintado)
            if getattr(self, "_tema_avisado", None) != tema:
                self._tema_avisado = tema
                self.trabajador.pide("tema")
            img = next(iter(imgs.values()), None)
        return img if img and os.path.exists(img[0]) else None

    @objc.python_method
    def muestra(self):
        tema = tema_activo()
        self.pop.setAppearance_(NSAppearance.appearanceNamed_(
            "NSAppearanceNameDarkAqua" if tema == "oscuro" else "NSAppearanceNameAqua"))
        img = self.imagen_actual()
        self.forma = None                   # zonas de clic de la imagen que se ve (no de la config viva)
        if not img:
            self.pop.setContentSize_(NSMakeSize(G.ANCHO, 120))
            self.vista.setImage_(None)
            return
        ruta, ancho, alto, n_botones, con_selector, *resto = img
        secciones = resto[0] if resto else []
        self.forma = (n_botones, con_selector, secciones)
        clave = (self.pestana(), self.servidor_activo())
        desde_arriba = None
        if self._clave_vista == clave and self._alto_prev:
            vis = self.scroll.documentVisibleRect()
            desde_arriba = self._alto_prev - (vis.origin.y + vis.size.height)       # cuánto se había bajado: se conserva al desplegar una sección
        self._clave_vista, self._alto_prev = clave, float(alto)
        # si no cabe en la pantalla, el popover se queda de la altura de la pantalla y el contenido se desplaza (no se encoge: las letras seguirían legibles)
        pantalla = NSScreen.mainScreen().visibleFrame().size.height - 40
        self.escala = 1.0
        w, h = float(ancho), float(alto)
        visible = min(h, pantalla)
        self.alto_mostrado = alto
        self.pop.setContentSize_(NSMakeSize(w, visible))
        self.scroll.setFrameSize_(NSMakeSize(w, visible))
        # el fondo primero: si el popover lo redimensiona despues de fijar la vista, el autoajuste la estira
        # (la primera vez, de 600 pt a la altura real, la imagen salia con las letras enormes)
        self.fondo.setFrameSize_(NSMakeSize(w, h))
        self.vista.setFrameSize_(NSMakeSize(w, h))
        self.vista.setImage_(nsimagen(ruta, w, h))
        if desde_arriba is None:
            self.fondo.scrollPoint_(NSMakePoint(0, h))      # arriba del todo (las vistas de AppKit tienen el origen abajo)
        else:
            self.fondo.scrollPoint_(NSMakePoint(0, max(0.0, h - max(0.0, desde_arriba) - visible)))
        log(f"desplegable {self.pestana()} {tema}: {w:.0f}x{h:.0f} pt (escala {self.escala:.2f})")

    def pulsa_(self, sender):
        try:
            self.monta_popover()
            if self.pop.isShown():
                self.pop.performClose_(sender)
                return
            self.muestra()
            boton = self.item.button()
            self.pop.showRelativeToRect_ofView_preferredEdge_(boton.bounds(), boton, 1)
            visible = boton.window() is not None and boton.window().isVisible()
            log(f"desplegable abierto (icono visible en la barra: {visible})")
        except Exception:
            import traceback
            log("FALLO clic: " + traceback.format_exc()[-700:])

    # --- clics y acciones ---
    def clicEn_(self, p):
        if not getattr(self, "forma", None):
            return
        n_botones, con_selector, secciones = self.forma
        botones = (BOTONES_SERVIDORES if self.pestana() == "servidores" else BOTONES_AGENTES)[:n_botones]
        y_arriba = self.alto_mostrado - p.y / self.escala
        if G.MARGEN <= p.x / self.escala < G.ANCHO - G.MARGEN:
            for ident, y0, y1 in secciones:
                if y0 <= y_arriba < y1:                 # el título de una sección plegable: se abre o se cierra
                    return self.alterna(ident)
        zona = G.zona_en(p.x / self.escala, p.y / self.escala, self.alto_mostrado, n_botones, con_selector)
        log(f"clic en: {zona}")
        if zona is None:
            return
        if zona == "refrescar":
            self.refresca_ya()
        elif zona.startswith("pestana:"):
            costbar.guardar_ui({"pestana": zona.split(":", 1)[1]})
            self.muestra()
        elif zona == "selector":
            self.menu_servidores(p)
        elif zona == "menu":
            self.menu_general(p)
        elif zona.startswith("boton:"):
            self.boton(botones[int(zona.split(":")[1])][1])
            if self.pop.isShown():
                self.pop.performClose_(None)          # el botón ya hizo su trabajo (abrir el panel, la guía, un Terminal…): el desplegable no se queda tapando

    @objc.python_method
    def alterna(self, ident):
        abiertas = {x for x in (costbar.leer_ui().get("abiertas") or []) if isinstance(x, str)}
        abiertas.symmetric_difference_update({ident})
        costbar.guardar_ui({"abiertas": sorted(abiertas)})
        log(f"sección {ident}: {'abierta' if ident in abiertas else 'cerrada'}")
        self.trabajador.pide("plegar")                  # repinta solo el servidor que se está viendo

    @objc.python_method
    def boton(self, texto):
        s = next((x for x in self.servidores() if x["nombre"] == self.servidor_activo()), None)
        if texto == "Abrir panel":
            self.abre(costbar.url_panel())
        elif texto == "Guía":
            self.abre(costbar.url_panel("guia"))
        elif s and texto == "Panel" and s.get("panel_url"):
            self.abre(s["panel_url"], publica=True)
        elif s and texto == "Abrir" and s.get("webs"):
            self.abre(s["webs"][0], publica=True)
        elif s and texto == "Terminal":
            self.terminal(s)

    @objc.python_method
    def abre(self, url, publica=False):
        if publica and url_valida(url):
            log(f"url rechazada: {url[:80]!r}")
            return
        # NSWorkspace y no `open <url>`: los argumentos de un proceso los ve cualquier usuario con `ps`, y la URL del
        # panel lleva un ticket de entrada de un solo uso
        nsurl = NSURL.URLWithString_(url)
        if nsurl is None or not NSWorkspace.sharedWorkspace().openURL_(nsurl):
            log(f"no se pudo abrir la URL ({url.split('?')[0][:60]})")

    @objc.python_method
    def terminal(self, s):
        """Terminal con tu sesion SSH normal (ssh_admin, p. ej. victor@...): la clave de infobar no da shell."""
        if errores_servidores([s]):
            return
        destino = s.get("ssh_admin") or s["ssh"].split("@")[-1]
        puerto = int(s.get("puerto") or 22)
        orden = f"ssh {shlex.quote(destino)}" + (f" -p {puerto}" if puerto != 22 else "")
        guion = f'tell application "Terminal" to do script "{orden}"\ntell application "Terminal" to activate'
        subprocess.Popen(["osascript", "-e", guion])   # el permiso de Automatizacion no congela la barra

    @objc.python_method
    def menu_en(self, menu, p):
        menu.popUpMenuPositioningItem_atLocation_inView_(None, NSMakePoint(p.x, p.y), self.vista)

    @objc.python_method
    def entrada_menu(self, titulo, accion, objeto=None, marcado=False):
        it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(titulo, accion, "")
        it.setTarget_(self)
        if objeto is not None:
            it.setRepresentedObject_(objeto)
        it.setState_(1 if marcado else 0)
        return it

    @objc.python_method
    def menu_servidores(self, p):
        menu = NSMenu.alloc().init()
        cache = servidores_ciclo.leer_cache().get("servidores") or {}
        activo = self.servidor_activo()
        normal, _ = servidores_ciclo.intervalos()
        marca = {"verde": "🟢", "ambar": "🟠", "rojo": "🔴", "gris": "⚪️"}
        for s in self.servidores():
            est = servidores_ciclo.estado_visible(cache.get(s["nombre"]), None, normal)
            menu.addItem_(self.entrada_menu(f"{marca[est]}  {s['nombre']}", "eligeServidor:", s["nombre"], s["nombre"] == activo))
        menu.addItem_(NSMenuItem.separatorItem())
        if activo:
            menu.addItem_(self.entrada_menu(f"Modo incidencia en {activo} (30 min, cada minuto)", "incidencia:", activo))
        self.menu_en(menu, p)

    def eligeServidor_(self, it):
        costbar.guardar_ui({"servidor_activo": str(it.representedObject()), "pestana": "servidores"})
        self.muestra()

    def incidencia_(self, it):
        servidores_ciclo.activar_incidencia(str(it.representedObject()))
        self.trabajador.pide("refrescar")

    @objc.python_method
    def menu_general(self, p):
        menu = NSMenu.alloc().init()
        pref = tema_preferido()
        sub = NSMenu.alloc().init()
        for clave, txt in (("claro", "Claro"), ("oscuro", "Oscuro"), ("sistema", "Como el sistema")):
            sub.addItem_(self.entrada_menu(txt, "tema:", clave, pref == clave))
        tema = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Tema", None, "")
        tema.setSubmenu_(sub)
        menu.addItem_(tema)
        menu.addItem_(self.entrada_menu("Refrescar ahora", "refrescarAhora:"))
        if self.pestana() == "servidores" and self.servidores():
            menu.addItem_(self.entrada_menu(f"Copiar cómo protegerlo ({self.servidor_activo()})", "copiaArreglos:"))
        menu.addItem_(self.entrada_menu("Abrir panel web", "panelWeb:"))
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(self.entrada_menu("Salir de infobar", "salir:"))
        self.menu_en(menu, p)

    def tema_(self, it):
        costbar.guardar_ui({"tema": str(it.representedObject())})
        self.muestra()              # con el tema anterior hasta que el trabajador pinte el nuevo (unos segundos)

    def refrescarAhora_(self, _):
        self.refresca_ya()

    @objc.python_method
    def refresca_ya(self):
        """Pide al trabajador recoger todo ya (NaN incluido) y lo muestra con la ruedita."""
        if getattr(self, "rueda", None) is not None and self.pop is not None:
            alto = self.vista.frame().size.height
            x = (G.REFRESCAR_X + G.REFRESCAR_ANCHO / 2) * self.escala - 9
            y = alto - (G.CABECERA_ALTO / 2) * self.escala - 9
            self.rueda.setFrameOrigin_(NSMakePoint(x, y))
            self.rueda.startAnimation_(None)
        self.trabajador.pide("refrescar")

    @objc.python_method
    def rueda_para(self):
        if getattr(self, "rueda", None) is not None:
            self.rueda.stopAnimation_(None)

    def copiaArreglos_(self, _):
        """Copia al portapapeles lo que falta en el servidor activo y como arreglarlo."""
        e = (servidores_ciclo.leer_cache().get("servidores") or {}).get(self.servidor_activo()) or {}
        falta = [c for c in (e.get("bastionado") or []) if c["ok"] is False]
        texto = "\n".join(f"# {c['texto']}\n{c['arreglo']}\n" for c in falta) or "# nada pendiente"
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(f"# {self.servidor_activo()}: que falta para protegerlo\n" + texto, NSPasteboardTypeString)

    def panelWeb_(self, _):
        self.abre(costbar.url_panel())

    def salir_(self, _):
        NSApplication.sharedApplication().terminate_(None)


def main():
    costbar.endurecer()       # umask 0077 y permisos de lo que ya habia: todo esto es sensible
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    barra = Barra.alloc().init()
    costbar.servir()          # el panel y su configurador, en un hilo aparte
    if "--abre" in sys.argv:
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1.5, barra, "pulsa:", None, False)
    log("entrando en el bucle")
    app.run()


if __name__ == "__main__":
    main()
