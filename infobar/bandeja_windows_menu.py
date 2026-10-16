"""Formateo puro del menu de la bandeja de Windows (fase 2), a partir del dict que devuelve
costbar.escanear().

Separado de bandeja_windows.py (que importa pystray y tiene la guarda de plataforma) para que
los tests ejerciten este formato en cualquier sistema operativo, sin crear un icono real de
pystray ni depender de que el modulo raiz pueda importarse.

No se toca costbar.py (regla del plan: lo de macOS queda exactamente igual), asi que el calculo
de fraccion/alerta/textos de CostBar.refrescar (rumps, macOS) se replica aqui para el subconjunto
que pide la fase 2 (5 h, hoy, ritmo). fila()/fmt_tok() SI se reusan tal cual: ya son
cross-platform (infobar/formato_e_icono.py).

El icono en si NO reusa formato_e_icono.icono_png(): esa funcion dibuja una "pildora" de 62x22
translucida pensada para la barra de menus de macOS (acepta anchos arbitrarios); un icono de
bandeja de Windows es SIEMPRE cuadrado, y forzar esa pildora en un hueco cuadrado la deja
distorsionada e ilegible. imagen_icono_windows() dibuja en su lugar un anillo de progreso propio,
cuadrado y opaco (no depende del tema claro/oscuro del sistema para leerse), con una paleta
neutra verificada contra WCAG 2.2 (1.4.11, componentes graficos >=3:1): ver CONTRASTE_WCAG.
"""
from .formato_e_icono import fila, fmt_tok

FONDO = (33, 33, 36, 255)              # gris neutro muy oscuro, opaco
TRACK = (142, 142, 147, 255)           # gris medio neutro: pista del anillo (4.93:1 sobre FONDO)
RELLENO_OK = (230, 230, 235, 255)      # casi blanco neutro: progreso normal (12.91:1 sobre FONDO)
RELLENO_ALERTA = (217, 119, 87, 255)   # ambar neutro-calido: ritmo por encima del limite (5.14:1)
CONTRASTE_WCAG = {"track": 4.93, "relleno_ok": 12.91, "relleno_alerta": 5.14}  # documentado, no recalculado en caliente


def imagen_icono_windows(fraccion, alerta=False, tamano=64):
    """PIL.Image RGBA cuadrada: anillo de progreso (fraccion 0..1) sobre fondo opaco neutro."""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (tamano, tamano), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, tamano - 1, tamano - 1], fill=FONDO)
    grosor = max(3, tamano // 8)
    caja = [grosor, grosor, tamano - 1 - grosor, tamano - 1 - grosor]
    d.arc(caja, start=-90, end=270, fill=TRACK, width=grosor)
    fraccion = max(0.0, min(fraccion, 1.0))
    if fraccion > 0:
        d.arc(caja, start=-90, end=-90 + 360 * fraccion,
              fill=(RELLENO_ALERTA if alerta else RELLENO_OK), width=grosor)
    return im


def texto_5h(r):
    """'Ultimas 5 h        1,2 M  ██░░░░░░░░   24 % del limite', o '... tokens' sin limite declarado."""
    c5 = r["cinco_h"]
    lim5 = r["limites"].get("limite_5h_tokens") or 0
    sufijo = f"{r['pct_5h']:.0f} % del limite" if lim5 else "tokens"
    return fila("Ultimas 5 h", fmt_tok(c5["tok"]), r["pct_5h"] if lim5 else None) + f"   {sufijo}"


def texto_hoy(r):
    """'Hoy                  300,0 K   12 turnos'."""
    h = r["hoy"]
    return fila("Hoy", fmt_tok(h["tok"])) + f"   {h['turnos']} turnos"


def texto_ritmo(r):
    """'Ritmo ultima hora: 50,0 K tokens', con '  ALTO' si supera el limite por hora."""
    alto = r["ritmo"]["tok"] >= (r["limites"].get("limite_hora_tokens") or 0)
    return f"Ritmo ultima hora: {fmt_tok(r['ritmo']['tok'])} tokens" + ("  ALTO" if alto else "")


def fraccion_e_icono(r):
    """(fraccion 0..1 para la barra de progreso del icono, alerta bool): mismo calculo que
    CostBar.refrescar en costbar.py (macOS), sin tocar ese fichero."""
    c5 = r["cinco_h"]
    lim5 = r["limites"].get("limite_5h_tokens") or 0
    lim_hora = r["limites"].get("limite_hora_tokens") or 1
    alerta = r["ritmo"]["tok"] >= (r["limites"].get("limite_hora_tokens") or 0)
    fraccion = (c5["tok"] / lim5) if lim5 else (r["ritmo"]["tok"] / lim_hora or 0.34)
    return fraccion, alerta


def titulo_icono(r):
    """La cifra de referencia de la ventana de 5 h (mismo dato que destacaba el PNG de macOS)."""
    return fmt_tok(r["cinco_h"]["tok"])


def texto_tooltip(r):
    """Texto corto para el tooltip nativo de Windows (pystray Icon.title) al pasar el raton por
    encima: con el icono reducido a un anillo sin digitos (ver imagen_icono_windows), aqui es
    donde se leen las cifras exactas sin abrir el menu. Se mantiene corto a proposito."""
    return f"CostGuard · 5h {titulo_icono(r)} · hoy {fmt_tok(r['hoy']['tok'])}"
