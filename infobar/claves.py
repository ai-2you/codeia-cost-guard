"""Claves (NaN, Dokploy, Easypanel) en el Llavero de macOS, servicio "infobar".

Cuentas: "nan", "dokploy:<host del panel>", "easypanel:<host del panel>". Nunca se guardan en
config.json ni en el repo. El valor tampoco pasa por la linea de comandos: `security -i` recibe la
orden por stdin, asi que no aparece en `ps` mientras se guarda.
"""
import getpass, re, shutil, subprocess

SERVICIO = "infobar"
_CUENTA_VALIDA = re.compile(r"^(nan|(dokploy|easypanel):[A-Za-z0-9.-]+(:\d+)?)$")


def hay_llavero():
    return shutil.which("security") is not None


def cuenta_valida(cuenta):
    return bool(_CUENTA_VALIDA.fullmatch(cuenta or ""))


def leer_clave(cuenta):
    """La clave de esa cuenta, o "" si no hay Llavero o no esta guardada."""
    if not (hay_llavero() and cuenta_valida(cuenta)):
        return ""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICIO, "-a", cuenta, "-w"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def guardar_clave(cuenta, valor=None):
    """Guarda (o reemplaza) la clave. Sin `valor`, la pide sin eco por terminal."""
    if not hay_llavero():
        raise RuntimeError("sin Llavero de macOS (comando 'security' no disponible)")
    if not cuenta_valida(cuenta):
        raise ValueError("cuenta no valida: usa nan, dokploy:<host> o easypanel:<host>")
    if valor is None:
        valor = getpass.getpass(f"Clave para {cuenta} (no se muestra): ")
    valor = (valor or "").strip()
    # security -i interpreta comillas y barras: una clave de API no las lleva, y asi no hay escape
    if not valor or not re.fullmatch(r"[\x21-\x7e]+", valor) or '"' in valor or "\\" in valor:
        raise ValueError("clave vacia o con caracteres no admitidos (espacios, comillas o barras)")
    orden = f'add-generic-password -U -s {SERVICIO} -a {cuenta} -w "{valor}"\n'
    r = subprocess.run(["security", "-i"], input=orden, capture_output=True, text=True, timeout=10)
    if r.returncode != 0 or r.stderr.strip():
        raise RuntimeError(f"el Llavero no la guardo: {r.stderr.strip()[:200]}")
    return True
