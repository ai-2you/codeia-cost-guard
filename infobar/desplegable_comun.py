"""Piezas comunes del desplegable (estilo glass): escapado, CSS, iconos, cabecera, pestanas y pie.

La pagina se pinta con Chrome (su CSP no deja ejecutar scripts) y con fondo transparente; el desenfoque lo pone macOS
(NSVisualEffectView) detras del PNG. Todo texto que viene de fuera (servidores, logs, config) pasa
por esc() antes de entrar en el HTML.
"""
import html

from . import geometria_desplegable as G

ESTADO_TXT = {"verde": "Todo en orden", "ambar": "Hay algo que mirar", "rojo": "Hay que actuar",
              "gris": "Sin datos recientes"}


def esc(x):
    return html.escape(str(x if x is not None else ""), quote=True)


def css(oscuro):
    """Tokens por tema. Las tarjetas son translucidas: el color de fondo lo pone el material nativo."""
    if oscuro:
        t = dict(fg="rgba(255,255,255,.96)", sub="rgba(255,255,255,.62)", tenue="rgba(255,255,255,.46)",
                 tarjeta="rgba(255,255,255,.09)", borde="rgba(255,255,255,.13)", activa="rgba(255,255,255,.18)",
                 verde_t="#30d158", naranja="#e0876a")
    else:
        t = dict(fg="rgba(0,0,0,.88)", sub="rgba(0,0,0,.58)", tenue="rgba(0,0,0,.42)",
                 tarjeta="rgba(255,255,255,.55)", borde="rgba(0,0,0,.08)", activa="rgba(255,255,255,.9)",
                 verde_t="#1a8f3c", naranja="#c4623f")
    return f"""
:root{{--fg:{t['fg']};--sub:{t['sub']};--tenue:{t['tenue']};--tarjeta:{t['tarjeta']};--borde:{t['borde']};
--activa:{t['activa']};--verde:#34c759;--ambar:#ff9f0a;--rojo:#ff453a;--gris:#8e8e93;
--terminal:{t['verde_t']};--opus:{t['naranja']}}}
*{{box-sizing:border-box}}
html,body{{margin:0;background:transparent}}
body{{font:13px/1.3 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif;
-webkit-font-smoothing:antialiased;color:var(--fg)}}
/* alfa minimo: define la caja que se recorta del PNG sin que se vea */
.hoja{{width:{G.ANCHO}px;background:rgba(127,127,127,.012);padding:0 {G.MARGEN}px}}
.cab{{height:{G.CABECERA_ALTO}px;display:flex;align-items:center;gap:12px}}
.escudo{{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;flex:none}}
.escudo svg{{width:22px;height:22px}}
.tit{{font-size:17px;font-weight:700;letter-spacing:-.01em}}
.est{{font-size:13px;color:var(--sub);margin-top:1px}}
.refrescar{{margin-left:auto;width:{G.REFRESCAR_ANCHO}px;height:30px;border-radius:9px;background:var(--tarjeta);
border:1px solid var(--borde);display:grid;place-items:center}}
.refrescar svg{{width:16px;height:16px}}
.pest{{height:{G.PESTANAS_ALTO}px;padding:4px 0}}
.seg{{display:grid;grid-template-columns:1fr 1fr;height:26px;border-radius:8px;background:var(--tarjeta);
border:1px solid var(--borde);padding:2px}}
.seg span{{display:grid;place-items:center;border-radius:6px;font-size:12px;font-weight:500;color:var(--sub)}}
.seg span.on{{background:var(--activa);color:var(--fg);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.12)}}
.sec{{font-size:11px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--tenue);
margin:12px 4px 6px;display:flex;justify-content:space-between;align-items:baseline}}
.sec .nota{{letter-spacing:0;text-transform:none;font-weight:400}}
.selector{{height:{G.SELECTOR_ALTO}px;margin:8px 0 0;display:flex;align-items:center;gap:6px;font-size:11px;
font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--tenue);padding:0 4px}}
.selector b{{color:var(--fg);letter-spacing:.08em}}
.selector .flecha{{width:14px;height:14px;opacity:.7}}
.selector .ip{{margin-left:auto;font:12px ui-monospace,"SF Mono",Menlo,monospace;letter-spacing:0;text-transform:none;font-weight:500;color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:55%}}
.rejilla{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.tj{{background:var(--tarjeta);border:1px solid var(--borde);border-radius:14px;padding:8px 12px 9px;min-width:0}}
.tj .et{{display:flex;align-items:center;gap:6px;color:var(--sub);font-size:12.5px}}
.tj .et svg{{width:15px;height:15px;flex:none;opacity:.85}}
.tj .et .p{{margin-left:auto}}
.tj .v{{font-size:20px;font-weight:700;margin:3px 0 0;font-variant-numeric:tabular-nums;letter-spacing:-.01em;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.tj .v.largo{{font-size:16px;padding-top:3px}}
.tj .s{{font-size:12px;color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.p{{width:7px;height:7px;border-radius:50%;flex:none;display:inline-block}}
.p.verde{{background:var(--verde)}}.p.ambar{{background:var(--ambar)}}.p.rojo{{background:var(--rojo)}}.p.gris{{background:var(--gris)}}
.pildoras{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}}
.pil{{display:flex;align-items:center;gap:7px;height:28px;padding:0 10px;border-radius:14px;background:var(--tarjeta);
border:1px solid var(--borde);font-size:12.5px;font-weight:600;min-width:0}}
.pil span:last-child{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.lista{{background:var(--tarjeta);border:1px solid var(--borde);border-radius:14px;padding:6px 12px}}
.lista .f{{display:flex;justify-content:space-between;gap:10px;padding:4px 0;font-size:12.5px}}
.lista .f>span:first-child{{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.lista .f .mono{{white-space:nowrap;flex:none}}
.lista .f+.f{{border-top:1px solid var(--borde)}}
.mono{{font:12.5px/1.35 ui-monospace,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums}}
.falta{{min-width:0}} .falta .t{{font-weight:600;color:var(--fg)}}
.falta .a{{color:var(--sub);font:11.5px/1.35 ui-monospace,"SF Mono",Menlo,monospace;margin-top:2px;overflow-wrap:anywhere}}
.aviso{{margin-top:10px;padding:9px 12px;border-radius:12px;background:rgba(255,69,58,.16);border:1px solid rgba(255,69,58,.35);font-size:12.5px}}
.pie{{height:{G.BOTONES_ALTO}px;display:grid;gap:9px;align-items:end;margin-top:10px}}
.btn{{height:38px;border-radius:12px;background:var(--tarjeta);border:1px solid var(--borde);display:flex;
align-items:center;justify-content:center;gap:7px;font-size:13.5px;font-weight:500}}
.btn svg{{width:16px;height:16px}}
.linea{{height:{G.LINEA_ALTO}px;display:flex;align-items:center;justify-content:space-between;color:var(--tenue);font-size:12px}}
.linea .menu{{width:{G.MENU_ANCHO - G.MARGEN}px;text-align:right;font-size:17px;letter-spacing:1px}}
.cifra{{font:600 26px/1.05 ui-monospace,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums}}
/* magnitud de las cifras: miles tenue, millones verde terminal, miles de millones naranja Opus */
.k{{color:var(--sub)}} .m{{color:var(--terminal)}} .b{{color:var(--opus)}}
.barra{{height:6px;border-radius:3px;background:var(--borde);overflow:hidden;margin-top:6px}}
.barra i{{display:block;height:100%;background:var(--terminal);border-radius:3px}}
"""


