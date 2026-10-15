"""Caducidad de certificados de las webs: días hasta notAfter del certificado verificado, mínimo por muestra (solo si todas las https dieron fecha),
umbrales de la tarjeta y migración de la base."""
import datetime
import os
import shutil
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import servidores_datos as sd, servidores_estado as se, servidores_externo as ex, servidores_historico as sh  # noqa: E402
from test_servidores_historico import Base, entrada  # noqa: E402

T0 = 1_790_000_000


def web(url, dias, ms=100, codigo=200, error=""):
    return {"url": url, "ms": ms, "codigo": codigo, "error": error, "cert_dias": dias}


class DiasDeCaducidadTest(unittest.TestCase):
    AHORA = ssl.cert_time_to_seconds("Jun  1 12:00:00 2026 GMT")

    def dias(self, texto):
        return ex.dias_de_caducidad({"notAfter": texto}, self.AHORA)

    def test_cuenta_dias_enteros_hacia_abajo(self):
        self.assertEqual(self.dias("Jun 21 12:00:00 2026 GMT"), 20)
        self.assertEqual(self.dias("Jun 21 11:59:59 2026 GMT"), 19)
        self.assertEqual(self.dias("Jun  1 12:00:00 2026 GMT"), 0)

    def test_caducado_da_negativo(self):
        self.assertEqual(self.dias("May 30 12:00:00 2026 GMT"), -2)
        self.assertEqual(self.dias("May 30 18:00:00 2026 GMT"), -2)             # hacia abajo: caducó hace 1,75 días, ya son -2 enteros (no -1)

    def test_dato_imposible_o_ilegible_es_none(self):
        for malo in ("ayer", "", "Jun 21 12:00:00 3026 GMT", "Jun 21 12:00:00 1926 GMT", None, 5, "x" * 500):
            self.assertIsNone(ex.dias_de_caducidad({"notAfter": malo}, self.AHORA), repr(malo))
        for malo in (None, {}, {"otra": 1}, [], "x"):
            self.assertIsNone(ex.dias_de_caducidad(malo, self.AHORA), repr(malo))


