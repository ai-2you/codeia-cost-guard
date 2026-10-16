#!/usr/bin/env python3
"""bandeja_windows: icono en la bandeja de sistema de Windows, con la cifra de la ventana de 5 h.

Equivalente Windows de barra.py (macOS, AppKit): arranca los mismos hilos (costbar.arrancar_hilos():
panel local, trabajador de agentes y servidores, ingesta y copias) y pinta lo que el trabajador entrega, con pystray
(ctypes puro en Windows, sin pywin32) para el icono y un popover nativo (infobar/
bandeja_windows_popover.py, pywebview/WebView2) que carga el panel SIN navegador -- lo mas
parecido al desplegable "glass" de macOS que se puede conseguir sin ese PNG renderizado por
Chrome (fuera de alcance, ver plan). Si pywebview no esta instalado, o si crear/arrancar la
ventana falla por cualquier motivo (runtime de WebView2 ausente/roto, por ejemplo), cae a abrir
el panel con Edge en modo --app (sin pestanas ni barra de direcciones) y, si tampoco hay un
navegador Chromium, al navegador por defecto a secas.

Arquitectura de hilos: pywebview.start() EXIGE correr en el hilo principal (asi lo dice su propia
documentacion), asi que aqui el hilo principal lo ocupa webview.start() y pystray corre con
icon.run_detached() (pensado justo para integrarse con el bucle de otra libreria): spawnea su
propio hilo internamente. El trabajador escanea cada minuto y llama a _entregar desde su hilo.

Uso:
    python bandeja_windows.py
"""
import sys

if sys.platform != "win32":
    sys.exit("bandeja_windows.py es solo para Windows. En macOS usa: python barra.py")

import os           # noqa: E402  (tras la guarda de plataforma, a proposito)
import subprocess   # noqa: E402
import threading    # noqa: E402
import webbrowser   # noqa: E402
import winreg        # noqa: E402  (solo Windows: ya pasamos la guarda de arriba)

try:
    import pystray
except ImportError as e:
    sys.exit(f"Falta una dependencia de la bandeja de Windows ({e}). Instala con: "
             "pip install pystray==0.19.5 pillow>=10.0")

import costbar  # noqa: E402
from infobar import bandeja_windows_menu as bwm  # noqa: E402
from infobar import bandeja_windows_popover as popover  # noqa: E402

_icon = None  # referencia global: la necesitan _entregar y las acciones del menu
_usa_popover = False   # True solo si el popover nativo se creo y arranco sin errores (ver main())
_hilos = []            # los de costbar.arrancar_hilos(): el trabajador recibe "Refrescar ahora" y todos paran al salir
_agentes = {"cinco_h": 0, "hoy": 0, "turnos": 0, "pct_5h": 0, "ritmo": 0, "limite_5h": 0, "limite_hora": 0}
_lock_icono = threading.Lock()   # _entregar (hilo trabajador) y las acciones (hilo de pystray) repintan el icono


def _construir_menu(a):
    """Texto plano: 5h/hoy/ritmo (solo informativos) + separador + acciones.

    "Abrir panel" lleva default=True: pystray en Windows llama al item default con el CLIC
    IZQUIERDO sobre el icono (ver pystray._win32._on_notify), ademas de poder elegirse desde el
    menu con clic derecho como cualquier otro item."""
    return pystray.Menu(
        pystray.MenuItem(bwm.texto_5h(a), None, enabled=False),
        pystray.MenuItem(bwm.texto_hoy(a), None, enabled=False),
        pystray.MenuItem(bwm.texto_ritmo(a), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Abrir panel", _accion_panel, default=True),
        pystray.MenuItem("Refrescar ahora", _accion_refrescar),
        pystray.MenuItem("Salir", _accion_salir),
    )


def _pinta():
    with _lock_icono:
        if _icon is not None:
            _icon.icon = bwm.imagen_icono_windows(*bwm.fraccion_e_icono(_agentes))
            _icon.menu = _construir_menu(_agentes)
            _icon.title = bwm.texto_tooltip(_agentes)


def _entregar(tipo, dato):
    """Lo llama el hilo trabajador (como en barra.py). Solo interesa el resumen de agentes; una excepcion no debe llegar al trabajador."""
    global _agentes
    if tipo != "agentes":
        return
    try:
        _agentes = {**_agentes, **dict(dato)}
        _pinta()
    except Exception as e:
        costbar.log(f"bandeja: ERROR al pintar el icono {type(e).__name__}: {e}")


VENTANA_PANEL = "440,760"  # ancho,alto en px del navegador en modo --app (fallback sin popover):
                            # mas grande que popover.ANCHO/ALTO (420x720) porque aqui SI cuenta el
                            # chrome propio de la ventana del navegador (bordes, barra de titulo).

_CLAVES_NAVEGADOR = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
)


