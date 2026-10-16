"""Icono de la barra: de qué se llena la pastilla y qué tamaño tiene la imagen (sin AppKit)."""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PIL import Image
    from infobar import icono_barra
except ImportError:              # sin Pillow no hay icono que probar
    icono_barra = None


@unittest.skipIf(icono_barra is None, "hace falta Pillow")
class TestRelleno(unittest.TestCase):
    def test_con_cuota_de_5h_manda_la_cuota(self):
        self.assertEqual(icono_barra.relleno(45, 99e6, 8e6), (0.45, False))
        self.assertEqual(icono_barra.relleno(95, 0, 0), (0.95, True))

    def test_sin_cuota_mide_el_ritmo_contra_su_limite(self):
        self.assertEqual(icono_barra.relleno(0, 4e6, 8e6), (0.5, False))
        self.assertEqual(icono_barra.relleno(0, 20e6, 8e6), (1.0, True))

    def test_sin_cuota_ni_limite_queda_vacia(self):
        self.assertEqual(icono_barra.relleno(0, 20e6, 0), (0.0, False))


@unittest.skipIf(icono_barra is None, "hace falta Pillow")
class TestDibujo(unittest.TestCase):
    def imagen(self, **kw):
        datos, ancho = icono_barra.dibuja("123,4 M", 0.4, **kw)
        return Image.open(io.BytesIO(datos)), ancho

    def test_doble_resolucion_y_alto_fijo(self):
        im, ancho = self.imagen()
        self.assertEqual(im.size, (ancho * icono_barra.ESCALA, icono_barra.ALTO * icono_barra.ESCALA))

    def test_el_punto_solo_aparece_con_servidores_y_ensancha_la_pastilla(self):
        _, sin = self.imagen(punto=None)
        _, con = self.imagen(punto="verde")
        self.assertGreater(con, sin)
        _, raro = self.imagen(punto="desconocido")
        self.assertEqual(raro, sin)

    def test_el_color_del_punto_sigue_al_estado(self):
        im, ancho = self.imagen(punto="rojo")
        x = im.size[0] - 1
        rojos = [im.getpixel((xx, im.size[1] // 2)) for xx in range(x - 30, x)]
        self.assertTrue(any(p[:3] == icono_barra.PUNTO["rojo"] and p[3] == 255 for p in rojos))


if __name__ == "__main__":
    unittest.main()
