"""Formateo puro del menu de la bandeja de Windows (fase 2), a partir del dict que devuelve
costbar.escanear().

Separado de bandeja_windows.py (que importa pystray y tiene la guarda de plataforma) para que
los tests ejerciten este formato en cualquier sistema operativo, sin crear un icono real de
pystray ni depender de que el modulo raiz pueda importarse.

No se toca costbar.py (regla del plan: lo de macOS queda exactamente igual), asi que el calculo
de fraccion/alerta/textos de CostBar.refrescar (rumps, macOS) se replica aqui para el subconjunto
que pide la fase 2 (5 h, hoy, ritmo). fila()/fmt_tok() SI se reusan tal cual: ya son
cross-platform (infobar/formato_e_icono.py).
"""
from .formato_e_icono import fila, fmt_tok


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
    """La cifra que se pinta dentro del icono: la ventana de 5 h, igual que en macOS."""
    return fmt_tok(r["cinco_h"]["tok"])
