"""Extractor de Claude Code (~/.claude/projects/<proyecto>/<sesion>.jsonl) para la ingesta.

Claude Code escribe varias líneas por mensaje (una por bloque) con la misma entrada y caché y la salida creciendo:
aquí cada línea es el MISMO turno (clave = id del mensaje) y el almacén se queda con el máximo de cada contador, que
coincide con la última línea. No se filtra `isSidechain` (los subagentes consumen de verdad). Nunca `requestId`: a un
8 % de las líneas les falta.
"""
import os
import re
import urllib.parse

from .ingesta import a_epoch_utc

AGENTE = "claude"
_ID_ANTHROPIC = re.compile(r"^msg_[0-9A-Za-z]{10,40}$")


def proyecto_de_ruta(ruta):
    """Nombre crudo del proyecto, igual que el que usa hoy el panel (los 3 últimos trozos de la carpeta de proyecto) pero sin
    el prefijo `Users/<usuario>` ni `home/<usuario>`, que llevaría el nombre del dueño del Mac a la base."""
    if "/projects/" not in ruta.replace("\\", "/"):
        return "otros"
    carpeta = urllib.parse.unquote(ruta.replace("\\", "/").split("/projects/")[-1].split("/")[0])
    trozos = [x for x in carpeta.split("-") if x]
    if len(trozos) >= 2 and trozos[0].lower() in ("users", "home"):
        trozos = trozos[2:]
    return "/".join(trozos[-3:]) if trozos else "otros"


def contexto_de_fichero(ruta):
    return {"proyecto": proyecto_de_ruta(ruta), "modelo": None}


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
    return {"clave": clave, "hora_utc": hora, "agente": AGENTE, "proveedor": "anthropic", "modelo": modelo,
            "gi": uso.get("input_tokens", 0), "go": uso.get("output_tokens", 0),
            "cr": uso.get("cache_read_input_tokens", 0), "cw": uso.get("cache_creation_input_tokens", 0),
            "proyecto_raw": ctx.get("proyecto", "")}
