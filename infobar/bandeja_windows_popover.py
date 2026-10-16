"""Ventana del panel en Windows (pywebview/WebView2): carga el panel sin navegador, con el marco nativo normal de Windows
(minimizar, maximizar y cerrar) y redimensionable, colocada junto al icono de la bandeja.

La X la OCULTA, no la destruye: la app sigue viva en la bandeja (igual que Signal, Slack o Discord). Solo "Salir" del menú
de la bandeja la cierra de verdad, y el cierre de sesión o el apagado de Windows se dejan pasar siempre (ver _al_cerrar).

Separado de bandeja_windows.py para mantener cada fichero enfocado: este solo sabe de la ventana y de Win32, no de pystray
ni del motor de costbar. `log` se pasa como parámetro en vez de importar costbar aquí.
"""
import ctypes
import threading
from ctypes import wintypes

try:
    import webview
    TIENE_WEBVIEW = True
except ImportError:
    TIENE_WEBVIEW = False   # bandeja_windows.py cae al navegador si esto es False

ANCHO, ALTO = 420, 720   # tamaño inicial ("ventanita alargada"); se puede redimensionar
TITULO_VENTANA = "CostGuard"

_ventana = None
_visible = False
_saliendo = False        # la ventana se está cerrando de verdad (Salir, cierre de sesión, apagado): no se cancela
_salida_pedida = False   # solo "Salir": distingue el cierre pedido del que hace el sistema
_lock = threading.Lock()   # mostrar/cerrar desde el hilo de pystray (clics en el icono y el menú)

# Página en blanco como CONTENIDO, nunca como URL: pywebview trata como fichero local cualquier URL que no empiece por
# http://, https:// o file:// (about:blank incluida) y levanta su servidor bottle, sin sesión, sirviendo el directorio
# de trabajo del proceso. Con html= / load_html() no arranca ningún servidor.
_EN_BLANCO = "<!doctype html><title></title>"


def _escala():
    """Factor de escala de la pantalla (1.0 al 100 %, 1.5 al 150 %). pywebview marca el proceso como consciente de DPI,
    así que el cursor y el área de trabajo llegan en píxeles físicos, mientras que create_window()/move() esperan
    píxeles lógicos y los vuelven a multiplicar por la escala: sin dividir, la ventana sale fuera de la pantalla."""
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
    """Rectángulo de pantalla SIN la barra de tareas (SPI_GETWORKAREA): para que la ventana no quede tapada por la barra
    de tareas ni se salga de la pantalla."""
    SPI_GETWORKAREA = 0x0030
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    e = _escala()
    return r.left / e, r.top / e, r.right / e, r.bottom / e       # en píxeles lógicos, como ANCHO y ALTO


def _posicion():
    """Ancla la ventana junto al cursor (justo donde está el icono que se acaba de pulsar), sin salirse del área de trabajo.
    Con el tamaño que tenga ahora (se puede redimensionar), no con el inicial."""
    cx, cy = _posicion_cursor()
    izq, arriba, derecha, abajo = area_trabajo()
    try:
        ancho, alto = int(_ventana.width) or ANCHO, int(_ventana.height) or ALTO
    except Exception:
        ancho, alto = ANCHO, ALTO
    x = min(max(cx - ancho, izq), derecha - ancho)
    y = min(max(cy - alto, arriba), abajo - alto)
    return int(x), int(y)


def crear():
    """Crea la ventana oculta y en blanco (requiere TIENE_WEBVIEW), con marco nativo y redimensionable. El panel se carga
    al mostrarla. Llamar una vez, desde main()."""
    global _ventana
    izq, arriba, derecha, abajo = area_trabajo()
    _ventana = webview.create_window(
        TITULO_VENTANA, html=_EN_BLANCO, width=ANCHO, height=ALTO,
        x=int(derecha - ANCHO), y=int(max(arriba, abajo - ALTO)),
        hidden=True, frameless=False, resizable=True, background_color="#212124")
    _ventana.events.before_show += _engancha_cierre
    return _ventana


