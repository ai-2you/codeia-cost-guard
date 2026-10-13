"""Desplegable: zonas de clic (y la conversion al eje de AppKit), escapado de datos remotos, CSP,
y que las dos pestanas se generan en todos los estados (sin servidores, con error, con datos).

Arrancar con:  python3 -m unittest discover tests
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401  (HOME y rutas temporales: antes que costbar)
from infobar import desplegable_agentes, desplegable_servidores, servidores_estado, servidores_ssh  # noqa: E402
from infobar import geometria_desplegable as G  # noqa: E402

DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos")


class ZonasTest(unittest.TestCase):
    def test_cabecera_desde_arriba_y_pie_desde_abajo(self):
        for alto in (600, 1100):                       # el mismo clic vale con cualquier alto
            # AppKit: vista sin voltear, y=0 abajo
            self.assertEqual(G.zona_en(100, alto - (G.PESTANAS_Y + 10), alto, 3, True), "pestana:agentes")
            self.assertEqual(G.zona_en(300, alto - (G.PESTANAS_Y + 10), alto, 3, True), "pestana:servidores")
            self.assertEqual(G.zona_en(200, alto - (G.SELECTOR_Y + 5), alto, 3, True), "selector")
            self.assertEqual(G.zona_en(G.ANCHO - 30, alto - 30, alto, 3, True), "refrescar")
            self.assertEqual(G.zona_en(40, G.LINEA_ALTO + 20, alto, 3, True), "boton:0")
            self.assertEqual(G.zona_en(G.ANCHO - 40, G.LINEA_ALTO + 20, alto, 3, True), "boton:2")
            self.assertEqual(G.zona_en(G.ANCHO - 10, 10, alto, 3, True), "menu")
            self.assertIsNone(G.zona_en(200, alto / 2, alto, 3, True))

    def test_sin_selector_en_agentes(self):
        self.assertIsNone(G.zona_en(200, 600 - (G.SELECTOR_Y + 5), 600, 2, False))


def _entrada(nombre_fichero, nombre_servicio="tienda-production_tienda-app"):
    with open(os.path.join(DATOS, nombre_fichero), encoding="utf-8") as f:
        d = servidores_ssh.interpretar(servidores_ssh.secciones(f.read()))
    d["servicios"] = [{"nombre": nombre_servicio, "ok": True, "detalle": "1/1"}]
    webs = [{"url": "https://tienda.example.com", "ms": 120, "codigo": 200, "error": ""}]
    t = servidores_estado.tarjetas(d, webs, [22, 80, 443])
    checks = servidores_estado.bastionado(d, [22, 80, 443])
    return {"datos": d, "webs": webs, "expuestos": [22, 80, 443], "tarjetas": t, "bastionado": checks,
            "bastionado_resumen": servidores_estado.resumen_bastionado(checks), "estado": "ambar",
            "ok_en": time.time(), "error": ""}


class PaginasTest(unittest.TestCase):
    CFG = [{"nombre": "tienda", "ssh": "infobar@tienda.example.com"}]

    def test_datos_hostiles_se_escapan(self):
        e = _entrada("servidor-dokploy-con-root.txt", '<img src=x onerror=alert(1)>')
        h = desplegable_servidores.documento("tienda", {"servidores": {"tienda": e}}, self.CFG, True)
        self.assertNotIn("<img src=x", h)
        self.assertIn("&lt;img src=x", h)
        self.assertIn("default-src 'none'", h)          # CSP: ni scripts ni red

    def test_estados_de_la_pestana(self):
        vacio = desplegable_servidores.documento("", {}, [], False)
        self.assertIn("alta-servidor.sh", vacio)
        sin_datos = desplegable_servidores.documento("tienda", {}, self.CFG, False)
        self.assertIn("Todavía sin datos", sin_datos)
        caido = desplegable_servidores.documento(
            "tienda", {"servidores": {"tienda": {"error": "timeout", "estado": "rojo", "intento_en": time.time()}}},
            self.CFG, False)
        self.assertIn("Sin conexión: timeout", caido)
        ok = desplegable_servidores.documento("tienda", {"servidores": {"tienda": _entrada("servidor-dokploy-con-root.txt")}},
                                              self.CFG, True)
        self.assertIn('class="ip">tienda.example.com<', ok)       # sin host en cache: el de la config
        for txt in ("Procesador", "Protección", "Qué falta", "Quién más intenta entrar", "tienda-app", "Terminal"):
            self.assertIn(txt, ok)

    def test_agentes(self):
        hoy = time.strftime("%Y-%m-%d")
        vacio = {"gi": 1, "go": 2, "cr": 3, "cw": 4, "tok": 10, "turnos": 1, "usd": 0}
        r = {"limites": {"limite_hora_tokens": 8_000_000, "limite_5h_tokens": 0}, "ritmo": {"tok": 1000},
             "cinco_h": {"tok": 1_500_000}, "semana": dict(vacio), "hoy": dict(vacio), "pct_5h": 0,
             "proveedores": {"NaN <x>": {"cinco_h": 5, "semana": 6, "pct_5h": 0, "pct_semana": 0,
                                         "modelos_cuota": [{"modelo": "glm", "mes": 10, "limite": 100, "pct": 10.0}],
                                         "cuota_fuente": "api"}},
             "cache_pct_hoy": 50.0, "dias": {hoy: {"tok": 10}}, "modelos_hoy": {"claude-opus-5": {"tok": 9}},
             "proyectos": {"GitHub/blog/es": {"tok": 9}}}
        h = desplegable_agentes.documento(r, False)
        self.assertIn("1,5 M", h)
        self.assertIn("NaN &lt;x&gt;", h)
        self.assertIn("en vivo · NaN", h)
        self.assertIn("blog/es", h)


if __name__ == "__main__":
    unittest.main()
