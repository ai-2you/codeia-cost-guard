"""Copias cifradas de la base: origen consistente, nombres por dispositivo, rotación y escritura verificada.

Reglas que sostienen todo: el texto en claro solo toca un temporal privado de `DATOS/tmp` (0600, nombre aleatorio, borrado en
`finally` y barrido al arrancar los de más de 6 h); la rotación solo toca copias con nuestro nombre y de ESTE dispositivo, nunca
antes de verificar la nueva y nunca la recién escrita; una base que falla `quick_check` se copia igual con sufijo `.danada` (como
mucho una al día y las 3 últimas) y queda fuera de la rotación normal; una carpeta de copias configurada que no existe es un
error (disco o nube sin montar), no se crea en silencio.
"""
import datetime
import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time

from . import cgx, rutas

DIARIAS = 14
MENSUALES = 6
DANADAS = 3
EDAD_TMP_S = 6 * 3600
EDAD_PART_S = 86400
ENSAYO_CADA_S = 90 * 86400
NOMBRE = re.compile(r"^copia-([0-9a-f]{8})-(\d{8})-(\d{6})-([0-9a-f]{4})(\.danada)?\.cgx$")


def carpeta_por_defecto():
    """Las copias cifradas van en una carpeta distinta de `rutas.COPIAS` (copias en claro previas a migraciones)."""
    return os.path.join(rutas.DATOS, "copias-cifradas")


def dispositivo_id(firma_publica):
    """8 hex de la huella de la clave de firma del dispositivo: separa las copias de varios equipos en una misma carpeta."""
    return hashlib.sha256(firma_publica).hexdigest()[:8]


