"""Avisos de escritorio de los servidores: web que no responde, servidor sin conexión, acceso rechazado, contenedor en bucle, disco casi lleno y
certificado a punto de caducar.

· Cada regla da, en cada pasada, «sí», «no» o «no sé» (dato viejo, base ocupada, el Mac sin red, histéresis): «no sé» no mueve nada, para que un hueco de datos
  ni avise ni rearme. Un aviso necesita que la condición dure (`confirma`), se da una vez, y solo se rearma cuando lleva `rearme` sin cumplirse; después de
  avisar hay un enfriamiento mínimo. La conexión y el disco salen de la caché de servidores (funciona aunque la base falle); las webs y los bucles, de la base.
· El texto no lleva nombres, dominios ni IPs (se ve en la pantalla de bloqueo). Solo se da por avisado lo que macOS aceptó; si falla se reintenta unas veces.
· Con los avisos apagados (global o de un servidor) todo se evalúa igual pero lo presente se anota como avisado, así que al reactivarlos no cae nada acumulado.
· La primera pasada con datos, un Mac que acaba de despertar y un fichero de estado hostil no generan avisos. Solo escribe el hilo trabajador de la barra."""
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time

from . import plataforma, rutas, servidores_copias, servidores_historico as sh, servidores_ciclo, servidores_panel, webs_sondeo
from .config_y_estado import cfg, log
from .servidores import servidores_validos
from .servidores_datos import FueraDeRango, porcentaje

# regla: (confirma_s, rearme_s, enfriamiento_s, crítica)
REGLAS = {"web_caida": (300, 600, 3600, True), "sin_conexion": (120, 600, 3600, True), "acceso_rechazado": (120, 600, 6 * 3600, True),
          "bucle": (0, 0, 3600, True), "disco_lleno": (600, 1800, 12 * 3600, False), "certificado": (600, 1800, 12 * 3600, False),
          "copia_antigua": (3600, 6 * 3600, 24 * 3600, False), "despliegue_fallido": (600, 1800, 12 * 3600, False), "panel_sin_lectura": (6 * 3600, 3600, 24 * 3600, False)}
CLASE = {"web_caida": "web", "sin_conexion": "conexión", "acceso_rechazado": "acceso", "bucle": "contenedor", "disco_lleno": "disco", "certificado": "certificado", "copia_antigua": "copia",
         "despliegue_fallido": "despliegue", "panel_sin_lectura": "panel"}
TEXTO = {"web_caida": "Una web de uno de tus servidores no responde correctamente desde hace más de 5 minutos.",
         "sin_conexion": "Uno de tus servidores no responde desde hace un rato.",
         "acceso_rechazado": "El acceso a uno de tus servidores ha cambiado: la conexión se rechaza.",
         "bucle": "Un contenedor de uno de tus servidores se está reiniciando en bucle.",
         "disco_lleno": "El disco de uno de tus servidores está casi lleno.",
         "certificado": "El certificado de una web está a punto de caducar.",
         "copia_antigua": "Una copia de seguridad de uno de tus servidores lleva más tiempo del normal sin hacerse.",
         "despliegue_fallido": "El último despliegue de una aplicación de uno de tus servidores ha fallado.",
         "panel_sin_lectura": "CostGuard no puede leer el panel de uno de tus servidores."}
PANEL_REGLAS = frozenset({"copia_antigua", "despliegue_fallido"})
VENTANA_PANEL_S = servidores_panel.VENTANA_S
DESPLIEGUE_MIN_S, DESPLIEGUE_MAX_S = 600, 7 * 86400
BANDA = "banda"             # entre el umbral de aviso y el de rearme: antes de avisar vale «no», después «no sé»
DISCO_ALTO, DISCO_BAJO = 90, 85
CERT_ALTO, CERT_BAJO = 7, 14
VENTANA_MIN_S = 900
MAX_BYTES = 256 * 1024
MAX_ENTRADAS = 4000
GRACIA_PODA_S = 86400
TOPE_HORA, TOPE_NO_CRITICAS_HORA = 8, 3
INTENTOS, REINTENTO_S = 3, 300
REPOSO_S = 120
RECHAZOS = ("autenticacion", "clave de host no confiada")
CLAVE = re.compile(r"(?:" + "|".join(REGLAS) + r")\|[0-9a-f]{32}(?:\|[0-9a-f]{32})?")
CAMPOS = ("desde", "avisado", "libre", "ultimo", "reintento_en", "visto")


