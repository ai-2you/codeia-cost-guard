"""Pestana Agentes del desplegable: el consumo de tokens de siempre (ventana de 5 h, planes, cuotas
mensuales de NaN, hoy, historial, modelos y proyectos) con el estilo glass.
"""
import re, time

from .desplegable_comun import cabecera, esc, pagina, pestanas, pie, tarjeta
from .formato_e_icono import fmt_tok
from .planes_y_tarifas import corto

BOTONES = [("panel", "Abrir panel"), ("libro", "Guía")]


def cifra(n):
    """18300000 -> '18,3 M' con el color de su magnitud (K/M/B)."""
    txt = fmt_tok(n)
    m = re.match(r"([\d.,]+)\s*([KMB]?)$", txt)
    u = (m.group(2) if m else "") or "K"
    return f'<span class="{u.lower()}">{esc(txt)}</span>'


def _fila(izq, der):
    return f'<div class="f"><span>{izq}</span><span class="mono">{der}</span></div>'


def _barra(pct):
    return f'<div class="barra"><i style="width:{max(0, min(100, pct)):.0f}%"></i></div>' if pct else ""


def documento(r, oscuro, ahora=None):
    ahora = ahora or time.time()
    lim_h = (r.get("limites") or {}).get("limite_hora_tokens") or 0
    alto = bool(lim_h) and r["ritmo"]["tok"] >= lim_h
    estado = "ambar" if alto else "verde"
    detalle = f'Ritmo alto: {fmt_tok(r["ritmo"]["tok"])}/h' if alto else f'Última hora: {fmt_tok(r["ritmo"]["tok"])} tokens'
    h = [cabecera("Mis agentes", estado, detalle), pestanas("agentes")]

    lim5 = (r.get("limites") or {}).get("limite_5h_tokens") or 0
    pct5 = r.get("pct_5h", 0) if lim5 else 0
    nota5 = f"{pct5:.0f} % del límite" if lim5 else "tokens (entrada + salida + caché)"
    h.append('<div class="sec"><span>Últimas 5 h</span><span class="nota">ventana en curso</span></div>'
             f'<div class="tj"><div class="cifra">{cifra(r["cinco_h"]["tok"])}</div>'
             f'<div class="s" style="margin-top:4px">{nota5}</div>{_barra(pct5)}</div>')

    sem, hoy = r["semana"], r["hoy"]
    h.append('<div class="rejilla" style="margin-top:9px">'
             + tarjeta("pulso", "Hoy", fmt_tok(hoy["tok"]), f'{hoy["turnos"]:,} turnos'.replace(",", "."), "verde")
             + tarjeta("caja", "Semana", fmt_tok(sem["tok"]), f'{sem["turnos"]:,} turnos'.replace(",", "."), "verde")
             + '</div>')

    planes = [(n, x) for n, x in r["proveedores"].items() if x["cinco_h"] or x["semana"]]
    if planes:
        filas = "".join(
            f'<div class="f" style="display:block"><div style="display:flex;justify-content:space-between">'
            f'<span>{esc(n)}</span><span class="mono">{cifra(x["cinco_h"])} · {esc(fmt_tok(x["semana"]))}</span></div>'
            f'{_barra(x["pct_5h"] or x["pct_semana"])}</div>' for n, x in planes)
        h.append(f'<div class="sec"><span>Planes</span><span class="nota">5 h · semana</span></div><div class="lista">{filas}</div>')

    cuotas = [mc for x in r["proveedores"].values() for mc in (x.get("modelos_cuota") or [])]
    if cuotas:
        en_vivo = any(x.get("cuota_fuente") == "api" for x in r["proveedores"].values() if x.get("modelos_cuota"))
        filas = "".join(
            f'<div class="f" style="display:block"><div style="display:flex;justify-content:space-between">'
            f'<span>{esc(mc["modelo"])}</span><span class="mono">{esc(fmt_tok(mc["mes"]))}'
            + (f' / {esc(fmt_tok(mc["limite"]))} · {mc["pct"]:.0f} %' if mc.get("limite") else " · sin tope")
            + f'</span></div>{_barra(mc.get("pct") or 0)}</div>' for mc in cuotas[:6])
        h.append(f'<div class="sec"><span>Cuotas del mes</span><span class="nota">{"en vivo · NaN" if en_vivo else "de tus logs"}</span></div>'
                 f'<div class="lista">{filas}</div>')

    h.append('<div class="sec"><span>Hoy, en detalle</span></div><div class="lista">'
             + _fila("Entrada", cifra(hoy["gi"])) + _fila("Salida", cifra(hoy["go"]))
             + _fila(f'Caché leída · {r["cache_pct_hoy"]:.0f} %', cifra(hoy["cr"]))
             + _fila("Caché escrita", cifra(hoy["cw"])) + '</div>')

    ayer = time.strftime("%Y-%m-%d", time.localtime(ahora - 86400))
    dias_2a5 = [time.strftime("%Y-%m-%d", time.localtime(ahora - i * 86400)) for i in (2, 3, 4, 5)]
    tok_2a5 = sum(r["dias"].get(d, {}).get("tok", 0) for d in dias_2a5)
    h.append('<div class="sec"><span>Historial</span></div><div class="lista">'
             + _fila("Ayer", cifra(r["dias"][ayer]["tok"]) if ayer in r["dias"] else "—")
             + _fila("Hace 2 a 5 días", cifra(tok_2a5) if tok_2a5 else "—") + '</div>')

    modelos = sorted(r["modelos_hoy"].items(), key=lambda x: -x[1]["tok"])[:4]
    proyectos = sorted(r["proyectos"].items(), key=lambda x: -x[1]["tok"])[:4]
    lm = "".join(_fila(esc(corto(n)), cifra(v["tok"])) for n, v in modelos) or _fila("—", "")
    lp = "".join(_fila(esc("/".join(n.split("/")[-2:])[:18]), cifra(v["tok"])) for n, v in proyectos) or _fila("—", "")
    h.append('<div class="rejilla" style="margin-top:14px">'
             f'<div><div class="sec" style="margin-top:0"><span>Modelos hoy</span></div><div class="lista">{lm}</div></div>'
             f'<div><div class="sec" style="margin-top:0"><span>Proyectos</span></div><div class="lista">{lp}</div></div></div>')

    h.append(pie(BOTONES, f'Medido a las {time.strftime("%H:%M", time.localtime(ahora))}'))
    return pagina("".join(h), oscuro)
