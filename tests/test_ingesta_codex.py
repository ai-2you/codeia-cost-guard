"""Ingesta de Codex: solo token_count, entrada fresca = input − cached, sesión/modelo/proyecto de líneas anteriores
persistidos entre pasadas, y equivalencia con el parser actual."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, ingesta, ingesta_codex, lectores_logs  # noqa: E402

SESION = "01a137be-aaaa-bbbb-cccc-1234567890ab"


def meta(cwd="/Users/yo/Proyectos/demo/app", ts="2026-10-02T00:20:00.000Z"):
    return json.dumps({"timestamp": ts, "type": "session_meta", "payload": {"id": SESION, "session_id": SESION, "cwd": cwd, "model_provider": "openai"}})


def contexto(modelo="gpt-6.1-sol", ts="2026-10-02T00:20:01.000Z"):
    return json.dumps({"timestamp": ts, "type": "turn_context", "payload": {"model": modelo, "cwd": "/x"}})


def cuenta(total, ts, inp=1000, cached=900, out=100, escritura=0, extra=None):
    uso = {"input_tokens": inp, "cached_input_tokens": cached, "cache_write_input_tokens": escritura, "output_tokens": out,
           "reasoning_output_tokens": 10, "total_tokens": inp + out}
    return json.dumps({"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count",
                       "info": {"last_token_usage": uso, "total_token_usage": {"total_tokens": total}, "model_context_window": 1}, **(extra or {})}})


def registro(ts, inp=1000, cached=900, out=100):
    uso = {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out, "total_tokens": inp + out}
    return json.dumps({"timestamp": ts, "type": "token_usage_record", "payload": {"usage": uso, "response_id": "r"}})


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-codex-")
        self.home = os.path.join(self.dir, "home")
        carpeta = os.path.join(self.home, ".codex", "sessions", "2026", "10", "02")
        os.makedirs(carpeta)
        self.ruta = os.path.join(carpeta, f"rollout-2026-10-02T00-20-00-{SESION}.jsonl")
        self.con = almacen.abrir(os.path.join(self.dir, "datos", "c.sqlite"))
        self.quien = almacen.identidad("barra")
        almacen.adquirir_escritura(self.con, self.quien)
        self.esp = almacen.espacio(self.con, self.quien)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def escribe(self, lineas, modo="w"):
        with open(self.ruta, modo, encoding="utf-8") as f:
            f.write("".join(l + "\n" for l in lineas))

    def pasada(self, **kw):
        return ingesta.ingerir_fichero(self.con, self.quien, self.esp, self.ruta, "codex", ingesta_codex.extractor, self.home,
                                       contexto=ingesta_codex.contexto_de_fichero(self.ruta), **kw)

    def turnos(self):
        return [dict(f) for f in self.con.execute("SELECT * FROM turnos ORDER BY hora_utc")]


class CodexTest(Base):
    def test_entrada_fresca_cache_y_doble_registro(self):
        self.escribe([meta(), contexto(), registro("2026-10-02T00:20:26.366Z"), cuenta(1100, "2026-10-02T00:20:26.366Z")])
        self.assertEqual(self.pasada()["nuevos"], 1)
        t = self.turnos()[0]
        self.assertEqual((t["gi"], t["go"], t["cr"], t["cw"]), (100, 100, 900, 0))           # cached va DENTRO de input
        self.assertEqual((t["modelo"], t["proveedor"], t["agente"]), ("gpt-6.1-sol", "openai", "codex"))
        self.assertTrue(t["clave"].startswith(f"codex:{SESION}:1100:"))

    def test_el_acumulado_no_se_suma(self):
        self.escribe([meta(), contexto(), cuenta(1100, "2026-10-02T00:20:26Z"), cuenta(2200, "2026-10-02T00:21:26Z"), cuenta(3300, "2026-10-02T00:22:26Z")])
        self.pasada()
        self.assertEqual(sum(t["gi"] + t["go"] + t["cr"] for t in self.turnos()), 3 * 1100)

    def test_una_repeticion_exacta_es_el_mismo_turno_y_el_mismo_total_con_otro_uso_son_dos(self):
        self.escribe([meta(), contexto(), cuenta(1100, "2026-10-02T00:20:26Z"), cuenta(1100, "2026-10-02T00:20:27Z"),
                      cuenta(1100, "2026-10-02T00:20:28Z", out=50)])
        self.pasada()
        self.assertEqual(len(self.turnos()), 2)

    def test_proyecto_de_cwd_sin_usuario(self):
        self.escribe([meta(), contexto(), cuenta(1100, "2026-10-02T00:20:26Z")])
        self.pasada()
        self.assertEqual(self.turnos()[0]["proyecto_raw"], "Proyectos/demo/app")

    def test_sin_session_meta_la_sesion_sale_del_nombre_del_fichero(self):
        self.escribe([contexto(), cuenta(1100, "2026-10-02T00:20:26Z")])
        self.pasada()
        self.assertIn(SESION, self.turnos()[0]["clave"])

    def test_el_contexto_sobrevive_entre_pasadas(self):
        self.escribe([meta(), contexto("gpt-6.1-sol"), cuenta(1100, "2026-10-02T00:20:26Z")])
        self.pasada()
        self.escribe([contexto("gpt-7-nuevo", "2026-10-02T01:00:00Z"), cuenta(2200, "2026-10-02T01:00:05Z")], modo="a")
        self.escribe([cuenta(3300, "2026-10-02T01:05:00Z")], modo="a")
        r = self.pasada()
        self.assertEqual(r["lineas"], 3)
        self.assertEqual([t["modelo"] for t in self.turnos()], ["gpt-6.1-sol", "gpt-7-nuevo", "gpt-7-nuevo"])
        self.escribe([cuenta(4400, "2026-10-02T01:10:00Z")], modo="a")                  # pasada que NO ve el turn_context: usa el contexto guardado
        self.pasada()
        self.assertEqual(self.turnos()[-1]["modelo"], "gpt-7-nuevo")

    def test_lineas_hostiles(self):
        self.escribe([meta(), contexto(), cuenta(1, "2026-10-02T00:20:26"), cuenta(2, "ayer"),
                      json.dumps({"timestamp": "2026-10-02T00:20:26Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": "uno"}}}}),
                      json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": None}}),
                      json.dumps({"type": "event_msg", "payload": "texto"}), json.dumps({"payload": []}),
                      cuenta(3300, "2026-10-02T00:30:00Z", inp=10 ** 15)])
        r = self.pasada()
        self.assertEqual(r["nuevos"], 0)
        self.assertEqual(self.pasada()["lineas"], 0)

    def test_sesion_hostil_en_session_meta_se_ignora(self):
        malo = json.loads(meta())
        malo["payload"]["session_id"] = "x'; DROP TABLE turnos; --"
        malo["payload"]["id"] = "a" * 500
        self.escribe([json.dumps(malo), contexto(), cuenta(1100, "2026-10-02T00:20:26Z")])
        self.pasada()
        self.assertIn(SESION, self.turnos()[0]["clave"])                            # cae al id del nombre de fichero

    def test_reanudar_en_pasadas_pequenas_da_lo_mismo(self):
        self.escribe([meta(), contexto()] + [cuenta(1100 * (i + 1), f"2026-10-02T00:{i:02d}:00Z", out=100 + i) for i in range(40)])
        for _ in range(300):
            if self.pasada(max_bytes=600)["completo"]:
                break
        a = [(t["clave"], t["gi"], t["go"], t["cr"]) for t in self.turnos()]
        self.assertEqual(len(a), 40)
        self.con.execute("DELETE FROM turnos"); self.con.execute("DELETE FROM horas"); self.con.execute("DELETE FROM lecturas")
        self.pasada()
        self.assertEqual(a, [(t["clave"], t["gi"], t["go"], t["cr"]) for t in self.turnos()])


class EquivalenciaTest(Base):
    def test_totales_iguales_al_parser_actual(self):
        lineas = [meta(), contexto("gpt-6.1-sol")]
        for i in range(25):
            ts = f"2026-10-02T{1 + i // 5:02d}:{(i * 7) % 60:02d}:{i % 60:02d}.123Z"
            lineas += [registro(ts, 2000 + i, 1500, 80 + i), cuenta(3000 * (i + 1), ts, inp=2000 + i, cached=1500, out=80 + i)]
        self.escribe(lineas)
        self.pasada()
        viejo = lectores_logs.parsear(self.ruta)
        esperado = [0, 0, 0, 0]
        for dia in viejo["dia"].values():
            for v in dia.values():
                for i in range(4):
                    esperado[i] += v[i]
        db = self.con.execute("SELECT SUM(gi), SUM(go), SUM(cr), SUM(cw), COUNT(*) FROM turnos").fetchone()
        self.assertEqual(list(db)[:4], esperado)
        self.assertEqual(db[4], sum(viejo["turnos"].values()))


if __name__ == "__main__":
    unittest.main()
