"""Almacén SQLite: esquema y PRAGMA por conexión, escritura monótona (mismo resultado en cualquier orden), anomalías,
agregado horario coherente, escritor único por latido, solo-lectura, copias y rutas no fiables."""
import itertools
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, plataforma, rutas  # noqa: E402

H0 = 1_790_000_000 - 1_790_000_000 % 3600          # una hora redonda UTC


def turno(clave="claude:msg_1", hora=H0 + 100, modelo="claude-sonnet-5", gi=10, go=5, cr=100, cw=20, **extra):
    t = {"clave": clave, "hora_utc": hora, "agente": "claude", "proveedor": "anthropic", "modelo": modelo,
         "gi": gi, "go": go, "cr": cr, "cw": cw, "proyecto_raw": "proy"}
    t.update(extra)
    return t


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-almacen-")
        self.ruta = os.path.join(self.dir, "datos", "consumo.sqlite")
        self.con = almacen.abrir(self.ruta)
        self.esp = almacen.espacio(self.con)
        self.fue = almacen.fuente(self.con, self.esp, "claude", "sesion-1")
        self.assertTrue(almacen.adquirir_escritura(self.con, "barra"))

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def ingiere(self, turnos, quien="barra", ahora=None):
        return almacen.ingerir_turnos(self.con, quien, self.esp, self.fue, turnos, ahora=ahora)

    def filas(self):
        return {f["clave"]: (f["gi"], f["go"], f["cr"], f["cw"], f["hora_utc"], f["modelo"]) for f in self.con.execute("SELECT * FROM turnos")}

    def suma_horas(self):
        f = self.con.execute("SELECT COALESCE(SUM(gi),0) a, COALESCE(SUM(go),0) b, COALESCE(SUM(cr),0) c, COALESCE(SUM(cw),0) d, COALESCE(SUM(turnos),0) n FROM horas").fetchone()
        return tuple(f)

    def suma_turnos(self):
        f = self.con.execute("SELECT COALESCE(SUM(gi),0), COALESCE(SUM(go),0), COALESCE(SUM(cr),0), COALESCE(SUM(cw),0), COUNT(*) FROM turnos").fetchone()
        return tuple(f)


class ConexionTest(Base):
    def test_pragma_en_cada_conexion(self):
        for solo in (False, True):
            con = almacen.abrir(self.ruta, solo_lectura=solo)
            self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1, solo)
            self.assertEqual(con.execute("PRAGMA secure_delete").fetchone()[0], 1, solo)
            con.close()
        self.assertEqual(self.con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.con.execute("PRAGMA user_version").fetchone()[0], almacen.VERSION_ESQUEMA)

    def test_solo_lectura_no_puede_escribir(self):
        con = almacen.abrir(self.ruta, solo_lectura=True)
        with self.assertRaises(sqlite3.OperationalError):
            con.execute("INSERT INTO espacios(nombre) VALUES('x')")
        con.close()

    def test_solo_lectura_sin_base_falla_sin_crearla(self):
        with self.assertRaises(FileNotFoundError):
            almacen.abrir(os.path.join(self.dir, "no.sqlite"), solo_lectura=True)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "no.sqlite")))

    @unittest.skipIf(os.name == "nt", "chmod no aplica en Windows")
    def test_permisos_privados(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.ruta).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.ruta)).st_mode), 0o700)

    def test_ruta_en_nube_o_red_rechazada(self):
        for ruta in ("/Users/x/Library/Mobile Documents/com~apple~CloudDocs/c.sqlite", "/Users/x/Dropbox/c.sqlite", "/Users/x/OneDrive/c.sqlite"):
            with self.assertRaises(almacen.ErrorAlmacen):
                almacen.abrir(ruta)

    def test_sqlite_antiguo_se_rechaza(self):
        with mock.patch.object(sqlite3, "sqlite_version_info", (3, 31, 1)), mock.patch.object(sqlite3, "sqlite_version", "3.31.1"):
            with self.assertRaises(almacen.ErrorAlmacen):
                almacen.abrir(os.path.join(self.dir, "otra.sqlite"))

    def test_checkout_de_desarrollo_no_escribe_en_los_datos_reales(self):
        with mock.patch.object(rutas, "RAIZ", "/tmp/checkout"), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COSTGUARD_DATOS", None)
            with self.assertRaises(almacen.ErrorAlmacen):
                almacen.abrir()

    def test_base_de_version_mas_nueva_se_rechaza(self):
        self.con.execute(f"PRAGMA user_version={almacen.VERSION_ESQUEMA + 1}")
        with self.assertRaises(almacen.ErrorAlmacen):
            almacen.abrir(self.ruta)

    def test_version_minima_del_escritor(self):
        self.con.execute("UPDATE meta SET valor='99' WHERE clave='version_minima_escritor'")
        with self.assertRaises(almacen.ErrorAlmacen):
            almacen.abrir(self.ruta)


