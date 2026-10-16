"""Seguimiento de webs: clasificación, estado confirmado, eventos, disponibilidad, olvido y conexión con el sondeador."""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import servidores_historico as sh, servidores_registro as reg, servidores_seguimiento as sg, webs_sondeo  # noqa: E402
from test_servidores_historico import Base, NOMBRE, SID, T0  # noqa: E402
from test_servidores_integracion import limpia_config, tearDownModule  # noqa: E402,F401

ID32 = "a" * 32
URL = "https://www.example.com/"
URL2 = "https://api.example.org/salud"
OK = {"codigo": 200, "ms": 120, "cert_dias": 40}
CAIDA = {"codigo": 502, "ms": 300}
SIN_RESP = {"codigo": None, "error": "timeout"}


class SeguimientoBase(Base):
    def setUp(self):
        super().setUp()
        sh._alta_servidor(self.con, SID, NOMBRE, T0)

    def anota(self, r, ts, url=URL):
        self.con.execute("BEGIN IMMEDIATE")
        try:
            return sg.anota_web(self.con, SID, url, r, ts)
        finally:
            self.con.execute("COMMIT")

    def estado(self, url=URL):
        clave = self.uno("SELECT clave FROM webs WHERE etiqueta=?", url)
        return self.con.execute("SELECT estado, codigo, ms, cert_dias, desde, pend_n FROM webs WHERE clave=?", (clave,)).fetchone()

    def eventos(self):
        return [tuple(r) for r in self.con.execute("SELECT ts, evento, codigo FROM eventos_web ORDER BY ts, evento")]


