"""El panel pinta nombres que salen de los logs (modelo, proyecto, plan): nadie que controle un nombre puede ejecutar código en él."""
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402
from infobar import servidor_local  # noqa: E402
import panel  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOSTIL = "</script><img src=x onerror=alert(1)>"


def asset(nombre):
    with open(os.path.join(RAIZ, "panel_assets", nombre), encoding="utf-8") as f:
        return f.read()


class TestDatosIncrustados(unittest.TestCase):
    def test_json_no_puede_cerrar_el_script(self):
        txt = panel.json_para_script({"modelo": HOSTIL + "<!--\u2028\u2029&"})
        for malo in ("<", ">", "&", "\u2028", "\u2029"):
            self.assertNotIn(malo, txt)
        self.assertEqual(json.loads(txt)["modelo"], HOSTIL + "<!--\u2028\u2029&")      # y sigue siendo el mismo dato

    def test_el_html_final_tiene_un_solo_cierre_de_script(self):
        html = panel.construye({"proveedores": {}, "x": HOSTIL})
        self.assertEqual(html.count("</script>"), 1)
        self.assertNotIn(HOSTIL, html)


@unittest.skipUnless(shutil.which("node"), "hace falta node para ejecutar el script del panel")
class TestNingunNombreSeEjecuta(unittest.TestCase):
    """Se construye el panel con nombres hostiles y se ejecuta de verdad su script hasta declarar D: nada de los datos puede salir de su cadena."""
    def ejecuta(self, d):
        html = panel.construye(d)
        cuerpo = html[html.index("<script>") + 8: html.rindex("</script>")]
        corte = cuerpo.index("\nlet periodo")
        js = "const vm=require('vm');const c={};vm.createContext(c);let e='';try{vm.runInContext(process.argv[1],c)}catch(x){e=String(x)}" \
             "process.stdout.write(JSON.stringify({pwned:c.PWNED===undefined?null:c.PWNED,err:e}))"
        r = subprocess.run(["node", "-e", js, cuerpo[:corte]], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_nombres_que_intentan_salirse_de_comentarios_cadenas_y_etiquetas(self):
        for malo in ("*/globalThis.PWNED=1/*", "\"+globalThis.PWNED=1+\"", "'+globalThis.PWNED=1+'", "</script><script>globalThis.PWNED=1</script>",
                     "\u2028globalThis.PWNED=1//", "`${globalThis.PWNED=1}`", "<!--", "-->globalThis.PWNED=1"):
            for d in ({"modelo_prov": {malo: malo}}, {"proveedores": {malo: {}}, "proyectos": {malo: 1}}, {"x": [malo]}):
                r = self.ejecuta(d)
                self.assertIsNone(r["pwned"], repr(malo))
                self.assertNotIn("SyntaxError", r["err"], repr(malo))

    def test_el_marcador_de_datos_solo_puede_aparecer_una_vez(self):
        self.assertEqual(asset("app.js").count("__DATOS__"), 1)
        original = panel._leer
        with mock.patch.object(panel, "_leer", lambda n: original(n) + ("\n/* __DATOS__ */" if n == "app.js" else "")):
            with self.assertRaises(RuntimeError):
                panel.construye({})

    def test_el_json_incrustado_no_lleva_barras_sin_escapar(self):
        self.assertIsNone(re.search(r"(?<!\\)/", panel.json_para_script({"a": "*/ x /* </ //"})))


class TestSinRestosDeMutacion(unittest.TestCase):
    def test_el_codigo_no_tiene_condiciones_fijas(self):
        """Una mutación mal restaurada deja `if False:` o `if True:`: nunca debe llegar a un commit (una vez desactivó una defensa)."""
        import glob
        for f in glob.glob(os.path.join(RAIZ, "infobar", "*.py")) + [os.path.join(RAIZ, n) for n in ("panel.py", "costbar.py", "barra.py")]:
            for i, linea in enumerate(open(f, encoding="utf-8"), 1):
                self.assertIsNone(re.match(r"\s*(el)?if\s+(False|True)\s*:", linea), f"{f}:{i}: {linea.strip()}")
                self.assertNotIn(" and False", linea, f"{f}:{i}")
                self.assertNotIn("or True:", linea, f"{f}:{i}")


class TestHistoricoSinHtmlDeDatos(unittest.TestCase):
    def test_historico_no_interpreta_datos_como_html(self):
        js = asset("historico.js")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertNotIn("document.write", js)

    def test_el_panel_incluye_la_vista_y_la_pestana_y_suspende_la_recarga(self):
        html = panel.construye({"proveedores": {}})
        for marca in ('id="vista-historico"', 'id="nav-historico"', "window.cargarHistorico"):
            self.assertIn(marca, html)
        self.assertIn("!enAjustes && !enHistorico", asset("ajustes.js"))                # la recarga de cada minuto no echa del histórico

    def test_la_apertura_por_hash_va_despues_de_definir_la_carga(self):
        js = asset("historico.js")
        self.assertLess(js.index("window.cargarHistorico = function"), js.index('location.hash === "#historico"'))


class TestSinManejadoresEnLinea(unittest.TestCase):
    def test_ningun_asset_usa_atributos_on(self):
        for nombre in ("app.js", "graficos.js", "plantilla.html", "ajustes.js", "historico.js"):
            self.assertIsNone(re.search(r"""\bon[a-z]+\s*=\s*["']""", asset(nombre)), nombre)

    def test_las_acciones_del_html_existen(self):
        acciones = set(re.findall(r'data-a="([A-Za-z]+)"', asset("plantilla.html")))
        acciones |= set(re.findall(r'data-a="([A-Za-z]+)"', asset("app.js")))
        acciones |= set(re.findall(r'"(set[A-Za-z]+)"', asset("app.js")))          # nombres que se pasan a botonesPeriodo/Plan/Pestanas
        self.assertGreaterEqual(len(acciones), 10)
        declaradas = re.search(r"const ACCIONES = \{(.*?)\};", asset("app.js"), re.S).group(1)
        for a in acciones:
            self.assertIn(a, declaradas, a)


@unittest.skipUnless(shutil.which("node"), "hace falta node para ejecutar el escapador del navegador")
class TestEscapadorDelNavegador(unittest.TestCase):
    def ejecuta(self, expresion):
        js = asset("app.js")
        ini, fin = js.index("function esc("), js.index("const D = escapar(")
        r = subprocess.run(["node", "-e", js[ini:fin] + f"\nprocess.stdout.write(JSON.stringify({expresion}));"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_escapa_texto_claves_y_anidados(self):
        out = self.ejecuta("""escapar({'a<b': ['x"y', {"k'": "`\\\\"}], n: 3, v: null})""")
        self.assertEqual(out, {"a&#60;b": ['x&#34;y', {"k&#39;": "&#96;&#92;"}], "n": 3, "v": None})

    def test_la_carga_hostil_no_deja_ningun_caracter_peligroso(self):
        out = self.ejecuta(f"esc({json.dumps(HOSTIL + chr(39) + chr(34))})")
        for c in "<>\"'`":
            self.assertNotIn(c, out)


class TestCabecerasDelServidor(unittest.TestCase):
    def test_el_panel_lleva_nonce_y_no_permite_nada_externo(self):
        html, csp = servidor_local.html_con_nonce(b"<html><script>var a=1;</script></html>")
        nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
        self.assertIn(f'<script nonce="{nonce}">'.encode(), html)
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn("unsafe-inline'; img", csp.replace("style-src 'unsafe-inline'", ""))
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotEqual(servidor_local.html_con_nonce(b"<script>")[1], csp)             # nonce nuevo cada vez

    def test_las_respuestas_de_datos_llevan_una_csp_cerrada(self):
        srv = servidor_local.crear_servidor(0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        puerto = srv.server_address[1]
        c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=30)
        c.request("GET", "/ajustes", headers={"Host": f"127.0.0.1:{puerto}", "Cookie": aislamiento.cookie_de(srv)})
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Security-Policy"), "default-src 'none'; frame-ancestors 'none'")


if __name__ == "__main__":
    unittest.main()
