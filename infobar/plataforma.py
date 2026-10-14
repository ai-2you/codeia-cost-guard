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
