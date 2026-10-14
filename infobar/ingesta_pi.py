"""Extractor de pi (~/.pi/agent/sessions/<cwd-slug>/*.jsonl) para la ingesta.

Una cabecera `{type: session, id, cwd}` y una línea por mensaje; los turnos son mensajes `assistant` con
`message.usage = {input, output, cacheRead, cacheWrite, reasoning, cost}`. `reasoning` NO va dentro de `output`: se suma a la
salida. Cada línea lleva un `id` único (medido: ni uno repetido entre los 22 ficheros reales) y las ramas/forks copian las
líneas con el mismo id, así que la clave es `pi:<id de línea>` y los forks se deduplican solos. Cada turno lleva su `provider`
(nodeclub, anthropic, opencode-go...): una misma sesión puede cambiar de proveedor a mitad.
"""
import json
import os
import re

from .ingesta import a_epoch_utc, proyecto_de_slug, texto_seguro

AGENTE = "pi"
_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
MAX_CABECERA = 64 * 1024


def _slug(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def contexto_de_fichero(ruta):
    """Proyecto de la cabecera (`cwd`) o, si falta, de la carpeta de la sesión (que lleva el cwd convertido a texto plano)."""
    ctx = {"proyecto": proyecto_de_slug(os.path.basename(os.path.dirname(ruta))) or "otros"}
    try:
        with open(ruta, "rb") as f:
            for linea in f.read(MAX_CABECERA).split(b"\n")[:20]:
                if b'"session"' in linea:
                    d = json.loads(linea)
                    if isinstance(d, dict) and d.get("type") == "session":
                        _cabecera(ctx, d)
                        break
    except (OSError, ValueError):
        pass
    return ctx


def _cabecera(ctx, d):
    cwd = texto_seguro(d.get("cwd"), 1000)
    if cwd:
        ctx["proyecto"] = proyecto_de_slug(_slug(cwd))


def _n(v):
    return 0 if v is None else v


def _suma(a, b):
    return a + b if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b)) else a if not isinstance(a, (int, float)) else b


def extractor(d, ctx):
    """Turno (dict) de un mensaje `assistant` con uso, None si la línea no lo lleva, o ("anomalia", texto)."""
    if d.get("type") == "session":
        _cabecera(ctx, d)
        return None
    m = d.get("message")
    if not (isinstance(m, dict) and m.get("role") == "assistant" and isinstance(m.get("usage"), dict)):
        return None
    uso, linea_id = m["usage"], d.get("id")
    if not (isinstance(linea_id, str) and _ID.match(linea_id)):
        return ("anomalia", "mensaje sin id válido")
    hora = a_epoch_utc(d.get("timestamp") if d.get("timestamp") is not None else m.get("timestamp"))
    if hora is None:
        return ("anomalia", "marca de tiempo sin zona o ilegible")
    crudo = m.get("model")
    if crudo is not None and not texto_seguro(crudo, 120):
        return ("anomalia", "nombre de modelo no válido")
    proveedor = m.get("provider")
    if proveedor is not None and not texto_seguro(proveedor, 120):
        return ("anomalia", "nombre de proveedor no válido")
    salida = _suma(_n(uso.get("output")), _n(uso.get("reasoning")))
    return {"clave": f"{AGENTE}:{linea_id}", "hora_utc": hora, "agente": AGENTE, "proveedor": (proveedor or "sin-proveedor").strip() or "sin-proveedor",
            "modelo": crudo or "sin-modelo", "gi": _n(uso.get("input")), "go": salida, "cr": _n(uso.get("cacheRead")),
            "cw": _n(uso.get("cacheWrite")), "proyecto_raw": ctx.get("proyecto") or "otros"}
