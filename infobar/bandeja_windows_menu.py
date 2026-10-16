"""Formateo puro del menú de la bandeja de Windows, a partir del resumen «agentes» que entrega el hilo
trabajador (infobar/trabajador_barra.py), el mismo que pinta el icono de macOS.

Separado de bandeja_windows.py (que importa pystray y tiene la guarda de plataforma) para que
los tests ejerciten este formato en cualquier sistema operativo, sin crear un icono real de
pystray ni depender de que el modulo raiz pueda importarse.

fila() y fmt_tok() se reutilizan de infobar/formato_e_icono.py, que ya es multiplataforma.

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


def texto_5h(a):
    """'Últimas 5 h        1,2 M  ██░░░░░░░░   24 % del límite', o '... tokens' sin límite declarado."""
    lim5 = a.get("limite_5h") or 0
    sufijo = f"{a.get('pct_5h') or 0:.0f} % del límite" if lim5 else "tokens"
    return fila("Últimas 5 h", fmt_tok(a["cinco_h"]), (a.get("pct_5h") or 0) if lim5 else None) + f"   {sufijo}"


def texto_hoy(a):
    """'Hoy                  300,0 K   12 turnos'."""
    return fila("Hoy", fmt_tok(a["hoy"])) + f"   {a['turnos']} turnos"


def _ritmo_alto(a):
    lim = a.get("limite_hora") or 0
    return bool(lim) and (a.get("ritmo") or 0) >= lim


def texto_ritmo(a):
    """'Ritmo última hora: 50,0 K tokens', con '  ALTO' si supera el límite por hora declarado."""
    return f"Ritmo última hora: {fmt_tok(a.get('ritmo') or 0)} tokens" + ("  ALTO" if _ritmo_alto(a) else "")


def fraccion_e_icono(a):
    """(fracción 0..1 del anillo, alerta): la parte consumida del límite de 5 h; sin límite declarado, el ritmo
    contra el límite por hora, y sin ninguno de los dos (o sin ritmo), un tercio fijo (el anillo no dice nada que no se sepa)."""
    lim5, lim_hora = a.get("limite_5h") or 0, a.get("limite_hora") or 0
    if lim5:
        fraccion = a["cinco_h"] / lim5
    elif lim_hora:
        fraccion = (a.get("ritmo") or 0) / lim_hora or 0.34      # sin ritmo, el mismo tercio: un anillo vacío parece apagado
    else:
        fraccion = 0.34
    return fraccion, _ritmo_alto(a)


def texto_tooltip(a):
    """Tooltip del icono (pystray Icon.title): el anillo no lleva cifras, así que se leen aquí sin abrir el menú.
    Windows corta el tooltip a 127 caracteres: se mantiene corto."""
    return f"CostGuard · 5 h {fmt_tok(a['cinco_h'])} · hoy {fmt_tok(a['hoy'])}"
