"""Extractor de Codex (~/.codex/sessions/AAAA/MM/DD/rollout-*.jsonl) para la ingesta.

Cada respuesta deja un `token_usage_record` con su uso (`usage`) y un `response_id` ÚNICO, seguido de uno o más
`event_msg/token_count`. Aquí solo se lee el `token_usage_record`: clave `codex:<response_id>`. Es la fuente buena porque
(medido sobre los rollouts reales) `token_count` repite eventos con el mismo acumulado en segundos distintos y, tras una
compactación, trae el uso a cero mientras el consumo real solo está en el registro. Los forks copian las líneas del padre
con los mismos `response_id`, así que también se deduplican solos. Un `token_count` sin ningún registro previo en el
fichero se anota como anomalía (no se cuenta). En Codex `cached_input_tokens` va DENTRO de `input_tokens`: la entrada
fresca es input − cached. El modelo (`turn_context`) y el proyecto (`cwd`) salen de líneas anteriores, por eso el
contexto se persiste entre pasadas (`lecturas.contexto`).
"""
import json
import re

from .ingesta import a_epoch_utc, proyecto_de_slug, texto_seguro

AGENTE = "codex"
MAX_CABECERA = 256 * 1024


def _slug(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _texto(v, maximo):
    return texto_seguro(v, maximo)


def _desde_meta(ctx, p):
    proveedor = _texto(p.get("model_provider"), 120)
    if proveedor:
        ctx["proveedor"] = proveedor
    cwd = _texto(p.get("cwd"), 1000)
    if cwd:
        ctx["proyecto"] = proyecto_de_slug(_slug(cwd))


def contexto_de_fichero(ruta):
    """Proveedor y proyecto leídos de la cabecera (`session_meta`) del rollout."""
    ctx = {"proveedor": "openai", "proyecto": "codex/otros", "modelo": ""}
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


_RESPUESTA = re.compile(r"^[A-Za-z0-9_-]{8,100}$")


def _n(v):
    return 0 if v is None else v


def extractor(d, ctx):
    """Turno (dict) de un `token_usage_record`, None si la línea no lleva uso, o ("anomalia", texto)."""
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
    if tipo == "event_msg" and p.get("type") == "token_count":
        if not ctx.get("registros"):
            return ("anomalia", "token_count sin token_usage_record previo")
        return None
    if tipo != "token_usage_record":
        return None
    ctx["registros"] = "1"
    uso = p.get("usage")
    if not isinstance(uso, dict):
        return None
    respuesta = p.get("response_id")
    if not (isinstance(respuesta, str) and _RESPUESTA.match(respuesta)):
        return ("anomalia", "registro de uso sin response_id válido")
    hora = a_epoch_utc(d.get("timestamp"))
    if hora is None:
        return ("anomalia", "marca de tiempo sin zona o ilegible")
    entrada, cached = _n(uso.get("input_tokens")), _n(uso.get("cached_input_tokens"))
    fresca = max(entrada - cached, 0) if all(isinstance(x, int) and not isinstance(x, bool) for x in (entrada, cached)) else entrada
    return {"clave": f"{AGENTE}:{respuesta}", "hora_utc": hora, "agente": AGENTE, "proveedor": ctx.get("proveedor") or "openai",
            "modelo": ctx.get("modelo") or "sin-modelo", "gi": fresca, "go": _n(uso.get("output_tokens")), "cr": cached,
            "cw": _n(uso.get("cache_write_input_tokens")), "proyecto_raw": ctx.get("proyecto") or "codex/otros"}
