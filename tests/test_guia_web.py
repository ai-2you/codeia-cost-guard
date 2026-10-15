"""La guía como página del panel local: conversor de Markdown seguro y ruta /guia con sesión."""
import http.client
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import guia_web, rutas, servidor_local  # noqa: E402


class ConversorTest(unittest.TestCase):
    def test_estructura_basica(self):
        h = guia_web.a_html("# Título\n\nUn párrafo\ncon dos líneas.\n\n## Sección\n\n- uno\n- dos\n  sigue\n\n1. primero\n2. segundo\n\n```\ncódigo <b>\n```\n\n> cita")
        self.assertIn("<h1>Título</h1>", h)
        self.assertIn("<p>Un párrafo con dos líneas.</p>", h)
        self.assertIn("<h2>Sección</h2>", h)
        self.assertIn("<ul><li>uno</li><li>dos sigue</li></ul>", h)
        self.assertIn("<ol><li>primero</li><li>segundo</li></ol>", h)
        self.assertIn("<pre><code>código &lt;b&gt;</code></pre>", h)
        self.assertIn("<blockquote>cita</blockquote>", h)

    def test_formato_en_linea(self):
        self.assertEqual(guia_web.en_linea("a **b** y *c* y `d`"), "a <strong>b</strong> y <em>c</em> y <code>d</code>")
        self.assertEqual(guia_web.en_linea("`**no** *toca*`"), "<code>**no** *toca*</code>")

    def test_todo_se_escapa(self):
        h = guia_web.a_html('<script>alert(1)</script> **<img src=x onerror=1>**\n\n```\n<script>x</script>\n```')
        self.assertNotIn("<script>", h)
        self.assertNotIn("<img", h)
        self.assertIn("&lt;script&gt;", h)

    def test_solo_se_enlazan_direcciones_http(self):
        self.assertIn('<a href="https://ejemplo.com/a?b=1&amp;c=2"', guia_web.en_linea("[x](https://ejemplo.com/a?b=1&c=2)"))
        for malo in ("[x](javascript:alert(1))", "[x](data:text/html,hola)", "[x](file:///etc/passwd)", "[x](//ejemplo.com)"):
            self.assertNotIn("<a ", guia_web.en_linea(malo), malo)

    def test_comillas_no_rompen_el_atributo(self):
        h = guia_web.en_linea('[x](https://a.example/"onmouseover="alert(1))')
        self.assertNotIn('"onmouseover', h)

    def test_bloque_de_codigo_sin_cerrar_no_se_pierde(self):
        self.assertIn("<pre><code>queda</code></pre>", guia_web.a_html("```\nqueda"))

    def test_la_guia_real_se_convierte_sin_restos_de_marcas(self):
        with open(os.path.join(rutas.RAIZ, "GUIA.md"), encoding="utf-8") as f:
            h = guia_web.a_html(f.read())
        self.assertIn("<h1>", h)
        self.assertNotIn("**", h)
        self.assertNotIn("\n## ", h)

    def test_documento_completo_sin_scripts_ni_recursos_externos(self):
        d = guia_web.documento("# Hola")
        self.assertIn("<title>Guía de CostGuard</title>", d)
        self.assertNotIn("<script", d)
        self.assertNotIn("http", d.split("</style>")[1].replace("https://", ""))
        self.assertEqual(guia_web.CSP, "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")


class RutaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = servidor_local.crear_servidor(0)
        cls.puerto = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.cookie = aislamiento.cookie_de(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def pide(self, ruta, cookie=True):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        h = {"Host": f"127.0.0.1:{self.puerto}"}
        if cookie:
            h["Cookie"] = self.cookie
        c.request("GET", ruta, headers=h)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_guia_con_sesion(self):
        estado, cab, cuerpo = self.pide("/guia")
        self.assertEqual(estado, 200)
        self.assertIn("text/html", cab["Content-Type"])
        self.assertEqual(cab["Content-Security-Policy"], guia_web.CSP)
        self.assertIn("Guía".encode(), cuerpo)

    def test_guia_sin_sesion_no_responde(self):
        self.assertEqual(self.pide("/guia", cookie=False)[0], 401)

    def test_entrar_solo_manda_a_destinos_conocidos(self):
        for ir, esperado in (("guia", "/guia"), ("http://evil.example", "/"), ("//evil.example", "/"), ("", "/")):
            t = servidor_local.nuevo_ticket(self.srv)
            estado, cab, _ = self.pide(f"/entrar?t={t}&ir={ir}", cookie=False)
            self.assertEqual(estado, 302)
            self.assertEqual(cab["Location"], esperado, ir)

    def test_url_panel_con_destino(self):
        with mock.patch.object(servidor_local, "_SERVIDOR", [self.srv]):
            u = servidor_local.url_panel("guia")
            self.assertIn("/entrar?t=", u)
            self.assertTrue(u.endswith("&ir=guia"))
            self.assertNotIn("&ir=", servidor_local.url_panel())
            self.assertNotIn("&ir=", servidor_local.url_panel("otra"))
        with mock.patch.object(servidor_local, "_SERVIDOR", []):
            self.assertTrue(servidor_local.url_panel("guia").endswith("/guia"))


if __name__ == "__main__":
    unittest.main()
