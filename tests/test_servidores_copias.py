"""Última copia buena por base de datos y volumen: cadencia aprendida, rotación de ejecuciones, identidades, olvido y retención."""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import servidores_copias as sc, servidores_historico as sh  # noqa: E402
from test_servidores_historico import Base, NOMBRE, SID, T0  # noqa: E402

DIA = 86400


def f(ts, estado="done", dueno="bd1", destino="d1", tipo="backup", nombre="bd-tienda", bd="postgres", variante="database"):
    return {"dueno": dueno, "tipo": tipo, "destino": destino, "bd": bd, "variante": variante, "estado": estado, "ts": ts, "nombre": nombre, "tipo_servicio": "postgres"}


class Copias(Base):
    def setUp(self):
        super().setUp()
        sh._alta_servidor(self.con, SID, NOMBRE, T0)

    def anota(self, filas, ahora):
        self.con.execute("BEGIN IMMEDIATE")
        try:
            return sc.anota(self.con, SID, filas, ahora)
        finally:
            self.con.execute("COMMIT")

    def filas(self, ahora):
        return sc.lista(self.con, SID, ahora)


class EvaluacionTest(Copias):
    def test_copia_diaria_al_dia_y_con_hueco_de_tres_dias_antigua(self):
        self.anota([f(T0 - i * DIA) for i in range(6)], T0)
        self.assertEqual(self.filas(T0)[0]["evaluacion"], "al_dia")
        self.assertEqual(self.filas(T0 + 2 * DIA)[0]["evaluacion"], "al_dia")                      # 3 días desde la última, pero el umbral es max(48 h, 2,5 × 1 día)
        self.assertEqual(self.filas(T0 + 3 * DIA)[0]["evaluacion"], "antigua")

    def test_copia_semanal_no_avisa_a_los_cinco_dias(self):
        self.anota([f(T0 - i * 7 * DIA) for i in range(4)], T0)
        self.assertEqual(self.filas(T0 + 5 * DIA)[0]["evaluacion"], "al_dia")
        self.assertEqual(self.filas(T0 + 16 * DIA)[0]["evaluacion"], "al_dia")                      # 2,5 × 7 días = 17,5
        self.assertEqual(self.filas(T0 + 18 * DIA)[0]["evaluacion"], "antigua")

    def test_con_una_sola_copia_buena_se_espera_ocho_dias(self):
        self.anota([f(T0)], T0)
        self.assertEqual(self.filas(T0 + 7 * DIA)[0]["evaluacion"], "al_dia")
        self.assertEqual(self.filas(T0 + 9 * DIA)[0]["evaluacion"], "antigua")

    def test_sin_ninguna_buena_se_muestra_aparte_y_no_es_antigua(self):
        self.anota([f(T0, "error"), f(T0 - 100, "cancelled"), f(T0 - 200, "running")], T0)
        c = self.filas(T0)[0]
        self.assertEqual((c["evaluacion"], c["ultima_ok"]), ("sin_buena", None))
        self.assertEqual(self.filas(T0 + 365 * DIA)[0]["evaluacion"], "sin_buena")

    def test_solo_done_cuenta_como_buena(self):
        self.anota([f(T0 - 5 * DIA), f(T0 - 4 * DIA, "running"), f(T0 - 3 * DIA, "cancelled"), f(T0 - 2 * DIA, "error"), f(T0 - DIA, None)], T0)
        c = self.filas(T0)[0]
        self.assertEqual(c["ultima_ok"], T0 - 5 * DIA)
        self.assertEqual(c["ultimo_estado"], "error")                                                # el último estado conocido sale del último estado válido

    def test_la_ultima_buena_sobrevive_a_la_rotacion_de_ejecuciones(self):
        self.anota([f(T0 - i * DIA) for i in range(10)], T0)
        self.anota([f(T0 + 20 * DIA - i * 3600, "error") for i in range(10)], T0 + 20 * DIA)          # solo quedan ejecuciones fallidas en el panel
        c = self.filas(T0 + 20 * DIA)[0]
        self.assertEqual(c["ultima_ok"], T0)
        self.assertEqual(c["evaluacion"], "antigua")

    def test_la_cadencia_se_aprende_y_se_conserva(self):
        self.anota([f(T0 - i * 7 * DIA) for i in range(3)], T0)
        self.anota([f(T0 + 7 * DIA)], T0 + 7 * DIA)                                                   # el panel ya solo enseña la última
        c = self.filas(T0 + 7 * DIA)[0]
        self.assertEqual(c["intervalo_s"], 7 * DIA)
        self.assertEqual((c["ultima_ok"], c["previa_ok"]), (T0 + 7 * DIA, T0))


