"""Tests de infobar.bandeja_windows_menu: solo la logica pura de formateo del menu de la
bandeja de Windows (fase 2), con un dict de ejemplo que imita lo que devuelve
costbar.escanear(). No crea ningun icono real de pystray ni importa bandeja_windows.py
(ese fichero tiene una guarda de plataforma que corta en no-Windows).

Arrancar con:  python3 -m unittest discover tests

Se salta entera si pystray no esta instalado: esta fase depende de esa aprobacion de
dependencia (ver plan), asi que el resto de la suite (macOS/Linux/CI sin pystray) no se ve
afectado por ella.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pystray  # noqa: F401
    _TIENE_PYSTRAY = True
except ImportError:
    _TIENE_PYSTRAY = False

from infobar import bandeja_windows_menu as bwm  # noqa: E402


def _r_ejemplo(cinco_h_tok=1_200_000, pct_5h=24.0, limite_5h=5_000_000, hoy_tok=300_000,
               hoy_turnos=12, ritmo_tok=50_000, limite_hora=8_000_000):
    """Subconjunto real de costbar.escanear(): solo las claves que usa bandeja_windows_menu."""
    return {
        "cinco_h": {"tok": cinco_h_tok},
        "pct_5h": pct_5h,
        "hoy": {"tok": hoy_tok, "turnos": hoy_turnos},
        "ritmo": {"tok": ritmo_tok},
        "limites": {"limite_5h_tokens": limite_5h, "limite_hora_tokens": limite_hora},
    }


@unittest.skipUnless(_TIENE_PYSTRAY, "pystray no esta instalado (dependencia de la fase 2)")
class TextoMenuTest(unittest.TestCase):
    def test_texto_5h_con_limite_declarado(self):
        txt = bwm.texto_5h(_r_ejemplo(cinco_h_tok=1_200_000, pct_5h=24.0, limite_5h=5_000_000))
        self.assertIn("1,2 M", txt)
        self.assertIn("24 % del limite", txt)

    def test_texto_5h_sin_limite_declarado(self):
        txt = bwm.texto_5h(_r_ejemplo(limite_5h=0))
        self.assertTrue(txt.endswith("tokens"))
        self.assertNotIn("% del limite", txt)

    def test_texto_hoy(self):
        txt = bwm.texto_hoy(_r_ejemplo(hoy_tok=300_000, hoy_turnos=12))
        self.assertIn("300,0 K", txt)
        self.assertIn("12 turnos", txt)

    def test_texto_ritmo_normal(self):
        txt = bwm.texto_ritmo(_r_ejemplo(ritmo_tok=50_000, limite_hora=8_000_000))
        self.assertIn("50,0 K tokens", txt)
        self.assertNotIn("ALTO", txt)

    def test_texto_ritmo_alto(self):
        txt = bwm.texto_ritmo(_r_ejemplo(ritmo_tok=9_000_000, limite_hora=8_000_000))
        self.assertIn("ALTO", txt)

    def test_fraccion_con_limite_5h(self):
        fraccion, alerta = bwm.fraccion_e_icono(_r_ejemplo(
            cinco_h_tok=1_000_000, limite_5h=4_000_000, ritmo_tok=1_000, limite_hora=8_000_000))
        self.assertAlmostEqual(fraccion, 0.25)
        self.assertFalse(alerta)

    def test_fraccion_sin_limite_5h_usa_ritmo(self):
        fraccion, _ = bwm.fraccion_e_icono(_r_ejemplo(
            limite_5h=0, ritmo_tok=4_000_000, limite_hora=8_000_000))
        self.assertAlmostEqual(fraccion, 0.5)

    def test_fraccion_sin_datos_usa_relleno_por_defecto(self):
        fraccion, _ = bwm.fraccion_e_icono(_r_ejemplo(limite_5h=0, ritmo_tok=0, limite_hora=8_000_000))
        self.assertAlmostEqual(fraccion, 0.34)

    def test_fraccion_alerta_cuando_ritmo_supera_limite_hora(self):
        _, alerta = bwm.fraccion_e_icono(_r_ejemplo(ritmo_tok=9_000_000, limite_hora=8_000_000))
        self.assertTrue(alerta)

    def test_titulo_icono(self):
        self.assertEqual(bwm.titulo_icono(_r_ejemplo(cinco_h_tok=1_200_000)), "1,2 M")


if __name__ == "__main__":
    unittest.main()
