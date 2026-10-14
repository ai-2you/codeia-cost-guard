"""Claves (NaN, Dokploy, Easypanel) en el almacén de secretos del sistema, servicio "infobar".

Cuentas: "nan", "prov:<proveedor>" (las que se ponen desde Ajustes), "dokploy:<host del panel>", "easypanel:<host del panel>". Nunca se guardan en
config.json ni en el repo. En macOS el almacén es el Llavero (`security -i` recibe la orden por stdin, así que el valor no aparece en `ps`);
en Windows, el Administrador de credenciales (API `Cred*` por ctypes: el valor tampoco pasa por línea de comandos). En otros sistemas no hay
almacén y las funciones lo dicen en vez de guardar en claro.
"""
import ctypes
import getpass
import os
import re
import shutil
import subprocess

SERVICIO = "infobar"
MAX_CLAVE = 1024          # el Administrador de credenciales admite 2560 bytes; una clave de API real queda muy por debajo
_CUENTA_VALIDA = re.compile(r"^(nan|prov:[a-z0-9-]{1,40}|(dokploy|easypanel):[A-Za-z0-9.-]+(:\d+)?)$")


class _Llavero:
    """Llavero de macOS, por el comando `security`."""
    def disponible(self):
        return shutil.which("security") is not None

    def leer(self, cuenta):
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICIO, "-a", cuenta, "-w"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""

    def guardar(self, cuenta, valor):
        orden = f'add-generic-password -U -s {SERVICIO} -a {cuenta} -w "{valor}"\n'
        r = subprocess.run(["security", "-i"], input=orden, capture_output=True, text=True, timeout=10)
        if r.returncode != 0 or r.stderr.strip():
            raise RuntimeError(f"el Llavero no la guardó (código {r.returncode})")   # sin stderr: podría repetir la orden con la clave

    def borrar(self, cuenta):
        subprocess.run(["security", "delete-generic-password", "-s", SERVICIO, "-a", cuenta], capture_output=True, timeout=5)


_ERROR_NO_ENCONTRADA = 1168
_TIPO_GENERICA, _PERSISTE_EN_ESTE_EQUIPO = 1, 2      # LOCAL_MACHINE: no viaja con el perfil de itinerancia


class _CREDENCIAL(ctypes.Structure):
    """CREDENTIALW de wincred.h, con los tipos de ctypes (no hace falta Windows para definirla)."""
    _fields_ = [("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32), ("TargetName", ctypes.c_wchar_p),
                ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_uint64), ("CredentialBlobSize", ctypes.c_uint32),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", ctypes.c_uint32),
                ("AttributeCount", ctypes.c_uint32), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]


class _AdminCredenciales:
    """Administrador de credenciales de Windows. `api` es advapi32 (inyectable en las pruebas)."""
    def __init__(self, api=None):
        self._api = api

    def _a(self):
        if self._api is None:
            a = ctypes.WinDLL("advapi32", use_last_error=True)
            ptr = ctypes.POINTER(_CREDENCIAL)
            a.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ptr)]
            a.CredWriteW.argtypes = [ctypes.POINTER(_CREDENCIAL), ctypes.c_uint32]
            a.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
            a.CredFree.argtypes = [ctypes.c_void_p]
            for f in (a.CredReadW, a.CredWriteW, a.CredDeleteW):
                f.restype = ctypes.c_int
            self._api = a
        return self._api

    @staticmethod
    def _objetivo(cuenta):
        return f"{SERVICIO}:{cuenta}"

    def disponible(self):
        return os.name == "nt"

    def leer(self, cuenta):
        a, p = self._a(), ctypes.POINTER(_CREDENCIAL)()
        if not a.CredReadW(self._objetivo(cuenta), _TIPO_GENERICA, 0, ctypes.byref(p)):
            return ""
        try:
            c = p.contents
            return bytes(c.CredentialBlob[:c.CredentialBlobSize]).decode("utf-8", "replace")
        finally:
            a.CredFree(p)

    def guardar(self, cuenta, valor):
        datos = valor.encode("utf-8")
        blob = (ctypes.c_ubyte * len(datos)).from_buffer_copy(datos)
        c = _CREDENCIAL(Type=_TIPO_GENERICA, TargetName=self._objetivo(cuenta), CredentialBlobSize=len(datos),
                        CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)), Persist=_PERSISTE_EN_ESTE_EQUIPO,
                        UserName=cuenta)
        if not self._a().CredWriteW(ctypes.byref(c), 0):
            raise RuntimeError(f"el Administrador de credenciales no la guardó (código {ctypes.get_last_error()})")

    def borrar(self, cuenta):
        self._a().CredDeleteW(self._objetivo(cuenta), _TIPO_GENERICA, 0)     # si no estaba, devuelve error y da igual


def _backend():
    """El almacén de este sistema, o None si no hay."""
    b = _AdminCredenciales() if os.name == "nt" else _Llavero()
    return b if b.disponible() else None


def hay_llavero():
    return _backend() is not None


def cuenta_valida(cuenta):
    return bool(_CUENTA_VALIDA.fullmatch(cuenta or ""))


def leer_clave(cuenta):
    """La clave de esa cuenta, o "" si no hay almacén o no está guardada."""
    b = _backend()
    if not (b and cuenta_valida(cuenta)):
        return ""
    try:
        return b.leer(cuenta)
    except Exception:
        return ""


def guardar_clave(cuenta, valor=None):
    """Guarda (o reemplaza) la clave. Sin `valor`, la pide sin eco por terminal."""
    b = _backend()
    if b is None:
        raise RuntimeError("sin almacén de secretos en este sistema (Llavero de macOS o Administrador de credenciales de Windows)")
    if not cuenta_valida(cuenta):
        raise ValueError("cuenta no válida: usa nan, prov:<proveedor>, dokploy:<host> o easypanel:<host>")
    if valor is None:
        valor = getpass.getpass(f"Clave para {cuenta} (no se muestra): ")
    valor = (valor or "").strip()
    # una clave de API no lleva comillas ni barras: se rechazan en todos los sistemas (`security -i` las interpretaría) para que
    # una clave guardada en uno sirva en los demás, y así no hace falta escapar nada
    if not valor or len(valor) > MAX_CLAVE or not re.fullmatch(r"[\x21-\x7e]+", valor) or '"' in valor or "\\" in valor:
        raise ValueError("clave vacía, demasiado larga o con caracteres no admitidos (espacios, comillas, barras o no ASCII)")
    b.guardar(cuenta, valor)
    return True


def borrar_clave(cuenta):
    """Quita la clave de esa cuenta (si no estaba, no pasa nada)."""
    b = _backend()
    if b and cuenta_valida(cuenta):
        b.borrar(cuenta)
