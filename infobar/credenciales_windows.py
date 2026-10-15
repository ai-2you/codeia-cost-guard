"""Claves (NaN, Dokploy, Easypanel) en el Credential Manager de Windows, servicio "infobar".

Mismo contrato que infobar/claves.py (hay_llavero, cuenta_valida, leer_clave, guardar_clave,
borrar_clave), implementado via ctypes puro contra advapi32.dll (CredWriteW/CredReadW/
CredDeleteW/CredFree). Sin dependencias nuevas.

TargetName en el Credential Manager = "infobar:<cuenta>" (mismo patron servicio+cuenta que
macOS). cuenta_valida se reutiliza de claves.py sin duplicar el regex (DRY): es una regex pura
sin dependencia de SO.

Referencia auditada para contrastar la implementacion (estructura CREDENTIALW, encoding del
blob, gestion de memoria): enthought/pywin32-ctypes, modulo win32ctypes/pywin32/win32cred.py
(solo lectura de codigo, no se instala como dependencia).
"""
import ctypes
import getpass
import re
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168

_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)

# argtypes/restype explicitos: si no, ctypes asume "int" de retorno y puede truncar
# punteros en 64-bit (gotcha documentado en el informe de investigacion).
_advapi32.CredWriteW.argtypes = [_PCREDENTIALW, wintypes.DWORD]
_advapi32.CredWriteW.restype = wintypes.BOOL
_advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(_PCREDENTIALW)]
_advapi32.CredReadW.restype = wintypes.BOOL
_advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
_advapi32.CredDeleteW.restype = wintypes.BOOL
_advapi32.CredFree.argtypes = [ctypes.c_void_p]
_advapi32.CredFree.restype = None


def _blob(valor):
    """Construye el buffer UTF-16 del secreto y su tamano en bytes sin el nulo final.

    Se asigna a una variable local (no inline) para que el GC no libere el buffer antes de
    que CredWriteW lo lea (gotcha de ciclo de vida documentado en el informe).
    """
    buffer = ctypes.create_unicode_buffer(valor)
    tamano = ctypes.sizeof(buffer) - ctypes.sizeof(ctypes.c_wchar)
    return buffer, tamano


def hay_llavero():
    return True  # este modulo solo se carga via dispatch cuando sys.platform == "win32"


def leer_clave(cuenta):
    """La clave de esa cuenta, o "" si no esta guardada o la cuenta no es valida."""
    if not cuenta_valida(cuenta):
        return ""
    target = f"{SERVICIO}:{cuenta}"
    pcred = _PCREDENTIALW()
    if not _advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        return ""  # no encontrado u otro error: mismo comportamiento que macOS (devolver "")
    try:
        cred = pcred.contents
        tamano = cred.CredentialBlobSize
        if not cred.CredentialBlob or tamano <= 0:
            return ""
        datos = ctypes.string_at(cred.CredentialBlob, tamano)
        return datos.decode("utf-16-le")
    finally:
        _advapi32.CredFree(pcred)


def guardar_clave(cuenta, valor=None):
    """Guarda (o reemplaza) la clave. Sin `valor`, la pide sin eco por terminal."""
    if not cuenta_valida(cuenta):
        raise ValueError("cuenta no valida: usa nan, prov:<proveedor>, dokploy:<host> o easypanel:<host>")
    if valor is None:
        valor = getpass.getpass(f"Clave para {cuenta} (no se muestra): ")
    valor = (valor or "").strip()
    # mismas validaciones que macOS: una clave de API no lleva espacios, comillas ni barras
    if not valor or not re.fullmatch(r"[\x21-\x7e]+", valor) or '"' in valor or "\\" in valor:
        raise ValueError("clave vacia o con caracteres no admitidos (espacios, comillas o barras)")
    target = f"{SERVICIO}:{cuenta}"
    buffer, tamano = _blob(valor)
    cred = _CREDENTIALW()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.Comment = None
    cred.CredentialBlobSize = tamano
    cred.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = None
    if not _advapi32.CredWriteW(ctypes.byref(cred), 0):
        # get_last_error() (no GetLastError()/WinError() sin args): con use_last_error=True en el
        # WinDLL, ctypes guarda el error justo tras la llamada C, sin que otra llamada Win32
        # intermedia (incluso interna de CPython) lo pise antes de leerlo.
        error = ctypes.get_last_error()
        raise RuntimeError(f"el Credential Manager no la guardo: {ctypes.WinError(error)}")
    return True


def borrar_clave(cuenta):
    """Quita la clave de esa cuenta (si no estaba, no pasa nada)."""
    if not cuenta_valida(cuenta):
        return
    target = f"{SERVICIO}:{cuenta}"
    if not _advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        error = ctypes.get_last_error()
        if error != ERROR_NOT_FOUND:
            raise RuntimeError(f"el Credential Manager no la borro: {ctypes.WinError(error)}")


# Import deliberadamente al FINAL del fichero (no arriba): claves.py importa este modulo desde
# su bloque de dispatch (import circular intencionado). Si este import estuviera arriba y algo
# importa credenciales_windows ANTES que claves, Python empezaria a ejecutar claves.py a mitad de
# cargar este modulo, llegaria a su propio bloque de dispatch y encontraria este modulo todavia a
# medias (sin hay_llavero/leer_clave/etc. definidas aun) -> AttributeError. Puesto aqui abajo, para
# cuando se resuelve este import ya estan definidas todas las funciones de arriba, en cualquier
# orden de importacion.
from .claves import SERVICIO, cuenta_valida  # noqa: E402  (reexportadas: mismo contrato, sin duplicar)
