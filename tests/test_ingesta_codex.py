"""Ingesta de Codex: token_usage_record con response_id, entrada fresca = input − cached, compactaciones y re-emisiones de
token_count, forks, contexto persistido entre pasadas, líneas hostiles y equivalencia con el parser actual."""
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


def meta(cwd="/Users/yo/Proyectos/demo/app", ts="2026-10-02T00:20:00.000Z", proveedor="openai"):
    return json.dumps({"timestamp": ts, "type": "session_meta", "payload": {"id": SESION, "session_id": SESION, "cwd": cwd, "model_provider": proveedor}})


def contexto(modelo="gpt-6.1-sol", ts="2026-10-02T00:20:01.000Z"):
    return json.dumps({"timestamp": ts, "type": "turn_context", "payload": {"model": modelo, "cwd": "/x"}})


def uso(inp=1000, cached=900, out=100, escritura=0):
    return {"input_tokens": inp, "cached_input_tokens": cached, "cache_write_input_tokens": escritura, "output_tokens": out,
            "reasoning_output_tokens": 10, "total_tokens": inp + out}


def registro(resp, ts, **kw):
    u = uso(**kw)
    return json.dumps({"timestamp": ts, "type": "token_usage_record", "payload": {"response_id": resp, "session_id": SESION, "usage": u,
                       "turn_token_usage": u, "thread_token_usage": u}})


def cuenta(ts, total=1100, **kw):
    return json.dumps({"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "info": {
        "last_token_usage": uso(**kw), "total_token_usage": {"total_tokens": total}, "model_context_window": 1}}})


def respuesta(n, ts, **kw):
    """Lo que deja Codex por respuesta: el registro y su token_count."""
    return [registro(f"resp_{n:08d}", ts, **kw), cuenta(ts, total=1100 * (n + 1), **kw)]


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

    def escribe(self, lineas, modo="w", ruta=None):
        with open(ruta or self.ruta, modo, encoding="utf-8") as f:
            f.write("".join(l + "\n" for l in lineas))

    def pasada(self, ruta=None, **kw):
        ruta = ruta or self.ruta
        return ingesta.ingerir_fichero(self.con, self.quien, self.esp, ruta, "codex", ingesta_codex.extractor, self.home,
                                       contexto=ingesta_codex.contexto_de_fichero(ruta), **kw)

    def turnos(self):
        return [dict(f) for f in self.con.execute("SELECT * FROM turnos ORDER BY hora_utc, clave")]

    def anomalias(self):
        return {(a["tipo"], a["detalle"]) for a in almacen.anomalias(self.con, 500)}