class EsquemaV5Test(SeguimientoBase):
    def test_version_y_clases(self):
        self.assertGreaterEqual(max(sh.MIGRACIONES), 5)
        for t in ("webs", "eventos_web", "horas_web", "dias_web"):
            self.assertIn(t, sh.CLASES_SERVIDORES)
        self.assertEqual(sh.CLASES_SERVIDORES["webs"]["etiqueta"], "personal")

    def test_migracion_de_v4_a_v5_conserva_datos(self):
        self.reg({"estado": "verde", "ok_en": T0})
        self.anota(OK, T0)
        self.con.close()
        c = sqlite3.connect(sh.ruta(), isolation_level=None)
        for t in ("webs", "eventos_web", "horas_web", "dias_web", "copias"):
            c.execute(f"DROP TABLE {t}")
        c.execute("PRAGMA user_version=4")
        c.close()
        self.con = sh.abrir()
        self.assertEqual(self.uno("PRAGMA user_version"), max(sh.MIGRACIONES))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 1)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM webs"), 0)

    def test_la_copia_previa_no_lleva_dominios_ni_claves(self):
        for i in range(3):
            self.anota(OK if i == 0 else CAIDA, T0 + i * 60)
        destino = os.path.join(self.dir, "copias")
        sh.copia_sin_ips(self.con, destino)
        for f in os.listdir(destino):
            c = sqlite3.connect(os.path.join(destino, f))
            for t in ("webs", "eventos_web", "horas_web", "dias_web"):
                self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 0, t)
            c.close()

    def test_la_base_rechaza_url_con_espacios_o_estado_raro(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("INSERT INTO webs(servidor_id, clave, etiqueta, primera, ultima, estado, desde) VALUES(?,?,?,?,?,?,?)",
                             (SID, "k" * 32, "https://a b.example", 1, 1, "ok", 1))
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("INSERT INTO webs(servidor_id, clave, etiqueta, primera, ultima, estado, desde) VALUES(?,?,?,?,?,?,?)",
                             (SID, "k" * 32, URL, 1, 1, "roto", 1))


class ClaseTest(unittest.TestCase):
    def test_clases(self):
        for r, esperado in (({"codigo": 200, "ms": 10}, "ok"), ({"codigo": 301}, "ok"), ({"codigo": 401}, "ok"), ({"codigo": 403}, "ok"),
                            ({"codigo": 404}, "mal"), ({"codigo": 429}, "mal"), ({"codigo": 200, "ms": 5001}, "mal"), ({"codigo": 200, "ms": 5000}, "ok"),
                            ({"codigo": 500}, "caida"), ({"codigo": 599}, "caida"), ({"codigo": None}, "caida"), ({}, "caida"),
                            ({"codigo": True}, "caida"), ({"codigo": "200"}, "caida"), ({"codigo": 99}, "caida"), ({"codigo": 600}, "caida"),
                            ({"codigo": float("nan")}, "caida")):
            self.assertEqual(sg.clase(r), esperado, r)


class EstadoYEventosTest(SeguimientoBase):
    def test_primera_vez_es_linea_base_sin_evento(self):
        self.assertEqual(self.anota(CAIDA, T0), "base")
        self.assertEqual(self.estado()[0], "caida")
        self.assertEqual(self.eventos(), [])

    def test_un_fallo_suelto_no_genera_evento(self):
        self.anota(OK, T0)
        self.anota(CAIDA, T0 + 60)
        self.anota(OK, T0 + 120)
        self.assertEqual(self.estado()[0], "ok")
        self.assertEqual(self.estado()[5], 0)
        self.assertEqual(self.eventos(), [])

    def test_dos_fallos_separados_confirman_la_caida_y_uno_ok_la_levanta(self):
        self.anota(OK, T0)
        self.anota(CAIDA, T0 + 60)
        self.anota(CAIDA, T0 + 120)
        self.assertEqual(self.estado()[0], "caida")
        self.assertEqual(self.estado()[4], T0 + 120)
        self.anota(OK, T0 + 180)
        self.assertEqual(self.estado()[0], "ok")
        self.assertEqual(self.eventos(), [(T0 + 120, "cayo", 502), (T0 + 180, "volvio", 200)])

    def test_dos_fallos_pegados_en_el_tiempo_no_confirman(self):
        self.anota(OK, T0)
        self.anota(CAIDA, T0 + 10)
        self.anota(CAIDA, T0 + 20)
        self.assertEqual(self.estado()[0], "ok")
        self.anota(CAIDA, T0 + 60)
        self.assertEqual(self.estado()[0], "caida")

    def test_pendiente_distinto_reinicia_la_cuenta(self):
        self.anota(OK, T0)
        self.anota(CAIDA, T0 + 60)
        self.anota({"codigo": 404, "ms": 5}, T0 + 120)
        self.anota(CAIDA, T0 + 180)
        self.assertEqual(self.estado()[0], "ok")

    def test_degradada_y_vuelta_desde_mal(self):
        self.anota(OK, T0)
        self.anota({"codigo": 404}, T0 + 60)
        self.anota({"codigo": 404}, T0 + 120)
        self.assertEqual(self.estado()[0], "mal")
        self.anota(OK, T0 + 180)
        self.assertEqual([e[1] for e in self.eventos()], ["degradada", "volvio"])

    def test_de_caida_a_mal_cuenta_como_volvio(self):
        self.anota(SIN_RESP, T0)
        self.anota({"codigo": 404}, T0 + 60)
        self.anota({"codigo": 404}, T0 + 120)
        self.assertEqual(self.estado()[0], "mal")
        self.assertEqual([e[1] for e in self.eventos()], ["volvio"])

    def test_actualiza_codigo_latencia_y_certificado(self):
        self.anota(OK, T0)
        self.anota({"codigo": 204, "ms": 7, "cert_dias": 3}, T0 + 60)
        e = self.estado()
        self.assertEqual((e[1], e[2], e[3]), (204, 7, 3))

    def test_presupuesto_de_eventos_por_web_y_dia(self):
        self.anota(OK, T0)
        t = T0
        for _ in range(sg.EVENTOS_DIA_POR_WEB):
            for r in (CAIDA, CAIDA, OK):
                t += 60
                self.anota(r, t)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos_web"), sg.EVENTOS_DIA_POR_WEB)
        self.assertGreater(self.descartes("eventos_web_sobre_tope"), 0)

    def test_presupuesto_por_servidor(self):
        with mock.patch.object(sg, "EVENTOS_DIA_POR_SERVIDOR", 2):
            for u in (URL, URL2):
                self.anota(OK, T0, u)
                self.anota(CAIDA, T0 + 60, u)
                self.anota(CAIDA, T0 + 120, u)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos_web"), 2)

    def test_primer_cayo_del_dia_cabe_con_el_presupuesto_agotado(self):
        self.anota(OK, T0)
        t = T0
        with mock.patch.object(sg, "EVENTOS_DIA_POR_WEB", 2):
            for r in ({"codigo": 404}, {"codigo": 404}, OK, {"codigo": 404}, {"codigo": 404}, OK):
                t += 60
                self.anota(r, t)
            self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos_web"), 2)
            for r in (CAIDA, CAIDA):
                t += 60
                self.anota(r, t)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos_web WHERE evento='cayo'"), 1)

    def test_url_que_url_valida_acepta_pero_el_esquema_no_se_descarta_sin_romper_el_lote(self):
        for url in ("https://ejemplo.com/a(b)", "https://[2606:4700::1]/", "https://ejemplo.com/?q='x'", "https://ejemplo.com/a|b"):
            self.assertIsNone(self.anota(OK, T0, url), url)
        self.assertEqual(self.anota(OK, T0), "base")

    def test_datos_hostiles_se_descartan(self):
        for url in ("ftp://x.example/", "https://a b/", "http://127.0.0.1/", 5, None, "https://é.example/"):
            self.assertIsNone(self.anota(OK, T0, url), url)
        self.assertIsNone(self.anota("no es un dict", T0))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM webs"), 0)
        self.assertGreaterEqual(self.descartes("web_no_valida"), 6)

    def test_valores_fuera_de_rango_se_guardan_como_nulos(self):
        self.anota({"codigo": 200, "ms": 10 ** 12, "cert_dias": 10 ** 9}, T0)
        e = self.estado()
        self.assertEqual((e[1], e[2], e[3]), (200, None, None))


class DisponibilidadTest(SeguimientoBase):
    def test_contadores_por_hora_y_por_dia(self):
        self.anota(OK, T0)
        self.anota({"codigo": 404, "ms": 900}, T0 + 60)
        self.anota(CAIDA, T0 + 120)
        self.assertEqual(self.uno("SELECT SUM(ok) FROM horas_web"), 1)
        self.assertEqual(self.uno("SELECT SUM(mal) FROM dias_web"), 1)
        self.assertEqual(self.uno("SELECT SUM(caidas) FROM dias_web"), 1)
        self.assertEqual(self.uno("SELECT MAX(ms_max) FROM horas_web"), 900)

    def test_disponibilidad_cuenta_mal_como_viva_y_caida_resta(self):
        for i, r in enumerate((OK, {"codigo": 404}, CAIDA, OK)):
            self.anota(r, T0 + i * 60)
        d = sg.lista(self.con, SID, T0 + 300)[0]
        self.assertEqual(d["disp_24h"], 75.0)
        self.assertEqual(d["disp_7d"], 75.0)
        self.assertEqual(d["disp_30d"], 75.0)
        self.assertEqual(d["url"], URL)

    def test_sin_datos_en_la_ventana_es_none(self):
        self.anota(OK, T0)
        d = sg.lista(self.con, SID, T0 + 40 * 86400)[0]
        self.assertIsNone(d["disp_24h"])
        self.assertIsNone(d["disp_30d"])

    def test_eventos_de_lectura(self):
        self.anota(OK, T0)
        self.anota(CAIDA, T0 + 60)
        self.anota(CAIDA, T0 + 120)
        ev = sg.eventos(self.con, SID, T0, T0 + 1000)
        self.assertEqual([(e["evento"], e["url"]) for e in ev], [("cayo", URL)])
        self.assertEqual(sg.eventos(self.con, SID, T0 + 500, T0 + 1000), [])


class OlvidoYRetencionTest(SeguimientoBase):
    def preparar(self):
        for u in (URL, URL2):
            self.anota(OK, T0, u)
            self.anota(CAIDA, T0 + 60, u)
            self.anota(CAIDA, T0 + 120, u)

    def cuenta(self):
        return [self.uno(f"SELECT COUNT(*) FROM {t}") for t in ("webs", "eventos_web", "horas_web", "dias_web")]

    def test_sincronizar_borra_solo_las_no_vigentes(self):
        self.preparar()
        self.con.execute("BEGIN IMMEDIATE")
        self.assertEqual(sg.sincronizar(self.con, {SID: {URL}}), 1)
        self.con.execute("COMMIT")
        self.assertEqual(self.uno("SELECT COUNT(*) FROM webs"), 1)
        self.assertEqual(self.uno("SELECT etiqueta FROM webs"), URL)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos_web"), 1)
        self.assertEqual(self.uno("SELECT COUNT(DISTINCT clave) FROM horas_web"), 1)

    def test_sincronizar_con_servidor_sin_webs_o_retirado_lo_borra_todo(self):
        self.preparar()
        self.con.execute("BEGIN IMMEDIATE")
        self.assertEqual(sg.sincronizar(self.con, {}), 2)
        self.con.execute("COMMIT")
        self.assertEqual(self.cuenta(), [0, 0, 0, 0])

    def test_olvidar_un_servidor_o_todos(self):
        self.preparar()
        sh._alta_servidor(self.con, "c" * 32, "otro", T0)
        self.con.execute("BEGIN IMMEDIATE")
        sg.anota_web(self.con, "c" * 32, URL, OK, T0)
        self.assertEqual(sg.olvidar(self.con, SID), 2)
        self.con.execute("COMMIT")
        self.assertEqual(self.uno("SELECT COUNT(*) FROM webs"), 1)
        self.con.execute("BEGIN IMMEDIATE")
        self.assertEqual(sg.olvidar(self.con), 1)
        self.con.execute("COMMIT")
        self.assertEqual(self.cuenta(), [0, 0, 0, 0])

    def test_olvidar_recursos_borra_tambien_los_dominios(self):
        self.preparar()
        sh.olvidar_recursos(self.con, SID)
        self.assertEqual(self.cuenta(), [0, 0, 0, 0])

    def test_borrar_servidor_borra_en_cascada(self):
        self.preparar()
        sh.borrar_servidor(self.con, SID)
        self.assertEqual(self.cuenta(), [0, 0, 0, 0])

    def test_mantener_purga_horas_y_eventos_viejos_y_sincroniza(self):
        self.preparar()
        ahora = T0 + 200 * 86400
        sh.mantener(self.con, {SID: {"guardar_ips": True, "retencion_dias": 30}}, ahora, webs_vigentes={SID: {URL}})
        self.assertEqual(self.uno("SELECT COUNT(*) FROM webs"), 1)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos_web"), 0)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM horas_web"), 0)
        self.assertGreater(self.uno("SELECT COUNT(*) FROM dias_web"), 0)

    def test_mantener_sin_webs_vigentes_no_toca_las_webs(self):
        self.preparar()
        sh.mantener(self.con, {SID: {"guardar_ips": True, "retencion_dias": 30}}, T0 + 300)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM webs"), 2)

    def test_servidor_retirado_pierde_sus_webs(self):
        self.preparar()
        sh.mantener(self.con, {}, T0 + 300)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM webs"), 0)

    def test_marcas_de_un_reloj_adelantado_se_traen_al_presente(self):
        self.anota(OK, T0 + 10 * 86400)
        self.con.execute("BEGIN IMMEDIATE")
        sg.purgar(self.con, T0)
        self.con.execute("COMMIT")
        self.assertLessEqual(self.uno("SELECT ultima FROM webs"), T0)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM horas_web"), 0)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM dias_web"), 0)


