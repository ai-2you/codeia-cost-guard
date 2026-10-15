"""Historial de los servidores en `DATOS/servidores.sqlite`: cómo ha ido cada uno y quién ha intentado entrar.

· Base aparte de `consumo.sqlite` (se abre con el mismo núcleo seguro). Un solo escritor: el hilo trabajador de la barra, que llama a
  `registrar` DESPUÉS de guardar la caché y fuera de su cerrojo; el ciclo no depende de esta base y un fallo suya nunca lo para.
· `ts` es el reloj del Mac. Una fila por lectura buena (dedupe por `ok_en`) y una de estado, sin métricas, por intento fallido; como mucho una
  por franja de 5 min. Crudo 14 días; `horas_servidor` es permanente y guarda picos, nunca sumas de ventanas solapadas.
· Las IPs de quien intenta entrar (datos personales) caducan a los N días (7–90) contados desde la última vez: la caducidad no se almacena,
  se calcula con la retención vigente y se filtra al leer. Se borran al quitar el servidor, al apagar `guardar_ips` y con `olvidar_ips`.
· Identidad por `id` estable: renombrar un servidor cambia su etiqueta, no su historial."""
import os
import sqlite3
import time

from . import plataforma, rutas
from .servidores_datos import ESTADOS, FueraDeRango, conjunto_canonico, horas_de, muestra_de
from .sqlite_base import ErrorAlmacen, abrir_base, _rollback, _sentencias, _tablas, _version

RETENCION_CRUDO_S = 14 * 86400
FRANJA_S = 270                      # una muestra por franja de ~5 min aunque el servidor esté en incidencia y se lea cada minuto
RETENCION_IPS_DIAS = (7, 90, 90)    # mínimo, máximo, por defecto
MAX_ALTAS_DIA = 500                 # IPs nuevas por servidor y día: un atacante que rota IP no desplaza al persistente
MAX_IPS = 5000
VACUUM_CADA_S = 30 * 86400
COPIA_VIDA_S = 30 * 86400
TS_MIN, TS_MAX = 946_684_800, 4_102_444_800
_RANGO = {"verde": 1, "ambar": 2, "rojo": 3, "gris": 0, "error": 4}

METRICAS = ("carga_pct", "mem_pct", "disco_pct", "uptime_s", "intentos_24h", "intentos_1h", "baneadas", "sesiones", "sesiones_ajenas",
            "puertos_n", "puertos_expuestos", "servicios_n", "servicios_caidos", "webs_n", "webs_caidas", "web_ms_max",
            "faltas_proteccion", "reinicio_pendiente")