# iconos de trazo (24x24), en linea: sin peticiones externas
_SVG = {
    "escudo": '<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    "aviso": '<path d="M12 3l9 16H3L12 3z"/><path d="M12 10v4M12 17v.5"/>',
    "refrescar": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "web": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>',
    "cpu": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    "memoria": '<rect x="3" y="7" width="18" height="10" rx="2"/><path d="M7 7v10M11 7v10M15 7v10"/>',
    "disco": '<rect x="3" y="12" width="18" height="8" rx="2"/><path d="M5 12l3-7h8l3 7M7 16h.5"/>',
    "persona": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="10" r="3"/><path d="M6.5 18.5c1.2-2 3.2-3 5.5-3s4.3 1 5.5 3"/>',
    "mano": '<path d="M8 13V6a1.5 1.5 0 0 1 3 0v6M11 11V4.5a1.5 1.5 0 0 1 3 0V11M14 11V6a1.5 1.5 0 0 1 3 0v8c0 4-2.5 7-6 7-2.5 0-4-1.5-5.5-4L4 13.5a1.5 1.5 0 0 1 2.5-1.5L8 14"/>',
    "prohibido": '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    "puerta": '<path d="M6 21V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v17M4 21h16M14 12v.5"/>',
    "candado": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "llave": '<path d="M14.7 6.3a4 4 0 0 0 5 5l-9.5 9.5H7v-3.2z"/><path d="M15 4l5 5"/>',
    "pulso": '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M12 15h5"/>',
    "caja": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
    "brujula": '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5 5-2z"/>',
    "flecha": '<path d="M7 10l5 5 5-5"/>',
    "libro": '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5z"/><path d="M8 7h7"/>',
    "panel": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11"/>',
}


