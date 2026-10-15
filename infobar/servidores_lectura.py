"""Lectura del historial de servidores (conexión `mode=ro`): series por hora o día y tabla de IPs con caducidad.

Las IPs solo salen de `ips()`, filtradas por la retención vigente aunque la purga no haya corrido, y nunca van en la serie. Las cifras de
intentos de acceso son PICOS de la ventana de 24 h que mide el servidor: no se suman ni se presentan como intentos del día."""
import datetime
import time

from . import historico, servidores_historico as sh

MAX_HORAS = 14 * 24
MAX_IPS_SALIDA = 500
CAMPOS = ("n", "errores", "carga_max", "mem_max", "disco_max", "intentos_pico_24h", "intentos_pico_1h", "baneadas_max", "sesiones_ajenas_max")
_RANGO = sh._RANGO


def _fila(r):
    return {k: r[k] for k in CAMPOS} | {"estado": r["estado_peor"], "intentos_exactos": r["intentos_exactos"],
                                        "intentos_completa": None if r["intentos_exactos"] is None else bool(r["intentos_completa"]),
                                        "horas_exactas": 0 if r["intentos_exactos"] is None else 1}


def _junta(a, b):
    """Funde dos puntos: picos con máximo, recuentos con suma y el peor estado."""
    out = {"n": a["n"] + b["n"], "errores": a["errores"] + b["errores"]}
    for k in CAMPOS[2:]:
        x, y = a[k], b[k]
        out[k] = y if x is None else (x if y is None else max(x, y))
    out["estado"] = max(a["estado"], b["estado"], key=lambda e: _RANGO[e])
    # los intentos exactos por hora SÍ se suman (son recuentos de horas naturales, no ventanas solapadas); el punto es completo si todas lo son
    x, y = a["intentos_exactos"], b["intentos_exactos"]
    out["intentos_exactos"] = y if x is None else (x if y is None else x + y)
    ca, cb = a["intentos_completa"], b["intentos_completa"]
    out["intentos_completa"] = cb if ca is None else (ca if cb is None else ca and cb)
    out["horas_exactas"] = a["horas_exactas"] + b["horas_exactas"]
    return out


def servidores(con):
    return [{"id": r["id"], "nombre": r["nombre"], "retirado": r["retirado_en"] is not None, "ok_en": r["ok_en"] or None}
            for r in con.execute("SELECT id, nombre, retirado_en, ok_en FROM servidores ORDER BY nombre")]


def historia(con, servidor_id, desde, hasta, paso="dia"):
    """Serie de un servidor entre dos fechas locales (AAAA-MM-DD): por día (máx. 400) o por hora (máx. 14 días)."""
    if paso not in ("hora", "dia"):
        raise historico.ErrorHistorico("paso no permitido")
    d0, d1 = historico._fecha(desde, "desde"), historico._fecha(hasta, "hasta")
    if d0 > d1:
        raise historico.ErrorHistorico("'desde' es posterior a 'hasta'")
    dias = (d1 - d0).days + 1
    if dias > (14 if paso == "hora" else historico.MAX_PUNTOS):
        raise historico.ErrorHistorico("demasiados puntos para ese rango: usa un paso mayor o acorta el rango")
    lo, hi = historico._medianoche(d0), historico._medianoche(d1 + datetime.timedelta(days=1))
    srv = con.execute("SELECT nombre FROM servidores WHERE id=?", (servidor_id,)).fetchone()
    if srv is None:
        raise sh.SinDatos()
    puntos = {}
    for r in con.execute("SELECT * FROM horas_servidor WHERE servidor_id=? AND hora>=? AND hora<? ORDER BY hora", (servidor_id, lo, hi)):
        if paso == "hora":
            clave = r["hora"]
            etiqueta = time.strftime("%Y-%m-%dT%H:00", time.localtime(r["hora"]))
        else:
            t = time.localtime(r["hora"])
            clave = etiqueta = f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        f = _fila(r)
        puntos[clave] = (etiqueta, _junta(puntos[clave][1], f) if clave in puntos else f)
    if paso == "dia":
        for etiqueta, p in puntos.values():
            # un día solo está completo con todas sus horas (23, 24 o 25 según el cambio de hora) con dato y cerradas
            dia = datetime.date.fromisoformat(etiqueta)
            esperadas = (historico._medianoche(dia + datetime.timedelta(days=1)) - historico._medianoche(dia)) // 3600
            if p["intentos_completa"] is not None:
                p["intentos_completa"] = bool(p["intentos_completa"] and p["horas_exactas"] >= esperadas)
    return {"servidor": srv["nombre"], "paso": paso, "desde": d0.isoformat(), "hasta": d1.isoformat(),
            "nota": "intentos_pico_* son picos de la ventana de 24 h del servidor, no sumas; intentos_exactos son los de cada hora natural "
                    "(según el journal del servidor; solo si el servidor tiene la versión nueva del script)",
            "puntos": [{"t": e, **p} for e, p in puntos.values()]}


def ips(con, servidor_id, ahora, retencion_dias=None, guardar_ips=True):
    """IPs de intentos de acceso de un servidor dentro de plazo (la caducidad se calcula con la retención vigente, no se almacena).
    Con `guardar_ips` apagado, o para un servidor retirado de la config, no sale ninguna aunque sigan en la base hasta el próximo mantenimiento."""
    if not guardar_ips:
        return []
    ret, ahora = sh.retencion_dias(retencion_dias) * 86400, int(ahora)
    filas = con.execute(
        "SELECT i.ip, MIN(i.primera, ?1) AS primera, MIN(i.ultima, ?1) AS ultima, i.max_24h, i.horas_vista FROM ips_atacantes i "
        "JOIN servidores s ON s.id = i.servidor_id WHERE i.servidor_id=?2 AND s.retirado_en IS NULL AND MIN(i.ultima, ?1) >= ?1 - ?3 "
        "ORDER BY 3 DESC, i.ip LIMIT ?4", (ahora, servidor_id, ret, MAX_IPS_SALIDA)).fetchall()
    return [{"ip": r["ip"], "primera": r["primera"], "ultima": r["ultima"], "max_24h": r["max_24h"], "horas_vista": r["horas_vista"],
             "caduca_en": r["ultima"] + ret} for r in filas]
