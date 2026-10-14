"""Presupuestos por proyecto y por plan: validación estricta, migración única, evaluación con los mismos totales del informe y avisos
que se dan una vez por umbral y periodo sin depender de state.json."""
import datetime
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from test_historico import ConHusoMadrid, turno, utc  # noqa: E402
from infobar import ajustes, avisos, config_y_estado, historico, plataforma, presupuestos, rutas  # noqa: E402

HOY = datetime.date(2026, 10, 14)            # miércoles
ACTIVO = {"almacen": "activo"}


def fila(**k):
    return {"ambito": "proyecto", "nombre": "Cliente A", "periodo": "mes", "tokens": 1000, **k}


class TestValidar(unittest.TestCase):
    def test_fila_buena_se_normaliza(self):
        self.assertEqual(presupuestos.validar([fila(nombre="  X  ")]), [{**fila(nombre="X"), "metrica": "total"}])

    def test_filas_malas_en_modo_estricto(self):
        malas = [fila(ambito="modelo"), fila(periodo="año"), fila(nombre=""), fila(nombre="x" * 201), fila(nombre="a\x00b"), fila(nombre="a\nb"),
                 fila(tokens=0), fila(tokens=-5), fila(tokens=10 ** 13 + 1), fila(tokens=True), fila(tokens=1.5), fila(tokens="1000"), "no", None]
        for m in malas:
            with self.assertRaises(ValueError, msg=repr(m)):
                presupuestos.validar([m])
        with self.assertRaises(ValueError):
            presupuestos.validar([fila(), fila()])                                  # repetida
        with self.assertRaises(ValueError):
            presupuestos.validar([fila(nombre=str(i)) for i in range(101)])         # demasiadas
        with self.assertRaises(ValueError):
            presupuestos.validar({"no": "lista"})

    def test_en_modo_tolerante_descarta_solo_las_malas(self):
        self.assertEqual([f["nombre"] for f in presupuestos.validar([fila(nombre="a"), fila(tokens=-1, nombre="b"), "x", fila(nombre="c")], estricto=False)],
                         ["a", "c"])
        self.assertEqual(presupuestos.validar("basura", estricto=False), [])


class TestPeriodos(unittest.TestCase):
    def test_semana_de_lunes_y_cambio_de_año(self):
        self.assertEqual(presupuestos.periodo_en_curso("semana", HOY), ("2026-10-12", datetime.date(2026, 10, 12), datetime.date(2026, 10, 19)))
        self.assertEqual(presupuestos.periodo_en_curso("semana", datetime.date(2026, 10, 12))[0], "2026-10-12")      # el propio lunes
        self.assertEqual(presupuestos.periodo_en_curso("semana", datetime.date(2026, 10, 18))[0], "2026-10-12")      # el domingo
        self.assertEqual(presupuestos.periodo_en_curso("mes", datetime.date(2026, 12, 31)), ("2026-12", datetime.date(2026, 12, 1), datetime.date(2027, 1, 1)))


class TestMigrar(unittest.TestCase):
    def setUp(self):
        self.guardado = []
        p = mock.patch.object(presupuestos, "guardar_config", side_effect=lambda parche: self.guardado.append(parche))
        p.start()
        self.addCleanup(p.stop)

    def test_pasa_los_presupuestos_mensuales_de_los_proyectos(self):
        conf = {"proyectos": {"u/x/a": {"alias": "Cliente A", "presupuesto_mes_tokens": 500},
                              "u/x/b": {"presupuesto_mes_tokens": 700}, "u/x/c": {"presupuesto_mes_tokens": 0}, "u/x/d": {"alias": "D"},
                              "u/x/e": {"presupuesto_mes_tokens": "mucho"}}}
        r = presupuestos.migrar(conf)
        self.assertEqual([(f["nombre"], f["tokens"]) for f in r], [("Cliente A", 500), ("u/x/b", 700)])
        self.assertEqual(self.guardado, [{"presupuestos": r}])

    def test_solo_una_vez(self):
        self.assertIsNone(presupuestos.migrar({"presupuestos": [], "proyectos": {"u/x/a": {"presupuesto_mes_tokens": 5}}}))
        self.assertEqual(self.guardado, [])

    def test_sin_nada_que_migrar_deja_la_lista_vacia_para_no_repetirlo(self):
        self.assertEqual(presupuestos.migrar({}), [])
        self.assertEqual(self.guardado, [{"presupuestos": []}])