def dir_tmp():
    d = os.path.join(rutas.DATOS, "tmp")
    os.makedirs(d, mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(d, 0o700)
    return d


def barrer_tmp(edad_min_s=EDAD_TMP_S, ahora=None):
    """Borra temporales con la base en claro que un cierre en seco dejó: solo los de más de `edad_min_s` (no se pisa el temporal
    de otro proceso en marcha) y también los `.restaurando-*` de la carpeta de datos."""
    ahora = ahora if ahora is not None else time.time()
    n = 0
    candidatos = []
    d = os.path.join(rutas.DATOS, "tmp")
    if os.path.isdir(d):
        candidatos += [os.path.join(d, f) for f in os.listdir(d)]
    if os.path.isdir(rutas.DATOS):
        candidatos += [os.path.join(rutas.DATOS, f) for f in os.listdir(rutas.DATOS) if f.startswith(".restaurando-")]
    for ruta in candidatos:
        try:
            if ahora - os.stat(ruta).st_mtime >= edad_min_s:
                os.remove(ruta)
                n += 1
        except OSError:
            pass
    return n


def copiar_base(con, danada_ok=True):
    """Copia consistente de la base viva en un temporal sin WAL (`journal_mode=DELETE`) y la comprueba con `quick_check`.
    Devuelve (ruta del temporal, "ok" o el primer problema). El llamador lo borra en `finally`."""
    fd, ruta = tempfile.mkstemp(prefix="base-", suffix=".tmp", dir=dir_tmp())
    os.close(fd)
    if os.name != "nt":
        os.chmod(ruta, 0o600)
    destino = sqlite3.connect(ruta)
    try:
        con.backup(destino)
        destino.execute("PRAGMA journal_mode=DELETE")
        estado = str(destino.execute("PRAGMA quick_check(1)").fetchone()[0])[:200]
    except BaseException:
        destino.close()
        os.remove(ruta)
        raise
    destino.close()
    return ruta, estado


def nombre_copia(ahora, disp, danada=False):
    """`copia-<dispositivo>-AAAAMMDD-HHMMSS-<4 hex>.cgx` (UTC); el sufijo aleatorio evita choques entre dos procesos."""
    t = datetime.datetime.fromtimestamp(int(ahora), datetime.timezone.utc)
    return f"copia-{disp}-{t:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}{'.danada' if danada else ''}.cgx"


def _analiza(nombre):
    """(dispositivo, fecha, es_danada) o None si no es una copia nuestra."""
    m = NOMBRE.match(nombre)
    if not m:
        return None
    return m.group(1), datetime.datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S"), bool(m.group(5))


def a_borrar(nombres, disp):
    """Qué copias de ESTE dispositivo sobran: de las buenas, se conserva la más reciente de cada uno de los últimos 14 días con
    copia y la más antigua de cada uno de los últimos 6 meses con copia; de las `.danada`, solo las 3 últimas. Lo que no tiene
    nuestro nombre o es de otro dispositivo no se toca nunca."""
    buenas, danadas = [], []
    for n in nombres:
        a = _analiza(n)
        if a and a[0] == disp:
            (danadas if a[2] else buenas).append((a[1], n))
    buenas.sort()
    danadas.sort()
    por_dia, por_mes = {}, {}
    for f, n in buenas:
        por_dia[f.date()] = n
        por_mes.setdefault((f.year, f.month), n)
    conservar = {por_dia[d] for d in sorted(por_dia)[-DIARIAS:]} | {por_mes[m] for m in sorted(por_mes)[-MENSUALES:]}
    return sorted([n for _, n in buenas if n not in conservar] + [n for _, n in danadas[:-DANADAS]])


# ---------------------------------------------------------------- escritura verificada

def reemplazar(origen, destino, intentos=6, espera=0.2):
    """`os.replace` con reintentos: en Windows un antivirus o el indexador pueden tener el fichero abierto unos instantes."""
    for n in range(intentos):
        try:
            return os.replace(origen, destino)
        except PermissionError:
            if n == intentos - 1:
                raise
            time.sleep(espera)


def _borra(ruta):
    try:
        os.remove(ruta)
    except OSError:
        pass


def _limpia_part(carpeta, ahora):
    for f in os.listdir(carpeta):
        if f.endswith(".part") and (f.startswith(".copia-") or f.startswith(".export-")):
            try:
                if ahora - os.stat(os.path.join(carpeta, f)).st_mtime >= EDAD_PART_S:
                    os.remove(os.path.join(carpeta, f))
            except OSError:
                pass


def _prepara_carpeta(carpeta):
    if os.path.isdir(carpeta):
        return
    if os.path.abspath(carpeta) == os.path.abspath(carpeta_por_defecto()):
        os.makedirs(carpeta, mode=0o700, exist_ok=True)
        return
    raise cgx.ErrorCgx(f"la carpeta de copias no está disponible ({os.path.basename(carpeta) or carpeta}): ¿disco o nube sin montar?")


def hacer_copia(con, carpeta, publica, firma_privada, firma_publica, ahora=None):
    """Copia cifrada a `publica` de la base de `con` (solo lectura basta). Escribe en un `.part` de la carpeta de destino,
    RELEE el fichero escrito con la clave simétrica que aún está en memoria (etiquetas, firma y SHA-256 del claro) y solo
    entonces lo renombra y rota las antiguas de este dispositivo; si algo falla no queda nada a medias y no se borra ninguna copia.
    Una base que falla `quick_check` se copia igual con sufijo `.danada`. Si el reloj va por detrás de la copia más reciente no se
    rota (se avisa en el resumen). Devuelve el resumen de la copia."""
    ahora = int(ahora if ahora is not None else time.time())
    _prepara_carpeta(carpeta)
    disp = dispositivo_id(firma_publica)
    _limpia_part(carpeta, ahora)
    origen, estado = copiar_base(con)
    parte = None
    try:
        danada = estado != "ok"
        destino = os.path.join(carpeta, nombre_copia(ahora, disp, danada))
        fd, parte = tempfile.mkstemp(prefix=".copia-", suffix=".part", dir=carpeta)
        tam = os.path.getsize(origen)
        with os.fdopen(fd, "wb") as salida, open(origen, "rb") as entrada:
            r = cgx.cifrar_a_destinatario(entrada, tam, salida, publica, firma_privada, ahora=ahora)
            salida.flush()
            os.fsync(salida.fileno())
        with open(parte, "rb") as releido:
            if cgx.verificar_con_clave(releido, r["clave"], [firma_publica]) != r["sha256_claro"]:
                raise cgx.ErrorCgx("la copia escrita no coincide con el origen")
        if os.path.exists(destino):
            raise cgx.ErrorCgx("ya existe una copia con ese nombre")
        nuevo = os.path.basename(destino)
        propias = [(_analiza(n), n) for n in os.listdir(carpeta)]
        reciente = max((a[1] for a, n in propias if a and a[0] == disp and not a[2]), default=None)
        reloj_atrasado = bool(reciente and _analiza(nuevo)[1] < reciente - datetime.timedelta(days=1))
        reemplazar(parte, destino)
        parte = None
        tamano = os.path.getsize(destino)
        rotadas = [] if reloj_atrasado else [n for n in a_borrar(os.listdir(carpeta), disp) if n != nuevo]
        for n in rotadas:
            _borra(os.path.join(carpeta, n))
        return {"archivo": nuevo, "bytes": tamano, "danada": danada, "estado": estado, "huella": cgx.huella(publica),
                "rotadas": len(rotadas), "ts": ahora, "reloj_atrasado": reloj_atrasado}
    finally:
        _borra(origen)
        if parte:
            _borra(parte)


# ---------------------------------------------------------------- cuándo toca (en un fichero de DATOS, no en la carpeta de copias)

def _ruta_ultima():
    return os.path.join(rutas.DATOS, "ultima_copia.json")


def _guarda(datos):
    os.makedirs(rutas.DATOS, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ultima-", dir=rutas.DATOS)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(datos, f)
    reemplazar(tmp, _ruta_ultima())


def ultima_copia():
    try:
        with open(_ruta_ultima(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def registrar_copia(resumen):
    d = ultima_copia() or {}
    d.update({k: resumen[k] for k in ("archivo", "ts", "danada", "huella", "bytes")})
    d.pop("ultimo_error", None)
    _guarda(d)


def registrar_error(mensaje, ahora=None):
    """Último fallo del hilo de copias, para que se vea en el estado y no solo en el log."""
    d = ultima_copia() or {}
    d["ultimo_error"] = {"ts": int(ahora if ahora is not None else time.time()), "mensaje": str(mensaje)[:200]}
    _guarda(d)


def registrar_ensayo(ahora=None):
    d = ultima_copia() or {}
    d["ultimo_ensayo"] = int(ahora if ahora is not None else time.time())
    _guarda(d)


def toca_copia(ahora=None, cada_s=86400):
    """True si la última copia (buena o `.danada`) tiene más de `cada_s`, no hay ninguna o su fecha es del futuro (reloj que
    estuvo adelantado). Una `.danada` cuenta como hecha: como mucho una al día, no una cada 15 minutos."""
    u = ultima_copia()
    ahora = ahora if ahora is not None else time.time()
    if not u or "ts" not in u:
        return True
    return ahora - u["ts"] >= cada_s or u["ts"] > ahora + 86400


def estado_copias(config_copias, ahora=None):
    """Para mostrar en la barra y en el informe: {estado, ultima, edad_h, local, ensayo_atrasado, ultimo_error}. Estados:
    `sin_destinatario` (no hay copias automáticas), `nunca`, `al_dia`, `atrasada` (la última buena tiene más de 48 h), `danada`
    (la última salió de una base dañada) o `reloj` (la última tiene fecha del futuro: el reloj del equipo va mal)."""
    ahora = ahora if ahora is not None else time.time()
    cc = config_copias or {}
    base = {"local": os.path.abspath(cc.get("carpeta") or carpeta_por_defecto()) == os.path.abspath(carpeta_por_defecto())}
    if not cc.get("destinatario"):
        return {**base, "estado": "sin_destinatario", "ultima": None, "edad_h": None, "ensayo_atrasado": False, "ultimo_error": None}
    u = ultima_copia() or {}
    ens = u.get("ultimo_ensayo")
    comun = {**base, "ensayo_atrasado": not ens or ahora - ens > ENSAYO_CADA_S, "ultimo_error": u.get("ultimo_error")}
    if "ts" not in u:
        return {**comun, "estado": "nunca", "ultima": None, "edad_h": None}
    edad = (ahora - u["ts"]) / 3600
    if u["ts"] > ahora + 86400:
        return {**comun, "estado": "reloj", "ultima": u.get("archivo"), "edad_h": round(edad, 1)}
    estado = "danada" if u.get("danada") else "atrasada" if edad > 48 else "al_dia"
    return {**comun, "estado": estado, "ultima": u.get("archivo"), "edad_h": round(edad, 1)}
