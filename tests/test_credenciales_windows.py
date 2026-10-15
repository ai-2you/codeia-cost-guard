"""Credenciales en Windows (Credential Manager via ctypes): round-trip real, validaciones de
caracteres antes de tocar la API de Windows, identidad de cuenta_valida compartida con claves.py,
y no-regresion del bloque de dispatch fuera de Windows.

No importa tests/aislamiento.py a proposito: no toca config.json/HOME, y aislamiento.py
monkeypatchea claves.leer_clave con un stub (para aislar otros tests de config/servidor), lo que
interferiria con el round-trip real contra el Credential Manager de esta suite.

Arrancar con:  python3 -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from infobar import claves  # noqa: E402

CUENTA_PRUEBA = "prov:cg-test"


@unittest.skipIf(os.name != "nt", "solo Windows")
class CredencialesWindowsTest(unittest.TestCase):
    """Round-trip real contra el Credential Manager. Se prueba contra credenciales_windows
    directamente (no via claves.*) para no depender de si otro fichero de la suite ya
    monkeypatcheo claves.leer_clave (ver docstring del modulo)."""

    @classmethod
    def setUpClass(cls):
        from infobar import credenciales_windows
        cls.cw = credenciales_windows

    def tearDown(self):
        # limpieza garantizada: ninguna prueba debe dejar basura en el Credential Manager real
        self.cw.borrar_clave(CUENTA_PRUEBA)

    def test_round_trip_guardar_leer_borrar(self):
        try:
            self.assertTrue(self.cw.guardar_clave(CUENTA_PRUEBA, "valor-de-prueba-123"))
            self.assertEqual(self.cw.leer_clave(CUENTA_PRUEBA), "valor-de-prueba-123")
        finally:
            self.cw.borrar_clave(CUENTA_PRUEBA)
        self.assertEqual(self.cw.leer_clave(CUENTA_PRUEBA), "")

    def test_borrar_clave_inexistente_no_falla(self):
        self.cw.borrar_clave(CUENTA_PRUEBA)  # no existe: no debe lanzar
        self.cw.borrar_clave(CUENTA_PRUEBA)  # dos veces seguidas, tampoco

    def test_valores_no_admitidos_se_rechazan_antes_de_credwritew(self):
        for valor in ('con"comillas', "con espacio", "barra\\", ""):
            with self.assertRaises(ValueError):
                self.cw.guardar_clave(CUENTA_PRUEBA, valor)
        # nada debe haber quedado escrito pese a los intentos fallidos
        self.assertEqual(self.cw.leer_clave(CUENTA_PRUEBA), "")

    def test_cuenta_no_valida_leer_devuelve_vacio(self):
        self.assertEqual(self.cw.leer_clave("cuenta; rm -rf"), "")

    def test_cuenta_no_valida_guardar_lanza_valueerror(self):
        with self.assertRaises(ValueError):
            self.cw.guardar_clave("cuenta invalida", "x")

    def test_identidad_cuenta_valida_compartida_con_claves(self):
        """cuenta_valida es la MISMA funcion que la de claves.py, no una copia (DRY)."""
        self.assertIs(self.cw.cuenta_valida, claves.cuenta_valida)

    def test_dispatch_rebindea_hay_llavero_guardar_y_borrar(self):
        # leer_clave se omite aqui a proposito: tests/aislamiento.py (importado por otros
        # ficheros de la suite, p.ej. test_ajustes.py) monkeypatchea claves.leer_clave con un
        # stub para aislar esos tests del Llavero/Credential Manager real; comparar su identidad
        # aqui dependeria de que ficheros se han importado antes (orden no determinista). El
        # round-trip de arriba ya prueba leer_clave real llamando a credenciales_windows directamente.
        self.assertIs(claves.hay_llavero, self.cw.hay_llavero)
        self.assertIs(claves.guardar_clave, self.cw.guardar_clave)
        self.assertIs(claves.borrar_clave, self.cw.borrar_clave)


class NoRegresionDispatchTest(unittest.TestCase):
    """El bloque de dispatch al final de claves.py nunca debe ejecutarse fuera de Windows."""

    @unittest.skipIf(os.name == "nt", "esta prueba verifica el camino NO-Windows; aqui se salta")
    def test_claves_conserva_sus_funciones_originales_fuera_de_windows(self):
        for nombre in ("hay_llavero", "leer_clave", "guardar_clave", "borrar_clave"):
            funcion = getattr(claves, nombre)
            self.assertEqual(funcion.__module__, "infobar.claves",
                              f"{nombre} fue rebindeada fuera de Windows")


if __name__ == "__main__":
    unittest.main()
