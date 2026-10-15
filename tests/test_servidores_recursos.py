"""Inventario de contenedores: plantilla fija de `docker inspect` (contra Docker real si lo hay), parseo hostil, recursos con identidad, y la base
(línea base, confirmación por tiempo, presupuestos, retención, borrado, copia previa sin nombres)."""
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import servidores_api as api, servidores_historico as sh, servidores_inventario as inv, servidores_recursos as rec  # noqa: E402
from infobar import servidores_ssh as ss  # noqa: E402
from test_servidores_historico import Base, SRV, entrada  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(RAIZ, "infobar", "remoto", "recoger-root.sh")
T0 = 1_790_000_000


def fila(nombre="web-1", estado="running", salud="none", reinicios=0, salida=0, inicio="2026-09-01T10:00:00.123456789Z", politica="always",
         proyecto="", servicio="", swarm="", tarea=""):
    campos = [nombre if nombre.startswith("/") else "/" + nombre, estado, salud]
    return "\t".join(["c", *(json.dumps(c) for c in campos[:3]), str(reinicios), str(salida), json.dumps(inicio), json.dumps(politica),
                      json.dumps(proyecto), json.dumps(servicio), json.dumps(swarm), json.dumps(tarea)])


def seccion(filas, total=None, pedidos=None, truncado=None, fuente="docker"):
    pedidos = len(filas) if pedidos is None else pedidos
    total = pedidos if total is None else total
    cuerpo = [f"fuente {fuente}", f"total {total}", f"pedidos {pedidos}", f"truncado {'si' if (total > pedidos if truncado is None else truncado) else 'no'}", *filas]
    return [f"n {len(cuerpo)}", *cuerpo, f"fin {len(cuerpo)} 0"]


def lee(filas, **kw):
    sec, marcadas, _, _ = ss.analizar("### inventario\n" + "\n".join(seccion(filas, **kw)) + "\n### fin\n")
    return inv.interpretar(sec["inventario"], "inventario" in marcadas) if "inventario" in sec else None


