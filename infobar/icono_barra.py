"""Icono de la barra de menús (macOS): pastilla con contorno, relleno según el consumo, la cifra de la ventana de
5 h y, si hay servidores, un separador y el punto de su estado.

No es una imagen plantilla: el punto lleva color, así que la pastilla se pinta en claro u oscuro según la
apariencia real de la barra (la decide barra.py). Aquí solo hay Pillow: se puede probar sin AppKit.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

ALTO = 20                       # puntos (pastilla de 18 como en la maqueta); la imagen va al doble para Retina
ESCALA = 2
PUNTO = {"verde": (52, 199, 89), "ambar": (255, 159, 10), "rojo": (255, 69, 58), "gris": (142, 142, 147)}
_FUENTES = ("/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Helvetica.ttc")
_fuentes = {}


def _fuente(px):
    """Monoespaciada del sistema en seminegrita (cifras de ancho fijo: el icono no baila al cambiar)."""
    if px not in _fuentes:
        fuente = None
        for ruta in _FUENTES:
            if os.path.exists(ruta):
                try:
                    fuente = ImageFont.truetype(ruta, px)
                except Exception:
                    continue
                try:
                    fuente.set_variation_by_name("Semibold")
                except Exception:
                    pass                    # Menlo y Helvetica no son variables: se quedan en su peso
                break
        _fuentes[px] = fuente or ImageFont.load_default()
    return _fuentes[px]


def relleno(pct_5h, ritmo, limite_hora):
    """(fracción 0..1, alerta) de la pastilla: la cuota de 5 h si está declarada; si no, el ritmo de la última
    hora frente a su límite. Sin cuota ni límite no hay nada contra qué medir y la pastilla queda vacía."""
    if pct_5h:
        return min(max(pct_5h, 0) / 100.0, 1.0), pct_5h >= 90
    if limite_hora:
        return min(max(ritmo or 0, 0) / limite_hora, 1.0), (ritmo or 0) >= limite_hora
    return 0.0, False


def dibuja(texto, fraccion, alerta=False, punto=None, oscuro=True):
    """PNG (bytes) a doble resolución y su ancho en puntos. `punto`: verde/ambar/rojo/gris, o None sin servidores.
    `alerta` (pasado el límite) solo oscurece un poco el relleno: el color se reserva para el punto de los servidores."""
    e = ESCALA
    fg = (242, 242, 242) if oscuro else (29, 29, 31)
    fuente = _fuente(int(11.5 * e))
    caja = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), texto, font=fuente)
    margen, hueco, diam = 7 * e, 6 * e, 6 * e
    x_texto = margen
    fin_texto = x_texto + (caja[2] - caja[0])
    con_punto = punto in PUNTO
    x_sep = fin_texto + hueco
    x_punto = x_sep + e + hueco
    ancho = (x_punto + diam + margen - e) if con_punto else (fin_texto + margen)
    ancho += ancho % 2                                  # entero de puntos al reducir a la mitad
    alto = ALTO * e
    im = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    arriba, abajo = e, alto - e - 1
    # el relleno va separado del borde por un hueco: se lee como una barra dentro de la pastilla, no como su fondo
    hueco_borde = 2 * e
    lleno = int((ancho - 2 * hueco_borde) * max(0.0, min(fraccion, 1.0)))
    if lleno > 0:
        d.rounded_rectangle([hueco_borde, arriba + hueco_borde, hueco_borde + lleno, abajo - hueco_borde], radius=3 * e,
                            fill=fg + (70 if alerta else 52,))
    d.rounded_rectangle([0, arriba, ancho - 1, abajo], radius=5 * e, outline=fg + (125,), width=e)
    d.text((x_texto, (alto - (caja[3] - caja[1])) / 2 - caja[1]), texto, font=fuente, fill=fg + (255,))
    if con_punto:
        d.line([(x_sep, alto / 2 - 3 * e), (x_sep, alto / 2 + 3 * e)], fill=fg + (95,), width=e)
        y = (alto - diam) / 2
        d.ellipse([x_punto, y, x_punto + diam, y + diam], fill=PUNTO[punto] + (255,))
    salida = io.BytesIO()
    im.save(salida, format="PNG")
    return salida.getvalue(), ancho // e
