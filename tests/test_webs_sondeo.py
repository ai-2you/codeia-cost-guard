"""Sondeo de webs aparte del ciclo SSH: calendario, presupuesto, canario de red, aceleración acotada, forma de lo medido, caché y estados."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import rutas, servidores_ciclo as ciclo, webs_sondeo as ws  # noqa: E402
from infobar.servidores import ip_no_publica, revision_servidores  # noqa: E402

T0 = 1_790_000_000


def srv(*urls, nombre="a"):
    return {"nombre": nombre, "ssh": "infobar@h", "webs": list(urls)}


def bien(u, ms=100):
    return {"url": u, "ms": ms, "codigo": 200, "error": "", "cert_dias": 40}


def cae(u, codigo=None, error="timed out"):
    return {"url": u, "ms": None, "codigo": codigo, "error": error, "cert_dias": None}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-webs-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        p = mock.patch.object(ws, "ruta", return_value=os.path.join(self.dir, "webs.json"))
        p.start()
        self.addCleanup(p.stop)

    def pasada(self, servidores, ahora, resultados=None, red=lambda: False, **kw):
        resultados = resultados or {}
        medir = lambda u: resultados.get(u, bien(u))  # noqa: E731
        return ws.pasada(servidores, ahora, medir=medir, red=red, **kw)


class CalendarioTest(Base):
    def test_primera_pasada_mide_todo_y_la_siguiente_espera(self):
        s = [srv("https://a.example.com", "https://b.example.com")]
        medidas = []
        r = ws.pasada(s, T0, medir=lambda u: medidas.append(u) or bien(u), red=lambda: False)
        self.assertTrue(r[0])
        self.assertEqual(sorted(medidas), ["https://a.example.com", "https://b.example.com"])
        medidas.clear()
        r = ws.pasada(s, T0 + 100, medir=lambda u: medidas.append(u) or bien(u), red=lambda: False)
        self.assertEqual((r[0], medidas), (False, []))
        ws.pasada(s, T0 + 301, medir=lambda u: medidas.append(u) or bien(u), red=lambda: False)
        self.assertEqual(len(medidas), 2)

    def test_un_fallo_se_vuelve_a_mirar_a_los_60_s_y_un_acierto_a_los_300(self):
        s = [srv("https://a.example.com")]
        self.pasada(s, T0, {"https://a.example.com": cae("https://a.example.com")})
        e = ws.leer()["webs"]["https://a.example.com"]
        self.assertEqual((e["fallos"], e["proximo_en"]), (1, T0 + 60))
        self.pasada(s, T0 + 61, {"https://a.example.com": cae("https://a.example.com", 503, "")})
        self.assertEqual(ws.leer()["webs"]["https://a.example.com"]["fallos"], 2)
        self.pasada(s, T0 + 122)
        e = ws.leer()["webs"]["https://a.example.com"]
        self.assertEqual((e["fallos"], e["proximo_en"], e["ok_en"]), (0, T0 + 122 + 300, T0 + 122))

    def test_un_4xx_responde_y_no_acelera(self):
        s = [srv("https://a.example.com")]
        self.pasada(s, T0, {"https://a.example.com": cae("https://a.example.com", 404, "")})
        e = ws.leer()["webs"]["https://a.example.com"]
        self.assertEqual((e["fallos"], e["proximo_en"]), (0, T0 + 300))

    def test_solo_se_aceleran_veinte_webs_a_la_vez(self):
        urls = [f"https://w{i:02d}.example.com" for i in range(30)]
        s = [srv(*urls)]
        self.pasada(s, T0, {u: cae(u) for u in urls})
        medidas = []
        ws.pasada(s, T0 + 61, medir=lambda u: medidas.append(u) or cae(u), red=lambda: False)
        self.assertEqual(len(medidas), ws.MAX_ACELERADAS)                       # las otras 10 esperan a su turno y no se pierden
        ws.pasada(s, T0 + 122, medir=lambda u: medidas.append(u) or cae(u), red=lambda: False)
        self.assertGreater(len(set(medidas)), ws.MAX_ACELERADAS)

    def test_lo_que_no_cabe_en_el_presupuesto_conserva_su_turno(self):
        urls = ["https://lenta.example.com", "https://rapida.example.com"]

        def medir(u):
            if "lenta" in u:
                time.sleep(2)
            return bien(u)
        with mock.patch.object(ws, "PRESUPUESTO_S", 0.3):
            r = ws.pasada([srv(*urls)], T0, medir=medir, red=lambda: False)
        webs = ws.leer()["webs"]
        self.assertIn("https://rapida.example.com", webs)
        self.assertNotIn("https://lenta.example.com", webs)                     # sin resultado no hay entrada: vuelve a tocar en la próxima pasada

    def test_una_pasada_a_la_vez(self):
        entra, sigue = threading.Event(), threading.Event()

        def medir(u):
            entra.set()
            sigue.wait(5)
            return bien(u)
        hilo = threading.Thread(target=lambda: ws.pasada([srv("https://a.example.com")], T0, medir=medir, red=lambda: False))
        hilo.start()
        entra.wait(5)
        self.assertIsNone(ws.pasada([srv("https://a.example.com")], T0, medir=medir, red=lambda: False))
        sigue.set()
        hilo.join()


class CanarioTest(Base):
    URLS = [f"https://w{i}.example.com" for i in range(4)]

    def test_sin_red_no_cambia_nada(self):
        s = [srv(*self.URLS)]
        self.pasada(s, T0)
        antes = json.dumps(ws.leer(), sort_keys=True)
        r = self.pasada(s, T0 + 301, {u: cae(u) for u in self.URLS}, red=lambda: True)
        self.assertFalse(r[0])
        self.assertEqual(json.dumps(ws.leer(), sort_keys=True), antes)          # ni un fallo anotado, ni el calendario movido

    def test_con_red_las_caidas_son_caidas(self):
        s = [srv(*self.URLS)]
        self.pasada(s, T0)
        self.pasada(s, T0 + 301, {u: cae(u) for u in self.URLS}, red=lambda: False)
        self.assertTrue(all(e["fallos"] == 1 for e in ws.leer()["webs"].values()))

    def test_si_alguna_responde_no_se_consulta_el_canario_ni_se_descarta(self):
        s = [srv(*self.URLS)]
        llamadas = []
        self.pasada(s, T0)
        self.pasada(s, T0 + 301, {self.URLS[0]: cae(self.URLS[0])}, red=lambda: llamadas.append(1) or True)
        self.assertEqual(llamadas, [])
        self.assertEqual(ws.leer()["webs"][self.URLS[0]]["fallos"], 1)

    def test_con_menos_de_tres_sondeos_no_hay_canario(self):
        s = [srv("https://a.example.com", "https://b.example.com")]
        self.pasada(s, T0)
        self.pasada(s, T0 + 301, {u: cae(u) for u in ("https://a.example.com", "https://b.example.com")}, red=lambda: True)
        self.assertEqual(ws.leer()["webs"]["https://a.example.com"]["fallos"], 1)

    def test_sin_red_solo_si_ningun_canario_responde(self):
        ok = mock.Mock()
        intentos = []

        def conectar(dir_, tope):
            intentos.append(dir_)
            if dir_[0] == "8.8.8.8":
                return ok
            raise OSError("sin ruta")
        self.assertFalse(ws.sin_red(conectar=conectar))
        self.assertEqual([i[0] for i in intentos], ["1.1.1.1", "8.8.8.8"])      # se para en el primero que responde
        ok.close.assert_called_once()
        self.assertTrue(ws.sin_red(conectar=mock.Mock(side_effect=OSError("x"))))


class ConfiguracionTest(Base):
    def test_urls_validas_sin_repetir_y_con_tope(self):
        malas = ["http://127.0.0.1/", "ftp://a.example.com", "https://usuario:clave@a.example.com/", "no es url", "https://localhost/", "https://" + "a" * 300 + ".com"]
        s = [srv("https://ok.example.com", "https://ok.example.com", *malas), srv(*[f"https://n{i}.example.com" for i in range(150)], nombre="b")]
        out = ws.urls_de(s)
        self.assertEqual([u for n, u in out if n == "a"], ["https://ok.example.com"])
        self.assertEqual(len([1 for n, _ in out if n == "b"]), ws.MAX_WEBS_POR_SERVIDOR)
        self.assertEqual(ws.urls_de(None), [])
        self.assertEqual(ws.urls_de([{"nombre": "x", "webs": "https://a.example.com"}, "raro"]), [])

    def test_lo_que_se_quita_de_la_configuracion_se_quita_del_estado(self):
        self.pasada([srv("https://a.example.com", "https://b.example.com")], T0)
        self.pasada([srv("https://a.example.com")], T0 + 10)
        self.assertEqual(list(ws.leer()["webs"]), ["https://a.example.com"])

    def test_medidas_tiene_la_forma_de_siempre_y_omite_lo_no_medido(self):
        s = srv("https://a.example.com", "https://sin-medir.example.com")
        self.pasada([srv("https://a.example.com")], T0)
        m = ws.medidas(s)
        self.assertEqual(m, [{"url": "https://a.example.com", "ms": 100, "codigo": 200, "error": "", "cert_dias": 40}])
        self.assertEqual(ws.medidas({"webs": None}), [])
        self.assertEqual(ws.medidas({"webs": [5, None]}), [])

    def test_un_fichero_estropeado_no_rompe(self):
        with open(ws.ruta(), "w") as f:
            f.write("{no es json")
        self.assertEqual(ws.leer(), {"webs": {}})
        self.assertTrue(self.pasada([srv("https://a.example.com")], T0)[0])


class ReaplicarTest(Base):
    def setUp(self):
        super().setUp()
        self.cache = os.path.join(self.dir, "servidores.json")
        for p in (mock.patch.object(rutas, "SERVIDORES", self.cache),
                  mock.patch.object(ciclo, "cfg", return_value={"servidores": [srv("https://a.example.com")]})):
            p.start()
            self.addCleanup(p.stop)
        datos = {"cpu": {"pct": 1.0, "carga": 0.1, "nucleos": 1}, "memoria": {"total": 100, "usada": 10}, "disco": {"total": 100, "usado": 10},
                 "puertos": [22], "con_root": False}
        with open(self.cache, "w") as f:
            json.dump({"generado_en": T0, "servidores": {"a": {"error": "", "datos": datos, "webs": [], "expuestos": [], "bastionado_resumen": {"estado": "verde"},
                                                               "tarjetas": {}, "estado_vivo": "verde", "estado": "verde", "ok_en": T0}}}, f)

    def entrada(self):
        with open(self.cache) as f:
            return json.load(f)["servidores"]["a"]

    def test_una_web_caida_pone_rojo_el_servidor_pero_no_su_salud_viva(self):
        self.pasada([srv("https://a.example.com")], T0, {"https://a.example.com": cae("https://a.example.com", 503, "")})
        self.assertTrue(ciclo.reaplicar_webs())
        e = self.entrada()
        self.assertEqual(e["tarjetas"]["webs"]["estado"], "rojo")
        self.assertEqual((e["estado"], e["estado_vivo"]), ("rojo", "verde"))
        self.assertFalse(ciclo.en_incidencia("a", e, {}, T0))                   # y no acelera la lectura por SSH

    def test_sin_cambios_no_reescribe(self):
        self.pasada([srv("https://a.example.com")], T0)
        self.assertTrue(ciclo.reaplicar_webs())
        antes = os.stat(self.cache).st_mtime_ns
        self.assertFalse(ciclo.reaplicar_webs())
        self.assertEqual(os.stat(self.cache).st_mtime_ns, antes)

    def test_un_servidor_con_error_de_ssh_no_se_toca(self):
        with open(self.cache) as f:
            d = json.load(f)
        d["servidores"]["a"]["error"] = "timeout"
        with open(self.cache, "w") as f:
            json.dump(d, f)
        self.pasada([srv("https://a.example.com")], T0)
        self.assertFalse(ciclo.reaplicar_webs())
        self.assertEqual(self.entrada()["webs"], [])

    def test_guardar_parte_de_la_cache_fresca_y_no_de_la_del_principio(self):
        self.pasada([srv("https://a.example.com")], T0)
        vieja = ciclo.leer_cache()["servidores"]                                   # el ciclo la leyó al empezar...
        self.assertTrue(ciclo.reaplicar_webs())                                    # ...y mientras tanto el sondeador la puso al día
        ciclo._guardar([srv("https://a.example.com")], vieja, {}, T0 + 5)
        self.assertEqual([w["url"] for w in self.entrada()["webs"]], ["https://a.example.com"])


class IpsNoPublicasTest(unittest.TestCase):
    def test_nat64_site_local_y_srv6_no_son_publicas(self):
        for x in ("64:ff9b::808:808", "64:ff9b::7f00:1", "64:ff9b::a00:1", "fec0::1", "5f00::1", "::ffff:10.0.0.1", "10.0.0.1", "100.64.0.1", "::1", "fe80::1"):
            self.assertTrue(ip_no_publica(x), x)
        for x in ("8.8.8.8", "1.1.1.1", "2606:4700::1111", "::ffff:8.8.8.8", "2a00:1450:4001::68"):
            self.assertFalse(ip_no_publica(x), x)


if __name__ == "__main__":
    unittest.main()
