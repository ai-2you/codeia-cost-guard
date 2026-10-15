"""Detalle de un servidor para el panel (`/servidores/detalle`) y prueba de aviso (`/servidores/avisos/probar`)."""
import os
import sqlite3
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import servidores_api as api, servidores_avisos as sa, servidores_detalle as sd, servidores_historico as sh, servidores_seguimiento as sg  # noqa: E402
from test_servidores_recursos import BaseRecursos, fila  # noqa: E402
from test_servidores_historico import NOMBRE, T0  # noqa: E402

ID = "a" * 32
OTRO = "b" * 32
URL = "https://web.example.com/"
URL2 = "https://sin-sondear.example.com/"
CONF = {"servidores": [{"id": ID, "nombre": "srv", "ssh": "infobar@a.example.com", "webs": [URL, URL2]}], "intervalo_normal_s": 300}


def q(servidor=ID, **extra):
    return {"servidor": [servidor], **{k: [v] for k, v in extra.items()}}


class DetalleBase(BaseRecursos):
    def setUp(self):
        super().setUp()
        sh._alta_servidor(self.con, ID, "srv", T0 - 3600)
        self.con.execute("UPDATE servidores SET ok_en=?, intento_en=? WHERE id=?", (T0, T0, ID))

    def web(self, r, ts, url=URL):
        self.con.execute("BEGIN IMMEDIATE")
        sg.anota_web(self.con, ID, url, r, ts)
        self.con.execute("COMMIT")

    def pide(self, parametros=None, ahora=T0 + 60, conf=CONF):
        return sd.atiende(parametros if parametros is not None else q(), ahora, conf)

    def inserta_recurso(self, tipo, etiqueta, clave, activo=1, estado="ok", ultima=T0, reinicios=0):
        self.con.execute("INSERT INTO recursos(servidor_id, tipo, clave, etiqueta, primera, ultima, activo, estado, salud, reinicios, inicio, visto_n, visto_desde, ausente_n, ausente_desde, bucle_n)"
                         " VALUES(?,?,?,?,?,?,?,?,NULL,?,NULL,1,?,0,NULL,0)", (ID, tipo, clave, etiqueta, T0 - 1000, ultima, activo, estado, reinicios, T0 - 1000))

    def inserta_evento(self, tipo, clave, evento, ts, severidad="aviso"):
        self.con.execute("INSERT INTO eventos(servidor_id, ts, clave, tipo, evento, severidad) VALUES(?,?,?,?,?,?)", (ID, ts, clave, tipo, evento, severidad))


class ParametrosYPuertaTest(DetalleBase):
    def test_parametros_estrictos(self):
        for malo in ({}, q(otro="x"), {"servidor": [ID, ID]}, q(servidor=""), q(servidor="A" * 32), q(servidor="a" * 31), q(servidor=ID + "x"), {"servidor": ["../" + ID]}):
            self.assertEqual(self.pide(malo)[0], 400, malo)

    def test_servidor_no_configurado_da_siempre_el_mismo_404(self):
        r1 = self.pide(q(OTRO))
        r2 = self.pide(q("c" * 32))
        self.assertEqual(r1, r2)
        self.assertEqual(r1[0], 404)
        self.assertEqual(self.pide(q(ID), conf={"servidores": []})[0], 404)

    def test_configurado_sin_lecturas_es_sin_datos_no_404(self):
        self.con.execute("DELETE FROM servidores")
        codigo, d = self.pide()
        self.assertEqual((codigo, d["disponible"]), (200, False))
        self.assertIn("lecturas", d["motivo"])

    def test_sin_base_es_sin_datos(self):
        self.con.close()
        os.remove(sh.ruta())
        codigo, d = self.pide()
        self.assertEqual((codigo, d["disponible"]), (200, False))

    def test_base_antigua_dice_que_se_esta_actualizando(self):
        self.con.execute("PRAGMA user_version=4")
        codigo, d = self.pide()
        self.assertEqual((codigo, d["disponible"]), (200, False))
        self.assertIn("actualizando", d["motivo"])

    def test_base_corrupta_da_503_sin_citar_el_error(self):
        self.con.close()
        with open(sh.ruta(), "wb") as f:
            f.write(b"esto no es sqlite " * 500)
        for ext in ("-wal", "-shm"):
            if os.path.exists(sh.ruta() + ext):
                os.remove(sh.ruta() + ext)
        with mock.patch.object(sd, "log") as lg:
            codigo, d = self.pide()
        self.assertEqual(codigo, 503)
        self.assertEqual(set(d), {"error"})
        self.assertNotIn("sqlite", str(d).lower())
        self.assertNotIn("srv", str(lg.call_args_list))

    def test_la_ruta_esta_en_la_lista_cerrada_de_la_api(self):
        self.assertEqual(api.PARAMS["/servidores/detalle"], {"servidor"})
        self.assertEqual(api.atiende("/servidores/detalle", {"servidor": [ID], "otro": ["x"]})[0], 400)
        self.assertEqual(api.atiende("/servidores/detalle", {"servidor": [ID, ID]})[0], 400)


