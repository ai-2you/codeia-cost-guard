"""Copias de seguridad que cuenta el panel (Dokploy), en `servidores.sqlite`: la última copia BUENA de cada base de datos y volumen.

· Dokploy solo conserva las últimas ejecuciones de cada configuración, así que «la última buena» tiene que sobrevivir a esa rotación: aquí se guarda `ultima_ok`, la `previa_ok`
  y la cadencia observada (`intervalo_s`, mediana de los huecos entre copias buenas vistas). Una copia semanal no está «antigua» a los 3 días.
· Cada copia es una identidad propia (servicio, tipo, destino, base de datos y variante): dos destinos de una misma base no se tapan entre sí.
· Solo «done» cuenta como buena (ni «running» colgada ni «cancelled»). Un recurso que NUNCA tuvo una copia buena se muestra aparte y no genera aviso: no se puede distinguir
  «no configurada» de «nunca le tocó».
· La etiqueta (nombre del servicio) es dato personal: se borra con el servidor, con `--olvidar-recursos` y 30 días después de la última vez que se vio."""
import statistics

from . import servidores_recursos as rec

ANTIGUA_MIN_S = 48 * 3600
SIN_CADENCIA_S = 8 * 86400
FACTOR_CADENCIA = 2.5
MAX_COPIAS_SERVIDOR = 500
RETENCION_S = 30 * 86400
MIN_INTERVALO_S, MAX_INTERVALO_S = 60, 366 * 86400
VISTAS_PARA_CADENCIA = 10


def _etiqueta(nombre, clave):
    ok = isinstance(nombre, str) and 1 <= len(nombre) <= 130 and all(c.isascii() and (c.isalnum() or c in "_./-") for c in nombre)
    return nombre if ok else f"copia-{clave[:8]}"


def identidad(f):
    return "|".join((f["dueno"], f["tipo"], f["destino"], f["bd"], f["variante"]))


def _mediana_de_huecos(dones):
    dones = sorted(set(dones))[-VISTAS_PARA_CADENCIA:]
    huecos = [b - a for a, b in zip(dones, dones[1:]) if b - a > 0]
    if not huecos:
        return None
    m = int(statistics.median(huecos))
    return m if MIN_INTERVALO_S <= m <= MAX_INTERVALO_S else None


def anota(con, sid, filas, ahora):
    """Anota las ejecuciones de la última lectura buena del panel (`filas` = copias normalizadas por `panel_dokploy`). Devuelve cuántas identidades se tocaron."""
    ahora = int(ahora)
    grupos = {}
    for f in filas:
        grupos.setdefault(identidad(f), []).append(f)
    n = 0
    for ident, runs in grupos.items():
        clave = rec.clave_de(con, "copia", ident)
        runs.sort(key=lambda r: r["ts"])
        nuevas_ok = [int(r["ts"]) for r in runs if r["estado"] == "done"]
        validos = [r for r in runs if r["estado"] is not None]
        ultimo = validos[-1] if validos else runs[-1]                           # el último estado CONOCIDO: un «no sé» no borra lo que ya se sabía
        previo = con.execute("SELECT ultima_ok, previa_ok, intervalo_s, ultimo_ts FROM copias WHERE servidor_id=? AND clave=?", (sid, clave)).fetchone()
        conocidas = set(nuevas_ok) | ({previo["ultima_ok"]} if previo and previo["ultima_ok"] else set()) | ({previo["previa_ok"]} if previo and previo["previa_ok"] else set())
        ordenadas = sorted(conocidas)
        ultima_ok = ordenadas[-1] if ordenadas else None
        previa_ok = ordenadas[-2] if len(ordenadas) > 1 else None
        intervalo = _mediana_de_huecos(ordenadas) or (previo["intervalo_s"] if previo else None)
        estado = ultimo["estado"]
        ts_ultimo = int(ultimo["ts"])
        if previo and previo["ultimo_ts"] and previo["ultimo_ts"] > ts_ultimo:
            estado, ts_ultimo = None, previo["ultimo_ts"]                       # la lectura trae ejecuciones más viejas que la última vista: no se retrocede
        if previo is None:
            if con.execute("SELECT COUNT(*) FROM copias WHERE servidor_id=?", (sid,)).fetchone()[0] >= MAX_COPIAS_SERVIDOR:
                rec.descartar(con, "copias_sobre_tope", sid)
                continue
            con.execute("INSERT INTO copias(servidor_id, clave, dueno, tipo, etiqueta, primera, vista, ultima_ok, previa_ok, intervalo_s, ultimo_estado, ultimo_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sid, clave, runs[0]["dueno"], runs[0]["tipo"], _etiqueta(next((r["nombre"] for r in runs if r["nombre"]), None), clave), ahora, ahora, ultima_ok, previa_ok,
                         intervalo, estado, ts_ultimo))
        else:
            con.execute("UPDATE copias SET vista=?, ultima_ok=?, previa_ok=?, intervalo_s=?, ultimo_estado=COALESCE(?, ultimo_estado), ultimo_ts=? WHERE servidor_id=? AND clave=?",
                        (ahora, ultima_ok, previa_ok, intervalo, estado, ts_ultimo, sid, clave))
        n += 1
    return n


