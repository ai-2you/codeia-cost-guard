"""HTML del desplegable -> PNG con Chrome headless (fondo transparente).

La pagina no puede ejecutar scripts: su CSP es default-src 'none' (desplegable_comun.pagina).
(Desactivar JS con --blink-settings rompe la captura del modo headless.)

Se ejecuta en el hilo trabajador de barra.py, nunca en el principal. Cada tanda de imagenes lleva
un numero de generacion en el nombre: la vista cambia de tanda de una vez y nunca lee un PNG a
medio escribir; las tandas viejas se borran despues.
"""
import glob, os, shutil, subprocess, tempfile, time

from . import desplegable_agentes, desplegable_servidores, permisos, plataforma, rutas
from . import geometria_desplegable as G
from .config_y_estado import log

NAVEGADORES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
ALTO_MAX = 1500             # pt: la captura se hace alta y se recorta a la tarjeta


def carpeta():
    return os.path.join(os.path.dirname(rutas.LOG), "render")


def navegador():
    return next((r for r in NAVEGADORES if os.path.exists(r)), None)


def html_a_png(html_txt, destino, chrome):
    """Pinta el HTML y lo recorta a la tarjeta. Devuelve (ancho_pt, alto_pt) o None."""
    from PIL import Image
    trabajo = tempfile.mkdtemp(prefix="infobar-render-")
    try:
        pagina = os.path.join(trabajo, "p.html")
        with open(pagina, "w", encoding="utf-8") as f:
            f.write(html_txt)
        bruto = os.path.join(trabajo, "bruto.png")
        p = subprocess.Popen([chrome, "--headless=new", "--hide-scrollbars", "--no-first-run",
                        "--disable-extensions", "--disable-gpu", f"--user-data-dir={trabajo}/perfil",
                        "--default-background-color=00000000",
                        "--force-device-scale-factor=2", f"--window-size={G.ANCHO},{ALTO_MAX}",
                        f"--screenshot={bruto}", f"file://{pagina}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **plataforma.grupo_nuevo_kwargs())
        try:
            p.wait(timeout=25)
        except subprocess.TimeoutExpired:
            plataforma.matar_arbol(p)              # Chrome y todos sus procesos hijos
            p.wait()
            raise TimeoutError("Chrome no termino en 25 s")
        im = Image.open(bruto).convert("RGBA")
        caja = im.getchannel("A").getbbox()      # la .hoja lleva un alfa minimo: es la caja util
        if not caja:
            return None
        im = im.crop((0, caja[1], im.size[0], caja[3]))
        tmp = destino + ".tmp.png"
        im.save(tmp)
        os.replace(tmp, destino)
        return im.size[0] // 2, im.size[1] // 2
    except Exception as e:
        log(f"render: {type(e).__name__}: {e}")
        return None
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


def renderizar(r, cache, servidores_cfg, solo_servidor=None, previo=None):
    """Pinta la pestana Agentes (si hay `r`) y la de cada servidor, en claro y oscuro.

    Devuelve el conjunto {"gen", "agentes": {tema: (ruta, ancho, alto)}, "servidores": {nombre: {...}}}.
    Con `solo_servidor` repinta solo ese (modo incidencia) y reutiliza el resto de `previo`."""
    chrome = navegador()
    if chrome is None:
        log("render: no hay Chrome, Chromium, Brave ni Edge instalado")
        return previo
    os.makedirs(carpeta(), exist_ok=True)
    gen = int(time.time() * 1000)
    ahora = time.time()
    nuevas = 0
    out = {"gen": gen, "agentes": dict((previo or {}).get("agentes") or {}),
           "servidores": dict((previo or {}).get("servidores") or {})}
    for oscuro, tema in ((False, "claro"), (True, "oscuro")):
        if r is not None:
            ruta = os.path.join(carpeta(), f"{gen}-agentes-{tema}.png")
            tam = html_a_png(desplegable_agentes.documento(r, oscuro, ahora), ruta, chrome)
            if tam:
                out["agentes"][tema] = (ruta, *tam, len(desplegable_agentes.BOTONES), False)
                nuevas += 1
        nombres = [s["nombre"] for s in servidores_cfg] or [""]
        for i, nombre in enumerate(nombres):
            if solo_servidor and nombre != solo_servidor:
                continue
            ruta = os.path.join(carpeta(), f"{gen}-servidor{i}-{tema}.png")
            tam = html_a_png(desplegable_servidores.documento(nombre, cache, servidores_cfg, oscuro, ahora), ruta, chrome)
            if tam:
                # con la imagen va la forma de sus zonas de clic (botones y selector de ESA imagen)
                botones = len(desplegable_servidores.BOTONES) if servidores_cfg else 0
                out["servidores"].setdefault(nombre, {})[tema] = (ruta, *tam, botones, bool(servidores_cfg))
                nuevas += 1
    out["servidores"] = {n: v for n, v in out["servidores"].items()
                         if n in {s["nombre"] for s in servidores_cfg} or (not servidores_cfg and n == "")}
    permisos.purgar_render()
    return out if nuevas else previo        # si Chrome no pinto nada, el trabajador lo reintentara


VIDA_PNG = 600               # s: un PNG que ya no usa nadie se borra pasado este tiempo


def limpiar(conjunto):
    """Borra los PNG viejos que no usa el conjunto vigente. Solo los que tienen mas de VIDA_PNG:
    el trabajador puede ir por delante del hilo principal (p. ej. con un menu abierto) y las
    imagenes nuevas aun no entregadas no deben desaparecer."""
    vivos = {v[0] for v in (conjunto or {}).get("agentes", {}).values()}
    vivos |= {v[0] for t in (conjunto or {}).get("servidores", {}).values() for v in t.values()}
    limite = time.time() - VIDA_PNG
    for f in glob.glob(os.path.join(carpeta(), "*.png")):
        if f not in vivos and os.path.getmtime(f) < limite:
            try:
                os.remove(f)
            except OSError:
                pass
