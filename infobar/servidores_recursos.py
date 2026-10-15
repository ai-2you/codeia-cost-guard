"""Recursos de un servidor (contenedores, servicios Swarm y servicios Compose) y sus eventos, en `servidores.sqlite`.

· La etiqueta (el nombre) es un DATO PERSONAL: puede ser el nombre de un cliente. Es lo único que lleva texto; la clave de cada recurso y de cada evento
  es un hash con sal propia de la instalación, así que los eventos (180 días) no dicen de qué recurso hablan cuando la etiqueta ya se borró (los recursos
  inactivos se purgan a los 30 días). Se borran con el servidor, con `olvidar_recursos` y al retirar el servidor de la configuración.
· Solo se concluye algo de lecturas COMPLETAS (todas las líneas pedidas llegaron y valen). Una aparición o una desaparición se confirma tras dos lecturas
  completas separadas al menos `ESPERA_S`: un fallo SSH, una sección recortada o un contenedor efímero no fabrican eventos. La primera lectura de un servidor
  (o la primera tras olvidar sus recursos) es la línea base: puebla sin eventos.
· Presupuestos: 500 recursos por servidor, 100 eventos por servidor, día y tipo de evento y 10 por recurso y día; lo que sobra se cuenta en `meta`, y un recurso
  que agota su presupuesto deja un único evento `inestable`."""
import hashlib
import os
import re

from .servidores_inventario import reclama_swarm, recursos as agrega

ESPERA_S = 600
MAX_RECURSOS = 500
EVENTOS_DIA_POR_TIPO = 100
EVENTOS_DIA_POR_CLAVE = 10
RETENCION_EVENTOS_S = 180 * 86400
RETENCION_INACTIVOS_S = 30 * 86400
BUCLE_RECIENTE_S = 600
_ETIQUETA_OK = re.compile(r"[A-Za-z0-9_./-]{1,130}")
EVENTOS = ("aparecio", "desaparecio", "cambio_estado", "inestable", "reinicios_en_bucle")
SEVERIDAD = {"aparecio": "info", "desaparecio": "aviso", "inestable": "aviso", "reinicios_en_bucle": "critica"}
_MAL = frozenset({"mal", "caido", "reiniciando"})


def descartar(con, motivo, sid):
    """Cuenta lo descartado por motivo y servidor (acotado: motivos fijos x servidores de la config)."""
    con.execute("INSERT INTO meta(clave, valor) VALUES(?, '1') ON CONFLICT(clave) DO UPDATE SET valor = CAST(valor AS INTEGER) + 1",
                (f"descartes:{motivo}:{sid}",))


def _sal(con):
    fila = con.execute("SELECT valor FROM meta WHERE clave='sal_recursos'").fetchone()
    if fila:
        return fila[0]
    sal = os.urandom(16).hex()
    con.execute("INSERT INTO meta(clave, valor) VALUES('sal_recursos', ?)", (sal,))
    return sal


def clave_de(con, tipo, nombre):
    return hashlib.sha256(f"{_sal(con)}\0{tipo}\0{nombre}".encode()).hexdigest()[:32]


def _evento(con, sid, tipo, clave, evento, ts, severidad=None):
    """Anota un evento si cabe en los presupuestos. Devuelve False si no cupo (el llamador decide si reintenta). Los presupuestos son por servidor, día,
    evento Y tipo de recurso: los contenedores sueltos y efímeros no pueden gastar el de los servicios que importan."""
    dia = ts - ts % 86400
    if con.execute("SELECT COUNT(*) FROM eventos WHERE servidor_id=? AND evento=? AND tipo=? AND ts>=?", (sid, evento, tipo, dia)).fetchone()[0] \
            >= EVENTOS_DIA_POR_TIPO:
        descartar(con, "eventos_sobre_tope", sid)
        return False
    if con.execute("SELECT COUNT(*) FROM eventos WHERE servidor_id=? AND clave=? AND ts>=?", (sid, clave, dia)).fetchone()[0] >= EVENTOS_DIA_POR_CLAVE:
        if not con.execute("SELECT 1 FROM eventos WHERE servidor_id=? AND clave=? AND evento='inestable' AND ts>=?", (sid, clave, dia)).fetchone():
            con.execute("INSERT OR IGNORE INTO eventos(servidor_id, ts, clave, tipo, evento, severidad) VALUES(?,?,?,?, 'inestable', 'aviso')",
                        (sid, ts, clave, tipo))
        descartar(con, "eventos_sobre_tope", sid)
        return False
    con.execute("INSERT OR IGNORE INTO eventos(servidor_id, ts, clave, tipo, evento, severidad) VALUES(?,?,?,?,?,?)",
                (sid, ts, clave, tipo, evento, severidad or SEVERIDAD[evento]))
    return True