ESQUEMA_V1 = f"""
CREATE TABLE meta(clave TEXT PRIMARY KEY, valor TEXT NOT NULL) STRICT;
CREATE TABLE servidores(
    id TEXT PRIMARY KEY CHECK(length(id) BETWEEN 8 AND 40),
    nombre TEXT NOT NULL CHECK(length(nombre) BETWEEN 1 AND 40),
    creado_en INTEGER NOT NULL, retirado_en INTEGER,
    ok_en INTEGER NOT NULL DEFAULT 0, intento_en INTEGER NOT NULL DEFAULT 0, muestra_en INTEGER NOT NULL DEFAULT 0,
    altas_dia INTEGER NOT NULL DEFAULT 0, altas_dia_num INTEGER NOT NULL DEFAULT 0) STRICT;
CREATE TABLE muestras(
    servidor_id TEXT NOT NULL REFERENCES servidores(id) ON DELETE CASCADE,
    ts INTEGER NOT NULL CHECK(ts BETWEEN {TS_MIN} AND {TS_MAX}),
    estado TEXT NOT NULL CHECK(estado IN ('verde','ambar','rojo','gris','error')),
    carga_pct REAL CHECK(carga_pct BETWEEN 0 AND 100), mem_pct REAL CHECK(mem_pct BETWEEN 0 AND 100),
    disco_pct REAL CHECK(disco_pct BETWEEN 0 AND 100), uptime_s INTEGER CHECK(uptime_s BETWEEN 0 AND 1000000000),
    intentos_24h INTEGER CHECK(intentos_24h BETWEEN 0 AND 1000000), intentos_1h INTEGER CHECK(intentos_1h BETWEEN 0 AND 1000000),
    baneadas INTEGER CHECK(baneadas BETWEEN 0 AND 1000000), sesiones INTEGER CHECK(sesiones BETWEEN 0 AND 1000000),
    sesiones_ajenas INTEGER CHECK(sesiones_ajenas BETWEEN 0 AND 1000000), puertos_n INTEGER CHECK(puertos_n BETWEEN 0 AND 1000000),
    puertos_expuestos INTEGER CHECK(puertos_expuestos BETWEEN 0 AND 1000000),
    servicios_n INTEGER CHECK(servicios_n BETWEEN 0 AND 1000000), servicios_caidos INTEGER CHECK(servicios_caidos BETWEEN 0 AND 1000000),
    webs_n INTEGER CHECK(webs_n BETWEEN 0 AND 1000000), webs_caidas INTEGER CHECK(webs_caidas BETWEEN 0 AND 1000000),
    web_ms_max INTEGER CHECK(web_ms_max BETWEEN 0 AND 10000000), faltas_proteccion INTEGER CHECK(faltas_proteccion BETWEEN 0 AND 1000000),
    reinicio_pendiente INTEGER CHECK(reinicio_pendiente IN (0, 1)),
    PRIMARY KEY(servidor_id, ts)) STRICT, WITHOUT ROWID;
CREATE TABLE horas_servidor(
    servidor_id TEXT NOT NULL REFERENCES servidores(id) ON DELETE CASCADE,
    hora INTEGER NOT NULL CHECK(hora BETWEEN {TS_MIN} AND {TS_MAX}),
    n INTEGER NOT NULL DEFAULT 0, errores INTEGER NOT NULL DEFAULT 0,
    carga_max REAL, mem_max REAL, disco_max REAL, intentos_pico_24h INTEGER, intentos_pico_1h INTEGER, baneadas_max INTEGER,
    sesiones_ajenas_max INTEGER, estado_peor TEXT NOT NULL CHECK(estado_peor IN ('verde','ambar','rojo','gris','error')),
    PRIMARY KEY(servidor_id, hora)) STRICT, WITHOUT ROWID;
CREATE TABLE ips_atacantes(
    servidor_id TEXT NOT NULL REFERENCES servidores(id) ON DELETE CASCADE,
    ip TEXT NOT NULL CHECK(length(ip) BETWEEN 3 AND 45),
    primera INTEGER NOT NULL, ultima INTEGER NOT NULL, max_24h INTEGER NOT NULL CHECK(max_24h BETWEEN 0 AND 1000000),
    horas_vista INTEGER NOT NULL CHECK(horas_vista >= 1), ultima_hora INTEGER NOT NULL,
    PRIMARY KEY(servidor_id, ip)) STRICT, WITHOUT ROWID;
CREATE INDEX ips_por_ultima ON ips_atacantes(servidor_id, ultima);
"""
ESQUEMA_V2 = """
ALTER TABLE horas_servidor ADD COLUMN intentos_exactos INTEGER CHECK(intentos_exactos BETWEEN 0 AND 1000000);
ALTER TABLE horas_servidor ADD COLUMN intentos_completa INTEGER CHECK(intentos_completa IN (0, 1));
"""
MIGRACIONES = {1: ESQUEMA_V1, 2: ESQUEMA_V2}       # versión -> script que lleva de la anterior a esa

# Clase de cada columna (publico < interno < personal < secreto). Un test falla si aparece una columna sin clasificar.
_I = "interno"
CLASES_SERVIDORES = {
    "meta": {"clave": _I, "valor": _I},
    "servidores": {"id": _I, "nombre": "personal", "creado_en": _I, "retirado_en": _I, "ok_en": _I, "intento_en": _I, "muestra_en": _I,
                   "altas_dia": _I, "altas_dia_num": _I},
    "muestras": {"servidor_id": _I, "ts": _I, "estado": _I, **{c: _I for c in METRICAS}},
    "horas_servidor": {c: _I for c in ("servidor_id", "hora", "n", "errores", "carga_max", "mem_max", "disco_max", "intentos_pico_24h",
                                       "intentos_pico_1h", "baneadas_max", "sesiones_ajenas_max", "estado_peor", "intentos_exactos", "intentos_completa")},
    "ips_atacantes": {"servidor_id": _I, "ip": "personal", "primera": _I, "ultima": _I, "max_24h": _I, "horas_vista": _I, "ultima_hora": _I},
}