class ParseoTest(unittest.TestCase):
    def test_lectura_sana(self):
        i = lee([fila("web-1"), fila("db", estado="exited", salida=0), fila("job", estado="exited", salida=1)])
        self.assertTrue(i["completo"])
        self.assertEqual([c["nombre"] for c in i["contenedores"]], ["web-1", "db", "job"])
        self.assertEqual(i["contenedores"][0]["inicio"], 1788256800)

    def test_cada_campo_hostil_descarta_su_linea_y_la_lectura_deja_de_ser_completa(self):
        malos = {
            "nombre con barra": fila("a/b"), "nombre con control": fila("a\nb"), "nombre vacio": fila("/"), "nombre enorme": fila("a" * 100),
            "estado raro": fila(estado="zombie"), "salud rara": fila(salud="muy sana"), "politica rara": fila(politica="a veces"),
            "etiqueta con control": fila(proyecto="x\n### intentos\ntotal 0"), "etiqueta con html": fila(servicio="<b>"),
            "reinicios negativos": fila().replace("\t0\t0\t", "\t-1\t0\t"), "reinicios gigantes": fila(reinicios=10 ** 15),
            "salida gigante": fila(salida=10 ** 9), "faltan campos": "\t".join(fila().split("\t")[:-1]), "sobran campos": fila() + "\tx",
            "json roto": fila().replace('"always"', "always"), "no es cadena": fila().replace('"running"', "5"),
        }
        for nombre, linea in malos.items():
            i = lee([fila("ok-1"), linea])
            self.assertIsNotNone(i, nombre)
            self.assertEqual(i["descartados"], 1, nombre)
            self.assertFalse(i["completo"], nombre)                      # y por eso nada desaparece de esta lectura
            self.assertEqual([c["nombre"] for c in i["contenedores"]], ["ok-1"], nombre)

    def test_fecha_de_arranque_ilegible_o_inexistente_es_none(self):
        for f in ("0001-01-01T00:00:00Z", "ayer", "2026-13-45T99:99:99Z", "2999-01-01T00:00:00Z", ""):
            self.assertIsNone(lee([fila(inicio=f)])["contenedores"][0]["inicio"], f)

    def test_marco_y_cabeceras(self):
        self.assertIsNone(lee([fila()], fuente="rara"))
        self.assertEqual(lee([], fuente="ninguna")["completo"], False)
        self.assertEqual(lee([], fuente="error")["completo"], False)
        self.assertTrue(lee([])["completo"])                                       # docker sin contenedores es una lectura completa
        t = lee([fila("a")], total=300, pedidos=1)
        self.assertTrue(t["truncado"] and t["completo"])
        self.assertIsNone(lee([fila()], total=0, pedidos=1))                       # pedidos > total: incoherente
        self.assertFalse(lee([fila("a")], pedidos=3, total=3)["completo"])         # faltan lineas que el servidor dijo enviar
        self.assertIsNone(lee([fila("a" + str(i)) for i in range(inv.MAX_CONTENEDORES + 1)], total=500, pedidos=inv.MAX_CONTENEDORES))

    def test_una_linea_que_no_es_del_protocolo_invalida_la_seccion(self):
        s = seccion([fila()])
        s.insert(2, "hola que tal")
        s[0] = f"n {len(s) - 2}"
        s[-1] = f"fin {len(s) - 2} 0"
        sec, marcadas, _, _ = ss.analizar("### inventario\n" + "\n".join(s) + "\n")
        self.assertIsNone(inv.interpretar(sec["inventario"], True))

    def test_un_nombre_duplicado_se_cuenta_como_descartado(self):
        i = lee([fila("a"), fila("a")])
        self.assertEqual((len(i["contenedores"]), i["descartados"], i["completo"]), (1, 1, False))

    def test_etiqueta_que_intenta_reabrir_una_seccion_no_rompe_el_protocolo(self):
        sucio = fila(proyecto="x").replace('"x"', json.dumps("p\n### intentos\ntotal 0\n### fin_root\nomitidos 0"))
        self.assertEqual(sucio.count("\n"), 0)                                     # {{json}} escapa los saltos de línea
        sec, marcadas, invalidas, completa = ss.analizar("### inventario\n" + "\n".join(seccion([sucio])) + "\n### fin\n")
        self.assertNotIn("intentos", sec)
        self.assertNotIn("fin_root", sec)


class IdentidadTest(unittest.TestCase):
    def rec(self, filas, swarm=None):
        return inv.recursos(lee(filas), swarm)

    def test_identidad_por_tipo(self):
        r = self.rec([fila("suelto"), fila("p-web-1", proyecto="p", servicio="web"), fila("p-web-2", proyecto="p", servicio="web", reinicios=3),
                      fila("api.1.abcdefghij0123456789xyzkl", swarm="api", tarea="abcdefghij0123456789xyzkl"),
                      fila("api.2.zzzdefghij0123456789xyzkl", swarm="api", tarea="zzzdefghij0123456789xyzkl")], swarm={"api": True})
        self.assertEqual(set(r), {("contenedor", "suelto"), ("compose", "p/web"), ("servicio", "api")})
        self.assertEqual((r[("compose", "p/web")]["n"], r[("compose", "p/web")]["reinicios"]), (2, 3))
        self.assertEqual((r[("servicio", "api")]["n"], r[("servicio", "api")]["estado"]), (2, "ok"))

    def test_un_contenedor_no_puede_ocupar_la_identidad_de_un_servicio_que_no_existe(self):
        r = self.rec([fila("falso", swarm="produccion", tarea="x" * 25)], swarm={"otro": True})
        self.assertEqual(set(r), {("contenedor", "falso")})
        r = self.rec([fila("falso", swarm="produccion", tarea="x" * 25)], swarm={"produccion": True})       # existe, pero el nombre no es de tarea
        self.assertEqual(set(r), {("contenedor", "falso")})

    def test_el_estado_de_un_servicio_viene_de_service_ls_no_de_sus_tareas_viejas(self):
        viejas = [fila(f"api.1.{'a' * 24}{i}", estado="exited", salida=1, swarm="api", tarea="a" * 24 + str(i)) for i in range(3)]
        self.assertEqual(self.rec(viejas, {"api": True})[("servicio", "api")]["estado"], "ok")
        self.assertEqual(self.rec(viejas, {"api": False})[("servicio", "api")]["estado"], "caido")

    def test_estado_de_cada_contenedor_y_agregacion_por_el_peor(self):
        casos = {("running", "none", 0): "ok", ("running", "unhealthy", 0): "mal", ("running", "healthy", 0): "ok", ("restarting", "none", 0): "reiniciando",
                 ("exited", "none", 0): "terminado", ("exited", "none", 137): "caido", ("dead", "none", 1): "caido", ("created", "none", 0): "otro",
                 ("paused", "none", 0): "otro"}
        for (st, sa, ex), esperado in casos.items():
            self.assertEqual(self.rec([fila("x", estado=st, salud=sa, salida=ex)])[("contenedor", "x")]["estado"], esperado, (st, sa, ex))
        r = self.rec([fila("a", proyecto="p", servicio="s"), fila("b", proyecto="p", servicio="s", estado="exited", salida=2)])
        self.assertEqual(r[("compose", "p/s")]["estado"], "caido")


