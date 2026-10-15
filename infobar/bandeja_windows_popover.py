"""Popover nativo de Windows (pywebview/WebView2): posicionamiento junto al cursor, arrastre por
el titulo (via JS inyectado) y cierre automatico al clicar fuera (hook de foco de Win32).

Separado de bandeja_windows.py (que ya rozaba las 200 lineas del estandar del repo con esto
dentro) para mantener cada fichero enfocado: este solo sabe de la ventana y de Win32, no de
pystray ni del motor de costbar. `log` se pasa como parametro en vez de importar costbar aqui,
para no acoplar este modulo al motor.
"""
import ctypes
import threading
from ctypes import wintypes

try:
    import webview
    TIENE_WEBVIEW = True
except ImportError:
    TIENE_WEBVIEW = False   # bandeja_windows.py cae al navegador si esto es False

ANCHO, ALTO = 420, 720   # "ventanita alargada", pedido explicito del usuario
TITULO_VENTANA = "CostGuard"

_ventana = None
_visible = False
_hwnd = None   # HWND real de la ventana, resuelto tras el primer show() (no cambia: no se destruye
               # entre show/hide, solo con "Salir" -- por eso basta con resolverlo una vez)
_lock = threading.Lock()   # mostrar/ocultar/cerrar: protege _visible y _hwnd entre el hilo de
                            # pystray (clics) y el hilo del hook de foco (cierre automatico)

# No se usa easy_drag=True de pywebview a proposito: en Windows arrastra desde CUALQUIER punto
# de la pagina sin excepcion, y el panel ya tiene su propio arrastre con el raton para seleccionar
# un rango de fechas en el grafico (app.js, arrastreDesde) -- con easy_drag ambos se pisarian. En
# su lugar se marca SOLO el titulo como zona de arrastre (igual que agarrar una barra de titulo),
# via la clase "pywebview-drag-region" que pywebview ya reconoce de forma nativa para esto.
_JS_ZONA_ARRASTRE = """
(function(){
  var t = document.querySelector('.cab h1'), s = document.getElementById('sub');
  [t, s].forEach(function(el){
    if (el) { el.classList.add('pywebview-drag-region'); el.style.cursor = 'move'; }
  });
})();
"""

_EVENT_SYSTEM_FOREGROUND = 0x0003
_WINEVENT_OUTOFCONTEXT = 0x0000
_WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
    wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)

# Sin esto, ctypes asume que FindWindowW/SetWinEventHook devuelven "int" (32 bits con signo) en
# vez de un handle de 64 bits: en Windows de 64 bits un HWND/HWINEVENTHOOK por encima de 0x7fffffff
# saldria truncado o en negativo, rompiendo toda comparacion "hwnd != _hwnd" de mas abajo (mismo
# tipo de bug que el de use_last_error en infobar/credenciales_windows.py).
ctypes.windll.user32.FindWindowW.restype = wintypes.HWND
ctypes.windll.user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
ctypes.windll.user32.SetWinEventHook.restype = wintypes.HANDLE
ctypes.windll.user32.SetWinEventHook.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE, _WINEVENTPROC,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]


def _posicion_cursor():
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def area_trabajo():
    """Rectangulo de pantalla SIN la barra de tareas (SPI_GETWORKAREA): para que el popover no
    quede tapado por la barra de tareas ni se salga de la pantalla."""
    SPI_GETWORKAREA = 0x0030
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def _posicion():
    """Ancla el popover junto al cursor (justo donde esta el icono que se acaba de clicar), sin
    salirse del area de trabajo (pantalla menos la barra de tareas)."""
    cx, cy = _posicion_cursor()
    izq, arriba, derecha, abajo = area_trabajo()
    x = min(max(cx - ANCHO, izq), derecha - ANCHO)
    y = min(max(cy - ALTO, arriba), abajo - ALTO)
    return x, y


def _al_cambiar_primer_plano(hook, evento, hwnd, id_objeto, id_hijo, id_hilo_evento, tiempo_ms):
    """Se dispara con CUALQUIER cambio de ventana en primer plano del sistema (no solo de este
    proceso): si el popover esta visible y la nueva ventana en primer plano no es el, se oculta --
    el "clic fuera cierra" de un popover nativo, pedido explicitamente."""
    global _visible
    if _hwnd and _visible and hwnd != _hwnd:
        with _lock:
            if _visible:
                _visible = False
                try:
                    _ventana.hide()
                except Exception:
                    pass   # nada que loguear aqui: no hay `log` a mano en el callback de ctypes


_callback_primer_plano = _WINEVENTPROC(_al_cambiar_primer_plano)   # referencia viva: si se recolecta
                                                                    # el GC, el callback deja de llamarse


