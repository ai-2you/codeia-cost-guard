"""Presupuestos por proyecto y por plan, en tokens totales (entrada + salida + caché leída + caché escrita, la suma de las cuotas).

`config.json` -> `presupuestos: [{ambito: proyecto|plan, nombre, periodo: mes|semana, tokens, metrica: total}]`. `nombre` es el proyecto
mostrado (alias o nombre crudo) o el plan final. La semana empieza el lunes. Solo se evalúan con el almacén activo: con `sombra` el
panel sale de los logs y las dos cifras no coincidirían."""
import datetime

from . import avisos, historico, plataforma
from .config_y_estado import cfg, guardar_config, log

AMBITOS = ("proyecto", "plan")
PERIODOS = ("mes", "semana")
MAX_PRESUPUESTOS = 100
MAX_TOKENS = 10 ** 13
UMBRALES = (80, 100)


def _nombre_ok(n):
    return isinstance(n, str) and 1 <= len(n.strip()) <= 200 and all(c.isprintable() for c in n)


def validar(lista, estricto=True):
    """Lista limpia de presupuestos. Estricto (al guardar): cualquier fila mala es un ValueError con un mensaje que se puede enseñar.
    No estricto (al evaluar): descarta las filas malas, porque config.json es editable a mano."""
    if lista is None:
        return []
    if not isinstance(lista, list) or len(lista) > MAX_PRESUPUESTOS:
        if estricto:
            raise ValueError(f"presupuestos: tiene que ser una lista de {MAX_PRESUPUESTOS} filas como máximo")
        return []
    limpia, vistas = [], set()
    for i, f in enumerate(lista, 1):
        try:
            if not isinstance(f, dict):
                raise ValueError("no es un objeto")
            ambito, periodo, nombre = f.get("ambito"), f.get("periodo"), f.get("nombre")
            if ambito not in AMBITOS or periodo not in PERIODOS:
                raise ValueError("ámbito o periodo no permitido")
            if not _nombre_ok(nombre):
                raise ValueError("nombre vacío, demasiado largo o con caracteres de control")
            tokens = f.get("tokens")
            if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= MAX_TOKENS:
                raise ValueError(f"los tokens tienen que ser un entero entre 1 y {MAX_TOKENS}")
            clave = (ambito, nombre.strip(), periodo)
            if clave in vistas:
                raise ValueError("repetido")
            vistas.add(clave)
            limpia.append({"ambito": ambito, "nombre": nombre.strip(), "periodo": periodo, "tokens": tokens, "metrica": "total"})
        except ValueError as e:
            if estricto:
                raise ValueError(f"presupuestos, fila {i}: {e}") from None
    return limpia


def migrar(conf=None):
    """Una sola vez: los `proyectos[x].presupuesto_mes_tokens` pasan a la lista nueva. El campo antiguo se conserva en config.json (para
    poder volver atrás) pero ya no se edita ni se usa. Devuelve la lista creada o None si no había nada que migrar."""
    conf = conf if conf is not None else cfg()
    if "presupuestos" in conf:
        return None
    filas = []
    for raw, e in sorted((conf.get("proyectos") or {}).items()):
        n = e.get("presupuesto_mes_tokens") if isinstance(e, dict) else 0
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            filas.append({"ambito": "proyecto", "nombre": (str(e.get("alias") or "").strip() or raw), "periodo": "mes", "tokens": min(n, MAX_TOKENS)})
    limpia = validar(filas, estricto=False)
    guardar_config({"presupuestos": limpia})
    if limpia:
        log(f"presupuestos: {len(limpia)} presupuesto(s) mensual(es) migrado(s) desde los proyectos")
    return limpia


def periodo_en_curso(periodo, hoy):
    """(clave, primer día, primer día del siguiente periodo). Semana = de lunes a domingo."""
    if periodo == "semana":
        ini = hoy - datetime.timedelta(days=hoy.weekday())
        return ini.isoformat(), ini, ini + datetime.timedelta(days=7)
    ini = hoy.replace(day=1)
    return ini.strftime("%Y-%m"), ini, datetime.date(ini.year + (ini.month == 12), ini.month % 12 + 1, 1)


def evaluar(conf=None, hoy=None):
    """{disponible, motivo, estados}. Cada estado: ambito, nombre, periodo, tokens, usado, pct (1 decimal), nivel (0, 80 o 100),
    periodo_clave, clave y `visto` (el nombre aparece en el consumo del periodo: si no, probablemente se cambió el alias)."""
    conf = conf if conf is not None else cfg()
    ok, motivo = historico.disponible(conf)
    if not ok:
        return {"disponible": False, "motivo": motivo, "estados": []}
    filas = validar(conf.get("presupuestos"), estricto=False)
    hoy = hoy or datetime.date.today()
    consumo = {}
    for periodo in {f["periodo"] for f in filas}:
        _clave, ini, fin = periodo_en_curso(periodo, hoy)
        consumo[periodo] = historico.consumo_por_grupo(ini, fin, conf)
    estados = []
    for f in filas:
        por_proyecto, por_plan = consumo[f["periodo"]]
        tabla = por_proyecto if f["ambito"] == "proyecto" else por_plan
        usado = int(tabla.get(f["nombre"], 0))
        pct = usado / f["tokens"] * 100
        clave_periodo = periodo_en_curso(f["periodo"], hoy)[0]
        estados.append({**f, "usado": usado, "pct": round(pct, 1), "nivel": max([u for u in UMBRALES if pct >= u] or [0]),
                        "periodo_clave": clave_periodo, "clave": f"{f['ambito']}|{f['nombre']}|{f['periodo']}", "visto": f["nombre"] in tabla})
    return {"disponible": True, "motivo": "", "estados": estados}


def vigilar(conf=None, hoy=None):
    """Para el hilo trabajador de la barra, una vez por ciclo: evalúa, anota los umbrales nuevos y avisa. Devuelve los estados que
    avisaron. El texto del aviso no lleva nombres (se vería en la pantalla de bloqueo)."""
    conf = conf if conf is not None else cfg()
    if conf.get("avisos_presupuesto") is False:
        return []
    r = evaluar(conf, hoy)
    if not r["disponible"]:
        return []
    nuevos = avisos.tomar_pendientes(r["estados"])
    for e in nuevos:
        plataforma.avisar("CostGuard", f"Un presupuesto de {e['ambito']} ha llegado al {e['nivel']} % ({'esta semana' if e['periodo'] == 'semana' else 'este mes'}).")
    return nuevos
