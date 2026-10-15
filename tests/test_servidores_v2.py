"""Protocolo v2 del servidor: marcos de sección, parser que no se deja reabrir, intentos con histograma por hora natural, el script real
ejecutado contra un journalctl simulado, y el histograma en la base (solo sube, migración 2, datos hostiles)."""
import os
import shutil
import stat
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import servidores_historico as sh, servidores_intentos as si, servidores_lectura as sl, servidores_ssh as ss  # noqa: E402
from test_servidores_historico import A, Base, T0, entrada  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(RAIZ, "infobar", "remoto", "recoger.sh")
AHORA = 1_790_003_000                      # no alineada a la hora, para que haya hora en curso parcial


def cubos(ahora=AHORA, valores=None, cobertura_h=None):
    """Los 25 cubos de un servidor sano: {hora_de_epoca: n}. `valores` = {k_hacia_atras: n}."""
    act = ahora // 3600
    return [(h, "-" if cobertura_h is not None and h < cobertura_h else str((valores or {}).get(act - h, 0))) for h in range(act - 24, act + 1)]


def seccion_v2(total=7, hora=2, ahora=AHORA, ips=(("8.8.8.8", 4), ("1.1.1.1", 3)), fuente="unit", aislamiento="si", parcial="no", valores=None,
               extra=(), cobertura_h=None):
    c = cubos(ahora, valores if valores is not None else {0: 2, 1: 5}, cobertura_h)
    cuerpo = [f"ahora {ahora}", f"fuente {fuente}", f"aislamiento {aislamiento}", "cobertura 1000", f"total {total}", f"hora {hora}",
              *[f"h {h} {n}" for h, n in c], *[f"ip {i} {n}" for i, n in ips], f"parcial {parcial}", *extra]
    return ["n %d" % len(cuerpo), *cuerpo, "fin %d 0" % len(cuerpo)]


def texto(*secs, fin=True):
    out = []
    for nombre, lineas in secs:
        out += [f"### {nombre}", *lineas]
    return "\n".join(out + (["### fin"] if fin else [])) + "\n"


class ProtocoloTest(unittest.TestCase):
    def test_marco_valido_se_entrega_sin_marco(self):
        sec, marcadas, invalidas, completa = ss.analizar(texto(("version", ["2"]), ("intentos", seccion_v2())))
        self.assertEqual(marcadas, {"intentos"})
        self.assertFalse(invalidas)
        self.assertTrue(completa)
        self.assertTrue(sec["intentos"][0].startswith("ahora "))

    def test_marcos_rotos_invalidan_la_seccion(self):
        base = seccion_v2()
        casos = {
            "sin cierre": base[:-1],
            "cierre con codigo": base[:-1] + ["fin %d 124" % (len(base) - 2)],
            "cuenta distinta": ["n 99"] + base[1:],
            "cierre con otra cuenta": base[:-1] + ["fin 3 0"],
            "linea de menos": base[:2] + base[3:],
            "cabecera n basura": ["n x"] + base[1:],
        }
        for nombre, lineas in casos.items():
            sec, marcadas, invalidas, _ = ss.analizar(texto(("intentos", lineas)))
            self.assertNotIn("intentos", sec, nombre)
            self.assertEqual(invalidas, {"intentos"}, nombre)

    def test_v1_sin_marco_se_acepta_como_v1(self):
        sec, marcadas, invalidas, completa = ss.analizar(texto(("version", ["1"]), ("intentos", ["total 5", "hora 1", "ip 8.8.8.8 3"]), fin=False))
        self.assertEqual(sec["intentos"], ["total 5", "hora 1", "ip 8.8.8.8 3"])
        self.assertFalse(marcadas | invalidas)
        self.assertFalse(completa)

    def test_seccion_repetida_es_invalida_y_no_se_reabre(self):
        # un nombre de contenedor/etiqueta con "### intentos" dentro no puede reescribir la seccion
        t = texto(("intentos", seccion_v2()), ("sistema", ["0.1 0.1 0.1 1/1 1", "4", "100"]), ("intentos", ["total 0", "hora 0"]))
        sec, marcadas, invalidas, _ = ss.analizar(t)
        self.assertNotIn("intentos", sec)
        self.assertIn("intentos", invalidas)
        self.assertIn("sistema", sec)

    def test_seccion_cortada_y_cabeceras_tras_fin(self):
        self.assertFalse(ss.analizar(texto(("intentos", seccion_v2())) + "### cortada\n")[3])
        self.assertFalse(ss.analizar(texto(("intentos", seccion_v2())) + "### sistema\n1\n")[3])      # algo despues del cierre del script
        self.assertFalse(ss.analizar(texto(("intentos", seccion_v2()), fin=False))[3])

    def test_secciones_sigue_devolviendo_el_diccionario(self):
        self.assertEqual(ss.secciones("### disco\n10 5\n### raro\nx\n"), {"disco": ["10 5"]})

    def test_ssh_con_salida_enorme_deja_pasar_lo_que_cabe(self):
        cuerpo = "### version\n2\n### sistema\n0.1 0.1 0.1 1/1 1\n4\n100\n" + "x" * (2 * ss.TOPE_BYTES)
        orden = ["python3", "-c", f"import sys; sys.stdout.write({cuerpo!r})"]
        with mock.patch.object(ss, "orden_ssh", return_value=orden):
            salida, error = ss.ejecutar({"nombre": "a", "ssh": "h"})
        self.assertEqual(error, "")
        sec, _, _, completa = ss.analizar(salida)
        self.assertIn("sistema", sec)
        self.assertFalse(completa)