class EscrituraMonotonaTest(Base):
    def test_releer_no_cambia_nada(self):
        t = [turno("claude:a"), turno("claude:b", gi=7)]
        self.assertEqual(self.ingiere(t)["nuevos"], 2)
        antes = (self.filas(), self.suma_horas())
        r = self.ingiere(t)
        self.assertEqual((r["nuevos"], r["subidos"], r["iguales"]), (0, 0, 2))
        self.assertEqual((self.filas(), self.suma_horas()), antes)

    def test_el_orden_de_lectura_no_cambia_el_resultado(self):
        versiones = [turno(gi=10, go=5, cr=100, cw=20), turno(gi=10, go=321, cr=100, cw=20), turno(gi=12, go=321, cr=100, cw=20)]
        resultados = set()
        for orden in itertools.permutations(versiones):
            self.con.execute("DELETE FROM turnos"); self.con.execute("DELETE FROM horas"); self.con.execute("DELETE FROM anomalias")
            for v in orden:
                self.ingiere([v])
            resultados.add((tuple(sorted(self.filas().items())), self.suma_horas()))
        self.assertEqual(len(resultados), 1)
        (filas, horas), = resultados
        self.assertEqual(dict(filas)["claude:msg_1"][:4], (12, 321, 100, 20))

    def test_un_contador_que_baja_no_se_aplica_y_queda_anotado(self):
        self.ingiere([turno(go=300)])
        r = self.ingiere([turno(go=6)])
        self.assertEqual(self.filas()["claude:msg_1"][1], 300)
        self.assertEqual(r["anomalias"], 1)
        self.assertEqual(almacen.anomalias(self.con)[0]["tipo"], "contador_baja")

    def test_no_se_mueve_el_modelo_ni_la_hora(self):
        self.ingiere([turno()])
        r = self.ingiere([turno(modelo="otro-modelo", hora=H0 + 86400 * 400)])
        f = self.filas()["claude:msg_1"]
        self.assertEqual((f[4], f[5]), (H0 + 100, "claude-sonnet-5"))
        self.assertEqual({a["tipo"] for a in almacen.anomalias(self.con)}, {"cambio_hora_utc", "cambio_modelo"})
        self.assertEqual(r["anomalias"], 2)

    def test_cifras_fuera_de_rango_se_descartan(self):
        malos = [turno("claude:x1", gi=-1), turno("claude:x2", go=10 ** 13), turno("claude:x3", hora=5), turno("claude:x4", modelo=""),
                 turno("claude:x5", gi=True), turno("claude:x6", gi=1.5), {"clave": "x"}, "no", None, turno("ab")]
        r = self.ingiere(malos)
        self.assertEqual((r["nuevos"], r["anomalias"]), (0, len(malos)))
        self.assertEqual(self.filas(), {})

    def test_una_linea_inyectada_no_pone_a_cero_ni_mueve_un_turno_real(self):
        self.ingiere([turno(gi=10, go=300)])
        self.ingiere([turno(gi=0, go=0, cr=0, cw=0, hora=H0 + 5 * 86400)])
        self.assertEqual(self.filas()["claude:msg_1"], (10, 300, 100, 20, H0 + 100, "claude-sonnet-5"))

    def test_el_agregado_horario_siempre_cuadra_con_los_turnos(self):
        self.ingiere([turno("claude:a", hora=H0 + 10), turno("claude:b", hora=H0 + 3700), turno("claude:c", hora=H0 + 3800, modelo="m2")])
        self.ingiere([turno("claude:a", hora=H0 + 10, go=50), turno("claude:d", hora=H0 + 7300)])
        self.assertEqual(self.suma_horas(), self.suma_turnos())
        t = almacen.totales(self.con, self.esp, H0, H0 + 3600, agrupar=("modelo",))
        self.assertEqual(sum(f["turnos"] for f in t), 1)

    def test_lote_con_error_se_revierte_entero(self):
        class Raro(dict):
            def get(self, *a):
                raise RuntimeError("fallo a mitad")
        with self.assertRaises(RuntimeError):
            self.ingiere([turno("claude:ok"), Raro(turno("claude:mal"))])
        self.assertEqual(self.filas(), {})
        self.assertEqual(self.suma_horas()[4], 0)

    def test_inundacion_de_anomalias_se_acota(self):
        self.ingiere([turno(f"claude:m{i}", gi=-1) for i in range(200)])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM anomalias").fetchone()[0], almacen.MAX_ANOMALIAS_POR_LOTE)
        self.assertEqual(int(almacen.meta(self.con, "anomalias_omitidas")), 150)