def ruta():
    return os.path.join(rutas.DATOS, "servidores.sqlite")


class SinDatos(Exception):
    """Aún no hay historial de servidores (la base no existe)."""


# ---------------------------------------------------------------- apertura y migración

def _migrar(con, ruta_base):
    version = _version(con)
    ultima = max(MIGRACIONES)
    if version > ultima:
        raise ErrorAlmacen(f"la base de servidores es de una versión más nueva ({version}); actualiza la aplicación")
    if version == ultima:
        return
    if version > 0 and (_tablas(con) - {"sqlite_sequence"}):
        copia_sin_ips(con, os.path.join(os.path.dirname(ruta_base), "copias"))      # copia ANTES de cambiar el esquema, sin IPs
    con.execute("BEGIN IMMEDIATE")
    try:
        actual = _version(con)
        for v in sorted(MIGRACIONES):
            if v > actual:
                for sentencia in _sentencias(MIGRACIONES[v]):
                    con.execute(sentencia)
                if v == 1:
                    con.execute("INSERT INTO meta(clave, valor) VALUES('creado', ?)", (str(int(time.time())),))
                con.execute(f"PRAGMA user_version={v}")
        con.execute("COMMIT")
    except Exception:
        _rollback(con)
        raise


def copia_sin_ips(con, directorio):
    """Copia consistente previa a migrar, SIN las IPs (son datos personales y las copias no se purgan por retención). La copia se hace en
    MEMORIA, se vacía `ips_atacantes` y solo entonces se escribe a disco (`VACUUM INTO` sobre un fichero 0600 ya creado): ningún byte de IP
    toca el disco ni siquiera en un temporal. Caduca a los 30 días (ver `caducar_copias`)."""
    os.makedirs(directorio, mode=0o700, exist_ok=True)
    caducar_copias(directorio)
    base = f"servidores.sqlite.premigracion.{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
    destino, n = os.path.join(directorio, base), 0
    while os.path.exists(destino):
        n += 1
        destino = os.path.join(directorio, f"{base}-{n}")
    tmp = destino + ".tmp"
    memoria = sqlite3.connect(":memory:", isolation_level=None)
    try:
        con.backup(memoria)
        if "ips_atacantes" in _tablas(memoria):
            memoria.execute("DELETE FROM ips_atacantes")
        os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        memoria.execute("VACUUM INTO ?", (tmp,))
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        memoria.close()
    os.replace(tmp, destino)
    return destino


def caducar_copias(directorio, ahora=None):
    """Borra las copias previas a migrar de más de 30 días y los temporales huérfanos de una copia interrumpida."""
    if not os.path.isdir(directorio):
        return
    limite = (ahora or time.time()) - COPIA_VIDA_S
    for f in os.listdir(directorio):
        p = os.path.join(directorio, f)
        if f.startswith("servidores.sqlite.premigracion.") and (os.path.getmtime(p) < limite or f.endswith(".tmp")):
            try:
                os.remove(p)
            except OSError:
                pass


def abrir(solo_lectura=False, espera_ms=2000):
    """Conexión a la base de servidores. Escritura: la crea y migra. Lectura: `mode=ro` y SinDatos si todavía no existe."""
    p = ruta()
    if solo_lectura and not os.path.exists(p):
        raise SinDatos()
    if (not solo_lectura and plataforma.es_checkout_de_desarrollo(rutas.RAIZ)
            and os.path.abspath(rutas.DATOS) == os.path.abspath(plataforma.dir_datos())):
        raise ErrorAlmacen("un checkout de desarrollo no escribe en los datos reales: define COSTGUARD_DATOS")
    return abrir_base(p, _migrar, solo_lectura, espera_ms)


# ---------------------------------------------------------------- escritura

def retencion_dias(valor=None):
    mn, mx, defecto = RETENCION_IPS_DIAS
    try:
        return max(mn, min(mx, int(valor)))
    except (TypeError, ValueError):
        return defecto


def _descartar(con, motivo, sid):
    """Cuenta lo descartado por motivo y servidor (acotado: motivos fijos x servidores de la config)."""
    con.execute("INSERT INTO meta(clave, valor) VALUES(?, '1') ON CONFLICT(clave) DO UPDATE SET valor = CAST(valor AS INTEGER) + 1",
                (f"descartes:{motivo}:{sid}",))


