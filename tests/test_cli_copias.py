"""CLI de copias: la frase solo por terminal real, una clave privada nunca por argumento, flujo completo generar -> copiar ->
probar -> importar, exportación con frase, y ni frases ni claves privadas en ficheros de la app."""
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, cli_copias, rutas  # noqa: E402
from infobar.config_y_estado import cfg, guardar_config  # noqa: E402

try:
    import cryptography  # noqa: F401
    HAY = True
except ImportError:
    HAY = False

FRASE = "frase larga de prueba 123"


class Falso(cli_copias.Terminal):
    def __init__(self, textos=(), frases=()):
        self.textos, self.frases, self.salida = list(textos), list(frases), []

    def frase(self, mensaje=""):
        return self.frases.pop(0)

    def texto(self, mensaje):
        return self.textos.pop(0)

    def escribe(self, texto=""):
        self.salida.append(texto)

    @property
    def todo(self):
        return "\n".join(self.salida)


def llena_base(ruta):
    con = almacen.abrir(ruta)
    q = almacen.identidad("test")
    almacen.adquirir_escritura(con, q)
    esp = almacen.espacio(con, q)
    fid = almacen.fuente(con, q, esp, "claude", "f")
    almacen.ingerir_turnos(con, q, esp, fid, [{"clave": f"claude:m{i}", "hora_utc": 1_790_000_000 + i, "agente": "claude", "proveedor": "",
                                                "modelo": "m", "gi": 1, "go": 2, "cr": 3, "cw": 4, "proyecto_raw": "p"} for i in range(30)])
    almacen.soltar_escritura(con, q)
    con.close()


@unittest.skipUnless(HAY, "falta cryptography")
class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-cli-")
        self.ant = (rutas.DATOS, rutas.ALMACEN, rutas.COPIAS)
        rutas.DATOS = os.path.join(self.dir, "datos")
        rutas.ALMACEN = os.path.join(rutas.DATOS, "consumo.sqlite")
        rutas.COPIAS = os.path.join(rutas.DATOS, "copias")
        llena_base(rutas.ALMACEN)
        self.fuera = os.path.join(self.dir, "extraible")
        os.makedirs(self.fuera)
        self.config_ant = cfg().get("copias")
        guardar_config({"copias": {"carpeta": os.path.join(self.dir, "cifradas"), "destinatario": [], "firmas_publicas": []}})

    def tearDown(self):
        rutas.DATOS, rutas.ALMACEN, rutas.COPIAS = self.ant
        guardar_config({"copias": self.config_ant or {}})
        shutil.rmtree(self.dir, ignore_errors=True)


class TestSeguridadDeEntrada(Base):
    def test_clave_privada_por_argumento_se_rechaza(self):
        t = Falso()
        self.assertEqual(cli_copias.main(["--importar", "x.cgx", "CGX-SECRET-KEY-1-abc"], t), 1)
        self.assertIn("línea de órdenes", t.todo)
        self.assertNotIn("abc", t.todo)

    def test_sin_terminal_real_no_pide_la_frase(self):
        destino = os.path.join(self.dir, "e.cgx")
        t = cli_copias.Terminal()
        t.escribe = lambda *a: None
        with mock.patch.object(sys.stdin, "isatty", return_value=False), mock.patch("getpass.getpass", side_effect=AssertionError("no debe pedirla")):
            self.assertEqual(cli_copias.main(["--exportar", destino], t), 1)
        self.assertFalse(os.path.exists(destino))

    def test_orden_desconocida(self):
        self.assertEqual(cli_copias.main(["--nada"], Falso()), 1)


class TestExportarImportar(Base):
    def test_exportar_con_frase_e_importar(self):
        destino = os.path.join(self.dir, "e.cgx")
        t = Falso(frases=[FRASE, FRASE])
        self.assertEqual(cli_copias.main(["--exportar", destino], t), 0, t.todo)
        self.assertTrue(os.path.exists(destino))
        os.remove(rutas.ALMACEN)
        for s in ("-wal", "-shm"):
            if os.path.exists(rutas.ALMACEN + s):
                os.remove(rutas.ALMACEN + s)
        t2 = Falso(textos=["SI"], frases=[FRASE])
        self.assertEqual(cli_copias.main(["--importar", destino], t2), 0, t2.todo)
        self.assertIn("30 turnos", t2.todo)
        con = almacen.abrir(rutas.ALMACEN, solo_lectura=True)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM turnos").fetchone()[0], 30)
        con.close()

    def test_frases_distintas_no_exportan(self):
        destino = os.path.join(self.dir, "e.cgx")
        self.assertEqual(cli_copias.main(["--exportar", destino], Falso(frases=[FRASE, FRASE + "x"])), 1)
        self.assertFalse(os.path.exists(destino))

    def test_frase_debil_no_exporta(self):
        destino = os.path.join(self.dir, "e.cgx")
        self.assertEqual(cli_copias.main(["--exportar", destino], Falso(frases=["corta", "corta"])), 1)
        self.assertFalse(os.path.exists(destino))

    def test_no_sobrescribe_un_fichero(self):
        destino = os.path.join(self.dir, "e.cgx")
        open(destino, "w").close()
        self.assertEqual(cli_copias.main(["--exportar", destino], Falso(frases=[FRASE, FRASE])), 1)

    def test_frase_equivocada_al_importar(self):
        destino = os.path.join(self.dir, "e.cgx")
        cli_copias.main(["--exportar", destino], Falso(frases=[FRASE, FRASE]))
        t = Falso(textos=["SI"], frases=["otra frase distinta 99"])
        self.assertEqual(cli_copias.main(["--importar", destino, "--forzar"], t), 1)
        self.assertIn("incorrecta", t.todo)


