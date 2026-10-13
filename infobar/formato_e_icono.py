"""Formato de cifras, colores por magnitud, icono PNG y el informe de texto."""
import re

from .config_y_estado import log
from .planes_y_tarifas import corto


def fmt_tok(n):
    """18300000 -> 18,3 M · 820000 -> 820 K"""
    n = float(n or 0)
    for corte, sufijo in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= corte:
            return f"{n / corte:.1f} {sufijo}".replace(".", ",")
    return str(int(n))


def color_magnitud(v):
    """Tono segun el tamano: miles de millones mas fuerte que millones."""
    v = abs(v or 0)
    if v >= 1e9:
        return (217, 119, 87)      # miles de millones: naranja Opus
    if v >= 1e6:
        return (48, 209, 88)       # millones: verde terminal
    if v >= 1e3:
        return (142, 142, 147)     # miles: gris
    return (120, 120, 140)


def icono_png(destino, fraccion, texto="", color=None, alerta=False):
    """Barra de la barra de menus: barra de progreso de la ventana de 5 h + el numero coloreado."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    W, H = 62, 22
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 1, W - 1, H - 2], radius=6, fill=(150, 140, 175, 45),
                        outline=((255, 0, 90, 255) if alerta else None), width=2)
    relleno = int((W - 4) * max(0.0, min(fraccion, 1.0)))
    for x in range(max(relleno, 0)):
        f = x / max(relleno - 1, 1)
        c = tuple(int(a + (b - a) * f) for a, b in ((0xAE, 0xFF), (0x00, 0x00), (0xFF, 0x8C)))
        d.line([(2 + x, 4), (2 + x, H - 5)], fill=c + (255,))
    if texto:
        try:
            fuente = ImageFont.load_default(size=13)
        except Exception:
            fuente = ImageFont.load_default()
        col = color or (60, 60, 70)
        caja = d.textbbox((0, 0), texto, font=fuente)
        d.text((W - (caja[2] - caja[0]) - 5, (H - (caja[3] - caja[1])) / 2 - 2), texto, font=fuente, fill=col + (255,))
    im.save(destino)
    return destino


def _color_ns(rgb):
    from AppKit import NSColor
    return NSColor.colorWithSRGBRed_green_blue_alpha_(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, 1.0)


def _pinta_magnitudes(item):
    """Colorea una entrada del menu: etiqueta en rosa de marca y los numeros segun su tamano.

    Los menues de macOS son texto plano; hay que ir al NSMenuItem de debajo. Si algo falla,
    se queda como estaba: nunca debe romper la barra.
    """
    try:
        from AppKit import NSMutableAttributedString, NSForegroundColorAttributeName, NSFont, NSFontAttributeName
        from Foundation import NSMakeRange
        ns = getattr(item, "_menuitem", None)
        if ns is None:
            return False
        txt = str(ns.title())
        if not txt:
            return False
        a = NSMutableAttributedString.alloc().initWithString_(txt)
        try:
            a.addAttribute_value_range_(NSFontAttributeName, NSFont.monospacedDigitSystemFontOfSize_weight_(12.5, 0.0), NSMakeRange(0, len(txt)))
        except Exception:
            pass
        for mm in re.finditer("\u2588+", txt):
            a.addAttribute_value_range_(NSForegroundColorAttributeName, _color_ns(color_magnitud(10 ** 9)), NSMakeRange(mm.start(), len(mm.group(0))))
        i = txt.find(":")
        if 0 < i < 40 and txt[:i].lower() not in ("5 h", "semana"):
            a.addAttribute_value_range_(NSForegroundColorAttributeName, _color_ns((255, 0, 140)), NSMakeRange(0, i))
        for m in re.finditer(r"(\d[\d.,]*)\s*([KMB])\b", txt):
            n = 0.0
            try:
                n = float(m.group(1).replace(".", "").replace(",", "."))
            except Exception:
                continue
            n *= {"K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]
            a.addAttribute_value_range_(NSForegroundColorAttributeName, _color_ns(color_magnitud(n)), NSMakeRange(m.start(), len(m.group(0))))
        ns.setAttributedTitle_(a)
        return True
    except Exception as e:
        log(f"menu sin color: {type(e).__name__}: {e}")
        return False


def barra(pct, n=10):
    """Barra de bloque para el menu: se ve de un golpe sin leer el numero."""
    try:
        lleno = int(round(n * max(0.0, min(float(pct or 0), 100.0)) / 100.0))
    except Exception:
        lleno = 0
    return "\u2588" * lleno + "\u2591" * (n - lleno)


def fila(etiqueta, valor, pct=None, ancho=19):
    """Fila alineada a columnas: la fuente monoespaciada del menu hace que cuadre."""
    base = f"{etiqueta:<{ancho}}{valor:>10}"
    return f"{base}  {barra(pct)}" if pct is not None else base


def informa(r):
    h, c5 = r["hoy"], r["cinco_h"]
    fam = " · ".join(f"{k}: {fmt_tok(v)}" for k, v in sorted(c5["fam"].items(), key=lambda x: -x[1]) if v)
    L = [f"fuentes: " + " · ".join(r.get("bases") or []) ,
         f"VENTANA 5 H  {fmt_tok(c5['tok'])} tokens" + (f"   {fam}" if fam else ""),
         f"SEMANA       {fmt_tok(r['semana']['tok'])} tokens ({int(r['semana']['turnos'])} turnos)",
         f"MES (30 d)   {fmt_tok(r['mes']['tok'])} tokens ({int(r['mes']['turnos'])} turnos)",
         f"HOY          {fmt_tok(h['tok'])} tokens ({int(h['turnos'])} turnos)   ritmo ultima hora {fmt_tok(r['ritmo']['tok'])} tokens",
         f"             entrada {fmt_tok(h['gi'])} · salida {fmt_tok(h['go'])} · cache leida {fmt_tok(h['cr'])} ({r['cache_pct_hoy']:.0f} %) · cache escrita {fmt_tok(h['cw'])}",
         "por dia: " + " · ".join(f"{d[5:]}: {fmt_tok(v['tok'])}" for d, v in sorted(r["dias"].items())),
         "modelos hoy: " + " · ".join(f"{corto(k)}: {fmt_tok(v['tok'])}" for k, v in sorted(r["modelos_hoy"].items(), key=lambda x: -x[1]["tok"])[:6]),
         "planes: " + " · ".join(f"{n}: 5h {fmt_tok(x['cinco_h'])}" + (f" ({x['pct_5h']:.0f} %)" if x["pct_5h"] else "") + f" · sem {fmt_tok(x['semana'])}" for n, x in r["proveedores"].items() if x["cinco_h"] or x["semana"]),
         "proyectos (mes): " + " · ".join(f"{k}: {fmt_tok(v['tok'])}" for k, v in sorted(r["proyectos"].items(), key=lambda x: -x[1]["tok"])[:5]),
         f"equivalente API (informativo): {h['usd']:.2f} USD hoy · {r['semana']['usd']:.0f} USD en 7 dias"]
    cuotas = [f"{mc['modelo']} {fmt_tok(mc['mes'])}" + (f"/{fmt_tok(mc['limite'])} ({mc['pct']:.0f} %)" if mc.get("limite") else (" (sin tope" + (", solo logs" if mc.get("fuente") == "logs" else "") + ")"))
              for _n, x in r["proveedores"].items() for mc in (x.get("modelos_cuota") or [])]
    if cuotas:
        _en_vivo = any(x.get("cuota_fuente") == "api" for x in r["proveedores"].values() if x.get("modelos_cuota"))
        L.append("cuotas mes" + (" (en vivo, API NaN)" if _en_vivo else "") + ": " + " · ".join(cuotas))
    return "\n".join(L)
