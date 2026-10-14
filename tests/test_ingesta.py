"""Ingesta de Claude Code al almacén: tiempo UTC con zona obligatoria, un turno por mensaje (máximo de cada contador),
lectura incremental y reanudable, líneas hostiles anotadas sin tumbar la pasada y equivalencia con el parser actual."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, ingesta, ingesta_claude, lectores_logs  # noqa: E402


def linea(mid="msg_01ABCDEFGHIJKLMN", ts="2026-09-27T20:45:08.343Z", out=5, inp=10, cr=100, cw=20, modelo="claude-sonnet-5", **extra):
    d = {"type": "assistant", "timestamp": ts, "sessionId": "ses-1", "cwd": "/Users/yo/Proyectos/demo/app",
         "message": {"id": mid, "model": modelo, "usage": {"input_tokens": inp, "output_tokens": out,
                                                           "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}
    d.update(extra)
    return json.dumps(d)


class EpochTest(unittest.TestCase):
    def test_con_zona(self):
        base = 1790541908
        for v in ("2026-09-27T20:45:08Z", "2026-09-27T20:45:08.343Z", "2026-09-27T22:45:08+02:00", "2026-09-27T22:45:08+0200",
                  "2026-09-27T22:45:08+02", "2026-09-27 20:45:08.343456789Z"):
            self.assertEqual(ingesta.a_epoch_utc(v), base, v)

    def test_epoch_en_segundos_y_milisegundos(self):
        self.assertEqual(ingesta.a_epoch_utc(1790541908), 1790541908)
        self.assertEqual(ingesta.a_epoch_utc(1790541908123), 1790541908)
        self.assertEqual(ingesta.a_epoch_utc("1790541908123"), 1790541908)

    def test_sin_zona_o_ilegible_es_none(self):
        for v in ("2026-09-27T20:45:08", "2026-09-27", "ayer", "", None, True, [], {}, "2026-02-30T10:00:00Z", float("nan"), "2026-13-01T00:00:00Z"):
            self.assertIsNone(ingesta.a_epoch_utc(v), repr(v))

    def test_cambio_de_hora_de_octubre_no_junta_dos_horas(self):
        a, b = ingesta.a_epoch_utc("2026-10-25T02:30:00+02:00"), ingesta.a_epoch_utc("2026-10-25T02:30:00+01:00")
        self.assertEqual(b - a, 3600)                                   # la misma hora de reloj, dos instantes distintos
        self.assertEqual(ingesta.a_epoch_utc("2026-03-29T01:30:00Z") - ingesta.a_epoch_utc("2026-03-29T00:30:00Z"), 3600)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-ingesta-")
        self.home = os.path.join(self.dir, "home")
        self.carpeta = os.path.join(self.home, ".claude", "projects", "-Users-yo-Proyectos-demo-app")
        os.makedirs(self.carpeta)
        self.ruta = os.path.join(self.carpeta, "sesion.jsonl")
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
        return ingesta.ingerir_fichero(self.con, self.quien, self.esp, self.ruta, "claude", ingesta_claude.extractor, self.home,
                                       contexto=ingesta_claude.contexto_de_fichero(self.ruta), **kw)

    def turnos(self):
        return {f["clave"]: dict(f) for f in self.con.execute("SELECT * FROM turnos")}

    def anomalias(self):
        return {(a["tipo"], a["detalle"]) for a in almacen.anomalias(self.con, 500)}


class ClaudeTest(Base):
    def test_varias_lineas_por_mensaje_son_un_turno_con_la_ultima_salida(self):
        self.escribe([linea(out=6, ts="2026-09-27T20:45:08Z"), linea(out=6, ts="2026-09-27T20:45:09Z"), linea(out=321, ts="2026-09-27T20:45:11Z"),
                      linea(mid="msg_01ZZZZZZZZZZZZZZ", out=40, ts="2026-09-27T20:50:00Z")])
        r = self.pasada()
        self.assertEqual((r["nuevos"], r["lineas"]), (2, 4))
        t = self.turnos()
        self.assertEqual(t["claude:msg_01ABCDEFGHIJKLMN"]["go"], 321)
        self.assertEqual((t["claude:msg_01ABCDEFGHIJKLMN"]["gi"], t["claude:msg_01ABCDEFGHIJKLMN"]["cr"]), (10, 100))     # entrada y caché una sola vez

    def test_el_proyecto_no_lleva_el_usuario_del_mac(self):
        self.escribe([linea()])
        self.pasada()
        proyecto = next(iter(self.turnos().values()))["proyecto_raw"]
        self.assertEqual(proyecto, "Proyectos/demo/app")
        self.assertNotIn("yo", proyecto.split("/"))
        self.assertEqual(ingesta_claude.proyecto_de_ruta("/h/.claude/projects/-home-ana-x-y/s.jsonl"), "x/y")
        self.assertEqual(ingesta_claude.proyecto_de_ruta("/fuera/de/projects.jsonl"), "otros")

    def test_la_fuente_no_guarda_rutas_absolutas(self):
        self.escribe([linea()])
        self.pasada()
        f = self.con.execute("SELECT id_fuente FROM fuentes").fetchone()[0]
        self.assertRegex(f, r"^[0-9a-f]{32}$")
        self.assertNotIn("yo", f)

    def test_id_no_anthropic_lleva_la_sesion_en_la_clave(self):
        self.escribe([linea(mid="msg_1790000000000_ab12cd"), linea(mid="msg_01ABCDEFGHIJKLMN")])
        self.pasada()
        self.assertEqual(set(self.turnos()), {"claude:ses-1:msg_1790000000000_ab12cd", "claude:msg_01ABCDEFGHIJKLMN"})

    def test_sidechain_se_cuenta(self):
        self.escribe([linea(isSidechain=True)])
        self.assertEqual(self.pasada()["nuevos"], 1)

    def test_lineas_sin_uso_o_de_otro_tipo_se_ignoran(self):
        self.escribe([json.dumps({"type": "user", "message": {"content": "hola"}}), json.dumps([1, 2]), "", linea()])
        self.assertEqual(self.pasada()["nuevos"], 1)

    def test_sin_modelo_usa_el_ultimo_visto_o_sin_modelo(self):
        sin = json.loads(linea(mid="msg_01SINMODELO00000"))
        del sin["message"]["model"]
        self.escribe([linea(), json.dumps(sin)])
        self.pasada()
        self.assertEqual(self.turnos()["claude:msg_01SINMODELO00000"]["modelo"], "claude-sonnet-5")


class IncrementalTest(Base):
    def test_segunda_pasada_solo_lee_lo_nuevo(self):
        self.escribe([linea(mid="msg_01AAAAAAAAAAAAAA"), linea(mid="msg_01BBBBBBBBBBBBBB")])
        self.assertEqual(self.pasada()["lineas"], 2)
        self.escribe([linea(mid="msg_01CCCCCCCCCCCCCC")], modo="a")
        r = self.pasada()
        self.assertEqual((r["lineas"], r["nuevos"]), (1, 1))
        self.assertEqual(self.pasada()["lineas"], 0)

    def test_linea_a_medias_no_avanza_hasta_completarse(self):
        completa = linea(mid="msg_01AAAAAAAAAAAAAA")
        mitad = linea(mid="msg_01BBBBBBBBBBBBBB")
        with open(self.ruta, "w") as f:
            f.write(completa + "\n" + mitad[:40])
        self.assertEqual(self.pasada()["nuevos"], 1)
        with open(self.ruta, "a") as f:
            f.write(mitad[40:] + "\n")
        r = self.pasada()
        self.assertEqual((r["nuevos"], r["rotas"]), (1, 0))

    def test_fichero_reescrito_o_truncado_se_relee_sin_duplicar(self):
        self.escribe([linea(mid=f"msg_01{i:014d}") for i in range(5)])
        self.pasada()
        self.escribe([linea(mid="msg_01NUEVO000000000")])              # se truncó y empezó otro contenido
        r = self.pasada()
        self.assertEqual(r["nuevos"], 1)
        self.assertEqual(len(self.turnos()), 6)                        # los anteriores se conservan: el histórico no se pierde
        self.escribe([linea(mid=f"msg_01{i:014d}") for i in range(5)])
        self.assertEqual(self.pasada()["nuevos"], 0)                   # releer lo ya visto no duplica

    def test_pasadas_pequenas_reanudan_y_dan_lo_mismo_que_una_sola(self):
        self.escribe([linea(mid=f"msg_01{i:014d}", out=i + 1) for i in range(60)])
        for _ in range(200):
            if self.pasada(max_bytes=700)["completo"]:
                break
        else:
            self.fail("no terminó")
        a = {k: (v["gi"], v["go"], v["cr"], v["cw"]) for k, v in self.turnos().items()}
        self.con.execute("DELETE FROM turnos"); self.con.execute("DELETE FROM horas"); self.con.execute("DELETE FROM lecturas")
        self.pasada()
        self.assertEqual(a, {k: (v["gi"], v["go"], v["cr"], v["cw"]) for k, v in self.turnos().items()})
        self.assertEqual(len(a), 60)

    def test_un_mensaje_en_streaming_a_traves_de_varias_pasadas(self):
        self.escribe([linea(out=6, ts="2026-09-27T20:45:08Z")])
        self.pasada()
        self.escribe([linea(out=100, ts="2026-09-27T20:45:20Z")], modo="a")
        self.pasada()
        self.escribe([linea(out=321, ts="2026-09-27T20:46:02Z")], modo="a")
        r = self.pasada()
        t = next(iter(self.turnos().values()))
        self.assertEqual((t["go"], t["hora_utc"], r["anomalias"]), (321, ingesta.a_epoch_utc("2026-09-27T20:45:08Z"), 0))
        self.assertEqual(len(almacen.anomalias(self.con)), 0)

    def test_un_log_que_crece_con_el_mismo_mensaje_sube_la_salida(self):
        self.escribe([linea(out=6)])
        self.pasada()
        self.escribe([linea(out=321)], modo="a")
        self.pasada()
        self.assertEqual(next(iter(self.turnos().values()))["go"], 321)


class HostilTest(Base):
    def test_lineas_rotas_o_hostiles_no_tumban_la_pasada(self):
        enorme = linea(mid="msg_01ENORME000000000", modelo="m" * 3_000_000)
        self.escribe(["{no es json", linea(mid="msg_01MALOS000000000", out=10 ** 15), linea(mid="msg_01SINZONA0000000", ts="2026-09-27T20:45:08"),
                      linea(mid="msg_01NUL0000000000", modelo="a\x00b"), enorme, '{"message": {"usage": {"input_tokens": "uno"}, "id": "msg_01X0000000"}}',
                      linea(mid="msg_01BUENO000000000")])
        r = self.pasada()
        self.assertEqual(r["nuevos"], 1)
        self.assertEqual(set(self.turnos()), {"claude:msg_01BUENO000000000"})
        tipos = {t for t, _ in self.anomalias()}
        self.assertTrue({"lineas_no_json", "lineas_enormes", "ingesta", "fuera_de_rango"} <= tipos, tipos)
        self.assertEqual(self.pasada()["nuevos"], 0)                    # relectura estable

    def test_json_muy_anidado_no_revienta(self):
        self.escribe(["[" * 100000 + "]" * 100000, linea()])
        self.assertEqual(self.pasada()["nuevos"], 1)

    def test_la_misma_clave_con_otro_modelo_en_el_mismo_fichero_manda_la_primera(self):
        self.escribe([linea(modelo="claude-sonnet-5"), linea(modelo="claude-opus-5", out=999999)])
        self.pasada()
        t = next(iter(self.turnos().values()))
        self.assertEqual((t["modelo"], t["go"]), ("claude-sonnet-5", 5))

    def test_una_linea_inyectada_en_el_log_no_cambia_un_turno_ya_guardado(self):
        self.escribe([linea(out=300)])
        self.pasada()
        self.escribe([linea(out=0, inp=0, cr=0, cw=0, ts="2026-12-24T00:00:00Z")], modo="a")
        self.pasada()
        t = next(iter(self.turnos().values()))
        self.assertEqual((t["go"], t["hora_utc"]), (300, ingesta.a_epoch_utc("2026-09-27T20:45:08Z")))


class EquivalenciaTest(Base):
    def test_los_totales_por_modelo_coinciden_con_el_parser_actual(self):
        lineas = []
        for i in range(30):
            mid = f"msg_01{i:014d}"
            for j, out in enumerate((6, 6, 50 + i)):
                lineas.append(linea(mid=mid, out=out, inp=2 + i, cr=1000 + i, cw=10, ts=f"2026-09-27T{8 + i // 6:02d}:{(i * 7) % 60:02d}:{j:02d}Z",
                                    modelo="claude-sonnet-5" if i % 3 else "claude-opus-5"))
        self.escribe(lineas)
        self.pasada()
        db = {}
        for f in self.con.execute("SELECT modelo, SUM(gi), SUM(go), SUM(cr), SUM(cw), COUNT(*) FROM turnos GROUP BY modelo"):
            db[f[0]] = tuple(f)[1:]
        viejo = lectores_logs.parsear(self.ruta)
        esperado = {}
        for dia in viejo["dia"].values():
            for modelo, v in dia.items():
                a = esperado.setdefault(modelo, [0, 0, 0, 0])
                for i in range(4):
                    a[i] += v[i]
        turnos = sum(viejo["turnos"].values())
        self.assertEqual({m: v[:4] for m, v in db.items()}, {m: tuple(v) for m, v in esperado.items()})
        self.assertEqual(sum(v[4] for v in db.values()), turnos)


if __name__ == "__main__":
    unittest.main()
