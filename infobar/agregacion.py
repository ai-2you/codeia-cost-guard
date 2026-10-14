"""Escaneo de todas las fuentes y agregacion en el informe (5 h, hoy, semana, mes, planes)."""
import collections, glob, os, time

from . import ajustes
from .config_y_estado import cfg, guardar_estado, leer_estado, log
from .cuota_nan import uso_nan
from .lectores_logs import FUENTES, NOMBRE_FUENTE, PI_DIR, _dia_hora, _funde, leer_bases, parsear, parsear_pi
from .planes_y_tarifas import corto, modelo_nativo, plan_final, proveedor_de, proveedores_de, tarifa


ULTIMOS = {}                # el `vistos` de la última pasada de escanear(): lo lee la comparación en sombra, solo lectura
DIAS = 31                   # periodo por defecto; escanear(dias=N) lo cambia para una pasada


VENTANA_H = 5               # ventana de 5 horas de las suscripciones


def escanear(dias=None):
    """Lee todas las fuentes (con cache por fichero) y devuelve el informe de los ultimos `dias`."""
    estado = leer_estado()
    vistos, nuevos = {}, 0
    nombres_fuentes = []
    for base in FUENTES:
        ficheros = glob.glob(f"{base}/**/*.jsonl", recursive=True) + glob.glob(f"{base}/**/*.json", recursive=True)
        if ficheros:
            nombres_fuentes.append(f"{NOMBRE_FUENTE.get(base, base)} ({len(ficheros)})")
        for f in ficheros:
            try:
                st = os.stat(f)
            except OSError:
                continue
            clave = f"{int(st.st_mtime)}:{st.st_size}"
            prev = estado["ficheros"].get(f)
            if prev and prev.get("clave") == clave:
                vistos[f] = prev
            else:
                r = parsear(f)
                if r is None:
                    continue
                r["clave"] = clave
                vistos[f] = r
                nuevos += 1
    # pi (agent): JSONL en ~/.pi/agent/sessions; cada sesion puede cambiar de proveedor a mitad,
    # asi que parsear_pi devuelve registros por provider y aqui se unen en pi:<proyecto>|<plan>.
    # El analisis va cacheado por fichero en estado["pi"] (los jsonl de pi pueden ser grandes).
    ficheros_pi = glob.glob(f"{PI_DIR}/**/*.jsonl", recursive=True) if os.path.isdir(PI_DIR) else []
    cache_pi = estado.get("pi") or {}
    for f in list(cache_pi):
        if f not in ficheros_pi:
            del cache_pi[f]
    recs_pi = {}
    for f in ficheros_pi:
        try:
            st = os.stat(f)
        except OSError:
            continue
        clave = f"{int(st.st_mtime)}:{st.st_size}"
        prev = cache_pi.get(f)
        if prev and prev.get("clave") == clave:
            datos = prev.get("datos")
        else:
            datos = parsear_pi(f)
            cache_pi[f] = {"clave": clave, "datos": datos}
        if not datos:
            continue
        for prov, rec in datos["proveedores"].items():
            k = f"pi:{datos['proyecto']}|{prov}"
            if k not in recs_pi:
                recs_pi[k] = _dia_hora()
            _funde(recs_pi[k], rec)
    estado["pi"] = cache_pi
    for k, rec in recs_pi.items():
        proy, plan = k[3:].split("|", 1)
        rec["proyecto"], rec["plan"], rec["clave"] = proy, plan, "pi"
        vistos[k] = rec
    proyectos_base, nombres_bases = leer_bases()
    if ficheros_pi:
        nombres_bases.append(f"pi ({len(ficheros_pi)})")
    for (proy, plan), datos in proyectos_base.items():
        datos["proyecto"] = proy
        datos["plan"] = plan
        datos["clave"] = "bases"
        vistos[f"sqlite:{proy}|{plan}"] = datos
    estado["ficheros"] = vistos
    guardar_estado(estado)
    ULTIMOS["vistos"] = vistos
    r = agregar(vistos, nuevos, dias)
    r["fuentes"] = r["fuentes"] + len(nombres_bases)
    r["bases"] = nombres_fuentes + nombres_bases
    return r


