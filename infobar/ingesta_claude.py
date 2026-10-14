"""Extractor de Claude Code (~/.claude/projects/<proyecto>/<sesion>.jsonl) para la ingesta.

Claude Code escribe varias líneas por mensaje (una por bloque) con la misma entrada y caché y la salida creciendo:
aquí cada línea es el MISMO turno (clave = id del mensaje) y el almacén se queda con el máximo de cada contador, que
coincide con la última línea. No se filtra `isSidechain` (los subagentes consumen de verdad). Nunca `requestId`: a un
8 % de las líneas les falta.
"""
import re
import urllib.parse

from . import ingesta
from .ingesta import a_epoch_utc

AGENTE = "claude"
_ID_ANTHROPIC = re.compile(r"^msg_[0-9A-Za-z]{10,40}$")


def proyecto_de_ruta(ruta, usuario=None):
    """Nombre crudo del proyecto de un log de Claude Code, a partir de la carpeta `projects/<slug>` (criterio común en
    `ingesta.proyecto_de_slug`)."""
    plano = ruta.replace("\\", "/")
    if "/projects/" not in plano:
        return "otros"
    return ingesta.proyecto_de_slug(urllib.parse.unquote(plano.split("/projects/")[-1].split("/")[0]), usuario)


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