def _alta_servidor(con, sid, nombre, ahora):
    con.execute("INSERT INTO servidores(id, nombre, creado_en) VALUES(?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET nombre = excluded.nombre, retirado_en = NULL", (sid, nombre, ahora))


def _hora_de(ts):
    return ts - ts % 3600


def _suma_hora(con, sid, ts, m, error):
    hora = _hora_de(ts)
    v = [m.get(k) if m else None for k in ("carga_pct", "mem_pct", "disco_pct", "intentos_24h", "intentos_1h", "baneadas", "sesiones_ajenas")]
    peor = m["estado"] if m else "error"
    con.execute(
        "INSERT INTO horas_servidor(servidor_id, hora, n, errores, carga_max, mem_max, disco_max, intentos_pico_24h, intentos_pico_1h, "
        "baneadas_max, sesiones_ajenas_max, estado_peor) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(servidor_id, hora) DO UPDATE SET n = n + excluded.n, errores = errores + excluded.errores, "
        + ", ".join(f"{c} = COALESCE(MAX({c}, excluded.{c}), {c}, excluded.{c})"
                    for c in ("carga_max", "mem_max", "disco_max", "intentos_pico_24h", "intentos_pico_1h", "baneadas_max", "sesiones_ajenas_max"))
        + ", estado_peor = CASE WHEN (CASE excluded.estado_peor WHEN 'verde' THEN 1 WHEN 'ambar' THEN 2 WHEN 'rojo' THEN 3 WHEN 'error' THEN 4 ELSE 0 END) > "
          "(CASE estado_peor WHEN 'verde' THEN 1 WHEN 'ambar' THEN 2 WHEN 'rojo' THEN 3 WHEN 'error' THEN 4 ELSE 0 END) THEN excluded.estado_peor "
          "ELSE estado_peor END",
        (sid, hora, 0 if error else 1, 1 if error else 0, *v, peor))


def _anota_horas(con, sid, horas):
    """Intentos exactos por hora natural. Solo suben: una hora ya anotada no baja aunque el journal del servidor se haya reiniciado, rotado
    o un servidor comprometido mienta sobre el pasado (el valor viejo es el máximo que se llegó a ver). Una hora completa se marca como tal."""
    for hora, n, completa in horas:
        con.execute(
            "INSERT INTO horas_servidor(servidor_id, hora, n, errores, estado_peor, intentos_exactos, intentos_completa) VALUES(?,?,0,0,'gris',?,?) "
            "ON CONFLICT(servidor_id, hora) DO UPDATE SET intentos_exactos = MAX(COALESCE(intentos_exactos, 0), excluded.intentos_exactos), "
            "intentos_completa = MAX(COALESCE(intentos_completa, 0), excluded.intentos_completa)", (sid, hora, n, 1 if completa else 0))


def _anota_ips(con, sid, top, ts, ahora_dia):
    """Altas y actualizaciones monótonas: `max_24h` solo sube, `ultima` es el último ts y `horas_vista` cuenta horas de reloj distintas."""
    fila = con.execute("SELECT altas_dia, altas_dia_num FROM servidores WHERE id=?", (sid,)).fetchone()
    altas = fila[0] if fila[1] == ahora_dia else 0
    hora = _hora_de(ts)
    for ip, n in top:
        previa = con.execute("SELECT ultima_hora FROM ips_atacantes WHERE servidor_id=? AND ip=?", (sid, ip)).fetchone()
        if previa:
            con.execute("UPDATE ips_atacantes SET ultima=MAX(ultima, ?), max_24h=MAX(max_24h, ?), "
                        "horas_vista = horas_vista + CASE WHEN ? > ultima_hora THEN 1 ELSE 0 END, ultima_hora=MAX(ultima_hora, ?) "
                        "WHERE servidor_id=? AND ip=?", (ts, n, hora, hora, sid, ip))
            continue
        if altas >= MAX_ALTAS_DIA:
            _descartar(con, "ips_sobre_tope_diario", sid)
            continue
        total = con.execute("SELECT COUNT(*) FROM ips_atacantes WHERE servidor_id=?", (sid,)).fetchone()[0]
        if total >= MAX_IPS:                              # expulsa las menos relevantes: vistas en menos horas y, a igualdad, las más antiguas
            con.execute("DELETE FROM ips_atacantes WHERE servidor_id=? AND ip IN (SELECT ip FROM ips_atacantes WHERE servidor_id=? "
                        "ORDER BY horas_vista, ultima LIMIT ?)", (sid, sid, total - MAX_IPS + 1))
            _descartar(con, "ips_expulsadas", sid)
        con.execute("INSERT INTO ips_atacantes(servidor_id, ip, primera, ultima, max_24h, horas_vista, ultima_hora) VALUES(?,?,?,?,?,1,?)",
                    (sid, ip, ts, ts, n, hora))
        altas += 1
    con.execute("UPDATE servidores SET altas_dia=?, altas_dia_num=? WHERE id=?", (altas, ahora_dia, sid))


