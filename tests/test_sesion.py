"""Sesion obligatoria en el servidor local: sin cookie no responde nada (lectura incluida), el ticket
de entrada es de un solo uso y caduca, y la cookie lleva HttpOnly y SameSite=Strict."""
import http.client
import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import servidor_local  # noqa: E402


class SesionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = servidor_local.crear_servidor(0)
        cls.puerto = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _pide(self, metodo, ruta, cuerpo=None, cookie=None, extra=None):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        h = {"Host": f"127.0.0.1:{self.puerto}", "Content-Type": "application/json",
             "Origin": f"http://127.0.0.1:{self.puerto}"}
        if cookie:
            h["Cookie"] = cookie
        h.update(extra or {})
        c.request(metodo, ruta, body=json.dumps(cuerpo).encode() if cuerpo is not None else None, headers=h)
        r = c.getresponse()
        return r.status, r.read(), r

    def test_sin_cookie_nada_responde_ni_la_lectura(self):
        for metodo, ruta in (("GET", "/"), ("GET", "/ajustes"), ("GET", "/origenes"), ("GET", "/config.json"),
                             ("POST", "/guardar"), ("POST", "/clave"), ("GET", "/inventada")):
            estado, cuerpo, _ = self._pide(metodo, ruta, {} if metodo == "POST" else None)
            self.assertEqual(estado, 401, f"{metodo} {ruta}")
            self.assertNotIn(b"proveedores", cuerpo)

    def test_cookie_inventada_o_vacia_rechazada(self):
        for ck in (f"cg_sesion_{self.puerto}=inventada", f"cg_sesion_{self.puerto}=", "otra=1", "basura"):
            self.assertEqual(self._pide("GET", "/ajustes", cookie=ck)[0], 401, ck)

    def test_ticket_de_un_solo_uso(self):
        t = servidor_local.nuevo_ticket(self.srv)
        estado, _, r = self._pide("GET", f"/entrar?t={t}")
        self.assertEqual(estado, 302)
        self.assertEqual(self._pide("GET", f"/entrar?t={t}")[0], 401)       # ya gastado

    def test_ticket_caducado_o_falso(self):
        t = servidor_local.nuevo_ticket(self.srv)
        self.srv.tickets[t] = time.time() - 1
        self.assertEqual(self._pide("GET", f"/entrar?t={t}")[0], 401)
        self.assertEqual(self._pide("GET", "/entrar?t=falso")[0], 401)
        self.assertEqual(self._pide("GET", "/entrar")[0], 401)

    def test_cookie_valida_da_acceso_y_lleva_las_banderas(self):
        t = servidor_local.nuevo_ticket(self.srv)
        _, _, r = self._pide("GET", f"/entrar?t={t}")
        sc = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", sc)
        self.assertIn("SameSite=Strict", sc)
        self.assertEqual(r.getheader("Location"), "/")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")
        estado, cuerpo, _ = self._pide("GET", "/origenes", cookie=sc.split(";")[0])
        self.assertEqual(estado, 200)

    def test_host_ajeno_sigue_rechazado_con_cookie_valida(self):
        ck = aislamiento.cookie_de(self.srv)
        self.assertEqual(self._pide("GET", "/origenes", cookie=ck, extra={"Host": "evil.com"})[0], 403)

    def test_tope_de_sesiones(self):
        for _ in range(servidor_local.MAX_SESIONES + 5):
            aislamiento.cookie_de(self.srv)
        self.assertLessEqual(len(self.srv.sesiones), servidor_local.MAX_SESIONES)

    def test_sesion_caducada(self):
        ck = aislamiento.cookie_de(self.srv)
        for sid in list(self.srv.sesiones):
            self.srv.sesiones[sid] = time.time() - 1
        self.assertEqual(self._pide("GET", "/origenes", cookie=ck)[0], 401)

    def test_cookie_ajena_mal_formada_no_bloquea_la_sesion(self):
        ck = aislamiento.cookie_de(self.srv)
        for ajena in ('otra={"a": 1, "b": [2', "x=; y", "z=\"sin cerrar", ""):
            cabecera = (ajena + "; " if ajena else "") + ck
            self.assertEqual(self._pide("GET", "/origenes", cookie=cabecera)[0], 200, ajena)

    def test_cookie_no_ascii_o_de_otro_puerto_se_rechaza_sin_error(self):
        nombre, _, valor = aislamiento.cookie_de(self.srv).partition("=")
        self.assertEqual(self._pide("GET", "/origenes", cookie=f"{nombre}=ñandú")[0], 401)
        self.assertEqual(self._pide("GET", "/origenes", cookie=f"cg_sesion_1={valor}")[0], 401)   # otro puerto
        self.assertEqual(self._pide("GET", "/origenes", cookie=f"cg_sesion={valor}")[0], 401)     # sin puerto

    def test_el_nombre_de_la_cookie_lleva_el_puerto(self):
        nombre = aislamiento.cookie_de(self.srv).partition("=")[0]
        self.assertEqual(nombre, f"cg_sesion_{self.puerto}")

    def test_url_panel_lleva_un_ticket_valido(self):
        servidor_local._SERVIDOR[:] = [self.srv]
        try:
            url = servidor_local.url_panel()
        finally:
            servidor_local._SERVIDOR[:] = []
        self.assertIn("/entrar?t=", url)
        ticket = url.split("t=", 1)[1]
        self.assertIn(ticket, self.srv.tickets)


if __name__ == "__main__":
    unittest.main()