class PresupuestoDeTiempoTest(unittest.TestCase):
    """Peor caso de los scripts: root < sudo < vigilante < tope del Mac menos la conexion (5 s)."""

    def leer(self, nombre):
        import re
        with open(os.path.join(RAIZ, "infobar", "remoto", nombre)) as f:
            return f.read(), re

    def test_los_topes_encajan(self):
        root, re = self.leer("recoger-root.sh")
        limite = int(re.search(r"LIMITE=([0-9]+)", root)[1])
        cada = int(re.search(r"timeout ([0-9]+) \"\$@\"", root)[1])
        user, _ = self.leer("recoger.sh")
        sudo = int(re.search(r"timeout ([0-9]+) sudo -n", user)[1])
        vigilante = int(re.search(r"sleep ([0-9]+); kill -TERM", user)[1])
        self.assertLess(limite + cada, sudo)
        self.assertLess(sudo, vigilante)
        self.assertLessEqual(vigilante, ss.TOPE_S - 5)
        self.assertIn("[ $(( $(date +%s) - INICIO )) -ge $LIMITE ] && return 1", root)       # el limite se aplica en cada comando

    def test_sin_comandos_sueltos_en_root_fuera_del_presupuesto(self):
        root, re = self.leer("recoger-root.sh")
        # los programas externos de root pasan por t (con su tope y el presupuesto)
        self.assertNotIn("docker ps -a --format", root.replace("t docker ps -a --format", ""))
        self.assertNotIn("sshd -T |", root.replace("t sshd -T |", ""))


