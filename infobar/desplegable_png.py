"""HTML del desplegable -> PNG con Chrome headless (fondo transparente).

La pagina no puede ejecutar scripts: su CSP es default-src 'none' (desplegable_comun.pagina).
(Desactivar JS con --blink-settings rompe la captura del modo headless.)

Se ejecuta en el hilo trabajador de barra.py, nunca en el principal. Cada tanda de imagenes lleva
un numero de generacion en el nombre: la vista cambia de tanda de una vez y nunca lee un PNG a
medio escribir; las tandas viejas se borran despues.
"""
import glob, os, re, shutil, subprocess, tempfile, time

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


def marcas_de_secciones(im):
    """Filas de la captura (en píxeles) donde la página dibujó el marcador magenta de cada sección plegable (una columna de 1 pt en el borde izquierdo).
    Las repinta como el fondo de la hoja para que no se vean. Devuelve [(fila_ini, fila_fin)] de arriba abajo."""
    px = im.load()
    alto = im.size[1]
    tramos, ini = [], None
    for y in range(alto):
        r, g, b, a = px[0, y]
        es = a > 250 and r > 250 and g < 5 and b > 250
        if es and ini is None:
            ini = y
        elif not es and ini is not None:
            tramos.append((ini, y))
            ini = None
    if ini is not None:
        tramos.append((ini, alto))
    for y0, y1 in tramos:
        for y in range(y0, y1):
            for x in (0, 1):
                px[x, y] = (127, 127, 127, 3)
    return tramos


def html_a_png(html_txt, destino, chrome):
    """Pinta el HTML y lo recorta a la tarjeta. Devuelve (ancho_pt, alto_pt, secciones) o None; `secciones` = [(id, y_arriba_pt, y_abajo_pt)] de los títulos plegables."""
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
        tramos = marcas_de_secciones(im)
        caja = im.getchannel("A").getbbox()      # la .hoja lleva un alfa minimo: es la caja util
        if not caja:
            return None
        ids = re.findall(r'data-plg="([a-z_]+)"', html_txt)
        if len(ids) != len(tramos):
            log(f"render: {len(tramos)} marcadores para {len(ids)} secciones plegables; sin zonas de clic")
            secciones = []
        else:
            secciones = [(i, (a - caja[1]) / 2, (b - caja[1]) / 2) for i, (a, b) in zip(ids, tramos)]
        im = im.crop((0, caja[1], im.size[0], caja[3]))
        tmp = destino + ".tmp.png"
        im.save(tmp)
        os.replace(tmp, destino)
        return im.size[0] // 2, im.size[1] // 2, secciones
    except Exception as e:
        log(f"render: {type(e).__name__}: {e}")
        return None
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


TEMAS = ("claro", "oscuro")


def renderizar(r, cache, servidores_cfg, solo_servidor=None, previo=None, temas=TEMAS, parar=None):
    """Pinta la pestana Agentes (si hay `r`) y la de cada servidor en los `temas` pedidos (cada PNG es un Chrome: ~1 s).

    Devuelve el conjunto {"gen", "agentes": {tema: (ruta, ancho, alto)}, "servidores": {nombre: {...}}}, solo con los `temas`
    pedidos en lo que se repinta (lo que no se repinta conserva su imagen de reserva, aunque sea del otro tema). Con `solo_servidor`
    repinta solo ese (modo incidencia) y reutiliza el resto de `previo`. `parar()` se mira antes
    de cada PNG: si devuelve True la tanda se corta (un clic espera como mucho al PNG en curso) y se entrega lo ya pintado."""
    chrome = navegador()
    if chrome is None:
        log("render: no hay Chrome, Chromium, Brave ni Edge instalado")
        return previo
    os.makedirs(carpeta(), exist_ok=True)
    gen = int(time.time() * 1000)
    ahora = time.time()
    out = {"gen": gen, "agentes": dict((previo or {}).get("agentes") or {}),
           "servidores": dict((previo or {}).get("servidores") or {})}
    def pinta(html, ruta):
        if parar is not None and parar():
            raise _Cortado
        return html_a_png(html, ruta, chrome)
    try:
        _pinta_temas(r, cache, servidores_cfg, solo_servidor, temas, out, gen, ahora, pinta)
    except _Cortado:
        pass
    nuevas, repintados = out.pop("_nuevas"), out.pop("_repintados")
    if "agentes" in repintados:
        out["agentes"] = {t: v for t, v in out["agentes"].items() if t in temas}
    out["servidores"] = {n: ({t: v for t, v in v.items() if t in temas} if n in repintados else v)
                         for n, v in out["servidores"].items()
                         if n in {s["nombre"] for s in servidores_cfg} or (not servidores_cfg and n == "")}
    permisos.purgar_render()
    return out if nuevas else previo        # si Chrome no pinto nada, el trabajador lo reintentara


class _Cortado(Exception):
    """La tanda se corta porque hay un clic esperando."""


def _pinta_temas(r, cache, servidores_cfg, solo_servidor, temas, out, gen, ahora, pinta):
    out["_nuevas"], out["_repintados"] = 0, set()
    for tema in temas:
        oscuro = tema == "oscuro"
        if r is not None:
            ruta = os.path.join(carpeta(), f"{gen}-agentes-{tema}.png")
            tam = pinta(desplegable_agentes.documento(r, oscuro, ahora), ruta)
            if tam:
                out["agentes"][tema] = (ruta, tam[0], tam[1], len(desplegable_agentes.BOTONES), False, [])
                out["_nuevas"] += 1
                out["_repintados"].add("agentes")
        nombres = [s["nombre"] for s in servidores_cfg] or [""]
        for i, nombre in enumerate(nombres):
            if solo_servidor and nombre != solo_servidor:
                continue
            ruta = os.path.join(carpeta(), f"{gen}-servidor{i}-{tema}.png")
            tam = pinta(desplegable_servidores.documento(nombre, cache, servidores_cfg, oscuro, ahora), ruta)
            if tam:
                # con la imagen va la forma de sus zonas de clic (botones y selector de ESA imagen)
                botones = len(desplegable_servidores.BOTONES) if servidores_cfg else 0
                out["servidores"][nombre] = dict(out["servidores"].get(nombre) or {}, **{tema: (ruta, tam[0], tam[1], botones, bool(servidores_cfg), tam[2])})
                out["_nuevas"] += 1
                out["_repintados"].add(nombre)


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