def evaluar(fila, ahora):
    """«al_dia», «antigua» o «sin_buena» (nunca tuvo una copia buena). La antigüedad se mide contra la cadencia observada."""
    if fila["ultima_ok"] is None:
        return "sin_buena"
    umbral = max(ANTIGUA_MIN_S, FACTOR_CADENCIA * fila["intervalo_s"]) if fila["intervalo_s"] else SIN_CADENCIA_S
    return "antigua" if ahora - fila["ultima_ok"] > umbral else "al_dia"


def lista(con, sid, ahora):
    """Copias de un servidor con su evaluación, las que piden atención primero."""
    orden = {"antigua": 0, "sin_buena": 1, "al_dia": 2}
    out = []
    for f in con.execute("SELECT clave, dueno, tipo, etiqueta, ultima_ok, previa_ok, intervalo_s, ultimo_estado, ultimo_ts, vista, primera FROM copias WHERE servidor_id=? ORDER BY etiqueta", (sid,)):
        d = {k: f[k] for k in f.keys()}
        d["evaluacion"] = evaluar(f, ahora)
        out.append(d)
    out.sort(key=lambda d: (orden[d["evaluacion"]], d["etiqueta"]))
    return out


def sincronizar(con, sid, duenos_vigentes):
    """Quita las copias de recursos que ya no existen en el panel (se recrean con otro id): salen de la evaluación al momento. Devuelve cuántas."""
    n = 0
    for clave, dueno in con.execute("SELECT clave, dueno FROM copias WHERE servidor_id=?", (sid,)).fetchall():
        if dueno not in duenos_vigentes:
            n += con.execute("DELETE FROM copias WHERE servidor_id=? AND clave=?", (sid, clave)).rowcount
    return n


def purgar(con, ahora, retirados=()):
    n = con.execute("DELETE FROM copias WHERE vista < ?", (ahora - RETENCION_S,)).rowcount
    for sid in retirados:
        n += con.execute("DELETE FROM copias WHERE servidor_id=?", (sid,)).rowcount
    con.execute("UPDATE copias SET vista=?, primera=MIN(primera, ?) WHERE vista > ?", (ahora, ahora, ahora + 3600))
    con.execute("UPDATE copias SET ultima_ok=? WHERE ultima_ok > ?", (ahora, ahora + 3600))
    con.execute("UPDATE copias SET previa_ok=? WHERE previa_ok > ?", (ahora, ahora + 3600))
    con.execute("UPDATE copias SET ultimo_ts=? WHERE ultimo_ts > ?", (ahora, ahora + 3600))
    return n


def olvidar(con, servidor_id=None):
    n = con.execute("SELECT COUNT(*) FROM copias" + (" WHERE servidor_id=?" if servidor_id else ""), (servidor_id,) if servidor_id else ()).fetchone()[0]
    con.execute("DELETE FROM copias" + (" WHERE servidor_id=?" if servidor_id else ""), (servidor_id,) if servidor_id else ())
    return n
