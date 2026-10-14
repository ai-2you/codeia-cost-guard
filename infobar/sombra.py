"""Comparación en sombra: lo que cuentan los logs (escanear) frente a lo que guarda la base, por agente y día cerrado.

Se compara ANTES de agregar, en contadores enteros por (agente, día local, modelo). Solo días cerrados de hasta 27 días
(Claude borra sus logs a los 30). Hermes queda fuera de la comparación numérica: sus filas son acumulados que siguen subiendo
y el lector antiguo lee otra tabla; se valida con la suma directa de su tabla. Cada fila lleva la dirección de la diferencia
(el lector antiguo cuenta de más: forks y re-emisiones; la base cuenta de más: OpenCode suma reasoning) para que una diferencia
nunca quede sin leer. El fichero guarda solo agente, día, modelo y cifras: ni proyectos ni URLs.
"""
import datetime
import json
import os
import time

from . import consulta, rutas
from .config_y_estado import log

MAX_EDAD_DIAS = 27
CADA_S = 3600
TOPE_FICHERO = 1_000_000
_ultimo = 0.0


def agente_de(clave, registro):
    if clave.startswith("pi:"):
        return "pi"
    if clave.startswith("sqlite:"):
        proyecto = str(registro.get("proyecto") or "")
        return "opencode" if proyecto.startswith("OpenCode") else "hermes" if proyecto.startswith("Hermes") else "otro"
    if "/.claude/" in clave:
        return "claude"
    if "/.codex/" in clave:
        return "codex"
    return "otro"


def lado_antiguo(vistos, dias):
    celdas = {}
    for clave, r in vistos.items():
        agente = agente_de(clave, r)
        for d, mods in (r.get("dia") or {}).items():
            if d not in dias:
                continue
            for m, v in mods.items():
                c = celdas.setdefault((agente, d, m), [0, 0, 0, 0])
                for i in range(4):
                    c[i] += v[i]
    return celdas


def lado_nuevo(con, dias, ahora, zona=None):
    espacio = con.execute("SELECT id FROM espacios WHERE nombre='local'").fetchone()
    celdas = {}
    if not espacio:
        return celdas
    desde = ahora - (MAX_EDAD_DIAS + 3) * 86400
    for f in con.execute("SELECT agente, modelo, (hora_utc/?)*? AS t, SUM(gi), SUM(go), SUM(cr), SUM(cw) FROM turnos "
                         "WHERE espacio_id=? AND hora_utc>=? GROUP BY 1,2,3", (consulta.CUBO_S, consulta.CUBO_S, espacio[0], desde)):
        agente, modelo, t, gi, go, cr, cw = tuple(f)
        d = consulta._dia(t, zona)
        if d in dias:
            c = celdas.setdefault((agente, d, modelo), [0, 0, 0, 0])
            for i, v in enumerate((gi, go, cr, cw)):
                c[i] += v
    return celdas


def dias_cerrados(ahora, zona=None):
    """Días locales completos y recientes: de ayer hacia atrás hasta MAX_EDAD_DIAS."""
    return {consulta._dia(ahora - n * 86400, zona) for n in range(1, MAX_EDAD_DIAS + 1)}


def comparar(antiguo, nuevo):
    """Una fila por (agente, día) con la diferencia, su dirección y si es solo atribución de modelo o cruce de día."""
    agentes_dias = sorted({(a, d) for a, d, _ in list(antiguo) + list(nuevo) if a != "hermes"})
    filas = {}
    for a, d in agentes_dias:
        modelos = {m for aa, dd, m in list(antiguo) + list(nuevo) if (aa, dd) == (a, d)}
        ant = [sum((antiguo.get((a, d, m)) or [0] * 4)[i] for m in modelos) for i in range(4)]
        nue = [sum((nuevo.get((a, d, m)) or [0] * 4)[i] for m in modelos) for i in range(4)]
        por_modelo = sum(1 for m in modelos if (antiguo.get((a, d, m)) or [0] * 4) != (nuevo.get((a, d, m)) or [0] * 4))
        filas[(a, d)] = {"agente": a, "dia": d, "antiguo": sum(ant), "nuevo": sum(nue), "diff": sum(nue) - sum(ant),
                         "celdas_distintas": por_modelo}
    for (a, d), f in filas.items():
        sig = filas.get((a, (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat()))
        if f["diff"] == 0:
            f["clase"] = "igual" if f["celdas_distintas"] == 0 else "atribucion_de_modelo"
        elif sig and sig["diff"] == -f["diff"]:
            f["clase"] = "cruce_de_dia"
        else:
            f["clase"] = "el_antiguo_cuenta_de_mas" if f["diff"] < 0 else "la_base_cuenta_de_mas"
    return [filas[k] for k in sorted(filas)]


def resumen(filas):
    r = {}
    for f in filas:
        x = r.setdefault(f["agente"], {"dias": 0, "iguales": 0, "diff_total": 0, "por_clase": {}})
        x["dias"] += 1
        x["iguales"] += f["clase"] == "igual"
        x["diff_total"] += f["diff"]
        x["por_clase"][f["clase"]] = x["por_clase"].get(f["clase"], 0) + 1
    return r


def _escribir(linea):
    ruta = os.path.join(rutas.DATOS, "sombra.jsonl")
    os.makedirs(rutas.DATOS, mode=0o700, exist_ok=True)
    if os.path.exists(ruta) and os.path.getsize(ruta) > TOPE_FICHERO:
        os.replace(ruta, ruta + ".1")
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(linea, ensure_ascii=False) + "\n")


def registrar(vistos, ahora=None, zona=None):
    """Compara y anota una línea en `sombra.jsonl`. Devuelve el resumen, o None si la base aún no se puede leer."""
    ahora = int(ahora if ahora is not None else time.time())
    try:
        con = consulta._abrir()
    except consulta.SinAlmacen:
        return None
    try:
        dias = dias_cerrados(ahora, zona)
        filas = comparar(lado_antiguo(vistos, dias), lado_nuevo(con, dias, ahora, zona))
    finally:
        con.close()
    res = {"ts": ahora, "resumen": resumen(filas), "filas": [f for f in filas if f["clase"] != "igual"]}
    _escribir(res)
    return res["resumen"]


def registrar_si_toca(vistos):
    """Como mucho una vez por hora y proceso; nunca rompe el hilo que lo llama."""
    global _ultimo
    if not vistos or time.time() - _ultimo < CADA_S:
        return
    _ultimo = time.time()
    try:
        registrar(vistos)
    except Exception as e:                                     # noqa: BLE001
        log(f"sombra: {type(e).__name__}")
