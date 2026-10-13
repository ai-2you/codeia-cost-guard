"""Lectores de los logs de cada agente: Claude Code, Codex, pi, OpenCode y Hermes."""
import collections, datetime, glob, json, os, re, sqlite3, time, urllib.parse, urllib.request

from . import rutas
from .config_y_estado import log
from .planes_y_tarifas import CLIFF, plan_ccs, tarifa


FUENTES = [f"{rutas.HOME}/.claude/projects", f"{rutas.HOME}/.codex/sessions"]


PI_DIR = f"{rutas.HOME}/.pi/agent/sessions"   # sesiones de pi (JSONL); ver parsear_pi()
# OpenCode no va aqui: guarda su consumo en SQLite (ver leer_bases()), no en .jsonl/.json;
# barrer su carpeta solo encontraba auth.json (credenciales) y diffs internos, sin datos de uso.


def _hallar_uso(o, p=0):
    """Busca el bloque de uso de un turno. Descarta los acumulados de Codex (llevan total_tokens)."""
    if p > 6 or not isinstance(o, (dict, list)):
        return None
    if isinstance(o, dict):
        if "input_tokens" in o and "total_tokens" not in o:
            return o
        if isinstance(o.get("last_token_usage"), dict):
            return o["last_token_usage"]
        for v in o.values():
            r = _hallar_uso(v, p + 1)
            if r:
                return r
    elif o:
        return _hallar_uso(o[0], p + 1)
    return None


_ISO_ZONA = re.compile(r"^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d(?::?\d\d)?)$")


def a_local(ts):
    """Pasa un ISO con zona ('...Z', '+02', '+0200' o '+02:00') a hora local sin zona. Las claves de
    dia y hora se leen luego con mktime como hora local; si se quedaran en UTC saldrian desplazadas.

    Se normaliza antes de fromisoformat porque el de Python 3.9 solo acepta '+HH:MM' y fracciones
    de 3 o 6 digitos: '+0200', 'Z', '.34' o nanosegundos lanzaban ValueError y la hora se quedaba
    en UTC. Sin zona, o si no se entiende, se devuelve tal cual."""
    ts = str(ts or "")
    m = _ISO_ZONA.match(ts)
    if not m:
        return ts
    dia, hora, frac, zona = m.groups()
    base = f"{dia}T{hora}"
    frac = "." + (frac + "000000")[:6] if frac else ""
    if zona == "Z":
        zona = "+00:00"
    else:
        zona = zona.replace(":", "")
        zona = f"{zona[:3]}:{zona[3:5] or '00'}"
    try:
        dt = datetime.datetime.fromisoformat(base + frac + zona)
        return dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return ts


