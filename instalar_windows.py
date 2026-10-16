#!/usr/bin/env python3
"""instalar_windows.py: instala codeia-cost-guard en Windows (bandeja + autoarranque).

Equivalente Windows de install.sh. Copia el motor, infobar/ y panel_assets/ a ~/.cost-guard (mismo destino
que macOS/Linux), crea un entorno virtual propio con PAQUETES (icono de bandeja, popover nativo del panel con
WebView2 y copias cifradas), registra el arranque al iniciar sesion con la clave Run del usuario (sin
administrador) y arranca la bandeja sin ventana de consola (pythonw.exe). Quita la tarea programada o el .vbs
de Inicio que dejaban las versiones anteriores de este instalador.

Uso:
    python instalar_windows.py
"""
import os
import shutil
import subprocess
import sys

if sys.platform != "win32":
    sys.exit("instalar_windows.py es solo para Windows. En macOS/Linux usa: ./install.sh")

AQUI = os.path.dirname(os.path.abspath(__file__))
DESTINO = os.environ.get("COSTGUARD_HOME") or os.path.join(os.path.expanduser("~"), ".cost-guard")
NOMBRE_TAREA = "CostGuardTray"

# barra.py/popover.py son exclusivos de macOS (AppKit): aqui no pintan nada, el equivalente es
# bandeja_windows.py (pystray) + infobar/bandeja_windows_menu.py, que se copian via infobar/*.py
FICHEROS_RAIZ = ("costbar.py", "bandeja_windows.py", "analizar_gasto.py", "panel.py",
                  "precio_astra.py", "instalar_reglas_globales.py", "GUIA.md", "AGENTS-snippet.md")
PAQUETES = ("pillow", "cryptography==46.0.7", "pystray==0.19.5", "pywebview==6.2.1", "pythonnet==3.2.1")


def copiar_ficheros(destino):
    """Copia lo mismo que install.sh: el motor, infobar/ (con los scripts de infobar/remoto que el alta
    instala en los servidores) y panel_assets/. config.json no se pisa si ya existe (trae tus cuotas y notas
    por proveedor); servidores.json tampoco se copia nunca (es solo tuyo, fuera de git)."""
    os.makedirs(os.path.join(destino, "logs"), exist_ok=True)
    for nombre in FICHEROS_RAIZ:
        origen = os.path.join(AQUI, nombre)
        if os.path.isfile(origen):
            shutil.copy2(origen, os.path.join(destino, nombre))

    destino_infobar = os.path.join(destino, "infobar")
    os.makedirs(destino_infobar, exist_ok=True)
    origen_infobar = os.path.join(AQUI, "infobar")
    for nombre in os.listdir(origen_infobar):
        if nombre.endswith(".py"):
            shutil.copy2(os.path.join(origen_infobar, nombre), os.path.join(destino_infobar, nombre))

    destino_remoto = os.path.join(destino_infobar, "remoto")
    os.makedirs(destino_remoto, exist_ok=True)
    for nombre in os.listdir(os.path.join(origen_infobar, "remoto")):
        if nombre.endswith(".sh"):
            shutil.copy2(os.path.join(origen_infobar, "remoto", nombre), os.path.join(destino_remoto, nombre))

    # panel.py lee sus plantillas de aqui (plantilla.html/estilos.css/app.js/graficos.js/ajustes.js);
    # sin esto, abrir el panel desde la bandeja fallaba con FileNotFoundError (500 silencioso).
    destino_assets = os.path.join(destino, "panel_assets")
    os.makedirs(destino_assets, exist_ok=True)
    for nombre in os.listdir(os.path.join(AQUI, "panel_assets")):
        if nombre.endswith((".css", ".js", ".html")):
            shutil.copy2(os.path.join(AQUI, "panel_assets", nombre), os.path.join(destino_assets, nombre))

    config_destino = os.path.join(destino, "config.json")
    if not os.path.isfile(config_destino):
        config_origen = os.path.join(AQUI, "config.json")
        if not os.path.isfile(config_origen):
            config_origen = os.path.join(AQUI, "config.example.json")
        if os.path.isfile(config_origen):
            shutil.copy2(config_origen, config_destino)
    estado = os.path.join(destino, "state.json")
    if not os.path.isfile(estado):
        with open(estado, "w", encoding="utf-8") as f:
            f.write("{}")


def _correr(cmd):
    """subprocess.run sin salir del proceso: devuelve (ok, salida). encoding="oem" decodifica la
    salida de binarios de consola de Windows (schtasks.exe, pip) con el codepage real en vez de
    reventar con UnicodeDecodeError en maquinas con codepage distinto de utf-8."""
    try:
        r = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="oem")
        return True, (r.stdout or "").strip()
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or e.stdout or "").strip()
    except FileNotFoundError as e:
        return False, str(e)


