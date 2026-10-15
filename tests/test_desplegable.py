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


def _entrada(nombre_fichero, nombre_servicio="tienda-production_tienda-app", servicio_ok=True):
    with open(os.path.join(DATOS, nombre_fichero), encoding="utf-8") as f:
        d = servidores_ssh.interpretar(servidores_ssh.secciones(f.read()))
    d["servicios"] = [{"nombre": nombre_servicio, "ok": servicio_ok, "detalle": "1/1"}]
    webs = [{"url": "https://tienda.example.com", "ms": 120, "codigo": 200, "error": ""}]
    t = servidores_estado.tarjetas(d, webs, [22, 80, 443])
    checks = servidores_estado.bastionado(d, [22, 80, 443])
    return {"datos": d, "webs": webs, "expuestos": [22, 80, 443], "tarjetas": t, "bastionado": checks,
            "bastionado_resumen": servidores_estado.resumen_bastionado(checks), "estado": "ambar",
            "ok_en": time.time(), "error": ""}


class PaginasTest(unittest.TestCase):
    CFG = [{"nombre": "tienda", "ssh": "infobar@tienda.example.com"}]

    def test_datos_hostiles_se_escapan(self):
        e = _entrada("servidor-dokploy-con-root.txt", '<img src=x onerror=alert(1)>', servicio_ok=False)
        e["webs"] = [{"url": "https://<b>x</b>.example.com", "ms": 5, "codigo": 200, "error": ""}]
        h = desplegable_servidores.documento("tienda", {"servidores": {"tienda": e}}, self.CFG, True)
        self.assertNotIn("<img src=x", h)
        self.assertNotIn("<b>x</b>", h)
        self.assertIn("&lt;img src=x", h)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", h)
        self.assertIn("default-src 'none'", h)          # CSP: ni scripts ni red

    def test_la_tarjeta_de_webs_rotula_el_certificado(self):
        def pagina(webs):
            e = _entrada("servidor-dokploy-con-root.txt")
            e["webs"] = webs
            e["tarjetas"] = servidores_estado.tarjetas(e["datos"], webs, [22, 80, 443])
            return desplegable_servidores.documento("tienda", {"servidores": {"tienda": e}}, self.CFG, False)
        w = {"url": "https://tienda.example.com", "ms": 120, "codigo": 200, "error": "", "cert_dias": 25}
        self.assertIn("certificado: 25 d", pagina([w]))
        malo = dict(w, cert_dias=None, ms=None, codigo=None, error="certificado: CERTIFICATE_VERIFY_FAILED")
        h = pagina([w, malo])
        self.assertIn("certificado inválido", h)
        self.assertNotIn("certificado: 25 d", h)                      # con una web inválida no se presume la fecha de otra
        self.assertNotIn("certificado:", pagina([dict(w, cert_dias=None)]))

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
        for txt in ("Procesador", "Protección", "Qué falta", "Quién más intenta entrar", "tienda.example.com", "Por qué no está en verde", "Terminal"):
            self.assertIn(txt, ok)

    def pagina(self, webs=None, servicios=None):
        e = _entrada("servidor-dokploy-con-root.txt")
        if webs is not None:
            e["webs"] = webs
            e["tarjetas"] = servidores_estado.tarjetas(e["datos"], webs, [22, 80, 443])
        if servicios is not None:
            e["datos"]["servicios"] = servicios
        return desplegable_servidores.documento("tienda", {"servidores": {"tienda": e}}, self.CFG, False)

    def test_enseña_todas_las_webs_con_las_que_fallan_primero(self):
        ok = lambda n: {"url": f"https://ok{n}.example.com/", "ms": 100 + n, "codigo": 200, "error": ""}  # noqa: E731
        h = self.pagina([ok(1), {"url": "https://rota.example.com/", "ms": 80, "codigo": 502, "error": ""},
                         {"url": "https://muda.example.com/", "ms": None, "codigo": None, "error": "timeout"},
                         {"url": "https://falta.example.com/", "ms": 50, "codigo": 404, "error": ""}, ok(2)])
        self.assertIn("5 · 2 con error", h)
        for dominio in ("ok1.example.com", "rota.example.com", "muda.example.com", "falta.example.com", "ok2.example.com"):
            self.assertIn(dominio, h)
        self.assertIn("502 · 80 ms", h)
        self.assertIn("sin respuesta", h)
        self.assertLess(h.index("rota.example.com"), h.index("falta.example.com"))      # rojo antes que ámbar
        self.assertLess(h.index("falta.example.com"), h.index("ok1.example.com"))       # ámbar antes que verde
        self.assertIn("2 webs con error o sin responder", h)

    def test_todas_las_webs_bien_lo_dicen_y_un_tope_las_recorta(self):
        webs = [{"url": f"https://w{n}.example.com/", "ms": 10, "codigo": 200, "error": ""} for n in range(20)]
        h = self.pagina(webs)
        self.assertIn("20 · todas responden", h)
        self.assertIn(f"+{20 - desplegable_servidores.MAX_WEBS} más", h)

    def test_los_servicios_sanos_no_se_enseñan_y_los_caidos_si(self):
        sano = self.pagina(servicios=[{"nombre": "p_sano-uno", "ok": True, "detalle": "1/1"}])
        self.assertNotIn("sano-uno", sano)
        self.assertNotIn("Servicios caídos", sano)
        roto = self.pagina(servicios=[{"nombre": "p_sano-uno", "ok": True, "detalle": "1/1"}, {"nombre": "p_roto-dos", "ok": False, "detalle": "0/1"}])
        self.assertIn("Servicios caídos", roto)
        self.assertIn("roto-dos", roto)
        self.assertNotIn("sano-uno", roto)

    def test_explica_por_que_no_esta_en_verde(self):
        h = self.pagina()
        self.assertIn("Por qué no está en verde", h)
        self.assertIn("ajustes de seguridad hechos", h)

    def test_el_motivo_de_fail2ban_distingue_instalado_de_no(self):
        t = {"bloqueadas": {"estado": "ambar"}}
        self.assertIn("no está instalado", desplegable_servidores._motivos(t, {}, {"fail2ban": {"instalado": False}}, [])[0][1])
        self.assertIn("Muchas direcciones", desplegable_servidores._motivos(t, {}, {"fail2ban": {"instalado": True}}, [])[0][1])

    def test_sin_motivos_no_hay_seccion(self):
        t = {k: {"estado": "verde"} for k in ("cpu", "memoria", "disco", "webs", "certificado", "sesiones", "intentos", "bloqueadas", "puertos", "mantenimiento", "servicios")}
        self.assertEqual(desplegable_servidores._motivos(t, {"estado": "verde", "total": 12, "hechos": 12}, {}, []), [])

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