class TestEvaluar(ConHusoMadrid):
    def test_totales_por_proyecto_y_plan_con_alias_y_ocultos(self):
        self.mete([turno("claude:a", utc("2026-10-10T10:00"), proyecto="u/x/a", gi=100, go=0, cr=0, cw=0),
                   turno("claude:b", utc("2026-10-11T10:00"), proyecto="u/x/a", gi=700, go=0, cr=0, cw=0),
                   turno("claude:c", utc("2026-10-12T10:00"), proyecto="u/x/oculto", gi=50, go=0, cr=0, cw=0),
                   turno("claude:v", utc("2026-09-30T10:00"), proyecto="u/x/a", gi=9999, go=0, cr=0, cw=0)])       # del mes anterior
        self.completa()
        conf = {**ACTIVO, "proyectos": {"u/x/a": {"alias": "Cliente A"}, "u/x/oculto": {"oculto": True}},
                "presupuestos": [fila(tokens=1000), fila(nombre="Claude (Max)", ambito="plan", tokens=900), fila(nombre="u/x/oculto", tokens=50),
                                 fila(nombre="Claude (Max)", ambito="plan", periodo="semana", tokens=100), fila(nombre="Fantasma", tokens=10)]}
        r = presupuestos.evaluar(conf, HOY)
        self.assertTrue(r["disponible"])
        e = {(x["ambito"], x["nombre"], x["periodo"]): x for x in r["estados"]}
        self.assertEqual((e[("proyecto", "Cliente A", "mes")]["usado"], e[("proyecto", "Cliente A", "mes")]["nivel"]), (800, 80))
        self.assertEqual((e[("plan", "Claude (Max)", "mes")]["usado"], e[("plan", "Claude (Max)", "mes")]["nivel"]), (850, 80))
        self.assertEqual((e[("proyecto", "u/x/oculto", "mes")]["usado"], e[("proyecto", "u/x/oculto", "mes")]["nivel"]), (50, 100))   # ocultar no libera
        sem = e[("plan", "Claude (Max)", "semana")]
        self.assertEqual((sem["usado"], sem["nivel"], sem["periodo_clave"]), (50, 0, "2026-10-12"))          # la semana empieza el lunes 12
        self.assertFalse(e[("proyecto", "Fantasma", "mes")]["visto"])
        self.assertTrue(e[("proyecto", "Cliente A", "mes")]["visto"])

    def test_los_umbrales_son_exactos(self):
        self.mete([turno("claude:a", utc("2026-10-10T10:00"), proyecto="u/x/proy", gi=79, go=0, cr=0, cw=0)])
        self.completa()
        conf = {**ACTIVO, "presupuestos": [fila(nombre="u/x/proy", tokens=100)]}
        for usado, nivel in ((79, 0), (80, 80), (99, 80), (100, 100), (250, 100)):
            self.con.execute("UPDATE horas SET gi=?", (usado,))
            self.assertEqual(presupuestos.evaluar(conf, HOY)["estados"][0]["nivel"], nivel, usado)

    def test_con_la_sombra_no_evalua(self):
        r = presupuestos.evaluar({"presupuestos": [fila()]}, HOY)
        self.assertEqual((r["disponible"], r["estados"]), (False, []))
        self.assertIn("activo", r["motivo"])

    def test_fila_mala_en_config_a_mano_no_rompe_el_resto(self):
        self.completa()
        r = presupuestos.evaluar({**ACTIVO, "presupuestos": [fila(nombre="ok"), {"basura": 1}]}, HOY)
        self.assertEqual([x["nombre"] for x in r["estados"]], ["ok"])


def estado(clave="proyecto|A|mes", nivel=80, periodo="2026-10", ambito="proyecto", pp="mes"):
    return {"clave": clave, "nivel": nivel, "periodo_clave": periodo, "ambito": ambito, "periodo": pp, "nombre": "A"}