def _hilo_vigilar_foco(log):
    """Hilo daemon con su propio bucle de mensajes de Windows: SetWinEventHook solo entrega
    eventos al hilo que instalo el hook, y para eso ese hilo tiene que bombear mensajes (GetMessage/
    DispatchMessage), igual que cualquier ventana de Windows. No usa nada interno de pywebview:
    es la API de accesibilidad de Win32 (WinEvent), independiente de que libreria dibuje la ventana.

    El hook (y este bucle) viven mientras viva el proceso: no hay UnhookWinEvent explicito porque
    el hilo es daemon y el handle lo libera el sistema al salir -- aceptado a proposito, no vale la
    complejidad de pasar el id de este hilo a _accion_salir solo para un apagado mas limpio."""
    hook = ctypes.windll.user32.SetWinEventHook(
        _EVENT_SYSTEM_FOREGROUND, _EVENT_SYSTEM_FOREGROUND, 0,
        _callback_primer_plano, 0, 0, _WINEVENT_OUTOFCONTEXT)
    if not hook:
        log("bandeja: aviso, no se pudo instalar el hook de foco "
            "(el popover no se cerrara solo al clicar fuera)")
        return
    msg = wintypes.MSG()
    while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
        ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))


def crear(url_inicial):
    """Crea la ventana oculta (requiere TIENE_WEBVIEW) y arranca el hilo que la cierra sola al
    clicar fuera. Llamar una vez, desde main()."""
    global _ventana
    izq, arriba, derecha, abajo = area_trabajo()
    _ventana = webview.create_window(
        TITULO_VENTANA, url=url_inicial, width=ANCHO, height=ALTO,
        x=derecha - ANCHO, y=max(arriba, abajo - ALTO),
        hidden=True, frameless=True, on_top=True, resizable=False, easy_drag=False,
        background_color="#212124")
    return _ventana


def iniciar_vigilancia_foco(log):
    threading.Thread(target=_hilo_vigilar_foco, args=(log,), name="bandeja-foco", daemon=True).start()


def alternar(url_fresca, log):
    """Toggle mostrar/ocultar. Si ya esta visible, lo oculta; si no, carga el panel fresco y lo
    muestra junto al cursor.

    Orden importante dentro de la rama de mostrar: show() -> _visible=True -> move() ->
    evaluate_js(), en ese orden exacto.
    - show() antes que move(): move() espera a que el evento "shown" se haya disparado ALGUNA VEZ
      (API de pywebview); en el primerisimo click hay que mostrar primero (en la posicion inicial
      de create_window, ya razonable) y solo entonces reposicionar. En el resto de clicks "shown"
      ya esta disparado y move() no espera nada.
    - _visible=True justo tras show(), ANTES de move()/resolver el hwnd/evaluate_js: si cualquiera
      de esos pasos lanza una excepcion, la ventana ya esta fisicamente visible en pantalla: que
      _visible siga en False dejaria el popover visible pero "atascado" (ni el toggle de clic ni
      el cierre automatico al clicar fuera funcionarian, los dos dependen de este flag).
    - evaluate_js() la ULTIMA: espera a que la pagina este lista (puede tardar 1-2 s en cargar el
      panel entero); si move() fuera despues de evaluate_js, el popover se veria aparecer en la
      posicion vieja (la del click anterior) y "saltar" a la correcta 1-2 s mas tarde -- el bug
      original reportado en pruebas reales."""
    global _visible, _hwnd
    with _lock:
        if _visible:
            _visible = False
            try:
                _ventana.hide()
            except Exception as e:
                log(f"bandeja: ERROR popover (ocultar) {type(e).__name__}: {e}")
            return
        try:
            _ventana.load_url(url_fresca)
            _ventana.show()
            _visible = True
            x, y = _posicion()
            _ventana.move(x, y)
            if not _hwnd:   # no "is None": 0 (no encontrado aun) debe reintentarse tambien
                _hwnd = ctypes.windll.user32.FindWindowW(None, TITULO_VENTANA)
            _ventana.evaluate_js(_JS_ZONA_ARRASTRE)
        except Exception as e:
            log(f"bandeja: ERROR popover {type(e).__name__}: {e}")


def cerrar(log):
    """Para _accion_salir: destruye la ventana (termina webview.start(), bloqueante en el hilo
    principal). Protegido por el mismo lock que mostrar/ocultar para no pisarse con un cierre
    automatico en curso."""
    with _lock:
        if _ventana is None:
            return
        try:
            _ventana.destroy()
        except Exception as e:
            log(f"bandeja: ERROR al cerrar el popover {type(e).__name__}: {e}")
