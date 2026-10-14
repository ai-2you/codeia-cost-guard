"""El informe leído del almacén (solo lectura): reconstruye lo que `agregar()` consume y lo llama.

Aquí, y solo aquí, la hora UTC de la base pasa a día y hora locales. Este módulo nunca escribe: abre `mode=ro` una conexión
por llamada (las conexiones de SQLite no se comparten entre hilos) y no importa nada de la ingesta.
"""
import collections
import datetime
import glob
import json
import os
import sqlite3
import time

from . import almacen, plataforma, rutas
from .agregacion import VENTANA_H, agregar
from .planes_y_tarifas import CLIFF, tarifa

CUBO_S = 900                  # 15 min: parte bien el día local también en zonas de media hora y de 45 min
RECIENTE_S = (VENTANA_H + 1) * 3600
NOMBRES = {"claude": "Claude Code", "codex": "Codex", "pi": "pi", "opencode": "OpenCode", "hermes": "Hermes"}


class SinAlmacen(Exception):
    """El informe no puede salir de la base (no existe, versión que no se entiende, dañada o aún sin construir)."""


def plan_de(agente, proveedor):
    """Plan (suscripción) de un turno, igual que lo fabricaban los lectores de logs: es la clave de los `aliases`."""
    if agente == "claude":
        return "Claude (Max)"
    if agente == "codex":
        return "OpenAI (Codex)" if (proveedor or "openai") == "openai" else proveedor
    return proveedor or ""


def _alternativas(agente, proyecto):
    """Nombres con los que el informe antiguo mostraba este proyecto: así un alias ya guardado sigue valiendo."""
    if agente == "pi":
        return ["pi · " + "/".join(proyecto.split("/")[-2:])]
    return []


def _dia(t, zona):
    if zona is None:
        return time.strftime("%Y-%m-%d", time.localtime(t))
    return datetime.datetime.fromtimestamp(t, zona).strftime("%Y-%m-%d")


def _abrir():
    try:
        con = almacen.abrir(solo_lectura=True)
    except (FileNotFoundError, almacen.ErrorAlmacen, sqlite3.Error) as e:
        raise SinAlmacen(f"sin base utilizable ({type(e).__name__})") from e
    try:
        if con.execute("PRAGMA user_version").fetchone()[0] > almacen.VERSION_ESQUEMA:
            raise SinAlmacen("la base es de una versión más nueva que esta aplicación")
        if not almacen.meta(con, "construccion_completa"):
            raise SinAlmacen("la base aún se está construyendo")
    except sqlite3.Error as e:
        con.close()
        raise SinAlmacen(f"base ilegible ({type(e).__name__})") from e
    except SinAlmacen:
        con.close()
        raise
    return con


def _registro(registros, agente, proyecto, plan):
    k = (agente, proyecto, plan)
    if k not in registros:
        registros[k] = {
            "proyecto": proyecto, "proyecto_alt": _alternativas(agente, proyecto), "plan": plan, "clave": "almacen",
            "dia": collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0, 0])),
            "turnos": collections.defaultdict(int),
            "horas": collections.defaultdict(lambda: {"tok": 0, "usd": 0.0, "mod": collections.defaultdict(int)})}
    return registros[k]


