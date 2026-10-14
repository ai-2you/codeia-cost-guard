"""Pestaña Ajustes: normalizacion de lo que envia la UI, alias/ocultacion de proyectos, saneado de
nombres en el panel y los endpoints /ajustes y /clave del servidor local (sin tocar el Llavero real).

Arrancar con:  python3 -m unittest discover tests
"""
import http.client
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401  (HOME y rutas temporales: antes que costbar)
from infobar import ajustes, claves, config_y_estado, rutas, servidor_local  # noqa: E402

ACTUAL = {"_nota": "x", "nan_api_key": "viejo",
          "proveedores": [{"nombre": "NaN", "modelos": [], "tipo": "suscripcion", "limite_5h_tokens": 5}], "aliases": {}}


class Normalizar(unittest.TestCase):
    def nuevo(self, **extra):
        n = {"proveedores": [{"nombre": "NaN", "modelos": ["a", " "], "limite_5h_tokens": "7"}], "aliases": {"x": " NaN "}}
        n.update(extra)
        return n

    def test_conserva_campos_del_proveedor_que_la_ui_no_conoce(self):
        out = ajustes.normalizar(self.nuevo(), ACTUAL)
        self.assertEqual(out["proveedores"][0]["tipo"], "suscripcion")
        self.assertEqual(out["proveedores"][0]["modelos"], ["a"])
        self.assertEqual(out["proveedores"][0]["limite_5h_tokens"], 7)
        self.assertEqual(out["aliases"], {"x": "NaN"})

    def test_el_parche_no_lleva_claves_en_claro_ni_campos_ajenos(self):
        out = ajustes.normalizar(self.nuevo(nan_api_key="nueva-clave-12345", limite_5h_tokens=9, _nota="y"), ACTUAL)
        self.assertEqual(set(out), {"aliases", "proveedores"})

    def test_rechaza_valores_malos(self):
        for malo in ({"limite_hora_tokens": -1}, {"ignorar": "no-lista"}, {"proyectos": []}):
            with self.assertRaises(ValueError):
                ajustes.normalizar(self.nuevo(**malo), ACTUAL)
        with self.assertRaises(ValueError):
            ajustes.normalizar({"proveedores": [{"nombre": "A"}, {"nombre": "A"}], "aliases": {}}, ACTUAL)
        with self.assertRaises(ValueError):
            ajustes.normalizar({"proveedores": [{"nombre": "A", "uso_api": "otro"}], "aliases": {}}, ACTUAL)
        with self.assertRaises(ValueError):
            ajustes.normalizar({"proveedores": []}, ACTUAL)

    def test_proyectos_solo_guarda_lo_que_cambia_algo(self):
        out = ajustes.normalizar(self.nuevo(proyectos={"a": {"alias": " Uno "}, "b": {}, "c": {"oculto": True, "presupuesto_mes_tokens": "9"}}), ACTUAL)
        self.assertEqual(set(out["proyectos"]), {"a", "c"})
        self.assertEqual(out["proyectos"]["a"]["alias"], "Uno")
        self.assertEqual(out["proyectos"]["c"], {"alias": "", "oculto": True, "presupuesto_mes_tokens": 9})

    def test_proyectos_conserva_campos_a_mano(self):
        act = {"proveedores": [], "aliases": {}, "proyectos": {"a": {"alias": "x", "nota": "mia"}}}
        out = ajustes.normalizar({"proveedores": [], "aliases": {}, "proyectos": {"a": {"alias": "y"}}}, act)
        self.assertEqual(out["proyectos"]["a"], {"alias": "y", "nota": "mia", "oculto": False, "presupuesto_mes_tokens": 0})

    def test_guardar_sin_cambios_no_anade_campos_vacios(self):
        act = {"proveedores": [{"nombre": "NaN", "modelos": []}], "aliases": {}}
        n = {"proveedores": [{"nombre": "NaN", "modelos": [], "limite_mes_tokens": 0, "cuotas_mes": {}}], "aliases": {},
             "proyectos": {}, "limite_hora_tokens": 8000000, "ignorar": ["synthetic", "<synthetic>", "sin-modelo"]}
        self.assertEqual(ajustes.normalizar(n, act), {"proveedores": [{"nombre": "NaN", "modelos": []}], "aliases": {}})

    def test_uso_api_vacio_lo_quita(self):
        act = {"proveedores": [{"nombre": "NaN", "uso_api": "nan"}], "aliases": {}}
        out = ajustes.normalizar({"proveedores": [{"nombre": "NaN", "uso_api": ""}], "aliases": {}}, act)
        self.assertNotIn("uso_api", out["proveedores"][0])

    def test_servidores_e_ips_pasan_tal_cual(self):
        srv = [{"nombre": "p", "ssh": "infobar@1.2.3.4"}]
        out = ajustes.normalizar(self.nuevo(servidores=srv, ips_conocidas=["1.1.1.1"]), ACTUAL)
        self.assertEqual((out["servidores"], out["ips_conocidas"]), (srv, ["1.1.1.1"]))


