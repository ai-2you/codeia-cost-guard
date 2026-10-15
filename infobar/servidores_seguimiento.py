"""Seguimiento de las webs que el dueño vigila: estado confirmado, eventos y disponibilidad observada, en `servidores.sqlite`.

· Cada sondeo (lo hace `webs_sondeo`, nunca esta capa) se clasifica en **ok** (200–399, y 401/403: la aplicación está viva), **mal** (el resto de 4xx, entre ellos el 404 que da el
  proxy cuando un host ya no tiene ruta, o una respuesta lentísima) o **caida** (5xx, sin respuesta, timeout, TLS inválido).
· El estado **confirmado** de una web solo cambia tras dos sondeos seguidos (separados ≥ `ESPERA_S`) que dicen lo mismo, para pasar a caída o a «mal»; volver a «ok» se confirma con
  uno. Un fallo suelto no genera eventos. La primera vez que se ve una web (línea base) se anota su estado sin evento.
· Eventos: `cayo`, `volvio`, `degradada`. Presupuesto por web y día (el primer `cayo` del día siempre cabe) y por servidor y día, separado del de los contenedores.
· «Disponibilidad observada»: por hora (90 días) y por día (permanente) se cuentan los sondeos ok/mal/caída del Mac; los huecos con el Mac apagado son «sin dato», nunca caída.
· El dominio (la URL) es dato personal: se borra al dejar de vigilar la web, con el servidor y con `--olvidar-recursos`."""
import re

from . import servidores_recursos as rec
from .servidores import url_valida

ESPERA_S = 45
CONFIRMA = {"caida": 2, "mal": 2, "ok": 1}
EVENTOS_DIA_POR_WEB = 20
EVENTOS_DIA_POR_SERVIDOR = 1000
MS_LENTISIMA = 5000
RETENCION_HORAS_S = 90 * 86400
RETENCION_EVENTOS_S = 180 * 86400
ETIQUETA = re.compile(r"[A-Za-z0-9._~:/?#@!$&*+,;=%-]{8,300}")          # lo que admite el CHECK de `webs.etiqueta`
_ESTADOS = ("ok", "mal", "caida")


def _entero(v, minimo, maximo):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and minimo <= v <= maximo else None


def clase(r):
    """«ok», «mal» o «caida» de un sondeo (`codigo`, `ms`, `error` de `medir_web`)."""
    codigo = _entero(r.get("codigo"), 100, 599)
    if codigo is None or codigo >= 500:
        return "caida"
    ms = _entero(r.get("ms"), 0, 10 ** 8)
    if 200 <= codigo < 400 or codigo in (401, 403):
        return "mal" if ms is not None and ms > MS_LENTISIMA else "ok"
    return "mal"


def _evento(con, sid, clave, evento, codigo, ts):
    dia = ts - ts % 86400
    primer_cayo = evento == "cayo" and not con.execute("SELECT 1 FROM eventos_web WHERE servidor_id=? AND clave=? AND ts>=? AND evento='cayo'", (sid, clave, dia)).fetchone()
    if (not primer_cayo and con.execute("SELECT COUNT(*) FROM eventos_web WHERE servidor_id=? AND clave=? AND ts>=?", (sid, clave, dia)).fetchone()[0] >= EVENTOS_DIA_POR_WEB) \
            or con.execute("SELECT COUNT(*) FROM eventos_web WHERE servidor_id=? AND ts>=?", (sid, dia)).fetchone()[0] >= EVENTOS_DIA_POR_SERVIDOR:
        rec.descartar(con, "eventos_web_sobre_tope", sid)
        return
    con.execute("INSERT OR IGNORE INTO eventos_web(servidor_id, ts, clave, evento, codigo) VALUES(?,?,?,?,?)", (sid, ts, clave, evento, codigo))


