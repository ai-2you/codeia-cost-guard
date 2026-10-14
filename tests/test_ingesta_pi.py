"""Ingesta de pi: clave por id de línea (los forks se deduplican), reasoning sumado a la salida, proveedor por turno,
líneas hostiles y equivalencia con el parser actual."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, ingesta, ingesta_pi, lectores_logs  # noqa: E402


def cabecera(cwd="/Users/yo/Proyectos/demo/app"):
    return json.dumps({"type": "session", "version": "3", "id": "019e43f8-e", "timestamp": "2026-05-20T10:00:00.000Z", "cwd": cwd})


def mensaje(lid, ts="2026-05-20T10:01:00.000Z", inp=100, out=50, cr=900, cw=10, reasoning=7, proveedor="nodeclub", modelo="gpt-x", role="assistant", **extra):
    uso = {"input": inp, "output": out, "cacheRead": cr, "cacheWrite": cw, "totalTokens": inp + out, "cost": {"total": 0}}
    if reasoning is not None:
        uso["reasoning"] = reasoning
    d = {"type": "message", "id": lid, "parentId": None, "timestamp": ts,
         "message": {"role": role, "provider": proveedor, "model": modelo, "usage": uso, "timestamp": 1747735260000}}
    d.update(extra)
    return json.dumps(d)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-pi-")
        self.home = os.path.join(self.dir, "home")
        carpeta = os.path.join(self.home, ".pi", "agent", "sessions", "--Users-yo-Proyectos-demo-app--")
        os.makedirs(carpeta)
        self.ruta = os.path.join(carpeta, "2026-05-20_sesion.jsonl")
        self.con = almacen.abrir(os.path.join(self.dir, "datos", "c.sqlite"))
        self.quien = almacen.identidad("barra")
        almacen.adquirir_escritura(self.con, self.quien)
        self.esp = almacen.espacio(self.con, self.quien)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def escribe(self, lineas, modo="w", ruta=None):
        with open(ruta or self.ruta, modo, encoding="utf-8") as f:
            f.write("".join(l + "\n" for l in lineas))

    def pasada(self, ruta=None, **kw):
        ruta = ruta or self.ruta
        return ingesta.ingerir_fichero(self.con, self.quien, self.esp, ruta, "pi", ingesta_pi.extractor, self.home,
                                       contexto=ingesta_pi.contexto_de_fichero(ruta), **kw)

    def turnos(self):
        return {t["clave"]: dict(t) for t in (self.con.execute("SELECT * FROM turnos"))}

    def anomalias(self):
        return {(a["tipo"], a["detalle"]) for a in almacen.anomalias(self.con, 500)}


class PiTest(Base):
    def test_turno_basico_reasoning_se_suma_a_la_salida_y_proveedor_por_turno(self):
        self.escribe([cabecera(), mensaje("aaaa1111", proveedor="nodeclub"), mensaje("bbbb2222", proveedor="opencode-go")])
        self.assertEqual(self.pasada()["nuevos"], 2)
        t = self.turnos()["pi:aaaa1111"]
        self.assertEqual((t["gi"], t["go"], t["cr"], t["cw"], t["agente"]), (100, 57, 900, 10, "pi"))
        self.assertEqual({t["proveedor"] for t in self.turnos().values()}, {"nodeclub", "opencode-go"})

    def test_sin_reasoning_no_falla(self):
        self.escribe([cabecera(), mensaje("aaaa1111", reasoning=None)])
        self.pasada()
        self.assertEqual(self.turnos()["pi:aaaa1111"]["go"], 50)

    def test_proyecto_de_cwd_sin_usuario_y_comun_con_los_demas_agentes(self):
        self.escribe([cabecera(), mensaje("aaaa1111")])
        self.pasada()
        self.assertEqual(self.turnos()["pi:aaaa1111"]["proyecto_raw"], "Proyectos/demo/app")

    def test_sin_cabecera_el_proyecto_sale_de_la_carpeta(self):
        self.escribe([mensaje("aaaa1111")])
        self.pasada()
        self.assertEqual(self.turnos()["pi:aaaa1111"]["proyecto_raw"], "Proyectos/demo/app")

    def test_un_fork_con_las_lineas_del_padre_no_duplica(self):
        padre = [cabecera(), mensaje("aaaa1111"), mensaje("bbbb2222")]
        hijo = os.path.join(os.path.dirname(self.ruta), "2026-05-21_rama.jsonl")
        self.escribe(padre)
        self.escribe([cabecera().replace("019e43f8-e", "otra-sesion")] + padre[1:] + [mensaje("cccc3333")], ruta=hijo)
        self.pasada()
        self.pasada(ruta=hijo)
        self.assertEqual(set(self.turnos()), {"pi:aaaa1111", "pi:bbbb2222", "pi:cccc3333"})

    def test_otros_roles_y_lineas_sin_uso_se_ignoran(self):
        self.escribe([cabecera(), mensaje("aaaa1111", role="user"), json.dumps({"type": "compaction", "id": "zzzz9999"}),
                      json.dumps({"type": "message", "id": "yyyy8888", "message": {"role": "assistant", "content": "x"}}), mensaje("bbbb2222")])
        self.assertEqual(self.pasada()["nuevos"], 1)

    def test_lineas_hostiles(self):
        malo_tipos = json.loads(mensaje("tipo0001"))
        malo_tipos["message"]["usage"]["input"] = "cien"
        self.escribe([cabecera(), mensaje("x'; DROP TABLE turnos; --"), mensaje("hora0001", ts="2026-05-20T10:00:00"), mensaje("hora0002", ts="ayer"),
                      mensaje("mod00001", modelo="a\x00b"), mensaje("prov0001", proveedor="p" * 500), mensaje("gran0001", inp=10 ** 15),
                      json.dumps(malo_tipos), mensaje("sin_id", ), json.dumps({"type": "message", "message": {"role": "assistant", "usage": {"input": 1}}}),
                      mensaje("bueno001")])
        r = self.pasada()
        self.assertEqual(r["nuevos"], 2)                              # "sin_id" cumple el patrón de id; el resto, no
        self.assertEqual(set(self.turnos()), {"pi:sin_id", "pi:bueno001"})
        self.assertEqual(self.pasada()["lineas"], 0)

    def test_misma_clave_con_otro_modelo_no_cambia_lo_guardado(self):
        self.escribe([cabecera(), mensaje("aaaa1111", modelo="gpt-x", out=50)])
        self.pasada()
        self.escribe([mensaje("aaaa1111", modelo="otro", out=999999)], modo="a")
        self.pasada()
        t = self.turnos()["pi:aaaa1111"]
        self.assertEqual((t["modelo"], t["go"]), ("gpt-x", 57))

    def test_reanudar_en_pasadas_pequenas_da_lo_mismo(self):
        self.escribe([cabecera()] + [mensaje(f"id{i:06d}", ts=f"2026-05-20T10:{i:02d}:00Z", out=10 + i) for i in range(40)])
        for _ in range(400):
            if self.pasada(max_bytes=800)["completo"]:
                break
        a = {k: (v["gi"], v["go"], v["cr"], v["proyecto_raw"]) for k, v in self.turnos().items()}
        self.assertEqual(len(a), 40)
        self.con.execute("DELETE FROM turnos"); self.con.execute("DELETE FROM horas"); self.con.execute("DELETE FROM lecturas")
        self.pasada()
        self.assertEqual(a, {k: (v["gi"], v["go"], v["cr"], v["proyecto_raw"]) for k, v in self.turnos().items()})


class EquivalenciaTest(Base):
    def test_totales_iguales_al_parser_actual(self):
        self.escribe([cabecera()] + [mensaje(f"id{i:06d}", ts=f"2026-05-20T10:{i:02d}:{i:02d}.5Z", inp=100 + i, out=20 + i, cr=500, cw=i % 3,
                                              reasoning=i % 5, proveedor=("nodeclub", "nan")[i % 2]) for i in range(30)])
        self.pasada()
        viejo = lectores_logs.parsear_pi(self.ruta)
        esperado = [0, 0, 0, 0]
        turnos = 0
        for rec in viejo["proveedores"].values():
            turnos += sum(rec["turnos"].values())
            for dia in rec["dia"].values():
                for v in dia.values():
                    for i in range(4):
                        esperado[i] += v[i]
        db = self.con.execute("SELECT SUM(gi), SUM(go), SUM(cr), SUM(cw), COUNT(*) FROM turnos").fetchone()
        self.assertEqual((list(db)[:4], db[4]), (esperado, turnos))


if __name__ == "__main__":
    unittest.main()
