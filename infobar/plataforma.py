"""Lo que cambia de un sistema a otro: dónde van los datos de la aplicación y qué rutas no son de fiar.

Los datos medidos (la base SQLite, sus copias) viven en el directorio estándar de datos de aplicación de
cada sistema, separados del código y de la configuración:
  macOS    ~/Library/Application Support/CostGuard
  Windows  %LOCALAPPDATA%\\CostGuard
  Linux    $XDG_DATA_HOME/costguard  (o ~/.local/share/costguard)
`COSTGUARD_DATOS` lo sustituye (tests, instalaciones a medida).
"""
import hashlib
import os
import subprocess
import sys

NUBE = {"icloud", "icloud drive", "mobile documents", "dropbox", "onedrive", "google drive", "googledrive", "box sync",
        "pcloud", "pcloud drive", "nextcloud", "cloudstorage"}
PREFIJOS_NUBE = ("onedrive-", "dropbox (", "google drive (", "googledrive-", "nextcloud-")


def dir_datos():
    forzado = os.environ.get("COSTGUARD_DATOS")
    if forzado:
        return os.path.abspath(forzado)
    home = os.path.expanduser("~")
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local"), "CostGuard")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "CostGuard")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share"), "costguard")


def ruta_no_fiable(ruta):
    """Motivo (texto) si la ruta está en una carpeta sincronizada o de red, donde SQLite en WAL puede corromperse
    o la base acabar copiada a un tercero; "" si parece un disco local. Compara componentes enteros de la ruta
    (no subcadenas): un usuario "box" o una carpeta "dropbox-clone" no son nube. No ve volúmenes de red montados
    con un nombre cualquiera: ahí manda la comprobación de WAL al abrir."""
    real = os.path.realpath(ruta)
    if real.startswith("\\\\") or real.replace("\\", "/").startswith("//"):
        return "recurso de red"
    partes = [p.lower() for p in real.replace("\\", "/").split("/") if p]
    for p in partes:
        if p in NUBE or p.startswith(PREFIJOS_NUBE):
            return f"carpeta sincronizada ({p})"
    if partes[:1] in (["net"],) or partes[:2] == ["mnt", "nfs"]:
        return "recurso de red"
    return ""


def es_checkout_de_desarrollo(raiz):
    """Un checkout (no la instalación en ~/.cost-guard) no escribe en los datos reales sin COSTGUARD_DATOS."""
    return os.path.basename(raiz.rstrip("/\\")) != ".cost-guard" and not os.environ.get("COSTGUARD_DATOS")


def id_fuente(ruta, base):
    """Id estable y portable de una fuente: hash de la ruta RELATIVA a `base` (nunca la ruta absoluta con el usuario)."""
    try:
        rel = os.path.relpath(os.path.realpath(ruta), os.path.realpath(base)).replace("\\", "/")
    except ValueError:                                  # Windows: otra unidad
        rel = os.path.realpath(ruta).replace("\\", "/")
    return hashlib.sha256(rel.encode("utf-8", "replace")).hexdigest()[:32]


def volumen_sin_propietarios(ruta, salida_mount=None):
    """True si la ruta está en un volumen montado sin propiedad de ficheros (macOS `noowners`, p. ej. un disco externo): ahí el
    0600 de la base no impide que otro usuario del equipo la lea. Solo informa; no impide nada. `salida_mount` es para tests."""
    if sys.platform != "darwin" and salida_mount is None:
        return False
    try:
        texto = salida_mount if salida_mount is not None else subprocess.run(
            ["mount"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    real, mejor = os.path.realpath(ruta), ("", False)
    for linea in texto.splitlines():
        if " on " not in linea or " (" not in linea:
            continue
        punto = linea.split(" on ", 1)[1].rsplit(" (", 1)[0]
        opciones = linea.rsplit(" (", 1)[1]
        if (real == punto or real.startswith(punto.rstrip("/") + "/") or punto == "/") and len(punto) >= len(mejor[0]):
            mejor = (punto, "noowners" in opciones)
    return mejor[1]


def avisar(titulo, texto):
    """Notificación del sistema. macOS con osascript; en otros sistemas todavía nada (devuelve False y el aviso solo es visual).
    Los dos textos los pone la aplicación, nunca vienen de los logs; aun así se escapan."""
    if sys.platform != "darwin":
        return False
    def esc(t):
        return str(t).replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.run(["osascript", "-e", f'display notification "{esc(texto)}" with title "{esc(titulo)}"'],
                       capture_output=True, timeout=5, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------- procesos y cerrojos portables

def grupo_nuevo_kwargs():
    """Argumentos de Popen para que el proceso hijo y sus hijos se puedan matar juntos: sesión nueva en POSIX, grupo nuevo en Windows."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def matar_arbol(proc):
    """Mata a `proc` y a todo lo que haya lanzado. No falla si ya terminó."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.kill()
        except OSError:
            pass
        return
    import signal
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


class bloqueo_de_fichero:
    """Cerrojo exclusivo entre procesos sobre un fichero abierto: `flock` en POSIX, `msvcrt.locking` en Windows (que no bloquea para siempre:
    se reintenta hasta `espera_s` y luego falla con TimeoutError, en vez de colgar la barra)."""
    def __init__(self, fichero, espera_s=30.0):
        self.f, self.espera_s = fichero, espera_s

    def __enter__(self):
        if os.name == "nt":
            import msvcrt, time
            self.f.seek(0)
            fin = time.monotonic() + self.espera_s
            while True:
                try:
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                    return self
                except OSError:
                    if time.monotonic() > fin:
                        raise TimeoutError("no se pudo tomar el cerrojo a tiempo") from None
                    time.sleep(0.05)
        import fcntl
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        if os.name == "nt":
            import msvcrt
            try:
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return
        import fcntl
        fcntl.flock(self.f, fcntl.LOCK_UN)


# ---------------------------------------------------------------- copias del sistema y datos sensibles

def excluir_de_copias_del_sistema(ruta):
    """Pide al sistema que no copie `ruta` en sus copias automáticas ni la indexe. macOS: exclusión de Time Machine (por ruta, se queda en el
    xattr). Windows: atributo «no indexar» (el Historial de archivos y OneDrive no cubren %LOCALAPPDATA% por defecto; la copia soportada es la
    cifrada de la aplicación). Devuelve True si quedó hecho; False si no se pudo o no aplica."""
    if not os.path.isdir(ruta):
        return False
    try:
        if sys.platform == "darwin":
            import time
            for intento in range(3):         # tmutil tarda un instante en reflejar el cambio: se verifica y se reintenta
                r = subprocess.run(["tmutil", "isexcluded", ruta], capture_output=True, text=True, timeout=10)
                if "[Excluded]" in r.stdout:
                    return True
                if subprocess.run(["tmutil", "addexclusion", ruta], capture_output=True, timeout=10).returncode != 0:
                    return False
                time.sleep(0.3)
            return False
        if os.name == "nt":
            import ctypes
            FILE_ATTRIBUTE_NOT_CONTENT_INDEXED = 0x2000
            atributos = ctypes.windll.kernel32.GetFileAttributesW(ruta)
            if atributos == -1:
                return False
            return bool(ctypes.windll.kernel32.SetFileAttributesW(ruta, atributos | FILE_ATTRIBUTE_NOT_CONTENT_INDEXED))
    except (OSError, subprocess.SubprocessError, AttributeError):
        return False
    return False