class ContenidoTest(DetalleBase):
    def test_cabecera_y_frescura(self):
        self.reg(self.lectura(T0, [fila("a")]))
        codigo, d = self.pide()
        self.assertEqual(codigo, 200)
        self.assertTrue(d["disponible"])
        self.assertEqual(d["generado_en"], T0 + 60)
        self.assertEqual(d["ventana_s"], 3 * 300 + 60)
        self.assertEqual((d["servidor"]["ok_en"], d["servidor"]["vigilado_desde"]), (T0, T0 - 3600))
        self.assertGreater(d["horas"]["total"], 0)

    def test_ventana_minima_de_15_minutos(self):
        conf = dict(CONF, intervalo_normal_s=60)
        self.assertEqual(self.pide(conf=conf)[1]["ventana_s"], 900)

    def test_webs_parten_de_la_lista_configurada(self):
        self.web({"codigo": 200, "ms": 50, "cert_dias": 30}, T0)
        self.web({"codigo": 404, "ms": 60, "cert_dias": 30}, T0 + 60)
        self.web({"codigo": 200, "ms": 55, "cert_dias": 29}, T0 + 120, "https://ya-no-configurada.example.com/")
        d = self.pide()[1]
        por_url = {w["url"]: w for w in d["webs"]}
        self.assertEqual(set(por_url), {URL, URL2})                                       # la quitada de la configuración no se pinta
        self.assertFalse(por_url[URL2]["sondeada"])
        self.assertEqual(set(por_url[URL2]), {"url", "sondeada"})
        w = por_url[URL]
        self.assertTrue(w["sondeada"])
        self.assertEqual((w["n_24h"], w["mal_24h"], w["disp_24h"]), (2, 1, 100.0))        # «mal» cuenta como viva, pero se ve cuántos fueron con error
        self.assertEqual((w["codigo"], w["cert_dias"]), (404, 30))

    def test_mal_no_incluye_las_caidas_que_son_las_que_restan(self):
        for i, r in enumerate(({"codigo": 200, "ms": 5}, {"codigo": 200, "ms": 5}, {"codigo": 404, "ms": 5}, {"codigo": 502})):
            self.web(r, T0 + i * 60)
        w = {x["url"]: x for x in self.pide()[1]["webs"]}[URL]
        self.assertEqual((w["n_24h"], w["mal_24h"], w["disp_24h"]), (4, 1, 75.0))          # 1 con error o lenta (404) y 1 caída (502): solo la caída resta

    def test_recursos_truncados_se_avisan(self):
        with mock.patch.object(sd, "MAX_RECURSOS", 2):
            for i in range(3):
                self.inserta_recurso("contenedor", f"c{i}", f"{i + 1:032x}")
            d = self.pide()[1]
        self.assertEqual((len(d["recursos"]), d["recursos_truncado"]), (2, True))
        self.assertFalse(self.pide()[1]["recursos_truncado"])

    def test_recursos_ordenados_con_lo_activo_primero_y_campos_acotados(self):
        self.inserta_recurso("compose", "p/web", "1" * 32, activo=1, ultima=T0)
        self.inserta_recurso("contenedor", "viejo", "2" * 32, activo=0, ultima=T0 - 86400, estado="ok")
        self.inserta_recurso("servicio", "pila_svc", "3" * 32, activo=1, ultima=T0 - 5, reinicios=None)
        d = self.pide()[1]
        self.assertEqual([r["nombre"] for r in d["recursos"]], ["p/web", "pila_svc", "viejo"])
        self.assertIsNone(d["recursos"][1]["reinicios"])
        self.assertEqual([r["activo"] for r in d["recursos"]], [True, True, False])
        self.assertEqual({"tipo", "nombre", "estado", "salud", "reinicios", "activo", "visto_n", "primera", "ultima"}, set(d["recursos"][0]))

    def test_eventos_mezclan_recursos_y_webs_y_un_recurso_olvidado_no_tiene_nombre(self):
        self.inserta_recurso("contenedor", "sigue", "4" * 32)
        self.inserta_evento("contenedor", "4" * 32, "reinicios_en_bucle", T0 - 10, "critica")
        self.inserta_evento("contenedor", "5" * 32, "desaparecio", T0 - 20)
        self.web({"codigo": 200, "ms": 5}, T0 - 500)
        self.web({"codigo": 502}, T0 - 440)
        self.web({"codigo": 502}, T0 - 380)
        d = self.pide()[1]
        e = d["eventos"]
        self.assertEqual([x["ts"] for x in e], sorted((x["ts"] for x in e), reverse=True))
        self.assertEqual({x["origen"] for x in e}, {"recurso", "web"})
        bucle = next(x for x in e if x["evento"] == "reinicios_en_bucle")
        self.assertEqual((bucle["nombre"], bucle["severidad"]), ("sigue", "critica"))
        self.assertIsNone(next(x for x in e if x["evento"] == "desaparecio")["nombre"])
        cayo = next(x for x in e if x["evento"] == "cayo")
        self.assertEqual((cayo["nombre"], cayo["codigo"]), (URL, 502))
        self.assertFalse(d["truncado"])

    def test_solo_se_miran_catorce_dias_y_hay_tope_con_aviso(self):
        self.inserta_evento("contenedor", "6" * 32, "desaparecio", T0 - 15 * 86400)
        for i in range(sd.MAX_EVENTOS + 5):
            self.inserta_evento("servicio", f"{i:032x}", "cambio_estado", T0 - 1000 + i)
        d = self.pide()[1]
        self.assertEqual(len(d["eventos"]), sd.MAX_EVENTOS)
        self.assertTrue(d["truncado"])
        self.assertTrue(all(x["ts"] >= T0 - 14 * 86400 for x in d["eventos"]))

    def test_nunca_salen_ips_ni_otros_servidores(self):
        sh._alta_servidor(self.con, OTRO, "otro-secreto", T0)
        self.con.execute("INSERT INTO ips_atacantes(servidor_id, ip, primera, ultima, max_24h, horas_vista, ultima_hora) VALUES(?,?,?,?,?,?,?)", (ID, "8.8.8.8", 1, T0, 5, 1, T0))
        self.con.execute("INSERT INTO recursos(servidor_id, tipo, clave, etiqueta, primera, ultima, activo, estado, reinicios, visto_n, bucle_n) VALUES(?,?,?,?,?,?,1,'ok',0,1,0)",
                         (OTRO, "contenedor", "7" * 32, "contenedor-ajeno", T0, T0))
        crudo = str(self.pide()[1])
        self.assertNotIn("8.8.8.8", crudo)
        self.assertNotIn("contenedor-ajeno", crudo)
        self.assertNotIn("otro-secreto", crudo)

    def test_lectura_en_una_sola_transaccion_y_conexion_de_solo_lectura(self):
        with self.assertRaises(sqlite3.OperationalError):
            ro = sh.abrir(solo_lectura=True)
            try:
                ro.execute("INSERT INTO meta(clave, valor) VALUES('x','y')")
            finally:
                ro.close()


