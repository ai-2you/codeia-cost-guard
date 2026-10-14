"""Lo que hace falta para que el ciclo de servidores y la retención funcionen igual en macOS, Linux y Windows: cerrojo, matar árboles de
procesos, exclusión de copias del sistema, poda de IPs de la caché, purga horaria de imágenes y el modo sin interfaz."""
import ast
import glob
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import permisos, plataforma, rutas, servidores_ciclo, trabajador_barra  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def vivo(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class TestNadaDeSoloPosixAlImportar(unittest.TestCase):
    def test_ningun_modulo_importa_fcntl_ni_resource_arriba(self):
        for f in glob.glob(os.path.join(RAIZ, "infobar", "*.py")) + [os.path.join(RAIZ, "costbar.py")]:
            arbol = ast.parse(open(f, encoding="utf-8").read())
            for nodo in arbol.body:                                   # solo el nivel del módulo: dentro de funciones vale
                nombres = [a.name for a in nodo.names] if isinstance(nodo, ast.Import) else ([nodo.module] if isinstance(nodo, ast.ImportFrom) else [])
                for n in nombres:
                    self.assertNotIn((n or "").split(".")[0], ("fcntl", "resource", "pwd", "grp", "termios"), f)

    def test_killpg_y_sesiones_nuevas_solo_en_plataforma(self):
        for f in glob.glob(os.path.join(RAIZ, "infobar", "*.py")) + [os.path.join(RAIZ, "costbar.py")]:
            if f.endswith("plataforma.py"):
                continue
            texto = open(f, encoding="utf-8").read()
            self.assertNotIn("os.killpg", texto, f)
            self.assertNotIn("start_new_session", texto, f)


class TestProcesos(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX")
    def test_matar_arbol_mata_tambien_a_los_nietos(self):
        codigo = textwrap.dedent("""
            import subprocess, sys, time
            h = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            print(h.pid, flush=True)
            time.sleep(60)
        """)
        p = subprocess.Popen([sys.executable, "-c", codigo], stdout=subprocess.PIPE, text=True, **plataforma.grupo_nuevo_kwargs())
        nieto = int(p.stdout.readline())
        self.assertTrue(vivo(nieto))
        plataforma.matar_arbol(p)
        p.wait(timeout=10)
        for _ in range(50):
            if not vivo(nieto):
                break
            time.sleep(0.1)
        self.assertFalse(vivo(nieto))

    def test_matar_arbol_de_algo_que_ya_termino_no_falla(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"], **plataforma.grupo_nuevo_kwargs())
        p.wait()
        plataforma.matar_arbol(p)

    def test_argumentos_de_grupo_segun_el_sistema(self):
        with mock.patch.object(plataforma.os, "name", "posix"):
            self.assertEqual(plataforma.grupo_nuevo_kwargs(), {"start_new_session": True})
        with mock.patch.object(plataforma.os, "name", "nt"), mock.patch.object(plataforma.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True):
            self.assertEqual(plataforma.grupo_nuevo_kwargs(), {"creationflags": 512})

    def test_en_windows_se_usa_taskkill_con_arbol(self):
        falso = mock.Mock(pid=4242)
        with mock.patch.object(plataforma.os, "name", "nt"), mock.patch.object(plataforma.subprocess, "run") as run:
            plataforma.matar_arbol(falso)
        self.assertEqual(run.call_args[0][0], ["taskkill", "/T", "/F", "/PID", "4242"])
        falso.kill.assert_called_once()


class TestCerrojo(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-lock-")
        self.ruta = os.path.join(self.dir, "x.lock")

    @unittest.skipIf(os.name == "nt", "POSIX")
    def test_exclusion_mutua_entre_dos_aperturas(self):
        orden, dentro = [], threading.Event()

        def primero():
            with open(self.ruta, "a+") as f, plataforma.bloqueo_de_fichero(f):
                dentro.set()
                time.sleep(0.4)
                orden.append("A")

        def segundo():
            dentro.wait()
            with open(self.ruta, "a+") as f, plataforma.bloqueo_de_fichero(f):
                orden.append("B")

        hilos = [threading.Thread(target=primero), threading.Thread(target=segundo)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(10)
        self.assertEqual(orden, ["A", "B"])

    def falso_msvcrt(self, fallos):
        m = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, llamadas=[], pendientes=fallos)

        def locking(fd, modo, n):
            m.llamadas.append((modo, n))
            if modo == m.LK_NBLCK and m.pendientes > 0:
                m.pendientes -= 1
                raise OSError("ocupado")
        m.locking = locking
        return m

    def test_en_windows_reintenta_y_suelta(self):
        m = self.falso_msvcrt(fallos=2)
        with mock.patch.object(plataforma.os, "name", "nt"), mock.patch.dict(sys.modules, {"msvcrt": m}), open(self.ruta, "a+") as f:
            with plataforma.bloqueo_de_fichero(f, espera_s=5):
                pass
        self.assertEqual([c[0] for c in m.llamadas], [2, 2, 2, 0])         # dos fallos, éxito y liberación

    def test_en_windows_no_espera_para_siempre(self):
        m = self.falso_msvcrt(fallos=10 ** 6)
        with mock.patch.object(plataforma.os, "name", "nt"), mock.patch.dict(sys.modules, {"msvcrt": m}), open(self.ruta, "a+") as f:
            with self.assertRaises(TimeoutError):
                with plataforma.bloqueo_de_fichero(f, espera_s=0.2):
                    self.fail("no debería entrar")

    def test_bloqueo_de_config_crea_el_byte_que_windows_necesita(self):
        from infobar import config_y_estado
        m = self.falso_msvcrt(fallos=0)
        ruta = os.path.join(self.dir, "datos.json")
        with mock.patch.object(plataforma.os, "name", "nt"), mock.patch.object(config_y_estado.os, "name", "nt"), \
                mock.patch.dict(sys.modules, {"msvcrt": m}):
            with config_y_estado._bloqueo(ruta):
                pass
        self.assertEqual(open(ruta + ".lock", "rb").read(), b"\0")


class TestExcluirDeCopias(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-excl-")

    def test_ruta_inexistente(self):
        self.assertFalse(plataforma.excluir_de_copias_del_sistema(os.path.join(self.dir, "no")))

    def test_macos_ya_excluida_no_vuelve_a_pedirlo(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(plataforma.subprocess, "run",
                                                                              return_value=mock.Mock(stdout="[Excluded]  /x", returncode=0)) as run:
            self.assertTrue(plataforma.excluir_de_copias_del_sistema(self.dir))
        self.assertEqual(run.call_count, 1)

    def test_macos_la_excluye_si_no_lo_estaba(self):
        respuestas = [mock.Mock(stdout="[Included]  /x", returncode=0), mock.Mock(stdout="", returncode=0)]
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(plataforma.subprocess, "run", side_effect=respuestas) as run:
            self.assertTrue(plataforma.excluir_de_copias_del_sistema(self.dir))
        self.assertEqual(run.call_args[0][0][:2], ["tmutil", "addexclusion"])

    def test_macos_si_tmutil_falla_dice_que_no(self):
        respuestas = [mock.Mock(stdout="[Included]", returncode=0), mock.Mock(stdout="", returncode=1)]
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(plataforma.subprocess, "run", side_effect=respuestas):
            self.assertFalse(plataforma.excluir_de_copias_del_sistema(self.dir))
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(plataforma.subprocess, "run", side_effect=OSError("sin tmutil")):
            self.assertFalse(plataforma.excluir_de_copias_del_sistema(self.dir))

    def test_windows_marca_no_indexar(self):
        kernel = mock.Mock()
        kernel.GetFileAttributesW.return_value = 0x10
        kernel.SetFileAttributesW.return_value = 1
        falso_ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(kernel32=kernel))
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(plataforma.os, "name", "nt"), mock.patch.dict(sys.modules, {"ctypes": falso_ctypes}):
            self.assertTrue(plataforma.excluir_de_copias_del_sistema(self.dir))
        kernel.SetFileAttributesW.assert_called_once_with(self.dir, 0x10 | 0x2000)


class TestRetencionFueraDeLaBase(unittest.TestCase):
    def entrada(self, ok_en):
        return {"ok_en": ok_en, "estado": "verde", "datos": {"intentos": {"total": 9, "hora": 1, "top": [["203.0.113.9", 5], ["198.51.100.7", 2]]},
                                                            "sesiones": [{"usuario": "root", "desde": "203.0.113.50"}]}}

    def test_pasadas_48_horas_se_quitan_las_ips_y_se_conservan_los_recuentos(self):
        ahora = 10 ** 9
        e = servidores_ciclo.podar_ips(self.entrada(ahora - 49 * 3600), ahora)
        self.assertEqual(e["datos"]["intentos"]["top"], [])
        self.assertEqual((e["datos"]["intentos"]["total"], e["datos"]["intentos"]["hora"]), (9, 1))
        self.assertEqual(e["datos"]["sesiones"][0], {"usuario": "root", "desde": ""})

    def test_dentro_de_las_48_horas_no_se_toca(self):
        ahora = 10 ** 9
        e = servidores_ciclo.podar_ips(self.entrada(ahora - 47 * 3600), ahora)
        self.assertEqual(e["datos"]["intentos"]["top"][0][0], "203.0.113.9")

    def test_entradas_raras_no_rompen(self):
        for rara in ({}, {"ok_en": 1}, {"ok_en": 1, "datos": None}, {"ok_en": 1, "datos": {"intentos": 5, "sesiones": [7]}}):
            servidores_ciclo.podar_ips(rara, 10 ** 9)

    def test_guardar_aplica_la_poda_al_escribir_la_cache(self):
        ahora = 10 ** 9
        servidores = [{"nombre": "uno"}]
        cache = {"uno": self.entrada(ahora - 60 * 3600)}
        out = servidores_ciclo._guardar(servidores, cache, {}, ahora)
        self.assertEqual(out["servidores"]["uno"]["datos"]["intentos"]["top"], [])
        import json
        en_disco = json.load(open(rutas.SERVIDORES))
        self.assertNotIn("203.0.113", json.dumps(en_disco))

    def test_purgar_render_borra_lo_viejo_tambien_en_windows(self):
        render = os.path.join(os.path.dirname(rutas.LOG), "render")
        os.makedirs(render, exist_ok=True)
        viejo, nuevo = os.path.join(render, "viejo.png"), os.path.join(render, "nuevo.png")
        for f in (viejo, nuevo):
            open(f, "wb").write(b"x")
        pasado = time.time() - permisos.RENDER_CADUCA_S - 60
        os.utime(viejo, (pasado, pasado))
        with mock.patch.object(permisos.os, "name", "nt"), mock.patch.object(permisos, "_modo") as modo:
            permisos.purgar_render()
        self.assertFalse(os.path.exists(viejo))
        self.assertTrue(os.path.exists(nuevo))
        modo.assert_not_called()                        # en Windows los permisos van por la carpeta del usuario, no por chmod

    def test_excluir_datos_sensibles_cuenta_lo_conseguido(self):
        with mock.patch.object(plataforma, "excluir_de_copias_del_sistema", side_effect=[True, False]) as ex:
            self.assertEqual(permisos.excluir_datos_sensibles(), 1)
        self.assertEqual({c[0][0] for c in ex.call_args_list}, {rutas.DATOS, os.path.dirname(rutas.LOG)})


class TestModoSinInterfaz(unittest.TestCase):
    def test_el_trabajador_sin_pintar_no_llama_a_chrome(self):
        t = trabajador_barra.Trabajador(lambda *a: None, pintar=False)
        with mock.patch.object(trabajador_barra.desplegable_png, "renderizar", side_effect=AssertionError("no debe renderizar")):
            self.assertFalse(t._pinta({}, {}))

    def test_el_demonio_arranca_trabajador_ingesta_y_copias(self):
        import costbar
        creados = []

        class Falso:
            def __init__(self, *a, **k):
                creados.append((type(self).__name__, k))
            def start(self):
                pass

        class FalsoTrabajador(Falso):
            pass

        class FalsoIngesta(Falso):
            pass

        class FalsoCopias(Falso):
            pass

        with mock.patch.object(costbar, "servir"), mock.patch.object(trabajador_barra, "Trabajador", FalsoTrabajador), \
                mock.patch("infobar.ingestor.HiloIngesta", FalsoIngesta), mock.patch("infobar.hilo_copias.HiloCopias", FalsoCopias), \
                mock.patch.object(permisos, "excluir_datos_sensibles"), mock.patch.object(costbar, "cfg", lambda: {"almacen": "activo"}):
            hilos = costbar.demonio(esperar=False)
        self.assertEqual([c[0] for c in creados], ["FalsoTrabajador", "FalsoIngesta", "FalsoCopias"])
        self.assertEqual(len(hilos), 3)

    def test_con_el_almacen_desactivado_solo_el_trabajador(self):
        import costbar
        arrancados = []

        class FalsoTrabajador:
            def __init__(self, entregar, pintar=True):
                self.pintar = pintar
            def start(self):
                arrancados.append(self.pintar)

        with mock.patch.object(costbar, "servir"), mock.patch.object(trabajador_barra, "Trabajador", FalsoTrabajador), \
                mock.patch.object(permisos, "excluir_datos_sensibles"), mock.patch.object(costbar, "cfg", lambda: {"almacen": "desactivado"}):
            hilos = costbar.demonio(esperar=False)
        self.assertEqual((len(hilos), arrancados), (1, [False]))        # sin interfaz: el trabajador no pinta


if __name__ == "__main__":
    unittest.main()
