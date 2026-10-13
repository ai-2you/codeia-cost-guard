"""Planes/proveedores, alias, CCS (Claude Code Switch) y tarifas del equivalente API."""
import collections, glob, json, os, re, time

from . import rutas
from .config_y_estado import cfg


# tarifas USD/1M: (entrada, salida, cache_leida, escritura_cache) — solo para el equivalente API
TARIFAS = {
    "claude-opus-5": (5, 25, .50, 6.25), "claude-sonnet-5": (3, 15, .30, 3.75),
    "claude-fable-5": (10, 50, 1, 12.50), "claude-haiku": (1, 5, .10, 1.25),
    "claude-opus-4": (15, 75, 1.5, 18.75), "claude-sonnet-4": (3, 15, .30, 3.75),
    "gpt-5.6-astra": (10, 50, 1, 12.50), "gpt-5.6-sol": (5, 30, .50, 6.25),
    "gpt-5.6-terra": (2.5, 15, .25, 3.125), "gpt-5.6-luna": (1, 6, .10, 1.25),
    "gpt-5.6": (5, 30, .50, 6.25),
}


CLIFF = 272_000


def corto(m):
    """claude-sonnet-5 -> sonnet-5 · gpt-5.6-terra -> terra · claude-haiku-4-5-20251001 -> haiku-4-5"""
    m = re.sub(r"-\d{8}$", "", (m or "?"))
    m = m.split("/")[-1]                      # anthropic/claude-sonnet-5 -> claude-sonnet-5
    return m.replace("claude-", "").replace("gpt-5.6-", "")


PROVEEDORES_DEFECTO = [
    {"nombre": "Claude", "modelos": ["claude"]},
    {"nombre": "OpenAI", "modelos": ["gpt", "o3", "o4", "codex"]},
]


ALIAS_DEFECTO = {
    "zai-coding-plan": "z.ai (coding plan)", "zai": "z.ai (coding plan)", "z-ai": "z.ai (coding plan)",
    "opencode-go": "OpenCode Go", "opencode": "OpenCode Go",
    "nan.builders": "NaN", "nan": "NaN", "openrouter": "OpenRouter",
    "nodeclub": "nodeclub.ai",
    "anthropic": "Claude (Max)", "openai": "OpenAI (Codex)",
    "codex": "OpenAI (Codex)", "google": "Google", "gemini": "Google",
}


def alias_de(fuente):
    """zai-coding-plan -> z.ai (coding plan). Los nombres mas largos primero, para que
    'opencode-go' no lo capture 'opencode'."""
    if not fuente:
        return None
    al = dict(ALIAS_DEFECTO)
    al.update(cfg().get("aliases") or {})
    f = str(fuente).lower()
    for k in sorted(al, key=len, reverse=True):
        if k.lower() in f:
            return al[k]
    return None


NOMBRE_URL = (("cheaperinference", "CheaperInference"), ("nan.builders", "NaN"), ("openrouter", "OpenRouter"),
              ("z.ai", "z.ai"), ("zhipu", "z.ai"), ("bigmodel", "z.ai"), ("nodeclub", "nodeclub.ai"),
              ("opencode", "OpenCode Go"), ("openai", "OpenAI (Codex)"), ("anthropic", "Claude (Max)"),
              ("deepseek", "DeepSeek"), ("chutes", "Chutes"), ("together", "Together"), ("groq", "Groq"))


def nombre_de_url(url, perfil=""):
    u = (url or "").lower()
    for trozo, nombre in NOMBRE_URL:
        if trozo in u:
            return nombre
    return alias_de(perfil) or perfil or "sin identificar"