def registrar(con, servidor, entrada, ahora, conocidas=(), guardar_ips=True):
    """Anota la lectura de UN servidor. `servidor` = {id, nombre} de la config; `entrada` = su entrada de la caché ya fusionada.
    Devuelve "buena", "estado" o None (nada que anotar). No lanza por datos del servidor: lo imposible se descarta y se cuenta."""
    sid, nombre = str(servidor.get("id") or ""), str(servidor.get("nombre") or "")
    if not (8 <= len(sid) <= 40 and 1 <= len(nombre) <= 40):
        return None
    ahora = int(ahora)
    conocidas = conjunto_canonico(conocidas)
    con.execute("BEGIN IMMEDIATE")
    try:
        _alta_servidor(con, sid, nombre, ahora)
        ok_en, intento_en = int(entrada.get("ok_en") or 0), int(entrada.get("intento_en") or 0)
        previo = [v if v <= ahora else 0 for v in con.execute("SELECT ok_en, intento_en, muestra_en FROM servidores WHERE id=?", (sid,)).fetchone()]
        # (un reloj que estuvo adelantado dejó marcas en el futuro: no pueden bloquear el registro de lo que llega ahora)
        resultado = None
        if entrada.get("error") or not ok_en or ok_en <= previo[0]:
            # intento fallido (o lectura ya anotada): una fila de estado, sin métricas ni IPs
            if entrada.get("error") and intento_en > previo[1] and TS_MIN <= intento_en <= ahora + 60:
                if intento_en - previo[2] >= FRANJA_S:
                    con.execute("INSERT OR IGNORE INTO muestras(servidor_id, ts, estado) VALUES(?,?,'error')", (sid, intento_en))
                    con.execute("UPDATE servidores SET muestra_en=? WHERE id=?", (intento_en, sid))
                _suma_hora(con, sid, intento_en, None, True)
                resultado = "estado"
            con.execute("UPDATE servidores SET intento_en=? WHERE id=?", (max(intento_en, previo[1]), sid))
        elif not TS_MIN <= ok_en <= ahora + 60:
            _descartar(con, "reloj", sid)
        else:
            try:
                m, top, propios = muestra_de(entrada, conocidas)
            except FueraDeRango:
                _descartar(con, "fuera_de_rango", sid)
                m = None
            if m is not None:
                if ok_en - previo[2] >= FRANJA_S:
                    cols = ["servidor_id", "ts", "estado", *METRICAS]
                    con.execute(f"INSERT OR IGNORE INTO muestras({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                                (sid, ok_en, m["estado"], *(m[k] for k in METRICAS)))
                    con.execute("UPDATE servidores SET muestra_en=? WHERE id=?", (ok_en, sid))
                _suma_hora(con, sid, ok_en, m, False)
                try:
                    _anota_horas(con, sid, horas_de(entrada, ok_en))
                except FueraDeRango:
                    _descartar(con, "histograma", sid)
                if propios:
                    _descartar(con, "ips_propias", sid)
                if guardar_ips and top:
                    _anota_ips(con, sid, top, ok_en, ok_en // 86400)
                resultado = "buena"
            con.execute("UPDATE servidores SET ok_en=?, intento_en=? WHERE id=?", (max(ok_en, previo[0]), max(intento_en, previo[1]), sid))
        con.execute("COMMIT")
        return resultado
    except Exception:
        _rollback(con)
        raise


# ---------------------------------------------------------------- mantenimiento y olvido

def mantener(con, politicas, ahora, vacuum=True):
    """Purga y consolidación. `politicas` = {id: {"guardar_ips": bool, "retencion_dias": int}} de los servidores que siguen en la config.
    Devuelve {"ips_borradas", "crudo_borrado", "consolidado"}. Un servidor que ya no está en la config se retira y pierde sus IPs en este paso."""
    ahora = int(ahora)
    ips = 0
    con.execute("BEGIN IMMEDIATE")
    try:
        for (sid,) in con.execute("SELECT id FROM servidores WHERE retirado_en IS NULL").fetchall():
            if sid not in politicas:
                con.execute("UPDATE servidores SET retirado_en=? WHERE id=?", (ahora, sid))
        for (sid,) in con.execute("SELECT id FROM servidores").fetchall():
            p = politicas.get(sid)
            if p is None or not p.get("guardar_ips", True):
                ips += con.execute("DELETE FROM ips_atacantes WHERE servidor_id=?", (sid,)).rowcount
            else:
                limite = ahora - retencion_dias(p.get("retencion_dias")) * 86400
                ips += con.execute("DELETE FROM ips_atacantes WHERE servidor_id=? AND ultima < ?", (sid, limite)).rowcount
        # marcas de un reloj que estuvo adelantado: se traen al presente para que envejezcan y caduquen como cualquier otra
        con.execute("UPDATE ips_atacantes SET ultima=?, ultima_hora=MIN(ultima_hora, ?), primera=MIN(primera, ?) WHERE ultima > ? ",
                    (ahora, _hora_de(ahora), ahora, ahora + 3600))
        con.execute("DELETE FROM muestras WHERE ts > ?", (ahora + 3600,))
        con.execute("DELETE FROM horas_servidor WHERE hora > ?", (ahora + 3600,))
        con.execute("UPDATE servidores SET ok_en=MIN(ok_en, ?), intento_en=MIN(intento_en, ?), muestra_en=MIN(muestra_en, ?)", (ahora, ahora, ahora))
        crudo = con.execute("DELETE FROM muestras WHERE ts < ?", (ahora - RETENCION_CRUDO_S,)).rowcount
        caducar_copias(os.path.join(os.path.dirname(ruta()), "copias"), ahora)
        con.execute("INSERT INTO meta(clave, valor) VALUES('mantenido_en', ?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (str(ahora),))
        con.execute("COMMIT")
    except Exception:
        _rollback(con)
        raise
    return {"ips_borradas": ips, "crudo_borrado": crudo, "consolidado": _consolidar(con, ahora, ips > 0 and vacuum)}


def _consolidar(con, ahora, hubo_borrado):
    """Checkpoint que trunca el WAL (las páginas con IPs recién borradas viven allí hasta entonces); lo reintenta quien llame hasta que
    no haya lectores. `VACUUM` mensual con el temporal en memoria. Devuelve True si el checkpoint se completó."""
    ultimo = con.execute("SELECT valor FROM meta WHERE clave='vacuum_en'").fetchone()
    if hubo_borrado and (not ultimo or ahora - int(ultimo[0]) >= VACUUM_CADA_S):
        con.execute("PRAGMA temp_store=MEMORY")
        con.execute("VACUUM")
        con.execute("INSERT INTO meta(clave, valor) VALUES('vacuum_en', ?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (str(ahora),))
    busy = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    if busy == 0:
        con.execute("INSERT INTO meta(clave, valor) VALUES('consolidado_en', ?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (str(ahora),))
    return busy == 0


def olvidar_ips(con, servidor_id=None, ahora=None):
    """Borra ya las IPs (de un servidor o de todos), consolida el WAL y devuelve el recuento. El borrado se hace aunque `guardar_ips` esté activo."""
    ahora = int(ahora or time.time())
    con.execute("BEGIN IMMEDIATE")
    try:
        if servidor_id is None:
            n = con.execute("DELETE FROM ips_atacantes").rowcount
        else:
            n = con.execute("DELETE FROM ips_atacantes WHERE servidor_id=?", (servidor_id,)).rowcount
        con.execute("COMMIT")
    except Exception:
        _rollback(con)
        raise
    return n, _consolidar(con, ahora, False)


def borrar_servidor(con, servidor_id):
    """Quita un servidor y todo su historial (olvido total, a petición)."""
    n = con.execute("DELETE FROM servidores WHERE id=?", (servidor_id,)).rowcount
    _consolidar(con, int(time.time()), False)
    return n
