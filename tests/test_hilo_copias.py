"""Hilo de copias: solo con destinatario configurado y cuando toca, un fallo no borra nada ni se registra como hecha, y una copia
lenta no retrasa la vuelta de ingesta."""
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
from infobar import almacen, exportacion, hilo_copias, ingestor, rutas  # noqa: E402
from infobar.config_y_estado import cfg, guardar_config  # noqa: E402
from test_cli_copias import llena_base  # noqa: E402

try:
    import cryptography  # noqa: F401
    from infobar import cgx
    HAY = True
except ImportError:
    HAY = False


@unittest.skipUnless(HAY, "falta cryptography")
class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-hilo-")
        self.ant = (rutas.DATOS, rutas.ALMACEN, rutas.COPIAS)
        rutas.DATOS = os.path.join(self.dir, "datos")
        rutas.ALMACEN = os.path.join(rutas.DATOS, "consumo.sqlite")
        rutas.COPIAS = os.path.join(rutas.DATOS, "copias")
        llena_base(rutas.ALMACEN)
        self.config_ant = cfg().get("copias")
        self.carpeta = os.path.join(self.dir, "cifradas")
        self.priv, self.pub = cgx.generar_par()

    def configura(self, destinatario=True):
        guardar_config({"copias": {"carpeta": self.carpeta, "firmas_publicas": [],
                                   "destinatario": [cgx.codificar_clave("publica", self.pub)] if destinatario else []}})

    def tearDown(self):
        rutas.DATOS, rutas.ALMACEN, rutas.COPIAS = self.ant
        guardar_config({"copias": self.config_ant or {}})
        shutil.rmtree(self.dir, ignore_errors=True)


class TestUnaVez(Base):
    def test_sin_destinatario_no_hace_nada(self):
        self.configura(False)
        self.assertEqual(hilo_copias.una_vez(), "sin_destinatario")
        self.assertFalse(os.path.exists(self.carpeta))

    def test_hace_la_copia_y_luego_no_toca(self):
        self.configura()
        r = hilo_copias.una_vez(1_790_000_000)
        self.assertEqual(r["danada"], False)
        self.assertEqual(hilo_copias.una_vez(1_790_000_100), "no_toca")
        self.assertEqual(len([f for f in os.listdir(self.carpeta) if f.endswith(".cgx")]), 1)
        self.assertIsInstance(hilo_copias.una_vez(1_790_000_000 + 90000), dict)

    def test_fallo_no_se_registra_como_hecha(self):
        self.configura()
        with mock.patch.object(exportacion, "hacer_copia", side_effect=OSError("destino no montado")):
            with self.assertRaises(OSError):
                hilo_copias.una_vez(1_790_000_000)
        self.assertIsNone(exportacion.ultima_copia())
        self.assertTrue(exportacion.toca_copia(1_790_000_001))

    def test_estado_para_la_barra(self):
        cc = {"destinatario": ["x"]}
        self.assertEqual(exportacion.estado_copias({}, 0)["estado"], "sin_destinatario")
        self.assertEqual(exportacion.estado_copias(cc, 0)["estado"], "nunca")
        exportacion.registrar_copia({"archivo": "a", "ts": 1000, "danada": False, "huella": "h", "bytes": 1})
        self.assertEqual(exportacion.estado_copias(cc, 1000 + 3600)["estado"], "al_dia")
        self.assertEqual(exportacion.estado_copias(cc, 1000 + 50 * 3600)["estado"], "atrasada")
        exportacion.registrar_copia({"archivo": "a", "ts": 1000, "danada": True, "huella": "h", "bytes": 1})
        self.assertEqual(exportacion.estado_copias(cc, 1000 + 3600)["estado"], "danada")


class TestIndependencia(Base):
    def test_una_copia_lenta_no_retrasa_la_ingesta_ni_el_latido(self):
        self.configura()
        home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(home, ".claude", "projects", "p"))
        ing = ingestor.Ingestor(home=home, ruta_base=rutas.ALMACEN)
        ing.vuelta()                                                   # toma el puesto
        real = exportacion.hacer_copia

        def lenta(*a, **k):
            time.sleep(2.5)
            return real(*a, **k)
        h = threading.Thread(target=lambda: hilo_copias.una_vez(1_790_000_000))
        with mock.patch.object(exportacion, "hacer_copia", lenta):
            h.start()
            t = time.monotonic()
            r = ing.vuelta()
            dt = time.monotonic() - t
            self.assertTrue(r["escritor"])
            self.assertLess(dt, 1.0)
            h.join()
        ing.cierra()


if __name__ == "__main__":
    unittest.main()