def _engancha_cierre():
    """El aviso `closing` de pywebview no dice por qué se cierra la ventana: se escucha el FormClosing nativo, que sí.
    before_show corre en el hilo de la interfaz, con la ventana ya creada (shown llegaría en otro hilo)."""
    _ventana.native.FormClosing += _al_cerrar


def _cierra_el_usuario(args):
    try:
        from System.Windows.Forms import CloseReason      # pythonnet: el enum de .NET, ya cargado por pywebview
        return args.CloseReason == CloseReason.UserClosing
    except Exception:
        return str(args.CloseReason) == "UserClosing"


def _al_cerrar(_emisor, args):
    """La X (o Alt+F4) cerraría la única ventana y webview.start() volvería: el icono seguiría vivo sin poder abrir nada.
    Si la cierra el usuario, se cancela y se oculta (en blanco). Cualquier otro motivo (cierre de sesión, apagado, el
    propio Salir) se deja pasar: cancelarlo haría que Windows dijera «CostGuard impide cerrar sesión»."""
    global _visible, _saliendo
    if _saliendo or not _cierra_el_usuario(args):
        _saliendo = True
        return
    args.Cancel = True
    # sin _lock: este evento llega en el hilo de la interfaz, y mostrar() puede tener el cerrojo esperando a ese mismo hilo
    _visible = False
    try:
        _ventana.hide()
        _ventana.load_html(_EN_BLANCO)
    except Exception:
        pass


def salida_pedida():
    return _salida_pedida


_SW_RESTORE = 9


def _hwnd():
    return wintypes.HWND(_ventana.native.Handle.ToInt64())


def _minimizada():
    """Estado real de la ventana en el momento del clic (los avisos de minimizar/restaurar de pywebview corren en hilos
    aparte, sin orden: un estado guardado a partir de ellos se desincroniza)."""
    try:
        return bool(ctypes.windll.user32.IsIconic(_hwnd()))
    except Exception:
        return False


def _restaurar():
    """SW_RESTORE devuelve la ventana a como estaba antes de minimizarla, maximizada incluida (restore() de pywebview la
    dejaría en tamaño normal)."""
    ctypes.windll.user32.ShowWindow(_hwnd(), _SW_RESTORE)


def _maximizada():
    try:
        return bool(ctypes.windll.user32.IsZoomed(_hwnd()))
    except Exception:
        return False


def mostrar(url_fresca, log):
    """Desde el icono de la bandeja: trae la ventana al frente, restaurándola si estaba minimizada; si estaba oculta, carga
    el panel fresco y la coloca junto al cursor. No la oculta nunca: para eso está la X (como en Slack o Discord), y
    un conmutador ocultaba sin que se notara una ventana tapada por otras. `url_fresca` es una función
    (costbar.url_panel): el ticket solo se pide al cargar.

    Orden al mostrar una ventana oculta: show() -> _visible=True -> move().
    - show() antes que move(): move() espera a que el evento "shown" se haya disparado alguna vez.
    - _visible=True justo tras show(): si move() lanzara una excepción, la ventana ya está visible en pantalla."""
    global _visible
    with _lock:
        try:
            if not _visible:
                url = url_fresca()
                if not url.startswith("http://127.0.0.1:"):    # solo el panel local: otra URL podría levantar el servidor de pywebview
                    raise ValueError("URL del panel inesperada")
                _ventana.load_url(url)
            _ventana.show()                                   # la trae al frente
            if _minimizada():                                 # también si se ocultó estando minimizada (cerrada desde la barra de tareas)
                _restaurar()
            if not _visible:
                _visible = True
                if not _maximizada():
                    _ventana.move(*_posicion())
        except Exception as e:
            log(f"bandeja: ERROR ventana {type(e).__name__}: {e}")


def cerrar(log):
    """Para _accion_salir: destruye la ventana de verdad (termina webview.start(), bloqueante en el hilo principal); el
    manejador de cierre la deja pasar porque _saliendo ya está puesto."""
    global _saliendo, _salida_pedida
    _saliendo = _salida_pedida = True
    with _lock:
        if _ventana is None:
            return
        try:
            _ventana.destroy()
        except Exception as e:
            log(f"bandeja: ERROR al cerrar la ventana {type(e).__name__}: {e}")
