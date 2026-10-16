"""Lectura periódica del panel de Dokploy (hilo propio, caché, copias) y los avisos de copia antigua, despliegue fallido y panel sin lectura."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import config_y_estado, rutas, servidores_avisos as sa, servidores_ciclo, servidores_copias as sc, servidores_historico as sh, servidores_panel as sp  # noqa: E402
from infobar import servidores_registro as reg  # noqa: E402
from test_servidores_integracion import limpia_config, tearDownModule  # noqa: E402,F401

ID = "a" * 32
OTRO = "b" * 32
T0 = 1_790_000_000
DIA = 86400
NOMBRE = "srv-secreto"
SERVIDOR = {"id": ID, "nombre": NOMBRE, "ssh": "infobar@a.example.com", "panel": "dokploy", "panel_url": "https://Panel.Ejemplo.com:443/lo/que/sea"}
CLAVE = "clave-de-prueba-XYZ789"


def srv(i="s1", **kw):
    return {"id": i, "tipo": "application", "estado": "done", "nombre": "Mi tienda", "slug": "tienda", "despliegue": T0 - 3600, "servidor_panel": None, "proyecto_id": "p1", "proyecto": "Tienda",
            "entorno": "production", **kw}


def cop(ts, estado="done", dueno="bd1", **kw):
    return {"dueno": dueno, "tipo": "backup", "destino": "d1", "bd": "postgres", "variante": "database", "estado": estado, "ts": ts, "nombre": "bd-tienda", "tipo_servicio": "postgres", **kw}


def lectura(servicios=None, copias=None, **kw):
    servicios = servicios if servicios is not None else [srv(f"s{i}") for i in range(5)] + [srv("bd1", tipo="postgres")]
    copias = copias if copias is not None else [cop(T0 - i * DIA) for i in range(4)]
    return {"ok": True, "version": "0.30.8", "version_no_probada": False, "servicios": servicios, "dominios": [], "copias": copias, "copias_truncadas": False, "descartes": {}, **kw}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-pan-")
        p = mock.patch.object(rutas, "DATOS", self.dir)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)
        limpia_config([dict(SERVIDOR)])
        self.addCleanup(limpia_config, [])
        os.makedirs(os.path.dirname(rutas.SERVIDORES), exist_ok=True)
        self.escribe_cache({NOMBRE: {"estado": "verde", "ok_en": T0, "intento_en": T0, "error": ""}})
        self.peticiones = []
        self.historial = reg.Historial(reloj=lambda: float(T0))

    def escribe_cache(self, servidores):
        config_y_estado._escribe_json(rutas.SERVIDORES, {"generado_en": T0, "servidores": servidores})

    def cache(self):
        return servidores_ciclo.leer_cache()["servidores"][NOMBRE]

    def leer_falso(self, resultado):
        def leer(url, clave, ahora):
            self.peticiones.append((url, clave))
            return resultado() if callable(resultado) else resultado
        return leer

    def pasada(self, resultado, ahora=T0, clave=CLAVE, historial=True):
        return sp.pasada(self.historial if historial else None, ahora, leer=self.leer_falso(resultado), clave_de=lambda cuenta: clave if not callable(clave) else clave(cuenta))


class CuentaTest(unittest.TestCase):
    def test_la_cuenta_normaliza_mayusculas_y_puerto_443(self):
        self.assertEqual(sp.cuenta(SERVIDOR), "dokploy:panel.ejemplo.com")
        self.assertEqual(sp.cuenta(dict(SERVIDOR, panel_url="https://panel.ejemplo.com:8443")), "dokploy:panel.ejemplo.com:8443")
        self.assertEqual(sp.cuenta(dict(SERVIDOR, panel_url="https://panel.ejemplo.com")), sp.cuenta(dict(SERVIDOR, panel_url="https://PANEL.ejemplo.com:443/x")))

    def test_sin_panel_o_con_otro_panel_no_hay_cuenta(self):
        for s in ({"nombre": "x"}, dict(SERVIDOR, panel="easypanel"), dict(SERVIDOR, panel_url=""), dict(SERVIDOR, panel_url="https://"), dict(SERVIDOR, panel_url="https://a:99999"), None, 5):
            self.assertEqual(sp.cuenta(s), "")


class PasadaTest(Base):
    def test_lectura_buena_llena_la_cache_y_las_copias(self):
        self.assertTrue(self.pasada(lectura()))
        p = self.cache()["panel"]
        self.assertEqual((p["ok_en"], p["error"], p["version"], p["n_servicios"], p["proximo_en"]), (T0, "", "0.30.8", 6, T0 + sp.CADA_S))
        self.assertEqual(len(p["servicios"]), 6)
        self.assertEqual(p["copias_resumen"], {"n": 1, "antiguas": 0, "sin_buena": 0})
        self.assertEqual(self.peticiones, [("https://Panel.Ejemplo.com:443/lo/que/sea", CLAVE)])
        con = sh.abrir(solo_lectura=True)
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM copias").fetchone()[0], 1)
        finally:
            con.close()

    def test_sin_clave_no_se_hace_ninguna_peticion(self):
        self.assertFalse(self.pasada(lectura(), clave=""))
        self.assertEqual(self.peticiones, [])
        self.assertNotIn("panel", self.cache())

    def test_sin_lectura_ssh_previa_no_se_crea_entrada(self):
        self.escribe_cache({})
        self.assertFalse(self.pasada(lectura()))
        self.assertEqual(self.peticiones, [])

    def test_calendario_de_cinco_minutos_y_reloj_adelantado(self):
        self.pasada(lectura())
        self.pasada(lectura(), ahora=T0 + 60)
        self.assertEqual(len(self.peticiones), 1)
        self.pasada(lectura(), ahora=T0 + sp.CADA_S)
        self.assertEqual(len(self.peticiones), 2)
        p = self.cache()["panel"]
        p["proximo_en"] = T0 + 10 ** 9                                       # un reloj que estuvo adelantado no congela la lectura
        servidores_ciclo._escribe_json(rutas.SERVIDORES, {"generado_en": T0, "servidores": {NOMBRE: {**self.cache(), "panel": p}}})
        self.pasada(lectura(), ahora=T0 + 2 * sp.CADA_S)
        self.assertEqual(len(self.peticiones), 3)

    def test_un_error_se_apunta_con_codigo_cerrado_y_conserva_lo_anterior(self):
        self.pasada(lectura())
        self.pasada({"ok": False, "error": "timeout"}, ahora=T0 + sp.CADA_S)
        p = self.cache()["panel"]
        self.assertEqual((p["error"], p["ok_en"], p["n_servicios"]), ("timeout", T0, 6))
        self.assertEqual(p["proximo_en"], T0 + 2 * sp.CADA_S)
        self.assertNotIn("error", {k for k in self.cache() if k == "error" and self.cache()["error"]})      # el error del PANEL nunca es el del servidor
        self.assertEqual(self.cache()["error"], "")
        self.assertEqual(self.cache()["estado"], "verde")

    def test_clave_rechazada_espera_media_hora(self):
        self.pasada({"ok": False, "error": "clave_rechazada"})
        self.assertEqual(self.cache()["panel"]["proximo_en"], T0 + sp.ESPERA_RECHAZO_S)
        self.pasada(lectura(), ahora=T0 + 600)
        self.assertEqual(len(self.peticiones), 1)

    def test_alcance_que_se_encoge_no_se_aplica(self):
        self.pasada(lectura())
        self.pasada(lectura(servicios=[]), ahora=T0 + sp.CADA_S)
        p = self.cache()["panel"]
        self.assertEqual((p["error"], p["n_servicios"], len(p["servicios"])), ("alcance", 6, 6))
        self.pasada(lectura(servicios=[srv("a"), srv("b")]), ahora=T0 + 2 * sp.CADA_S)
        self.assertEqual(self.cache()["panel"]["error"], "alcance")                   # 2 de 6: menos de la mitad
        self.pasada(lectura(), ahora=T0 + 3 * sp.CADA_S)
        self.assertEqual(self.cache()["panel"]["error"], "")

    def test_el_ciclo_ssh_no_borra_lo_del_panel_ni_lo_confunde(self):
        self.pasada(lectura())
        previo = servidores_ciclo.leer_cache()["servidores"]
        servidores = [dict(SERVIDOR)]
        servidores_ciclo._guardar(servidores, previo, {NOMBRE: {"estado": "verde", "estado_vivo": "verde", "datos": {}, "host": "h"}}, T0 + 10)
        self.assertEqual(self.cache()["panel"]["n_servicios"], 6)

    def test_el_panel_no_decide_el_estado_del_servidor(self):
        self.pasada(lectura(servicios=[srv("a", estado="error")] * 6))
        self.assertEqual(self.cache()["estado"], "verde")
        self.assertEqual(servidores_ciclo.estado_visible(self.cache(), T0 + 10), "verde")

    def test_dos_pasadas_a_la_vez_no_se_solapan(self):
        soltar, dentro = threading.Event(), threading.Event()

        def lento():
            dentro.set()
            soltar.wait(5)
            return lectura()
        hilo = threading.Thread(target=lambda: self.pasada(lento))
        hilo.start()
        self.assertTrue(dentro.wait(5))
        self.assertFalse(self.pasada(lectura(), ahora=T0 + 10))
        soltar.set()
        hilo.join(5)
        self.assertEqual(len(self.peticiones), 1)

    def test_la_clave_no_queda_en_la_cache_ni_en_la_base(self):
        self.pasada(lectura())
        with open(rutas.SERVIDORES, encoding="utf-8") as f:
            self.assertNotIn(CLAVE, f.read())
        with open(sh.ruta(), "rb") as f:
            self.assertNotIn(CLAVE.encode(), f.read())

    def test_un_fallo_de_la_base_no_para_la_lectura_y_frena_las_copias(self):
        with mock.patch.object(sh, "abrir", side_effect=sh.ErrorAlmacen("x")), mock.patch.object(reg, "log"):
            self.assertTrue(self.pasada(lectura()))
        p = self.cache()["panel"]
        self.assertEqual((p["error"], p["n_servicios"], p["copias_resumen"]), ("", 6, None))
        self.assertGreater(self.historial.no_antes_panel, T0)

    def test_podar_nombres_vacia_el_arbol_del_panel(self):
        self.pasada(lectura())
        e = servidores_ciclo.podar_nombres(self.cache())
        self.assertEqual((e["panel"]["servicios"], e["panel"]["dominios"]), ([], []))
        self.assertEqual(e["panel"]["version"], "0.30.8")


class CopiasDelPanelTest(Base):
    def test_resumen_con_copia_antigua_y_servicio_desaparecido(self):
        self.pasada(lectura(copias=[cop(T0 - 20 * DIA - i * DIA) for i in range(4)]))
        self.assertEqual(self.cache()["panel"]["copias_resumen"], {"n": 1, "antiguas": 1, "sin_buena": 0})
        self.pasada(lectura(servicios=[srv(f"x{i}") for i in range(6)], copias=[]), ahora=T0 + sp.CADA_S)       # bd1 ya no está en el panel
        self.assertEqual(self.cache()["panel"]["copias_resumen"], {"n": 0, "antiguas": 0, "sin_buena": 0})


class AvisosPanelTest(Base):
    def setUp(self):
        super().setUp()
        self.t = float(T0 + 2 * DIA)
        self.enviados = []
        self.v = sa.Vigilante(reloj=lambda: self.t, mono=lambda: self.t, canal=lambda t, x: self.enviados.append(x) or True, sin_red=lambda: False, canal_real=True)
        self.conf = {"servidores": [dict(SERVIDOR)], "intervalo_normal_s": 300}

    def panel_en_cache(self, ahora, **panel):
        p = {"ok_en": ahora, "intento_en": ahora, "empezo_en": ahora - 3600, "error": "", "servicios": [srv("a")] + [srv(f"s{i}") for i in range(5)], **panel}
        return {"servidores": {NOMBRE: {"estado": "verde", "ok_en": ahora, "intento_en": ahora, "error": "", "panel": p}}}

    def pasa(self, segundos, cache):
        self.t += segundos
        return self.v.vigilar(self.conf, cache=cache() if callable(cache) else cache)                # la caché se construye YA con el reloj avanzado

    def inserta_copia(self, ultima_ok, intervalo=DIA):
        con = sh.abrir()
        try:
            sh._alta_servidor(con, ID, NOMBRE, T0)
            con.execute("INSERT OR REPLACE INTO copias(servidor_id, clave, dueno, tipo, etiqueta, primera, vista, ultima_ok, intervalo_s) VALUES(?,?,?,?,?,?,?,?,?)",
                        (ID, "c" * 32, "bd1", "backup", "bd-tienda", T0, int(self.t), ultima_ok, intervalo))
        finally:
            con.close()

    def test_las_reglas_nuevas_estan_en_todas_las_tablas(self):
        self.assertEqual(set(sa.REGLAS), set(sa.TEXTO))
        self.assertEqual(set(sa.REGLAS), set(sa.CLASE))
        for regla in ("copia_antigua", "despliegue_fallido", "panel_sin_lectura"):
            self.assertRegex(f"{regla}|{ID}|{'c' * 32}", sa.CLAVE.pattern)

    def test_copia_antigua_avisa_tras_confirmar_y_no_la_que_nunca_tuvo_buena(self):
        cache = lambda: self.panel_en_cache(int(self.t))  # noqa: E731
        self.inserta_copia(int(self.t) - 3600)
        self.pasa(0, cache)                                                                  # línea base global y del panel: en silencio
        self.assertEqual(self.enviados, [])
        self.inserta_copia(int(self.t) - 10 * DIA)                                              # ahora la copia pasa a antigua
        self.pasa(60, cache)
        self.assertEqual(self.enviados, [])
        self.pasa(3700, cache)
        self.assertEqual(self.enviados, [sa.TEXTO["copia_antigua"]])
        self.pasa(300, cache)
        self.assertEqual(len(self.enviados), 1)

    def test_copia_sin_ninguna_buena_no_avisa(self):
        self.inserta_copia(None)
        self.pasa(0, lambda: self.panel_en_cache(int(self.t)))
        for _ in range(5):
            self.pasa(3700, lambda: self.panel_en_cache(int(self.t)))
        self.assertEqual(self.enviados, [])

    def test_lo_que_ya_estaba_mal_en_la_primera_lectura_del_panel_no_avisa(self):
        self.pasa(0, lambda: {"servidores": {NOMBRE: {"estado": "verde", "ok_en": int(self.t), "intento_en": int(self.t), "error": ""}}})        # base global sin panel
        self.inserta_copia(int(self.t) - 30 * DIA)
        self.pasa(60, lambda: self.panel_en_cache(int(self.t), servicios=[srv("a", estado="error", despliegue=int(self.t) - 3 * DIA)] + [srv(f"s{i}") for i in range(5)]))
        for _ in range(4):
            self.pasa(3700, lambda: self.panel_en_cache(int(self.t), servicios=[srv("a", estado="error", despliegue=int(self.t) - 3 * DIA)] + [srv(f"s{i}") for i in range(5)]))
        self.assertEqual(self.enviados, [])

    def test_despliegue_fallido_reciente_avisa_y_el_antiguo_o_muy_nuevo_no(self):
        malo = lambda edad: self.panel_en_cache(int(self.t), servicios=[srv("a", estado="error", despliegue=int(self.t) - edad)] + [srv(f"s{i}") for i in range(5)])  # noqa: E731
        self.pasa(0, lambda: malo(100 * DIA))
        self.pasa(700, lambda: malo(100 * DIA))
        self.pasa(700, lambda: malo(100 * DIA))
        self.assertEqual(self.enviados, [])                                                    # de hace meses: informativo
        self.pasa(700, lambda: self.panel_en_cache(int(self.t)))                                       # el panel pasa a estar bien: rearma
        self.pasa(700, lambda: malo(60))                                                               # error de hace un minuto: aún no
        self.assertEqual(self.enviados, [])
        self.pasa(700, lambda: malo(1500))
        self.pasa(700, lambda: malo(2200))
        self.assertEqual(self.enviados, [sa.TEXTO["despliegue_fallido"]])

    def test_panel_sin_lectura_avisa_pasadas_seis_horas(self):
        viejo = lambda: self.panel_en_cache(int(self.t) - 3600, error="clave_rechazada")  # noqa: E731
        self.pasa(0, lambda: self.panel_en_cache(int(self.t)))
        self.pasa(600, viejo)
        self.pasa(3 * 3600, viejo)
        self.assertEqual(self.enviados, [])
        self.pasa(4 * 3600, viejo)
        self.assertEqual(self.enviados, [sa.TEXTO["panel_sin_lectura"]])

    def test_con_el_alcance_cambiado_no_se_evaluan_copias(self):
        self.inserta_copia(int(self.t) - 30 * DIA)
        self.pasa(0, lambda: self.panel_en_cache(int(self.t), error="alcance"))
        self.pasa(3700, lambda: self.panel_en_cache(int(self.t), error="alcance"))
        self.assertEqual(self.enviados, [])

    def test_el_texto_no_lleva_nombres(self):
        for t in (sa.TEXTO["copia_antigua"], sa.TEXTO["despliegue_fallido"], sa.TEXTO["panel_sin_lectura"]):
            self.assertNotIn("tienda", t.lower())


if __name__ == "__main__":
    unittest.main()