class EscritorUnicoTest(Base):
    def test_solo_escribe_el_escritor_vigente(self):
        with self.assertRaises(almacen.SinEscritura):
            self.ingiere([turno()], quien="cli")
        self.assertEqual(self.filas(), {})

    def test_otro_no_toma_el_puesto_mientras_hay_latido(self):
        self.assertFalse(almacen.adquirir_escritura(self.con, "cli"))
        self.assertTrue(almacen.adquirir_escritura(self.con, "barra"))          # el dueño se renueva

    def test_el_cli_toma_el_relevo_sin_latido(self):
        pasado = int(almacen.meta(self.con, "escritor_latido")) + almacen.LATIDO_TTL_S
        self.assertTrue(almacen.adquirir_escritura(self.con, "cli", ahora=pasado))
        with self.assertRaises(almacen.SinEscritura):
            self.ingiere([turno()], quien="barra", ahora=pasado)
        self.assertEqual(self.ingiere([turno()], quien="cli", ahora=pasado)["nuevos"], 1)

    def test_el_escritor_caducado_no_puede_seguir_escribiendo(self):
        tarde = int(almacen.meta(self.con, "escritor_latido")) + almacen.LATIDO_TTL_S + 1
        with self.assertRaises(almacen.SinEscritura):
            self.ingiere([turno()], quien="barra", ahora=tarde)

    def test_soltar_deja_el_puesto_libre(self):
        almacen.soltar_escritura(self.con, "barra")
        self.assertTrue(almacen.adquirir_escritura(self.con, "cli"))

    def test_el_lector_en_otra_conexion_no_escribe_pero_ve_lo_escrito(self):
        self.ingiere([turno()])
        lector = almacen.abrir(self.ruta, solo_lectura=True)
        self.assertEqual(lector.execute("SELECT COUNT(*) FROM turnos").fetchone()[0], 1)
        with self.assertRaises(sqlite3.OperationalError):
            lector.execute("UPDATE meta SET valor='x'")
        lector.close()


class ConsultaTest(Base):
    def test_agrupacion_solo_con_columnas_permitidas(self):
        for mala in (("modelo; DROP TABLE turnos",), ("gi",), ("1=1 --",)):
            with self.assertRaises(ValueError):
                almacen.totales(self.con, self.esp, 0, 2 ** 40, agrupar=mala)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='turnos'").fetchone()[0], 1)

    def test_nombres_hostiles_se_guardan_tal_cual_sin_inyeccion(self):
        raro = "x'); DROP TABLE turnos; --"
        self.ingiere([turno("claude:h", modelo=raro, proyecto_raw=raro)])
        self.assertEqual(self.filas()["claude:h"][5], raro)
        self.assertEqual(almacen.totales(self.con, self.esp, 0, 2 ** 40, agrupar=("modelo",))[0]["modelo"], raro)

    def test_ventana_semiabierta(self):
        self.ingiere([turno("claude:a", hora=H0 + 5), turno("claude:b", hora=H0 + 3600 + 5)])
        self.assertEqual(almacen.totales(self.con, self.esp, H0, H0 + 3600)[0]["turnos"], 1)
        self.assertEqual(almacen.totales(self.con, self.esp, H0, H0 + 7200)[0]["turnos"], 2)


class CopiaTest(Base):
    def test_copia_consistente_privada_y_rotada(self):
        self.ingiere([turno()])
        d = os.path.join(self.dir, "copias")
        with mock.patch("time.strftime", side_effect=[f"20260101-00000{i}" for i in range(8)]):
            rutas_ = [almacen.copia(self.con, d, conservar=3) for _ in range(7)]
        quedan = sorted(os.listdir(d))
        self.assertEqual(len(quedan), 3)
        con = sqlite3.connect(rutas_[-1])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM turnos").fetchone()[0], 1)
        con.close()
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(rutas_[-1]).st_mode), 0o600)

    def test_mantenimiento_trunca_el_wal(self):
        self.ingiere([turno(f"claude:w{i}") for i in range(200)])
        almacen.mantenimiento(self.con)
        self.assertEqual(os.path.getsize(self.ruta + "-wal"), 0)


class PlataformaTest(unittest.TestCase):
    def test_directorio_de_datos_por_sistema(self):
        with mock.patch.dict(os.environ, {"COSTGUARD_DATOS": ""}):
            os.environ.pop("COSTGUARD_DATOS")
            with mock.patch.object(plataforma.sys, "platform", "darwin"), mock.patch.object(plataforma.os, "name", "posix"):
                self.assertTrue(plataforma.dir_datos().endswith("Library/Application Support/CostGuard"))
            with mock.patch.object(plataforma.sys, "platform", "linux"), mock.patch.object(plataforma.os, "name", "posix"), \
                    mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/xdg"}):
                self.assertEqual(plataforma.dir_datos(), "/xdg/costguard")
            with mock.patch.object(plataforma.os, "name", "nt"), mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:/L"}):
                self.assertTrue(plataforma.dir_datos().replace("\\", "/").endswith("L/CostGuard"))

    def test_costguard_datos_manda(self):
        with mock.patch.dict(os.environ, {"COSTGUARD_DATOS": "/tmp/mis-datos"}):
            self.assertEqual(plataforma.dir_datos(), os.path.abspath("/tmp/mis-datos"))


if __name__ == "__main__":
    unittest.main()