class IdentidadTest(Copias):
    def test_dos_destinos_de_la_misma_base_son_copias_distintas(self):
        self.anota([f(T0 - DIA, destino="s3"), f(T0, "error", destino="s3"), f(T0 - 20 * DIA, destino="local"), f(T0 - 21 * DIA, destino="local")], T0)
        por_etiqueta = {c["evaluacion"] for c in self.filas(T0)}
        self.assertEqual(por_etiqueta, {"al_dia", "antigua"})                                        # la rota no la tapa la buena
        self.assertEqual(len(self.filas(T0)), 2)

    def test_tipo_de_base_y_volumen_son_distintos(self):
        self.anota([f(T0), f(T0, tipo="volumeBackup", bd="", variante="")], T0)
        self.assertEqual(sorted(c["tipo"] for c in self.filas(T0)), ["backup", "volumeBackup"])

    def test_etiqueta_valida_o_neutra(self):
        self.anota([f(T0, nombre="Mi tienda ñ")], T0)
        self.anota([f(T0, nombre="bien-1_x.y", dueno="otro")], T0)
        etiquetas = {c["etiqueta"] for c in self.filas(T0)}
        self.assertIn("bien-1_x.y", etiquetas)
        neutra = next(e for e in etiquetas if e != "bien-1_x.y")
        self.assertRegex(neutra, r"^copia-[0-9a-f]{8}$")

    def test_la_clave_es_un_hash_con_sal_y_no_lleva_el_id(self):
        self.anota([f(T0)], T0)
        clave = self.filas(T0)[0]["clave"]
        self.assertRegex(clave, r"^[0-9a-f]{32}$")
        self.assertNotIn("bd1", clave)


class ProtegidoTest(Copias):
    def test_no_se_retrocede_con_ejecuciones_mas_viejas(self):
        self.anota([f(T0)], T0)
        self.anota([f(T0 - 10 * DIA, "error")], T0 + 10)
        c = self.filas(T0 + 10)[0]
        self.assertEqual(c["ultimo_ts"], T0)
        self.assertEqual(c["ultimo_estado"], "done")

    def test_tope_de_copias_por_servidor(self):
        filas = [f(T0, dueno=f"d{i}") for i in range(sc.MAX_COPIAS_SERVIDOR + 20)]
        self.anota(filas, T0)
        self.assertEqual(len(self.filas(T0)), sc.MAX_COPIAS_SERVIDOR)
        self.assertGreaterEqual(self.descartes("copias_sobre_tope"), 20)

    def test_la_base_rechaza_datos_imposibles(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("INSERT INTO copias(servidor_id, clave, dueno, tipo, etiqueta, primera, vista) VALUES(?,?,?,?,?,?,?)", (SID, "k" * 32, "x", "otra", "ok", 1, 1))
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("INSERT INTO copias(servidor_id, clave, dueno, tipo, etiqueta, primera, vista) VALUES(?,?,?,?,?,?,?)", (SID, "k" * 32, "x", "backup", "con espacio", 1, 1))


class VidaTest(Copias):
    def test_sincronizar_quita_lo_que_ya_no_existe_en_el_panel(self):
        self.anota([f(T0, dueno="a"), f(T0, dueno="b")], T0)
        self.con.execute("BEGIN IMMEDIATE")
        self.assertEqual(sc.sincronizar(self.con, SID, {"a"}), 1)
        self.con.execute("COMMIT")
        self.assertEqual([c["dueno"] for c in self.filas(T0)], ["a"])

    def test_purga_a_los_treinta_dias_sin_verse_y_por_servidor_retirado(self):
        self.anota([f(T0, dueno="a")], T0)
        self.con.execute("BEGIN IMMEDIATE")
        sc.purgar(self.con, T0 + 10 * DIA)
        self.con.execute("COMMIT")
        self.assertEqual(len(self.filas(T0)), 1)
        self.con.execute("BEGIN IMMEDIATE")
        self.assertEqual(sc.purgar(self.con, T0 + 31 * DIA), 1)
        self.con.execute("COMMIT")
        self.anota([f(T0)], T0)
        self.con.execute("BEGIN IMMEDIATE")
        sc.purgar(self.con, T0 + 60, retirados=[SID])
        self.con.execute("COMMIT")
        self.assertEqual(self.filas(T0), [])

    def test_horas_de_un_reloj_adelantado_se_traen_al_presente(self):
        self.anota([f(T0 + 20 * DIA)], T0 + 20 * DIA)
        self.con.execute("BEGIN IMMEDIATE")
        sc.purgar(self.con, T0)
        self.con.execute("COMMIT")
        c = self.filas(T0)[0]
        self.assertLessEqual(max(c["ultima_ok"], c["vista"], c["ultimo_ts"]), T0)

    def test_olvidar_recursos_borra_las_copias_y_la_copia_previa_no_las_lleva(self):
        self.anota([f(T0)], T0)
        destino = os.path.join(self.dir, "copias")
        sh.copia_sin_ips(self.con, destino)
        for nombre in os.listdir(destino):
            c = sqlite3.connect(os.path.join(destino, nombre))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM copias").fetchone()[0], 0)
            c.close()
        sh.olvidar_recursos(self.con, SID)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM copias"), 0)

    def test_borrar_el_servidor_borra_sus_copias(self):
        self.anota([f(T0)], T0)
        sh.borrar_servidor(self.con, SID)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM copias"), 0)

    def test_mantener_purga_y_las_columnas_estan_clasificadas(self):
        self.anota([f(T0)], T0)
        sh.mantener(self.con, {SID: {"guardar_ips": True, "retencion_dias": 30}}, T0 + 40 * DIA)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM copias"), 0)
        self.assertEqual(sh.CLASES_SERVIDORES["copias"]["etiqueta"], "personal")


if __name__ == "__main__":
    unittest.main()
