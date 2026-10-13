"""Permisos: umask 0077, directorio privado, ficheros sensibles en 0600 y render purgado."""
import os
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import permisos, rutas  # noqa: E402


def modo(ruta):
    return stat.S_IMODE(os.stat(ruta).st_mode)


@unittest.skipIf(os.name == "nt", "chmod no aplica en Windows")
class EndurecerTest(unittest.TestCase):
    def setUp(self):
        self.antes = os.umask(0o022)
        self.raiz = tempfile.mkdtemp(prefix="cg-perm-")
        os.makedirs(f"{self.raiz}/logs/render")
        self.parches = [mock.patch.object(rutas, "RAIZ", self.raiz), mock.patch.object(rutas, "LOG", f"{self.raiz}/logs/costbar.log"),
                        mock.patch.dict(os.environ, {"COSTGUARD_HOME": self.raiz})]
        for p in self.parches:
            p.start()
        for f in ("config.json", "panel.html", "state.json", "logs/costbar.log", "logs/barra.err", "logs/nan_uso.json"):
            with open(f"{self.raiz}/{f}", "w") as fh:
                fh.write("x")
            os.chmod(f"{self.raiz}/{f}", 0o644)
        os.chmod(self.raiz, 0o755)

    def tearDown(self):
        for p in self.parches:
            p.stop()
        os.umask(self.antes)

    def test_corrige_lo_que_ya_habia(self):
        permisos.endurecer()
        self.assertEqual(modo(self.raiz), 0o700)
        self.assertEqual(modo(f"{self.raiz}/logs"), 0o700)
        for f in ("config.json", "panel.html", "state.json", "logs/costbar.log", "logs/barra.err", "logs/nan_uso.json"):
            self.assertEqual(modo(f"{self.raiz}/{f}"), 0o600, f)

    def test_umask_deja_lo_nuevo_privado(self):
        permisos.endurecer()
        ruta = f"{self.raiz}/nuevo.txt"
        with open(ruta, "w") as f:
            f.write("x")
        self.assertEqual(modo(ruta), 0o600)

    def test_render_privado_y_purgado_por_edad(self):
        viejo, nuevo = f"{self.raiz}/logs/render/viejo.png", f"{self.raiz}/logs/render/nuevo.png"
        for f in (viejo, nuevo):
            open(f, "w").close()
            os.chmod(f, 0o644)
        antiguo = time.time() - permisos.RENDER_CADUCA_S - 10
        os.utime(viejo, (antiguo, antiguo))
        permisos.endurecer()
        self.assertFalse(os.path.exists(viejo))
        self.assertEqual(modo(nuevo), 0o600)
        self.assertEqual(modo(f"{self.raiz}/logs/render"), 0o700)

    def test_un_checkout_de_desarrollo_no_cambia_el_modo_del_directorio(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COSTGUARD_HOME", None)
            permisos.endurecer()
        self.assertEqual(modo(self.raiz), 0o755)          # no es ~/.cost-guard
        self.assertEqual(modo(f"{self.raiz}/config.json"), 0o600)

    def test_costguard_home_de_otra_ruta_no_cuenta_como_instalacion(self):
        with mock.patch.dict(os.environ, {"COSTGUARD_HOME": tempfile.mkdtemp(prefix="cg-otra-")}):
            permisos.endurecer()
        self.assertEqual(modo(self.raiz), 0o755)

    def test_idempotente(self):
        permisos.endurecer()
        permisos.endurecer()
        self.assertEqual(modo(f"{self.raiz}/config.json"), 0o600)


if __name__ == "__main__":
    unittest.main()
