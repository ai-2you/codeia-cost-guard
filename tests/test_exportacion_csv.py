"""Exportación a CSV: nada que una hoja de cálculo pueda ejecutar, ficheros privados con nombre propio, cifrado con tipo aparte
y la misma salida desde la línea de órdenes y desde el panel."""
import csv
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from test_historico import ConHusoMadrid, ServidorBase, turno, utc  # noqa: E402
from test_cli_copias import Falso  # noqa: E402
from infobar import cgx, cli_copias, exportacion_csv as ec, historico, rutas  # noqa: E402

FRASE = "una frase larga y distinta 123"
HOSTILES = ["=1+1", "+cmd|' /C calc'!A0", "-2+3", "@SUM(A1)", "\t=1", " =1", "\r=1", " =1", "‮=1", "​=1", "＝1", "＠SUM(A1)",
            "=HYPERLINK(\"http://e\";\"x\")", "a\r\n=1"]


class TestNeutralizar(unittest.TestCase):
    def test_ninguna_celda_empieza_por_algo_que_sea_formula(self):
        for h in HOSTILES:
            t = ec.neutralizar(h)
            self.assertNotIn(t.lstrip()[:1], ("=", "+", "-", "@"), repr(h))
            self.assertFalse(re.search(r"[\x00-\x08\x0b-\x1f\x7f‪-‮​-‏⁦-⁩]", t), repr(h))
            self.assertNotIn("\n", t)
            self.assertNotIn("\r", t)

    def test_lo_normal_no_se_toca(self):
        for ok in ("Cliente A", "u/x/niño-año", "OpenAI (Codex)", "Claude (Max)", "日本語", "a-b", "x=1", "café"):
            self.assertEqual(ec.neutralizar(ok), ok)

    def test_saltos_y_tabuladores_se_vuelven_espacios(self):
        self.assertEqual(ec.neutralizar("a\nb\tc\rd"), "a b c d")

    def test_controles_e_invisibles_desaparecen(self):
        self.assertEqual(ec.neutralizar("a\x00b​c‮d﻿e"), "abcde")


def resultado(**kw):
    base = {"desde": "2026-10-12", "hasta": "2026-10-13", "paso": "dia", "agrupa": "proyecto", "cubos": ["2026-10-12", "2026-10-13"],
            "series": [{"nombre": "Cliente A", "tokens": [5, None], "turnos": [2, None]}, {"nombre": "=EVIL()", "tokens": [0, 7], "turnos": [0, 3]},
                       {"nombre": "vacío", "tokens": [0, 0], "turnos": [0, 0]}]}
    base.update(kw)
    return base


