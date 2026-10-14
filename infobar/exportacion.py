"""Copias cifradas de la base: origen consistente, nombres, rotación y (más adelante) escritura verificada y restauración.

Reglas que sostienen todo: el texto en claro solo toca un temporal privado de `DATOS/tmp` (0600, nombre aleatorio, borrado en
`finally` y barrido al arrancar); la rotación nunca borra nada que no sea una copia con nuestro nombre y nunca antes de verificar
la nueva; una base que falla `quick_check` se copia igual con sufijo `.danada` y queda fuera de la rotación.
"""
import datetime
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
NOMBRE = re.compile(r"^copia-(\d{8})-(\d{6})-([0-9a-f]{4})(\.danada)?\.cgx$")


def carpeta_por_defecto():
    """Las copias cifradas van en una carpeta distinta de `rutas.COPIAS` (copias en claro previas a migraciones)."""
    return os.path.join(rutas.DATOS, "copias-cifradas")


def dir_tmp():
    d = os.path.join(rutas.DATOS, "tmp")
    os.makedirs(d, mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(d, 0o700)
    return d


def barrer_tmp():
    """Al arrancar: un cierre en seco puede haber dejado temporales (con la base en claro)."""
    d = os.path.join(rutas.DATOS, "tmp")
    if not os.path.isdir(d):
        return 0
    n = 0
    for f in os.listdir(d):
        try:
            os.remove(os.path.join(d, f))
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


def nombre_copia(ahora, danada=False):
    """`copia-AAAAMMDD-HHMMSS-<4 hex>.cgx` (UTC); el sufijo aleatorio evita choques de dos procesos en el mismo segundo."""
    t = datetime.datetime.fromtimestamp(int(ahora), datetime.timezone.utc)
    return f"copia-{t:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}{'.danada' if danada else ''}.cgx"


def _fecha(nombre):
    m = NOMBRE.match(nombre)
    if not m or m.group(4):
        return None
    return datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")


def a_borrar(nombres):
    """Qué copias sobran: se conserva la más reciente de cada uno de los últimos 14 días con copia y la más antigua de cada uno de
    los últimos 6 meses con copia. Los ficheros que no tienen nuestro nombre y las `.danada` no se tocan nunca."""
    copias = sorted((f, n) for n in nombres if (f := _fecha(n)))
    por_dia, por_mes = {}, {}
    for f, n in copias:
        por_dia[f.date()] = n                              # la última del día
        por_mes.setdefault((f.year, f.month), n)           # la primera del mes
    dias = sorted(por_dia)[-DIARIAS:]
    meses = sorted(por_mes)[-MENSUALES:]
    conservar = {por_dia[d] for d in dias} | {por_mes[m] for m in meses}
    return sorted(n for _, n in copias if n not in conservar)


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


def hacer_copia(con, carpeta, publica, firma_privada, firma_publica, ahora=None):
    """Copia cifrada a `publica` de la base de `con` (solo lectura basta). Escribe en un `.part` de la carpeta de destino,
    RELEE el fichero escrito con la clave simétrica que aún está en memoria (etiquetas, firma y SHA-256 del claro) y solo
    entonces lo renombra y rota las antiguas; si algo falla no queda nada a medias y no se borra ninguna copia. Una base que
    falla `quick_check` se copia igual con sufijo `.danada` y fuera de la rotación. Devuelve el resumen de la copia."""
    ahora = int(ahora if ahora is not None else time.time())
    os.makedirs(carpeta, exist_ok=True)
    origen, estado = copiar_base(con)
    parte = None
    try:
        danada = estado != "ok"
        destino = os.path.join(carpeta, nombre_copia(ahora, danada))
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
        reemplazar(parte, destino)
        parte = None
        rotadas = [] if danada else a_borrar(os.listdir(carpeta))
        for n in rotadas:
            _borra(os.path.join(carpeta, n))
        return {"archivo": os.path.basename(destino), "bytes": os.path.getsize(destino), "danada": danada,
                "estado": estado, "huella": cgx.huella(publica), "rotadas": len(rotadas), "ts": ahora}
    finally:
        _borra(origen)
        if parte:
            _borra(parte)


# ---------------------------------------------------------------- cuándo toca (en un fichero de DATOS, no en la carpeta de copias)

def _ruta_ultima():
    return os.path.join(rutas.DATOS, "ultima_copia.json")


def registrar_copia(resumen):
    os.makedirs(rutas.DATOS, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ultima-", dir=rutas.DATOS)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({k: resumen[k] for k in ("archivo", "ts", "danada", "huella", "bytes")}, f)
    reemplazar(tmp, _ruta_ultima())


def ultima_copia():
    try:
        with open(_ruta_ultima(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def toca_copia(ahora=None, cada_s=86400):
    """True si la última copia BUENA tiene más de `cada_s` (o no hay ninguna). Una `.danada` no cuenta como copia hecha."""
    u = ultima_copia()
    ahora = ahora if ahora is not None else time.time()
    return not u or u.get("danada") or ahora - u.get("ts", 0) >= cada_s


def estado_copias(config_copias, ahora=None):
    """Para mostrar en la barra y en el informe: {estado, ultima, edad_h}. Estados: `sin_destinatario` (no hay copias automáticas),
    `nunca`, `al_dia` (la última buena tiene menos de 48 h), `atrasada` (más de 48 h) o `danada` (la última salió de una base dañada)."""
    ahora = ahora if ahora is not None else time.time()
    if not (config_copias or {}).get("destinatario"):
        return {"estado": "sin_destinatario", "ultima": None, "edad_h": None}
    u = ultima_copia()
    if not u:
        return {"estado": "nunca", "ultima": None, "edad_h": None}
    edad = (ahora - u.get("ts", 0)) / 3600
    estado = "danada" if u.get("danada") else "atrasada" if edad > 48 else "al_dia"
    return {"estado": estado, "ultima": u.get("archivo"), "edad_h": round(edad, 1)}