def linea_ccs():
    """Linea de tiempo de CCS (Claude Code Switch): que proveedor tenia activo y cuando.

    CCS deja un settings.json por cada cambio en ~/.ccs/backups/<perfil>.<fecha>.settings.json,
    y ahi dentro va ANTHROPIC_BASE_URL. Con eso cada sesion de Claude se atribuye a su proveedor real.
    """
    traza = []
    for b in glob.glob(f"{rutas.HOME}/.ccs/backups/*.settings.json"):
        m = re.match(r"^([^.]+)\.(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})", os.path.basename(b))
        if not m:
            continue
        perfil = m.group(1)
        try:
            cuando = time.mktime(time.strptime(f"{m.group(2)} {m.group(3)}:{m.group(4)}:{m.group(5)}", "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
        url = ""
        try:
            d = json.load(open(b, encoding="utf-8"))
            url = ((d.get("env") or {}).get("ANTHROPIC_BASE_URL") or "")
        except Exception:
            pass
        traza.append((cuando, nombre_de_url(url, perfil), perfil, url))
    # el perfil activo ahora mismo
    ahora = f"{rutas.HOME}/.claude/settings.json"
    if os.path.exists(ahora):
        try:
            d = json.load(open(ahora, encoding="utf-8"))
            url = ((d.get("env") or {}).get("ANTHROPIC_BASE_URL") or "")
            traza.append((os.path.getmtime(ahora), nombre_de_url(url, "ahora"), "ahora", url))
        except Exception:
            pass
    return sorted(traza)


_TRAZA_CCS = None


def plan_ccs(ts):
    """Proveedor activo en CCS en ese momento (epoch)."""
    global _TRAZA_CCS
    if _TRAZA_CCS is None:
        _TRAZA_CCS = linea_ccs()
    anterior = None
    for cuando, nombre, perfil, url in _TRAZA_CCS:
        if cuando <= (ts or 0):
            anterior = (cuando, nombre, perfil, url)
        else:
            break
    return anterior[1] if anterior else None


def familia_de(m):
    """glm5.3-flash -> glm · qwen3.8-27b -> qwen · <synthetic> -> synthetic · sin-modelo -> otros"""
    m = re.sub(r"[^a-z0-9_.-]", "", corto(m or "").lower())
    g = re.match(r"[a-z]+", m)
    fam = g.group(0) if g else "otros"
    return "otros" if fam in ("sin", "the", "") else fam


def proveedores_de(c, modelos=()):
    """Proveedores declarados en config.json: nombre, patrones de modelo y limites (0 = sin declarar).

    Cada plan es una cuota distinta (y una suscripcion distinta), asi que se miden por separado.
    """
    lista = c.get("proveedores") if c.get("proveedores") else PROVEEDORES_DEFECTO
    salida = []
    for p in lista:
        if not isinstance(p, dict):
            continue
        salida.append({"nombre": str(p.get("nombre") or "?"),
                       "modelos": [str(m).lower() for m in (p.get("modelos") or [])],
                       "limite_5h_tokens": p.get("limite_5h_tokens") or 0,
                       "limite_semana_tokens": p.get("limite_semana_tokens") or 0,
                       "cuotas_mes": {str(k).lower(): (v or 0) for k, v in (p.get("cuotas_mes") or {}).items()}})
    # lo que no este declarado en config.json aparece solo, agrupado por familia de modelo
    auto = collections.defaultdict(set)
    for m in modelos:
        mm = (m or "").lower()
        if not mm or mm == "sin-modelo":
            continue
        if any(pat and pat in mm for p in salida for pat in p["modelos"]):
            continue
        auto[familia_de(m)].add(corto(m))
    for fam, ms in sorted(auto.items()):
        salida.append({"nombre": fam, "modelos": sorted(ms), "auto": True,
                       "limite_5h_tokens": 0, "limite_semana_tokens": 0, "cuotas_mes": {}})
    return salida


NATIVOS = {"Claude (Max)": ("claude", "sonnet", "opus", "haiku", "fable"),
           "OpenAI (Codex)": ("gpt", "o3", "o4", "codex")}


def modelo_nativo(plan, modelo):
    """¿Este modelo es de la casa de ese plan? GLM usado desde Claude Code NO es de Claude."""
    patrones = NATIVOS.get(plan)
    if not patrones:
        return True
    m = (modelo or "").lower()
    return any(p in m for p in patrones)


def plan_final(nombre, declarados):
    """Evita duplicados tontos: 'Openrouter' y 'OpenRouter' son el mismo plan."""
    for d in (declarados or ()):
        if (d or "").strip().lower() == (nombre or "").strip().lower():
            return d
    return nombre


def proveedor_de(modelo, provs, plan=None, fiable=True):
    """plan = proveedor real segun el log (providerID de OpenCode, URL base de Hermes...).

    Orden: origen fiable del log -> alias del nombre de modelo -> patrones de config -> familia.
    """
    m = (modelo or "").lower()
    if plan and fiable:
        return alias_de(plan) or plan
    al = dict(ALIAS_DEFECTO)
    al.update(cfg().get("aliases") or {})
    for k in sorted(al, key=len, reverse=True):
        if k.lower() in m:
            return al[k]
    for p in provs:
        if any(pat and pat in m for pat in p["modelos"]):
            return p["nombre"]
    if plan:
        return alias_de(plan) or plan
    for p in provs:
        if any(pat and pat in m for pat in p["modelos"]):
            return p["nombre"]
    return "otros"


def tarifa(m):
    m = (m or "").lower()
    for k, v in TARIFAS.items():
        if k in m:
            return v
    return None
