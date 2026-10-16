#!/usr/bin/env python3
"""instalar_windows.py: instala codeia-cost-guard en Windows (bandeja + autoarranque).

Equivalente Windows de install.sh (que hoy solo monta la parte de macOS, LaunchAgent). Copia
el motor + infobar/ a ~/.cost-guard (mismo destino que macOS/Linux), crea un entorno virtual
propio con pillow + pystray==0.19.5 (icono de bandeja) + pywebview==6.2.1 + pythonnet==3.2.1 (popover
nativo del panel, sin navegador), y registra una tarea en el Programador de Tareas de
Windows (schtasks /sc onlogon) que lanza bandeja_windows.py sin ventana de consola
(pythonw.exe) en cada inicio de sesion. No requiere permisos de administrador: una tarea
ONLOGON para el propio usuario en el equipo local se crea con los permisos del usuario actual
(confirmado en la documentacion oficial de Microsoft, ver informe de investigacion).

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


def registrar_tarea(destino, venv_dir):
    """Tarea ONLOGON para el propio usuario: no requiere administrador. Gotcha de quoting: /tr
    es UNA sola cadena que Windows vuelve a parsear como linea de comandos, asi que el
    interprete y el script van entre comillas dobles DENTRO de esa cadena -- no basta pasar una
    lista a subprocess y esperar que Windows la resuelva sola. /f fuerza sobrescritura: reinstalar
    una segunda vez no falla por "la tarea ya existe" (idempotente).

    Si el Programador de Tareas rechaza el trigger ONLOGON (visto en la practica: algunas
    cuentas/politicas devuelven "Acceso denegado" solo para ONLOGON, aunque ONCE/DAILY funcionen
    con el mismo usuario), cae al fallback ya previsto en el plan: un .vbs en shell:startup.
    Devuelve "schtasks" o "startup_vbs" segun cual haya quedado activo."""
    pythonw = os.path.join(venv_dir, "Scripts", "pythonw.exe")
    script = os.path.join(destino, "bandeja_windows.py")
    comando_tr = f'"{pythonw}" "{script}"'
    ok, salida = _correr(
        ["schtasks", "/create", "/tn", NOMBRE_TAREA, "/tr", comando_tr, "/sc", "onlogon", "/f"])
    if ok:
        return "schtasks"
    print(f"  aviso: schtasks /create fallo ({salida or 'sin detalle'}); uso el fallback de "
          "Inicio (carpeta Startup) en su lugar.")
    _registrar_fallback_startup(pythonw, script)
    return "startup_vbs"


def ruta_vbs_startup():
    carpeta_inicio = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                                   "Start Menu", "Programs", "Startup")
    return os.path.join(carpeta_inicio, f"{NOMBRE_TAREA}.vbs")


def _registrar_fallback_startup(pythonw, script):
    """Equivalente mas directo al LaunchAgent de macOS: Explorer ejecuta siempre este .vbs al
    iniciar sesion de este usuario, sin privilegios especiales. Chr(34) evita tener que escapar
    comillas dobles dentro del propio literal de cadena de VBS."""
    ruta_vbs = ruta_vbs_startup()
    os.makedirs(os.path.dirname(ruta_vbs), exist_ok=True)
    contenido = (
        "' Generado por instalar_windows.py: arranca bandeja_windows.py oculto al iniciar sesion.\n"
        "' Fallback cuando el Programador de Tareas (schtasks /sc onlogon) no esta disponible.\n"
        'Set objShell = CreateObject("WScript.Shell")\n'
        f'objShell.Run Chr(34) & "{pythonw}" & Chr(34) & " " & Chr(34) & "{script}" & Chr(34), 0, False\n'
    )
    with open(ruta_vbs, "w", encoding="utf-8") as f:
        f.write(contenido)


def main():
    print(f"-> Copiando a {DESTINO}")
    copiar_ficheros(DESTINO)

    print("-> Entorno virtual + " + ", ".join(p.split("==")[0] for p in PAQUETES))
    venv_dir = crear_venv_e_instalar(DESTINO)

    print("-> Registrando arranque automatico (Programador de tareas)")
    metodo = registrar_tarea(DESTINO, venv_dir)

    pythonw = os.path.join(venv_dir, "Scripts", "pythonw.exe")
    script = os.path.join(DESTINO, "bandeja_windows.py")
    print("")
    print("Listo: en el siguiente inicio de sesion arrancara solo el icono de la bandeja.")
    if metodo == "schtasks":
        print(f"  Mecanismo activo: tarea \"{NOMBRE_TAREA}\" en el Programador de tareas.")
        print(f"  Verificar:     schtasks /query /tn {NOMBRE_TAREA} /v /fo list")
        print(f"  Desinstalar:   schtasks /delete /tn {NOMBRE_TAREA} /f")
    else:
        print("  Mecanismo activo: fallback de Inicio (el Programador de tareas rechazo")
        print("  el trigger ONLOGON en esta cuenta/maquina).")
        print(f"  Verificar:     dir \"{ruta_vbs_startup()}\"")
        print(f"  Desinstalar:   del \"{ruta_vbs_startup()}\"")
    print(f'  Arrancar ya:   "{pythonw}" "{script}"')
    print("")
    print(f"Panel: python \"{os.path.join(DESTINO, 'panel.py')}\"  (genera panel.html)")


if __name__ == "__main__":
    main()