class IntentosTest(unittest.TestCase):
    def interp(self, lineas, ahora=AHORA, marcada=True):
        return si.interpretar(lineas, marcada, ahora)

    def cuerpo(self, **kw):
        return ss.analizar(texto(("intentos", seccion_v2(**kw))))[0]["intentos"]

    def test_v2_sano(self):
        it = self.interp(self.cuerpo())
        self.assertEqual((it["total"], it["hora"], it["origen_fiable"], it["desfase"]), (7, 2, True, 0))
        self.assertEqual(len(it["horas"]), 25)
        self.assertEqual(it["horas"][-1], [(AHORA // 3600) * 3600, 2])
        self.assertEqual([p[0] for p in it["top"]], ["8.8.8.8", "1.1.1.1"])
        self.assertNotIn("descartes", it)

    def test_sin_fuente_es_sin_dato_no_cero(self):
        lineas = ss.analizar(texto(("intentos", ["n 2", f"ahora {AHORA}", "fuente ninguna", "fin 2 0"])))[0]["intentos"]
        self.assertIsNone(self.interp(lineas))
        d = ss.interpretar({"intentos": lineas}, {"intentos"}, AHORA)
        self.assertNotIn("intentos", d)
        self.assertTrue(d["intentos_sin_dato"])

    def test_sin_aislamiento_no_hay_origen_fiable_pero_si_numeros(self):
        it = self.interp(self.cuerpo(aislamiento="no"))
        self.assertFalse(it["origen_fiable"])
        self.assertEqual(it["total"], 7)

    def test_v1_nunca_es_origen_fiable(self):
        it = self.interp(["total 5", "hora 1", "ip 8.8.8.8 3", "ip 1.1.1.1 2"], marcada=False)
        self.assertFalse(it["origen_fiable"])
        self.assertEqual(len(it["top"]), 2)

    def test_v1_con_lineas_de_v2_las_ignora(self):
        # una seccion sin marco que finge ser v2 ("fuente unit", "aislamiento si") no gana confianza
        d = ss.interpretar(ss.secciones(texto(("intentos", ["total 5", "hora 1", "fuente unit", "aislamiento si", "ip 8.8.8.8 3"]))))
        self.assertFalse(d["intentos"]["origen_fiable"])
        self.assertNotIn("horas", d["intentos"])

    def test_histograma_descartado_por_motivo(self):
        casos = {
            "parcial": dict(parcial="si"),
            "incoherente": dict(total=500),               # la suma de cubos (7) no llega al total
        }
        for motivo, kw in casos.items():
            it = self.interp(self.cuerpo(**kw))
            self.assertNotIn("horas", it, motivo)
            self.assertIn("histograma_" + motivo, it["descartes"], motivo)

    def test_reloj_del_servidor_muy_desfasado(self):
        it = self.interp(self.cuerpo(), ahora=AHORA + 3600)
        self.assertNotIn("horas", it)
        self.assertIn("histograma_reloj", it["descartes"])
        self.assertEqual(it["total"], 7)                  # los numeros se muestran, el histograma no se coloca

    def test_cubos_incorrectos(self):
        base = ss.analizar(texto(("intentos", seccion_v2())))[0]["intentos"]
        def cambia(f):
            return [f(l) for l in base]
        for nombre, lineas in {
            "falta uno": [l for i, l in enumerate(base) if not l.startswith("h ") or i != 10],
            "hora repetida": cambia(lambda l: l.replace(f"h {AHORA // 3600 - 5} ", f"h {AHORA // 3600 - 4} ")),
            "cuenta negativa": cambia(lambda l: l.replace(f"h {AHORA // 3600} 2", f"h {AHORA // 3600} -2")),
            "cuenta gigante": cambia(lambda l: l.replace(f"h {AHORA // 3600} 2", f"h {AHORA // 3600} {10 ** 12}")),
            "texto": cambia(lambda l: l.replace(f"h {AHORA // 3600} 2", f"h {AHORA // 3600} x")),
        }.items():
            it = self.interp(lineas)
            self.assertIsNotNone(it, nombre)
            self.assertNotIn("horas", it, nombre)
            self.assertIn("histograma_cubos", it["descartes"], nombre)

    def test_ips_que_suman_mas_que_el_total_se_descartan(self):
        it = self.interp(self.cuerpo(ips=(("8.8.8.8", 40), ("1.1.1.1", 30))))
        self.assertEqual(it["top"], [])
        self.assertIn("ips_incoherentes", it["descartes"])

    def test_claves_repetidas_o_invalidas_invalidan_la_seccion(self):
        self.assertIsNone(self.interp(self.cuerpo(extra=("total 1",))))
        self.assertIsNone(self.interp(self.cuerpo(aislamiento="quizas")))
        self.assertIsNone(self.interp(self.cuerpo(total=10 ** 12)))

    def test_ips_malformadas_y_mas_de_doce(self):
        ips = [(f"203.0.113.{i}" if False else f"8.8.{i // 250}.{i % 250 + 1}", 1) for i in range(20)]
        it = self.interp(self.cuerpo(total=100, ips=tuple(ips) + (("no-es-ip!", 9),)))
        self.assertEqual(len(it["top"]), si.MAX_IPS)
        self.assertTrue(all(si._IP.fullmatch(p[0]) for p in it["top"]))

    def test_horas_no_cubiertas_son_desconocidas_no_cero(self):
        it = self.interp(self.cuerpo(cobertura_h=AHORA // 3600 - 3))
        horas = dict((h, n) for h, n in it["horas"])
        self.assertIsNone(horas[(AHORA // 3600 - 24) * 3600])
        self.assertEqual(horas[(AHORA // 3600 - 2) * 3600], 0)


@unittest.skipUnless(shutil.which("bash") and shutil.which("timeout") and shutil.which("python3"), "hace falta bash, timeout y python3")
class ScriptRealTest(unittest.TestCase):
    """Ejecuta infobar/remoto/recoger.sh de verdad con un journalctl simulado delante en el PATH."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-sh-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        j = os.path.join(self.dir, "journalctl")
        with open(j, "w") as f:
            f.write(f'#!{sys.executable}\nimport os, sys, time\na = sys.argv[1:]\n'
                    'if os.environ.get("FAKE_SIN_UNIDAD"):\n    sys.exit(0)\n'
                    'if "-n" in a:\n    print("x"); sys.exit(0)\n'
                    'if "--since" not in a:\n    print(os.environ.get("FAKE_COBERTURA", "1000") + ".000000 h kernel: x"); sys.exit(0)\n'
                    'if os.environ.get("FAKE_LENTO"):\n    time.sleep(30)\n'
                    'unidades = [x.split("=", 1)[1] for x in a if x.startswith("_SYSTEMD_UNIT=")]\n'
                    'for linea in open(os.environ["FAKE_JOURNAL"]):\n'
                    '    unidad, _, resto = linea.partition("|")\n'
                    '    if not unidades or unidad in unidades:\n        sys.stdout.write(resto)\n')
        os.chmod(j, os.stat(j).st_mode | stat.S_IXUSR)
        with open(SCRIPT) as f:
            s = f.read()
        self.assertIn("export LC_ALL=C PATH=", s)
        s = s.replace("export LC_ALL=C PATH=", f"export LC_ALL=C PATH={self.dir}:{os.path.dirname(shutil.which('timeout'))}:", 1).replace("timeout 6 journalctl", "timeout 1 journalctl")
        self.script = os.path.join(self.dir, "recoger.sh")
        with open(self.script, "w") as f:
            f.write(s)

    def diario(self, lineas):
        ahora = int(time.time())
        ruta = os.path.join(self.dir, "diario.txt")
        with open(ruta, "w") as f:
            for dt, msg, *unidad in lineas:
                f.write(f"{unidad[0] if unidad else 'ssh.service'}|{ahora - dt}.123456 srv sshd[1]: {msg}\n")
        return ruta

    def ejecuta(self, **env):
        e = dict(os.environ, **env)
        r = subprocess.run(["bash", self.script], capture_output=True, text=True, timeout=60, env=e, stdin=subprocess.DEVNULL)
        return r.stdout

    def intentos(self, salida):
        sec, marcadas, invalidas, completa = ss.analizar(salida)
        return sec, marcadas, invalidas, completa

    def test_sintaxis(self):
        r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cuenta_solo_lineas_canonicas_y_la_ip_del_ultimo_port(self):
        j = self.diario([
            (100, "Invalid user admin from 203.0.113.9 port 5000"),
            (100, "Connection closed by authenticating user root 198.51.100.7 port 22 [preauth]"),
            (4000, "Failed password for root from 198.51.100.7 port 22 ssh2"),
            (4001, "Failed password for invalid user x from 198.51.100.7 port 22 ssh2"),     # duplicado de un Invalid user: no cuenta
            (90000, "Invalid user old from 203.0.113.50 port 1"),                             # fuera de las 24 h (pero dentro de los 25 cubos)
            (10, "Invalid user a port 1 from 8.8.8.8 port 2 [preauth]"),                     # el usuario finge un "port": manda el ultimo
            (10, "Invalid user 9.9.9.9 port 7 from 7.7.7.7 port 2"),
            (10, "pam_unix(sshd:session): session opened for user x"),
        ])
        salida = self.ejecuta(FAKE_JOURNAL=j)
        sec, marcadas, invalidas, completa = self.intentos(salida)
        self.assertEqual(marcadas, {"intentos"})
        self.assertTrue(completa)
        it = si.interpretar(sec["intentos"], True, time.time())
        self.assertEqual((it["total"], it["hora"]), (5, 4))
        self.assertEqual({p[0] for p in it["top"]}, {"203.0.113.9", "198.51.100.7", "8.8.8.8", "7.7.7.7"})
        self.assertEqual(dict(it["top"])["198.51.100.7"], 2)
        self.assertEqual(len(it["horas"]), 25)
        self.assertIn(sum(n for _, n in it["horas"] if n), (5, 6))          # los 5 de las 24 h (+ el de hace 25 h si cae en el primer cubo)

    def test_lineas_falsas_de_otro_proceso_no_cuentan(self):
        # `logger -t sshd ...` desde una sesion de usuario (otra unidad): con el filtro por -t o por _COMM se contaria
        j = self.diario([(10, "Invalid user a from 8.8.8.8 port 2"),
                         (10, "Invalid user x from 6.6.6.6 port 2", "session-4.scope"),
                         (10, "Invalid user y from 6.6.6.6 port 2", "docker-abc.scope")])
        sec, _, _, _ = self.intentos(self.ejecuta(FAKE_JOURNAL=j))
        it = si.interpretar(sec["intentos"], True, time.time())
        self.assertEqual(it["total"], 1)
        self.assertEqual([p[0] for p in it["top"]], ["8.8.8.8"])

    def test_version_y_cierre_global(self):
        salida = self.ejecuta(FAKE_JOURNAL=self.diario([]))
        self.assertTrue(salida.startswith("### version\n2\n"))
        self.assertTrue(salida.rstrip().endswith("### fin"))

    def test_sin_unidad_es_sin_dato(self):
        salida = self.ejecuta(FAKE_SIN_UNIDAD="1", FAKE_JOURNAL=self.diario([]))
        sec, marcadas, _, _ = self.intentos(salida)
        self.assertIn("fuente ninguna", sec["intentos"])
        self.assertIsNone(si.interpretar(sec["intentos"], True, time.time()))

    def test_journalctl_lento_marca_parcial_y_descarta_el_histograma(self):
        salida = self.ejecuta(FAKE_LENTO="1", FAKE_JOURNAL=self.diario([(10, "Invalid user a from 8.8.8.8 port 2")]))
        sec, _, _, _ = self.intentos(salida)
        it = si.interpretar(sec["intentos"], True, time.time())
        self.assertTrue(it["parcial"])
        self.assertNotIn("horas", it)

    def test_horas_anteriores_al_primer_registro_son_desconocidas(self):
        j = self.diario([(10, "Invalid user a from 8.8.8.8 port 2")])
        salida = self.ejecuta(FAKE_JOURNAL=j, FAKE_COBERTURA=str(int(time.time()) - 5 * 3600))
        sec, _, _, _ = self.intentos(salida)
        it = si.interpretar(sec["intentos"], True, time.time())
        self.assertIsNone(it["horas"][0][1])
        self.assertIsNotNone(it["horas"][-1][1])

    def test_una_linea_muy_larga_no_rompe_el_marco(self):
        j = self.diario([(10, "Invalid user " + "a" * 5000 + " from 8.8.8.8 port 2")])
        salida = self.ejecuta(FAKE_JOURNAL=j)
        sec, marcadas, invalidas, _ = self.intentos(salida)
        self.assertEqual(marcadas, {"intentos"})
        self.assertFalse(invalidas)


class HistogramaEnLaBaseTest(Base):
    def lectura(self, ok_en, valores, ahora_srv=None, **kw):
        ahora_srv = ahora_srv or ok_en
        e = entrada(ok_en)
        kw.setdefault("ips", (("8.8.8.8", 2), ("1.1.1.1", 1)))
        it = si.interpretar(ss.analizar(texto(("intentos", seccion_v2(ahora=ahora_srv, valores=valores, total=sum(valores.values()), hora=1, **kw))))[0]["intentos"],
                            True, ok_en)
        e["datos"]["intentos"] = it
        return e

    def exactos(self):
        return {h: (n, c) for h, n, c in self.con.execute("SELECT hora, intentos_exactos, intentos_completa FROM horas_servidor ORDER BY hora")}

    def test_se_escriben_horas_completas_y_la_parcial(self):
        ok = AHORA
        self.reg(self.lectura(ok, {0: 2, 1: 5, 3: 9}))
        ex = self.exactos()
        act = (ok // 3600) * 3600
        self.assertEqual(ex[act], (2, 0))                    # la hora en curso es parcial
        self.assertEqual(ex[act - 3600], (5, 1))
        self.assertEqual(ex[act - 3 * 3600], (9, 1))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM horas_servidor WHERE n = 0 AND intentos_exactos IS NOT NULL"), 24)    # las que el Mac no vio
        self.assertEqual(self.uno("SELECT estado_peor FROM horas_servidor WHERE hora=?", act - 3600), "gris")

    def test_una_hora_anotada_nunca_baja(self):
        self.reg(self.lectura(AHORA, {0: 2, 1: 5}))
        # el servidor se reinicia con journal volatil (o miente): ahora dice 0 para las horas pasadas
        self.reg(self.lectura(AHORA + 400, {0: 3, 1: 0}))
        ex = self.exactos()
        act = (AHORA // 3600) * 3600
        self.assertEqual(ex[act - 3600][0], 5)
        self.assertEqual(ex[act][0], 3)                       # la parcial sube

    def test_una_hora_completa_no_vuelve_a_parcial(self):
        h = (AHORA // 3600) * 3600 - 3600
        sh._alta_servidor(self.con, "a" * 12, "x", AHORA)
        self.con.execute("BEGIN IMMEDIATE")
        sh._anota_horas(self.con, "a" * 12, [(h, 5, True)])
        sh._anota_horas(self.con, "a" * 12, [(h, 3, False)])        # un reloj del servidor retrasado la vuelve a ver «en curso»
        self.con.execute("COMMIT")
        self.assertEqual(self.exactos()[h], (5, 1))

    def test_la_hora_parcial_que_se_completa_se_marca(self):
        self.reg(self.lectura(AHORA, {0: 2}))
        self.reg(self.lectura(AHORA + 3600, {1: 4}))
        act = (AHORA // 3600) * 3600
        self.assertEqual(self.exactos()[act], (4, 1))

    def test_ventana_de_25_horas_y_sin_horas_futuras(self):
        e = self.lectura(AHORA, {0: 1})
        e["datos"]["intentos"]["horas"].append([((AHORA // 3600) + 5) * 3600, 99])                 # cubo del futuro
        self.reg(e)
        self.assertEqual(self.descartes("histograma"), 1)         # desordenado/fuera: el histograma entero se descarta, la muestra se guarda
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 1)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM horas_servidor WHERE intentos_exactos IS NOT NULL"), 0)

    def test_hostil_se_descarta_solo_el_histograma(self):
        for malo in ([[5, 3]], [["x", 1]], [[AHORA - AHORA % 3600, -1]], [[AHORA - AHORA % 3600, 10 ** 12]], [[AHORA - AHORA % 3600 + 1, 1]], [[True, 1]],
                      [[AHORA - AHORA % 3600 - 3600, 1], [AHORA - AHORA % 3600 - 3600, 2]], [[AHORA - AHORA % 3600, 2], [AHORA - AHORA % 3600 - 3600, 1]]):
            con = self.lectura(AHORA, {0: 1})
            con["datos"]["intentos"]["horas"] = malo
            with self.subTest(malo=malo):
                self.reg(con, ahora=AHORA)
                self.con.execute("UPDATE servidores SET ok_en=0")
        self.assertEqual(self.uno("SELECT COUNT(*) FROM horas_servidor WHERE intentos_exactos IS NOT NULL"), 0)

    def test_sin_origen_fiable_no_se_guardan_ips(self):
        e = self.lectura(AHORA, {0: 3}, aislamiento="no")
        self.assertEqual(e["datos"]["intentos"]["top"][0][0], "8.8.8.8")        # se muestra...
        self.reg(e)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM ips_atacantes"), 0)    # ...pero no se guarda
        self.reg(self.lectura(AHORA + 300, {0: 3}))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM ips_atacantes"), 2)

    def test_v1_no_guarda_ips(self):
        e = entrada(AHORA)
        e["datos"]["intentos"] = si.interpretar(["total 5", "hora 1", "ip 8.8.8.8 3"], False)
        self.reg(e)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM ips_atacantes"), 0)
        self.assertEqual(self.uno("SELECT intentos_24h FROM muestras"), 5)

    def test_migracion_de_v1_a_v2_conserva_los_datos(self):
        self.con.close()
        ruta = sh.ruta()
        os.remove(ruta)
        c = sqlite3.connect(ruta, isolation_level=None)
        for s in sh._sentencias(sh.MIGRACIONES[1]):
            c.execute(s)
        c.execute("INSERT INTO servidores(id, nombre, creado_en) VALUES('aaaaaaaaaaaa', 'x', 1)")
        c.execute("INSERT INTO horas_servidor(servidor_id, hora, n, estado_peor) VALUES('aaaaaaaaaaaa', 1790000000 - 1790000000 % 3600, 3, 'verde')")
        c.execute("INSERT INTO ips_atacantes(servidor_id, ip, primera, ultima, max_24h, horas_vista, ultima_hora) VALUES('aaaaaaaaaaaa','8.8.8.8',1,1,1,1,1)")
        c.execute("PRAGMA user_version=1")
        c.close()
        self.con = sh.abrir()
        self.assertEqual(self.uno("PRAGMA user_version"), 2)
        self.assertEqual(self.uno("SELECT n FROM horas_servidor"), 3)
        self.assertIsNone(self.uno("SELECT intentos_exactos FROM horas_servidor"))
        copias = os.listdir(os.path.join(self.dir, "copias"))
        self.assertEqual(len(copias), 1)
        with open(os.path.join(self.dir, "copias", copias[0]), "rb") as f:
            self.assertNotIn(b"8.8.8.8", f.read())


class LecturaExactosTest(Base):
    def test_historia_suma_los_exactos_por_hora_y_por_dia(self):
        import datetime
        dia = datetime.date.fromtimestamp(AHORA)
        h0 = (AHORA // 3600) * 3600
        sh._alta_servidor(self.con, "a" * 12, "x", AHORA)
        self.con.execute("BEGIN IMMEDIATE")
        sh._anota_horas(self.con, "a" * 12, [(h0 - 2 * 3600, 2, True), (h0 - 3600, 3, True)])
        self.con.execute("COMMIT")
        d = sl.historia(self.con, "a" * 12, (dia - datetime.timedelta(days=1)).isoformat(), (dia + datetime.timedelta(days=1)).isoformat(), "hora")
        por_hora = [p["intentos_exactos"] for p in d["puntos"]]
        self.assertEqual(sorted(por_hora), [2, 3])
        dd = sl.historia(self.con, "a" * 12, (dia - datetime.timedelta(days=1)).isoformat(), (dia + datetime.timedelta(days=1)).isoformat(), "dia")
        self.assertEqual(sum(p["intentos_exactos"] or 0 for p in dd["puntos"]), 5)
        self.assertTrue(all(p["intentos_completa"] for p in d["puntos"]))


if __name__ == "__main__":
    unittest.main()