class MedirWebConCertificadoRealTest(unittest.TestCase):
    """Servidor https local con un certificado firmado por una CA de prueba: el handshake se verifica de verdad y `cert_dias` sale del certificado presentado."""

    def setUp(self):
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.x509.oid import NameOID
        except ImportError:
            self.skipTest("hace falta la librería 'cryptography'")
        self.dir = tempfile.mkdtemp(prefix="cg-cert-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        ahora = datetime.datetime.now(datetime.timezone.utc)

        def clave():
            return ec.generate_private_key(ec.SECP256R1())

        def nombre(cn):
            return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
        ca_k, srv_k = clave(), clave()
        ca = (x509.CertificateBuilder().subject_name(nombre("CA de prueba")).issuer_name(nombre("CA de prueba")).public_key(ca_k.public_key())
              .serial_number(1).not_valid_before(ahora - datetime.timedelta(days=1)).not_valid_after(ahora + datetime.timedelta(days=400))
              .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(ca_k, hashes.SHA256()))
        self.expira = ahora + datetime.timedelta(days=20, hours=1)
        srv = (x509.CertificateBuilder().subject_name(nombre("localhost")).issuer_name(ca.subject).public_key(srv_k.public_key()).serial_number(2)
               .not_valid_before(ahora - datetime.timedelta(days=1)).not_valid_after(self.expira)
               .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False).sign(ca_k, hashes.SHA256()))
        self.ca_pem = os.path.join(self.dir, "ca.pem")
        cert_pem, key_pem = os.path.join(self.dir, "srv.pem"), os.path.join(self.dir, "srv.key")
        with open(self.ca_pem, "wb") as f:
            f.write(ca.public_bytes(serialization.Encoding.PEM))
        with open(cert_pem, "wb") as f:
            f.write(srv.public_bytes(serialization.Encoding.PEM))
        with open(key_pem, "wb") as f:
            f.write(srv_k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

        class H(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass
        self.http = HTTPServer(("127.0.0.1", 0), H)
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexto.load_cert_chain(cert_pem, key_pem)
        self.http.socket = contexto.wrap_socket(self.http.socket, server_side=True)
        self.puerto = self.http.server_address[1]
        hilo = threading.Thread(target=self.http.serve_forever, daemon=True)
        hilo.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def medir(self, confiar=True):
        ca_pem = self.ca_pem

        def contexto(*a, **k):
            c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            c.check_hostname = True
            c.verify_mode = ssl.CERT_REQUIRED
            if confiar:
                c.load_verify_locations(ca_pem)
            return c
        with mock.patch.object(ex.ssl, "create_default_context", contexto), mock.patch.object(ex, "ip_publica", return_value="127.0.0.1"), \
                mock.patch.object(ex, "url_valida", return_value=""):
            return ex.medir_web(f"https://localhost:{self.puerto}/")

    def test_certificado_valido_da_los_dias_que_le_quedan(self):
        r = self.medir()
        self.assertEqual((r["error"], r["codigo"]), ("", 200))
        self.assertIn(r["cert_dias"], (20, 21))

    def test_certificado_no_confiado_no_da_fecha_y_si_error(self):
        r = self.medir(confiar=False)
        self.assertTrue(r["error"].startswith("certificado"))
        self.assertIsNone(r["cert_dias"])

    def test_http_no_tiene_certificado(self):
        with mock.patch.object(ex, "ip_publica", return_value="127.0.0.1"), mock.patch.object(ex, "url_valida", return_value=""), \
                mock.patch.object(ex.http.client.HTTPConnection, "request", side_effect=OSError("sin red")):
            r = ex.medir_web("http://localhost:9/")
        self.assertIsNone(r["cert_dias"])


class MuestraYTarjetaTest(unittest.TestCase):
    def muestra(self, webs):
        e = entrada(T0)
        e["webs"] = webs
        return sd.muestra_de(e)[0]["cert_dias_min"]

    def test_minimo_solo_si_todas_las_https_dieron_fecha(self):
        self.assertEqual(self.muestra([web("https://a", 40), web("https://b", 12)]), 12)
        self.assertEqual(self.muestra([web("https://a", 40), web("https://b", -3)]), -3)
        self.assertIsNone(self.muestra([web("https://a", 40), web("https://b", None)]))          # falta una: el mínimo escondería la que falta
        self.assertIsNone(self.muestra([web("http://a", None)]))
        self.assertEqual(self.muestra([web("http://a", None), web("https://b", 7)]), 7)           # las http no cuentan
        self.assertIsNone(self.muestra([]))
        for malo in (True, "7", 7.5):
            self.assertIsNone(self.muestra([web("https://a", malo)]), repr(malo))

    def test_dias_fuera_de_rango_descartan_la_muestra(self):
        with self.assertRaises(sd.FueraDeRango):
            self.muestra([web("https://a", 10 ** 6)])

    def tarjetas(self, dias, **kw):
        w = web("https://a", dias, **kw)
        return se.tarjetas({"cpu": {"pct": 1.0, "carga": 0.1, "nucleos": 1}}, [w], None)

    def test_umbrales_en_su_propia_tarjeta(self):
        for dias, esperado in ((60, "verde"), (14, "verde"), (13, "ambar"), (3, "ambar"), (2, "rojo"), (-1, "rojo")):
            self.assertEqual(self.tarjetas(dias)["certificado"]["estado"], esperado, dias)
        self.assertNotIn("certificado", self.tarjetas(None))
        self.assertEqual(self.tarjetas(13)["certificado"]["cert_dias"], 13)
        for raro in (True, "13", 13.0):                                                   # un booleano o un texto no son días
            self.assertNotIn("certificado", self.tarjetas(raro), repr(raro))

    def test_certificado_invalido_es_rojo_sin_fecha(self):
        t = self.tarjetas(None, error="certificado: CERTIFICATE_VERIFY_FAILED", codigo=None, ms=None)
        self.assertEqual((t["certificado"]["estado"], t["certificado"]["invalido"]), ("rojo", True))

    def test_el_certificado_no_cuenta_para_la_salud_viva_ni_acelera_la_recogida(self):
        from infobar import servidores_ciclo as ciclo
        datos = {"cpu": {"pct": 1.0, "carga": 0.1, "nucleos": 1}, "memoria": {"total": 100, "usada": 10}, "disco": {"total": 100, "usado": 10},
                 "sesiones": [], "puertos": [22], "intentos": {"total": 0, "hora": 0, "top": []}, "servicios": [], "fail2ban": {"instalado": True, "ahora": 0, "total": 0},
                 "actualizaciones": {"pendientes": 0, "reinicio": False}, "con_root": False}
        with mock.patch.object(ciclo, "leer", return_value=(datos, "", 0.1)), mock.patch.object(ciclo.webs_sondeo, "medidas", return_value=[web("https://a", 5)]), \
                mock.patch.object(ciclo, "host_de_ssh", return_value="h"), mock.patch.object(ciclo, "puertos_expuestos", return_value=[]):
            r = ciclo.recoger_uno({"nombre": "x", "ssh": "u@h", "webs": ["https://a"]}, [])
        self.assertEqual(r["tarjetas"]["certificado"]["estado"], "ambar")
        self.assertEqual(r["estado_vivo"], "verde")                                          # el certificado no es salud viva...
        self.assertIn(r["estado"], ("ambar", "rojo"))                                         # ...pero sí cuenta para el estado global
        self.assertFalse(ciclo.en_incidencia("x", r, {}, T0))                                # y no mete al servidor en modo incidencia


class MuestraDeWebsTest(unittest.TestCase):
    def test_url_en_mayusculas_cuenta_como_https(self):
        e = entrada(T0)
        e["webs"] = [web("HTTPS://a", None, error="certificado: x", codigo=None, ms=None), web("https://b", 50)]
        m = sd.muestra_de(e)[0]
        self.assertIsNone(m["cert_dias_min"])
        self.assertEqual(m["webs_cert_mal"], 1)

    def test_webs_cert_mal_cuenta_solo_errores_de_certificado(self):
        e = entrada(T0)
        e["webs"] = [web("https://a", None, error="certificado: caducado"), web("https://b", None, error="timed out"), web("https://c", 9)]
        self.assertEqual(sd.muestra_de(e)[0]["webs_cert_mal"], 1)
        e["webs"] = []
        self.assertIsNone(sd.muestra_de(e)[0]["webs_cert_mal"])


class CapturaConSocketFalsoTest(unittest.TestCase):
    """Sin dependencias: `connect()` guarda el certificado justo tras el handshake, aunque la respuesta cierre el socket."""

    def test_connect_guarda_el_certificado(self):
        falso = mock.Mock()
        falso.getpeercert.return_value = {"notAfter": "Jun  1 12:00:00 2030 GMT"}
        contexto = mock.Mock()
        contexto.wrap_socket.return_value = falso
        con = ex._HTTPSFijo.__new__(ex._HTTPSFijo)
        con.host, con.port, con.timeout, con._ip, con._context = "x.test", 443, 1, "127.0.0.1", contexto
        with mock.patch.object(ex.socket, "create_connection", return_value=mock.Mock()):
            con.connect()
        self.assertEqual(con.certificado, {"notAfter": "Jun  1 12:00:00 2030 GMT"})
        contexto.wrap_socket.assert_called_once()
        self.assertEqual(contexto.wrap_socket.call_args.kwargs["server_hostname"], "x.test")


class AlmacenCertificadosTest(Base):
    def test_se_guarda_el_minimo_en_la_muestra(self):
        e = entrada(T0)
        e["webs"] = [web("https://a", 40), web("https://b", 9)]
        self.reg(e)
        self.assertEqual(self.uno("SELECT cert_dias_min FROM muestras"), 9)

    def test_la_columna_rechaza_dias_imposibles(self):
        sh._alta_servidor(self.con, "a" * 12, "x", T0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("INSERT INTO muestras(servidor_id, ts, estado, cert_dias_min) VALUES(?, ?, 'verde', 99999)", ("a" * 12, T0))

    def test_resumen_por_hora_guarda_el_certificado_que_antes_caduca_y_el_peor_recuento(self):
        for k, (dias, mal) in enumerate(((40, 0), (9, 1), (30, 0))):
            e = entrada(T0 + 600 * k)
            e["webs"] = [web("https://a", dias if not mal else None, error="certificado: x" if mal else "")] + [web("https://b", dias + 1)]
            self.reg(e)
        self.assertEqual([tuple(r) for r in self.con.execute("SELECT cert_dias_min, webs_cert_mal_max FROM horas_servidor")], [(30, 1)])
        # el segundo ciclo dejó cert_dias_min en None (una web sin fecha): no baja ni sube el mínimo de la hora

    def test_el_historial_expone_el_certificado_por_hora_y_por_dia(self):
        import datetime
        from infobar import servidores_lectura as sl
        for k, (dias, mal) in enumerate(((40, 0), (12, 0), (3, 1))):
            e = entrada(T0 + 4000 * k)
            e["webs"] = [web("https://a", dias if not mal else None, error="certificado: x" if mal else "")]
            self.reg(e)
        dia = datetime.date.fromtimestamp(T0)
        for paso in ("hora", "dia"):
            r = sl.historia(self.con, "a" * 12, (dia - datetime.timedelta(days=1)).isoformat(), (dia + datetime.timedelta(days=1)).isoformat(), paso)
            self.assertEqual(min(p["cert_dias_min"] for p in r["puntos"] if p["cert_dias_min"] is not None), 12)
            self.assertEqual(max(p["webs_cert_mal_max"] or 0 for p in r["puntos"]), 1)

    def test_migracion_de_v3_a_v4_conserva_las_muestras(self):
        self.reg(entrada(T0))
        self.con.close()
        c = sqlite3.connect(sh.ruta(), isolation_level=None)
        for tabla in ("webs", "eventos_web", "horas_web", "dias_web"):
            c.execute(f"DROP TABLE {tabla}")
        for tabla, col in (("muestras", "cert_dias_min"), ("muestras", "webs_cert_mal"), ("horas_servidor", "cert_dias_min"), ("horas_servidor", "webs_cert_mal_max")):
            c.execute(f"ALTER TABLE {tabla} DROP COLUMN {col}")
        c.execute("PRAGMA user_version=3")
        c.close()
        self.con = sh.abrir()
        self.assertEqual(self.uno("PRAGMA user_version"), max(sh.MIGRACIONES))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 1)
        self.assertIsNone(self.uno("SELECT cert_dias_min FROM muestras"))


if __name__ == "__main__":
    unittest.main()