def informe(dias=None):
    """Único punto por el que los llamadores piden el informe. `almacen: activo` lo lee de la base; si la base no puede
    (no existe, versión que no entiende, dañada o aún sin construir) cae a los logs y lo anota una vez por proceso."""
    if str(cfg().get("almacen") or "sombra").lower() == "activo":
        from . import consulta
        try:
            return consulta.consultar(dias)
        except consulta.SinAlmacen as e:
            if str(e) not in _AVISADO and len(_AVISADO) < 20:
                _AVISADO.add(str(e))
                log(f"informe: se usan los logs: {e}")
    return escanear(dias)


_AVISADO = set()


def vacio():
    return {"gi": 0, "go": 0, "cr": 0, "cw": 0, "tok": 0, "turnos": 0, "usd": 0.0}


def suma(dest, v):
    dest["gi"] += v[0]; dest["go"] += v[1]; dest["cr"] += v[2]; dest["cw"] += v[3]
    dest["tok"] += v[0] + v[1] + v[2] + v[3]


def modelos_ignorados(conf):
    return [str(x).lower() for x in (conf.get("ignorar") or ["synthetic", "<synthetic>", "sin-modelo"])]


def es_ignorado(modelo, ignora):
    return any(x in (modelo or "").lower() for x in ignora)


def proyecto_mostrado(r, conf):
    """(nombre a mostrar, oculto) de un registro de consumo: lo comparten el informe y el histórico para que sus cifras coincidan."""
    return ajustes.nombre_proyecto(r.get("proyecto", "otros"), conf, r.get("proyecto_alt") or ())


def plan_del_consumo(r, modelo, provs):
    """Plan final al que se asigna el consumo de `modelo` en el registro `r` (mismo criterio para el informe y el histórico)."""
    plan_f = r.get("plan")
    fiable = bool(plan_f) and modelo_nativo(plan_f, modelo)
    return plan_final(proveedor_de(modelo, provs, plan_f, fiable), [x["nombre"] for x in provs])