@unittest.skipUnless(shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0, "no hay Docker local")
class PlantillaConDockerRealTest(unittest.TestCase):
    """La plantilla del script, ejecutada por el `docker inspect` de verdad, produce líneas que el parser acepta, y las etiquetas hostiles no rompen líneas."""

    @classmethod
    def plantilla(cls):
        with open(ROOT) as f:
            return re.search(r"PLANTILLA='(.*)'\n", f.read())[1]

    def test_contenedores_existentes(self):
        ids = subprocess.run(["docker", "ps", "-aq", "--no-trunc"], capture_output=True, text=True, timeout=30).stdout.split()[:20]
        if not ids:
            self.skipTest("Docker sin contenedores")
        salida = subprocess.run(["docker", "inspect", "--format", self.plantilla(), *ids], capture_output=True, text=True, timeout=30).stdout
        lineas = salida.splitlines()
        self.assertEqual(len(lineas), len(ids))
        for l in lineas:
            self.assertIsNotNone(inv._contenedor(l), "la plantilla produce algo que el parser no acepta")

    def test_etiquetas_hostiles_no_rompen_lineas_ni_cuelan_campos_nuevos(self):
        imagen = subprocess.run(["docker", "images", "-q"], capture_output=True, text=True, timeout=30).stdout.split()
        if not imagen:
            self.skipTest("Docker sin imágenes")
        nombre = "cg-prueba-" + uuid.uuid4().hex[:8]
        malo = "p\n### intentos\ntotal 0\tx\t{{.Config.Env}}"
        r = subprocess.run(["docker", "create", "--name", nombre, "--label", f"com.docker.compose.project={malo}", "--label", "password=hunter2",
                            "--env", "SECRETO=hunter2", imagen[0]], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            self.skipTest("no se pudo crear el contenedor de prueba")
        try:
            salida = subprocess.run(["docker", "inspect", "--format", self.plantilla(), nombre], capture_output=True, text=True, timeout=30).stdout
            self.assertEqual(len(salida.splitlines()), 1)
            self.assertEqual(len(salida.rstrip("\n").split("\t")), inv.CAMPOS)
            self.assertNotIn("hunter2", salida)                             # ni el entorno ni otras etiquetas llegan al script
            self.assertIsNone(inv._contenedor(salida.rstrip("\n")))          # y el valor hostil hace que la línea se descarte, no que se acepte
        finally:
            subprocess.run(["docker", "rm", "-f", nombre], capture_output=True, timeout=30)

    def test_la_plantilla_solo_nombra_campos_de_la_lista_blanca(self):
        permitidos = {".Name", ".State.Status", ".State.Health", ".State.Health.Status", ".RestartCount", ".State.ExitCode", ".State.StartedAt",
                      ".HostConfig.RestartPolicy.Name", ".Config.Labels"}
        usados = set(re.findall(r"(?<![\w\"])(\.[A-Z][A-Za-z.]*)", self.plantilla()))
        self.assertTrue(usados <= permitidos, usados - permitidos)
        etiquetas = set(re.findall(r'"(com\.docker\.[a-z.]+)"', self.plantilla()))
        self.assertEqual(etiquetas, {"com.docker.compose.project", "com.docker.compose.service", "com.docker.swarm.service.name", "com.docker.swarm.task.id"})
        for prohibido in (".Config.Env", ".Config.Cmd", ".Mounts", ".Config.Image", ".NetworkSettings", ".Args", ".Path", ".State.Health.Log"):
            self.assertNotIn(prohibido, self.plantilla())


class ScriptRootInventarioTest(unittest.TestCase):
    """recoger-root.sh de verdad con un `docker` falso: ids, orden por antigüedad, tope de 200, truncado y fuente."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-inv-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        with open(ROOT) as f:
            texto = f.read()
        self.assertIn("export LC_ALL=C PATH=", texto)
        # PATH cerrado: solo los programas falsos, timeout y tac (por enlace) y lo básico del sistema; así un docker real del Mac no se cuela
        for programa in ("timeout", "tac"):
            os.symlink(shutil.which(programa), os.path.join(self.dir, programa))
        texto = re.sub(r"export LC_ALL=C PATH=\S+", f"export LC_ALL=C PATH={self.dir}:/usr/bin:/bin", texto, count=1)
        self.script = os.path.join(self.dir, "root.sh")
        with open(self.script, "w") as f:
            f.write(texto)
        self.plantilla = re.search(r"PLANTILLA='(.*)'\n", texto)[1]

    def docker(self, n_ids, falla=None):
        ids = [f"{i:064x}" for i in range(n_ids, 0, -1)]            # `docker ps -a` da los más nuevos primero
        ruta = os.path.join(self.dir, "docker")
        with open(ruta, "w") as f:
            f.write(f"#!{sys.executable}\nimport sys\na = sys.argv[1:]\nIDS = {ids!r}\n"
                    "if a[:1] == ['ps']:\n    print('\\n'.join(IDS)); sys.exit(0)\n"
                    "if a[:1] == ['inspect']:\n"
                    "    for i in a[3:]:\n"
                    "        n = int(i, 16)\n"
                    "        print('c\\t\"/c%d\"\\t\"running\"\\t\"none\"\\t0\\t0\\t\"2026-09-01T10:00:00Z\"\\t\"no\"\\t\"\"\\t\"\"\\t\"\"\\t\"\"' % n)\n"
                    "    sys.exit(0)\n"
                    f"sys.exit({falla or 0})\n")
        os.chmod(ruta, 0o755)

    def ejecuta(self):
        r = subprocess.run(["bash", self.script], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        return ss.analizar("### version\n2\n" + r.stdout + "### fin\n")

    def test_pocos_contenedores_completo(self):
        self.docker(3)
        sec, marcadas, invalidas, _ = self.ejecuta()
        self.assertIn("inventario", marcadas)
        i = inv.interpretar(sec["inventario"], True)
        self.assertTrue(i["completo"])
        self.assertEqual(sorted(c["nombre"] for c in i["contenedores"]), ["c1", "c2", "c3"])
        self.assertEqual(sec["fin_root"], ["omitidos 0"])

    def test_mas_de_200_se_recorta_a_los_mas_antiguos_y_se_declara(self):
        self.docker(250)
        sec, marcadas, _, _ = self.ejecuta()
        i = inv.interpretar(sec["inventario"], True)
        self.assertEqual((i["total"], i["pedidos"], i["truncado"], i["completo"]), (250, 200, True, True))
        self.assertEqual({c["nombre"] for c in i["contenedores"]}, {f"c{n}" for n in range(1, 201)})        # los 200 más antiguos (ids 1..200)

    def test_docker_que_falla_o_no_existe_es_sin_dato(self):
        self.docker(2, falla=1)
        os.remove(os.path.join(self.dir, "docker"))
        with open(os.path.join(self.dir, "docker"), "w") as f:
            f.write("#!/bin/bash\nexit 1\n")
        os.chmod(os.path.join(self.dir, "docker"), 0o755)
        sec, marcadas, _, _ = self.ejecuta()
        i = inv.interpretar(sec["inventario"], True)
        self.assertEqual((i["fuente"], i["completo"]), ("error", False))
        os.remove(os.path.join(self.dir, "docker"))
        sec, marcadas, _, _ = self.ejecuta()
        i = inv.interpretar(sec["inventario"], True)
        self.assertEqual((i["fuente"], i["completo"]), ("ninguna", False))

    def test_si_root_omite_algo_el_inventario_se_descarta_con_el_resto(self):
        self.docker(2)
        sec, _, invalidas, _ = ss.analizar("### version_root\n2\n### inventario\nn 2\nfuente error\nfin 2 0\n### fin_root\nomitidos 3\n")
        self.assertNotIn("inventario", sec)
        self.assertIn("root", invalidas)


class BaseRecursos(Base):
    def lectura(self, ts, filas=(), swarm=None, **kw):
        e = entrada(ts)
        i = lee(list(filas), **kw)
        e["datos"]["inventario"] = i
        e["datos"]["swarm_servicios"] = swarm or {}
        return e

    def recursos(self):
        return {r[0]: r[1:] for r in self.con.execute("SELECT etiqueta, tipo, activo, estado FROM recursos ORDER BY etiqueta")}

    def eventos(self):
        return [(e, t) for e, t in self.con.execute("SELECT evento, tipo FROM eventos ORDER BY ts, evento")]


class AlmacenRecursosTest(BaseRecursos):
    def test_la_primera_lectura_es_linea_base_sin_eventos(self):
        self.reg(self.lectura(T0, [fila("a"), fila("b")]))
        self.assertEqual({k: v[1] for k, v in self.recursos().items()}, {"a": 1, "b": 1})
        self.assertEqual(self.eventos(), [])

    def test_aparicion_exige_dos_lecturas_separadas_en_el_tiempo(self):
        self.reg(self.lectura(T0, [fila("a")]))
        self.reg(self.lectura(T0 + 300, [fila("a"), fila("nuevo")]))
        self.assertEqual(self.recursos()["nuevo"][1], 0)                            # visto una vez: pendiente
        self.reg(self.lectura(T0 + 360, [fila("a"), fila("nuevo")]))
        self.assertEqual(self.recursos()["nuevo"][1], 0)                            # dos lecturas pero demasiado juntas
        self.reg(self.lectura(T0 + 900, [fila("a"), fila("nuevo")]))
        self.assertEqual((self.recursos()["nuevo"][1], self.eventos()), (1, [("aparecio", "contenedor")]))

    def test_un_efimero_que_no_dura_no_genera_eventos(self):
        self.reg(self.lectura(T0, [fila("a")]))
        for i in range(1, 6):
            self.reg(self.lectura(T0 + 300 * i, [fila("a"), fila(f"efimero{i}")]))
        self.assertEqual(self.eventos(), [])

    def test_desaparicion_exige_dos_lecturas_completas_y_no_ocurre_con_truncadas_ni_incompletas(self):
        self.reg(self.lectura(T0, [fila("a"), fila("b")]))
        self.reg(self.lectura(T0 + 300, [fila("a")]))
        self.assertEqual(self.eventos(), [])
        self.reg(self.lectura(T0 + 900, [fila("a")], total=300, pedidos=1))        # truncada: no concluye nada
        self.assertEqual(self.recursos()["b"][1], 1)
        e = self.lectura(T0 + 1200, [fila("a")], pedidos=2, total=2)                # incompleta: faltaba una linea
        self.reg(e)
        self.assertEqual(self.recursos()["b"][1], 1)
        self.reg(self.lectura(T0 + 1500, [fila("a")]))
        self.assertEqual(self.eventos(), [("desaparecio", "contenedor")])
        self.assertEqual(self.recursos()["b"][1], 0)

    def test_un_fallo_ssh_o_sin_inventario_no_cambia_nada(self):
        self.reg(self.lectura(T0, [fila("a"), fila("b")]))
        for i in range(1, 5):
            e = entrada(T0 + 300 * i)
            e["error"] = "timeout"
            e["ok_en"] = T0
            self.reg(e)
            self.reg(entrada(T0 + 300 * i + 1))                                      # lectura buena sin sección de inventario
        self.assertEqual(self.eventos(), [])
        self.assertEqual({k: v[1] for k, v in self.recursos().items()}, {"a": 1, "b": 1})

    def test_cambio_de_estado_bucle_y_presupuestos(self):
        self.reg(self.lectura(T0, [fila("a", reinicios=0)]))
        self.reg(self.lectura(T0 + 300, [fila("a", estado="exited", salida=1)]))
        self.assertEqual(self.eventos(), [("cambio_estado", "contenedor")])
        # bucle: el contador de reinicios sube en dos lecturas seguidas con arranque reciente
        ahora = T0 + 600
        for k in range(1, 3):
            ts = ahora + 300 * k
            arranque = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime(ts - 60))
            self.reg(self.lectura(ts, [fila("a", reinicios=k, inicio=arranque)]))
        self.assertIn(("reinicios_en_bucle", "contenedor"), self.eventos())
        # un recurso que cambia sin parar agota su presupuesto (10 al día) y deja un único «inestable»
        for k in range(30):
            self.reg(self.lectura(T0 + 20_000 + 300 * k, [fila("a", estado="running" if k % 2 else "exited", salida=0)]))
        n = self.uno("SELECT COUNT(*) FROM eventos WHERE evento='inestable'")
        self.assertEqual(n, 1)
        self.assertLessEqual(self.uno("SELECT COUNT(*) FROM eventos WHERE clave=(SELECT clave FROM recursos LIMIT 1)"), rec.EVENTOS_DIA_POR_CLAVE + 3 * 1)
        self.assertGreater(self.descartes("eventos_sobre_tope"), 0)

    def test_tope_de_500_recursos_expulsa_inactivos_y_si_no_hay_descarta(self):
        self.reg(self.lectura(T0, [fila("base")]))
        self.con.execute("BEGIN IMMEDIATE")
        for i in range(rec.MAX_RECURSOS - 1):
            self.con.execute("INSERT INTO recursos(servidor_id, tipo, clave, etiqueta, primera, ultima, activo, estado) VALUES(?, 'contenedor', ?, 'x', 1, ?, 0, 'ok')",
                             (SRV["id"], f"{i:032x}", 100 + i))
        self.con.execute("COMMIT")
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos"), rec.MAX_RECURSOS)
        self.reg(self.lectura(T0 + 300, [fila("base"), fila("nuevo")]))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos"), rec.MAX_RECURSOS)       # entró el nuevo expulsando al inactivo más antiguo
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE etiqueta='nuevo'"), 1)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE ultima=100"), 0)
        self.con.execute("UPDATE recursos SET activo=1")                                      # y si todos están activos, el nuevo se descarta
        self.reg(self.lectura(T0 + 600, [fila("base"), fila("otro-nuevo")]))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE etiqueta='otro-nuevo'"), 0)
        self.assertEqual(self.descartes("recursos_sobre_tope"), 1)

    def test_el_nombre_solo_esta_en_la_etiqueta(self):
        self.reg(self.lectura(T0, [fila("cliente-secreto")]))
        self.reg(self.lectura(T0 + 300, [fila("cliente-secreto", estado="exited", salida=1)]))
        claves = {r[0] for r in self.con.execute("SELECT clave FROM recursos")} | {r[0] for r in self.con.execute("SELECT clave FROM eventos")}
        self.assertTrue(all(len(c) == 32 and "cliente" not in c for c in claves))
        for tabla in ("eventos", "meta"):
            for fila_ in self.con.execute(f"SELECT * FROM {tabla}"):
                self.assertNotIn("cliente-secreto", " ".join(map(str, fila_)))

    def test_misma_clave_misma_instalacion_y_distinta_sal_distinta_clave(self):
        a = rec.clave_de(self.con, "contenedor", "x")
        self.assertEqual(a, rec.clave_de(self.con, "contenedor", "x"))
        self.assertNotEqual(a, rec.clave_de(self.con, "compose", "x"))
        self.con.execute("UPDATE meta SET valor='otra' WHERE clave='sal_recursos'")
        self.assertNotEqual(a, rec.clave_de(self.con, "contenedor", "x"))

    def test_retencion_y_reloj_adelantado(self):
        self.reg(self.lectura(T0, [fila("a"), fila("b")]))
        self.reg(self.lectura(T0 + 300, [fila("a")]))
        self.reg(self.lectura(T0 + 900, [fila("a")]))                                   # b desaparece (inactivo)
        self.con.execute("UPDATE eventos SET ts = ?", (T0 - 200 * 86400,))             # evento de hace más de 180 días
        sh.mantener(self.con, {SRV["id"]: {"guardar_ips": True, "retencion_dias": 90}}, T0 + 40 * 86400, vacuum=False)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos"), 0)
        self.assertEqual(list(self.recursos()), ["a"] if self.recursos().get("a") else [])     # b (inactivo, de hace 40 días) purgado
        self.assertNotIn("b", self.recursos())
        # marcas en el futuro (reloj adelantado) se traen al presente
        self.con.execute("UPDATE recursos SET ultima=?, visto_desde=?", (T0 + 10 * 86400, T0 + 10 * 86400))
        sh.mantener(self.con, {SRV["id"]: {"guardar_ips": True, "retencion_dias": 90}}, T0 + 40 * 86400, vacuum=False)
        self.assertLessEqual(self.uno("SELECT MAX(ultima) FROM recursos"), T0 + 40 * 86400 + 3600)

    def test_servidor_retirado_pierde_recursos_y_eventos(self):
        self.reg(self.lectura(T0, [fila("a")]))
        self.reg(self.lectura(T0 + 300, [fila("a", estado="exited", salida=1)]))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos"), 1)
        sh.mantener(self.con, {}, T0 + 3600, vacuum=False)                              # ya no está en la configuración
        self.assertEqual((self.uno("SELECT COUNT(*) FROM recursos"), self.uno("SELECT COUNT(*) FROM eventos")), (0, 0))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM servidores"), 1)                # el historial numérico permanece

    def test_olvidar_recursos_borra_de_verdad_los_bytes(self):
        self.reg(self.lectura(T0, [fila("nombre-muy-reconocible-123")]))
        (n, e), _ = sh.olvidar_recursos(self.con, SRV["id"])
        self.assertEqual(n, 1)
        self.con.close()
        crudo = b""
        for ext in ("", "-wal"):
            if os.path.exists(sh.ruta() + ext):
                with open(sh.ruta() + ext, "rb") as f:
                    crudo += f.read()
        self.assertNotIn(b"nombre-muy-reconocible-123", crudo)
        self.con = sh.abrir()

    def test_olvidar_recursos_por_orden_y_con_nombre_inexistente(self):
        self.reg(self.lectura(T0, [fila("a")]))
        self.con.close()
        self.assertEqual(api.olvidar("no-existe", "recursos")[0], 1)
        codigo, mensaje = api.olvidar(NOMBRE_SRV, "recursos")
        self.assertEqual(codigo, 0)
        self.assertIn("Recursos borrados: 1", mensaje)
        self.con = sh.abrir()
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos"), 0)
        with open(os.path.join(self.dir, "servidores_ips.log")) as f:
            self.assertNotIn('"a"', f.read())                                            # la auditoría lleva el número, no los nombres

    def test_la_copia_previa_a_migrar_no_lleva_nombres(self):
        self.reg(self.lectura(T0, [fila("nombre-que-no-debe-quedar")]))
        self.con.close()
        nueva = dict(sh.MIGRACIONES)
        nueva[max(nueva) + 1] = "CREATE TABLE extra(x INTEGER) STRICT;"
        from unittest import mock
        with mock.patch.object(sh, "MIGRACIONES", nueva):
            self.con = sh.abrir()
        for f in os.listdir(os.path.join(self.dir, "copias")):
            with open(os.path.join(self.dir, "copias", f), "rb") as fh:
                self.assertNotIn(b"nombre-que-no-debe-quedar", fh.read())

    def test_migracion_de_v2_a_v3_conserva_los_datos(self):
        self.reg(self.lectura(T0, [fila("a")]))
        self.con.close()
        c = sqlite3.connect(sh.ruta(), isolation_level=None)
        c.execute("DROP TABLE recursos")
        c.execute("DROP TABLE eventos")
        c.execute("PRAGMA user_version=2")
        c.close()
        self.con = sh.abrir()
        self.assertEqual(self.uno("PRAGMA user_version"), 3)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 1)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos"), 0)


NOMBRE_SRV = SRV["nombre"]


if __name__ == "__main__":
    unittest.main()