def ruta():
    return os.path.join(rutas.DATOS, "avisos_servidores.json")


def _t(v, ahora, holgura=0):
    """Marca de tiempo sana: entero, nunca booleano ni futuro (un reloj que estuvo adelantado no silencia nada). None si no vale."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0 or v > 10 ** 11:
        return None
    return int(min(v, ahora + holgura))


def leer(ahora):
    """(estado, existía). Lo ilegible, enorme u hostil se ignora entrada a entrada; un fichero roto se aparta como `.corrupto` y se empieza de cero."""
    vacio = {"base": False, "envios": [], "entradas": {}, "paneles": []}
    try:
        # sin seguir enlaces y sin bloquearse con un FIFO o un dispositivo: solo se lee un fichero normal de tamaño razonable
        fd = os.open(ruta(), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise ValueError("no es un fichero")
            crudo = f.read(MAX_BYTES + 1)
        if len(crudo) > MAX_BYTES:
            raise ValueError("grande")
        d = json.loads(crudo.decode("utf-8"))
        if not isinstance(d, dict):
            raise ValueError("forma")
    except FileNotFoundError:
        return vacio, False
    except (OSError, ValueError, RecursionError):
        try:
            os.replace(ruta(), ruta() + ".corrupto")
        except OSError:
            pass
        return vacio, False
    entradas = {}
    crudas = d.get("entradas") if isinstance(d.get("entradas"), dict) else {}
    for k, e in list(crudas.items())[:MAX_ENTRADAS]:
        if isinstance(k, str) and CLAVE.fullmatch(k) and isinstance(e, dict):
            n = {c: _t(e.get(c), ahora, REINTENTO_S if c == "reintento_en" else 0) for c in CAMPOS}
            n["intentos"] = e.get("intentos") if isinstance(e.get("intentos"), int) and not isinstance(e.get("intentos"), bool) and 0 <= e["intentos"] <= 100 else 0
            entradas[k] = n
    envios = [t for t in (_t(x, ahora) for x in (d.get("envios") if isinstance(d.get("envios"), list) else [])[:200]) if t is not None and ahora - t < 3600]
    paneles = [p for p in (d.get("paneles") if isinstance(d.get("paneles"), list) else [])[:200] if isinstance(p, str) and re.fullmatch(r"[0-9a-f]{32}", p)]
    return {"base": d.get("base") is True, "envios": envios, "entradas": entradas, "paneles": paneles}, True


def _guardar(estado):
    os.makedirs(rutas.DATOS, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".avisos-srv-", dir=rutas.DATOS)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        from .exportacion import reemplazar
        reemplazar(tmp, ruta())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def paso(e, v, regla, ahora):
    """Aplica «sí/no/no sé» (True/False/None) a la entrada `e` y dice si toca avisar ahora."""
    confirma, rearme, enfria, _ = REGLAS[regla]
    if v is None:
        return False
    if v == BANDA:
        if e.get("avisado") is not None:
            return False
        v = False
    if v:
        e["libre"] = None
        if e.get("desde") is None:
            e["desde"] = ahora
        if e.get("avisado") is not None or ahora - e["desde"] < confirma:
            return False
        if e.get("ultimo") is not None and ahora - e["ultimo"] < enfria:
            return False
        return (e.get("reintento_en") or 0) <= ahora
    if e.get("avisado") is None:                        # un parpadeo antes de avisar no cuenta
        e["desde"], e["intentos"], e["reintento_en"] = None, 0, None
        return False
    if e.get("libre") is None:
        e["libre"] = ahora
    if ahora - e["libre"] >= rearme:
        e.update(desde=None, avisado=None, libre=None, intentos=0, reintento_en=None)
    return False


def _fresco(t, ahora, ventana):
    return t is not None and isinstance(t, (int, float)) and 0 <= ahora - t <= ventana


def _umbral(valor, alto, bajo, mayor):
    """Sí/no/no sé con histéresis. `mayor`: la condición es «valor ≥ alto» (disco); si no, «valor ≤ alto» (certificado)."""
    if valor is None:
        return None
    if mayor:
        return True if valor >= alto else (False if valor < bajo else BANDA)
    return True if valor <= alto else (False if valor > bajo else BANDA)


def _desde_cache(servidores, cache, ahora, ventana, sin_red):
    out = {}
    for s in servidores:
        e = cache.get(s["nombre"])
        if not isinstance(e, dict):
            continue
        sid, ok_en, intento = s["id"], e.get("ok_en") or 0, e.get("intento_en") or 0
        error = e.get("error") if isinstance(e.get("error"), str) else ""
        reciente = _fresco(ok_en, ahora, ventana)
        rechazo = error in RECHAZOS
        out[("acceso_rechazado", sid)] = True if (rechazo and ok_en > 0) else (False if reciente and not error else None)
        caido = bool(error) and not rechazo and not error.startswith("falta la clave") and ok_en > 0 and _fresco(intento, ahora, ventana) and ahora - ok_en >= ventana
        if caido:
            out[("sin_conexion", sid)] = None if sin_red() else True
        else:
            out[("sin_conexion", sid)] = False if reciente and not error else None
        pct = None
        if reciente and not error:
            d = (e.get("datos") or {}).get("disco") if isinstance(e.get("datos"), dict) else None
            try:
                pct = porcentaje(d.get("usado"), d.get("total")) if isinstance(d, dict) else None
            except FueraDeRango:
                pct = None
        out[("disco_lleno", sid)] = _umbral(pct, DISCO_ALTO, DISCO_BAJO, True)
    return out


def _clave_despliegue(sid, servicio):
    return hashlib.sha256(f"{sid}\0despliegue\0{servicio}".encode()).hexdigest()[:32]


def _desde_panel(servidores, entradas_cache, ahora):
    """(frescos, con copias al día, condiciones): paneles con lectura buena reciente, paneles cuyas copias también se anotaron, y las condiciones que salen de la caché del
    panel (despliegue fallido y panel sin lectura). Un panel de otra cuenta (se quitó o cambió de dirección) no cuenta."""
    frescos, copias_al_dia, out = {}, {}, {}
    for s in servidores:
        e = entradas_cache.get(s["nombre"])
        p = e.get("panel") if isinstance(e, dict) and isinstance(e.get("panel"), dict) else None
        cta = servidores_panel.cuenta(s)
        if p is None or not cta or p.get("cuenta") != cta:
            continue
        ok_en = p.get("ok_en") if isinstance(p.get("ok_en"), (int, float)) and not isinstance(p.get("ok_en"), bool) else None
        empezo = p.get("empezo_en") if isinstance(p.get("empezo_en"), (int, float)) and not isinstance(p.get("empezo_en"), bool) else None
        fresco = ok_en is not None and _fresco(ok_en, ahora, VENTANA_PANEL_S) and p.get("error") != "alcance"
        sin_lectura = ahora - (ok_en if ok_en is not None else (empezo if empezo is not None else ahora))
        out[("panel_sin_lectura", s["id"])] = True if (sin_lectura > 0 and not fresco and (ok_en is not None or empezo is not None)) else (False if fresco else None)
        if not fresco:
            continue
        frescos[s["id"]] = p
        if _fresco(p.get("copias_en"), ahora, VENTANA_PANEL_S):
            copias_al_dia[s["id"]] = p
        for sv in (p.get("servicios") or [])[:2000]:
            if not isinstance(sv, dict) or not isinstance(sv.get("id"), str) or sv.get("tipo") not in ("application", "compose"):
                continue
            d = sv.get("despliegue")
            edad = ahora - d if isinstance(d, (int, float)) and not isinstance(d, bool) else None
            if sv.get("estado") == "error" and edad is not None and DESPLIEGUE_MIN_S < edad <= DESPLIEGUE_MAX_S:
                v = True
            elif sv.get("estado") in ("done", "running", "idle") or (sv.get("estado") == "error" and edad is not None and edad > DESPLIEGUE_MAX_S):
                v = False
            else:
                v = None                                  # un error de hace menos de 10 minutos o de hace meses no avisa: lo viejo es informativo en la pestaña
            out[("despliegue_fallido", s["id"], _clave_despliegue(s["id"], sv["id"]))] = v
    return frescos, copias_al_dia, out


def _desde_base(servidores, ahora, frescos=()):
    """Webs (estado confirmado y certificado), bucles de contenedores y copias del panel. None si la base no se puede leer ahora: entonces no sé nada de ellas."""
    ids = {s["id"] for s in servidores}
    out = {}
    try:
        con = sh.abrir(solo_lectura=True)
    except Exception:
        return None
    try:
        for sid, clave, estado, ultima, cert in con.execute("SELECT servidor_id, clave, estado, ultima, cert_dias FROM webs").fetchall():
            if sid not in ids:
                continue
            fresca = _fresco(ultima, ahora, VENTANA_MIN_S)
            out[("web_caida", sid, clave)] = (estado == "caida") if fresca else None
            out[("certificado", sid, clave)] = _umbral(cert, CERT_ALTO, CERT_BAJO, False) if fresca and cert is not None else None
        con_bucle = {r[0] for r in con.execute("SELECT DISTINCT servidor_id FROM eventos WHERE evento='reinicios_en_bucle' AND ts BETWEEN ? AND ?",
                                                (ahora - 900, ahora + 60)).fetchall()}
        for sid in ids:
            out[("bucle", sid)] = sid in con_bucle
        for sid, clave, ultima_ok, intervalo in con.execute("SELECT servidor_id, clave, ultima_ok, intervalo_s FROM copias").fetchall():
            if sid not in ids:
                continue
            ev = servidores_copias.evaluar({"ultima_ok": ultima_ok, "intervalo_s": intervalo}, ahora) if sid in frescos else None
            out[("copia_antigua", sid, clave)] = True if ev == "antigua" else (False if ev == "al_dia" else None)       # «nunca tuvo copia buena» no avisa
    except Exception:
        return None
    finally:
        con.close()
    return out


def _texto(reglas):
    if len(reglas) == 1:
        return TEXTO[reglas[0]]
    clases = sorted({CLASE[r] for r in reglas})
    return f"{len(reglas)} avisos de servidores: {', '.join(clases)}."


TEXTO_PRUEBA = "Prueba de CostGuard: si ves este aviso, las notificaciones de servidores funcionan."
ESPERA_PRUEBA_S, MAX_PRUEBAS_HORA = 30, 10
_PRUEBA = threading.Lock()
_PRUEBAS = []               # instantes (monótonos) de los intentos de la última hora


def probar(cuerpo, conf=None, canal=plataforma.avisar, reloj=time.monotonic, es_mac=None):
    """POST /servidores/avisos/probar → (código, dict). Manda una notificación de prueba (texto distinto de los reales). Una a la vez, 1 cada 30 s y 10 por hora para
    toda la aplicación; el intento cuenta aunque falle. Dice si los avisos reales están apagados (global o del servidor pedido): la prueba no los respeta, a propósito."""
    if not isinstance(cuerpo, dict) or set(cuerpo) - {"servidor"}:
        return 400, {"ok": False, "error": "entrada no válida"}
    sid = cuerpo.get("servidor")
    if sid is not None and not (isinstance(sid, str) and re.fullmatch(r"[0-9a-f]{32}", sid)):
        return 400, {"ok": False, "error": "entrada no válida"}
    conf = conf if conf is not None else cfg()
    apagados = []
    if conf.get("avisos_servidores") is False:
        apagados.append("global")
    if sid and any(s.get("id") == sid and s.get("avisos") is False for s in servidores_validos(conf.get("servidores"))):
        apagados.append("servidor")
    if not _PRUEBA.acquire(blocking=False):
        return 429, {"ok": False, "espera_s": ESPERA_PRUEBA_S}
    try:
        ahora = reloj()
        _PRUEBAS[:] = [t for t in _PRUEBAS if ahora - t < 3600]
        if _PRUEBAS and ahora - _PRUEBAS[-1] < ESPERA_PRUEBA_S:
            return 429, {"ok": False, "espera_s": int(ESPERA_PRUEBA_S - (ahora - _PRUEBAS[-1])) + 1}
        if len(_PRUEBAS) >= MAX_PRUEBAS_HORA:
            return 429, {"ok": False, "espera_s": int(3600 - (ahora - _PRUEBAS[0])) + 1}
        _PRUEBAS.append(ahora)
        mac = (sys.platform == "darwin") if es_mac is None else es_mac
        if not mac:
            return 200, {"ok": False, "motivo": "solo_macos", "apagados": apagados}
        ok = bool(canal("CostGuard", TEXTO_PRUEBA))
        log("aviso de prueba enviado" if ok else "aviso de prueba: macOS no lo aceptó")
        return 200, {"ok": ok, "motivo": "" if ok else "no_aceptado", "apagados": apagados}
    finally:
        _PRUEBA.release()


class Vigilante:
    def __init__(self, reloj=time.time, mono=time.monotonic, canal=plataforma.avisar, sin_red=webs_sondeo.sin_red, canal_real=None):
        self.reloj, self.mono, self.canal, self.sin_red = reloj, mono, canal, sin_red
        self.canal_real = (sys.platform == "darwin") if canal_real is None else canal_real
        self._ref = None

    def vigilar(self, conf=None, cache=None):
        """Una pasada. Devuelve las reglas que se avisaron. Nunca lanza: un fallo se apunta con su tipo y se sigue."""
        try:
            return self._vigilar(conf if conf is not None else cfg(), cache)
        except Exception as e:
            log(f"avisos de servidores: {type(e).__name__}")
            return []

    def _olvida_confirmaciones(self, ahora):
        estado, existia = leer(ahora)
        if not existia:
            return
        for e in estado["entradas"].values():
            e["libre"] = None
            if e.get("avisado") is None:
                e["desde"], e["reintento_en"] = None, None
        _guardar(estado)

    def _vigilar(self, conf, cache):
        ahora = int(self.reloj())
        ref = ahora - self.mono()
        arranca = self._ref is None
        despierta = not arranca and abs(ref - self._ref) > REPOSO_S
        self._ref = ref
        if arranca or despierta:
            self._olvida_confirmaciones(ahora)           # al arrancar o despertar, lo visto antes (el tiempo parado del Mac) no confirma nada
            if despierta:
                return []
        servidores = [s for s in servidores_validos(conf.get("servidores")) if s.get("id")]
        if not servidores:
            return []                                    # sin servidores legibles no se poda ni se avisa
        cache = cache if cache is not None else servidores_ciclo.leer_cache()
        entradas_cache = cache.get("servidores") if isinstance(cache.get("servidores"), dict) else {}
        normal, _ = servidores_ciclo.intervalos(conf)
        ventana = max(VENTANA_MIN_S, 3 * normal + 60)
        red = {}

        def sin_red():
            if "v" not in red:
                red["v"] = bool(self.sin_red())
            return red["v"]
        conds = _desde_cache(servidores, entradas_cache, ahora, ventana, sin_red)
        frescos, copias_al_dia, de_panel = _desde_panel(servidores, entradas_cache, ahora)
        conds.update(de_panel)
        base = _desde_base(servidores, ahora, copias_al_dia)
        if base:
            conds.update(base)
        estado, existia = leer(ahora)
        antes = json.dumps(estado, sort_keys=True)
        apagado_global = conf.get("avisos_servidores") is False
        mudos = {s["id"] for s in servidores if s.get("avisos") is False}
        entradas = estado["entradas"]
        debidas = []
        for k, v in conds.items():
            regla, sid = k[0], k[1]
            clave = "|".join(k)
            e = entradas.setdefault(clave, {c: None for c in CAMPOS} | {"intentos": 0})
            if ahora - (e.get("visto") or 0) > 3600:
                e["visto"] = ahora                    # basta para la poda con gracia; no se reescribe el fichero en cada pasada
            if paso(e, v, regla, ahora):
                debidas.append((clave, regla, sid))
        vigentes = {"|".join(k) for k in conds}
        for clave in [c for c, e in entradas.items() if c not in vigentes and ahora - (e.get("visto") or 0) > GRACIA_PODA_S]:
            del entradas[clave]
        if not estado["base"]:
            for k, v in conds.items():
                if v is True:
                    entradas["|".join(k)].update(avisado=ahora, ultimo=ahora)       # línea base: lo que ya está pasando al arrancar no avisa
        nuevos_paneles = {sid for sid in copias_al_dia if sid not in estado["paneles"]}         # la línea base de un panel espera a que sus copias también estén anotadas
        for k, v in conds.items():
            if v is True and k[0] in PANEL_REGLAS and k[1] in nuevos_paneles:
                entradas["|".join(k)].update(avisado=ahora, ultimo=ahora)          # línea base de cada panel: lo que ya estaba mal en su primera lectura no avisa
        estado["paneles"] = sorted(set(estado["paneles"]) | set(copias_al_dia))[:200]
        silencio = apagado_global or not estado["base"]
        enviar = []
        for clave, regla, sid in debidas:
            if silencio or sid in mudos:
                entradas[clave].update(avisado=ahora, ultimo=ahora)
            else:
                enviar.append((clave, regla))
        avisadas = []
        if enviar:
            criticas = any(REGLAS[r][3] for _, r in enviar)
            if len(estado["envios"]) < (TOPE_HORA if criticas else min(TOPE_HORA, TOPE_NO_CRITICAS_HORA)):
                ok = self.canal("CostGuard", _texto([r for _, r in enviar])) if self.canal_real else True
                for clave, regla in enviar:
                    e = entradas[clave]
                    if ok:
                        e.update(avisado=ahora, ultimo=ahora, intentos=0, reintento_en=None)
                        avisadas.append(regla)
                    else:
                        e["intentos"] = (e.get("intentos") or 0) + 1
                        if e["intentos"] >= INTENTOS:
                            e.update(avisado=ahora, ultimo=ahora, intentos=0, reintento_en=None)       # no se entrega: se deja de insistir
                        else:
                            e["reintento_en"] = ahora + REINTENTO_S
                if ok:
                    estado["envios"].append(ahora)
        if not estado["base"] and any(v is not None for v in conds.values()):
            estado["base"] = True
        if len(entradas) > MAX_ENTRADAS:
            estado["entradas"] = dict(sorted(entradas.items(), key=lambda kv: kv[1].get("visto") or 0)[-MAX_ENTRADAS:])
        if not existia or json.dumps(estado, sort_keys=True) != antes:
            _guardar(estado)
        return avisadas