def icono(nombre, color="currentColor"):
    return (f'<svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round">{_SVG[nombre]}</svg>')


COLOR = {"verde": "#34c759", "ambar": "#ff9f0a", "rojo": "#ff453a", "gris": "#8e8e93"}


def pagina(cuerpo, oscuro):
    """Documento completo: sin JS, sin red (CSP), fondo transparente."""
    return ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:">'
            f'<style>{css(oscuro)}</style></head><body><div class="hoja">{cuerpo}</div></body></html>')


def cabecera(titulo, estado, detalle=""):
    c = COLOR[estado]
    fondo = {"verde": "rgba(52,199,89,.22)", "ambar": "rgba(255,159,10,.22)", "rojo": "rgba(255,69,58,.22)",
             "gris": "rgba(142,142,147,.22)"}[estado]
    return (f'<div class="cab"><div class="escudo" style="background:{fondo}">'
            f'{icono("escudo" if estado != "rojo" else "aviso", c)}</div>'
            f'<div><div class="tit">{esc(titulo)}</div><div class="est">{esc(detalle or ESTADO_TXT[estado])}</div></div>'
            f'<div class="refrescar">{icono("refrescar")}</div></div>')


def pestanas(activa):
    return ('<div class="pest"><div class="seg">'
            f'<span class="{"on" if activa == "agentes" else ""}">Agentes</span>'
            f'<span class="{"on" if activa == "servidores" else ""}">Servidores</span></div></div>')


def tarjeta(ic, etiqueta, valor, sub, estado="gris"):
    largo = " largo" if len(str(valor)) > 12 else ""      # p. ej. 22·80·443·3000: letra mas pequena
    return (f'<div class="tj"><div class="et">{icono(ic)}<span>{esc(etiqueta)}</span>'
            f'<i class="p {estado}"></i></div><div class="v{largo}">{esc(valor)}</div><div class="s">{esc(sub)}</div></div>')


def pie(botones, medido):
    """botones: [(icono, texto)]. La linea final lleva la hora y el menu ⋯ (nativo)."""
    filas = "".join(f'<div class="btn">{icono(ic)}<span>{esc(t)}</span></div>' for ic, t in botones)
    return (f'<div class="pie" style="grid-template-columns:repeat({max(1, len(botones))},1fr)">{filas}</div>'
            f'<div class="linea"><span>{esc(medido)}</span><span class="menu">···</span></div>')