def _hueco(con, sid, vistos, ts):
    """True si cabe un recurso nuevo; si el servidor está lleno expulsa el inactivo más antiguo que no se esté viendo en esta lectura ni tenga eventos recientes."""
    total = con.execute("SELECT COUNT(*) FROM recursos WHERE servidor_id=?", (sid,)).fetchone()[0]
    if total < MAX_RECURSOS:
        return True
    for tipo, clave in con.execute(
            "SELECT tipo, clave FROM recursos WHERE servidor_id=? AND activo=0 AND clave NOT IN (SELECT clave FROM eventos WHERE servidor_id=? AND ts>?) "
            "ORDER BY ultima LIMIT 50", (sid, sid, ts - 7 * 86400)).fetchall():
        if (tipo, clave) in vistos:
            continue
        con.execute("DELETE FROM recursos WHERE servidor_id=? AND tipo=? AND clave=?", (sid, tipo, clave))
        descartar(con, "recursos_expulsados", sid)
        return True
    return False


def anota_inventario(con, sid, inv, swarm, ts):
    """Anota una lectura de inventario (dentro de la transacción de quien llama). Devuelve "base", "anotada" o None si no vale para concluir nada.

    Una lectura con contenedores descartados o recortada actualiza lo que ve pero no concluye desapariciones; una lectura de un nodo Swarm sin la lista
    de servicios no vale (la identidad de los servicios dependería de ella)."""
    if not isinstance(inv, dict) or inv.get("fuente") != "docker":
        return None
    if not inv.get("completo"):
        descartar(con, "inventario_incompleto", sid)
        return None
    swarm = swarm if isinstance(swarm, dict) else {}
    if reclama_swarm(inv) and (not swarm or len(swarm) >= 300):
        descartar(con, "inventario_sin_servicios_swarm", sid)       # worker o lista de servicios recortada: la identidad cambiaría de una lectura a otra
        return None
    if inv.get("descartados"):
        descartar(con, "inventario_contenedores_descartados", sid)
    recs = agrega(inv, swarm)
    largos = [k for k in recs if len(k[1]) > 130 or not _ETIQUETA_OK.fullmatch(k[1])]
    for k in largos:                       # un nombre que la base no admite descarta ese recurso, no toda la lectura (un contenedor hostil no ciega el inventario)
        descartar(con, "recurso_nombre_no_valido", sid)
        del recs[k]
    filas = {(t, c): r for t, c, *r in con.execute(
        "SELECT tipo, clave, estado, reinicios, activo, visto_n, visto_desde, ausente_n, ausente_desde, bucle_n FROM recursos WHERE servidor_id=?", (sid,))}
    marca = f"inv_base:{sid}"
    base = not filas and not con.execute("SELECT 1 FROM meta WHERE clave=?", (marca,)).fetchone()
    if base:
        con.execute("INSERT OR IGNORE INTO meta(clave, valor) VALUES(?, ?)", (marca, str(ts)))
    silencioso = base or bool(inv.get("truncado"))                  # un recorte puede dejar entrar «nuevos» que solo son los que antes no cabían
    ahora_srv = inv.get("ahora_srv")
    vistos = set()
    claves = {k: clave_de(con, *k) for k in recs}
    vistos = {(t, claves[(t, n)]) for (t, n) in recs}
    for (tipo, nombre), r in sorted(recs.items()):
        clave = claves[(tipo, nombre)]
        previa = filas.get((tipo, clave))
        if previa is None:
            if not _hueco(con, sid, vistos, ts):
                descartar(con, "recursos_sobre_tope", sid)
                continue
            con.execute("INSERT INTO recursos(servidor_id, tipo, clave, etiqueta, primera, ultima, activo, estado, salud, reinicios, inicio, "
                        "visto_n, visto_desde, ausente_n, ausente_desde, bucle_n) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,0)",
                        (sid, tipo, clave, nombre, ts, ts, 1 if silencioso else 0, r["estado"], r["salud"], r["reinicios"], r["inicio"], 1, ts))
            continue
        estado, reinicios, activo, visto_n, visto_desde, _, _, bucle_n = previa
        nuevo_activo, nuevo_visto_n, nuevo_visto_desde = activo, visto_n, visto_desde
        if not activo:
            nuevo_visto_n = visto_n + 1 if visto_n else 1
            nuevo_visto_desde = visto_desde if visto_n and visto_desde else ts
            if nuevo_visto_n >= 2 and ts - nuevo_visto_desde >= ESPERA_S:
                if _evento(con, sid, tipo, clave, "aparecio", ts):
                    nuevo_activo = 1                                 # si el presupuesto del día no deja anotarlo, sigue pendiente y se reintenta
        else:
            nuevo_visto_n, nuevo_visto_desde = 0, None
            if r["estado"] != estado:
                _evento(con, sid, tipo, clave, "cambio_estado", ts, "aviso" if r["estado"] in _MAL else "info")
        nuevo_bucle = 0
        # la edad del arranque se mide con el reloj del servidor (StartedAt es suyo), no con el del Mac
        edad = (ahora_srv - r["inicio"]) if (ahora_srv is not None and r["inicio"] is not None) else None
        if r["reinicios"] is not None and reinicios is not None and r["reinicios"] > reinicios and edad is not None and 0 <= edad <= BUCLE_RECIENTE_S:
            nuevo_bucle = bucle_n + 1
            if nuevo_bucle == 2 and nuevo_activo:
                _evento(con, sid, tipo, clave, "reinicios_en_bucle", ts)
        con.execute("UPDATE recursos SET ultima=?, activo=?, estado=?, salud=?, reinicios=?, inicio=?, visto_n=?, visto_desde=?, ausente_n=0, "
                    "ausente_desde=NULL, bucle_n=? WHERE servidor_id=? AND tipo=? AND clave=?",
                    (ts, nuevo_activo, r["estado"], r["salud"], r["reinicios"], r["inicio"], nuevo_visto_n, nuevo_visto_desde, nuevo_bucle, sid, tipo, clave))
    if not inv.get("truncado") and not inv.get("descartados"):
        for (tipo, clave), previa in filas.items():
            if (tipo, clave) in vistos:
                continue
            activo, ausente_n, ausente_desde = previa[2], previa[5], previa[6]
            if not activo:
                con.execute("UPDATE recursos SET visto_n=0, visto_desde=NULL WHERE servidor_id=? AND tipo=? AND clave=?", (sid, tipo, clave))
                continue
            n = ausente_n + 1
            desde = ausente_desde if ausente_n and ausente_desde else ts
            if n >= 2 and ts - desde >= ESPERA_S and _evento(con, sid, tipo, clave, "desaparecio", ts):
                con.execute("UPDATE recursos SET activo=0, ausente_n=0, ausente_desde=NULL WHERE servidor_id=? AND tipo=? AND clave=?", (sid, tipo, clave))
            else:
                con.execute("UPDATE recursos SET ausente_n=?, ausente_desde=? WHERE servidor_id=? AND tipo=? AND clave=?", (n, desde, sid, tipo, clave))
    return "base" if base else "anotada"


