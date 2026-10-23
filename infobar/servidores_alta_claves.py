"""Claves SSH personales del dueño para el alta guiada: qué claves privadas hay en ~/.ssh (solo NOMBRES de fichero, nunca su contenido),
cuál ha elegido (validada contra la lista real: nada de rutas arbitrarias ni `..`) y la línea para autorizar su clave PÚBLICA dentro del servidor.

La clave dedicada `infobar_ed25519` nunca sale aquí: es la de solo lectura (sin frase) y no debe autorizarse para un administrador."""
import base64
import binascii
import os
import re

DIR_SSH = os.path.expanduser("~/.ssh")
PERSONALES = ("id_ed25519", "id_ecdsa", "id_rsa")                  # las que ssh prueba por defecto, en este orden
TIPOS = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa")
_FICHERO = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._@+-]{0,99}")         # sin «/», sin «..» delante, sin espacios
_EXCLUIDOS = {"config", "authorized_keys", "authorized_keys2", "environment", "rc"}
_B64 = re.compile(r"[A-Za-z0-9+/]{60,1100}={0,2}")
# el comentario va dentro de comillas simples en una línea de shell: solo caracteres que no pueden cerrarlas ni expandirse
_COMENTARIO = re.compile(r"[A-Za-z0-9@._:+=,-]{1,100}")


def _cabecera_privada(ruta):
    try:
        with open(ruta, "rb") as f:
            primera = f.read(100).split(b"\n", 1)[0]
    except OSError:
        return False
    return primera.startswith(b"-----BEGIN ") and primera.rstrip().endswith(b"PRIVATE KEY-----")


def _tipo_y_base64(ruta_pub):
    """«tipo base64» del .pub (sin comentario), o None si no se puede leer."""
    try:
        with open(ruta_pub, encoding="ascii") as f:
            partes = f.read(16384).split()
    except (OSError, UnicodeDecodeError):
        return None
    return " ".join(partes[:2]) if len(partes) >= 2 else None


def _es_la_de_infobar(ruta_pub):
    """True si ese .pub es la clave dedicada de infobar copiada con otro nombre (mismo tipo y base64): nunca se ofrece para un administrador."""
    propia = _tipo_y_base64(ruta_pub)
    return propia is not None and propia == _tipo_y_base64(os.path.join(DIR_SSH, "infobar_ed25519.pub"))


def listar():
    """Nombres de las claves privadas de ~/.ssh: las que tienen su .pub al lado y las .pem/.key con cabecera de clave privada. Ordenados."""
    try:
        nombres = os.listdir(DIR_SSH)
    except OSError:
        return []
    salida = []
    for n in nombres:
        if (not _FICHERO.fullmatch(n) or n in _EXCLUIDOS or n.startswith(("known_hosts", "infobar_ed25519", "authorized_keys"))
                or n.endswith(".pub")):
            continue
        ruta = os.path.join(DIR_SSH, n)
        if not os.path.isfile(ruta):
            continue
        if os.path.isfile(ruta + ".pub"):
            if not _es_la_de_infobar(ruta + ".pub"):
                salida.append(n)
        elif n.endswith((".pem", ".key")) and _cabecera_privada(ruta):
            salida.append(n)
    return sorted(salida)


def ruta(nombre):
    """Ruta absoluta de una clave de la lista real, o None si el nombre no está en ella (o no es texto)."""
    if isinstance(nombre, str) and nombre in listar():
        return os.path.join(DIR_SSH, nombre)
    return None


def publica_valida(texto):
    """La clave pública OpenSSH normalizada («tipo base64 [comentario]»), o None si no es una ed25519/ecdsa-p256/rsa bien formada.
    El tipo declarado debe coincidir con el que lleva dentro el base64; un comentario con caracteres raros se descarta (la clave sigue valiendo)."""
    if not isinstance(texto, str):
        return None
    t = texto.strip()
    if not t or "\n" in t or "\r" in t:
        return None
    partes = t.split()
    if len(partes) < 2 or partes[0] not in TIPOS or not _B64.fullmatch(partes[1]):
        return None
    try:
        blob = base64.b64decode(partes[1], validate=True)
    except (binascii.Error, ValueError):
        return None
    largo = int.from_bytes(blob[:4], "big") if len(blob) >= 4 else -1
    if blob[4:4 + largo] != partes[0].encode():
        return None
    base = partes[0] + " " + partes[1]
    comentario = " ".join(partes[2:])
    return base + " " + comentario if _COMENTARIO.fullmatch(comentario) else base


def linea_autorizar(publica):
    """Línea para ejecutar DENTRO del servidor (como el usuario con el que entras): añade la clave a su authorized_keys sin duplicarla."""
    p = publica_valida(publica)
    if not p:
        return None
    k = "'" + p + "'"
    return (f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && (grep -qxF {k} ~/.ssh/authorized_keys 2>/dev/null || echo {k} >> ~/.ssh/authorized_keys)"
            " && chmod 600 ~/.ssh/authorized_keys")


def autorizar(nombre=None):
    """{"linea", "fichero"} con la clave pública de la clave elegida (su .pub) o, sin elegir, de la primera personal por defecto;
    {"linea": None, "motivo": "sin_pub_elegida" | "sin_clave"} si no hay ninguna válida."""
    candidatos = [nombre + ".pub"] if nombre else [p + ".pub" for p in PERSONALES]
    for f in candidatos:
        if not _FICHERO.fullmatch(f) or f.startswith("infobar_ed25519") or _es_la_de_infobar(os.path.join(DIR_SSH, f)):
            continue
        try:
            with open(os.path.join(DIR_SSH, f), encoding="ascii") as fh:
                linea = linea_autorizar(fh.read(16384))
        except (OSError, UnicodeDecodeError):
            continue
        if linea:
            return {"linea": linea, "fichero": f}
    return {"linea": None, "motivo": "sin_pub_elegida" if nombre else "sin_clave"}
