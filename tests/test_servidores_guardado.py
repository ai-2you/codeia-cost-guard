"""Guardado seguro de la lista de servidores desde Ajustes: revisión (no se pisa un alta hecha entre medias), campos cerrados (una contraseña no
puede llegar a servidores.json) y secretos."""
import hashlib
import http.client
import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import config_y_estado, rutas, servidor_local  # noqa: E402
from infobar.servidores import errores_servidores, revision_servidores  # noqa: E402
from test_config_y_servidor_local import CONFIG_BASE, LimpiaConfig, _lee_config  # noqa: E402


def huella(ruta):
    with open(ruta, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class GuardadoTest(LimpiaConfig):
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
        for ruta in (rutas.CONFIG_SERVIDORES, rutas.CONFIG_SERVIDORES + ".lock"):
            if os.path.exists(ruta):
                os.remove(ruta)                                                # cada prueba parte de los servidores de CONFIG_BASE
        super().setUp()

    def pide(self, metodo, ruta, cuerpo=None):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        h = {"Host": f"127.0.0.1:{self.puerto}", "Cookie": self.cookie, "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.puerto}"}
        c.request(metodo, ruta, body=json.dumps(cuerpo).encode() if cuerpo is not None else None, headers=h)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def lee_config_pagina(self):
        return self.pide("GET", "/ajustes")[1]["config"]

    def test_la_pagina_recibe_la_revision_y_el_guardado_devuelve_la_nueva(self):
        cfg = self.lee_config_pagina()
        self.assertEqual(cfg["servidores_rev"], revision_servidores(cfg["servidores"]))
        cfg["servidores"][0]["webs"] = ["https://a.example.com"]
        estado, r = self.pide("POST", "/guardar", cfg)
        self.assertEqual(estado, 200)
        self.assertEqual(r["servidores_rev"], revision_servidores(self.lee_config_pagina()["servidores"]))
        self.assertNotEqual(r["servidores_rev"], cfg["servidores_rev"])
        self.assertNotIn("servidores_rev", _lee_config())                       # la revisión es de la conversación, no se guarda

    def test_un_alta_entre_la_lectura_y_el_guardado_no_se_pisa(self):
        cfg = self.lee_config_pagina()                                         # la página lee la lista...
        config_y_estado.guardar_config({"servidores": CONFIG_BASE["servidores"] + [{"nombre": "nuevo", "ssh": "infobar@nuevo", "id": "a" * 32}]})   # ...el alta añade uno...
        antes = huella(rutas.CONFIG_SERVIDORES)
        cfg["aliases"]["x"] = "y"
        estado, r = self.pide("POST", "/guardar", cfg)                         # ...y la página guarda su copia vieja
        self.assertEqual(estado, 409)
        self.assertIn("recarga", r["error"])
        self.assertEqual(huella(rutas.CONFIG_SERVIDORES), antes)               # nada se escribió
        self.assertEqual([s["nombre"] for s in config_y_estado.cfg()["servidores"]], ["produccion", "nuevo"])
        self.assertNotIn("x", _lee_config()["aliases"])                         # tampoco el resto de la configuración

    def test_sin_revision_o_con_otra_no_se_guardan_servidores(self):
        cfg = self.lee_config_pagina()
        for rev in (None, "", "0" * 24, 5):
            malo = dict(cfg, servidores_rev=rev)
            if rev is None:
                malo.pop("servidores_rev")
            self.assertEqual(self.pide("POST", "/guardar", malo)[0], 409, repr(rev))
        self.assertEqual(config_y_estado.cfg()["servidores"], CONFIG_BASE["servidores"])

    def test_guardar_sin_tocar_servidores_no_necesita_revision(self):
        self.assertEqual(self.pide("POST", "/guardar", {"proveedores": [], "aliases": {"a": "b"}})[0], 200)
        self.assertEqual(_lee_config()["aliases"], {"a": "b"})

    def test_solo_gana_uno_de_dos_guardados_con_la_misma_revision(self):
        cfg = self.lee_config_pagina()
        resultados = []

        def guarda(nombre):
            c = json.loads(json.dumps(cfg))
            c["servidores"][0]["webs"] = [f"https://{nombre}.example.com"]
            resultados.append(self.pide("POST", "/guardar", c)[0])
        hilos = [threading.Thread(target=guarda, args=(n,)) for n in ("uno", "dos")]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        self.assertEqual(sorted(resultados), [200, 409])

    def test_una_contrasena_no_llega_al_disco(self):
        cfg = self.lee_config_pagina()
        antes = (huella(rutas.CONFIG), huella(rutas.CONFIG_SERVIDORES) if os.path.exists(rutas.CONFIG_SERVIDORES) else None)
        for campo in ("contrasena", "contraseña", "password", "passwd", "pass", "clave", "api_key", "secreto", "token"):
            malo = json.loads(json.dumps(cfg))
            malo["servidores"][0][campo] = "hunter2-no-debe-quedar"
            estado, r = self.pide("POST", "/guardar", malo)
            self.assertEqual(estado, 400, campo)
            self.assertNotIn("hunter2", json.dumps(r), campo)                  # ni siquiera en el error
        despues = (huella(rutas.CONFIG), huella(rutas.CONFIG_SERVIDORES) if os.path.exists(rutas.CONFIG_SERVIDORES) else None)
        self.assertEqual(antes, despues)
        for ruta in (rutas.CONFIG, rutas.CONFIG_SERVIDORES):
            if os.path.exists(ruta):
                with open(ruta, "rb") as f:
                    self.assertNotIn(b"hunter2", f.read())

    def test_contrasena_a_primer_nivel_tampoco_se_guarda(self):
        estado, _ = self.pide("POST", "/guardar", {"proveedores": [], "aliases": {}, "contrasena": "hunter2-no-debe-quedar"})
        self.assertEqual(estado, 200)                                          # se guarda lo demás, pero el secreto se filtra
        with open(rutas.CONFIG, "rb") as f:
            self.assertNotIn(b"hunter2", f.read())


class ModificarServidoresTest(LimpiaConfig):
    def setUp(self):
        for ruta in (rutas.CONFIG_SERVIDORES, rutas.CONFIG_SERVIDORES + ".lock"):
            if os.path.exists(ruta):
                os.remove(ruta)
        super().setUp()
        config_y_estado.cfg()                                                   # migra la lista a servidores.json

    def test_dos_escritores_no_pierden_cambios(self):
        def anade(prefijo):
            for i in range(25):
                config_y_estado.modificar_servidores(lambda l: l + [{"nombre": f"{prefijo}{i}", "ssh": f"infobar@{prefijo}{i}"}])
        hilos = [threading.Thread(target=anade, args=(p,)) for p in ("a", "b")]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        nombres = {s["nombre"] for s in config_y_estado.cfg()["servidores"]}
        self.assertEqual(len(nombres), 1 + 50)                                  # el heredado de config.json y los 50 añadidos, ninguno pisado

    def test_guardar_con_revision_no_pisa_un_alta_que_escribe_a_la_vez(self):
        base = config_y_estado.cfg()["servidores"]
        rev_vieja = revision_servidores(base)
        resultado = []

        def guarda_con_la_copia_vieja():
            resultado.append(config_y_estado.guardar_con_revision(
                {"servidores": base, "aliases": {"x": "y"}}, lambda actual: revision_servidores(actual) == rev_vieja))

        def alta(lista):
            hilo = threading.Thread(target=guarda_con_la_copia_vieja)
            hilo.start()                                                      # la página intenta guardar mientras el alta tiene la lista en las manos
            time.sleep(0.4)
            self.hilo = hilo
            return lista + [{"nombre": "recien-llegado", "ssh": "infobar@nuevo"}]
        config_y_estado.modificar_servidores(alta)
        self.hilo.join(10)
        self.assertEqual(resultado, [False])                                  # esperó el cerrojo, vio la lista nueva y NO escribió nada
        self.assertEqual([s["nombre"] for s in config_y_estado.cfg()["servidores"]], ["produccion", "recien-llegado"])
        self.assertNotIn("x", _lee_config().get("aliases", {}))                # ni el resto de la configuración

    def test_trabaja_sobre_la_lista_actual_y_no_escribe_si_no_cambia(self):
        self.assertEqual(config_y_estado.modificar_servidores(lambda l: l), CONFIG_BASE["servidores"])      # parte de la lista heredada de config.json
        self.assertTrue(os.path.exists(rutas.CONFIG_SERVIDORES))
        antes = os.stat(rutas.CONFIG_SERVIDORES).st_mtime_ns
        self.assertEqual(config_y_estado.modificar_servidores(lambda l: l), CONFIG_BASE["servidores"])
        self.assertEqual(os.stat(rutas.CONFIG_SERVIDORES).st_mtime_ns, antes)                              # sin cambios no se reescribe

    def test_preparar_da_id_sobre_la_lista_actual(self):
        from infobar import servidores_registro
        config_y_estado.guardar_config({"servidores": [{"nombre": "uno", "ssh": "infobar@h1"}, {"nombre": "dos", "ssh": "infobar@h2"}]})
        servidores_registro.Historial().preparar()
        lista = config_y_estado.cfg()["servidores"]
        self.assertEqual([s["nombre"] for s in lista], ["uno", "dos"])
        self.assertTrue(all(len(s["id"]) == 32 for s in lista))
        ids = [s["id"] for s in lista]
        servidores_registro.Historial().preparar()
        self.assertEqual([s["id"] for s in config_y_estado.cfg()["servidores"]], ids)     # idempotente

    def test_un_servidor_invalido_no_para_a_los_demas(self):
        from infobar.servidores import servidores_validos
        lista = [{"nombre": "ok", "ssh": "infobar@h"}, {"nombre": "campo", "ssh": "infobar@h", "contrasena": "x"}, {"nombre": "ok", "ssh": "infobar@otro"},
                 {"nombre": "mal ssh", "ssh": "-oProxyCommand=x"}, "raro", {"nombre": "web", "ssh": "infobar@w", "webs": ["https://a.example.com", "http://127.0.0.1/"]}]
        v = servidores_validos(lista)
        self.assertEqual([s["nombre"] for s in v], ["ok", "web"])
        self.assertEqual(v[1]["webs"], ["https://a.example.com"])
        self.assertEqual(servidores_validos(None), [])
        self.assertEqual(servidores_validos("x"), [])


class CamposTest(unittest.TestCase):
    def test_campos_conocidos_validos(self):
        s = {"nombre": "a", "ssh": "infobar@h", "ssh_admin": "root@h", "puerto": 22, "id": "a" * 32, "webs": ["https://x.example.com"], "panel": "dokploy",
             "panel_url": "https://panel.example.com", "guardar_ips": False, "retencion_ips_dias": 30, "detectar_webs": False}
        self.assertEqual(errores_servidores([s]), [])

    def test_campo_desconocido_se_rechaza_con_su_nombre_y_sin_su_valor(self):
        e = errores_servidores([{"nombre": "a", "ssh": "infobar@h", "contrasena": "hunter2"}])
        self.assertEqual(len(e), 1)
        self.assertIn("contrasena", e[0])
        self.assertNotIn("hunter2", e[0])

    def test_detectar_webs_tiene_que_ser_booleano(self):
        for malo in ("si", 1, None, [True]):
            self.assertTrue(errores_servidores([{"nombre": "a", "ssh": "infobar@h", "detectar_webs": malo}]), repr(malo))

    def test_nombres_secretos(self):
        for n in ("contrasena", "contraseña", "passwd", "pass", "Password", "mi_password"):
            self.assertTrue(servidor_local.es_secreto(n), n)
        for n in ("passes", "compass", "limite_5h_tokens"):
            self.assertFalse(servidor_local.es_secreto(n), n)


class RevisionTest(unittest.TestCase):
    def test_estable_y_sensible_a_cualquier_cambio(self):
        a = [{"nombre": "a", "ssh": "u@h", "webs": ["https://x.example.com"]}]
        self.assertEqual(revision_servidores(a), revision_servidores(json.loads(json.dumps(a))))
        self.assertEqual(revision_servidores([{"b": 1, "a": 2}]), revision_servidores([{"a": 2, "b": 1}]))        # el orden de claves no cuenta
        self.assertNotEqual(revision_servidores(a), revision_servidores(a + [{"nombre": "b", "ssh": "u@h2"}]))
        self.assertNotEqual(revision_servidores(a), revision_servidores([dict(a[0], webs=[])]))
        self.assertEqual(revision_servidores(None), revision_servidores([]))


def tearDownModule():
    for ruta in (rutas.CONFIG_SERVIDORES, rutas.CONFIG_SERVIDORES + ".lock"):
        if os.path.exists(ruta):
            os.remove(ruta)


if __name__ == "__main__":
    unittest.main()
