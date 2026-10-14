"""Ingesta de logs al almacén: lectura incremental, tiempo UTC y el bucle común de los extractores por agente.

Lo que llega de un log es hostil (un `jsonl` lo puede escribir cualquier proceso, o una sesión con inyección de
prompt): se acota todo, nada de lo leído se interpreta como SQL ni como ruta, y lo que no cuadra no se guarda: se
anota en `anomalias`. Releer es inocuo porque el almacén es monótono, así que perder el desplazamiento solo cuesta tiempo.
"""
import datetime
import getpass
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import stat

from . import almacen

MAX_LECTURA = 8 * 1024 * 1024          # bytes por pasada y fichero: la ingesta es reanudable y no monopoliza la barra
MAX_LINEA = 2 * 1024 * 1024            # una línea mayor no es un turno
LOTE = 500                             # turnos por transacción (lotes cortos: ≤ 200 ms)
TAM_CABECERA = 4096

_ISO = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.(\d+))?(Z|[+-]\d\d(?::?\d\d)?)$")


def a_epoch_utc(v):
    """Segundos UTC de una marca de tiempo, o None. EXIGE zona: sin ella no se puede saber el instante y se
    cuarentena (hora local y UTC se mezclarían). Acepta ISO con Z/offset y epoch en segundos o milisegundos."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d{9,13}(\.\d+)?", v)):
        try:
            n = float(v)
        except OverflowError:                           # un entero de cientos de cifras
            return None
        if n != n or n in (float("inf"), float("-inf")):
            return None
        return int(n / 1000) if n > 1e11 else int(n)
    if not isinstance(v, str):
        return None
    m = _ISO.match(v.strip())
    if not m:
        return None
    anio, mes, dia, h, mi, se, _frac, zona = m.groups()
    if zona == "Z":
        desplazamiento = 0
    else:
        z = zona.replace(":", "")
        signo = -1 if z[0] == "-" else 1
        horas_z, minutos_z = int(z[1:3]), int(z[3:5] or "0")
        if horas_z > 14 or minutos_z > 59:              # las zonas reales van de -12:00 a +14:00
            return None
        desplazamiento = signo * (horas_z * 3600 + minutos_z * 60)
    try:
        dt = datetime.datetime(int(anio), int(mes), int(dia), int(h), int(mi), int(se), tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp()) - desplazamiento


def id_fuente(ruta, base):
    """Id estable y portable de una fuente: hash de la ruta RELATIVA a `base` (nunca la ruta absoluta con el usuario)."""
    try:
        rel = os.path.relpath(os.path.realpath(ruta), os.path.realpath(base)).replace("\\", "/")
    except ValueError:                                  # Windows: otra unidad
        rel = os.path.realpath(ruta).replace("\\", "/")
    return hashlib.sha256(rel.encode("utf-8", "replace")).hexdigest()[:32]


TOPE_LINEA_ENORME = 64 * 1024 * 1024    # una línea de más de esto se da por perdida y se salta hasta el final del fichero
LEN_COLA = 64                           # bytes justo antes del desplazamiento: comprueban que seguimos en el mismo contenido


def _hash(datos):
    return hashlib.sha256(datos).hexdigest()


def _comprueba_continuidad(f, estado, st):
    """True si el fichero abierto sigue siendo el que se leyó: mismo inodo, cabecera, cola y un salto de línea justo antes del
    desplazamiento. Si el fichero se reescribió en el mismo inodo conservando el principio, la cola lo delata."""
    if not estado or estado["desplazamiento"] <= 0 or estado["inodo"] != st.st_ino or st.st_size < estado["desplazamiento"]:
        return False
    try:
        f.seek(0)
        if _hash(f.read(estado["cab_len"])) != estado["hash_cab"]:
            return False
        if estado["cola_len"]:
            f.seek(estado["desplazamiento"] - estado["cola_len"])
            cola = f.read(estado["cola_len"])
            return cola.endswith(b"\n") and _hash(cola) == estado["hash_cola"]
        return True
    except OSError:
        return False


def leer_nuevas(ruta, estado, max_bytes=MAX_LECTURA):
    """Líneas completas nuevas de `ruta` desde donde se quedó (`estado` de `almacen.estado_lectura`, o None).

    Todo sobre UN descriptor abierto (stat, lectura y hashes): el fichero no puede cambiar de inodo entre medias. Se relee
    desde el principio si cambió el inodo, la cabecera o la cola, o se truncó. Una línea sin terminar (se está escribiendo)
    no avanza. Una línea mayor que `max_bytes` se salta ENTERA (se busca su salto de línea) para no dejar un resto que se
    lea como JSON roto. Devuelve (lineas, info) con info lista para `almacen.guardar_lectura`.
    `completo`: no queda nada más que leer ahora (una última línea sin terminar no cuenta como pendiente)."""
    if not stat.S_ISREG(os.stat(ruta).st_mode):         # antes de abrir: abrir un FIFO sin escritor cuelga la ingesta
        raise OSError("no es un fichero regular")
    with open(ruta, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise OSError("no es un fichero regular")
        desde = estado["desplazamiento"] if _comprueba_continuidad(f, estado, st) else 0
        f.seek(desde)
        trozo = f.read(max_bytes)
        ultimo = trozo.rfind(b"\n")
        enormes, fin = 0, False
        if ultimo >= 0:
            avance, lineas = ultimo + 1, trozo[:ultimo + 1].split(b"\n")[:-1]
        elif len(trozo) >= max_bytes:                   # una sola línea mayor que max_bytes: buscar su final sin acumularla
            enormes, lineas, avance = 1, [], len(trozo)
            while avance < TOPE_LINEA_ENORME:
                bloque = f.read(1 << 20)
                if not bloque:
                    avance = 0 if desde + avance >= st.st_size else avance      # sin final aún: no avanzar (se está escribiendo)
                    enormes = 0 if avance == 0 else 1
                    fin = True                      # se llegó al final del fichero: no queda nada que leer ahora
                    break
                i = bloque.find(b"\n")
                if i >= 0:
                    avance += i + 1
                    break
                avance += len(bloque)
        else:
            avance, lineas = 0, []
        nuevo = desde + avance
        cab_len, cola_len = min(TAM_CABECERA, nuevo), min(LEN_COLA, nuevo)
        f.seek(0)
        hash_cab = _hash(f.read(cab_len)) if cab_len else ""
        f.seek(nuevo - cola_len)
        cola = f.read(cola_len) if cola_len else b""
    info = {"desplazamiento": nuevo, "tam": st.st_size, "inodo": st.st_ino, "mtime_ns": st.st_mtime_ns,
            "cab_len": cab_len, "hash_cab": hash_cab, "cola_len": len(cola), "hash_cola": _hash(cola) if cola else "",
            "completo": nuevo >= st.st_size or len(trozo) < max_bytes or fin, "enormes": enormes, "reanudado": desde > 0}
    return lineas, info


def _maximo(a, b):
    """Dos líneas del mismo mensaje: máximo de cada contador y la primera hora vista."""
    return {**a, "hora_utc": min(a["hora_utc"], b["hora_utc"]), **{c: max(a[c], b[c]) for c in almacen.CONTADORES}}


def ingerir_fichero(con, quien, espacio_id, ruta, agente, extractor, base, contexto=None, max_bytes=MAX_LECTURA, ahora=None):
    """Una pasada de ingesta de un fichero `jsonl`. `extractor(dict, ctx)` devuelve un turno (dict), None o ("anomalia", texto).

    Una línea que falla (JSON roto, tipos raros, desbordes, errores del extractor) se cuenta y se anota, y la pasada sigue:
    una sola línea hostil no puede bloquear un fichero para siempre. Devuelve {nuevos, subidos, iguales, anomalias, lineas,
    rotas, completo}. Reanudable: si no termina, la siguiente pasada sigue donde quedó."""
    fid = almacen.fuente(con, quien, espacio_id, agente, id_fuente(ruta, base), ahora=ahora)
    estado_previo = almacen.estado_lectura(con, fid)
    try:
        lineas, info = leer_nuevas(ruta, estado_previo, max_bytes)
    except OSError as e:
        almacen.anotar(con, quien, "ingesta", "", fid, f"fichero no legible: {type(e).__name__}", ahora=ahora)
        return {"nuevos": 0, "subidos": 0, "iguales": 0, "anomalias": 1, "lineas": 0, "rotas": 0, "completo": True}
    ctx = dict(contexto or {})
    guardado = _contexto_guardado(estado_previo) if info["reanudado"] else {}
    ctx.update(guardado)                                 # lo aprendido en pasadas anteriores (sesión, modelo) si seguimos en el mismo contenido
    turnos, rotas, grandes, otras = {}, 0, info["enormes"], {}

    def cuenta(texto):
        otras[texto] = otras.get(texto, 0) + 1

    for crudo in lineas:
        if len(crudo) > MAX_LINEA:
            grandes += 1
            continue
        if not crudo.strip():
            continue
        try:
            d = json.loads(crudo)
        except (ValueError, RecursionError):
            rotas += 1
            continue
        if not isinstance(d, dict):
            continue
        try:
            r = extractor(d, ctx)
            if r is None:
                continue
            if isinstance(r, tuple):
                cuenta(r[1])
                continue
            motivo = almacen.problema(r)
            if motivo:                                  # se valida AQUÍ para que un tipo raro no rompa la fusión del lote
                cuenta(f"turno descartado: {motivo}")
                continue
            previo = turnos.get(r["clave"])
            if previo is None:
                turnos[r["clave"]] = r
            elif previo.get("modelo") == r.get("modelo") and previo.get("agente") == r.get("agente") \
                    and abs(previo["hora_utc"] - r["hora_utc"]) <= almacen.TOLERANCIA_HORA_S:
                turnos[r["clave"]] = _maximo(previo, r)
            else:                                      # misma clave con otra identidad en el mismo fichero: manda la primera
                cuenta("clave repetida con otra identidad")
        except Exception as e:                         # noqa: BLE001  (una línea hostil no bloquea el fichero)
            cuenta(f"línea con error: {type(e).__name__}")
    total = {"nuevos": 0, "subidos": 0, "iguales": 0, "anomalias": 0}
    lote = list(turnos.values())
    for i in range(0, len(lote), LOTE):
        res = almacen.ingerir_turnos(con, quien, espacio_id, fid, lote[i:i + LOTE], ahora=ahora)
        for k in total:
            total[k] += res[k]
    # el id de fuente va en el detalle: la anomalía es única por fuente y no se funde entre ficheros distintos
    for tipo, n in (("lineas_no_json", rotas), ("lineas_enormes", grandes)):
        if n:
            almacen.anotar(con, quien, tipo, "", fid, f"fuente {fid}: {n} líneas", ahora=ahora)
    for texto, n in otras.items():
        almacen.anotar(con, quien, "ingesta", "", fid, f"fuente {fid}: {_ctrl_fuera(texto)}: {n}"[:200], ahora=ahora)
    almacen.guardar_lectura(con, quien, fid, info["inodo"], info["tam"], info["mtime_ns"], info["desplazamiento"],
                            info["cab_len"], info["hash_cab"], info["cola_len"], info["hash_cola"],
                            contexto=_serializa_contexto(ctx), ahora=ahora)
    total.update(lineas=len(lineas), rotas=rotas, completo=info["completo"])
    return total


def _contexto_guardado(estado):
    try:
        d = json.loads((estado or {}).get("contexto") or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def texto_seguro(v, maximo):
    """`v` si es un texto no vacío, UTF-8 real (sin sustitutos sueltos), sin caracteres de control y de longitud acotada; si no, ""."""
    return v if almacen._texto_ok(v, maximo) and v else ""


def _serializa_contexto(ctx):
    """Solo valores de texto cortos y válidos (sesión, modelo, proyecto...): el contexto no puede crecer sin límite, ni llevar
    sustitutos o control, ni romper el guardado de la lectura (un fallo aquí bloquearía el fichero en cada pasada)."""
    limpio = {k: v for k, v in ctx.items() if texto_seguro(k, 30) and texto_seguro(v, 200)}
    try:
        texto = json.dumps(limpio, ensure_ascii=False)
        texto.encode("utf-8")
    except (ValueError, UnicodeError):
        return "{}"
    return texto if len(texto) <= 1000 else "{}"


def proyecto_de_slug(slug, usuario=None):
    """Nombre crudo de proyecto a partir de un slug de ruta (`-Users-yo-Proyectos-demo-app`): los 3 últimos trozos, sin
    `Users/<usuario>`, `home/<usuario>` ni la unidad de Windows. Es el mismo criterio para todos los agentes, así un proyecto
    se llama igual en Claude Code y en Codex. El usuario actual se quita entero aunque lleve guion o punto."""
    trozos = [x for x in slug.split("-") if x]
    if len(trozos) > 1 and len(trozos[0]) == 1 and trozos[0].isalpha() and trozos[1].lower() == "users":
        trozos = trozos[1:]                            # Windows: C--Users-nombre-...
    if len(trozos) >= 2 and trozos[0].lower() in ("users", "home"):
        mios = usuario if usuario is not None else _trozos_usuario()
        resto = [x.lower() for x in trozos[1:1 + len(mios)]]
        trozos = trozos[1 + len(mios):] if mios and resto == mios else trozos[2:]
    nombre = "/".join(trozos[-3:]) if trozos else "otros"
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "_", nombre)[:200]


def _trozos_usuario():
    try:
        usuario = getpass.getuser()
    except Exception:                                  # noqa: BLE001
        usuario = os.path.basename(os.path.expanduser("~"))
    return [x.lower() for x in re.split(r"[^A-Za-z0-9]+", usuario) if x]


def _ctrl_fuera(texto):
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", str(texto))[:120]


# ---------------------------------------------------------------- fuentes SQLite ajenas (OpenCode, Hermes)

def firma_sqlite(ruta):
    """Tamaño y fecha de la base y de su WAL (donde SQLite escribe primero): si no cambian, la base no se abre."""
    partes = []
    for f in (ruta, ruta + "-wal"):
        try:
            st = os.stat(f)
            partes.append(f"{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}")
        except OSError:
            partes.append("-")
    return "|".join(partes)


def abrir_sqlite_solo_lectura(ruta):
    """Abre una base AJENA sin poder modificarla (`mode=ro` + `query_only`), con la ruta absoluta y escapada como URI.

    Si la base no tiene WAL (la aplicación está cerrada) se abre `immutable=1`: así SQLite no crea ficheros `-wal`/`-shm` en el
    directorio ajeno y funciona aunque sea de solo lectura. Con WAL se usa `mode=ro` (necesita el `-shm` de quien la tiene abierta)."""
    ruta = os.path.abspath(ruta)
    wal = ruta + "-wal"
    sin_wal = not (os.path.exists(wal) and os.path.getsize(wal) > 0)
    modo = "immutable=1" if sin_wal else "mode=ro"
    con = sqlite3.connect(f"{pathlib.Path(ruta).as_uri()}?{modo}", uri=True, timeout=2)
    con.execute("PRAGMA query_only=ON")
    return con


def aplicar_filas(con, quien, espacio_id, fuente_id, filas, a_turno, ahora=None, extra=None):
    """Convierte filas de una base ajena en turnos y los guarda. `a_turno(fila)` devuelve un turno, None o ("anomalia", texto).

    Una fila rara se cuenta y se anota, y el resto sigue. Devuelve {nuevos, subidos, iguales, anomalias}."""
    turnos, otras = {}, dict(extra or {})

    def cuenta(texto):
        otras[texto] = otras.get(texto, 0) + 1

    for fila in filas:
        try:
            r = a_turno(fila)
        except Exception as e:                           # noqa: BLE001  (una fila rara no bloquea la fuente)
            cuenta(f"fila con error: {type(e).__name__}")
            continue
        if r is None:
            continue
        if isinstance(r, tuple):
            cuenta(r[1])
            continue
        motivo = almacen.problema(r)
        if motivo:
            cuenta(f"turno descartado: {motivo}")
            continue
        turnos[r["clave"]] = r
    total = {"nuevos": 0, "subidos": 0, "iguales": 0, "anomalias": 0}
    lote = list(turnos.values())
    for i in range(0, len(lote), LOTE):
        res = almacen.ingerir_turnos(con, quien, espacio_id, fuente_id, lote[i:i + LOTE], ahora=ahora)
        for k in total:
            total[k] += res[k]
    total["anomalias"] += sum(otras.values())             # lo descartado también cuenta como anomalía
    for texto, n in otras.items():
        almacen.anotar(con, quien, "ingesta", "", fuente_id, f"fuente {fuente_id}: {_ctrl_fuera(texto)}: {n}"[:200], ahora=ahora)
    return total
