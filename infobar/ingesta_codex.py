"""Extractor de Codex (~/.codex/sessions/AAAA/MM/DD/rollout-*.jsonl) para la ingesta.

Cada respuesta deja DOS registros (`token_usage_record` y `event_msg/token_count`) con el mismo uso: aquí solo se lee
`token_count`, que tiene el uso del turno (`last_token_usage`) y el acumulado de la sesión (`total_token_usage`); el
acumulado se IGNORA como cifra (sumarlo contaría cada turno muchas veces) y solo sirve para distinguir turnos. Clave:
`codex:<sesión>:<acumulado>:<huella del uso>`. En Codex `cached_input_tokens` va DENTRO de `input_tokens`: la entrada
fresca es input − cached. La sesión, el modelo (`turn_context`) y el proyecto (`cwd`) salen de líneas anteriores, por eso
el contexto se persiste entre pasadas (`lecturas.contexto`).
"""
import hashlib
import json
import os
import re

from .ingesta import a_epoch_utc, proyecto_de_slug

AGENTE = "codex"
_SESION = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_UUID_EN_NOMBRE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$")
MAX_CABECERA = 256 * 1024


def _slug(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _texto(v, maximo):
    return v if isinstance(v, str) and 0 < len(v) <= maximo else ""


def _desde_meta(ctx, p):
    sesion = _texto(p.get("session_id"), 64) or _texto(p.get("id"), 64)
    if _SESION.match(sesion):
        ctx["sesion"] = sesion
    proveedor = _texto(p.get("model_provider"), 120)
    if proveedor:
        ctx["proveedor"] = proveedor
    cwd = _texto(p.get("cwd"), 1000)
    if cwd:
        ctx["proyecto"] = proyecto_de_slug(_slug(cwd))


def contexto_de_fichero(ruta):
    """Sesión, proveedor y proyecto leídos de la cabecera (`session_meta`) del rollout; si no hay, la sesión sale del nombre."""
    ctx = {"proveedor": "openai", "proyecto": "codex/otros", "modelo": ""}
    m = _UUID_EN_NOMBRE.search(os.path.basename(ruta))
    if m:
        ctx["sesion"] = m.group(1).lower()
    try:
        with open(ruta, "rb") as f:
            cabecera = f.read(MAX_CABECERA)
        for linea in cabecera.split(b"\n")[:50]:
            if b'"session_meta"' in linea:
                d = json.loads(linea)
                if isinstance(d, dict) and d.get("type") == "session_meta" and isinstance(d.get("payload"), dict):
                    _desde_meta(ctx, d["payload"])
                    break
    except (OSError, ValueError):
        pass
    return ctx


def _huella(uso):
    return hashlib.sha1(json.dumps(uso, sort_keys=True, default=str).encode("utf-8", "replace")).hexdigest()[:8]


def _n(v):
    return 0 if v is None else v


def extractor(d, ctx):
    """Turno (dict) de un `token_count`, None si la línea no lleva uso, o ("anomalia", texto)."""
    tipo, p = d.get("type"), d.get("payload")
    if not isinstance(p, dict):
        return None
    if tipo == "session_meta":
        _desde_meta(ctx, p)
        return None
    if tipo == "turn_context":
        modelo = _texto(p.get("model"), 120)
        if modelo:
            ctx["modelo"] = modelo
        return None
    if tipo != "event_msg" or p.get("type") != "token_count":
        return None
    info = p.get("info")
    last = info.get("last_token_usage") if isinstance(info, dict) else None
    if not isinstance(last, dict):
        return None
    hora = a_epoch_utc(d.get("timestamp"))
    if hora is None:
        return ("anomalia", "marca de tiempo sin zona o ilegible")
    total = (info.get("total_token_usage") or {}).get("total_tokens") if isinstance(info.get("total_token_usage"), dict) else None
    sesion = ctx.get("sesion") or "sin-sesion"
    entrada, cached = _n(last.get("input_tokens")), _n(last.get("cached_input_tokens"))
    fresca = max(entrada - cached, 0) if all(isinstance(x, int) and not isinstance(x, bool) for x in (entrada, cached)) else entrada
    return {"clave": f"{AGENTE}:{sesion}:{total if isinstance(total, int) and not isinstance(total, bool) else hora}:{_huella(last)}",
            "hora_utc": hora, "agente": AGENTE, "proveedor": ctx.get("proveedor") or "openai", "modelo": ctx.get("modelo") or "sin-modelo",
            "gi": fresca, "go": _n(last.get("output_tokens")), "cr": cached, "cw": _n(last.get("cache_write_input_tokens")),
            "proyecto_raw": ctx.get("proyecto") or "codex/otros"}