class TestConstruir(unittest.TestCase):
    def test_formato_bom_separador_entrecomillado_y_crlf(self):
        datos = ec.construir(resultado())
        self.assertTrue(datos.startswith(b"\xef\xbb\xbf"))
        texto = datos.decode("utf-8-sig")
        self.assertTrue(texto.endswith("\r\n"))
        self.assertEqual(texto.split("\r\n")[0], '"inicio_periodo";"paso";"agrupacion";"grupo";"tokens";"turnos"')
        filas = list(csv.reader(io.StringIO(texto), delimiter=";"))
        self.assertEqual(filas[1:], [["2026-10-12", "dia", "proyecto", "Cliente A", "5", "2"], ["2026-10-13", "dia", "proyecto", "'=EVIL()", "7", "3"]])

    def test_huecos_y_ceros_no_salen(self):
        self.assertEqual(len(ec.filas(resultado())), 2)

    def test_un_nombre_con_saltos_o_comillas_no_crea_filas_ni_rompe_columnas(self):
        r = resultado(series=[{"nombre": 'a"b\r\nc;d', "tokens": [1, 1], "turnos": [1, 1]}])
        filas = list(csv.reader(io.StringIO(ec.construir(r).decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(len(filas), 3)
        self.assertTrue(all(len(f) == 6 for f in filas))
        self.assertEqual(filas[1][3], 'a"b  c;d')

    def test_las_cifras_tienen_que_ser_enteros_no_negativos(self):
        for malo in (-1, 1.5, "7", True, None):
            r = resultado(series=[{"nombre": "x", "tokens": [malo, 1], "turnos": [1, 1]}])
            if malo is None:
                self.assertEqual(len(ec.filas(r)), 1)           # None es hueco
            else:
                with self.assertRaises(ec.ErrorCsv, msg=repr(malo)):
                    ec.filas(r)

    def test_tope_de_filas(self):
        with mock.patch.object(ec, "MAX_FILAS", 1):
            with self.assertRaises(ec.ErrorCsv):
                ec.construir(resultado())


class ConCarpeta(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-csv-")
        self._datos = rutas.DATOS
        rutas.DATOS = os.path.join(self.dir, "datos")
        self.addCleanup(lambda: (setattr(rutas, "DATOS", self._datos), shutil.rmtree(self.dir, ignore_errors=True)))


class TestFicheros(ConCarpeta):
    def test_claro_privado_con_nombre_generado_y_auditado_sin_contenido(self):
        ruta, n = ec.escribir_claro(resultado(series=[{"nombre": "Secreto-Cliente", "tokens": [1, 2], "turnos": [1, 1]}]))
        self.assertEqual(n, 2)
        self.assertRegex(os.path.basename(ruta), r"^consumo-2026-10-12-2026-10-13-dia-proyecto-\d{8}-\d{6}-[0-9a-f]{4}\.csv$")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(ruta).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(ruta)).st_mode), 0o700)
        log = open(os.path.join(rutas.DATOS, "exportaciones.log"), encoding="utf-8").read()
        self.assertNotIn("Secreto", log)
        self.assertEqual(json.loads(log.splitlines()[0])["filas"], 2)
        self.assertEqual(json.loads(log.splitlines()[0])["cifrado"], False)

    def test_dos_exportaciones_seguidas_no_se_pisan(self):
        a, _ = ec.escribir_claro(resultado())
        b, _ = ec.escribir_claro(resultado())
        self.assertNotEqual(a, b)
        self.assertTrue(os.path.exists(a) and os.path.exists(b))

    def test_un_fichero_existente_con_el_mismo_nombre_no_se_pisa(self):
        with mock.patch.object(ec, "nombre", return_value="consumo-fijo.csv"):
            ruta, _ = ec.escribir_claro(resultado())
            antes = open(ruta, "rb").read()
            with self.assertRaises(FileExistsError):
                ec.escribir_claro(resultado(series=[{"nombre": "otro", "tokens": [9, 9], "turnos": [1, 1]}]))
        self.assertEqual(open(ruta, "rb").read(), antes)

    @unittest.skipIf(os.name == "nt", "enlaces simbólicos")
    def test_carpeta_enlazada_se_rechaza(self):
        os.makedirs(rutas.DATOS)
        destino = os.path.join(self.dir, "otro")
        os.makedirs(destino)
        os.symlink(destino, os.path.join(rutas.DATOS, "exportaciones"))
        with self.assertRaises(ec.ErrorCsv):
            ec.escribir_claro(resultado())
        self.assertEqual(os.listdir(destino), [])

    @unittest.skipIf(os.name == "nt", "enlaces simbólicos")
    def test_no_sigue_un_enlace_puesto_en_el_nombre_del_fichero(self):
        carpeta = ec.carpeta()
        victima = os.path.join(self.dir, "victima.txt")
        open(victima, "w").write("intacto")
        with mock.patch.object(ec, "nombre", return_value="consumo-fijo.csv"):
            os.symlink(victima, os.path.join(carpeta, "consumo-fijo.csv"))
            with self.assertRaises(OSError):
                ec.escribir_claro(resultado())
        self.assertEqual(open(victima).read(), "intacto")

    def test_cifrado_ida_y_vuelta_y_tipo_propio(self):
        ruta, _ = ec.escribir_cifrado(resultado(), FRASE)
        self.assertTrue(ruta.endswith(".csv.cgx"))
        self.assertEqual([f for f in os.listdir(os.path.dirname(ruta)) if f.endswith(".part")], [])
        self.assertNotIn(b"Cliente A", open(ruta, "rb").read())
        abierto = ec.descifrar(ruta, FRASE)
        self.assertEqual(open(abierto, "rb").read(), ec.construir(resultado()))
        with self.assertRaisesRegex(cgx.ErrorCgx, "CSV"):                               # no se toma por una exportación de la base
            with open(ruta, "rb") as f:
                cgx.descifrar_con_frase(f, io.BytesIO(), FRASE, tipo=cgx.TIPO_EXPORT)

    def test_frase_incorrecta_no_deja_nada(self):
        ruta, _ = ec.escribir_cifrado(resultado(), FRASE)
        antes = sorted(os.listdir(os.path.dirname(ruta)))
        with self.assertRaises(cgx.ErrorCgx):
            ec.descifrar(ruta, "otra frase larga distinta 456")
        self.assertEqual(sorted(os.listdir(os.path.dirname(ruta))), antes)

    def test_frase_corta_no_escribe(self):
        with self.assertRaises(cgx.ErrorCgx):
            ec.escribir_cifrado(resultado(), "corta")
        self.assertEqual(os.listdir(ec.carpeta()), [])


class TestCli(ConCarpeta, ConHusoMadrid):
    def setUp(self):
        ConHusoMadrid.setUp(self)
        ConCarpeta.setUp(self)
        self.mete([turno("claude:a", utc("2026-10-12T10:00"), proyecto="=cliente", gi=7, go=0, cr=0, cw=0)])
        self.completa()
        p = mock.patch.object(historico, "cfg", lambda: {"almacen": "activo"})
        p.start()
        self.addCleanup(p.stop)

    def test_claro_por_defecto_con_aviso(self):
        t = Falso()
        self.assertEqual(cli_copias.main(["--exportar-csv", "--desde", "2026-10-12", "--hasta", "2026-10-12", "--agrupa", "proyecto"], t), 0, t.todo)
        self.assertIn("sin cifrar", t.todo)
        archivo = [f for f in os.listdir(ec.carpeta()) if f.endswith(".csv")][0]
        self.assertIn("'=", open(os.path.join(ec.carpeta(), archivo), encoding="utf-8-sig").read())

    def test_cifrado_y_descifrado(self):
        t = Falso(frases=[FRASE, FRASE])
        self.assertEqual(cli_copias.main(["--exportar-csv", "--desde", "2026-10-12", "--hasta", "2026-10-12", "--cifrar"], t), 0, t.todo)
        ruta = [os.path.join(ec.carpeta(), f) for f in os.listdir(ec.carpeta()) if f.endswith(".cgx")][0]
        t2 = Falso(frases=[FRASE])
        self.assertEqual(cli_copias.main(["--descifrar-csv", ruta], t2), 0, t2.todo)
        self.assertTrue([f for f in os.listdir(ec.carpeta()) if f.startswith("descifrado-")])

    def test_frases_distintas_no_exporta(self):
        t = Falso(frases=[FRASE, FRASE + "x"])
        self.assertEqual(cli_copias.main(["--exportar-csv", "--cifrar"], t), 1)
        self.assertEqual(os.listdir(ec.carpeta()), [])

    def test_parametros_malos_dan_un_error_legible(self):
        t = Falso()
        self.assertEqual(cli_copias.main(["--exportar-csv", "--desde", "20261012"], t), 1)
        self.assertIn("AAAA-MM-DD", t.todo)

    def test_con_la_sombra_no_exporta(self):
        with mock.patch.object(historico, "cfg", lambda: {}):
            t = Falso()
            self.assertEqual(cli_copias.main(["--exportar-csv"], t), 1)
            self.assertIn("activo", t.todo)


class TestDescargaDelPanel(ServidorBase, ConCarpeta):
    def setUp(self):
        ServidorBase.setUp(self)
        ConCarpeta.setUp(self)
        self.mete([turno("claude:a", utc("2026-10-12T10:00"), proyecto="=cliente", gi=7, go=0, cr=0, cw=0)])
        self.completa()

    def pide_crudo(self, ruta):
        c = self.http.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        c.request("GET", ruta, headers={"Host": f"127.0.0.1:{self.puerto}", "Cookie": self.cookie})
        r = c.getresponse()
        return r.status, r.read(), r

    def test_descarga_en_memoria_sin_dejar_ficheros_y_auditada(self):
        with self.activo():
            estado, cuerpo, r = self.pide_crudo("/historico.csv?desde=2026-10-12&hasta=2026-10-12&agrupa=proyecto")
        self.assertEqual(estado, 200)
        self.assertTrue(r.getheader("Content-Type").startswith("text/csv"))
        self.assertRegex(r.getheader("Content-Disposition"), r'^attachment; filename="consumo-[0-9a-z.-]+\.csv"$')
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertTrue(cuerpo.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"'=cliente", cuerpo)
        self.assertFalse(os.path.exists(os.path.join(rutas.DATOS, "exportaciones")))
        linea = json.loads(open(os.path.join(rutas.DATOS, "exportaciones.log"), encoding="utf-8").read().splitlines()[0])
        self.assertEqual((linea["origen"], linea["cifrado"]), ("panel", False))

    def test_sin_sesion_no_hay_csv(self):
        c = self.http.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        c.request("GET", "/historico.csv?desde=2026-10-12&hasta=2026-10-12", headers={"Host": f"127.0.0.1:{self.puerto}"})
        self.assertEqual(c.getresponse().status, 401)

    def test_origen_ajeno_no_descarga(self):
        c = self.http.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        with self.activo():
            c.request("GET", "/historico.csv?desde=2026-10-12&hasta=2026-10-12",
                      headers={"Host": f"127.0.0.1:{self.puerto}", "Cookie": self.cookie, "Sec-Fetch-Site": "cross-site"})
            self.assertEqual(c.getresponse().status, 403)

    def test_parametros_malos_y_sombra(self):
        with self.activo():
            self.assertEqual(self.pide_crudo("/historico.csv?desde=2026-1-1&hasta=2026-10-12")[0], 400)
            self.assertEqual(self.pide_crudo("/historico.csv?desde=2026-10-12&hasta=2026-10-12&x=1")[0], 400)
        estado, cuerpo, _ = self.pide_crudo("/historico.csv?desde=2026-10-12&hasta=2026-10-12")
        self.assertEqual(json.loads(cuerpo)["disponible"], False)


if __name__ == "__main__":
    unittest.main()
