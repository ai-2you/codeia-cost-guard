"""Ciclo de servidores + historial: el registrador va fuera del cerrojo, un fallo suyo no para el ciclo, ids estables y políticas por servidor."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import ajustes, config_y_estado, rutas, servidores, servidores_ciclo, servidores_historico as sh, servidores_registro as reg  # noqa: E402
from infobar.servidor_local import errores_config  # noqa: E402

ID_A, ID_B = "a" * 32, "b" * 32
T0 = 1_790_000_000
BUENO = {"error": "", "estado": "verde", "estado_vivo": "verde", "host": "h", "tarjetas": {}, "webs": [], "expuestos": None,
         "datos": {"cpu": {"pct": 5.0, "carga": 0.2, "nucleos": 4}, "intentos": {"total": 10, "hora": 2, "top": [["8.8.8.8", 4]]}}}


def limpia_config(servidores_cfg):
    for r in (rutas.SERVIDORES, rutas.UI_ESTADO, rutas.CONFIG, rutas.CONFIG_SERVIDORES):
        if os.path.exists(r):
            os.remove(r)
    config_y_estado._ULTIMO_CFG = {}
    config_y_estado.guardar_config({"servidores": servidores_cfg})


def tearDownModule():
    for r in (rutas.SERVIDORES, rutas.UI_ESTADO, rutas.CONFIG, rutas.CONFIG_SERVIDORES):      # no dejar servidores a otros módulos de tests
        if os.path.exists(r):
            os.remove(r)
    config_y_estado._ULTIMO_CFG = {}


class IdsTest(unittest.TestCase):
    def test_asegurar_ids(self):
        lista, cambio = reg.asegurar_ids([{"nombre": "a", "id": ID_A}, {"nombre": "b"}, {"nombre": "c", "id": ID_A}, {"nombre": "d", "id": "mal"}])
        self.assertTrue(cambio)
        ids = [s["id"] for s in lista]
        self.assertEqual(ids[0], ID_A)
        self.assertEqual(len(set(ids)), 4)
        self.assertTrue(all(reg.ID_SERVIDOR.fullmatch(i) for i in ids))
        lista2, cambio2 = reg.asegurar_ids(lista, lista)
        self.assertFalse(cambio2)
        self.assertEqual(lista2, lista)

    def test_hereda_el_id_por_nombre_si_la_interfaz_lo_perdio_y_uno_nuevo_al_reanadir(self):
        previos = [{"nombre": "a", "id": ID_A}]
        self.assertEqual(reg.asegurar_ids([{"nombre": "a"}], previos)[0][0]["id"], ID_A)
        self.assertNotEqual(reg.asegurar_ids([{"nombre": "z"}], previos)[0][0]["id"], ID_A)

    def test_normalizar_pone_ids_y_los_conserva_al_renombrar(self):
        base = {"proveedores": [], "aliases": {}}
        out = ajustes.normalizar(dict(base, servidores=[{"nombre": "x", "ssh": "u@h.example.com"}]), {}, [{"nombre": "x", "id": ID_B}])
        self.assertEqual(out["servidores"][0]["id"], ID_B)
        out = ajustes.normalizar(dict(base, servidores=[{"nombre": "renombrado", "ssh": "u@h.example.com", "id": ID_B}]), {}, [{"nombre": "x", "id": ID_B}])
        self.assertEqual(out["servidores"][0]["id"], ID_B)

    def test_validacion_de_id_y_politicas(self):
        base = {"nombre": "a", "ssh": "u@h.example.com"}
        self.assertEqual(servidores.errores_servidores([dict(base, id=ID_A, guardar_ips=False, retencion_ips_dias=30)]), [])
        for malo in ({"id": "xyz"}, {"id": 5}, {"guardar_ips": "no"}, {"retencion_ips_dias": 3}, {"retencion_ips_dias": 91}, {"retencion_ips_dias": True}):
            self.assertTrue(servidores.errores_servidores([dict(base, **malo)]), malo)
        cfg_ok = {"proveedores": [], "aliases": {}}
        self.assertEqual(errores_config(dict(cfg_ok, guardar_ips=True, retencion_ips_dias=60)), [])
        self.assertTrue(errores_config(dict(cfg_ok, retencion_ips_dias=500)))
        self.assertTrue(errores_config(dict(cfg_ok, guardar_ips=1)))

    def test_politicas_override_por_servidor(self):
        conf = {"guardar_ips": True, "retencion_ips_dias": 60,
                "servidores": [{"nombre": "a", "id": ID_A}, {"nombre": "b", "id": ID_B, "guardar_ips": False, "retencion_ips_dias": 14}, {"nombre": "c"}]}
        self.assertEqual(reg.politicas(conf), {ID_A: {"guardar_ips": True, "retencion_dias": 60}, ID_B: {"guardar_ips": False, "retencion_dias": 14}})
        self.assertEqual(reg.politicas({"servidores": [{"nombre": "a", "id": ID_A}]})[ID_A], {"guardar_ips": True, "retencion_dias": 90})


class CicloHistorialTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-int-")
        for p in (mock.patch.object(rutas, "DATOS", self.dir),):
            p.start()
            self.addCleanup(p.stop)
        limpia_config([{"nombre": "a", "ssh": "infobar@a.example.com", "id": ID_A}])

    def test_el_registrador_corre_con_la_cache_ya_guardada_y_registra_solo_lo_nuevo(self):
        vistos = []

        def historial(srvs, salida, nuevos, ahora):
            vistos.append((set(nuevos), servidores_ciclo.leer_cache()["servidores"]["a"].get("ok_en"), salida["a"]["ok_en"]))
        with mock.patch.object(servidores_ciclo, "recoger_uno", return_value=BUENO):
            servidores_ciclo.ciclo(forzar=True, historial=historial)
            servidores_ciclo.ciclo(historial=historial)                 # nada que recoger: no se llama
        self.assertEqual(len(vistos), 1)
        self.assertEqual(vistos[0][0], {"a"})
        self.assertEqual(vistos[0][1], vistos[0][2])                    # la caché en disco ya estaba escrita

    def test_un_fallo_del_historial_no_para_el_ciclo(self):
        def roto(*a):
            raise RuntimeError("SECRETO 8.8.8.8")
        with mock.patch.object(servidores_ciclo, "recoger_uno", return_value=BUENO), mock.patch.object(servidores_ciclo, "log") as lg:
            c = servidores_ciclo.ciclo(forzar=True, historial=roto)
        self.assertEqual(c["servidores"]["a"]["estado"], "verde")
        self.assertTrue(all("SECRETO" not in str(x) for x in lg.call_args_list))

    def test_sin_historial_no_se_crea_la_base(self):
        with mock.patch.object(servidores_ciclo, "recoger_uno", return_value=BUENO):
            servidores_ciclo.ciclo(forzar=True)
        self.assertFalse(os.path.exists(sh.ruta()))

    def test_extremo_a_extremo_con_el_registrador_real(self):
        h = reg.Historial()
        with mock.patch.object(servidores_ciclo, "recoger_uno", return_value=BUENO):
            servidores_ciclo.ciclo(forzar=True, historial=h)
        con = sh.abrir(solo_lectura=True)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM muestras").fetchone()[0], 1)
        self.assertEqual([r[0] for r in con.execute("SELECT ip FROM ips_atacantes")], ["8.8.8.8"])
        self.assertEqual(con.execute("SELECT nombre FROM servidores").fetchone()[0], "a")
        con.close()

    def test_servidor_sin_id_no_se_registra_y_preparar_se_lo_da(self):
        limpia_config([{"nombre": "a", "ssh": "infobar@a.example.com"}])
        h = reg.Historial()
        with mock.patch.object(servidores_ciclo, "recoger_uno", return_value=BUENO):
            servidores_ciclo.ciclo(forzar=True, historial=h)
        if os.path.exists(sh.ruta()):
            con = sh.abrir(solo_lectura=True)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM muestras").fetchone()[0], 0)
            con.close()
        h.preparar()
        self.assertRegex(config_y_estado.cfg()["servidores"][0]["id"], r"^[0-9a-f]{32}$")
        antes = config_y_estado.cfg()["servidores"][0]["id"]
        h.preparar()
        self.assertEqual(config_y_estado.cfg()["servidores"][0]["id"], antes)       # idempotente


class RetrocesoTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-int-")
        p = mock.patch.object(rutas, "DATOS", self.dir)
        p.start()
        self.addCleanup(p.stop)
        limpia_config([{"nombre": "a", "ssh": "infobar@a.example.com", "id": ID_A}])

    def test_base_rota_un_solo_aviso_y_espera_una_hora(self):
        ahora = [1000.0]
        h = reg.Historial(reloj=lambda: ahora[0])
        entrada = dict(BUENO, ok_en=T0, intento_en=T0)
        with mock.patch.object(sh, "abrir", side_effect=sh.ErrorAlmacen("ruta 8.8.8.8")) as ab, mock.patch.object(reg, "log") as lg:
            for _ in range(5):
                h([{"nombre": "a", "id": ID_A}], {"a": entrada}, {"a"}, T0)
            self.assertEqual(ab.call_count, 1)
            self.assertEqual(lg.call_count, 1)
            self.assertNotIn("8.8.8.8", str(lg.call_args))
            ahora[0] += 3601
            h([{"nombre": "a", "id": ID_A}], {"a": entrada}, {"a"}, T0)
            self.assertEqual(ab.call_count, 2)

    def test_mantenimiento_al_primer_ciclo_y_cada_hora(self):
        ahora = [10 ** 9 + 5000.0]
        h = reg.Historial(reloj=lambda: ahora[0])
        entrada = dict(BUENO, ok_en=T0, intento_en=T0)
        with mock.patch.object(sh, "mantener", wraps=sh.mantener) as m:
            h([{"nombre": "a", "id": ID_A}], {"a": entrada}, {"a"}, T0)
            h([{"nombre": "a", "id": ID_A}], {"a": dict(entrada, ok_en=T0 + 400)}, {"a"}, T0 + 400)
            self.assertEqual(m.call_count, 1)
            ahora[0] += 3601
            h([{"nombre": "a", "id": ID_A}], {"a": dict(entrada, ok_en=T0 + 800)}, {"a"}, T0 + 800)
            self.assertEqual(m.call_count, 2)


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-api-")
        p = mock.patch.object(rutas, "DATOS", self.dir)
        p.start()
        self.addCleanup(p.stop)
        limpia_config([{"nombre": "a", "ssh": "infobar@a.example.com", "id": ID_A, "retencion_ips_dias": 30}])

    def con_datos(self):
        con = sh.abrir()
        e = dict(BUENO, ok_en=T0, intento_en=T0)
        sh.registrar(con, {"id": ID_A, "nombre": "a"}, e, T0)
        con.close()

    def auditoria(self):
        p = os.path.join(self.dir, "servidores_ips.log")
        if not os.path.exists(p):
            return ""
        with open(p) as f:
            return f.read()

    def test_sin_base_dice_que_no_hay_datos_y_no_la_crea(self):
        from infobar import servidores_api as api
        c, d = api.atiende("/servidores/historia", {})
        self.assertEqual((c, d["disponible"]), (200, False))
        self.assertFalse(os.path.exists(sh.ruta()))

    def test_lista_historia_e_ips_con_auditoria_de_recuento(self):
        from infobar import servidores_api as api
        self.con_datos()
        c, d = api.atiende("/servidores/historia", {})
        self.assertEqual([x["id"] for x in d["servidores"]], [ID_A])
        self.assertNotIn("8.8.8.8", json_(d))
        c, d = api.atiende("/servidores/ips", {"servidor": [ID_A], "con_ips": ["1"]}, ahora=T0 + 100)
        self.assertEqual((c, [x["ip"] for x in d["ips"]], d["retencion_dias"]), (200, ["8.8.8.8"], 30))
        self.assertEqual(d["ips"][0]["caduca_en"], T0 + 30 * 86400)
        self.assertIn('"ips": 1', self.auditoria())
        self.assertNotIn("8.8.8.8", self.auditoria())
        c, d = api.atiende("/servidores/ips", {"servidor": [ID_A], "con_ips": ["1"]}, ahora=T0 + 31 * 86400)      # fuera de plazo
        self.assertEqual(d["ips"], [])

    def test_parametros_estrictos(self):
        from infobar import servidores_api as api
        self.con_datos()
        for ruta, q in (("/servidores/ips", {}), ("/servidores/ips", {"servidor": [ID_A]}), ("/servidores/ips", {"con_ips": ["1"]}),
                        ("/servidores/ips", {"servidor": [ID_A], "con_ips": ["0"]}), ("/servidores/ips", {"servidor": [ID_A, ID_B], "con_ips": ["1"]}),
                        ("/servidores/ips", {"servidor": [ID_A], "con_ips": ["1"], "x": ["1"]}), ("/servidores/historia", {"otro": ["1"]}),
                        ("/servidores/historia", {"servidor": [ID_A], "desde": ["2026-01-01"], "hasta": ["2026-01-02"], "paso": ["mes"]}),
                        ("/servidores/otra", {})):
            self.assertEqual(api.atiende(ruta, q)[0], 400, (ruta, q))
        self.assertEqual(api.atiende("/servidores/ips", {"servidor": ["zzzz"], "con_ips": ["1"]}, ahora=T0)[1]["ips"], [])
        self.assertEqual(self.auditoria().count("\n"), 1)

    def test_olvidar_por_nombre_y_todos_con_auditoria(self):
        from infobar import servidores_api as api
        self.assertEqual(api.olvidar()[0], 0)                       # sin base: nada que borrar
        self.con_datos()
        self.assertEqual(api.olvidar("no existe")[0], 1)
        c, msg = api.olvidar("a")
        self.assertEqual((c, "borradas: 1" in msg), (0, True))
        con = sh.abrir(solo_lectura=True)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM ips_atacantes").fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM horas_servidor").fetchone()[0], 1)      # el agregado se conserva
        con.close()
        self.assertIn('"origen": "orden"', self.auditoria())

    def test_orden_sin_valor_de_servidor_no_borra_todo(self):
        import subprocess
        self.con_datos()
        r = subprocess.run([sys.executable, os.path.join(rutas.RAIZ, "costbar.py"), "--olvidar-ips", "--servidor"], capture_output=True, timeout=60,
                           env=dict(os.environ, COSTGUARD_DATOS=self.dir, HOME=self.dir))
        self.assertEqual(r.returncode, 2)
        con = sh.abrir(solo_lectura=True)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM ips_atacantes").fetchone()[0], 1)
        con.close()


class RutasHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from infobar import servidor_local
        cls.dir = tempfile.mkdtemp(prefix="cg-http-")
        cls.p = mock.patch.object(rutas, "DATOS", cls.dir)
        cls.p.start()
        cls.srv = servidor_local.crear_servidor(0)
        cls.puerto = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.cookie = aislamiento.cookie_de(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.p.stop()

    def pide(self, ruta, cookie=True, host=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.puerto}"}
        if cookie:
            h["Cookie"] = self.cookie
        c.request("GET", ruta, headers=h)
        r = c.getresponse()
        cuerpo = r.read()
        c.close()
        return r.status, cuerpo

    def test_sesion_host_y_parametros(self):
        self.assertEqual(self.pide("/servidores/historia", cookie=False)[0], 401)
        self.assertEqual(self.pide("/servidores/ips?servidor=x&con_ips=1", cookie=False)[0], 401)
        self.assertEqual(self.pide("/servidores/historia", host="evil.example.com")[0], 403)
        self.assertEqual(self.pide("/servidores/ips")[0], 400)
        self.assertEqual(self.pide("/servidores/ips?servidor=a&servidor=b&con_ips=1")[0], 400)
        st, cuerpo = self.pide("/servidores/historia")
        self.assertEqual(st, 200)
        self.assertIn(b'"disponible": false', cuerpo)


def json_(d):
    import json
    return json.dumps(d)


if __name__ == "__main__":
    unittest.main()