def _cuenta(con, tabla, sid, clave, periodo, c, ms):
    columna = {"ok": "ok", "mal": "mal", "caida": "caidas"}[c]
    campo = "hora" if tabla == "horas_web" else "dia"
    con.execute(f"INSERT INTO {tabla}(servidor_id, clave, {campo}, {columna}, ms_max) VALUES(?,?,?,1,?) ON CONFLICT(servidor_id, clave, {campo}) DO UPDATE SET "
                f"{columna} = {columna} + 1, ms_max = COALESCE(MAX(ms_max, excluded.ms_max), ms_max, excluded.ms_max)", (sid, clave, periodo, ms))


def anota_web(con, sid, url, r, ts):
    """Anota un sondeo de una web (dentro de la transacción de quien llama). Devuelve "base", "anotada" o None si no vale."""
    if not isinstance(url, str) or not ETIQUETA.fullmatch(url) or url_valida(url) or not isinstance(r, dict):
        rec.descartar(con, "web_no_valida", sid)
        return None
    ts = int(ts)
    clave = rec.clave_de(con, "web", url)
    c = clase(r)
    ms, codigo = _entero(r.get("ms"), 0, 10 ** 8), _entero(r.get("codigo"), 100, 599)
    cert = _entero(r.get("cert_dias"), -36500, 36500)
    _cuenta(con, "horas_web", sid, clave, ts - ts % 3600, c, ms)
    _cuenta(con, "dias_web", sid, clave, ts - ts % 86400, c, ms)
    fila = con.execute("SELECT estado, pend_estado, pend_n, pend_desde FROM webs WHERE servidor_id=? AND clave=?", (sid, clave)).fetchone()
    if fila is None:
        con.execute("INSERT INTO webs(servidor_id, clave, etiqueta, primera, ultima, estado, codigo, ms, cert_dias, desde, pend_n) VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                    (sid, clave, url, ts, ts, c, codigo, ms, cert, ts))
        return "base"
    estado, pend, pend_n, pend_desde = fila
    nuevo_estado, desde_sql, pend_sql = estado, None, None
    if c == estado:
        pend_sql = (None, 0, None)
    else:
        if pend == c:
            pend_n, pend_desde = pend_n + 1, pend_desde or ts
        else:
            pend, pend_n, pend_desde = c, 1, ts
        pend_sql = (pend, pend_n, pend_desde)
        if pend_n >= CONFIRMA[c] and (CONFIRMA[c] == 1 or ts - pend_desde >= ESPERA_S):
            nuevo_estado, desde_sql, pend_sql = c, ts, (None, 0, None)
            if c == "ok":
                _evento(con, sid, clave, "volvio", codigo, ts)
            elif c == "caida":
                _evento(con, sid, clave, "cayo", codigo, ts)
            else:
                _evento(con, sid, clave, "degradada" if estado == "ok" else "volvio", codigo, ts)       # caída → «mal»: responde de nuevo, aunque con error
    con.execute("UPDATE webs SET ultima=?, estado=?, codigo=?, ms=?, cert_dias=?, desde=COALESCE(?, desde), pend_estado=?, pend_n=?, pend_desde=? WHERE servidor_id=? AND clave=?",
                (ts, nuevo_estado, codigo, ms, cert, desde_sql, *pend_sql, sid, clave))
    return "anotada"


def sincronizar(con, vigentes):
    """Borra lo de las webs que ya no se vigilan. `vigentes` = {servidor_id: {urls}} de la configuración: todo lo que no esté ahí (webs quitadas, servidores sin webs o
    retirados) pierde su estado, sus eventos y sus resúmenes (la URL es dato personal). Devuelve cuántas webs borró."""
    borrar = []
    for sid, clave, url in con.execute("SELECT servidor_id, clave, etiqueta FROM webs").fetchall():
        if url not in vigentes.get(sid, ()):
            borrar.append((sid, clave))
    for sid, clave in borrar:
        for tabla in ("webs", "eventos_web", "horas_web", "dias_web"):
            con.execute(f"DELETE FROM {tabla} WHERE servidor_id=? AND clave=?", (sid, clave))
    return len(borrar)


def olvidar(con, servidor_id=None):
    """Borra todo el seguimiento de webs de un servidor o de todos. Devuelve cuántas webs."""
    n = con.execute("SELECT COUNT(*) FROM webs" + (" WHERE servidor_id=?" if servidor_id else ""), (servidor_id,) if servidor_id else ()).fetchone()[0]
    for tabla in ("webs", "eventos_web", "horas_web", "dias_web"):
        con.execute(f"DELETE FROM {tabla}" + (" WHERE servidor_id=?" if servidor_id else ""), (servidor_id,) if servidor_id else ())
    return n


def purgar(con, ahora):
    """Retención: horas de más de 90 días y eventos de más de 180; marcas de un reloj adelantado traídas al presente."""
    n = con.execute("DELETE FROM horas_web WHERE hora < ?", (ahora - RETENCION_HORAS_S,)).rowcount
    n += con.execute("DELETE FROM eventos_web WHERE ts < ?", (ahora - RETENCION_EVENTOS_S,)).rowcount
    n += con.execute("DELETE FROM horas_web WHERE hora > ?", (ahora + 3600,)).rowcount
    n += con.execute("DELETE FROM dias_web WHERE dia > ?", (ahora + 86400,)).rowcount
    n += con.execute("DELETE FROM eventos_web WHERE ts > ?", (ahora + 3600,)).rowcount
    con.execute("UPDATE webs SET ultima=?, primera=MIN(primera, ?), desde=MIN(desde, ?), pend_desde=MIN(COALESCE(pend_desde, ?), ?) WHERE ultima > ?",
                (ahora, ahora, ahora, ahora, ahora, ahora + 3600))
    return n


def _disponibilidad(ok, mal, caidas):
    n = (ok or 0) + (mal or 0) + (caidas or 0)
    return None if n == 0 else round(100.0 * ((ok or 0) + (mal or 0)) / n, 2)      # «mal» responde: la aplicación está viva; solo la caída resta


def lista(con, servidor_id, ahora):
    """Las webs de un servidor con su estado confirmado y su disponibilidad observada en 24 h, 7 días y 30 días (None = sin datos)."""
    out = []
    for f in con.execute("SELECT clave, etiqueta, estado, codigo, ms, cert_dias, desde, ultima, primera FROM webs WHERE servidor_id=? ORDER BY etiqueta", (servidor_id,)).fetchall():
        d = {"url": f["etiqueta"], "estado": f["estado"], "codigo": f["codigo"], "ms": f["ms"], "cert_dias": f["cert_dias"], "desde": f["desde"], "ultima": f["ultima"]}
        for nombre, segundos, tabla, campo in (("disp_24h", 86400, "horas_web", "hora"), ("disp_7d", 7 * 86400, "horas_web", "hora"), ("disp_30d", 30 * 86400, "dias_web", "dia")):
            ok, mal, caidas = con.execute(f"SELECT SUM(ok), SUM(mal), SUM(caidas) FROM {tabla} WHERE servidor_id=? AND clave=? AND {campo} >= ?",
                                          (servidor_id, f["clave"], ahora - segundos)).fetchone()
            d[nombre] = _disponibilidad(ok, mal, caidas)
        out.append(d)
    return out


def eventos(con, servidor_id, desde, hasta, limite=500):
    """Eventos de las webs de un servidor entre dos instantes (más recientes primero, con su URL si la web sigue vigilada)."""
    return [{"ts": f["ts"], "evento": f["evento"], "codigo": f["codigo"], "url": f["etiqueta"]} for f in con.execute(
        "SELECT e.ts, e.evento, e.codigo, w.etiqueta FROM eventos_web e LEFT JOIN webs w ON w.servidor_id = e.servidor_id AND w.clave = e.clave "
        "WHERE e.servidor_id=? AND e.ts>=? AND e.ts<? ORDER BY e.ts DESC LIMIT ?", (servidor_id, int(desde), int(hasta), int(limite))).fetchall()]