class CodexTest(Base):
    def test_entrada_fresca_cache_y_clave_por_response_id(self):
        self.escribe([meta(), contexto()] + respuesta(0, "2026-10-02T00:20:26.366Z"))
        self.assertEqual(self.pasada()["nuevos"], 1)
        t = self.turnos()[0]
        self.assertEqual((t["gi"], t["go"], t["cr"], t["cw"]), (100, 100, 900, 0))           # cached va DENTRO de input
        self.assertEqual((t["modelo"], t["proveedor"], t["agente"], t["clave"]), ("gpt-6.1-sol", "openai", "codex", "codex:resp_00000000"))

    def test_re_emisiones_de_token_count_no_se_cuentan(self):
        lineas = [meta(), contexto(), registro("resp_00000001", "2026-10-02T00:20:26Z"), cuenta("2026-10-02T00:20:26Z"),
                  cuenta("2026-10-02T00:20:41Z"), cuenta("2026-10-02T00:21:41Z")]        # el mismo acumulado re-emitido en otros segundos
        self.escribe(lineas)
        self.assertEqual(self.pasada()["nuevos"], 1)
        self.assertEqual(len(self.turnos()), 1)

    def test_una_compactacion_con_token_count_a_cero_cuenta_por_el_registro(self):
        lineas = [meta(), contexto(), registro("resp_00000002", "2026-10-02T00:20:26Z", inp=50000, cached=1000, out=700),
                  cuenta("2026-10-02T00:20:26Z", inp=0, cached=0, out=0)]
        self.escribe(lineas)
        self.pasada()
        t = self.turnos()
        self.assertEqual((len(t), t[0]["gi"], t[0]["go"], t[0]["cr"]), (1, 49000, 700, 1000))

    def test_un_fork_con_las_lineas_del_padre_no_duplica(self):
        padre = [meta(), contexto()] + respuesta(0, "2026-10-02T00:20:26Z") + respuesta(1, "2026-10-02T00:21:26Z")
        hijo_ruta = os.path.join(os.path.dirname(self.ruta), "rollout-2026-10-02T00-30-00-hijo.jsonl")
        self.escribe(padre)
        self.escribe(padre + respuesta(2, "2026-10-02T00:40:26Z"), ruta=hijo_ruta)
        self.pasada()
        self.pasada(ruta=hijo_ruta)
        self.assertEqual(len(self.turnos()), 3)

    def test_token_count_sin_ningun_registro_se_anota_y_no_se_cuenta(self):
        self.escribe([meta(), contexto(), cuenta("2026-10-02T00:20:26Z")])
        r = self.pasada()
        self.assertEqual(r["nuevos"], 0)
        self.assertTrue(any("token_count sin token_usage_record" in d for _, d in self.anomalias()))

    def test_proyecto_de_cwd_sin_usuario(self):
        self.escribe([meta(), contexto()] + respuesta(0, "2026-10-02T00:20:26Z"))
        self.pasada()
        self.assertEqual(self.turnos()[0]["proyecto_raw"], "Proyectos/demo/app")

    def test_el_contexto_sobrevive_entre_pasadas(self):
        self.escribe([meta(), contexto("gpt-6.1-sol")] + respuesta(0, "2026-10-02T00:20:26Z"))
        self.pasada()
        self.escribe([contexto("gpt-7-nuevo", "2026-10-02T01:00:00Z")] + respuesta(1, "2026-10-02T01:00:05Z"), modo="a")
        self.pasada()
        self.escribe(respuesta(2, "2026-10-02T01:05:00Z"), modo="a")                          # pasada que NO ve el turn_context
        self.pasada()
        self.assertEqual([t["modelo"] for t in self.turnos()], ["gpt-6.1-sol", "gpt-7-nuevo", "gpt-7-nuevo"])

    def test_lineas_hostiles(self):
        self.escribe([meta(), contexto(), registro("resp_00000009", "2026-10-02T00:20:26"), registro("resp_00000010", "ayer"),
                      registro("x'; DROP TABLE turnos; --", "2026-10-02T00:20:26Z"), registro("resp_00000011", "2026-10-02T00:20:26Z", inp=10 ** 15),
                      json.dumps({"timestamp": "2026-10-02T00:20:26Z", "type": "token_usage_record", "payload": {"response_id": "resp_00000012", "usage": {"input_tokens": "uno"}}}),
                      json.dumps({"type": "token_usage_record", "payload": {"response_id": "resp_00000013", "usage": None}}),
                      json.dumps({"type": "event_msg", "payload": "texto"}), json.dumps({"payload": []})])
        self.assertEqual(self.pasada()["nuevos"], 0)
        self.assertEqual(self.pasada()["lineas"], 0)

    def test_un_modelo_con_sustituto_suelto_no_bloquea_el_fichero(self):
        self.escribe([meta(), json.dumps({"type": "turn_context", "payload": {"model": "gpt\ud800x"}}),
                      json.dumps({"type": "turn_context", "payload": {"model": "ok-modelo"}})] + respuesta(0, "2026-10-02T00:20:26Z"))
        self.assertEqual(self.pasada()["nuevos"], 1)
        self.assertEqual(self.pasada()["lineas"], 0)                                         # la lectura se guardó

    def test_el_contexto_guardado_descarta_sustitutos_control_y_tamanos_absurdos(self):
        ctx = {"modelo": "a\ud800", "proyecto": "ok", "x\x00": "v", "largo": "y" * 500, "bien": "valor"}
        self.assertEqual(json.loads(ingesta._serializa_contexto(ctx)), {"proyecto": "ok", "bien": "valor"})
        self.assertEqual(ingesta._serializa_contexto({f"k{i}": "v" * 190 for i in range(30)}), "{}")

    def test_proveedor_y_proyecto_hostiles_se_ignoran(self):
        self.escribe([meta(cwd="/" + "x" * 5000, proveedor="p\x00q"), contexto()] + respuesta(0, "2026-10-02T00:20:26Z"))
        self.assertEqual(self.pasada()["nuevos"], 1)
        t = self.turnos()[0]
        self.assertEqual((t["proveedor"], t["proyecto_raw"]), ("openai", "codex/otros"))

    def test_base_con_esquema_de_desarrollo_obsoleto_da_error_claro(self):
        ruta = os.path.join(self.dir, "vieja.sqlite")
        almacen.abrir(ruta).close()
        import sqlite3
        con = sqlite3.connect(ruta)
        con.execute("DROP TABLE lecturas")
        con.execute("CREATE TABLE lecturas(fuente_id INTEGER PRIMARY KEY, inodo INTEGER)")
        con.commit()
        con.close()
        with self.assertRaises(almacen.ErrorAlmacen):
            almacen.abrir(ruta)

    def test_reanudar_en_pasadas_pequenas_da_lo_mismo(self):
        lineas = [meta(), contexto()]
        for i in range(40):
            lineas += respuesta(i, f"2026-10-02T00:{i:02d}:00Z", out=100 + i)
        self.escribe(lineas)
        for _ in range(400):
            if self.pasada(max_bytes=900)["completo"]:
                break
        a = [(t["clave"], t["gi"], t["go"], t["cr"], t["modelo"]) for t in self.turnos()]
        self.assertEqual(len(a), 40)
        self.con.execute("DELETE FROM turnos"); self.con.execute("DELETE FROM horas"); self.con.execute("DELETE FROM lecturas")
        self.pasada()
        self.assertEqual(a, [(t["clave"], t["gi"], t["go"], t["cr"], t["modelo"]) for t in self.turnos()])


