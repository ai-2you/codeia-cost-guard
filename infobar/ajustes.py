"""Ajustes editables desde el panel: normalizacion de lo que envia la pestaña Ajustes y alias/ocultacion
de proyectos. El guardado (fusion atomica con copia) y las claves (Llavero) viven en infobar/.

Sin dependencias: solo libreria estandar."""
import re

# proveedores cuyo consumo se sabe leer por API (hoy solo NaN: GET /v1/usage)
API_SOPORTADAS = {"nan"}


def slug(nombre):
    return re.sub(r"[^a-z0-9]+", "-", str(nombre).lower()).strip("-")[:40] or "sin-nombre"


def cuenta_de(nombre):
    """Cuenta del Llavero de un proveedor: 'nan' para NaN (la lee el lector de cuota) y prov:<slug> para el resto."""
    s = slug(nombre)
    return "nan" if s == "nan" else f"prov:{s}"


def api_soportada(prov):
    return slug(prov.get("nombre", "")) in API_SOPORTADAS or str(prov.get("uso_api") or "").lower() in API_SOPORTADAS


def nombre_proyecto(raw, config):
    """(nombre a mostrar, oculto) segun config['proyectos'][raw] = {alias, oculto}."""
    p = (config.get("proyectos") or {}).get(raw) or {}
    return (str(p.get("alias") or "").strip() or raw), bool(p.get("oculto"))


def _entero(v, campo):
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{campo}: no es un numero")
    if n < 0:
        raise ValueError(f"{campo}: no puede ser negativo")
    return n


def _lista_txt(v, campo):
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError(f"{campo}: tiene que ser una lista de textos")
    return [x.strip() for x in v if x.strip()]


# campos de primer nivel que la UI manda tal cual: los valida errores_config (servidores, ips) o son numeros simples
PASAN = ("servidores", "ips_conocidas", "intervalo_normal_s", "intervalo_incidencia_s")


def normalizar(nuevo, actual):
    """El parche (solo las claves de primer nivel que edita la UI, ya saneadas) para guardar_config.

    `actual` es config.json tal cual esta en disco (no cfg(): este mezcla valores por defecto que no
    deben acabar en el repo). Lo que la UI no conoce (tipo, nota, fuente de cada proveedor...) se
    conserva desde `actual`; una clave en claro nunca se acepta."""
    if not isinstance(nuevo, dict):
        raise ValueError("la configuracion no es un objeto")
    for k in ("proveedores", "aliases"):
        if k not in nuevo:
            raise ValueError(f"falta el campo {k}")
    out = {}
    if "limite_hora_tokens" in nuevo:
        n = _entero(nuevo["limite_hora_tokens"], "limite_hora_tokens")
        if "limite_hora_tokens" in actual or n != 8_000_000:        # 8 M es el valor por defecto de cfg()
            out["limite_hora_tokens"] = n
    if "ignorar" in nuevo:
        ign = _lista_txt(nuevo["ignorar"], "ignorar")
        if "ignorar" in actual or ign != ["synthetic", "<synthetic>", "sin-modelo"]:
            out["ignorar"] = ign
    al = nuevo["aliases"]
    if not isinstance(al, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in al.items()):
        raise ValueError("aliases: tiene que ser {origen: plan}")
    out["aliases"] = {k: v.strip() for k, v in al.items() if v.strip()}

    previos = {p.get("nombre"): p for p in actual.get("proveedores") or []}
    provs, vistos = [], set()
    for p in nuevo["proveedores"]:
        nombre = str((p or {}).get("nombre") or "").strip()
        if not nombre or nombre in vistos:
            raise ValueError("cada proveedor necesita un nombre distinto")
        vistos.add(nombre)
        q = dict(previos.get(nombre) or {})
        q["nombre"] = nombre
        q["modelos"] = _lista_txt(p.get("modelos") or [], f"{nombre}.modelos")
        for c in ("limite_5h_tokens", "limite_semana_tokens", "limite_mes_tokens"):
            n = _entero(p.get(c, q.get(c)), f"{nombre}.{c}")
            if n or c in q:                      # no ensucia config.json con ceros que nadie puso
                q[c] = n
        if "uso_api" in p:
            u = str(p["uso_api"] or "").lower()
            if u and u not in API_SOPORTADAS:
                raise ValueError(f"{nombre}.uso_api: '{u}' no esta soportado")
            q.pop("uso_api", None)
            if u:
                q["uso_api"] = u
        if "cuotas_mes" in p:
            cm = p["cuotas_mes"]
            if not isinstance(cm, dict):
                raise ValueError(f"{nombre}.cuotas_mes: tiene que ser {{modelo: tokens}}")
            cm = {str(m).strip(): _entero(v, f"{nombre}.cuotas_mes") for m, v in cm.items() if str(m).strip()}
            if cm or "cuotas_mes" in q:
                q["cuotas_mes"] = cm
        provs.append(q)
    out["proveedores"] = provs

    if "proyectos" in nuevo:
        py = nuevo["proyectos"]
        if not isinstance(py, dict):
            raise ValueError("proyectos: tiene que ser {proyecto: {...}}")
        res = {}
        for k, v in py.items():
            v = v or {}
            e = dict((actual.get("proyectos") or {}).get(k) or {})      # conserva campos puestos a mano
            e.update({"alias": str(v.get("alias") or "").strip(), "oculto": bool(v.get("oculto")),
                      "presupuesto_mes_tokens": _entero(v.get("presupuesto_mes_tokens"), f"proyectos.{k}")})
            if e["alias"] or e["oculto"] or e["presupuesto_mes_tokens"]:
                res[str(k)] = e
        if res or "proyectos" in actual:
            out["proyectos"] = res
    for k in PASAN:
        if k in nuevo:
            out[k] = nuevo[k]
    return out
