"""Servidor local del panel y su configurador (http://127.0.0.1:PUERTO).

Solo escucha en 127.0.0.1, pero eso no basta: cualquier web abierta en el navegador puede pedirle
cosas a localhost. Por eso:
  · toda peticion con una cabecera Host que no sea 127.0.0.1/localhost:PUERTO se rechaza (corta el
    DNS rebinding: evil.com resuelto a 127.0.0.1 llega con Host: evil.com);
  · el POST solo va a /guardar, con Content-Type application/json y Origin exacto del panel (un
    POST "simple" desde otra web no puede poner ese tipo sin preflight, ni falsear Origin);
  · lo que se guarda se FUSIONA con config.json (nunca lo reemplaza) y se valida;
  · las respuestas nunca llevan claves (lista cerrada de nombres).
"""
import json, os, subprocess, sys, threading, time

from . import rutas
from .agregacion import escanear
from .config_y_estado import cfg, guardar_config, log, olvidar_cache
from .servidores import errores_ips, errores_servidores


PUERTO = 47391              # panel/configurador local: un puerto alto que casi nada usa
# campos que nunca salen por HTTP ni se aceptan desde el panel: nombres exactos y sufijos con '_'.
# Los limite_*_tokens son cuotas (acaban en _tokens, no en _token): por eso no vale un patron *token*
SECRETOS = {"api_key", "apikey", "token", "password", "secret", "clave", "authorization", "private_key"}
SUFIJOS_SECRETOS = ("_key", "_token", "_secret", "_password")
MAX_CUERPO = 1_000_000      # bytes: la config cabe de sobra


def es_secreto(nombre):
    n = str(nombre).lower()
    return n in SECRETOS or n.endswith(SUFIJOS_SECRETOS)


def sin_secretos(o):
    """Copia de `o` sin los campos de SECRETOS (a cualquier profundidad)."""
    if isinstance(o, dict):
        return {k: sin_secretos(v) for k, v in o.items() if not es_secreto(k)}
    if isinstance(o, list):
        return [sin_secretos(v) for v in o]
    return o


def errores_config(nuevo):
    """Errores de una config recibida del panel; vacia si se puede guardar."""
    if not isinstance(nuevo, dict):
        return ["no es un objeto"]
    errores = []
    provs, alias = nuevo.get("proveedores"), nuevo.get("aliases")
    if not isinstance(provs, list) or not isinstance(alias, dict):
        return ["faltan campos (proveedores / aliases)"]
    for i, p in enumerate(provs):
        if not (isinstance(p, dict) and isinstance(p.get("nombre"), str) and p["nombre"].strip()):
            errores.append(f"proveedores[{i}]: sin nombre")
            continue
        for k, v in p.items():
            if k.startswith("limite_") and not (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0):
                errores.append(f"proveedores[{i}].{k}: debe ser un numero >= 0")
        if not isinstance(p.get("modelos", []), list):
            errores.append(f"proveedores[{i}].modelos: debe ser una lista")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in alias.items()):
        errores.append("aliases: claves y valores deben ser texto")
    for k, v in nuevo.items():
        if k.startswith("limite_") and not (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0):
            errores.append(f"{k}: debe ser un numero >= 0")
    errores += errores_servidores(nuevo.get("servidores"))
    errores += errores_ips(nuevo.get("ips_conocidas"))
    return errores


