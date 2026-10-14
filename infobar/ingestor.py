"""Ingesta de las cinco fuentes al almacén: una vuelta acotada en tiempo, reanudable, con un único escritor.

Solo la barra (`HiloIngesta`) y `costbar.py --ingerir` escriben. El panel, el servidor local y el temporizador de la barra
solo consultan (`consulta.py`), y este módulo no se importa desde ellos (lo vigila un test).
"""
import glob
import json
import os
import threading
import time
import traceback

from . import almacen, ingesta, ingesta_claude, ingesta_codex, ingesta_hermes, ingesta_opencode, ingesta_pi, rutas
from .config_y_estado import log

PRESUPUESTO_S = 5.0            # tiempo máximo de una vuelta: el latido (90 s) se refresca al empezar cada una
PAUSA_PENDIENTE_S = 0.05       # entre vueltas mientras queda trabajo, para no acaparar la CPU ni el disco
PAUSA_AL_DIA_S = 30            # con todo al día (un tercio del TTL del latido)
CADA_MANTENIMIENTO_S = 3600
CADA_COMPROBACION_S = 86400

JSONL = (("claude", ingesta_claude, "{home}/.claude/projects"), ("codex", ingesta_codex, "{home}/.codex/sessions"),
         ("pi", ingesta_pi, "{home}/.pi/agent/sessions"))


def _firma(ruta):
    st = os.stat(ruta)
    return (st.st_ino, st.st_size, st.st_mtime_ns)


