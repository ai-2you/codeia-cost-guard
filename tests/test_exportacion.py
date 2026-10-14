"""Copias cifradas: origen consistente en temporal privado, nombres y rotación escalonada que no toca lo ajeno."""
import datetime
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, exportacion, rutas  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-export-")
        self._datos = rutas.DATOS
        rutas.DATOS = os.path.join(self.dir, "datos")

    def tearDown(self):
        rutas.DATOS = self._datos
        shutil.rmtree(self.dir, ignore_errors=True)


class TestOrigen(Base):
    def test_copia_consistente_sin_wal_y_privada(self):
        con = almacen.abrir(os.path.join(self.dir, "viva", "consumo.sqlite"))
        q = almacen.identidad("barra")
        almacen.adquirir_escritura(con, q)
        esp = almacen.espacio(con, q)
        con.execute("PRAGMA wal_autocheckpoint=0")
        fid = almacen.fuente(con, q, esp, "claude", "f")
        almacen.ingerir_turnos(con, q, esp, fid, [{"clave": "claude:m1", "hora_utc": 1_790_000_000, "agente": "claude", "proveedor": "",
                                                    "modelo": "m", "gi": 1, "go": 2, "cr": 3, "cw": 4, "proyecto_raw": "p"}])
        ruta, estado = exportacion.copiar_base(con)
        try:
            self.assertEqual(estado, "ok")
            self.assertEqual(stat.S_IMODE(os.stat(ruta).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(ruta)).st_mode), 0o700)
            self.assertFalse(os.path.exists(ruta + "-wal"))
            c = sqlite3.connect(ruta)
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM turnos").fetchone()[0], 1)   # lo que aún estaba solo en el WAL
            c.close()
        finally:
            os.remove(ruta)
            con.close()

    def test_barrido_de_temporales(self):
        d = exportacion.dir_tmp()
        for n in ("a.tmp", "b.tmp"):
            open(os.path.join(d, n), "w").close()
        self.assertEqual(exportacion.barrer_tmp(), 2)
        self.assertEqual(os.listdir(d), [])


class TestNombresYRotacion(Base):
    def test_nombre(self):
        n = exportacion.nombre_copia(1_790_000_000)
        self.assertRegex(n, r"^copia-\d{8}-\d{6}-[0-9a-f]{4}\.cgx$")
        self.assertTrue(exportacion.nombre_copia(1_790_000_000, danada=True).endswith(".danada.cgx"))
        self.assertNotEqual(exportacion.nombre_copia(1_790_000_000), exportacion.nombre_copia(1_790_000_000))

    @staticmethod
    def copia(dia, hora="030000", extra="aaaa"):
        return f"copia-{dia}-{hora}-{extra}.cgx"

    def test_conserva_14_diarias_y_6_mensuales_y_no_toca_lo_ajeno(self):
        inicio = datetime.date(2026, 1, 1)
        nombres = [self.copia((inicio + datetime.timedelta(days=i)).strftime("%Y%m%d")) for i in range(300)]
        ajenos = ["notas.txt", "copia-20260101-030000-aaaa.danada.cgx", "copia-mala.cgx", "clave.cgx"]
        borrar = set(exportacion.a_borrar(nombres + ajenos))
        self.assertFalse(borrar & set(ajenos))
        quedan = [n for n in nombres if n not in borrar]
        ultimos = nombres[-14:]
        self.assertTrue(set(ultimos) <= set(quedan))
        primeras_de_mes = {n for n in nombres if n[12:14] == "01"}
        self.assertTrue({n for n in primeras_de_mes if n[6:12] >= "202605"} <= set(quedan))     # mayo..octubre
        self.assertEqual(len(quedan), 14 + 6 - len(set(ultimos) & primeras_de_mes))
        self.assertFalse({n for n in primeras_de_mes if n[6:12] < "202605"} & set(quedan))

    def test_mismo_dia_se_queda_la_ultima(self):
        a, b = self.copia("20261014", "010000", "aaaa"), self.copia("20261014", "230000", "bbbb")
        self.assertEqual(exportacion.a_borrar([a, b]), [])       # son la primera del mes y la última del día: ambas se conservan
        c = self.copia("20261014", "120000", "cccc")
        self.assertEqual(exportacion.a_borrar([a, c, b]), [c])

    def test_una_sola_copia_nunca_se_borra(self):
        self.assertEqual(exportacion.a_borrar([self.copia("20260101")]), [])
        self.assertEqual(exportacion.a_borrar([]), [])


if __name__ == "__main__":
    unittest.main()