def _ruta_navegador_app():
    """Ruta de msedge.exe (preferido, siempre viene con Windows) o chrome.exe, resuelta via el
    registro "App Paths": el mismo mecanismo que usa Windows para resolver el nombre corto en
    Ejecutar/cmd, mas fiable que adivinar Program Files (cambia entre instalaciones de 32/64/ARM64
    bits). None si no se encuentra ninguno de los dos (solo navegadores Chromium soportan --app)."""
    for clave in _CLAVES_NAVEGADOR:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, clave) as k:
                ruta = winreg.QueryValueEx(k, None)[0]
        except OSError:
            continue
        if ruta and os.path.isfile(ruta):
            return ruta
    return None


def _accion_abrir_panel_navegador(url):
    """Respaldo cuando no hay popover nativo (pywebview no instalado, o fallo al crearlo/arrancarlo
    -- ver main()): ventana de "aplicacion" de Edge/Chrome (sin pestanas ni barra de direcciones)
    y, si no hay ninguno de los dos, el navegador por defecto.

    Aqui el ticket de entrada va en la linea de comandos del navegador (en macOS se evita con NSWorkspace). En Windows esa
    linea solo la leen los procesos de tu usuario y los administradores, que ya pueden leer tus credenciales; y el ticket
    es de un solo uso y caduca a los 60 s. El popover nativo no tiene este problema: la URL no sale del proceso."""
    try:
        navegador = _ruta_navegador_app()
        if navegador:
            subprocess.Popen([navegador, f"--app={url}", f"--window-size={VENTANA_PANEL}"])
        else:
            webbrowser.open(url)
    except Exception as e:
        costbar.log(f"bandeja: ERROR abrir panel (navegador) {type(e).__name__}: {e}")


def _accion_panel(icon):
    if _usa_popover:
        popover.alternar(costbar.url_panel(), costbar.log)
    else:
        _accion_abrir_panel_navegador(costbar.url_panel())


def _accion_refrescar(icon):
    if _hilos:
        _hilos[0].pide("refrescar")       # el trabajador recoge todo ya y entrega el resumen nuevo


def _accion_salir(icon):
    icon.stop()
    for h in _hilos:                      # ingesta y copias cierran su base antes de salir
        if hasattr(h, "parar"):
            h.parar.set()
    for h in _hilos:
        if hasattr(h, "parar"):
            h.join(timeout=10)
    if _usa_popover:
        # termina webview.start() (hilo principal, bloqueante): sin esto el proceso quedaria
        # vivo en segundo plano aunque el icono ya hubiera desaparecido de la bandeja
        popover.cerrar(costbar.log)


def main():
    global _icon, _usa_popover
    costbar.endurecer()       # umask / permisos de lo que ya hubiera: todo esto es sensible
    _icon = pystray.Icon("CostGuard", bwm.imagen_icono_windows(*bwm.fraccion_e_icono(_agentes)),
                         bwm.texto_tooltip(_agentes), _construir_menu(_agentes))
    _hilos.extend(costbar.arrancar_hilos(_entregar))     # panel local, trabajador, ingesta y copias

    if popover.TIENE_WEBVIEW:
        try:
            popover.crear(costbar.url_panel())
            _usa_popover = True
        except Exception as e:
            # el paquete pywebview importa bien pero el runtime de WebView2 puede faltar o estar
            # roto (imagen de Windows recortada, politica de grupo, etc.): sin este try/except el
            # proceso moria aqui en silencio (pythonw.exe no tiene consola) y sin dejar ni rastro
            # en el log -- hallazgo de la revision adversarial.
            costbar.log(f"bandeja: ERROR no se pudo crear el popover nativo ({type(e).__name__}: "
                        f"{e}); cae al navegador para Abrir panel")
            _usa_popover = False

    if _usa_popover:
        popover.iniciar_vigilancia_foco(costbar.log)
        _icon.run_detached()      # spawnea su propio hilo: el principal lo necesita webview.start()
        costbar.log("bandeja: icono listo, entrando en el bucle (popover nativo)")
        try:
            import webview
            webview.start()       # bloqueante en el hilo principal (lo exige pywebview)
        except Exception as e:
            _usa_popover = False   # el bucle de webview ya no esta vivo: de aqui en mas, navegador
            costbar.log(f"bandeja: ERROR webview.start() {type(e).__name__}: {e}; "
                        "el icono sigue activo (pystray ya corre detached) pero sin popover, "
                        "Abrir panel cae al navegador")
    else:
        costbar.log("bandeja: icono listo, entrando en el bucle (sin popover: navegador para Abrir panel)")
        _icon.run()               # bloqueante en el hilo principal (patron recomendado en Windows)


if __name__ == "__main__":
    main()