def agregar(vistos, nuevos=0, periodo=None):
    hoy = time.strftime("%Y-%m-%d")
    ayer = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    desde = time.strftime("%Y-%m-%d", time.localtime(time.time() - (periodo or DIAS) * 86400))
    hace7 = time.strftime("%Y-%m-%d", time.localtime(time.time() - 6 * 86400))
    hace30 = time.strftime("%Y-%m-%d", time.localtime(time.time() - 29 * 86400))
    inicio_mes = time.strftime("%Y-%m-01")       # las cuotas mensuales (NaN SOTA) resetean el dia 1
    dias = collections.defaultdict(vacio)
    modelos_hoy = collections.defaultdict(vacio)
    modelos_mes = collections.defaultdict(vacio)
    modelo_dia = collections.defaultdict(lambda: collections.defaultdict(int))
    proys = collections.defaultdict(vacio)
    ritmo = {"tok": 0, "usd": 0.0}
    cinco = {"tok": 0, "usd": 0.0, "fam": collections.defaultdict(int)}
    modelos_vistos = {m for r in vistos.values() for mods in r.get("dia", {}).values() for m in mods}
    provs = proveedores_de(cfg(), modelos_vistos)
    por_prov = collections.defaultdict(lambda: {"cinco_h": 0, "semana": 0, "hoy": 0, "mes": 0, "modelos": set()})
    cruce = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(int)))
    proy_prov = collections.defaultdict(lambda: collections.defaultdict(int))
    # como cruce, pero por proyecto en vez de por modelo: hace falta para el detalle por dia/tramo del panel
    proy_cruce = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(int)))
    modelo_prov = {}
    origenes = collections.defaultdict(int)
    proys_raw = collections.defaultdict(int)       # tokens 30 d por nombre detectado (para Ajustes)
    conf = cfg()
    ahora = time.time()
    ignora = modelos_ignorados(conf)
    for f, r in vistos.items():
        proy_raw = r.get("proyecto", "otros")
        proy_n, proy_oculto = proyecto_mostrado(r, conf)
        for d, mods in r.get("dia", {}).items():
            if not d or d < desde:
                continue
            for m, v in mods.items():
                if es_ignorado(m, ignora):
                    continue
                suma(dias[d], v)
                t = tarifa(m)
                if t:
                    # el recargo por contexto largo (CLIFF) es por turno: sobre sumas diarias no se puede aplicar
                    dias[d]["usd"] += (v[0] * t[0] + v[2] * t[2] + v[3] * t[3] + v[1] * t[1]) / 1e6
                if d >= hace30:
                    suma(modelos_mes[m], v)
                    modelo_dia[m][d] += v[0] + v[1] + v[2] + v[3]
                if d == hoy:
                    suma(modelos_hoy[m], v)
                    if t:
                        modelos_hoy[m]["usd"] += (v[0] * t[0] + v[2] * t[2] + v[3] * t[3] + v[1] * t[1]) / 1e6
                if not proy_oculto:
                    suma(proys[proy_n], v)
                pn = plan_del_consumo(r, m, provs)
                pp = por_prov[pn]
                pp["modelos"].add(corto(m))
                modelo_prov[corto(m)] = pn
                tokv = v[0] + v[1] + v[2] + v[3]
                cruce[pn][corto(m)][d] += tokv
                origenes[str(r.get("plan") or "(sin origen)")] += tokv
                if d >= hace30:
                    proys_raw[proy_raw] += tokv
                if not proy_oculto:
                    proy_prov[pn][proy_n] += tokv
                    proy_cruce[pn][proy_n][d] += tokv
                if d >= hace7:
                    pp["semana"] += tokv
                if d >= hace30:
                    pp["mes"] += tokv
                if d == hoy:
                    pp["hoy"] += tokv
        for d, n in r.get("turnos", {}).items():
            if d and d >= desde:
                dias[d]["turnos"] += n
        for h, v in r.get("horas", {}).items():
            if isinstance(h, int):             # la base entrega el inicio del cubo en epoch: sin ambigüedad en el día de 25 h
                hm = h
            elif len(h) < 13:
                continue
            else:
                try:
                    hm = time.mktime(time.strptime(h, "%Y-%m-%dT%H"))
                except ValueError:
                    continue
            edad = ahora - hm
            if edad < 3600:
                ritmo["tok"] += v.get("tok", 0)
                ritmo["usd"] += v.get("usd", 0.0)
            if edad < VENTANA_H * 3600:            # ventana de 5 horas de la suscripcion
                cinco["tok"] += v.get("tok", 0)
                cinco["usd"] += v.get("usd", 0.0)
                for k2, t2 in (v.get("mod") or {}).items():
                    cinco["fam"][proveedor_de(k2, provs)] += t2

    def pct(bloque):
        entrada_total = bloque["gi"] + bloque["cr"] + bloque["cw"]
        return (bloque["cr"] / entrada_total * 100) if entrada_total else 0.0

    for d in dias:
        dias[d]["cache_pct"] = pct(dias[d])
    c = cfg()
    lim5, limsem = c.get("limite_5h_tokens") or 0, c.get("limite_semana_tokens") or 0
    for n, t2 in cinco["fam"].items():          # la ventana de 5 h, por proveedor
        por_prov[n]["cinco_h"] = t2
    lim_prov = {p["nombre"]: (p["limite_5h_tokens"], p["limite_semana_tokens"]) for p in provs}
    provs_out = {}
    for n, x in sorted(por_prov.items(), key=lambda kv: -kv[1]["cinco_h"]):
        l5, ls = lim_prov.get(n, (0, 0))
        provs_out[n] = {"cinco_h": x["cinco_h"], "semana": x["semana"], "hoy": x["hoy"], "mes": x["mes"],
                        "modelos": sorted(x["modelos"]),
                        "pct_5h": (x["cinco_h"] / l5 * 100) if l5 else 0.0,
                        "pct_semana": (x["semana"] / ls * 100) if ls else 0.0}
    def agrega(filtro):
        acc = vacio()
        for d, x in dias.items():
            if d and filtro(d):
                for k in acc:
                    acc[k] += x[k]
        return acc
    semana = agrega(lambda d: d >= hace7)
    mes = agrega(lambda d: d >= hace30)
    # Un modelo puede servir a dos planes (deepseek por NaN y por OpenRouter): se muestra
    # el plan con el que mas se consume, para que la etiqueta no mienta.
    _tok = collections.defaultdict(lambda: collections.defaultdict(int))
    for _pn, _mods in cruce.items():
        for _m, _ds in _mods.items():
            _tok[_m][_pn] = sum(_ds.values())
    for _m, _d in _tok.items():
        if _d:
            modelo_prov[_m] = max(_d, key=_d.get)
    # Cuota mensual por modelo. Para un plan con uso_api (NaN) la fuente es su endpoint /usage, que
    # es el contador real; para el resto, los logs locales. Se calcula por proveedor.
    _vivo_nan = {}

    def _vivo_de(_cf):
        if str(_cf.get("uso_api") or "").lower() != "nan":
            return None
        if "nan" not in _vivo_nan:
            _vivo_nan["nan"] = uso_nan(inicio_mes)
        return _vivo_nan["nan"]

    def _cuotas_de(_nombre, _cf, _vivo):
        _cuotas = _cf.get("cuotas_mes") or {}
        # Solo los planes que declaran cuotas (o leen su consumo de una API) listan modelos sin tope.
        _con_cuotas = bool(_cuotas) or _vivo is not None or str(_cf.get("uso_api") or "").lower() == "nan"
        if not _con_cuotas:
            return None, None
        # consumo del mes segun los logs locales (para los modelos que la API no reporte)
        _local = {}
        for _m, _ds in (cruce.get(_nombre) or {}).items():
            _t = sum(_v for _d, _v in _ds.items() if _d >= inicio_mes)
            if _t:
                _local[_m] = _t
        _mc = []
        _cubiertos = set()

        def _cubre(_m):
            _ml = _m.lower()
            return _ml in _cubiertos or any(_ml == c or c in _ml for c in _cubiertos)

        for _pat, _lim in _cuotas.items():
            if not _lim:
                continue
            _patl = str(_pat).lower()
            _tot = 0
            if _vivo is not None:
                for _m, _t in _vivo.items():
                    if _patl == _m or _patl in _m:
                        _tot += _t
                        _cubiertos.add(_m)
            else:
                for _m, _t in _local.items():
                    if _patl == _m.lower() or _patl in _m.lower():
                        _tot += _t
                        _cubiertos.add(_m.lower())
            _mc.append({"modelo": str(_pat), "mes": int(_tot), "limite": int(_lim),
                        "pct": round(100.0 * _tot / _lim, 1), "fuente": "api" if _vivo is not None else "logs"})
        # Modelos del plan SIN tope publicado (no-SOTA): se listan con su consumo del mes, marcados
        # "sin_tope". Primero los que si reporta la API; luego los que solo ven los logs locales
        # (p. ej. qwen3.6, que NaN responde pero su /usage no contabiliza).
        if _vivo is not None:
            for _m, _t in _vivo.items():
                if not _t or _cubre(_m):
                    continue
                _mc.append({"modelo": _m, "mes": int(_t), "limite": 0, "pct": None,
                            "sin_tope": True, "fuente": "api"})
                _cubiertos.add(_m.lower())
        for _m, _t in _local.items():
            if not _t or _cubre(_m):
                continue
            _mc.append({"modelo": _m, "mes": int(_t), "limite": 0, "pct": None,
                        "sin_tope": True, "fuente": "logs"})
            _cubiertos.add(_m.lower())
        if not _mc:
            return None, None
        _mc.sort(key=lambda z: (-(z["pct"] if z.get("pct") is not None else -1), -z["mes"]))
        return _mc, ("api" if _vivo is not None else "logs")

    for _n, _pr in (provs_out or {}).items():
        _cf = next((x for x in (cfg().get("proveedores") or []) if x["nombre"] == _n), {})
        _t5 = next((v for kk, v in _pr.items() if isinstance(v, (int, float)) and ("5h" in kk.lower() or "cinco" in kk.lower())), 0)
        _ts = next((v for kk, v in _pr.items() if isinstance(v, (int, float)) and "semana" in kk.lower()), 0)
        _l5, _ls = _cf.get("limite_5h_tokens") or 0, _cf.get("limite_semana_tokens") or 0
        _pr["tipo"] = _cf.get("tipo") or ("suscripcion" if (_l5 or _ls) else "pago_por_uso")
        _pr["pct_5h"] = round(100.0 * _t5 / _l5, 1) if _l5 else 0
        _pr["pct_semana"] = round(100.0 * _ts / _ls, 1) if _ls else 0
        _mc, _cfuente = _cuotas_de(_n, _cf, _vivo_de(_cf))
        if _mc:
            _pr["modelos_cuota"] = _mc
            _pr["cuota_fuente"] = _cfuente
    # Aunque no haya uso local (p. ej. todo corrio en la nube), la cuota de NaN sale igual: la
    # publica su API. Sin esto, un mes sin logs locales dejaria la seccion de cuotas vacia.
    for _pcf in (cfg().get("proveedores") or []):
        _nombre = str(_pcf.get("nombre") or "")
        _vivo = _vivo_de(_pcf)
        if not _nombre or _vivo is None:
            continue
        _mc, _cfuente = _cuotas_de(_nombre, _pcf, _vivo)
        if not _mc:
            continue
        _pn = plan_final(_nombre, [x["nombre"] for x in provs])
        _pr = provs_out.get(_pn) or {"cinco_h": 0, "semana": 0, "hoy": 0, "mes": 0, "modelos": [],
                                     "pct_5h": 0.0, "pct_semana": 0.0, "tipo": "suscripcion"}
        _pr["modelos_cuota"] = _mc
        _pr["cuota_fuente"] = _cfuente
        provs_out[_pn] = _pr
    return {"hoy": dias.get(hoy, vacio()), "ayer": dias.get(ayer, vacio()), "semana": semana, "mes": mes,
            "modelos_mes": dict(modelos_mes),
            "modelo_dia": {m: dict(v) for m, v in modelo_dia.items()},
            "ritmo": ritmo, "cinco_h": {"tok": cinco["tok"], "usd": cinco["usd"], "fam": dict(cinco["fam"])},
            "pct_5h": (cinco["tok"] / lim5 * 100) if lim5 else 0.0,
            "pct_semana": (semana["tok"] / limsem * 100) if limsem else 0.0,
            "dias": {d: dias[d] for d in dias if d},
            "modelos_hoy": dict(modelos_hoy), "proyectos": dict(proys),
            "proveedores": provs_out,
            "cruce": {n: {m: dict(v) for m, v in d2.items()} for n, d2 in cruce.items()},
            "proy_prov": {n: dict(v) for n, v in proy_prov.items()},
            "proy_cruce": {n: {p: dict(dd) for p, dd in mm.items()} for n, mm in proy_cruce.items()},
        "modelo_prov": modelo_prov,
        "origenes": dict(origenes), "proyectos_origen": dict(proys_raw),
        "config": cfg(),
            "cache_pct_hoy": pct(dias.get(hoy, vacio())), "nuevos": nuevos,
            "limites": c, "fuentes": len(vistos)}
