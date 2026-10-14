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


_PELIGROSOS = str.maketrans({"<": "＜", ">": "＞", '"': "＂", "'": "＇", "&": "＆", "`": "｀"})


def _sana(o):
    """Los nombres de proyecto, modelo y plan salen de logs y rutas ajenas y el panel los pinta como HTML (y
    embebidos en un <script>). Se cambian los caracteres de marcado por su version de ancho completo en claves
    y valores: se ven casi igual, y claves y filtros siguen cuadrando porque el cambio es el mismo en todo el panel."""
    if isinstance(o, str):
        return o.translate(_PELIGROSOS)
    if isinstance(o, dict):
        return {_sana(k): _sana(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sana(x) for x in o]
    return o


def datos():
    return _sana(_datos())


def _datos():
    r = costbar.informe()
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


def json_para_script(d):
    """JSON seguro dentro de un <script>: ni `</script>` ni `<!--` ni los separadores de línea de JS pueden salir de una cadena."""
    txt = json.dumps(d, ensure_ascii=False)
    for c, e in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("/", "\\/"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        txt = txt.replace(c, e)
    return txt


def construye(d):
    """Ensambla el unico panel.html a partir de panel_assets/ (fuente separada, salida inline)."""
    cuerpo = _leer("plantilla.html")
    estilos = _leer("estilos.css")
    app_js = _leer("app.js")
    if app_js.count("__DATOS__") != 1:           # el marcador solo puede estar donde se declara D: en un comentario ejecutaría los datos
        raise RuntimeError("app.js debe contener __DATOS__ exactamente una vez")
    app_js = app_js.replace("__DATOS__", json_para_script(d))
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
{_leer("historico.js")}
</script></body></html>"""
    return html


def main():
    costbar.endurecer()
    d = datos()
    open(SALIDA, "w", encoding="utf-8").write(construye(d))
    return SALIDA, d["hoy"]["tok"], d["ritmo"]


if __name__ == "__main__":
    p, tok, ritmo = main()
    print(f"panel: {p} ({os.path.getsize(p)} bytes) · hoy {costbar.fmt_tok(tok)} · ritmo {costbar.fmt_tok(ritmo)}")