def parsear(ruta):
    """Devuelve {dia: {modelo: [gi,go,cr,cw]}, turnos: {dia: n}, horas: {'YYYY-MM-DDTHH': {'tok','usd','fam'}}, proyecto: slug}

    Dos formatos, y confundirlos infla los totales:
      · Codex (rollouts): cada turno se escribe DOS veces (token_usage_record y event_msg/token_count,
        con el mismo segundo) y el fichero lleva ademas total_token_usage, que es ACUMULADO. Se usa el
        uso del turno (last_token_usage) y se descarta el acumulado. En Codex cached_input_tokens esta
        DENTRO de input_tokens, asi que la entrada fresca es input - cached.
      · Claude Code: una linea por respuesta con message.usage (el input ya excluye la cache).
    """
    dia = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0, 0]))
    fecha_ini = None
    fecha_ini = None
    turnos = collections.defaultdict(int)
    horas = collections.defaultdict(lambda: {"tok": 0, "usd": 0.0, "mod": collections.defaultdict(int)})
    vistos = set()
    proy = "codex/otros"
    if "/projects/" in ruta:
        trozos = [x for x in urllib.parse.unquote(ruta.split("/projects/")[-1].split("/")[0]).split("-") if x]
        proy = "/".join(trozos[-3:]) if trozos else "otros"
    modelo = None
    try:
        fh = open(ruta, encoding="utf-8", errors="ignore")
    except Exception:
        return None
    for linea in fh:
        if '"model"' in linea or "\"model\"" in linea:
            m = re.search(r'"model"\s*:\s*"([^"]+)"', linea)
            if m:
                modelo = m.group(1)
        if "input_tokens" not in linea and "inputTokens" not in linea:
            continue
        try:
            d = json.loads(linea)
        except Exception:
            continue
        ts = a_local(d.get("timestamp") or d.get("ts") or "")
        uso = _hallar_uso(d)
        if not uso:
            continue
        if "cached_input_tokens" in uso or "total_tokens" in uso:      # Codex: la cache va dentro de la entrada
            cr = uso.get("cached_input_tokens", 0) or 0
            gi = max((uso.get("input_tokens", 0) or 0) - cr, 0)
            cw = uso.get("cache_write_input_tokens", 0) or 0
        else:                                                          # Claude: entrada y cache separadas
            gi = uso.get("input_tokens", uso.get("inputTokens", 0)) or 0
            cr = uso.get("cache_read_input_tokens", uso.get("cachedInputTokens", 0)) or 0
            cw = uso.get("cache_creation_input_tokens", uso.get("cacheWriteInputTokens", 0)) or 0
        go = uso.get("output_tokens", uso.get("outputTokens", 0)) or 0
        # el doble registro de Codex comparte el segundo: incluir el segundo en la clave lo descarta
        clave = f"{modelo}|{ts[:19]}|{gi}|{go}|{cr}|{cw}"
        if clave in vistos:
            continue
        vistos.add(clave)
        k = modelo or "sin-modelo"
        tok = gi + go + cr + cw
        if len(ts) >= 10:
            a = dia[ts[:10]][k]
            a[0] += gi
            a[1] += go
            a[2] += cr
            a[3] += cw
            turnos[ts[:10]] += 1
        if len(ts) >= 13:
            horas[ts[:13]]["tok"] += tok
            horas[ts[:13]]["mod"][k] += tok
            t = tarifa(k)
            if t:
                over = gi > CLIFF
                horas[ts[:13]]["usd"] += (max(gi - cr, 0) * t[0] * (2 if over else 1)
                                          + cr * t[2] * (2 if over else 1) + cw * t[3] * (2 if over else 1)
                                          + go * t[1] * (1.5 if over else 1)) / 1e6
    fh.close()
    plan = None
    if "/.claude/" in ruta:
        plan = "Claude (Max)"
    elif "/.codex/" in ruta:
        plan = "OpenAI (Codex)"
    ccs = plan_ccs(fecha_ini) if "/.claude/" in ruta else None
    return {"dia": {k: {m: v for m, v in d.items()} for k, d in dia.items()},
            "turnos": dict(turnos), "horas": dict(horas), "proyecto": proy, "plan": ccs or plan}


def _dia_hora():
    """Registros vacios de una fuente: {dia, turnos, horas} (mismo formato que parsear/leer_bases)."""
    return {"dia": collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0, 0])),
            "turnos": collections.defaultdict(int),
            "horas": collections.defaultdict(lambda: {"tok": 0, "usd": 0.0, "mod": collections.defaultdict(int)})}


def _funde(dest, src):
    """Suma un registro de _dia_hora() en otro (para unir varias sesiones en el mismo plan/proyecto)."""
    for d, mods in src["dia"].items():
        for m, v in mods.items():
            a = dest["dia"][d][m]
            for i in range(4):
                a[i] += v[i]
    for d, n in src["turnos"].items():
        dest["turnos"][d] += n
    for h, v in src["horas"].items():
        dd = dest["horas"][h]
        dd["tok"] += v.get("tok", 0)
        dd["usd"] += v.get("usd", 0.0)
        for m, t in (v.get("mod") or {}).items():
            dd["mod"][m] += t


