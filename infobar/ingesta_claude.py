"""Extractor de Claude Code (~/.claude/projects/<proyecto>/<sesion>.jsonl) para la ingesta.

Claude Code escribe varias líneas por mensaje (una por bloque) con la misma entrada y caché y la salida creciendo:
aquí cada línea es el MISMO turno (clave = id del mensaje) y el almacén se queda con el máximo de cada contador, que
coincide con la última línea. No se filtra `isSidechain` (los subagentes consumen de verdad). Nunca `requestId`: a un
8 % de las líneas les falta.
"""
import getpass
import os
import re
import urllib.parse

from .ingesta import a_epoch_utc

AGENTE = "claude"
_ID_ANTHROPIC = re.compile(r"^msg_[0-9A-Za-z]{10,40}$")


def _trozos_usuario():
    try:
        usuario = getpass.getuser()
    except Exception:                                  # noqa: BLE001
        usuario = os.path.basename(os.path.expanduser("~"))
    return [x.lower() for x in re.split(r"[^A-Za-z0-9]+", usuario) if x]


def proyecto_de_ruta(ruta, usuario=None):
    """Nombre crudo del proyecto, igual que el que usa hoy el panel (los 3 últimos trozos de la carpeta de proyecto) pero sin el
    prefijo `Users/<usuario>`, `home/<usuario>` ni `C:/Users/<usuario>`, que llevaría el nombre del dueño del Mac a la base.
    La carpeta de Claude Code sustituye `/`, `.` y `-` por `-`, así que un usuario `jean-luc` ocupa dos trozos: se quita el
    nombre del usuario actual entero (si es otro, solo `Users/<un trozo>`)."""
    plano = ruta.replace("\\", "/")
    if "/projects/" not in plano:
        return "otros"
    carpeta = urllib.parse.unquote(plano.split("/projects/")[-1].split("/")[0])
    trozos = [x for x in carpeta.split("-") if x]
    if trozos and len(trozos[0]) == 1 and trozos[0].isalpha() and len(trozos) > 1 and trozos[1].lower() == "users":
        trozos = trozos[1:]                            # Windows: C--Users-nombre-...
    if len(trozos) >= 2 and trozos[0].lower() in ("users", "home"):
        mios = usuario if usuario is not None else _trozos_usuario()
        resto = [x.lower() for x in trozos[1:1 + len(mios)]]
        trozos = trozos[1 + len(mios):] if mios and resto == mios else trozos[2:]
    nombre = "/".join(trozos[-3:]) if trozos else "otros"
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "_", nombre)[:200]


def contexto_de_fichero(ruta):
    return {"proyecto": proyecto_de_ruta(ruta), "modelo": None}


def _n(v):
    """Contador de uso: un `null` o un campo ausente cuentan como 0 (igual que el parser actual); lo demás pasa tal cual y la
    validación del almacén rechaza lo que no sea un entero."""
    return 0 if v is None else v


def _proveedor(modelo):
    """`anthropic` solo para modelos de Claude; para el resto se deja vacío y el plan se deduce al consultar (los backends
    compatibles sirven modelos de otros proveedores con el mismo formato de log)."""
    m = modelo.lower()
    return "anthropic" if m.startswith("claude") or any(x in m for x in ("opus", "sonnet", "haiku", "fable")) else ""


def _texto(v, maximo):
    return v if isinstance(v, str) and 0 < len(v) <= maximo else ""


def extractor(d, ctx):
    """Turno (dict) de una línea de Claude Code, None si no lleva uso, o ("anomalia", texto) si hay que anotarla."""
    m = d.get("message")
    if not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
        return None
    uso, mid = m["usage"], _texto(m.get("id"), 120)
    if not mid:
        return ("anomalia", "mensaje sin id")
    hora = a_epoch_utc(d.get("timestamp"))
    if hora is None:
        return ("anomalia", "marca de tiempo sin zona o ilegible")
    modelo = _texto(m.get("model"), 120) or ctx.get("modelo") or "sin-modelo"
    ctx["modelo"] = modelo
    if _ID_ANTHROPIC.match(mid):
        clave = f"{AGENTE}:{mid}"
    else:                                          # backends no Anthropic: ids predecibles que podrían chocar con los de otros agentes
        clave = f"{AGENTE}:{_texto(d.get('sessionId'), 80) or 'sin-sesion'}:{mid}"
    return {"clave": clave, "hora_utc": hora, "agente": AGENTE, "proveedor": _proveedor(modelo), "modelo": modelo,
            "gi": _n(uso.get("input_tokens")), "go": _n(uso.get("output_tokens")),
            "cr": _n(uso.get("cache_read_input_tokens")), "cw": _n(uso.get("cache_creation_input_tokens")),
            "proyecto_raw": ctx.get("proyecto", "")}