class InventarioHostilTest(BaseRecursos):
    def test_un_nombre_compose_demasiado_largo_descarta_ese_recurso_y_no_toda_la_lectura(self):
        largo = fila("x-1", proyecto="p" * 100, servicio="s" * 100)
        self.reg(self.lectura(T0, [fila("bueno"), largo]))
        self.assertEqual(list(self.recursos()), ["bueno"])
        self.assertGreaterEqual(self.descartes("recurso_nombre_no_valido"), 1)
        self.reg(self.lectura(T0 + 300, [fila("bueno"), fila("otro"), largo]))
        self.assertIn("otro", self.recursos())                                              # el inventario siguió actualizándose
        self.assertEqual(self.descartes("inventario_error"), 0)


class ProbarAvisoTest(unittest.TestCase):
    def setUp(self):
        sa._PRUEBAS.clear()
        self.addCleanup(sa._PRUEBAS.clear)
        self.t = 1000.0
        self.enviados = []

    def prueba(self, cuerpo=None, conf=None, **kw):
        kw.setdefault("canal", lambda t, x: self.enviados.append(x) or True)
        return sa.probar({} if cuerpo is None else cuerpo, conf if conf is not None else CONF, reloj=lambda: self.t, es_mac=True, **kw)

    def test_envia_un_texto_distinto_de_los_reales(self):
        codigo, d = self.prueba()
        self.assertEqual((codigo, d["ok"]), (200, True))
        self.assertEqual(self.enviados, [sa.TEXTO_PRUEBA])
        self.assertNotIn(sa.TEXTO_PRUEBA, sa.TEXTO.values())

    def test_cuerpo_invalido(self):
        for malo in ([], "x", {"otro": 1}, {"servidor": 5}, {"servidor": "no-es-hex"}):
            self.assertEqual(self.prueba(malo)[0], 400, malo)
        self.assertEqual(self.enviados, [])

    def test_una_cada_treinta_segundos_y_el_intento_cuenta_aunque_falle(self):
        self.assertEqual(self.prueba(canal=lambda t, x: False)[1]["ok"], False)
        codigo, d = self.prueba()
        self.assertEqual(codigo, 429)
        self.assertGreater(d["espera_s"], 0)
        self.t += sa.ESPERA_PRUEBA_S + 1
        self.assertEqual(self.prueba()[0], 200)

    def test_diez_por_hora(self):
        for _ in range(sa.MAX_PRUEBAS_HORA):
            self.assertEqual(self.prueba()[0], 200)
            self.t += sa.ESPERA_PRUEBA_S + 1
        self.assertEqual(self.prueba()[0], 429)
        self.t += 3600
        self.assertEqual(self.prueba()[0], 200)

    def test_peticiones_simultaneas_solo_lanzan_una_notificacion(self):
        soltar, dentro = threading.Event(), threading.Event()

        def lento(t, x):
            self.enviados.append(x)
            dentro.set()
            soltar.wait(5)
            return True
        res = []
        hilo = threading.Thread(target=lambda: res.append(self.prueba(canal=lento)))
        hilo.start()
        self.assertTrue(dentro.wait(5))
        otros = [self.prueba() for _ in range(5)]
        soltar.set()
        hilo.join(5)
        self.assertTrue(all(c == 429 for c, _ in otros))
        self.assertEqual(len(self.enviados), 1)
        self.assertEqual(res[0][0], 200)

    def test_fuera_de_macos_lo_dice_claro(self):
        codigo, d = sa.probar({}, CONF, canal=lambda t, x: self.enviados.append(x), es_mac=False, reloj=lambda: self.t)
        self.assertEqual((codigo, d["ok"], d["motivo"]), (200, False, "solo_macos"))
        self.assertEqual(self.enviados, [])

    def test_avisa_de_que_los_reales_estan_apagados(self):
        self.assertEqual(self.prueba(conf=dict(CONF, avisos_servidores=False))[1]["apagados"], ["global"])
        self.t += 100
        apagado = {"servidores": [dict(CONF["servidores"][0], avisos=False)]}
        self.assertEqual(self.prueba({"servidor": ID}, apagado)[1]["apagados"], ["servidor"])
        self.t += 100
        self.assertEqual(self.prueba({"servidor": ID})[1]["apagados"], [])

    def test_no_se_loguea_nada_del_servidor(self):
        with mock.patch.object(sa, "log") as lg:
            self.prueba({"servidor": ID})
        self.assertEqual([c.args for c in lg.call_args_list], [("aviso de prueba enviado",)])


if __name__ == "__main__":
    unittest.main()