def _ejecutar(cmd, error_msg):
    """Como _correr pero para pasos sin fallback (venv, pip): si falla, error explicito y sale."""
    ok, salida = _correr(cmd)
    if not ok:
        sys.exit(f"{error_msg}\n{salida}")
    return salida


def crear_venv_e_instalar(destino):
    """Entorno virtual propio en destino/venv con PAQUETES: pillow y pystray (icono de bandeja; backend Windows
    100% ctypes, sin pywin32), pywebview y pythonnet (popover nativo del panel) y cryptography (copias cifradas,
    la misma versión que install.sh). Reejecutar es idempotente: si el venv ya existe no se recrea, solo se
    reinstalan/confirman los paquetes."""
    venv_dir = os.path.join(destino, "venv")
    python_venv = os.path.join(venv_dir, "Scripts", "python.exe")
    if not os.path.isfile(python_venv):
        _ejecutar([sys.executable, "-m", "venv", venv_dir],
                  "No se pudo crear el entorno virtual en " + venv_dir)
    _ejecutar([python_venv, "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
              "No se pudo actualizar pip en el entorno virtual")
    _ejecutar([python_venv, "-m", "pip", "install", "--quiet", *PAQUETES],
              "No se pudo instalar " + ", ".join(PAQUETES) + " en el entorno virtual")
    return venv_dir


RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOMBRE_RUN = "CostGuard"


def registrar_arranque(pythonw, script):
    """Arranque al iniciar sesion con la clave Run del propio usuario (HKCU): no pide administrador y, a diferencia de una
    tarea de `schtasks /sc onlogon`, no hereda condiciones que la impiden con bateria o la matan a las 72 h. El valor es
    una linea de ordenes: el interprete y el script van entre comillas (las rutas de Windows no pueden llevar comillas)."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN) as k:
        winreg.SetValueEx(k, NOMBRE_RUN, 0, winreg.REG_SZ, f'"{pythonw}" "{script}"')


def ruta_vbs_startup():
    carpeta_inicio = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                                   "Start Menu", "Programs", "Startup")
    return os.path.join(carpeta_inicio, f"{NOMBRE_TAREA}.vbs")


def quitar_arranques_antiguos():
    """Las versiones anteriores del instalador usaban una tarea programada o un .vbs en Inicio: si se quedan junto a la
    clave Run, cada inicio de sesion lanza dos bandejas (la segunda se va sola por el mutex, pero no hace falta)."""
    _correr(["schtasks", "/delete", "/tn", NOMBRE_TAREA, "/f"])       # si no existe, falla y da igual
    try:
        os.remove(ruta_vbs_startup())
    except OSError:
        pass


def arrancar(pythonw, script):
    """Arranca la bandeja ya, sin consola y desligada del instalador. Si ya habia una en marcha, la nueva se va sola."""
    try:
        subprocess.Popen([pythonw, script], close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        return True
    except OSError as e:
        print(f"  aviso: no se pudo arrancar ahora ({e}); arrancara en el siguiente inicio de sesion.")
        return False


def main():
    print(f"-> Copiando a {DESTINO}")
    copiar_ficheros(DESTINO)

    print("-> Entorno virtual + " + ", ".join(p.split("==")[0] for p in PAQUETES))
    venv_dir = crear_venv_e_instalar(DESTINO)

    pythonw = os.path.join(venv_dir, "Scripts", "pythonw.exe")
    script = os.path.join(DESTINO, "bandeja_windows.py")
    print("-> Arranque automatico al iniciar sesion (clave Run de tu usuario)")
    quitar_arranques_antiguos()
    registrar_arranque(pythonw, script)
    arrancada = arrancar(pythonw, script)

    print("")
    print("Listo: mira la bandeja del sistema (icono con un anillo; las cifras, al pasar el raton)."
          if arrancada else "Listo: el icono aparecera en el siguiente inicio de sesion.")
    print("  Si ya tenias una version anterior en marcha, sal de ella (clic derecho > Salir) y vuelve a abrirla.")
    print(f'  Arrancar:      "{pythonw}" "{script}"')
    print(f'  Desinstalar:   reg delete "HKCU\\{RUN}" /v {NOMBRE_RUN} /f')
    print("")
    print(f"Panel: python \"{os.path.join(DESTINO, 'panel.py')}\"  (genera panel.html)")


if __name__ == "__main__":
    main()
