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
_saliendo = False        # la ventana se esta cerrando de verdad (Salir, cierre de sesion, apagado): no se cancela
_salida_pedida = False   # solo "Salir": distingue el cierre pedido del que hace el sistema
_hwnd = None   # HWND real de la ventana, resuelto tras el primer show() (no cambia: no se destruye
               # entre show/hide, solo con "Salir" -- por eso basta con resolverlo una vez)
_lock = threading.Lock()   # mostrar/ocultar/cerrar: protege _visible y _hwnd entre el hilo de
                            # pystray (clics) y el hilo del hook de foco (cierre automatico)

# No se usa easy_drag=True de pywebview a proposito: en Windows arrastra desde CUALQUIER punto
# de la pagina, y el panel ya tiene su propio arrastre con el raton para seleccionar un rango de
# fechas en el grafico (app.js, arrastreDesde). Solo el titulo es zona de arrastre, con el selector
# que pywebview aplica en cada carga de pagina (el panel se recarga solo cada 60 s).
SELECTOR_ARRASTRE = ".cab h1, #sub"

_EVENT_SYSTEM_FOREGROUND = 0x0003
_WINEVENT_OUTOFCONTEXT = 0x0000
_WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
    wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)

# Sin esto, ctypes asume que GetAncestor/SetWinEventHook devuelven "int" (32 bits con signo) en
# vez de un handle de 64 bits: en Windows de 64 bits un HWND/HWINEVENTHOOK por encima de 0x7fffffff
# saldria truncado o en negativo, rompiendo toda comparacion "hwnd != _hwnd" de mas abajo (mismo
# tipo de fallo que el de los handles en infobar/claves.py).
ctypes.windll.user32.GetAncestor.restype = wintypes.HWND
ctypes.windll.user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
ctypes.windll.user32.SetWinEventHook.restype = wintypes.HANDLE
ctypes.windll.user32.SetWinEventHook.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE, _WINEVENTPROC,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]


_GA_ROOTOWNER = 3
# Página en blanco como CONTENIDO, nunca como URL: pywebview trata como fichero local cualquier URL que no empiece por
# http://, https:// o file:// (about:blank incluida) y levanta su servidor bottle, sin sesión, sirviendo el directorio
# de trabajo del proceso. Con html= / load_html() no arranca ningún servidor.
_EN_BLANCO = "<!doctype html><title></title>"


def _escala():
    """Factor de escala de la pantalla (1.0 al 100 %, 1.5 al 150 %). pywebview marca el proceso como consciente de DPI,
    así que el cursor y el área de trabajo llegan en píxeles físicos, mientras que create_window()/move() esperan
    píxeles lógicos y los vuelven a multiplicar por la escala: sin dividir, el popover sale fuera de la pantalla."""
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
    except (AttributeError, OSError):           # Windows anterior a 10 (1607)
        return 1.0


def _posicion_cursor():
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    e = _escala()
    return pt.x / e, pt.y / e


def area_trabajo():
    """Rectangulo de pantalla SIN la barra de tareas (SPI_GETWORKAREA): para que el popover no
    quede tapado por la barra de tareas ni se salga de la pantalla."""
    SPI_GETWORKAREA = 0x0030
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    e = _escala()
    return r.left / e, r.top / e, r.right / e, r.bottom / e       # en píxeles lógicos, como ANCHO y ALTO


def _posicion():
    """Ancla el popover junto al cursor (justo donde esta el icono que se acaba de clicar), sin
    salirse del area de trabajo (pantalla menos la barra de tareas)."""
    cx, cy = _posicion_cursor()
    izq, arriba, derecha, abajo = area_trabajo()
    x = min(max(cx - ANCHO, izq), derecha - ANCHO)
    y = min(max(cy - ALTO, arriba), abajo - ALTO)
    return int(x), int(y)


def _es_del_popover(hwnd):
    """La ventana es el popover o un diálogo suyo (alert, guardar el CSV, un desplegable nativo): esas no lo cierran."""
    if hwnd == _hwnd:
        return True
    return bool(hwnd) and ctypes.windll.user32.GetAncestor(hwnd, _GA_ROOTOWNER) == _hwnd


def _ocultar():
    """Oculta y deja la página en blanco: oculto, el panel seguiría recargándose cada 60 s (y regenerándose en el servidor)
    sin que nadie lo mire. Cada apertura carga una URL nueva con su ticket. Llamar con _lock tomado."""
    global _visible
    _visible = False
    _ventana.hide()
    _ventana.load_html(_EN_BLANCO)


def _al_cambiar_primer_plano(hook, evento, hwnd, id_objeto, id_hijo, id_hilo_evento, tiempo_ms):
    """Se dispara con CUALQUIER cambio de ventana en primer plano del sistema (no solo de este
    proceso): si el popover esta visible y la nueva ventana en primer plano no es el, se oculta --
    el "clic fuera cierra" de un popover nativo, pedido explicitamente."""
    if _hwnd and _visible and not _es_del_popover(hwnd):
        with _lock:
            if _visible:
                try:
                    _ocultar()
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


