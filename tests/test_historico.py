"""Histórico: días, semanas (lunes) y meses locales también en el cambio de hora, mismas cifras que el informe, parámetros
estrictos y proyecto oculto/ignorado tratados igual que en el panel."""
import datetime
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from test_consulta import Base, turno, unittest_cfg  # noqa: E402
from infobar import consulta, historico  # noqa: E402


def utc(s):
    return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc).timestamp()


@unittest.skipUnless(hasattr(time, "tzset"), "necesita time.tzset (POSIX)")
class ConHusoMadrid(Base):
    def setUp(self):
        super().setUp()
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Madrid"
        time.tzset()
        self.addCleanup(self._restaura)
        # planes_y_tarifas lee los alias de la configuración global: se aísla para que otros tests no la contaminen
        patch = mock.patch("infobar.planes_y_tarifas.cfg", return_value={})
        patch.start()
        self.addCleanup(patch.stop)

    def _restaura(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def total_por_dia(self, desde, hasta):
        r = historico.historico(desde, hasta)
        return dict(zip(r["cubos"], r["totales"]))


class TestCubos(ConHusoMadrid):
    def test_el_dia_de_25_horas_reparte_bien(self):
        # 25-10-2026: a las 03:00 CEST se vuelve a las 02:00 CET. Cada instante cae en su día local.
        self.mete([turno("claude:a", utc("2026-10-24T22:30"), gi=1, go=0, cr=0, cw=0),      # 00:30 CEST del 25
                   turno("claude:b", utc("2026-10-25T22:30"), gi=10, go=0, cr=0, cw=0),     # 23:30 CET del 25
                   turno("claude:c", utc("2026-10-25T23:30"), gi=100, go=0, cr=0, cw=0),    # 00:30 CET del 26
                   turno("claude:d", utc("2026-10-24T21:30"), gi=1000, go=0, cr=0, cw=0)])  # 23:30 CEST del 24
        self.completa()
        self.assertEqual(self.total_por_dia("2026-10-24", "2026-10-26"), {"2026-10-24": 1000, "2026-10-25": 11, "2026-10-26": 100})

    def test_un_dia_suelto_incluye_su_primera_y_su_ultima_hora(self):
        # los límites del rango son la medianoche local, con el horario de verano que toque en cada extremo
        self.mete([turno("claude:a", utc("2026-10-24T22:30"), gi=1, go=0, cr=0, cw=0),      # 00:30 CEST del 25: primera hora del día
                   turno("claude:b", utc("2026-10-25T22:30"), gi=10, go=0, cr=0, cw=0),     # 23:30 CET del 25: última hora del día
                   turno("claude:c", utc("2026-10-24T21:59"), gi=100, go=0, cr=0, cw=0),    # 23:59 CEST del 24: no es del 25
                   turno("claude:d", utc("2026-10-25T23:00"), gi=1000, go=0, cr=0, cw=0)])  # 00:00 CET del 26: no es del 25
        self.completa()
        self.assertEqual(historico.historico("2026-10-25", "2026-10-25")["total"], 11)
        # y en verano, donde la medianoche es 22:00 UTC
        self.mete([turno("claude:e", utc("2026-07-14T22:30"), gi=5, go=0, cr=0, cw=0), turno("claude:f", utc("2026-07-14T21:30"), gi=50, go=0, cr=0, cw=0)])
        self.assertEqual(historico.historico("2026-07-15", "2026-07-15")["total"], 5)

    def test_el_dia_de_23_horas_de_primavera(self):
        # 28-03-2027: a las 02:00 CET se pasa a las 03:00 CEST.
        self.mete([turno("claude:a", utc("2027-03-27T23:30"), gi=1, go=0, cr=0, cw=0),      # 00:30 CET del 28
                   turno("claude:b", utc("2027-03-28T21:30"), gi=10, go=0, cr=0, cw=0),     # 23:30 CEST del 28
                   turno("claude:c", utc("2027-03-28T22:30"), gi=100, go=0, cr=0, cw=0)])   # 00:30 CEST del 29
        self.completa()
        self.assertEqual(self.total_por_dia("2027-03-28", "2027-03-29"), {"2027-03-28": 11, "2027-03-29": 100})

    def test_semana_de_lunes_y_mes(self):
        self.mete([turno("claude:a", utc("2026-10-11T10:00"), gi=1, go=0, cr=0, cw=0),      # domingo
                   turno("claude:b", utc("2026-10-12T10:00"), gi=10, go=0, cr=0, cw=0),     # lunes
                   turno("claude:c", utc("2026-10-18T10:00"), gi=100, go=0, cr=0, cw=0),    # domingo
                   turno("claude:d", utc("2026-11-01T10:00"), gi=1000, go=0, cr=0, cw=0)])
        self.completa()
        s = historico.historico("2026-10-11", "2026-10-18", paso="semana")
        self.assertEqual(s["cubos"], ["2026-10-05", "2026-10-12"])                       # el cubo se alinea al lunes
        self.assertEqual(s["totales"], [1, 110])
        m = historico.historico("2026-10-01", "2026-11-30", paso="mes")
        self.assertEqual((m["cubos"], m["totales"]), (["2026-10-01", "2026-11-01"], [111, 1000]))

    def test_cubos_anteriores_al_primer_dato_son_nulos_y_se_informa(self):
        self.mete([turno("claude:a", utc("2026-10-12T10:00"), gi=7, go=0, cr=0, cw=0)])
        self.completa()
        r = historico.historico("2026-10-10", "2026-10-13")
        self.assertEqual(r["datos_desde"], "2026-10-12")
        self.assertEqual(r["totales"], [None, None, 7, 0])
        self.assertEqual(r["series"][0]["tokens"][:2], [None, None])


class TestAgrupacion(ConHusoMadrid):
    def test_series_por_proyecto_plan_y_agente_con_ocultos_e_ignorados(self):
        t = utc("2026-10-12T10:00")
        self.mete([turno("claude:a", t, proyecto="u/x/clienteA", gi=1, go=0, cr=0, cw=0),
                   turno("claude:b", t, proyecto="u/x/secreto", gi=10, go=0, cr=0, cw=0),
                   turno("claude:c", t, proyecto="u/x/clienteA", modelo="<synthetic>", gi=500, go=0, cr=0, cw=0)])
        self.mete([turno("codex:a", t, agente="codex", proveedor="openai", modelo="gpt-5", proyecto="u/x/clienteA", gi=100, go=0, cr=0, cw=0)],
                  "codex", "f2")
        self.completa()
        conf = {"proyectos": {"u/x/clienteA": {"alias": "Cliente A"}, "u/x/secreto": {"oculto": True}}}
        with unittest_cfg(conf):
            def serie(agrupa):
                r = historico.historico("2026-10-12", "2026-10-12", agrupa=agrupa, conf=conf)
                return {s["nombre"]: s["tokens"][0] for s in r["series"]}, r["total"]
            por_proy, por_plan, por_agente = serie("proyecto")[0], serie("plan")[0], serie("agente")[0]
            total = serie("ninguno")[1]
        self.assertEqual(por_proy, {"Cliente A": 101, "(ocultos)": 10})
        self.assertEqual(por_plan, {"OpenAI (Codex)": 100, "Claude (Max)": 11})
        self.assertEqual(por_agente, {"Codex": 100, "Claude Code": 11})
        self.assertEqual(total, 111)                       # los ocultos cuentan en el total y el modelo ignorado no cuenta en ninguno

    def test_mas_series_que_el_maximo_se_agrupan_en_otros(self):
        t = utc("2026-10-12T10:00")
        with mock.patch.object(historico, "MAX_SERIES", 3):
            self.mete([turno(f"claude:{i}", t, proyecto=f"u/x/p{i}", gi=10 - i, go=0, cr=0, cw=0) for i in range(5)])
            self.completa()
            r = historico.historico("2026-10-12", "2026-10-12", agrupa="proyecto")
        self.assertEqual([s["nombre"] for s in r["series"]][-1], "(otros)")
        self.assertEqual(sum(s["tokens"][0] for s in r["series"]), r["total"])

    def test_nombre_hostil_sale_como_dato_sin_tocar(self):
        mal = "u/x/=HYPERLINK(\"http://e\")<img src=x>"
        self.mete([turno("claude:a", utc("2026-10-12T10:00"), proyecto=mal)])
        self.completa()
        nombres = [s["nombre"] for s in historico.historico("2026-10-12", "2026-10-12", agrupa="proyecto")["series"]]
        self.assertEqual(len(nombres), 1)
        self.assertIn("HYPERLINK", nombres[0])             # es un dato: quien lo pinte lo trata como texto


class TestCoincideConElInforme(ConHusoMadrid):
    def test_mismos_totales_que_consultar_en_tres_fechas(self):
        base = int(time.time())
        base -= base % 3600
        turnos = [turno(f"claude:{i}", base - i * 5 * 3600 - 1800, proyecto=f"u/x/p{i % 3}", gi=i + 1, go=1, cr=10 * i, cw=2) for i in range(40)]
        turnos += [turno("claude:syn", base - 3600, modelo="<synthetic>", gi=99999)]
        self.mete(turnos)
        self.mete([turno(f"pi:{i}", base - i * 7 * 3600 - 900, agente="pi", proveedor="nan", modelo="deepseek-v4", proyecto="a/b/z") for i in range(15)],
                  "pi", "f2")
        self.completa()
        r = consulta.consultar()
        hoy = datetime.date.today()
        h = historico.historico((hoy - datetime.timedelta(days=29)).isoformat(), hoy.isoformat())
        for dia, v in r["dias"].items():
            self.assertEqual(h["totales"][h["cubos"].index(dia)] or 0, v["tok"], dia)
        for plan, v in r["proveedores"].items():
            ph = historico.historico((hoy - datetime.timedelta(days=29)).isoformat(), hoy.isoformat(), agrupa="plan")
            self.assertEqual(sum(x or 0 for x in next((s["tokens"] for s in ph["series"] if s["nombre"] == plan), [0])), v["mes"], plan)


class TestParametros(ConHusoMadrid):
    def test_rechaza_lo_que_no_es_una_fecha_estricta(self):
        self.mete([turno("claude:a", utc("2026-10-12T10:00"))])
        self.completa()
        for malo in ("20261012", "2026-1-2", "2026-13-01", "2026-02-30", "1999-01-01", "", None, "2026-10-12 ", "２０２６-10-12"):
            with self.assertRaises(historico.ErrorHistorico, msg=str(malo)):
                historico.historico(malo, "2026-10-13")
        with self.assertRaises(historico.ErrorHistorico):
            historico.historico("2026-10-13", "2026-10-12")
        for paso, agrupa in (("hora", "ninguno"), ("dia", "modelo"), ("dia'; DROP", "ninguno")):
            with self.assertRaises(historico.ErrorHistorico):
                historico.historico("2026-10-12", "2026-10-13", paso=paso, agrupa=agrupa)

    def test_rango_enorme_se_corta_antes_de_consultar(self):
        self.completa()
        with mock.patch.object(consulta.almacen, "abrir", side_effect=AssertionError("no debe abrir la base")):
            for paso, desde, hasta in (("dia", "2000-01-01", "2026-10-13"), ("semana", "2000-01-01", "2026-10-13")):
                with self.assertRaises(historico.ErrorHistorico):
                    historico.historico(desde, hasta, paso=paso)
        self.assertEqual(len(historico._cubos(datetime.date(2000, 1, 1), datetime.date(2026, 10, 13), "mes")), 322)

    def test_sin_base_es_sin_almacen(self):
        with self.assertRaises(consulta.SinAlmacen):
            historico.historico("2026-10-12", "2026-10-13")

    def test_solo_disponible_con_el_almacen_activo(self):
        self.assertFalse(historico.disponible({})[0])
        self.assertFalse(historico.disponible({"almacen": "sombra"})[0])
        self.assertTrue(historico.disponible({"almacen": "activo"})[0])


class TestAnteriorYProyeccion(ConHusoMadrid):
    def test_periodo_anterior_y_proyeccion_del_mes(self):
        self.mete([turno("claude:a", utc("2026-10-02T10:00"), gi=100, go=0, cr=0, cw=0), turno("claude:b", utc("2026-10-09T10:00"), gi=40, go=0, cr=0, cw=0)])
        self.completa()
        r = historico.historico("2026-10-08", "2026-10-14", paso="semana")
        self.assertEqual(r["anterior"], 100)               # la semana del 5 al 11 frente a la anterior (del 28-09 al 4-10)
        m = r["mes_en_curso"]
        self.assertEqual(m["dias_mes"], 31)
        self.assertIn(m["proyeccion"] is None, (True, False))


class TestEndpoint(ConHusoMadrid):
    def setUp(self):
        super().setUp()
        import http.client, threading
        from infobar import servidor_local
        self.srv = servidor_local.crear_servidor(0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.srv.shutdown(), self.srv.server_close()))
        self.puerto = self.srv.server_address[1]
        self.cookie = aislamiento.cookie_de(self.srv)
        self.http = http.client

    def pide(self, ruta, sesion=True):
        c = self.http.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        h = {"Host": f"127.0.0.1:{self.puerto}"}
        if sesion:
            h["Cookie"] = self.cookie
        c.request("GET", ruta, headers=h)
        r = c.getresponse()
        import json
        cuerpo = r.read().decode()
        try:
            return r.status, json.loads(cuerpo or "{}")
        except ValueError:
            return r.status, {}

    def activo(self):
        return mock.patch.object(historico, "cfg", lambda: {"almacen": "activo"})

    def test_pide_sesion(self):
        self.assertEqual(self.pide("/historico?desde=2026-10-12&hasta=2026-10-12", sesion=False)[0], 401)

    def test_con_la_sombra_avisa_y_no_calcula(self):
        estado, d = self.pide("/historico?desde=2026-10-12&hasta=2026-10-12")
        self.assertEqual((estado, d["disponible"]), (200, False))
        self.assertIn("activo", d["motivo"])

    def test_devuelve_el_histórico_con_el_almacén_activo(self):
        self.mete([turno("claude:a", utc("2026-10-12T10:00"), gi=7, go=0, cr=0, cw=0)])
        self.completa()
        with self.activo():
            estado, d = self.pide("/historico?desde=2026-10-12&hasta=2026-10-12&agrupa=agente")
        self.assertEqual((estado, d["disponible"], d["total"]), (200, True, 7))
        self.assertEqual(d["series"][0]["nombre"], "Claude Code")

    def test_parametros_raros_son_400(self):
        self.completa()
        with self.activo():
            for q in ("desde=20261012&hasta=2026-10-13", "desde=2026-10-12&hasta=2026-10-13&paso=hora", "desde=2026-10-12&hasta=2026-10-13&x=1",
                      "desde=2026-10-12&desde=2026-10-13&hasta=2026-10-13", "desde=2000-01-01&hasta=2026-10-13"):
                self.assertEqual(self.pide("/historico?" + q)[0], 400, q)

    def test_sin_base_es_503(self):
        with self.activo():
            self.assertEqual(self.pide("/historico?desde=2026-10-12&hasta=2026-10-13")[0], 503)

    def test_la_tercera_consulta_simultanea_recibe_429(self):
        import threading
        dentro, suelta, estados = threading.Semaphore(0), threading.Event(), []

        def lenta(*a, **k):
            dentro.release()
            suelta.wait(10)
            return {"cubos": [], "series": [], "totales": [], "total": 0}

        with self.activo(), mock.patch.object(historico, "historico", side_effect=lenta):
            hilos = [threading.Thread(target=lambda: estados.append(self.pide("/historico?desde=2026-10-12&hasta=2026-10-12")[0])) for _ in range(2)]
            for h in hilos:
                h.start()
            for _ in range(2):
                self.assertTrue(dentro.acquire(timeout=10))
            tercera = self.pide("/historico?desde=2026-10-12&hasta=2026-10-12")[0]
            suelta.set()
            for h in hilos:
                h.join(10)
        self.assertEqual(tercera, 429)
        self.assertEqual(sorted(estados), [200, 200])