class ConexionTest(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        from infobar import rutas
        self.dir = tempfile.mkdtemp(prefix="cg-seg-")
        p = mock.patch.object(rutas, "DATOS", self.dir)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)
        limpia_config([{"id": ID32, "nombre": NOMBRE, "ssh": "infobar@a.example.com", "webs": [URL, URL2]}])
        self.addCleanup(limpia_config, [])

    def filas(self, sql):
        con = sh.abrir(solo_lectura=True)
        try:
            return [tuple(r) for r in con.execute(sql)]
        finally:
            con.close()

    def test_registrar_webs_anota_solo_las_vigiladas(self):
        h = reg.Historial()
        h.registrar_webs({URL: OK, "https://ajena.example.net/": OK}, T0)
        self.assertEqual(self.filas("SELECT etiqueta, estado FROM webs"), [(URL, "ok")])

    def test_url_rara_vigilada_no_frena_ni_a_las_demas_ni_al_historial_ssh(self):
        rara = "https://ejemplo.com/a(b)"
        limpia_config([{"id": ID32, "nombre": NOMBRE, "ssh": "infobar@a.example.com", "webs": [URL, rara]}])
        h = reg.Historial()
        h.registrar_webs({URL: OK, rara: OK}, T0)
        self.assertEqual(self.filas("SELECT etiqueta FROM webs"), [(URL,)])
        self.assertEqual(h.no_antes, 0)
        self.assertEqual(h.no_antes_webs, 0)

    def test_fallo_de_webs_no_frena_el_historial_ssh(self):
        h = reg.Historial()
        with mock.patch.object(sh, "abrir", side_effect=sh.ErrorAlmacen("x")), mock.patch.object(reg, "log"):
            h.registrar_webs({URL: OK}, T0)
        self.assertEqual(h.no_antes, 0)

    def test_registrar_webs_sin_datos_no_crea_la_base(self):
        reg.Historial().registrar_webs({}, T0)
        self.assertFalse(os.path.exists(sh.ruta()))

    def test_un_fallo_de_la_base_no_propaga_y_frena_una_hora(self):
        ahora = [1000.0]
        h = reg.Historial(reloj=lambda: ahora[0])
        with mock.patch.object(sh, "abrir", side_effect=sh.ErrorAlmacen("x")), mock.patch.object(reg, "log") as lg:
            h.registrar_webs({URL: OK}, T0)
            self.assertEqual(lg.call_count, 1)
            h.registrar_webs({URL: OK}, T0)
            self.assertEqual(lg.call_count, 1)
        self.assertGreaterEqual(h.no_antes_webs, 1000.0 + reg.RETROCESO_S)

    def test_sincronizar_webs_borra_las_quitadas_y_no_crea_la_base(self):
        self.assertEqual(reg.sincronizar_webs(), 0)
        self.assertFalse(os.path.exists(sh.ruta()))
        h = reg.Historial()
        h.registrar_webs({URL: OK, URL2: OK}, T0)
        limpia_config([{"id": ID32, "nombre": NOMBRE, "ssh": "infobar@a.example.com", "webs": [URL]}])
        self.assertEqual(reg.sincronizar_webs(), 1)
        self.assertEqual(self.filas("SELECT etiqueta FROM webs"), [(URL,)])

    def test_pasada_llama_al_registro_con_lo_medido_y_un_fallo_suyo_no_la_rompe(self):
        vistos = []
        medir = lambda u: dict(OK)  # noqa: E731
        with mock.patch.object(webs_sondeo, "ruta", return_value=os.path.join(self.dir, "webs.json")):
            r = webs_sondeo.pasada([{"id": ID32, "nombre": NOMBRE, "ssh": "infobar@a.example.com", "webs": [URL, URL2]}], ahora=T0, medir=medir,
                                   registrar=lambda res, ahora: vistos.append((set(res), ahora)))
            self.assertTrue(r[0])
            self.assertEqual(vistos, [({URL, URL2}, T0)])
            webs_sondeo.olvidar(servidores=[])

            def roto(res, ahora):
                raise RuntimeError("x")
            r = webs_sondeo.pasada([{"id": ID32, "nombre": NOMBRE, "ssh": "infobar@a.example.com", "webs": [URL]}], ahora=T0 + 1000, medir=medir, registrar=roto)
            self.assertTrue(r[0])

    def test_pasada_con_pocas_webs_y_sin_red_no_registra(self):
        vistos = []
        srv = [{"id": ID32, "nombre": NOMBRE, "ssh": "infobar@a.example.com", "webs": [URL, URL2]}]
        with mock.patch.object(webs_sondeo, "ruta", return_value=os.path.join(self.dir, "webs3.json")):
            webs_sondeo.pasada(srv, ahora=T0, medir=lambda u: dict(SIN_RESP), red=lambda: True, registrar=lambda *a: vistos.append(a))
            self.assertEqual(vistos, [])
            webs_sondeo.pasada(srv, ahora=T0 + 1000, medir=lambda u: dict(SIN_RESP), red=lambda: False, registrar=lambda *a: vistos.append(a))
        self.assertEqual(len(vistos), 1)

    def test_el_post_de_webs_no_da_500_si_falla_la_limpieza(self):
        from infobar import servidores_ciclo, servidores_webs
        cuerpo = {"servidor": ID32, "servidores_rev": "r", "quitar": [URL]}
        with mock.patch.object(servidores_webs, "_servidor_por_id", return_value={"nombre": NOMBRE}), \
                mock.patch.object(servidores_ciclo, "leer_cache", return_value={}), \
                mock.patch.object(servidores_webs, "cambiar", return_value=([], {"servidores": []}, [URL])), \
                mock.patch.object(reg, "sincronizar_webs", side_effect=sqlite3.OperationalError("locked")), \
                mock.patch.object(servidores_webs, "log") as lg:
            codigo, _ = servidores_webs.atiende_cambio(cuerpo)
        self.assertEqual(codigo, 200)
        self.assertTrue(lg.called)

    def test_pasada_sin_red_no_registra(self):
        vistos = []
        srv = [{"id": ID32, "nombre": NOMBRE, "ssh": "infobar@a.example.com", "webs": ["https://w%d.example.com/" % i for i in range(3)]}]
        with mock.patch.object(webs_sondeo, "ruta", return_value=os.path.join(self.dir, "webs2.json")):
            r = webs_sondeo.pasada(srv, ahora=T0, medir=lambda u: dict(SIN_RESP), red=lambda: True, registrar=lambda *a: vistos.append(a))
        self.assertFalse(r[0])
        self.assertEqual(vistos, [])


if __name__ == "__main__":
    unittest.main()