def crear():
    """Crea la ventana oculta y en blanco (requiere TIENE_WEBVIEW): oculta con el panel cargado se recargaria cada 60 s
    sin que nadie la mire. El panel se carga al mostrarla. Llamar una vez, desde main()."""
    global _ventana
    izq, arriba, derecha, abajo = area_trabajo()
    webview.settings["DRAG_REGION_SELECTOR"] = SELECTOR_ARRASTRE
    _ventana = webview.create_window(
        TITULO_VENTANA, html=_EN_BLANCO, width=ANCHO, height=ALTO,
        x=int(derecha - ANCHO), y=int(max(arriba, abajo - ALTO)),
        hidden=True, frameless=True, on_top=True, resizable=False, easy_drag=False,
        background_color="#212124")
    _ventana.events.before_show += _engancha_cierre
    return _ventana


def _engancha_cierre():
    """El aviso `closing` de pywebview no dice por qué se cierra la ventana: se escucha el FormClosing nativo, que sí.
    before_show corre en el hilo de la interfaz, con la ventana ya creada (shown llegaria en otro hilo)."""
    _ventana.native.FormClosing += _al_cerrar


def _cierra_el_usuario(args):
    try:
        from System.Windows.Forms import CloseReason      # pythonnet: el enum de .NET, ya cargado por pywebview
        return args.CloseReason == CloseReason.UserClosing
    except Exception:
        return str(args.CloseReason) == "UserClosing"


def _al_cerrar(_emisor, args):
    """Alt+F4 sobre el popover cerraría la única ventana y webview.start() volvería: el icono seguiría vivo sin poder
    abrir nada. Si lo cierra el usuario, se cancela y se oculta (en blanco). Cualquier otro motivo (cierre de sesión,
    apagado, el propio Salir) se deja pasar: cancelarlo haría que Windows dijera «CostGuard impide cerrar sesión»."""
    global _visible, _saliendo
    if _saliendo or not _cierra_el_usuario(args):
        _saliendo = True
        return
    args.Cancel = True
    # sin _lock: este evento llega en el hilo de la interfaz, y alternar() puede tener el cerrojo esperando a ese mismo hilo
    _visible = False
    try:
        _ventana.hide()
        _ventana.load_html(_EN_BLANCO)
    except Exception:
        pass


def salida_pedida():
    return _salida_pedida


def iniciar_vigilancia_foco(log):
    threading.Thread(target=_hilo_vigilar_foco, args=(log,), name="bandeja-foco", daemon=True).start()


def alternar(url_fresca, log):
    """Toggle mostrar/ocultar. Si ya esta visible, lo oculta; si no, carga el panel fresco y lo
    muestra junto al cursor. `url_fresca` es una funcion (costbar.url_panel): el ticket solo se pide al mostrar.

    Orden importante dentro de la rama de mostrar: show() -> _visible=True -> move(), en ese orden exacto.
    - show() antes que move(): move() espera a que el evento "shown" se haya disparado ALGUNA VEZ
      (API de pywebview); en el primerisimo click hay que mostrar primero (en la posicion inicial
      de create_window, ya razonable) y solo entonces reposicionar. En el resto de clicks "shown"
      ya esta disparado y move() no espera nada.
    - _visible=True justo tras show(), ANTES de move()/resolver el hwnd: si cualquiera de esos pasos
      lanza una excepcion, la ventana ya esta fisicamente visible en pantalla: que _visible siga en
      False dejaria el popover visible pero "atascado" (ni el toggle de clic ni el cierre automatico
      al clicar fuera funcionarian, los dos dependen de este flag)."""
    global _visible, _hwnd
    with _lock:
        if _visible:
            try:
                _ocultar()
            except Exception as e:
                log(f"bandeja: ERROR popover (ocultar) {type(e).__name__}: {e}")
            return
        try:
            url = url_fresca()
            if not url.startswith("http://127.0.0.1:"):        # solo el panel local: otra URL podria levantar el servidor de pywebview
                raise ValueError("URL del panel inesperada")
            _ventana.load_url(url)
            _ventana.show()
            _visible = True
            x, y = _posicion()
            _ventana.move(x, y)
            if not _hwnd:   # el de esta ventana, no el primero que se llame "CostGuard"
                _hwnd = _ventana.native.Handle.ToInt64()
        except Exception as e:
            log(f"bandeja: ERROR popover {type(e).__name__}: {e}")


def cerrar(log):
    """Para _accion_salir: destruye la ventana (termina webview.start(), bloqueante en el hilo
    principal). Protegido por el mismo lock que mostrar/ocultar para no pisarse con un cierre
    automatico en curso."""
    global _saliendo, _salida_pedida
    _saliendo = _salida_pedida = True
    with _lock:
        if _ventana is None:
            return
        try:
            _ventana.destroy()
        except Exception as e:
            log(f"bandeja: ERROR al cerrar el popover {type(e).__name__}: {e}")
