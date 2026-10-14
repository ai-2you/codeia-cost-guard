"""Formato .cgx: ida y vuelta en los dos modos, vector conocido recalculado con las primitivas, y manipulación de cada región
(cabecera, cada trozo, etiquetas, tráiler), truncado, duplicado, reordenado, límites de scrypt y claves mal escritas."""
import hashlib
import io
import os
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import cgx  # noqa: E402

try:
    import cryptography  # noqa: F401
    HAY = True
except ImportError:
    HAY = False

KDF = (14, 8, 1)                      # el mínimo admitido: los tests no esperan 0,7 s por fichero
FRASE = "una frase de prueba larga"
FIJA = 1_790_000_000


def aleatorio_fijo(n):
    return bytes((i * 7 + 3) % 256 for i in range(n))


def cifra_frase(datos, frase=FRASE, **kw):
    salida = io.BytesIO()
    cgx.cifrar_con_frase(io.BytesIO(datos), len(datos), salida, frase, kdf=KDF, **kw)
    return salida.getvalue()


def abre_frase(blob, frase=FRASE):
    salida = io.BytesIO()
    cgx.descifrar_con_frase(io.BytesIO(blob), salida, frase)
    return salida.getvalue()


@unittest.skipUnless(HAY, "falta cryptography")
class Base(unittest.TestCase):
    def setUp(self):
        self.priv, self.pub = cgx.generar_par()
        self.firma_priv, self.firma_pub = cgx.generar_par_firma()

    def cifra_dest(self, datos, **kw):
        salida = io.BytesIO()
        cgx.cifrar_a_destinatario(io.BytesIO(datos), len(datos), salida, self.pub, self.firma_priv, **kw)
        return salida.getvalue()

    def abre_dest(self, blob, priv=None, firmas=None):
        salida = io.BytesIO()
        cgx.descifrar_con_privada(io.BytesIO(blob), salida, priv or self.priv, [self.firma_pub] if firmas is None else firmas)
        return salida.getvalue()

    def rechaza(self, blob, abre):
        with self.assertRaises(cgx.ErrorCgx):
            abre(blob)


class TestIdaYVuelta(Base):
    TAMANOS = (0, 1, 1000, cgx.TROZO - 8, cgx.TROZO, 3 * cgx.TROZO + 7)

    def test_frase(self):
        for n in self.TAMANOS:
            datos = os.urandom(n)
            blob = cifra_frase(datos)
            self.assertEqual(abre_frase(blob), datos, n)

    def test_destinatario(self):
        for n in self.TAMANOS:
            datos = os.urandom(n)
            self.assertEqual(self.abre_dest(self.cifra_dest(datos)), datos, n)

    def test_el_tamano_no_delata_el_crecimiento_fino(self):
        a, b = len(cifra_frase(b"x" * 100)), len(cifra_frase(b"x" * 20000))
        self.assertEqual(a, b)

    def test_cada_fichero_usa_clave_nueva(self):
        self.assertNotEqual(cifra_frase(b"mismo")[:cgx.TAM_CABECERA], cifra_frase(b"mismo")[:cgx.TAM_CABECERA])


class TestVectorConocido(Base):
    def test_frase_recalculado_con_las_primitivas(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        datos = b"hola cgx"
        blob = cifra_frase(datos, ahora=FIJA, aleatorio=aleatorio_fijo)
        sal, prefijo = aleatorio_fijo(16), aleatorio_fijo(8)
        base = struct.pack(">4sBBBQBBB16s32s32s8s", b"CGX1", 1, 1, 1, FIJA, 14, 8, 1, sal, bytes(32), bytes(32), prefijo)
        clave = Scrypt(salt=sal, length=32, n=1 << 14, r=8, p=1).derive(FRASE.encode())
        comp = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"cgx1 compromiso").derive(clave)
        cab = base + comp
        claro = struct.pack(">Q", len(datos)) + datos + aleatorio_fijo(65536 - 8 - len(datos))
        esperado = cab + ChaCha20Poly1305(clave).encrypt(prefijo + struct.pack(">I", 0), claro, cab + struct.pack(">IB", 0, 1))
        self.assertEqual(blob, esperado)

    def test_vector_congelado_destinatario(self):
        priv = bytes(range(1, 33))
        firma = bytes(range(33, 65))
        pub = cgx.publica_de(priv)
        salida = io.BytesIO()
        cgx.cifrar_a_destinatario(io.BytesIO(b"vector"), 6, salida, pub, firma, ahora=FIJA, aleatorio=aleatorio_fijo,
                                  efimera_privada=bytes(range(65, 97)))
        self.assertEqual(hashlib.sha256(salida.getvalue()).hexdigest(), VECTOR_DESTINATARIO)


