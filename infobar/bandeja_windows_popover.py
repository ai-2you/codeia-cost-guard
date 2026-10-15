"""Ventana del panel en Windows (pywebview/WebView2): carga el panel sin navegador, con el marco
nativo normal de Windows (minimizar, maximizar y cerrar) en vez de un popover sin bordes.

Separado de bandeja_windows.py (que ya rozaba las 200 lineas del estandar del repo con esto
dentro) para mantener cada fichero enfocado: este solo sabe de la ventana y de Win32, no de
pystray ni del motor de costbar. `log` se pasa como parametro en vez de importar costbar aqui,
para no acoplar este modulo al motor.

Comportamiento del boton de cerrar (la X nativa): OCULTA la ventana, no la destruye -- la app
sigue viva en la bandeja (igual que Signal/Slack/Discord). Solo "Salir" del menu de la bandeja
destruye de verdad la ventana (ver cerrar()); sin esa distincion, "Salir" se quedaria colgado:
destroy() dispara el MISMO evento nativo de cierre que la X (FormClosing en WinForms), asi que
hace falta una bandera (_saliendo) para dejarlo pasar solo cuando lo pide cerrar()."""
import ctypes
import threading
from ctypes import wintypes

try:
    import webview
    TIENE_WEBVIEW = True
except ImportError:
    TIENE_WEBVIEW = False   # bandeja_windows.py cae al navegador si esto es False

ANCHO, ALTO = 420, 720   # tamano inicial ("ventanita alargada"); ahora redimensionable (ver crear())
TITULO_VENTANA = "CostGuard"

_ventana = None
_visible = False
_saliendo = False   # True solo durante cerrar() (Salir del menu): distingue "cerrar de verdad" de
                    # "la X nativa", que disparan el mismo evento closing (ver _al_intentar_cerrar)
_lock = threading.Lock()   # protege _visible/_saliendo entre el hilo de pystray (clics del menu)
                            # y el hilo de pywebview (clics en la X, minimizar, maximizar)


def _posicion_cursor():
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def area_trabajo():
    """Rectangulo de pantalla SIN la barra de tareas (SPI_GETWORKAREA): para que la ventana no
    quede tapada por la barra de tareas ni se salga de la pantalla."""
    SPI_GETWORKAREA = 0x0030
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def _posicion():
    """Ancla la ventana junto al cursor (justo donde esta el icono que se acaba de clicar), sin
    salirse del area de trabajo (pantalla menos la barra de tareas)."""
    cx, cy = _posicion_cursor()
    izq, arriba, derecha, abajo = area_trabajo()
    x = min(max(cx - ANCHO, izq), derecha - ANCHO)
    y = min(max(cy - ALTO, arriba), abajo - ALTO)
    return x, y


def _al_intentar_cerrar():
    """Handler de events.closing (se dispara con la X nativa Y con destroy() -- mismo evento
    FormClosing de WinForms en los dos casos). Si no viene de cerrar() (Salir), oculta la ventana
    y VETA el cierre nativo devolviendo False (pywebview cancela el cierre si algun handler
    devuelve exactamente False, ver webview/event.py). Si viene de cerrar(), no hace nada y deja
    pasar el cierre real (return None, que no cuenta como veto)."""
    global _visible
    if _saliendo:
        return None
    _visible = False
    try:
        _ventana.hide()
    except Exception:
        pass   # nada que loguear aqui: no hay `log` a mano en el callback del evento
    return False


def crear(url_inicial):
    """Crea la ventana oculta (requiere TIENE_WEBVIEW), con marco nativo (minimizar/maximizar/
    cerrar) y redimensionable. Llamar una vez, desde main()."""
    global _ventana
    izq, arriba, derecha, abajo = area_trabajo()
    _ventana = webview.create_window(
        TITULO_VENTANA, url=url_inicial, width=ANCHO, height=ALTO,
        x=derecha - ANCHO, y=max(arriba, abajo - ALTO),
        hidden=True, frameless=False, resizable=True, background_color="#212124")
    _ventana.events.closing += _al_intentar_cerrar
    return _ventana


def alternar(url_fresca, log):
    """Toggle mostrar/ocultar desde el icono de la bandeja. Si ya esta visible, la oculta; si no,
    carga el panel fresco y la muestra junto al cursor.

    Orden importante dentro de la rama de mostrar: show() -> _visible=True -> move().
    - show() antes que move(): move() espera a que el evento "shown" se haya disparado ALGUNA VEZ
      (API de pywebview); en el primerisimo click hay que mostrar primero (en la posicion inicial
      de create_window, ya razonable) y solo entonces reposicionar. En el resto de clicks "shown"
      ya esta disparado y move() no espera nada.
    - _visible=True justo tras show(), ANTES de move(): si move() lanzara una excepcion, la
      ventana ya esta fisicamente visible en pantalla; que _visible siga en False dejaria la
      ventana visible pero "atascada" (el toggle de clic no la ocultaria)."""
    global _visible
    with _lock:
        if _visible:
            _visible = False
            try:
                _ventana.hide()
            except Exception as e:
                log(f"bandeja: ERROR ventana (ocultar) {type(e).__name__}: {e}")
            return
        try:
            _ventana.load_url(url_fresca)
            _ventana.show()
            _visible = True
            x, y = _posicion()
            _ventana.move(x, y)
        except Exception as e:
            log(f"bandeja: ERROR ventana {type(e).__name__}: {e}")


def cerrar(log):
    """Para _accion_salir: destruye la ventana de verdad (termina webview.start(), bloqueante en
    el hilo principal), dejando pasar el cierre pese al handler de events.closing que normalmente
    lo vetaria (la X nativa oculta en vez de cerrar)."""
    global _saliendo
    with _lock:
        if _ventana is None:
            return
        _saliendo = True
        try:
            _ventana.destroy()
        except Exception as e:
            log(f"bandeja: ERROR al cerrar la ventana {type(e).__name__}: {e}")
