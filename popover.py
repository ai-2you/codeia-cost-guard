#!/usr/bin/env python3
"""Vista previa del desplegable en HTML (sin la app): escribe popover-*.html con los datos de ahora.

La app (barra.py) no usa este fichero: pinta los PNG desde su hilo trabajador
(infobar/desplegable_png.py). Esto sirve para ver o depurar el diseno en un navegador.

Uso:  python3 popover.py      # popover-agentes-{claro,oscuro}.html y popover-<servidor>-{claro,oscuro}.html
"""
import os, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import costbar  # noqa: E402
from infobar import desplegable_agentes, desplegable_servidores, servidores_ciclo  # noqa: E402


def main():
    r = costbar.escanear()
    cache = servidores_ciclo.leer_cache()
    servidores = costbar.cfg().get("servidores") or []
    escritos = []
    for oscuro, tema in ((False, "claro"), (True, "oscuro")):
        paginas = [("agentes", desplegable_agentes.documento(r, oscuro))]
        paginas += [(s["nombre"], desplegable_servidores.documento(s["nombre"], cache, servidores, oscuro))
                    for s in servidores]
        for nombre, html in paginas:
            ruta = os.path.join(AQUI, f"popover-{nombre}-{tema}.html")
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(html)
            escritos.append(ruta)
    return escritos


if __name__ == "__main__":
    print("\n".join(main()))
