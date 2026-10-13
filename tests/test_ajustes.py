"""Tests de ajustes.py: validacion de config, proyectos y comprobaciones del servidor local."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ajustes

ACTUAL = {"_nota": "x", "tema": "sistema", "nan_api_key": "viejo",
          "proveedores": [{"nombre": "NaN", "modelos": [], "tipo": "suscripcion", "limite_5h_tokens": 5}], "aliases": {}}


class Validar(unittest.TestCase):
    def nuevo(self, **extra):
        n = {"proveedores": [{"nombre": "NaN", "modelos": ["a", " "], "limite_5h_tokens": "7"}], "aliases": {"x": " NaN "}}
        n.update(extra)
        return n

    def test_conserva_campos_que_la_ui_no_conoce(self):
        out = ajustes.validar(self.nuevo(), ACTUAL)
        self.assertEqual(out["_nota"], "x")
        self.assertEqual(out["proveedores"][0]["tipo"], "suscripcion")
        self.assertEqual(out["proveedores"][0]["modelos"], ["a"])
        self.assertEqual(out["proveedores"][0]["limite_5h_tokens"], 7)
        self.assertEqual(out["aliases"], {"x": "NaN"})

    def test_ignora_clave_en_claro_que_llegue_de_la_ui(self):
        out = ajustes.validar(self.nuevo(nan_api_key="nueva-clave-12345"), ACTUAL)
        self.assertEqual(out["nan_api_key"], "viejo")

    def test_rechaza_valores_malos(self):
        for malo in ({"tema": "rojo"}, {"limite_hora_tokens": -1}, {"ignorar": "no-lista"}, {"proyectos": []}):
            with self.assertRaises(ValueError):
                ajustes.validar(self.nuevo(**malo), ACTUAL)
        with self.assertRaises(ValueError):
            ajustes.validar({"proveedores": [{"nombre": "A"}, {"nombre": "A"}], "aliases": {}}, ACTUAL)
        with self.assertRaises(ValueError):
            ajustes.validar({"proveedores": [{"nombre": "A", "uso_api": "otro"}], "aliases": {}}, ACTUAL)
        with self.assertRaises(ValueError):
            ajustes.validar({"proveedores": []}, ACTUAL)

    def test_proyectos_solo_guarda_lo_que_cambia_algo(self):
        out = ajustes.validar(self.nuevo(proyectos={"a": {"alias": " Uno "}, "b": {}, "c": {"oculto": True, "presupuesto_mes_tokens": "9"}}), ACTUAL)
        self.assertEqual(set(out["proyectos"]), {"a", "c"})
        self.assertEqual(out["proyectos"]["a"]["alias"], "Uno")
        self.assertEqual(out["proyectos"]["c"], {"alias": "", "oculto": True, "presupuesto_mes_tokens": 9})

    def test_guardar_sin_cambios_no_anade_campos_vacios(self):
        act = {"proveedores": [{"nombre": "NaN", "modelos": []}], "aliases": {}}
        n = {"proveedores": [{"nombre": "NaN", "modelos": [], "limite_mes_tokens": 0, "cuotas_mes": {}}], "aliases": {}, "proyectos": {}}
        out = ajustes.validar(n, act)
        self.assertEqual(out, {"proveedores": [{"nombre": "NaN", "modelos": []}], "aliases": {}})

    def test_uso_api_vacio_lo_quita(self):
        act = {"proveedores": [{"nombre": "NaN", "uso_api": "nan"}], "aliases": {}}
        out = ajustes.validar({"proveedores": [{"nombre": "NaN", "uso_api": ""}], "aliases": {}}, act)
        self.assertNotIn("uso_api", out["proveedores"][0])


class Proyectos(unittest.TestCase):
    def test_alias_y_oculto(self):
        c = {"proyectos": {"a": {"alias": "Uno"}, "b": {"oculto": True}}}
        self.assertEqual(ajustes.nombre_proyecto("a", c), ("Uno", False))
        self.assertEqual(ajustes.nombre_proyecto("b", c), ("b", True))
        self.assertEqual(ajustes.nombre_proyecto("z", c), ("z", False))


class Servidor(unittest.TestCase):
    def test_peticiones(self):
        def ok(host, origin=None, tipo="application/json", post=True):
            return ajustes.peticion_permitida(host, origin, tipo, 47391, post)
        self.assertTrue(ok("127.0.0.1:47391"))
        self.assertTrue(ok("localhost:47391", "http://localhost:47391"))
        self.assertFalse(ok("evil.com:47391"))                       # DNS rebinding
        self.assertFalse(ok("127.0.0.1:47391", "http://evil.com"))   # CSRF
        self.assertFalse(ok("127.0.0.1:47391", "null"))
        self.assertFalse(ok("127.0.0.1:47391", tipo="text/plain"))   # <form> cruzado
        self.assertTrue(ok("127.0.0.1:47391", tipo=None, post=False))


class Claves(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(ajustes.slug("OpenCode Go"), "opencode-go")

    def test_clave_mala_se_rechaza_antes_de_tocar_el_keychain(self):
        if sys.platform != "darwin":
            self.skipTest("solo macOS")
        for mala in ('abc"def12345', "corta", "con espacio 123456"):
            with self.assertRaises(ValueError):
                ajustes.guardar_clave("prueba", mala)

    def test_api_soportada(self):
        self.assertTrue(ajustes.api_soportada({"nombre": "NaN"}))
        self.assertFalse(ajustes.api_soportada({"nombre": "OpenRouter"}))


class Robustez(unittest.TestCase):
    def test_proyectos_conserva_campos_a_mano(self):
        act = {"proveedores": [], "aliases": {}, "proyectos": {"a": {"alias": "x", "nota": "mia"}}}
        out = ajustes.validar({"proveedores": [], "aliases": {}, "proyectos": {"a": {"alias": "y"}}}, act)
        self.assertEqual(out["proyectos"]["a"]["nota"], "mia")
        self.assertEqual(out["proyectos"]["a"]["alias"], "y")

    def test_escritura_atomica(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "config.json")
            ajustes.escribir_config(ruta, {"a": "ñ"})
            self.assertEqual(json.load(open(ruta, encoding="utf-8")), {"a": "ñ"})
            self.assertEqual(os.listdir(d), ["config.json"])

    def test_panel_neutraliza_marcado_en_nombres(self):
        import panel
        sucio = {'<img src=x onerror=1>': {"</script>": ["a&b", "\"'`"]}}
        txt = repr(panel._sana(sucio))
        for c in '<>"&`':
            self.assertNotIn(c, txt.replace("\\", ""))
        self.assertEqual(panel._sana(5), 5)


if __name__ == "__main__":
    unittest.main()