def crear_servidor(puerto=PUERTO):
    """El servidor HTTP (sin arrancar). servir() lo lanza en un hilo; los tests usan un puerto libre."""
    import http.server, socketserver

    base = rutas.RAIZ
    generando = threading.Lock()        # un solo panel.py a la vez aunque lleguen muchas peticiones

    class H(http.server.BaseHTTPRequestHandler):
        timeout = 15            # un cliente que anuncia un cuerpo y no lo manda no retiene el hilo

        def log_message(self, *a):
            pass

        def _hosts(self):
            p = self.server.server_address[1]
            return {f"127.0.0.1:{p}", f"localhost:{p}"}

        def _manda(self, codigo, cuerpo, tipo):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(cuerpo)

        def _json(self, codigo, datos):
            self._manda(codigo, json.dumps(datos, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _host_ok(self):
            # Host exacto (DNS rebinding) y nada que venga lanzado desde otra web (un <img> a
            # localhost no lee la respuesta, pero si le haria escanear o generar el panel)
            if (self.headers.get("Host") or "") in self._hosts() and \
                    (self.headers.get("Sec-Fetch-Site") or "same-origin") in ("same-origin", "none"):
                return True
            self._manda(403, b"origen no permitido", "text/plain")
            return False

        def do_GET(self):
            if not self._host_ok():
                return
            ruta = self.path.split("?")[0]
            if ruta in ("/", "/panel.html"):
                try:
                    # se regenera al abrir: asi el panel siempre lleva los datos de ahora
                    with generando:
                        subprocess.run([sys.executable, f"{base}/panel.py"], capture_output=True, timeout=120)
                    self._manda(200, open(f"{base}/panel.html", "rb").read(), "text/html; charset=utf-8")
                except Exception as e:
                    self._manda(500, str(e).encode(), "text/plain")
            elif ruta == "/origenes":
                try:
                    r = escanear()
                    self._json(200, {"origenes": r.get("origenes", {}),
                                     "planes": [p["nombre"] for p in (cfg().get("proveedores") or [])],
                                     "config": sin_secretos(cfg())})
                except Exception as e:
                    self._json(500, {"error": str(e)})
            elif ruta == "/config.json":
                # el fichero tal cual (sin los valores por defecto que mete cfg()), sin secretos
                try:
                    with open(rutas.CONFIG, encoding="utf-8") as f:
                        self._json(200, sin_secretos(json.load(f)))
                except (OSError, ValueError):
                    self._json(200, {})
            else:
                self._manda(404, b"no", "text/plain")

        def do_POST(self):
            if not self._host_ok():
                return
            if self.path.split("?")[0] != "/guardar":
                return self._manda(404, b"no", "text/plain")
            tipo = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            origen = self.headers.get("Origin") or ""
            if tipo != "application/json" or origen not in {f"http://{h}" for h in self._hosts()}:
                return self._json(403, {"ok": False, "error": "origen o tipo no permitido"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not 0 < n <= MAX_CUERPO:
                return self._json(413, {"ok": False, "error": "cuerpo vacio o demasiado grande"})
            try:
                nuevo = json.loads(self.rfile.read(n).decode("utf-8"))
                errores = errores_config(nuevo)
                if errores:
                    return self._json(400, {"ok": False, "error": "; ".join(errores[:5])})
                guardar_config(sin_secretos(nuevo))
                try:
                    olvidar_cache()                    # el plan de cada fichero cacheado depende de la config
                except OSError:
                    pass
                log("configuracion guardada desde el panel")
                self._json(200, {"ok": True})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)[:200]})

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    return S(("127.0.0.1", puerto), H)


def servir():
    """Sirve el panel en http://127.0.0.1:PUERTO y guarda la configuracion que envies desde el.

    Asi no hay que editar ficheros a mano: el propio panel tiene el configurador.
    """
    def arranca():
        # si el puerto esta ocupado (otra copia que aun se esta cerrando), se reintenta cada 30 s
        # en vez de quedarse sin panel hasta el siguiente reinicio
        avisado = False
        while True:
            try:
                srv = crear_servidor(PUERTO)
            except OSError as e:
                if not avisado:
                    log(f"configurador no disponible ({e}); reintento cada 30 s")
                    avisado = True
                time.sleep(30)
                continue
            log(f"configurador listo en http://127.0.0.1:{PUERTO}")
            srv.serve_forever()
            return

    threading.Thread(target=arranca, name="infobar-panel", daemon=True).start()