def parsear_pi(ruta):
    """Sesion de pi (agent): JSONL con un cabecera {type:session, cwd} y una linea por mensaje.

    Formato (igual que el lector de nodeclub-tracker): los turnos son mensajes assistant con
    message.usage = {input, output, cacheRead, cacheWrite, reasoning, cost:{total}}. `reasoning`
    NO va dentro de `output` (totalTokens = input+output), asi que se suma a la salida. Los
    timestamps van en UTC (iso con Z) y hay que pasarlos a hora local para no descuadrar el dia.
    Cada turno lleva su `provider` (nodeclub, anthropic, opencode-go, ...) y una misma sesion
    puede cambiar de proveedor a mitad, asi que se devuelven los registros por proveedor:
    {"proyecto": "pi · <proyecto>", "proveedores": {provider: {dia, turnos, horas}}}.
    """
    cwd, provs = None, None
    try:
        fh = open(ruta, encoding="utf-8", errors="ignore")
    except Exception:
        return None
    vistos = set()
    for linea in fh:
        try:
            d = json.loads(linea)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("type") == "session" and not cwd:
            cwd = d.get("cwd") or None
            if provs is None:
                provs = collections.OrderedDict()
        m = d.get("message")
        if not (isinstance(m, dict) and m.get("role") == "assistant" and isinstance(m.get("usage"), dict)):
            continue
        uso = m["usage"]
        ts = a_local(d.get("timestamp") or m.get("timestamp") or "")     # UTC -> hora local
        gi = uso.get("input", 0) or 0
        go = (uso.get("output", 0) or 0) + (uso.get("reasoning", 0) or 0)
        cr = uso.get("cacheRead", 0) or 0
        cw = uso.get("cacheWrite", 0) or 0
        modelo = m.get("model") or "sin-modelo"
        clave = f"{modelo}|{ts[:19]}|{gi}|{go}|{cr}|{cw}"     # si un turno se repitiera, no contar dos veces
        if clave in vistos:
            continue
        vistos.add(clave)
        prov = (m.get("provider") or "").strip() or "sin-proveedor"
        if prov not in provs:
            provs[prov] = _dia_hora()
        rec = provs[prov]
        if len(ts) >= 10:
            a = rec["dia"][ts[:10]][modelo]
            a[0] += gi; a[1] += go; a[2] += cr; a[3] += cw
            rec["turnos"][ts[:10]] += 1
        if len(ts) >= 13:
            tok = gi + go + cr + cw
            rec["horas"][ts[:13]]["tok"] += tok
            rec["horas"][ts[:13]]["mod"][modelo] += tok
            costo = ((uso.get("cost") or {}).get("total")) or 0     # pi ya trae el coste del turno (0 en planes planos)
            if costo:
                rec["horas"][ts[:13]]["usd"] += costo
            elif (t := tarifa(modelo)):
                over = gi > CLIFF
                rec["horas"][ts[:13]]["usd"] += (max(gi - cr, 0) * t[0] * (2 if over else 1)
                                                 + cr * t[2] * (2 if over else 1) + cw * t[3] * (2 if over else 1)
                                                 + go * t[1] * (1.5 if over else 1)) / 1e6
    fh.close()
    if provs is None or not any(r["turnos"].values() for r in provs.values()):
        return None
    if not cwd:
        cwd = os.path.basename(os.path.dirname(ruta))     # la carpeta lleva el cwd slugificado
    trozos = [x for x in (cwd or "").split("/") if x]
    return {"proyecto": f"pi · {'/'.join(trozos[-2:]) or 'sin proyecto'}", "proveedores": provs}


# La consulta de OpenCode recorre todos los mensajes con json_extract: con miles de mensajes cuesta,
# y el escaneo corre cada minuto. Se guarda el resultado y solo se repite si la base cambia
# (tamano o fecha de la base y de su -wal, donde SQLite escribe primero).
_OPENCODE = {}


def _firma_sqlite(ruta):
    firma = []
    for f in (ruta, f"{ruta}-wal"):
        try:
            st = os.stat(f)
            firma.append((st.st_mtime_ns, st.st_size))
        except OSError:
            firma.append(None)
    return tuple(firma)