class Auxiliares(unittest.TestCase):
    def test_alias_y_oculto(self):
        c = {"proyectos": {"a": {"alias": "Uno"}, "b": {"oculto": True}}}
        self.assertEqual(ajustes.nombre_proyecto("a", c), ("Uno", False))
        self.assertEqual(ajustes.nombre_proyecto("b", c), ("b", True))
        self.assertEqual(ajustes.nombre_proyecto("z", c), ("z", False))

    def test_cuentas_del_llavero(self):
        self.assertEqual(ajustes.cuenta_de("NaN"), "nan")
        self.assertEqual(ajustes.cuenta_de("OpenCode Go"), "prov:opencode-go")
        self.assertTrue(claves.cuenta_valida(ajustes.cuenta_de("OpenCode Go")))
        self.assertTrue(claves.cuenta_valida(ajustes.cuenta_de("¿¿??")))

    def test_api_soportada(self):
        self.assertTrue(ajustes.api_soportada({"nombre": "NaN"}))
        self.assertTrue(ajustes.api_soportada({"nombre": "Otro", "uso_api": "nan"}))
        self.assertFalse(ajustes.api_soportada({"nombre": "OpenRouter"}))

    def test_panel_neutraliza_marcado_en_nombres(self):
        import panel
        sucio = {'<img src=x onerror=1>': {"</script>": ["a&b", "\"'`"]}}
        txt = repr(panel._sana(sucio))
        for c in '<>"&`':
            self.assertNotIn(c, txt)
        self.assertEqual(panel._sana(5), 5)


class ServidorAjustes(unittest.TestCase):
    CONFIG = {"proveedores": [{"nombre": "NaN", "modelos": []}, {"nombre": "OpenCode Go", "modelos": []}],
              "aliases": {}, "limite_hora_tokens": 8000000,
              "servidores": [{"nombre": "prod", "ssh": "infobar@prod", "panel": "dokploy", "panel_url": "https://d.example.com"}]}

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

    def setUp(self):
        config_y_estado._ULTIMO_CFG = {}
        with open(rutas.CONFIG, "w", encoding="utf-8") as f:
            json.dump(self.CONFIG, f)
        self.guardadas = {}
        self.parches = [
            mock.patch.object(claves, "hay_llavero", return_value=True),
            mock.patch.object(claves, "leer_clave", side_effect=lambda c: self.guardadas.get(c, "")),
            mock.patch.object(claves, "guardar_clave", side_effect=lambda c, v=None: self.guardadas.__setitem__(c, v)),
            mock.patch.object(claves, "borrar_clave", side_effect=lambda c: self.guardadas.pop(c, None)),
        ]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in self.parches:
            p.stop()

    def _pide(self, metodo, ruta, cuerpo=None, origen=True):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        cab = {"Content-Type": "application/json", "Cookie": self.cookie}
        if origen:
            cab["Origin"] = f"http://127.0.0.1:{self.puerto}"
        c.request(metodo, ruta, json.dumps(cuerpo) if cuerpo is not None else None, cab)
        r = c.getresponse()
        return r.status, json.loads(r.read().decode() or "{}")

    def test_ajustes_trae_proveedores_paneles_y_no_secretos(self):
        self.guardadas["nan"] = "x" * 20
        st, d = self._pide("GET", "/ajustes")
        self.assertEqual(st, 200)
        self.assertEqual({p["nombre"]: p["clave_guardada"] for p in d["proveedores"]}, {"NaN": True, "OpenCode Go": False})
        self.assertEqual(d["paneles"]["prod"]["cuenta"], "dokploy:d.example.com")
        self.assertNotIn("x" * 20, json.dumps(d))

    def test_ajustes_trae_el_estado_de_las_copias(self):
        st, d = self._pide("GET", "/ajustes")
        self.assertEqual(st, 200)
        self.assertIn("estado", d["copias"])

    def test_clave_se_guarda_y_se_borra(self):
        st, d = self._pide("POST", "/clave", {"proveedor": "OpenCode Go", "clave": "abcdefgh12345678"})
        self.assertEqual((st, d["ok"]), (200, True))
        self.assertEqual(self.guardadas, {"prov:opencode-go": "abcdefgh12345678"})
        st, d = self._pide("POST", "/clave", {"proveedor": "OpenCode Go", "borrar": True})
        self.assertEqual((st, self.guardadas), (200, {}))

    def test_clave_de_panel_por_cuenta(self):
        st, d = self._pide("POST", "/clave", {"cuenta": "dokploy:d.example.com", "clave": "abcdefgh12345678"})
        self.assertEqual(st, 200)
        self.assertIn("dokploy:d.example.com", self.guardadas)

    def test_clave_de_cuenta_desconocida_se_rechaza(self):
        for cuerpo in ({"proveedor": "Inventado", "clave": "abcdefgh12345678"}, {"cuenta": "dokploy:evil.com", "clave": "abcdefgh12345678"}):
            st, d = self._pide("POST", "/clave", cuerpo)
            self.assertEqual((st, d["ok"]), (400, False))
        self.assertEqual(self.guardadas, {})

    def test_clave_exige_origen_del_panel(self):
        st, _ = self._pide("POST", "/clave", {"proveedor": "NaN", "clave": "abcdefgh12345678"}, origen=False)
        self.assertEqual(st, 403)
        self.assertEqual(self.guardadas, {})

    def test_guardar_ajustes_conserva_el_resto_de_config(self):
        st, d = self._pide("GET", "/ajustes")
        c = d["config"]
        c["aliases"] = {"nan.builders": "NaN"}
        c["proyectos"] = {"a": {"alias": "Uno", "oculto": False, "presupuesto_mes_tokens": 5}}
        st, d = self._pide("POST", "/guardar", c)
        self.assertEqual((st, d.get("ok")), (200, True), d)
        with open(rutas.CONFIG, encoding="utf-8") as f:
            en_disco = json.load(f)
        self.assertEqual(en_disco["aliases"], {"nan.builders": "NaN"})
        self.assertEqual(en_disco["servidores"], self.CONFIG["servidores"])
        self.assertNotIn("limite_5h_tokens", en_disco)          # el valor por defecto de cfg() no se cuela en el fichero

    def test_guardar_rechaza_servidor_invalido(self):
        st, d = self._pide("GET", "/ajustes")
        c = d["config"]
        c["servidores"] = [{"nombre": "x", "ssh": "-oProxyCommand=evil"}]
        st, d = self._pide("POST", "/guardar", c)
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()
