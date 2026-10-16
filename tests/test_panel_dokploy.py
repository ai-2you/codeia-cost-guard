"""Cliente de la API de Dokploy: solo GET a rutas fijas, clave que no se filtra, respuestas hostiles y lista blanca de campos."""
import ast
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import panel_dokploy as pd  # noqa: E402

CLAVE = "clave-de-prueba-ABC123"
URL = "https://panel.ejemplo.com"
AHORA = 1_790_000_000.0
FECHA_PANEL = "Mon, 21 Sep 2026 14:13:20 GMT"          # = AHORA (1_790_000_000)
ISO = lambda s: __import__("datetime").datetime.fromtimestamp(AHORA - s, __import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: E731


def servicio(i="s1", **kw):
    return {"id": i, "name": "Mi tienda", "appName": "tienda-abc123", "status": "done", "createdAt": ISO(9999), "serverId": None, "serverName": None, "projectId": "p1",
            "projectName": "Tienda", "environmentId": "e1", "environmentName": "production", "type": "application", "lastDeployAt": ISO(3600), "icon": "data:image/png;base64," + "A" * 5000, **kw}


def dominio(**kw):
    return {"domainId": "d1", "host": "Tienda.Ejemplo.com", "path": "/", "port": 3000, "https": True, "certificateType": "letsencrypt", "enabled": True, "serviceOwnerId": "s1",
            "serviceOwnerType": "application", "serviceName": "tienda", **kw}


def copia(i=0, **kw):
    return {"deploymentId": f"x{i}", "kind": "backup", "status": "done", "createdAt": ISO(7200 + i), "destinationId": "dest1", "destinationName": "S3", "databaseType": "postgres",
            "serviceType": "postgres", "backupType": "database", "serviceName": "bd-tienda", "serviceOwnerId": "bd1", "serviceOwnerType": "postgres", **kw}


class Resp:
    def __init__(self, estado=200, cuerpo=b"{}", tipo="application/json", extra=None):
        self.status, self._cuerpo, self._pos = estado, cuerpo, 0
        self.cab = {"Content-Type": tipo, "Date": FECHA_PANEL, **(extra or {})}

    def getheader(self, k, d=None):
        return self.cab.get(k, d)

    def read(self, n):
        t = self._cuerpo[self._pos:self._pos + n]
        self._pos += len(t)
        return t


class Falso:
    """Conexión de mentira: apunta cada petición y contesta según la ruta."""

    def __init__(self, rutas):
        self.rutas, self.llamadas, self.ultima = rutas, [], None
        self.cerrada = False

    def request(self, metodo, ruta, headers=None, body=None):
        self.llamadas.append((metodo, ruta, dict(headers or {}), body))
        self.ultima = ruta

    def getresponse(self):
        r = self.rutas[self.ultima]
        return r() if callable(r) else r

    def close(self):
        self.cerrada = True


def json_resp(v, **kw):
    return Resp(cuerpo=json.dumps(v).encode(), **kw)


def sano(**sobre):
    r = {pd.RUTAS[0]: json_resp({"status": "ok"}), pd.RUTAS[1]: json_resp("v0.30.8"), pd.RUTAS[2]: json_resp([servicio()]), pd.RUTAS[3]: json_resp([dominio()]),
         pd.RUTAS[4]: json_resp([copia(0), copia(1)])}
    r.update(sobre)
    return r


class Base(unittest.TestCase):
    def lee(self, rutas=None, clave=CLAVE, url=URL, reloj=None):
        self.falso = Falso(rutas if rutas is not None else sano())
        self.conectadas = []

        def conectar(host, ip, puerto, plazo):
            self.conectadas.append((host, ip, puerto, plazo))
            return self.falso
        with mock.patch.object(pd, "ip_publica", return_value="203.0.113.5"):
            return pd.leer(url, clave, AHORA, conectar=conectar, **({"reloj": reloj} if reloj else {}))


class GarantiaSoloLecturaTest(Base):
    def test_cinco_get_a_las_rutas_fijas_y_la_clave_solo_a_las_que_la_necesitan(self):
        r = self.lee()
        self.assertTrue(r["ok"])
        self.assertEqual([(m, ruta) for m, ruta, _, _ in self.falso.llamadas], [("GET", x) for x in pd.RUTAS])
        self.assertTrue(all(c[3] is None for c in self.falso.llamadas))                       # nunca cuerpo
        self.assertNotIn("x-api-key", self.falso.llamadas[0][2])                                # la ruta pública no recibe la clave
        self.assertTrue(all(c[2]["x-api-key"] == CLAVE for c in self.falso.llamadas[1:]))
        self.assertTrue(self.falso.cerrada)
        self.assertEqual(self.conectadas, [("panel.ejemplo.com", "203.0.113.5", 443, pd.PLAZO_PETICION_S)])

    def test_las_rutas_aprobadas_no_cambian_sin_tocar_este_test(self):
        self.assertEqual(pd.RUTAS, ("/api/settings.health", "/api/settings.getDokployVersion", "/api/overview.services", "/api/overview.domains", "/api/overview.backups"))

    def test_el_codigo_solo_contiene_una_llamada_http_y_es_un_get(self):
        ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "infobar", "panel_dokploy.py")
        with open(ruta, encoding="utf-8") as f:
            fuente = f.read()
        arbol = ast.parse(fuente)
        peticiones = [n for n in ast.walk(arbol) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "request"]
        self.assertEqual(len(peticiones), 1)
        self.assertEqual(ast.literal_eval(peticiones[0].args[0]), "GET")
        self.assertEqual(len(peticiones[0].args), 2)
        importados = {a.name.split(".")[0] for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names} | {n.module.split(".")[0] for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
        self.assertFalse(importados & {"requests", "subprocess", "os", "urllib3", "httpx", "aiohttp"})
        self.assertNotIn("urllib.request", fuente.replace("urllib.parse", ""))
        cadenas = [n.value for n in ast.walk(arbol) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for metodo in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"):
            self.assertNotIn(metodo, cadenas)

    def test_una_ruta_fuera_de_la_lista_no_sale(self):
        with self.assertRaises(pd.ErrorPanel):
            pd._get(Falso({}), "/api/project.all", CLAVE, 100, 10 ** 12)

    def test_sin_clave_no_se_hace_ninguna_peticion_ni_conexion(self):
        r = self.lee(clave="")
        self.assertEqual(r, {"ok": False, "error": "sin_clave"})
        self.assertEqual(self.conectadas, [])

    def test_clave_con_caracteres_raros_no_se_usa_ni_sale_en_ningun_resultado(self):
        for rara in ("abc\ndef", "con espacio", "tab\tx", "x" * 2000, "ñandú"):
            r = self.lee(clave=rara)
            self.assertEqual(r, {"ok": False, "error": "clave_invalida"}, rara)
            self.assertEqual(self.conectadas, [])
            self.assertNotIn("ñandú" if rara == "ñandú" else "abc", json.dumps(r))

    def test_la_clave_no_aparece_en_ninguna_salida_de_error(self):
        casos = [sano(**{pd.RUTAS[1]: Resp(401)}), sano(**{pd.RUTAS[2]: Resp(500)}), sano(**{pd.RUTAS[2]: Resp(302)}),
                 sano(**{pd.RUTAS[2]: Resp(cuerpo=b"<html>" + CLAVE.encode() + b"</html>", tipo="text/html")}), sano(**{pd.RUTAS[2]: json_resp({"error": CLAVE})})]
        for rutas in casos:
            self.assertNotIn(CLAVE, json.dumps(self.lee(rutas)))

    def test_un_excepcion_de_red_con_la_clave_dentro_no_la_filtra(self):
        class Rota(Falso):
            def request(self, *a, **k):
                raise ValueError("Invalid header value b'" + CLAVE + "'")
        self.falso = Rota({})
        with mock.patch.object(pd, "ip_publica", return_value="203.0.113.5"):
            r = pd.leer(URL, CLAVE, AHORA, conectar=lambda *a: self.falso)
        self.assertEqual(r, {"ok": False, "error": "red"})
        self.assertNotIn(CLAVE, json.dumps(r))


class UrlTest(Base):
    def test_solo_https_con_nombre_publico_y_sin_credenciales(self):
        for mala in ("http://panel.ejemplo.com", "https://u:p@panel.ejemplo.com", "https://127.0.0.1", "https://10.0.0.5:3000", "ftp://x.ejemplo.com", "", None, 5):
            self.assertEqual(self.lee(url=mala), {"ok": False, "error": "url_invalida"}, mala)
        self.assertEqual(self.conectadas, [])

    def test_se_ignora_la_ruta_de_la_url_y_se_respeta_el_puerto(self):
        self.lee(url="https://panel.ejemplo.com:8443/lo/que/sea?x=1")
        self.assertEqual(self.conectadas[0][2], 8443)
        self.assertTrue(all(c[1] in pd.RUTAS for c in self.falso.llamadas))

    def test_una_ip_no_publica_no_conecta(self):
        with mock.patch.object(pd, "ip_publica", side_effect=OSError("resuelve a una IP local o privada")):
            r = pd.leer(URL, CLAVE, AHORA, conectar=lambda *a: self.fail("no debe conectar"))
        self.assertEqual(r, {"ok": False, "error": "red"})


class RespuestasHostilesTest(Base):
    def malo(self, ruta, resp):
        return self.lee(sano(**{ruta: resp}))

    def test_codigos_http(self):
        self.assertEqual(self.malo(pd.RUTAS[2], Resp(401))["error"], "clave_rechazada")
        self.assertEqual(self.malo(pd.RUTAS[2], Resp(403))["error"], "http_otro")
        self.assertEqual(self.malo(pd.RUTAS[2], Resp(502))["error"], "http_5xx")
        self.assertEqual(self.malo(pd.RUTAS[2], Resp(404))["error"], "http_otro")

    def test_una_redireccion_no_se_repite_ni_se_sigue(self):
        self.assertEqual(self.malo(pd.RUTAS[2], Resp(307, extra={"Location": "https://otro.ejemplo.com/"}))["error"], "redireccion")
        self.assertEqual(len(self.falso.llamadas), 3)                                           # la petición redirigida no se repite

    def test_tipo_de_contenido_y_codificacion(self):
        self.assertEqual(self.malo(pd.RUTAS[2], Resp(cuerpo=b"<html>login</html>", tipo="text/html"))["error"], "no_json")
        self.assertEqual(self.malo(pd.RUTAS[2], Resp(cuerpo=b"[]", extra={"Content-Encoding": "gzip"}))["error"], "no_json")

    def test_json_hostil(self):
        for cuerpo in (b"[NaN]", b"[Infinity]", b"[1e999]", b"no es json", b"\xff\xfe", b"[" * 200000, b""):
            self.assertEqual(self.malo(pd.RUTAS[2], Resp(cuerpo=cuerpo))["error"], "no_json", cuerpo[:20])

    def test_la_raiz_tiene_que_ser_una_lista(self):
        for cuerpo in ({}, "x", 5, None):
            self.assertEqual(self.malo(pd.RUTAS[3], json_resp(cuerpo))["error"], "contrato")

    def test_salud_y_version(self):
        self.assertEqual(self.malo(pd.RUTAS[0], json_resp({"status": "mal"}))["error"], "contrato")
        self.assertEqual(self.malo(pd.RUTAS[1], json_resp("0.29.14"))["error"], "version_antigua")
        self.assertEqual(self.malo(pd.RUTAS[1], json_resp("quién sabe"))["error"], "contrato")
        self.assertEqual(self.malo(pd.RUTAS[1], json_resp({"v": 1}))["error"], "contrato")
        self.assertTrue(self.malo(pd.RUTAS[1], json_resp("v0.31.2"))["version_no_probada"])
        self.assertFalse(self.lee()["version_no_probada"])

    def test_respuesta_demasiado_grande(self):
        grande = Resp(cuerpo=b"[" + b"1," * (pd.MAX_BYTES // 2 + 10) + b"1]")
        self.assertEqual(self.malo(pd.RUTAS[3], grande)["error"], "demasiado_grande")
        servicios_grandes = [servicio(f"s{i}") for i in range(900)]              # ~ 4,5 MB con icono: cabe en el tope de servicios
        self.assertTrue(self.malo(pd.RUTAS[2], json_resp(servicios_grandes))["ok"])

    def test_un_panel_que_gotea_se_corta_por_plazo_total(self):
        reloj = iter([0.0, 0.0, 1.0, 2.0, 40.0] + [99.0] * 50)
        lento = Resp(cuerpo=b"[" + b" " * 100000)
        self.assertEqual(self.lee(sano(**{pd.RUTAS[2]: lento}), reloj=lambda: next(reloj))["error"], "plazo")

    def test_mas_del_20_por_ciento_de_filas_invalidas_rompe_el_contrato(self):
        filas = [servicio(f"s{i}") for i in range(7)] + [{"id": 5}, {"id": "a b"}, "no es dict"]
        self.assertEqual(self.malo(pd.RUTAS[2], json_resp(filas))["error"], "contrato")
        r = self.malo(pd.RUTAS[2], json_resp([servicio(f"s{i}") for i in range(9)] + [{"id": 5}]))
        self.assertTrue(r["ok"])
        self.assertEqual((len(r["servicios"]), r["descartes"]["panel_campo"]), (9, 1))

    def test_listas_vacias_son_validas_pero_se_distinguen(self):
        r = self.malo(pd.RUTAS[2], json_resp([]))
        self.assertTrue(r["ok"])
        self.assertEqual(r["servicios"], [])

    def test_topes_de_elementos(self):
        r = self.malo(pd.RUTAS[4], json_resp([copia(i, deploymentId=f"y{i}") for i in range(pd.MAX_COPIAS + 30)]))
        self.assertEqual((len(r["copias"]), r["copias_truncadas"], r["descartes"]["panel_truncado"]), (pd.MAX_COPIAS, True, 30))


class ListaBlancaTest(Base):
    def test_solo_pasan_los_campos_permitidos(self):
        r = self.lee()
        crudo = json.dumps(r)
        for prohibido in ("icon", "data:image", "environmentId", "certificateType", "deploymentId", "destinationName", "password", "env"):
            self.assertNotIn(prohibido, crudo, prohibido)
        s = r["servicios"][0]
        self.assertEqual(set(s), {"id", "tipo", "estado", "nombre", "slug", "despliegue", "servidor_panel", "proyecto_id", "proyecto", "entorno"})
        self.assertEqual((s["nombre"], s["slug"], s["estado"], s["tipo"]), ("Mi tienda", "tienda-abc123", "done", "application"))
        self.assertAlmostEqual(s["despliegue"], AHORA - 3600, delta=2)
        self.assertEqual(r["dominios"][0]["host"], "tienda.ejemplo.com")

    def test_estados_y_tipos_fuera_del_conjunto_cerrado(self):
        relleno = [servicio(f"r{i}") for i in range(10)]
        r = self.lee(sano(**{pd.RUTAS[2]: json_resp([servicio("a", status="queued"), servicio("b", type="otraCosa")] + relleno),
                             pd.RUTAS[4]: json_resp([copia(0, status="paused"), copia(1, kind="otra")] + [copia(i + 2) for i in range(10)])}))
        self.assertEqual([s["id"] for s in r["servicios"]][:1], ["a"])
        self.assertNotIn("b", [s["id"] for s in r["servicios"]])
        self.assertIsNone(r["servicios"][0]["estado"])                                          # «no sé»: ni bueno ni error
        self.assertIsNone(r["copias"][0]["estado"])
        self.assertEqual(len(r["copias"]), 11)
        self.assertGreaterEqual(r["descartes"]["panel_estado"], 2)

    def test_nombres_hostiles_se_descartan_sin_perder_el_recurso(self):
        for malo in ("a\nb", "x‮y", "tab\tx", "n" * 200, "\x00"):
            r = self.lee(sano(**{pd.RUTAS[2]: json_resp([servicio("a", name=malo)] + [servicio(f"s{i}") for i in range(9)])}))
            a = next(s for s in r["servicios"] if s["id"] == "a")
            self.assertIsNone(a["nombre"], repr(malo))
            self.assertEqual(a["slug"], "tienda-abc123")
        self.assertGreater(r["descartes"]["panel_nombre"], 0)

    def test_identidades_con_caracteres_raros_se_descartan(self):
        r = self.lee(sano(**{pd.RUTAS[2]: json_resp([servicio("con espacio")] + [servicio(f"s{i}") for i in range(9)])}))
        self.assertEqual(len(r["servicios"]), 9)

    def test_dominios_solo_nombres_dns_y_puertos_validos(self):
        malos = [dominio(host="localhost"), dominio(host="a b.com"), dominio(host="x.com/../"), dominio(host=5), dominio(serviceOwnerId=None)]
        r = self.lee(sano(**{pd.RUTAS[3]: json_resp(malos + [dominio(host=f"ok{i}.ejemplo.com") for i in range(30)])}))
        self.assertEqual(len(r["dominios"]), 30)
        r = self.lee(sano(**{pd.RUTAS[3]: json_resp([dominio(port=70000), dominio(port=True), dominio(path="/\x01x"), dominio(path="é")])}))
        self.assertEqual([(d["puerto"], d["ruta"]) for d in r["dominios"]], [(None, "/"), (None, "/"), (3000, None), (3000, None)])

    def test_se_guardan_como_mucho_200_dominios(self):
        r = self.lee(sano(**{pd.RUTAS[3]: json_resp([dominio(host=f"h{i}.ejemplo.com") for i in range(500)])}))
        self.assertEqual(len(r["dominios"]), pd.MAX_DOMINIOS_GUARDADOS)

    def test_copia_sin_identidad_se_descarta_y_no_se_junta_con_otras(self):
        r = self.lee(sano(**{pd.RUTAS[4]: json_resp([copia(0, destinationId=None), copia(1, serviceOwnerId=None), copia(2)] + [copia(i + 3) for i in range(8)])}))
        self.assertEqual(len(r["copias"]), 9)
        self.assertEqual(r["descartes"]["panel_copia_sin_identidad"], 2)

    def test_motivos_de_descarte_son_de_la_lista_cerrada(self):
        r = self.lee(sano(**{pd.RUTAS[2]: json_resp([servicio("a", status="x", name="a\nb", lastDeployAt="2999-01-01T00:00:00Z")] + [servicio(f"s{i}") for i in range(9)])}))
        self.assertTrue(set(r["descartes"]) <= pd.DESCARTES)
        self.assertGreater(r["descartes"]["panel_reloj"], 0)


class RelojTest(Base):
    def test_se_corrige_con_la_cabecera_date_del_panel(self):
        atrasado = "Mon, 21 Sep 2026 10:53:20 GMT"                                   # el panel va 2 h por detrás
        r = self.lee(sano(**{pd.RUTAS[2]: json_resp([servicio("a", lastDeployAt="2026-09-21T10:23:20Z")], extra={"Date": atrasado})}))
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["servicios"][0]["despliegue"], AHORA - 1800, delta=2)             # 30 min antes de «ahora» del panel

    def test_una_hora_futura_se_descarta_y_se_cuenta(self):
        r = self.lee(sano(**{pd.RUTAS[2]: json_resp([servicio(f"s{i}") for i in range(9)] + [servicio("f", lastDeployAt=ISO(-86400))])}))
        self.assertIsNone(next(s for s in r["servicios"] if s["id"] == "f")["despliegue"])
        self.assertEqual(r["descartes"]["panel_reloj"], 1)

    def test_formatos_de_hora(self):
        for h, ok in (("2026-09-21T11:00:00Z", True), ("2026-09-21T11:00:00+02:00", True), ("2026-09-21T11:00:00", True), ("2026-09-21 11:00:00", True),
                      ("ayer", False), ("", False), (5, False), ("2026-13-45T00:00:00Z", False), ("1970-01-01T00:00:00Z", False), ("x" * 100, False)):
            self.assertEqual(pd._epoch(h, 0.0, AHORA) is not None, ok, h)


if __name__ == "__main__":
    unittest.main()