def leer_bases():
    """OpenCode y Hermes no escriben .jsonl: guardan el consumo en SQLite.

    Devuelve {proyecto: {dia, turnos, horas}} para que cada aplicacion y cada proyecto cuenten
    por separado (Hermes entra como proyecto, no como plan).
    """
    proyectos = {}

    def saca(proyecto):
        if proyecto not in proyectos:
            proyectos[proyecto] = {
                "dia": collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0, 0])),
                "turnos": collections.defaultdict(int),
                "horas": collections.defaultdict(lambda: {"tok": 0, "usd": 0.0, "mod": collections.defaultdict(int)}),
            }
        return proyectos[proyecto]

    nombres = []

    def limpia_modelo(m):
        m = m or ""
        if m.strip().startswith("{"):
            try:
                d = json.loads(m)
                m = d.get("id") or d.get("modelID") or m
            except Exception:
                pass
        return m.strip()

    def mete(proyecto, plan, ts, modelo, gi, go, cr, cw, n=1):
        if not ts or len(str(ts)) < 10:
            return
        ts = a_local(ts)
        proy = saca((proyecto or "Hermes/OpenCode", plan or ""))
        k = limpia_modelo(modelo) or "sin-modelo"
        a = proy["dia"][ts[:10]][k]
        a[0] += gi; a[1] += go; a[2] += cr; a[3] += cw
        proy["turnos"][ts[:10]] += n
        if len(ts) >= 13:
            tok = gi + go + cr + cw
            proy["horas"][ts[:13]]["tok"] += tok
            proy["horas"][ts[:13]]["mod"][k] += tok
            tr = tarifa(k)
            if tr:
                over = gi > CLIFF
                proy["horas"][ts[:13]]["usd"] += (max(gi - cr, 0) * tr[0] * (2 if over else 1)
                                                  + cr * tr[2] * (2 if over else 1) + cw * tr[3] * (2 if over else 1)
                                                  + go * tr[1] * (1.5 if over else 1)) / 1e6

    def hora(v):
        v = v or 0
        if v > 1e11:            # milisegundos
            v /= 1000
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(v)) if v else ""

    def ultimos(partes, n=2):
        trozos = [x for x in (partes or "").split("/") if x]
        return "/".join(trozos[-n:]) if trozos else ""

    def _prov_de_sesion(m):
        """En el respaldo, session.model es un JSON con providerID; de ahi sale el plan."""
        if (m or "").strip().startswith("{"):
            try:
                return json.loads(m).get("providerID") or ""
            except Exception:
                return ""
        return ""

    # OpenCode: se lee MENSAJE a mensaje (trae el uso del turno y su hora). El total de la sesion
    # vive en la tabla session, pero su time_created es cuando ARRANCO la sesion: si se cerraba el
    # calculo con eso, todo el trabajo nuevo de una sesion larga se fechaba en su hora de inicio y
    # no entraba en la ventana de 5 h ni en el dia. La suma de mensajes cuadra exacta con el total
    # de la sesion (verificado: 0 desajustes en 626 sesiones), asi que es equivalente y ademas
    # correcto en el tiempo.
    for ruta in (f"{rutas.HOME}/.local/share/opencode/opencode.db", f"{rutas.HOME}/.local/share/opencode/opencode-.db"):
        if not os.path.exists(ruta):
            continue
        firma = _firma_sqlite(ruta)
        if _OPENCODE.get("ruta") == ruta and _OPENCODE.get("firma") == firma:
            filas = _OPENCODE["filas"]           # la base no ha cambiado: no se repite la consulta
            for direc, prov, m, creado, ti, to, tcr, tcw, n in filas:
                mete(f"OpenCode · {ultimos(direc) or 'sin proyecto'}", prov, hora(creado), m, ti or 0, to or 0, tcr or 0, tcw or 0, n or 1)
            nombres.append(f"OpenCode ({len(filas)})")
            break
        try:
            con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
            try:
                filas = list(con.execute("""
                    select s.directory,
                           json_extract(msg.data, '$.providerID'),
                           json_extract(msg.data, '$.modelID'),
                           min(msg.time_created),
                           sum(coalesce(json_extract(msg.data, '$.tokens.input'), 0)),
                           sum(coalesce(json_extract(msg.data, '$.tokens.output'), 0)),
                           sum(coalesce(json_extract(msg.data, '$.tokens.cache.read'), 0)),
                           sum(coalesce(json_extract(msg.data, '$.tokens.cache.write'), 0)),
                           count(*)
                    from message msg join session s on s.id = msg.session_id
                    where json_extract(msg.data, '$.modelID') is not null
                    group by s.directory, 2, 3, msg.time_created / 3600000"""))
                fuente = "mensajes"
            except sqlite3.Error:
                # respaldo: OpenCode sin tabla/uso por mensaje -> total de sesion (fecha de arranque)
                filas = [(direc, _prov_de_sesion(m), m, creado, ti, to, tcr, tcw, 1)
                         for m, ti, to, tcr, tcw, creado, direc in con.execute(
                             "select model, tokens_input, tokens_output, tokens_cache_read, tokens_cache_write, time_created, directory from session")]
                fuente = "sesiones"
            con.close()
            _OPENCODE.update(ruta=ruta, firma=firma, filas=filas)
            for direc, prov, m, creado, ti, to, tcr, tcw, n in filas:
                mete(f"OpenCode · {ultimos(direc) or 'sin proyecto'}", prov, hora(creado), m, ti or 0, to or 0, tcr or 0, tcw or 0, n or 1)
            nombres.append(f"OpenCode ({len(filas)})")
            break
        except Exception as e:
            log(f"opencode {ruta}: {type(e).__name__}: {e}")
    # Hermes: su cwd es el proyecto (Hermes no es un plan, es la app)
    for db in sorted(glob.glob(f"{rutas.HOME}/.hermes/profiles/*/state.db")):
        try:
            perfil = db.split("/profiles/")[-1].split("/")[0]
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            filas = list(con.execute("select model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, started_at, cwd, billing_provider, billing_base_url from sessions"))
            con.close()
            for m, i, o, cr, cw, ini, cwd, bprov, burl in filas:
                mete(f"Hermes/{perfil}" + (f" · {ultimos(cwd, 1)}" if cwd else ""), burl or bprov or "", hora(ini), m, i or 0, o or 0, cr or 0, cw or 0)
            nombres.append(f"Hermes/{perfil} ({len(filas)})")
        except Exception as e:
            log(f"hermes {db}: {type(e).__name__}: {e}")
    return proyectos, nombres


NOMBRE_FUENTE = {f"{rutas.HOME}/.claude/projects": "Claude Code", f"{rutas.HOME}/.codex/sessions": "Codex"}
