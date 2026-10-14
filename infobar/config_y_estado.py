"""Configuracion (config.json), estado de la interfaz (logs/ui_estado.json), cache del escaneo
(state.json) y log.

config.json lo escriben la barra, el configurador del panel y el CLI: toda escritura pasa por
guardar_config(), que bloquea, FUSIONA con lo que haya en disco y reemplaza el fichero de golpe.
Asi dos escritores a la vez no se pisan y nadie borra claves que no conoce.
"""
import contextlib, fcntl, glob, json, os, tempfile, time

from . import rutas


VERSION_ESTADO = 13         # subir cuando cambie el formato del cache o lo que cuenta el parser: obliga a reescanear


def log(msg):
    os.makedirs(os.path.dirname(rutas.LOG), exist_ok=True)
    with open(rutas.LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


# Lo de los servidores vive en servidores.json (fuera de git): config.json esta versionado y no
# debe llevar hosts ni usuarios. cfg() junta los dos y guardar_config() reparte; nadie mas lo nota.
CLAVES_SERVIDORES = ("servidores", "ips_conocidas", "intervalo_normal_s", "intervalo_incidencia_s")
_ULTIMO_SRV = {}
_MIGRADO = []
COPIAS_CONFIG = 5           # copias config.json.bak.<fecha> que se guardan al escribir
_ULTIMO_CFG = {}            # ultima config leida bien: si una lectura falla, se usa esta


def cfg():
    """config.json con sus valores por defecto. Si el fichero esta a medio escribir o roto, devuelve
    la ultima version buena en vez de los defectos (que harian desaparecer planes y servidores)."""
    global _ULTIMO_CFG
    d = {"limite_hora_tokens": 8_000_000, "limite_5h_tokens": 0, "limite_semana_tokens": 0}
    if os.path.exists(rutas.CONFIG):
        try:
            with open(rutas.CONFIG, encoding="utf-8") as f:
                leido = json.load(f)
            if isinstance(leido, dict):
                _ULTIMO_CFG = leido
        except Exception as e:
            log(f"config.json ilegible ({type(e).__name__}); uso la ultima version buena")
        d.update(_ULTIMO_CFG)
    if not _MIGRADO:
        _MIGRADO.append(1)
        _migra_servidores()
    d.update(_lee_servidores())
    return d


def _lee_servidores():
    """servidores.json (o su ultima version buena si esta a medio escribir)."""
    global _ULTIMO_SRV
    if os.path.exists(rutas.CONFIG_SERVIDORES):
        try:
            with open(rutas.CONFIG_SERVIDORES, encoding="utf-8") as f:
                leido = json.load(f)
            if isinstance(leido, dict):
                _ULTIMO_SRV = {k: v for k, v in leido.items() if k in CLAVES_SERVIDORES}
        except Exception as e:
            log(f"servidores.json ilegible ({type(e).__name__}); uso la ultima version buena")
    else:
        _ULTIMO_SRV = {}
    return dict(_ULTIMO_SRV)


def _migra_servidores():
    """Una vez: si config.json aun trae servidores (instalaciones anteriores), se pasan a
    servidores.json y se quitan de config.json (con copia de seguridad)."""
    try:
        actual = _lee_json(rutas.CONFIG)
        mover = {k: actual[k] for k in CLAVES_SERVIDORES if k in actual}
        if not mover:
            return
        _guarda_servidores(mover)
        with _bloqueo(rutas.CONFIG):
            actual = _lee_json(rutas.CONFIG)
            _copia_config()
            _escribe_json(rutas.CONFIG, {k: v for k, v in actual.items() if k not in CLAVES_SERVIDORES})
        log("servidores movidos de config.json a servidores.json")
    except Exception as e:
        log(f"migracion de servidores: {type(e).__name__}: {e}")


@contextlib.contextmanager
def _bloqueo(ruta):
    """Bloqueo exclusivo entre procesos (barra, panel, CLI) sobre <ruta>.lock."""
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(f"{ruta}.lock", "a") as fl:
        fcntl.flock(fl, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fl, fcntl.LOCK_UN)


def _escribe_json(ruta, datos):
    """Escritura atomica (tmp + os.replace) con permisos 0600: puede llevar hosts y usuarios."""
    tmp = f"{ruta}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)
    os.replace(tmp, ruta)


def _lee_json(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _copia_config():
    if os.path.exists(rutas.CONFIG):
        copia = f"{rutas.CONFIG}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
        with open(rutas.CONFIG, "rb") as src:
            datos = src.read()
        fd = os.open(copia, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(datos)
        for vieja in sorted(glob.glob(f"{rutas.CONFIG}.bak.*"))[:-COPIAS_CONFIG]:
            os.remove(vieja)


def _guarda_servidores(parche):
    """Fusiona y guarda servidores.json (atomico, 0600, bajo su propio bloqueo)."""
    with _bloqueo(rutas.CONFIG_SERVIDORES):
        actual = _lee_json(rutas.CONFIG_SERVIDORES)
        nuevo = dict(actual)
        nuevo.update(parche)
        if nuevo != actual or not os.path.exists(rutas.CONFIG_SERVIDORES):
            _escribe_json(rutas.CONFIG_SERVIDORES, nuevo)
        return nuevo


def guardar_config(parche):
    """Fusiona `parche` (claves de primer nivel) y lo guarda de forma atomica: lo de servidores en
    servidores.json y el resto en config.json. Las claves que no vienen se conservan. Antes de
    escribir config.json deja una copia fechada y conserva las COPIAS_CONFIG ultimas."""
    srv = {k: v for k, v in parche.items() if k in CLAVES_SERVIDORES}
    parche = {k: v for k, v in parche.items() if k not in CLAVES_SERVIDORES}
    if srv:
        _guarda_servidores(srv)
    if not parche:
        return cfg()
    with _bloqueo(rutas.CONFIG):
        actual = {}
        if os.path.exists(rutas.CONFIG):
            try:
                with open(rutas.CONFIG, encoding="utf-8") as f:
                    actual = json.load(f)
            except ValueError:
                # nunca reescribir encima de un fichero que no se entiende: se perderia todo lo demas
                raise ValueError("config.json no es JSON valido; arreglalo a mano antes de guardar")
            if not isinstance(actual, dict):
                raise ValueError("config.json no es un objeto JSON")
        nuevo = dict(actual)
        nuevo.update(parche)
        if nuevo == actual and os.path.exists(rutas.CONFIG):
            return actual                                # nada que guardar: sin copia repetida
        _copia_config()
        _escribe_json(rutas.CONFIG, nuevo)
    return nuevo


def leer_ui():
    """Estado de la interfaz (tema, pestana, servidor activo...): no es configuracion, cambia con
    cada clic, y por eso vive aparte de config.json. El tema se migra de config.json la primera vez."""
    ui = _lee_json(rutas.UI_ESTADO)
    if "tema" not in ui and cfg().get("tema"):
        ui["tema"] = cfg()["tema"]
    return ui


def guardar_ui(parche):
    with _bloqueo(rutas.UI_ESTADO):
        ui = leer_ui()
        ui.update(parche)
        _escribe_json(rutas.UI_ESTADO, ui)
    return ui


def leer_estado():
    if os.path.exists(rutas.STATE):
        try:
            with open(rutas.STATE) as f:
                s = json.load(f)
            if s.get("version") == VERSION_ESTADO:
                return s
        except Exception:
            pass
    return {"version": VERSION_ESTADO, "ficheros": {}}


def guardar_estado(s):
    """state.json lo escriben la barra y los hilos del servidor local (cada /origenes escanea):
    temporal unico (mkstemp) y bloqueo, para que dos escritores no se pisen ni se rompa."""
    s["version"] = VERSION_ESTADO
    with _bloqueo(rutas.STATE):
        fd, tmp = tempfile.mkstemp(prefix=".state.", dir=os.path.dirname(rutas.STATE))
        with os.fdopen(fd, "w") as f:
            json.dump(s, f)
        os.replace(tmp, rutas.STATE)


def olvidar_cache():
    """Tras cambiar alias/planes desde el panel: el plan va guardado en cada fichero cacheado, asi
    que se descarta el cache entero y el siguiente escaneo lo recalcula con la config nueva."""
    with _bloqueo(rutas.STATE):
        if os.path.exists(rutas.STATE):
            os.remove(rutas.STATE)