VECTOR_DESTINATARIO = "957500d39df75dd38cc77bd7759890350b3cb58a51a7ce64d0b0062186b731e0"


class TestManipulacion(Base):
    def test_cada_byte_de_la_cabecera(self):
        for blob, abre in ((cifra_frase(b"datos"), abre_frase), (self.cifra_dest(b"datos"), self.abre_dest)):
            for i in range(cgx.TAM_CABECERA):
                malo = bytearray(blob)
                malo[i] ^= 0x01
                self.rechaza(bytes(malo), abre)

    def test_cada_trozo_y_su_etiqueta(self):
        datos = os.urandom(2 * cgx.TROZO + 5)
        for blob, abre in ((cifra_frase(datos), abre_frase), (self.cifra_dest(datos), self.abre_dest)):
            paso = cgx.TROZO + cgx.ETIQUETA
            for pos in (cgx.TAM_CABECERA, cgx.TAM_CABECERA + 100, cgx.TAM_CABECERA + paso - 1, cgx.TAM_CABECERA + paso,
                        cgx.TAM_CABECERA + 2 * paso + 3):
                malo = bytearray(blob)
                malo[pos] ^= 0x80
                self.rechaza(bytes(malo), abre)

    def test_truncado_en_frontera_y_a_mitad(self):
        datos = os.urandom(3 * cgx.TROZO)
        paso = cgx.TROZO + cgx.ETIQUETA
        for blob, abre in ((cifra_frase(datos), abre_frase), (self.cifra_dest(datos), self.abre_dest)):
            hay_firma = abre == self.abre_dest
            fin = len(blob) - (cgx.TAM_FIRMA if hay_firma else 0)
            for corte in (cgx.TAM_CABECERA, cgx.TAM_CABECERA + paso, cgx.TAM_CABECERA + 2 * paso, fin - paso, fin - 1, fin - 20,
                          cgx.TAM_CABECERA + 50, 10):
                self.rechaza(blob[:corte] + (blob[fin:] if hay_firma and corte < fin else b""), abre)

    def test_trozos_duplicados_reordenados_y_saltados(self):
        datos = os.urandom(3 * cgx.TROZO)
        paso = cgx.TROZO + cgx.ETIQUETA
        blob = cifra_frase(datos)
        cab, cuerpo = blob[:cgx.TAM_CABECERA], blob[cgx.TAM_CABECERA:]
        t = [cuerpo[i * paso:(i + 1) * paso] for i in range(len(cuerpo) // paso + 1) if cuerpo[i * paso:(i + 1) * paso]]
        self.assertGreaterEqual(len(t), 3)
        for orden in ([0, 0, 1, 2], [1, 0, 2], [0, 2], [0, 1, 1], [2, 1, 0]):
            self.rechaza(cab + b"".join(t[i] for i in orden if i < len(t)), abre_frase)

    def test_intercambio_de_dos_trozos_de_mitad_con_el_mismo_tamano(self):
        # cambiar dos trozos completos de sitio conserva el tamaño total y la longitud declarada: solo el índice real
        # dentro del nonce y del AAD lo delata
        datos = os.urandom(4 * cgx.TROZO + 5)
        paso = cgx.TROZO + cgx.ETIQUETA
        for blob, abre in ((cifra_frase(datos), abre_frase), (self.cifra_dest(datos), self.abre_dest)):
            c = cgx.TAM_CABECERA
            malo = bytearray(blob)
            malo[c + paso:c + 2 * paso], malo[c + 2 * paso:c + 3 * paso] = blob[c + 2 * paso:c + 3 * paso], blob[c + paso:c + 2 * paso]
            self.rechaza(bytes(malo), abre)
            self.assertEqual(abre(blob), datos)

    def test_bytes_de_mas_y_cero_trozos(self):
        self.rechaza(cifra_frase(b"x") + b"\0", abre_frase)
        self.rechaza(cifra_frase(b"x")[:cgx.TAM_CABECERA], abre_frase)
        self.rechaza(b"", abre_frase)
        self.rechaza(self.cifra_dest(b"x") + b"\0", self.abre_dest)

    def test_tailer_manipulado_o_de_otra_clave(self):
        blob = self.cifra_dest(b"datos")
        malo = bytearray(blob)
        malo[-1] ^= 1
        self.rechaza(bytes(malo), self.abre_dest)
        otra_priv, otra_pub = cgx.generar_par_firma()
        self.rechaza(blob, lambda b: self.abre_dest(b, firmas=[otra_pub]))
        self.rechaza(blob, lambda b: self.abre_dest(b, firmas=[]))
        self.assertEqual(self.abre_dest(blob, firmas=[otra_pub, self.firma_pub]), b"datos")

    def test_copia_de_otro_dispositivo_firmada_por_otro(self):
        otra_priv, otra_pub = cgx.generar_par_firma()
        salida = io.BytesIO()
        cgx.cifrar_a_destinatario(io.BytesIO(b"ajena"), 5, salida, self.pub, otra_priv)
        self.rechaza(salida.getvalue(), self.abre_dest)
        self.assertEqual(self.abre_dest(salida.getvalue(), firmas=[self.firma_pub, otra_pub]), b"ajena")


class TestClaves(Base):
    def test_frase_incorrecta_y_destinatario_equivocado(self):
        self.rechaza(cifra_frase(b"x"), lambda b: abre_frase(b, "otra frase distinta"))
        otra, _ = cgx.generar_par()
        self.rechaza(self.cifra_dest(b"x"), lambda b: self.abre_dest(b, priv=otra))

    def test_modo_cruzado(self):
        self.rechaza(cifra_frase(b"x"), self.abre_dest)
        self.rechaza(self.cifra_dest(b"x"), abre_frase)

    def test_frases_debiles(self):
        for f in ("corta", "aaaaaaaaaaaaaaaa", "ab" * 10):
            with self.assertRaises(cgx.ErrorCgx):
                cifra_frase(b"x", frase=f)

    def test_limites_de_scrypt_al_leer(self):
        blob = bytearray(cifra_frase(b"x"))
        for kdf in ((13, 8, 1), (19, 8, 1), (17, 32, 1), (14, 0, 1), (14, 8, 3), (14, 8, 0), (24, 16, 2)):
            malo = bytearray(blob)
            malo[cgx.OFF_KDF], malo[cgx.OFF_KDF + 1], malo[cgx.OFF_KDF + 2] = kdf
            with mock.patch("cryptography.hazmat.primitives.kdf.scrypt.Scrypt.derive", side_effect=AssertionError("no debe derivarse")):
                self.rechaza(bytes(malo), abre_frase)

    def test_publica_de_orden_bajo_o_cero(self):
        salida = io.BytesIO()
        with self.assertRaises(cgx.ErrorCgx):
            cgx.cifrar_a_destinatario(io.BytesIO(b"x"), 1, salida, bytes(32), self.firma_priv)
        blob = bytearray(self.cifra_dest(b"x"))
        blob[cgx.OFF_EFIMERA:cgx.OFF_EFIMERA + 32] = bytes(32)          # efímera a cero en la cabecera
        self.rechaza(bytes(blob), self.abre_dest)

    def test_texto_de_las_claves(self):
        for tipo, crudo in (("publica", self.pub), ("privada", self.priv), ("firma_publica", self.firma_pub), ("firma_privada", self.firma_priv)):
            texto = cgx.codificar_clave(tipo, crudo)
            self.assertEqual(cgx.decodificar_clave(tipo, texto), crudo)
            self.assertEqual(cgx.decodificar_clave(tipo, texto.upper() if tipo == "publica" else texto), crudo)
        texto = cgx.codificar_clave("privada", self.priv)
        self.assertTrue(texto.startswith("CGX-SECRET-KEY-1-"))
        for i in range(len(cgx.PREFIJOS["privada"]), len(texto) - 1):
            malo = texto[:i] + ("a" if texto[i] != "a" else "b") + texto[i + 1:]
            with self.assertRaises(cgx.ErrorCgx):
                cgx.decodificar_clave("privada", malo)
        with self.assertRaises(cgx.ErrorCgx):
            cgx.decodificar_clave("privada", cgx.codificar_clave("publica", self.pub))

    def test_huella_estable_y_legible(self):
        h = cgx.huella(self.pub)
        self.assertEqual(h, cgx.huella(self.pub))
        self.assertEqual(len(h.split()), 8)

    def test_sin_cryptography_mensaje_claro(self):
        with mock.patch.dict(sys.modules, {"cryptography": None, "cryptography.exceptions": None}):
            with self.assertRaisesRegex(cgx.ErrorCgx, "cryptography"):
                cifra_frase(b"x")


if __name__ == "__main__":
    unittest.main()
