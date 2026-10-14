"""Histórico del consumo, leído de la tabla permanente `horas` (solo lectura, sin importar nada de la ingesta).

El proyecto mostrado, el plan final y los modelos ignorados se deciden con las mismas funciones que usa el informe
(`agregacion.proyecto_mostrado`, `plan_del_consumo`, `es_ignorado`), así que las cifras coinciden con el panel. Los límites de día,
semana (lunes) y mes son locales: se calculan con la librería C del sistema (`time.mktime`/`localtime`), que conoce el cambio de
hora también en Windows, y la base los recibe ya convertidos a segundos UTC. Se asume una hora entera de desfase con UTC; en zonas de
media hora el consumo de la media hora de borde cae en el día vecino."""
import collections
import datetime
import re
import time

from . import almacen
from .agregacion import es_ignorado, modelos_ignorados, plan_del_consumo, proyecto_mostrado
from .config_y_estado import cfg
from .consulta import NOMBRES, SinAlmacen, _abrir, _alternativas, plan_de
from .planes_y_tarifas import proveedores_de

PASOS = ("dia", "semana", "mes")
AGRUPACIONES = ("ninguno", "proyecto", "plan", "agente")
MAX_PUNTOS = 400
MAX_SERIES = 200
OTROS, OCULTOS = "(otros)", "(ocultos)"
_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ErrorHistorico(ValueError):
    """Parámetros fuera de lo permitido: el mensaje se puede enseñar tal cual."""


def disponible(conf=None):
    """(bool, motivo). El histórico sale de la base; mientras el informe salga de los logs (`sombra`) las dos cifras pueden diferir."""
    if str((conf or cfg()).get("almacen") or "sombra").lower() != "activo":
        return False, "El histórico necesita el almacén activo (hoy el panel se calcula desde los logs y las cifras no coincidirían)."
    return True, ""


def _fecha(texto, nombre):
    if not isinstance(texto, str) or not _FECHA.match(texto):
        raise ErrorHistorico(f"'{nombre}' debe ser AAAA-MM-DD")
    try:
        d = datetime.date.fromisoformat(texto)
    except ValueError:
        raise ErrorHistorico(f"'{nombre}' no es una fecha válida") from None
    if d.year < 2000:
        raise ErrorHistorico(f"'{nombre}' es anterior a 2000")
    return d


def _medianoche(d):
    return int(time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1)))


def _inicio(d, paso):
    if paso == "semana":
        return d - datetime.timedelta(days=d.weekday())
    return d.replace(day=1) if paso == "mes" else d


def _siguiente(d, paso):
    if paso == "dia":
        return d + datetime.timedelta(days=1)
    if paso == "semana":
        return d + datetime.timedelta(days=7)
    return datetime.date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def _anterior(d, paso, n):
    for _ in range(n):
        if paso == "dia":
            d -= datetime.timedelta(days=1)
        elif paso == "semana":
            d -= datetime.timedelta(days=7)
        else:
            d = datetime.date(d.year - (d.month == 1), (d.month - 2) % 12 + 1, 1)
    return d


def _cubos(desde, hasta, paso):
    """Inicios de cada cubo desde el que contiene `desde` hasta el que contiene `hasta`. Corta antes de recorrer rangos enormes."""
    inicio, ultimo = _inicio(desde, paso), _inicio(hasta, paso)
    dias = (ultimo - inicio).days
    if dias // {"dia": 1, "semana": 7, "mes": 28}[paso] + 1 > MAX_PUNTOS:
        raise ErrorHistorico(f"demasiados puntos para ese rango (máximo {MAX_PUNTOS}): usa un paso mayor o acorta el rango")
    cubos, d = [], inicio
    while d <= ultimo:
        cubos.append(d)
        d = _siguiente(d, paso)
    return cubos


def _filas(con, eid, lo, hi, conf):
    """Consumo de [lo, hi) clasificado: (fecha local, agente, plan, proyecto mostrado, oculto, gi, go, cr, cw, turnos)."""
    modelos = [f[0] for f in con.execute("SELECT DISTINCT modelo FROM horas WHERE espacio_id=? AND hora>=? AND hora<?", (eid, lo, hi))]
    provs = proveedores_de(conf, modelos)
    ignora = modelos_ignorados(conf)
    registros, planes = {}, {}
    for hora, agente, prov, modelo, proy, gi, go, cr, cw, turnos in con.execute(
            "SELECT hora, agente, proveedor, modelo, proyecto_raw, gi, go, cr, cw, turnos FROM horas "
            "WHERE espacio_id=? AND hora>=? AND hora<?", (eid, lo, hi)):
        if es_ignorado(modelo, ignora):
            continue
        k = (agente, prov, proy or "otros")
        if k not in registros:
            r = {"proyecto": k[2], "proyecto_alt": _alternativas(agente, k[2]), "plan": plan_de(agente, prov)}
            registros[k] = (r, proyecto_mostrado(r, conf))
        r, (nombre, oculto) = registros[k]
        if (k, modelo) not in planes:
            planes[(k, modelo)] = plan_del_consumo(r, modelo, provs)
        t = time.localtime(hora)
        yield datetime.date(t.tm_year, t.tm_mon, t.tm_mday), agente, planes[(k, modelo)], nombre, oculto, gi, go, cr, cw, turnos


def _etiqueta(agrupa, agente, plan, proyecto, oculto):
    if agrupa == "proyecto":
        return OCULTOS if oculto else proyecto
    return {"plan": plan, "agente": NOMBRES.get(agente, agente)}.get(agrupa, "Total")