class EquivalenciaTest(Base):
    def _parser(self):
        viejo = lectores_logs.parsear(self.ruta)
        esperado = [0, 0, 0, 0]
        for dia in viejo["dia"].values():
            for v in dia.values():
                for i in range(4):
                    esperado[i] += v[i]
        return esperado, sum(viejo["turnos"].values())

    def test_totales_iguales_al_parser_actual_sin_re_emisiones(self):
        lineas = [meta(), contexto("gpt-6.1-sol")]
        for i in range(25):
            lineas += respuesta(i, f"2026-10-02T{1 + i // 5:02d}:{(i * 7) % 60:02d}:{i % 60:02d}.123Z", inp=2000 + i, cached=1500, out=80 + i)
        self.escribe(lineas)
        self.pasada()
        esperado, turnos = self._parser()
        db = self.con.execute("SELECT SUM(gi), SUM(go), SUM(cr), SUM(cw), COUNT(*) FROM turnos").fetchone()
        self.assertEqual((list(db)[:4], db[4]), (esperado, turnos))

    def test_diverge_del_parser_actual_justo_en_las_re_emisiones(self):
        base = [meta(), contexto()] + respuesta(0, "2026-10-02T00:20:26.100Z", inp=2000, cached=1500, out=80)
        self.escribe(base + [cuenta("2026-10-02T00:20:41.900Z", total=1100, inp=2000, cached=1500, out=80)])        # re-emisión en otro segundo
        self.pasada()
        esperado, turnos = self._parser()
        db = self.con.execute("SELECT SUM(gi), SUM(go), SUM(cr), SUM(cw), COUNT(*) FROM turnos").fetchone()
        self.assertEqual(db[4], 1)
        self.assertEqual(turnos, 2)                                                    # el parser actual la cuenta dos veces (divergencia a firmar)
        self.assertEqual(list(db)[:4], [x // 2 for x in esperado])


if __name__ == "__main__":
    unittest.main()
