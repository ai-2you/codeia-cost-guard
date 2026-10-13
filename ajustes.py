"""Ajustes editables desde el panel: claves en el Keychain, validacion de config.json,
alias/ocultacion de proyectos y las comprobaciones de seguridad del servidor local.

Sin dependencias: solo libreria estandar y el binario `security` de macOS."""
import re, subprocess, sys

SERVICIO = "costguard"
# proveedores cuyo consumo se sabe leer por API (hoy solo NaN: GET /v1/usage)
API_SOPORTADAS = {"nan"}
TEMAS = ("claro", "oscuro", "sistema")
_CLAVE_OK = re.compile(r"^[A-Za-z0-9._~+/=:-]{8,512}$")


def slug(nombre):
    return re.sub(r"[^a-z0-9]+", "-", str(nombre).lower()).strip("-") or "sin-nombre"


def clave_de_api(config, api):
    """Clave guardada en el Keychain del proveedor que lee esa API (p. ej. api='nan')."""
    for p in config.get("proveedores") or []:
        if slug(p.get("nombre", "")) == api or str(p.get("uso_api") or "").lower() == api:
            v = leer_clave(p["nombre"])
            if v:
                return v
    return ""


def escribir_config(ruta, obj):
    """Escritura atomica: un lector que coincida con el guardado ve el fichero viejo o el nuevo, nunca uno a medias."""
    import json, os
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, ruta)


def api_soportada(prov):
    return slug(prov.get("nombre", "")) in API_SOPORTADAS or str(prov.get("uso_api") or "").lower() in API_SOPORTADAS


# ---------- Keychain ----------

def _security(args, entrada=None):
    return subprocess.run(["security"] + args, input=entrada, capture_output=True, text=True, timeout=15)


def clave_guardada(nombre):
    return bool(leer_clave(nombre))


def leer_clave(nombre):
    if sys.platform != "darwin":
        return ""
    try:
        p = _security(["find-generic-password", "-s", SERVICIO, "-a", slug(nombre), "-w"])
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def guardar_clave(nombre, clave):
    """Guarda la clave en el Keychain. Va por stdin (`security -i`): en argv se veria con `ps`."""
    if sys.platform != "darwin":
        raise ValueError("el Keychain solo existe en macOS: usa la variable de entorno del proveedor")
    clave = (clave or "").strip()
    if not _CLAVE_OK.match(clave):
        raise ValueError("la clave tiene caracteres raros o longitud no valida (8-512, sin espacios ni comillas)")
    p = _security(["-i"], f'add-generic-password -U -s {SERVICIO} -a {slug(nombre)} -w "{clave}"\n')
    if p.returncode != 0:
        raise ValueError("no pude guardar en el Keychain (codigo %d)" % p.returncode)   # sin stderr: podria repetir la clave


def borrar_clave(nombre):
    if sys.platform == "darwin":
        _security(["delete-generic-password", "-s", SERVICIO, "-a", slug(nombre)])


# ---------- proyectos ----------

def nombre_proyecto(raw, config):
    """(nombre a mostrar, oculto) segun config['proyectos'][raw] = {alias, oculto}."""
    p = (config.get("proyectos") or {}).get(raw) or {}
    return (str(p.get("alias") or "").strip() or raw), bool(p.get("oculto"))


# ---------- validacion de config.json ----------

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


def validar(nuevo, actual):
    """Devuelve la config a escribir: la actual + los campos que edita la UI, ya saneados.

    Los campos que la UI no conoce (_nota, fuente, tipo...) se conservan; una clave en claro
    (nan_api_key) nunca se acepta desde la UI: las claves van al Keychain."""
    if not isinstance(nuevo, dict):
        raise ValueError("la configuracion no es un objeto")
    for k in ("proveedores", "aliases"):
        if k not in nuevo:
            raise ValueError(f"falta el campo {k}")
    out = dict(actual)
    if "tema" in nuevo:
        if nuevo["tema"] not in TEMAS:
            raise ValueError("tema: claro, oscuro o sistema")
        out["tema"] = nuevo["tema"]
    if "limite_hora_tokens" in nuevo:
        out["limite_hora_tokens"] = _entero(nuevo["limite_hora_tokens"], "limite_hora_tokens")
    if "ignorar" in nuevo:
        out["ignorar"] = _lista_txt(nuevo["ignorar"], "ignorar")
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
            q["uso_api"] = u
            if not u:
                q.pop("uso_api")
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
        if res or "proyectos" in out:
            out["proyectos"] = res
    return out


# ---------- servidor local ----------

def peticion_permitida(host, origin, tipo, puerto, es_post):
    """El servidor escucha en 127.0.0.1 pero cualquier web abierta en tu navegador puede intentar
    hablarle (CSRF) o resolver un dominio suyo a 127.0.0.1 (DNS rebinding). Se exige Host local,
    Origin local si viene, y JSON en los POST (un <form> no puede mandar ese Content-Type)."""
    locales = {f"127.0.0.1:{puerto}", f"localhost:{puerto}"}
    if (host or "") not in locales:
        return False
    if origin and origin not in {f"http://{h}" for h in locales}:
        return False
    if es_post and not (tipo or "").lower().startswith("application/json"):
        return False
    return True