def purgar(con, ahora, retirados=()):
    """Retención: inactivos de más de 30 días, eventos de más de 180, todo lo de los servidores retirados y las marcas de un reloj adelantado."""
    n = con.execute("DELETE FROM recursos WHERE activo=0 AND ultima < ?", (ahora - RETENCION_INACTIVOS_S,)).rowcount
    n += con.execute("DELETE FROM eventos WHERE ts < ?", (ahora - RETENCION_EVENTOS_S,)).rowcount
    for sid in retirados:
        n += con.execute("DELETE FROM recursos WHERE servidor_id=?", (sid,)).rowcount
        n += con.execute("DELETE FROM eventos WHERE servidor_id=?", (sid,)).rowcount
    con.execute("UPDATE recursos SET ultima=?, primera=MIN(primera, ?), visto_desde=MIN(COALESCE(visto_desde, ?), ?), "
                "ausente_desde=MIN(COALESCE(ausente_desde, ?), ?) WHERE ultima > ?", (ahora, ahora, ahora, ahora, ahora, ahora, ahora + 3600))
    con.execute("DELETE FROM eventos WHERE ts > ?", (ahora + 3600,))
    return n


def olvidar(con, servidor_id=None):
    """Borra los recursos y los eventos de un servidor o de todos. Devuelve (recursos, eventos)."""
    if servidor_id is None:
        con.execute("DELETE FROM meta WHERE clave LIKE 'inv_base:%'")
        return con.execute("DELETE FROM recursos").rowcount, con.execute("DELETE FROM eventos").rowcount
    con.execute("DELETE FROM meta WHERE clave=?", (f"inv_base:{servidor_id}",))
    return (con.execute("DELETE FROM recursos WHERE servidor_id=?", (servidor_id,)).rowcount,
            con.execute("DELETE FROM eventos WHERE servidor_id=?", (servidor_id,)).rowcount)
