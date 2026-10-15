#!/usr/bin/env python3
"""bandeja_windows: icono en la bandeja de sistema de Windows, con la cifra de la ventana de 5 h.

Equivalente Windows de barra.py (macOS, AppKit) / costbar.py -> CostBar (rumps): mismo motor
(costbar.escanear(), costbar.url_panel(), costbar.servir(), costbar.endurecer()), pero con
pystray (en Windows: ctypes puro, sin pywin32) y un menu de texto plano en vez del desplegable
"glass" con PNG de macOS (fuera de alcance de esta fase, ver plan).

El hilo principal lo ocupa pystray (icon.run(), bloqueante); el refresco cada 60 s va en un
hilo daemon aparte (_bucle_refresco), igual que el rumps.Timer(60) de CostBar en macOS.

Uso:
    python bandeja_windows.py
"""
import sys

if sys.platform != "win32":
    sys.exit("bandeja_windows.py es solo para Windows. En macOS usa: python barra.py")

import os          # noqa: E402  (tras la guarda de plataforma, a proposito)
import threading   # noqa: E402
import time         # noqa: E402
import webbrowser  # noqa: E402

try:
    import pystray
    from PIL import Image
except ImportError as e:
    sys.exit(f"Falta una dependencia de la bandeja de Windows ({e}). Instala con: "
             "pip install pystray==0.19.5 pillow>=10.0")

import costbar  # noqa: E402
from infobar import bandeja_windows_menu as bwm  # noqa: E402

REFRESCO_S = 60
# logs/ ya esta fuera de git (.gitignore): aqui no se acumula un PNG suelto en el repo
RUTA_ICONO = os.path.join(os.path.dirname(costbar.rutas.LOG), "icono_bandeja_windows.png")

_icon = None  # referencia global: la necesitan el bucle de refresco y las acciones del menu
_lock_refresco = threading.Lock()  # _refrescar() la llaman tanto el hilo de refresco cada 60s
                                    # como el click de "Refrescar ahora" (hilo de pystray): sin
                                    # esto, un escritor y un lector podrian pisarse en RUTA_ICONO


def _imagen_para(r):
    """PIL.Image del icono actual (barra de progreso + cifra de 5 h): infobar/formato_e_icono
    (icono_png, color_magnitud), ya cross-platform, reusado tal cual via la fachada costbar."""
    os.makedirs(os.path.dirname(RUTA_ICONO), exist_ok=True)
    fraccion, alerta = bwm.fraccion_e_icono(r)
    ruta = costbar.icono_png(RUTA_ICONO, fraccion, bwm.titulo_icono(r),
                             costbar.color_magnitud(r["cinco_h"]["tok"]), alerta)
    return Image.open(ruta)


def _construir_menu(r):
    """Texto plano: 5h/hoy/ritmo (solo informativos) + separador + acciones."""
    return pystray.Menu(
        pystray.MenuItem(bwm.texto_5h(r), None, enabled=False),
        pystray.MenuItem(bwm.texto_hoy(r), None, enabled=False),
        pystray.MenuItem(bwm.texto_ritmo(r), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Abrir panel", _accion_abrir_panel),
        pystray.MenuItem("Refrescar ahora", _accion_refrescar),
        pystray.MenuItem("Salir", _accion_salir),
    )


def _refrescar():
    """Recoge el estado actual y repinta icono + menu. Llamable desde cualquier hilo (con lock:
    el hilo de refresco y el click de "Refrescar ahora" pueden solaparse sobre el mismo PNG)."""
    with _lock_refresco:
        r = costbar.escanear()
        if _icon is not None:
            _icon.icon = _imagen_para(r)
            _icon.menu = _construir_menu(r)
        costbar.log(f"bandeja: refresco ok, 5h {bwm.titulo_icono(r)} tok, hoy {r['hoy']['turnos']} turnos")
        return r


def _accion_abrir_panel(icon):
    try:
        webbrowser.open(costbar.url_panel())
    except Exception as e:
        costbar.log(f"bandeja: ERROR abrir panel {type(e).__name__}: {e}")


def _accion_refrescar(icon):
    try:
        _refrescar()
    except Exception as e:
        costbar.log(f"bandeja: ERROR refrescar-ahora {type(e).__name__}: {e}")


def _accion_salir(icon):
    icon.stop()


def _bucle_refresco():
    """Cada 60 s, en un hilo daemon aparte: una excepcion no atrapada no debe matar el hilo ni
    dejar el icono congelado (mismo patron defensivo que CostBar.refrescar en macOS)."""
    while True:
        time.sleep(REFRESCO_S)
        try:
            _refrescar()
        except Exception as e:
            costbar.log(f"bandeja: ERROR bucle de refresco {type(e).__name__}: {e}")


def main():
    global _icon
    costbar.endurecer()       # umask / permisos de lo que ya hubiera: todo esto es sensible
    costbar.servir()          # panel + configurador, en su propio hilo
    r = costbar.escanear()
    _icon = pystray.Icon("CostGuard", _imagen_para(r), "CostGuard", _construir_menu(r))
    threading.Thread(target=_bucle_refresco, name="bandeja-refresco", daemon=True).start()
    costbar.log("bandeja: icono listo, entrando en el bucle")
    _icon.run()               # bloqueante en el hilo principal (patron recomendado en Windows)


if __name__ == "__main__":
    main()