class TestCopiasAutomaticas(Base):
    def test_flujo_completo_generar_copiar_probar_importar(self):
        clave = os.path.join(self.fuera, "clave.txt")
        t = Falso(textos=["SI"])
        self.assertEqual(cli_copias.main(["--claves-generar", clave], t), 0, t.todo)
        self.assertIn("PAQUETE DE RESTAURACIÓN", t.todo)
        c = cfg()["copias"]
        self.assertEqual(len(c["destinatario"]), 1)
        self.assertEqual(len(c["firmas_publicas"]), 1)
        t2 = Falso()
        self.assertEqual(cli_copias.main(["--copia-ahora"], t2), 0, t2.todo)
        carpeta = c["carpeta"]
        copia = [f for f in os.listdir(carpeta) if f.endswith(".cgx")][0]
        t3 = Falso()
        self.assertEqual(cli_copias.main(["--probar-restauracion", os.path.join(carpeta, copia), "--clave-privada", clave], t3), 0, t3.todo)
        self.assertIn("30 turnos", t3.todo)
        # restaurar sobre una base distinta (con forzar) y comprobar que queda lo de la copia
        os.remove(rutas.ALMACEN)
        for s in ("-wal", "-shm"):
            if os.path.exists(rutas.ALMACEN + s):
                os.remove(rutas.ALMACEN + s)
        t4 = Falso(textos=["SI"])
        self.assertEqual(cli_copias.main(["--importar", os.path.join(carpeta, copia), "--clave-privada", clave], t4), 0, t4.todo)
        self.assertIn("para la clave", t4.todo)

    def test_copia_sin_destinatario_explica_que_falta(self):
        t = Falso()
        self.assertEqual(cli_copias.main(["--copia-ahora"], t), 1)
        self.assertIn("claves-generar", t.todo)

    def test_generar_exige_confirmar(self):
        clave = os.path.join(self.fuera, "clave.txt")
        self.assertEqual(cli_copias.main(["--claves-generar", clave], Falso(textos=["no"])), 1)
        self.assertFalse(os.path.exists(clave))
        self.assertEqual(cfg()["copias"]["destinatario"], [])

    def test_ni_la_privada_ni_la_frase_quedan_en_ficheros_de_la_app(self):
        clave = os.path.join(self.fuera, "clave.cgx")
        cli_copias.main(["--claves-generar", clave, "--con-frase"], Falso(textos=["SI"], frases=[FRASE, FRASE]))
        cli_copias.main(["--copia-ahora"], Falso())
        destino = os.path.join(self.dir, "e.cgx")
        cli_copias.main(["--exportar", destino], Falso(frases=[FRASE + "z", FRASE + "z"]))
        with open(clave, "rb") as f:
            self.assertTrue(f.read().startswith(b"CGX1"))                            # la privada protegida no está en claro
        for raiz in (rutas.DATOS, self.dir, os.path.dirname(rutas.CONFIG), os.path.dirname(rutas.LOG)):
            for d, _ds, fs in os.walk(raiz):
                for f in fs:
                    ruta = os.path.join(d, f)
                    if ruta == clave or ruta.endswith(".sqlite") or "clave_firma" in ruta:
                        continue
                    try:
                        with open(ruta, "rb") as h:
                            dato = h.read()
                    except OSError:
                        continue
                    self.assertNotIn(b"CGX-SECRET-KEY", dato, ruta)
                    self.assertNotIn(FRASE.encode(), dato, ruta)

    def test_clave_privada_dentro_de_la_carpeta_de_copias_se_rechaza(self):
        carpeta = cfg()["copias"]["carpeta"]
        os.makedirs(carpeta)
        self.assertEqual(cli_copias.main(["--claves-generar", os.path.join(carpeta, "k.txt")], Falso(textos=["SI"])), 1)
        self.assertEqual(os.listdir(carpeta), [])


class TestAutoprueba(Base):
    def test_autoprueba_pasa_y_no_toca_los_datos(self):
        antes = sorted(os.listdir(rutas.DATOS))
        t = Falso()
        self.assertEqual(cli_copias.main(["--autoprueba-copias"], t), 0, t.todo)
        self.assertIn("todo correcto", t.todo)
        self.assertEqual(sorted(os.listdir(rutas.DATOS)), antes)


if __name__ == "__main__":
    unittest.main()