class TestAvisos(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-avisos-")
        self._datos = rutas.DATOS
        rutas.DATOS = self.dir
        self.addCleanup(lambda: setattr(rutas, "DATOS", self._datos))

    def test_un_umbral_se_avisa_una_vez_por_periodo(self):
        self.assertEqual(len(avisos.tomar_pendientes([estado(nivel=80)])), 1)
        self.assertEqual(avisos.tomar_pendientes([estado(nivel=80)]), [])                    # el siguiente ciclo, nada
        self.assertEqual(len(avisos.tomar_pendientes([estado(nivel=100)])), 1)               # de 80 a 100 sí
        self.assertEqual(avisos.tomar_pendientes([estado(nivel=100)]), [])
        self.assertEqual(avisos.tomar_pendientes([estado(nivel=80)]), [])                    # subir el presupuesto no repite el 80
        self.assertEqual(len(avisos.tomar_pendientes([estado(nivel=80, periodo="2026-11")])), 1)       # otro periodo: de nuevo

    def test_nivel_cero_no_avisa_ni_escribe(self):
        self.assertEqual(avisos.tomar_pendientes([estado(nivel=0)]), [])
        self.assertFalse(os.path.exists(avisos.ruta()))

    def test_sobrevive_a_editar_el_presupuesto_y_a_state_json(self):
        avisos.tomar_pendientes([estado(nivel=100)])
        config_y_estado.guardar_config({"limite_hora_tokens": 123})          # lo que hace /guardar
        self.assertEqual(avisos.tomar_pendientes([estado(nivel=100)]), [])

    def test_fichero_privado_y_atomico(self):
        avisos.tomar_pendientes([estado()])
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(avisos.ruta()).st_mode), 0o600)
        self.assertEqual([f for f in os.listdir(self.dir) if f.startswith(".avisos-")], [])

    def test_fichero_corrupto_o_manipulado(self):
        for contenido in ("{no es json", "[]", '{"x": 5}', '{"proyecto|A|mes": {"periodo": "2026-10", "avisado": 7}}'):
            with open(avisos.ruta(), "w") as f:
                f.write(contenido)
            self.assertEqual(len(avisos.tomar_pendientes([estado()])), 1, contenido)         # lo ilegible equivale a "nada avisado"

    def test_olvida_los_periodos_que_ya_pasaron_pero_no_una_fila_quitada_temporalmente(self):
        avisos.tomar_pendientes([estado(clave="a", periodo="2026-10"), estado(clave="b", periodo="2026-10")])
        avisos.tomar_pendientes([estado(clave="a", nivel=0, periodo="2026-10")])           # "b" desaparece del config solo un rato
        self.assertIn("b", avisos.leer())
        avisos.tomar_pendientes([estado(clave="a", nivel=0, periodo="2026-11")])           # empieza noviembre
        self.assertEqual(avisos.leer(), {})


class TestVigilar(ConHusoMadrid):
    def setUp(self):
        super().setUp()
        self._datos = rutas.DATOS
        rutas.DATOS = self.dir
        self.addCleanup(lambda: setattr(rutas, "DATOS", self._datos))
        self.mete([turno("claude:a", utc("2026-10-10T10:00"), proyecto="u/x/secreto-cliente", gi=1000, go=0, cr=0, cw=0)])
        self.completa()
        self.conf = {**ACTIVO, "presupuestos": [fila(nombre="u/x/secreto-cliente", tokens=1000)]}

    def test_avisa_una_vez_y_sin_nombres(self):
        with mock.patch.object(plataforma, "avisar", return_value=True) as av:
            self.assertEqual(len(presupuestos.vigilar(self.conf, HOY)), 1)
            self.assertEqual(presupuestos.vigilar(self.conf, HOY), [])
        titulo, texto = av.call_args[0]
        self.assertNotIn("secreto", texto + titulo)
        self.assertIn("100 %", texto)
        self.assertEqual(av.call_count, 1)

    def test_se_puede_apagar_y_con_sombra_no_avisa(self):
        with mock.patch.object(plataforma, "avisar") as av:
            self.assertEqual(presupuestos.vigilar({**self.conf, "avisos_presupuesto": False}, HOY), [])
            self.assertEqual(presupuestos.vigilar({"presupuestos": self.conf["presupuestos"]}, HOY), [])
        av.assert_not_called()


class TestPlataformaYAjustes(unittest.TestCase):
    def test_avisar_escapa_y_devuelve_false_si_falla(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(subprocess, "run") as run:
            self.assertTrue(plataforma.avisar('Cost"Guard', 'a "b" \\ c'))
        script = run.call_args[0][0][2]
        self.assertIn('with title "Cost\\"Guard"', script)
        self.assertIn('display notification "a \\"b\\" \\\\ c"', script)
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertFalse(plataforma.avisar("a", "b"))
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(subprocess, "run") as run:
            self.assertFalse(plataforma.avisar("a", "b"))
        run.assert_not_called()

    def base(self, **extra):
        return {"proveedores": [], "aliases": {}, **extra}

    def test_guardar_valida_los_presupuestos(self):
        out = ajustes.normalizar(self.base(presupuestos=[fila()]), {})
        self.assertEqual(out["presupuestos"][0]["metrica"], "total")
        with self.assertRaises(ValueError):
            ajustes.normalizar(self.base(presupuestos=[fila(tokens=-1)]), {})
        with self.assertRaises(ValueError):
            ajustes.normalizar(self.base(avisos_presupuesto="si"), {})
        self.assertIs(ajustes.normalizar(self.base(avisos_presupuesto=False), {})["avisos_presupuesto"], False)

    def test_el_campo_antiguo_de_proyecto_se_conserva_si_la_interfaz_ya_no_lo_manda(self):
        actual = {"proyectos": {"u/x/a": {"alias": "A", "presupuesto_mes_tokens": 500}}}
        out = ajustes.normalizar(self.base(proyectos={"u/x/a": {"alias": "A2", "oculto": False}}), actual)
        self.assertEqual(out["proyectos"]["u/x/a"], {"alias": "A2", "oculto": False, "presupuesto_mes_tokens": 500})


if __name__ == "__main__":
    unittest.main()