class Ingestor:
    """Estado de una ingesta en curso. Se usa desde un solo hilo (la conexión de SQLite es de ese hilo)."""

    def __init__(self, home=None, ruta_base=None, rol="barra"):
        self.home = home or rutas.HOME
        self.ruta_base = ruta_base
        self.quien = almacen.identidad(rol)
        self.con = None
        self.espacio = None
        self.al_dia = {}                      # ruta -> firma de la última pasada que terminó entera
        self.leidos = {}                      # id_fuente -> firma que la base ya tenía al abrir
        self.inodo = None
        self.ultimo_mantenimiento = 0.0
        self.ultima_comprobacion = 0.0

    def _ruta(self):
        return os.path.abspath(self.ruta_base or rutas.ALMACEN)

    def _abre(self):
        """Conexión del hilo. Si el fichero de la base cambió de inodo o desapareció (borrada o restaurada con la barra en
        marcha) se cierra y se reabre: escribir en un fichero borrado perdería la ingesta sin avisar."""
        if self.con is not None:
            try:
                igual = os.stat(self._ruta()).st_ino == self.inodo
            except OSError:
                igual = False
            if not igual:
                self.con.close()
                self.con, self.espacio = None, None
                self.al_dia.clear()
        if self.con is None:
            self.con = almacen.abrir(self.ruta_base)
            self.inodo = os.stat(self._ruta()).st_ino
            self._carga_al_dia()
        return self.con

    def _carga_al_dia(self):
        """Lo que la base ya sabe de cada fichero: si su firma no cambió y lo que queda por leer cabe en una pasada, no hace
        falta entrar (un arranque no repasa los ~1.400 ficheros con una transacción cada uno)."""
        self.leidos = {}
        for f in self.con.execute("SELECT f.id_fuente, l.inodo, l.tam, l.mtime_ns, l.desplazamiento FROM fuentes f "
                                  "JOIN lecturas l ON l.fuente_id=f.id"):
            if f[4] >= 0 and f[2] - f[4] < ingesta.MAX_LECTURA:
                self.leidos[f[0]] = (f[1], f[2], f[3])

    def _al_dia(self, ruta, firma):
        return self.al_dia.get(ruta) == firma or self.leidos.get(ingesta.id_fuente(ruta, self.home)) == firma

    def cierra(self):
        if self.con is not None:
            try:
                almacen.soltar_escritura(self.con, self.quien)
            except Exception:                                # noqa: BLE001
                pass
            self.con.close()
            self.con = None

    def vuelta(self, presupuesto_s=PRESUPUESTO_S, ahora=None):
        """Una vuelta sobre todas las fuentes. Devuelve {escritor, nuevos, subidos, anomalias, ficheros, completo, s}."""
        t0 = time.monotonic()
        res = {"escritor": False, "nuevos": 0, "subidos": 0, "anomalias": 0, "ficheros": 0, "fallos": 0, "completo": False, "s": 0.0}
        con = self._abre()
        if not almacen.adquirir_escritura(con, self.quien):
            return res
        res["escritor"] = True
        if self.espacio is None:
            self.espacio = almacen.espacio(con, self.quien)
        limite = t0 + presupuesto_s
        pendiente = False

        def suma(r):
            for k in ("nuevos", "subidos", "anomalias"):
                res[k] += r.get(k, 0)

        for agente, modulo, patron in JSONL:
            raiz = patron.format(home=self.home)
            for f in sorted(glob.glob(os.path.join(raiz, "**", "*.jsonl"), recursive=True)):
                try:
                    firma = _firma(f)
                except OSError:
                    continue
                if self._al_dia(f, firma):
                    continue
                if time.monotonic() > limite:
                    pendiente = True
                    continue
                res["ficheros"] += 1
                try:
                    r = ingesta.ingerir_fichero(con, self.quien, self.espacio, f, agente, modulo.extractor, self.home,
                                                contexto=modulo.contexto_de_fichero(f), ahora=ahora)
                except almacen.SinEscritura:
                    raise
                except Exception as e:                       # noqa: BLE001 — un fichero malo no para el resto
                    log(f"ingesta {agente}: {type(e).__name__}")
                    res["fallos"] += 1
                    continue
                suma(r)
                if r["completo"]:
                    self.al_dia[f] = firma
                else:
                    pendiente = True
        bases = [("opencode", ingesta_opencode, os.path.join(self.home, ".local", "share", "opencode", n))
                 for n in ("opencode.db", "opencode-.db")]
        bases += [("hermes", ingesta_hermes, d) for d in ingesta_hermes.bases(self.home)]
        for agente, modulo, ruta in bases:
            if not os.path.exists(ruta):
                continue
            if time.monotonic() > limite:
                pendiente = True
                continue
            res["ficheros"] += 1
            try:
                r = modulo.ingerir_base(con, self.quien, self.espacio, ruta, self.home, ahora=ahora)
            except almacen.SinEscritura:
                raise
            except Exception as e:                           # noqa: BLE001
                log(f"ingesta {agente}: {type(e).__name__}")
                res["fallos"] += 1
                continue
            suma(r)
            if not r.get("completo", True):
                pendiente = True
        res["completo"] = not pendiente
        res["s"] = round(time.monotonic() - t0, 2)
        if res["completo"] and not res["fallos"] and not almacen.meta(con, "construccion_completa"):
            almacen.guardar_meta(con, self.quien, "construccion_completa", int(ahora if ahora is not None else time.time()))
        if res["nuevos"] or res["subidos"] or res["anomalias"]:
            almacen.guardar_meta(con, self.quien, "ultima_vuelta", json.dumps(
                {k: res[k] for k in ("nuevos", "subidos", "anomalias", "ficheros", "s")} | {"en": int(time.time())}))
        self._mantenimiento(con)
        return res

    def _mantenimiento(self, con):
        ahora = time.time()
        if ahora - self.ultimo_mantenimiento >= CADA_MANTENIMIENTO_S:
            self.ultimo_mantenimiento = ahora
            almacen.mantenimiento(con)
        if ahora - self.ultima_comprobacion >= CADA_COMPROBACION_S:
            self.ultima_comprobacion = ahora
            estado = almacen.comprobar(con)
            almacen.guardar_meta(con, self.quien, "ultimo_quick_check", f"{int(ahora)}:{estado}")
            if estado != "ok":
                log(f"ingesta: quick_check de la base devuelve un problema ({estado[:60]})")


class HiloIngesta(threading.Thread):
    """Ingesta continua de la barra: encadena vueltas mientras quede trabajo y descansa cuando todo está al día."""

    def __init__(self, ingestor=None):
        super().__init__(name="infobar-ingesta", daemon=True)
        self.ingestor = ingestor or Ingestor()
        self.parar = threading.Event()

    def run(self):
        try:
            while not self.parar.is_set():
                espera = PAUSA_AL_DIA_S
                try:
                    r = self.ingestor.vuelta()
                    espera = PAUSA_PENDIENTE_S if (r["escritor"] and not r["completo"]) else 60 if r["fallos"] else PAUSA_AL_DIA_S
                except almacen.ErrorAlmacen as e:
                    log(f"ingesta: {e}")
                    if not isinstance(e, almacen.SinEscritura):
                        return                                  # sin base utilizable no se insiste cada medio minuto
                except Exception:                               # noqa: BLE001
                    log("ingesta: " + traceback.format_exc()[-500:])
                    espera = 60
                self.parar.wait(espera)
        finally:
            self.ingestor.cierra()
