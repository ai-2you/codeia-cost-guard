#!/usr/bin/env python3
"""Genera panel.html: consumo en tokens, con modo claro/oscuro, filtros por plan y periodo,
graficos (barras, lineas, tarta, calendario...) y detalle al clicar un dia o un tramo.

Todo el dato se calcula en Python (datos()) y se embute como JSON; el HTML/CSS/JS vive en
panel_assets/ (varios ficheros pequeños en vez de una sola plantilla gigante) pero el
resultado sigue siendo un unico panel.html autocontenido: sin red, sin dependencias."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costbar

AQUI = os.path.dirname(os.path.abspath(__file__))
ASSETS = f"{AQUI}/panel_assets"
SALIDA = f"{AQUI}/panel.html"
def _leer(nombre):
    return open(f"{ASSETS}/{nombre}", encoding="utf-8").read()


def datos():
    r = costbar.escanear()
    def b(x):
        return {"gi": int(x["gi"]), "go": int(x["go"]), "cr": int(x["cr"]), "cw": int(x["cw"]),
                "tok": int(x["tok"]), "usd": round(x["usd"], 2), "turnos": int(x["turnos"])}
    return {
        "generado": time.strftime("%d/%m/%Y %H:%M:%S"),
        "cinco_h": int(r["cinco_h"]["tok"]), "ritmo": int(r["ritmo"]["tok"]),
        "cache_pct": round(r["cache_pct_hoy"], 1),
        "hoy": b(r["hoy"]), "ayer": b(r["ayer"]), "semana": b(r["semana"]), "mes": b(r["mes"]),
        "limites": {k: int(v) for k, v in r["limites"].items() if k in ("limite_hora_tokens", "limite_5h_tokens", "limite_semana_tokens") and isinstance(v, (int, float))},
        "dias": {d: {"tok": int(v["tok"]), "turnos": int(v["turnos"]), "usd": round(v["usd"], 2), "cr": int(v["cr"])} for d, v in sorted(r["dias"].items())},
        "proveedores": {n: {"cinco_h": int(x["cinco_h"]), "hoy": int(x["hoy"]), "semana": int(x["semana"]),
                            "mes": int(x["mes"]), "pct_5h": round(x["pct_5h"], 1), "pct_semana": round(x["pct_semana"], 1),
                            "modelos": x["modelos"], "auto": bool(x.get("auto", False)),
                            "modelos_cuota": x.get("modelos_cuota") or [],
                            "cuota_fuente": x.get("cuota_fuente") or "logs"}
                        for n, x in r["proveedores"].items()},
        "modelo_prov": r["modelo_prov"],
        "cruce": {n: {m: {d: int(t) for d, t in dd.items()} for m, dd in mm.items()} for n, mm in r["cruce"].items()},
        "proy_prov": {n: {p: int(t) for p, t in pp.items()} for n, pp in r["proy_prov"].items()},
        "proy_cruce": {n: {p: {d: int(t) for d, t in dd.items()} for p, dd in mm.items()} for n, mm in r.get("proy_cruce", {}).items()},
        "proyectos": {n: int(x["tok"]) for n, x in sorted(r["proyectos"].items(), key=lambda kv: -kv[1]["tok"])},
        "bases": r.get("bases") or [], "fuentes": r["fuentes"],
    }


def construye(d):
    """Ensambla el unico panel.html a partir de panel_assets/ (fuente separada, salida inline)."""
    cuerpo = _leer("plantilla.html")
    estilos = _leer("estilos.css")
    app_js = _leer("app.js").replace("__DATOS__", json.dumps(d, ensure_ascii=False))
    graficos_js = _leer("graficos.js")
    html = f"""<!doctype html><html lang="es" data-tema="claro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Consumo de tokens</title>
<style>
{estilos}
</style></head><body>
{cuerpo}
<script>
{graficos_js}
{app_js}
{_leer("ajustes.js")}
</script></body></html>"""
    return html


def main():
    d = datos()
    open(SALIDA, "w", encoding="utf-8").write(construye(d))
    return SALIDA, d["hoy"]["tok"], d["ritmo"]


if __name__ == "__main__":
    p, tok, ritmo = main()
    print(f"panel: {p} ({os.path.getsize(p)} bytes) · hoy {costbar.fmt_tok(tok)} · ritmo {costbar.fmt_tok(ritmo)}")