def vistos_desde(con, dias, zona=None, ahora=None):
    """La estructura `vistos` de `escanear()` desde la base: días de la ventana (por cubos de 15 min) y, para el ritmo y la
    ventana de 5 h, las horas recientes turno a turno (el recargo por contexto largo es por turno)."""
    ahora = int(ahora if ahora is not None else time.time())
    espacio = con.execute("SELECT id FROM espacios WHERE nombre='local'").fetchone()
    if not espacio:
        raise SinAlmacen("la base no tiene datos")
    eid = espacio[0]
    registros = {}
    desde = ahora - (int(dias) + 1) * 86400
    for f in con.execute(
            "SELECT agente, proveedor, modelo, proyecto_raw, (hora_utc/?)*? AS t, SUM(gi), SUM(go), SUM(cr), SUM(cw), COUNT(*) "
            "FROM turnos WHERE espacio_id=? AND hora_utc>=? GROUP BY 1,2,3,4,5", (CUBO_S, CUBO_S, eid, desde)):
        agente, prov, modelo, proy, t, gi, go, cr, cw, n = tuple(f)
        r = _registro(registros, agente, proy or "otros", plan_de(agente, prov))
        d = _dia(t, zona)
        a = r["dia"][d][modelo]
        a[0] += gi; a[1] += go; a[2] += cr; a[3] += cw
        r["turnos"][d] += n
    for f in con.execute(
            "SELECT agente, proveedor, modelo, proyecto_raw, hora_utc, gi, go, cr, cw FROM turnos "
            "WHERE espacio_id=? AND hora_utc>=?", (eid, ahora - RECIENTE_S)):
        agente, prov, modelo, proy, t, gi, go, cr, cw = tuple(f)
        h = _registro(registros, agente, proy or "otros", plan_de(agente, prov))["horas"][(t // 3600) * 3600]
        tok = gi + go + cr + cw
        h["tok"] += tok
        h["mod"][modelo] += tok
        tr = tarifa(modelo)
        if tr:
            over = gi > CLIFF
            h["usd"] += (gi * tr[0] * (2 if over else 1) + cr * tr[2] * (2 if over else 1) + cw * tr[3] * (2 if over else 1)
                         + go * tr[1] * (1.5 if over else 1)) / 1e6
    bases = [(NOMBRES.get(f[0], f[0]), f[1]) for f in con.execute(
        "SELECT agente, COUNT(*) FROM fuentes WHERE espacio_id=? GROUP BY agente ORDER BY agente", (eid,))]
    return {f"almacen:{k[0]}|{k[1]}|{k[2]}": v for k, v in registros.items()}, bases


def perfiles_hermes(con, eid, dias, home=None, ahora=None):
    """Tokens por perfil de Hermes. La base no guarda el nombre (la fuente es un hash de la ruta): se recupera calculando el hash
    de cada perfil que existe en el disco. Un perfil ya borrado sale como `hermes/<huella corta>`."""
    home = home or rutas.HOME
    conocidos = {}
    raiz = os.path.join(home, ".hermes", "state.db")
    conocidos[plataforma.id_fuente(raiz, home)] = "predeterminado"
    for ruta in glob.glob(os.path.join(home, ".hermes", "profiles", "*", "state.db")):
        conocidos[plataforma.id_fuente(ruta, home)] = os.path.basename(os.path.dirname(ruta))
    desde = int(ahora if ahora is not None else time.time()) - (int(dias) + 1) * 86400
    res = {}
    for f in con.execute("SELECT f.id_fuente, SUM(t.gi+t.go+t.cr+t.cw) FROM turnos t JOIN fuentes f ON f.id=t.fuente_id "
                         "WHERE t.espacio_id=? AND t.agente='hermes' AND t.hora_utc>=? GROUP BY f.id_fuente", (eid, desde)):
        res[conocidos.get(f[0]) or f"hermes/{f[0][:8]}"] = int(f[1] or 0)
    return res


def consultar(dias=None, zona=None):
    """Mismo informe que `escanear()`, leído de la base. Lanza `SinAlmacen` si no puede."""
    con = _abrir()
    try:
        try:
            vistos, bases = vistos_desde(con, dias or 31, zona)
            eid = con.execute("SELECT id FROM espacios WHERE nombre='local'").fetchone()[0]
            hermes = perfiles_hermes(con, eid, dias or 31)
            vuelta = json.loads(almacen.meta(con, "ultima_vuelta") or "{}")
        except (ValueError, TypeError):
            hermes, vuelta = {}, {}
        except sqlite3.Error as e:
            raise SinAlmacen(f"base ilegible ({type(e).__name__})") from e
    finally:
        con.close()
    r = agregar(vistos, 0, dias)
    r["perfiles_hermes"], r["nuevos"] = hermes, int(vuelta.get("nuevos") or 0) if time.time() - int(vuelta.get("en") or 0) < 120 else 0
    r["fuentes"], r["bases"] = sum(n for _, n in bases), [f"{a} ({n})" for a, n in bases]
    return r
