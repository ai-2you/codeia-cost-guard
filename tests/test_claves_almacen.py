"""Almacén de secretos: el Administrador de credenciales de Windows se prueba con un advapi32 simulado (sin Windows),
y la selección de almacén por sistema con un doble. Las claves no se imprimen ni salen en mensajes de error."""
import ctypes
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import claves  # noqa: E402


class AdvapiFalso:
    """Imita CredReadW/CredWriteW/CredDeleteW/CredFree sobre las mismas estructuras ctypes que usa el código real."""
    def __init__(self, falla_escritura=False):
        self.datos, self.liberadas, self.falla_escritura = {}, 0, falla_escritura

    def CredWriteW(self, ref, _flags):
        if self.falla_escritura:
            return 0
        c = ref._obj
        self.datos[c.TargetName] = (c.Type, c.Persist, c.UserName, bytes(c.CredentialBlob[:c.CredentialBlobSize]))
        return 1

    def CredReadW(self, objetivo, tipo, _flags, ref):
        d = self.datos.get(objetivo)
        if not d or d[0] != tipo:
            return 0
        blob = (ctypes.c_ubyte * len(d[3])).from_buffer_copy(d[3])
        self._vivos = getattr(self, "_vivos", [])
        cred = claves._CREDENCIAL(Type=tipo, CredentialBlobSize=len(d[3]), CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)))
        self._vivos.append((blob, cred))
        ref._obj.contents = cred
        return 1

    def CredDeleteW(self, objetivo, tipo, _flags):
        return 1 if self.datos.pop(objetivo, None) else 0

    def CredFree(self, _p):
        self.liberadas += 1


class AdminCredencialesTest(unittest.TestCase):
    def setUp(self):
        self.api = AdvapiFalso()
        self.b = claves._AdminCredenciales(self.api)

    def test_estructura_con_el_tamano_de_wincred(self):
        esperado = 80 if ctypes.sizeof(ctypes.c_void_p) == 8 else 52
        self.assertEqual(ctypes.sizeof(claves._CREDENCIAL), esperado)

    def test_ida_y_vuelta_reemplazo_y_borrado(self):
        self.b.guardar("nan", "abc123-XYZ")
        self.assertEqual(self.b.leer("nan"), "abc123-XYZ")
        self.b.guardar("nan", "otra")
        self.assertEqual(self.b.leer("nan"), "otra")
        self.assertEqual(self.api.liberadas, 2)
        self.b.borrar("nan")
        self.assertEqual(self.b.leer("nan"), "")
        self.b.borrar("nan")                       # borrar lo que no está no falla

    def test_se_guarda_local_generica_y_con_prefijo_de_servicio(self):
        self.b.guardar("dokploy:panel.example.com:3000", "k")
        (objetivo, (tipo, persiste, usuario, _)), = self.api.datos.items()
        self.assertEqual((objetivo, tipo, persiste), ("infobar:dokploy:panel.example.com:3000", 1, 2))   # 2 = solo este equipo
        self.assertEqual(usuario, "dokploy:panel.example.com:3000")

    def test_cuentas_no_se_mezclan(self):
        self.b.guardar("nan", "uno")
        self.b.guardar("prov:openai", "dos")
        self.assertEqual((self.b.leer("nan"), self.b.leer("prov:openai"), self.b.leer("prov:otro")), ("uno", "dos", ""))

    def test_fallo_de_escritura_no_repite_la_clave(self):
        b = claves._AdminCredenciales(AdvapiFalso(falla_escritura=True))
        with mock.patch.object(ctypes, "get_last_error", return_value=5, create=True):
            with self.assertRaises(RuntimeError) as e:
                b.guardar("nan", "SECRETO-no-debe-salir")
        self.assertNotIn("SECRETO", str(e.exception))


class SeleccionTest(unittest.TestCase):
    def test_con_backend_las_funciones_validan_y_delegan(self):
        b = claves._AdminCredenciales(AdvapiFalso())
        with mock.patch.object(claves, "_backend", return_value=b):
            self.assertTrue(claves.hay_llavero())
            self.assertTrue(claves.guardar_clave("nan", "valor1"))
            self.assertEqual(claves.leer_clave_real("nan"), "valor1")
            self.assertEqual(claves.leer_clave_real("cuenta mala"), "")
            for malo in ("", "con espacio", 'co"mi', "ba\\rra", "ñandú", "x" * (claves.MAX_CLAVE + 1)):
                with self.assertRaises(ValueError, msg=malo[:10]):
                    claves.guardar_clave("nan", malo)
            with self.assertRaises(ValueError):
                claves.guardar_clave("cuenta mala", "valor")
            claves.borrar_clave("nan")
            self.assertEqual(claves.leer_clave_real("nan"), "")

    def test_sin_almacen_no_se_guarda_en_claro(self):
        with mock.patch.object(claves, "_backend", return_value=None):
            self.assertFalse(claves.hay_llavero())
            self.assertEqual(claves.leer_clave_real("nan"), "")
            with self.assertRaises(RuntimeError):
                claves.guardar_clave("nan", "valor1")
            claves.borrar_clave("nan")

    def test_un_fallo_del_almacen_al_leer_da_vacio(self):
        class Roto:
            def leer(self, _c):
                raise OSError("boom")
        with mock.patch.object(claves, "_backend", return_value=Roto()):
            self.assertEqual(claves.leer_clave_real("nan"), "")

    def test_el_almacen_sale_segun_el_sistema(self):
        with mock.patch.object(os, "name", "nt"):
            self.assertIsInstance(claves._backend(), claves._AdminCredenciales)
        with mock.patch.object(os, "name", "posix"), mock.patch.object(claves.shutil, "which", return_value=None):
            self.assertIsNone(claves._backend())
        with mock.patch.object(os, "name", "posix"), mock.patch.object(claves.shutil, "which", return_value="/usr/bin/security"):
            self.assertIsInstance(claves._backend(), claves._Llavero)


if __name__ == "__main__":
    unittest.main()
