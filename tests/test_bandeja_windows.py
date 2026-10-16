"""Tests de infobar.bandeja_windows_menu: el formateo del menú y el icono de la bandeja de Windows a partir del resumen
«agentes» que entrega el hilo trabajador. No crea ningún icono de pystray ni importa bandeja_windows.py (lleva una guarda
de plataforma), así que corren en cualquier sistema; los del icono solo necesitan Pillow.

Arrancar con:  python3 -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import PIL  # noqa: F401
    _TIENE_PIL = True
except ImportError:
    _TIENE_PIL = False

from infobar import bandeja_windows_menu as bwm  # noqa: E402


def _r_ejemplo(cinco_h_tok=1_200_000, pct_5h=24.0, limite_5h=5_000_000, hoy_tok=300_000,
               hoy_turnos=12, ritmo_tok=50_000, limite_hora=8_000_000):
    """El resumen «agentes» tal como lo entrega infobar/trabajador_barra.py."""
    return {"cinco_h": cinco_h_tok, "pct_5h": pct_5h, "hoy": hoy_tok, "turnos": hoy_turnos,
            "ritmo": ritmo_tok, "limite_5h": limite_5h, "limite_hora": limite_hora}


class TextoMenuTest(unittest.TestCase):
    def test_texto_5h_con_limite_declarado(self):
        txt = bwm.texto_5h(_r_ejemplo(cinco_h_tok=1_200_000, pct_5h=24.0, limite_5h=5_000_000))
        self.assertIn("1,2 M", txt)
        self.assertIn("24 % del límite", txt)

    def test_texto_5h_sin_limite_declarado(self):
        txt = bwm.texto_5h(_r_ejemplo(limite_5h=0))
        self.assertTrue(txt.endswith("tokens"))
        self.assertNotIn("% del límite", txt)

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

    def test_sin_limite_por_hora_el_ritmo_nunca_es_alto(self):
        a = _r_ejemplo(ritmo_tok=9_000_000, limite_hora=0)
        self.assertNotIn("ALTO", bwm.texto_ritmo(a))
        self.assertFalse(bwm.fraccion_e_icono(a)[1])

    def test_sin_ningun_limite_el_anillo_queda_en_un_tercio(self):
        self.assertAlmostEqual(bwm.fraccion_e_icono(_r_ejemplo(limite_5h=0, limite_hora=0))[0], 0.34)

    def test_resumen_incompleto_no_rompe(self):
        a = {"cinco_h": 0, "hoy": 0, "turnos": 0}           # un trabajador anterior sin ritmo ni límites
        self.assertIn("tokens", bwm.texto_5h(a))
        self.assertIn("0 turnos", bwm.texto_hoy(a))
        self.assertNotIn("ALTO", bwm.texto_ritmo(a))

    def test_tooltip_trae_5h_y_hoy(self):
        txt = bwm.texto_tooltip(_r_ejemplo(cinco_h_tok=1_200_000, hoy_tok=300_000))
        self.assertIn("1,2 M", txt)
        self.assertIn("300,0 K", txt)


@unittest.skipUnless(_TIENE_PIL, "Pillow no está instalado")
class ImagenIconoTest(unittest.TestCase):

    def test_devuelve_imagen_cuadrada_rgba(self):
        im = bwm.imagen_icono_windows(0.5, alerta=False, tamano=64)
        self.assertEqual(im.size, (64, 64))
        self.assertEqual(im.mode, "RGBA")

    def test_fraccion_cero_no_pinta_relleno(self):
        # sin relleno, el centro del anillo (fuera del propio trazo) debe seguir siendo el fondo
        im = bwm.imagen_icono_windows(0.0, tamano=64)
        self.assertEqual(im.getpixel((32, 32))[:3], bwm.FONDO[:3])

    def test_alerta_usa_el_color_de_alerta_no_el_normal(self):
        # en fraccion=1 el trazo de relleno cubre todo el anillo; se mira el centro de la banda
        # superior. grosor calculado con la MISMA formula que imagen_icono_windows (no un numero
        # magico): si esa formula cambia, el punto de muestreo cambia con ella en vez de romper
        # el test por una coordenada que ya no corresponde al trazo.
        tamano = 64
        grosor = max(3, tamano // 8)
        punto = (tamano // 2, grosor)
        normal = bwm.imagen_icono_windows(1.0, alerta=False, tamano=tamano)
        alerta = bwm.imagen_icono_windows(1.0, alerta=True, tamano=tamano)
        self.assertEqual(normal.getpixel(punto)[:3], bwm.RELLENO_OK[:3])
        self.assertEqual(alerta.getpixel(punto)[:3], bwm.RELLENO_ALERTA[:3])



class PopoverSinServidorTest(unittest.TestCase):
    """pywebview levanta su servidor HTTP (sin sesión, sirviendo el directorio de trabajo) con cualquier URL que no empiece por
    http://, https:// o file://. El popover solo puede cargar el panel local o contenido HTML."""

    def test_el_popover_no_pasa_urls_que_levanten_el_servidor_de_pywebview(self):
        import ast
        ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "infobar", "bandeja_windows_popover.py")
        arbol = ast.parse(open(ruta, encoding="utf-8").read())
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call):
                continue
            nombre = getattr(nodo.func, "attr", getattr(nodo.func, "id", ""))
            if nombre == "create_window":
                claves = {k.arg for k in nodo.keywords}
                self.assertIn("html", claves)
                self.assertNotIn("url", claves)
            if nombre == "load_url":
                self.assertTrue(all(isinstance(a, ast.Name) and a.id == "url" for a in nodo.args), "load_url solo con la URL comprobada")
        self.assertIn('url.startswith("http://127.0.0.1:")', open(ruta, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