def historico(desde, hasta, paso="dia", agrupa="ninguno", conf=None):
    """Consumo por cubo (día, semana de lunes o mes locales) entre dos fechas locales, agrupado. Devuelve números crudos:
    el nombre de cada serie lo pinta quien lo reciba, nunca como HTML. Los cubos anteriores al primer dato valen None."""
    if paso not in PASOS or agrupa not in AGRUPACIONES:
        raise ErrorHistorico("paso o agrupación no permitidos")
    d0, d1 = _fecha(desde, "desde"), _fecha(hasta, "hasta")
    if d0 > d1:
        raise ErrorHistorico("'desde' es posterior a 'hasta'")
    conf = conf if conf is not None else cfg()
    cubos = _cubos(d0, d1, paso)
    indice = {c: i for i, c in enumerate(cubos)}
    fin = _siguiente(cubos[-1], paso)
    lo, hi = _medianoche(cubos[0]), _medianoche(fin)
    con = _abrir()
    try:
        eid = con.execute("SELECT id FROM espacios WHERE nombre='local'").fetchone()[0]
        primero = con.execute("SELECT MIN(hora) FROM horas WHERE espacio_id=?", (eid,)).fetchone()[0]
        series = collections.defaultdict(lambda: [[0] * len(cubos), [0] * len(cubos)])
        comp = [0, 0, 0, 0]
        for dia, agente, plan, proy, oculto, gi, go, cr, cw, n in _filas(con, eid, lo, hi, conf):
            s = series[_etiqueta(agrupa, agente, plan, proy, oculto)]
            i = indice[_inicio(dia, paso)]
            s[0][i] += gi + go + cr + cw
            s[1][i] += n
            comp[0] += gi; comp[1] += go; comp[2] += cr; comp[3] += cw
        n_ant = len(cubos)
        ant_lo, ant_hi = _medianoche(_anterior(cubos[0], paso, n_ant)), lo
        anterior = sum(gi + go + cr + cw for _d, _a, _p, _n, _o, gi, go, cr, cw, _t in _filas(con, eid, ant_lo, ant_hi, conf))
        hoy = datetime.date.today()
        mes_ini = hoy.replace(day=1)
        acumulado = sum(gi + go + cr + cw for _d, _a, _p, _n, _o, gi, go, cr, cw, _t in
                        _filas(con, eid, _medianoche(mes_ini), _medianoche(_siguiente(mes_ini, "mes")), conf))
    except (TypeError, IndexError):
        raise SinAlmacen("la base no tiene datos") from None
    finally:
        con.close()
    datos_desde = None
    if primero is not None:
        t = time.localtime(primero)
        datos_desde = datetime.date(t.tm_year, t.tm_mon, t.tm_mday)
    hay = [datos_desde is not None and _siguiente(c, paso) > datos_desde for c in cubos]     # el cubo que contiene el primer dato ya cuenta
    orden = sorted(series.items(), key=lambda kv: -sum(kv[1][0]))
    salida = [{"nombre": k, "tokens": v[0], "turnos": v[1]} for k, v in orden[:MAX_SERIES - 1]]
    if len(orden) > MAX_SERIES - 1:
        resto = orden[MAX_SERIES - 1:]
        salida.append({"nombre": OTROS, "tokens": [sum(v[0][i] for _k, v in resto) for i in range(len(cubos))],
                       "turnos": [sum(v[1][i] for _k, v in resto) for i in range(len(cubos))]})
    totales = [sum(s["tokens"][i] for s in salida) if hay[i] else None for i in range(len(cubos))]
    for s in salida:
        s["tokens"] = [v if hay[i] else None for i, v in enumerate(s["tokens"])]
        s["turnos"] = [v if hay[i] else None for i, v in enumerate(s["turnos"])]
    dias_mes = (_siguiente(mes_ini, "mes") - mes_ini).days
    pasados = (hoy - mes_ini).days + 1
    return {"desde": cubos[0].isoformat(), "hasta": (fin - datetime.timedelta(days=1)).isoformat(), "paso": paso, "agrupa": agrupa,
            "cubos": [c.isoformat() for c in cubos], "series": salida, "totales": totales,
            "total": sum(t for t in totales if t), "componentes": dict(zip(("entrada", "salida", "cache_leida", "cache_escrita"), comp)),
            "anterior": anterior, "datos_desde": datos_desde.isoformat() if datos_desde else None,
            "mes_en_curso": {"mes": mes_ini.strftime("%Y-%m"), "acumulado": acumulado, "dias_transcurridos": pasados, "dias_mes": dias_mes,
                             "proyeccion": round(acumulado / pasados * dias_mes) if pasados >= 3 else None}}


def consumo_por_grupo(desde, hasta, conf=None):
    """Tokens de [desde, hasta) (fechas locales; `hasta` excluido) por proyecto mostrado y por plan final, para evaluar presupuestos.
    Cuenta también los proyectos ocultos (ocultar no cambia los totales). Devuelve ({proyecto: tokens}, {plan: tokens})."""
    conf = conf if conf is not None else cfg()
    por_proyecto, por_plan = collections.defaultdict(int), collections.defaultdict(int)
    con = _abrir()
    try:
        eid = con.execute("SELECT id FROM espacios WHERE nombre='local'").fetchone()[0]
        for _d, _a, plan, proy, _o, gi, go, cr, cw, _n in _filas(con, eid, _medianoche(desde), _medianoche(hasta), conf):
            por_proyecto[proy] += gi + go + cr + cw
            por_plan[plan] += gi + go + cr + cw
    except (TypeError, IndexError):
        raise SinAlmacen("la base no tiene datos") from None
    finally:
        con.close()
    return dict(por_proyecto), dict(por_plan)
