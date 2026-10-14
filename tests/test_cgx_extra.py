"""Segunda ronda del formato .cgx (revisión de la criptografía): vector de dos trozos, compromiso antes de descifrar, bordes de scrypt,
ficheros falsificados pero bien firmados, tipo de contenido, normalización de la frase, excepciones siempre ErrorCgx y lecturas cortas."""
import io
import os
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_cgx import FIJA, FRASE, HAY, Base, abre_frase, aleatorio_fijo, cifra_frase  # noqa: E402
from infobar import cgx  # noqa: E402


class LecturaCorta(io.BytesIO):
    """Un flujo que devuelve como mucho 7 bytes por lectura (como una unidad de red o una tubería)."""
    def read(self, n=-1):
        return super().read(7 if n is None or n < 0 or n > 7 else n)


@unittest.skipUnless(HAY, "falta cryptography")
class TestExtra(Base):
    def test_vector_dos_trozos(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        datos = bytes(range(256)) * (cgx.TROZO // 256 + 1)
        blob = cifra_frase(datos, ahora=FIJA, aleatorio=aleatorio_fijo)
        sal, prefijo = aleatorio_fijo(16), aleatorio_fijo(8)
        base = struct.pack(">4sBBBQBBB16s32s32s8s", b"CGX1", 1, 1, 1, FIJA, 14, 8, 1, sal, bytes(32), bytes(32), prefijo)
        clave = Scrypt(salt=sal, length=32, n=1 << 14, r=8, p=1).derive(FRASE.encode())
        cab = base + HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"cgx1 compromiso").derive(clave)
        claro = struct.pack(">Q", len(datos)) + datos
        claro += aleatorio_fijo((-len(claro)) % 65536)
        a = ChaCha20Poly1305(clave)
        trozos = [claro[i:i + cgx.TROZO] for i in range(0, len(claro), cgx.TROZO)]
        self.assertEqual(len(trozos), 2)
        esperado = cab
        for i, t in enumerate(trozos):
            esperado += a.encrypt(prefijo + struct.pack(">I", i), t, cab + struct.pack(">IB", i, int(i == len(trozos) - 1)))
        self.assertEqual(blob, esperado)

    def test_tamano_multiplo_de_relleno(self):
        for n in (0, 1, 70000, cgx.TROZO + 3):
            blob = cifra_frase(os.urandom(n))
            cuerpo = len(blob) - cgx.TAM_CABECERA
            n_trozos = -(-cuerpo // (cgx.TROZO + cgx.ETIQUETA))
            self.assertEqual((cuerpo - cgx.ETIQUETA * n_trozos) % cgx.RELLENO, 0, n)

    def test_compromiso_antes_de_descifrar(self):
        blob = cifra_frase(b"x")
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        with mock.patch.object(ChaCha20Poly1305, "decrypt", side_effect=AssertionError("no debe descifrar")):
            with self.assertRaisesRegex(cgx.ErrorCgx, "incorrecta"):
                abre_frase(blob, "otra frase distinta")

    def test_bordes_aceptados_de_scrypt(self):
        blob = cifra_frase(b"x")
        for kdf, acepta in (((18, 8, 1), True), ((16, 16, 2), True), ((14, 17, 1), False), ((19, 8, 1), False)):
            malo = bytearray(blob)
            malo[cgx.OFF_KDF], malo[cgx.OFF_KDF + 1], malo[cgx.OFF_KDF + 2] = kdf
            with mock.patch("cryptography.hazmat.primitives.kdf.scrypt.Scrypt.derive", return_value=bytes(32)) as d:
                with self.assertRaisesRegex(cgx.ErrorCgx, "incorrecta" if acepta else "límites"):
                    abre_frase(bytes(malo))
                self.assertEqual(d.called, acepta, kdf)

    def test_mensaje_de_otra_clave(self):
        otra, _ = cgx.generar_par()
        with self.assertRaisesRegex(cgx.ErrorCgx, "otra clave"):
            self.abre_dest(self.cifra_dest(b"x"), priv=otra)

    def _forja(self, trozos):
        """Fichero modo 2 bien firmado con contenido arbitrario: lo que fabrica quien tiene la clave de firma."""
        import hashlib
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        c = cgx._cripto()
        x = c["x25519"]
        ef = x.X25519PrivateKey.generate()
        base = cgx._cabecera_base(2, cgx.TIPO_COPIA, 1, (0, 0, 0), bytes(16), ef.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
                                  self.pub, os.urandom(8))
        k = cgx._clave_destinatario(c, base, ef.exchange(x.X25519PublicKey.from_public_bytes(self.pub)))
        cab = base + cgx._compromiso(c, k)
        h, out = hashlib.sha256(), cab
        for i, t in enumerate(trozos):
            ct = c["AEAD"](k).encrypt(cab[cgx.TAM_BASE - 8:cgx.TAM_BASE] + struct.pack(">I", i), t,
                                      cab + struct.pack(">IB", i, int(i == len(trozos) - 1)))
            h.update(ct)
            out += ct
        return out + ed25519.Ed25519PrivateKey.from_private_bytes(self.firma_priv).sign(cab + h.digest())

    def test_forjas_firmadas_incoherentes(self):
        casos = ([b"abc"],                                                   # trozo 0 sin longitud
                 [struct.pack(">Q", 2 ** 64 - 1) + b"Z" * 100],               # longitud mayor que lo que hay
                 [struct.pack(">Q", 5) + b"hola!" + bytes(10)],               # relleno que no llega a 64 KiB
                 [struct.pack(">Q", 70000) + bytes(65528)])                   # longitud mayor, tamaño canónico
        for trozos in casos:
            with self.assertRaises(cgx.ErrorCgx):
                self.abre_dest(self._forja(trozos))
        bueno = [struct.pack(">Q", 3) + b"abc" + bytes(65525)]
        self.assertEqual(self.abre_dest(self._forja(bueno)), b"abc")

    def test_frase_borde_12(self):
        with self.assertRaises(cgx.ErrorCgx):
            cifra_frase(b"x", frase="abcdefghijk")
        self.assertEqual(abre_frase(cifra_frase(b"x", frase="abcdefghijkl"), "abcdefghijkl"), b"x")

    def test_suma_cubre_el_prefijo(self):
        t = cgx.codificar_clave("privada", self.priv)
        with self.assertRaises(cgx.ErrorCgx):
            cgx.decodificar_clave("firma_privada", cgx.PREFIJOS["firma_privada"] + t[len(cgx.PREFIJOS["privada"]):])

    def test_efimera_a_cero_en_su_sitio(self):
        blob = bytearray(self.cifra_dest(b"x"))
        blob[cgx.OFF_EFIMERA:cgx.OFF_EFIMERA + 32] = bytes(32)
        with self.assertRaisesRegex(cgx.ErrorCgx, "efímera|incorrecta"):
            self.abre_dest(bytes(blob))

    # ---- lo añadido tras la revisión
    def test_tipo_de_contenido_no_intercambiable(self):
        clave = io.BytesIO()
        cgx.cifrar_con_frase(io.BytesIO(b"secreto"), 7, clave, FRASE, kdf=(14, 8, 1), tipo=cgx.TIPO_CLAVE)
        with self.assertRaisesRegex(cgx.ErrorCgx, "clave privada"):
            abre_frase(clave.getvalue())                                    # una exportación no puede ser una clave, ni al revés
        self.assertEqual(cgx.descifrar_con_frase(io.BytesIO(clave.getvalue()), s := io.BytesIO(), FRASE, tipo=cgx.TIPO_CLAVE), None)
        self.assertEqual(s.getvalue(), b"secreto")
        copia = self.cifra_dest(b"x")
        with self.assertRaisesRegex(cgx.ErrorCgx, "copia automática"):
            cgx.descifrar_con_privada(io.BytesIO(copia), io.BytesIO(), self.priv, [self.firma_pub], tipo=cgx.TIPO_EXPORT)

    def test_tipo_desconocido_en_la_cabecera(self):
        blob = bytearray(cifra_frase(b"x"))
        blob[6] = 9
        with self.assertRaises(cgx.ErrorCgx):
            abre_frase(bytes(blob))

    def test_frase_normalizada_nfc(self):
        nfc, nfd = "contraseña muy larga é", "contraseña muy larga é"
        self.assertNotEqual(nfc, nfd)
        self.assertEqual(abre_frase(cifra_frase(b"datos", frase=nfc), nfd), b"datos")

    def test_frase_con_sustituto_suelto_no_se_filtra(self):
        frase = "una frase larga \ud800 con sustituto"
        with self.assertRaises(cgx.ErrorCgx) as cm:
            cifra_frase(b"x", frase=frase)
        self.assertNotIn("sustituto", repr(cm.exception))
        self.assertNotIn("sustituto", str(cm.exception))
        with self.assertRaises(cgx.ErrorCgx):
            abre_frase(cifra_frase(b"x"), frase)

    def test_claves_mal_formadas_son_errorcgx(self):
        for f in (lambda: cgx.publica_de(b"corta"), lambda: cgx.publica_de(None),
                  lambda: cgx.cifrar_a_destinatario(io.BytesIO(b"x"), 1, io.BytesIO(), self.pub, b"corta"),
                  lambda: cgx.cifrar_a_destinatario(io.BytesIO(b"x"), 1, io.BytesIO(), b"corta", self.firma_priv),
                  lambda: cgx.descifrar_con_privada(io.BytesIO(self.cifra_dest(b"x")), io.BytesIO(), b"corta", [self.firma_pub])):
            with self.assertRaises(cgx.ErrorCgx):
                f()

    def test_firma_invalida_no_escribe_nada(self):
        salida = io.BytesIO()
        with self.assertRaises(cgx.ErrorCgx):
            cgx.cifrar_a_destinatario(io.BytesIO(b"x" * 100), 100, salida, self.pub, b"mala")
        self.assertEqual(salida.getvalue(), b"")

    def test_sin_firmas_conocidas_no_lee_nada(self):
        entrada = io.BytesIO(self.cifra_dest(b"x"))
        with self.assertRaisesRegex(cgx.ErrorCgx, "firma"):
            cgx.descifrar_con_privada(entrada, io.BytesIO(), self.priv, [])
        self.assertEqual(entrada.tell(), 0)

    def test_entrada_que_falla_es_errorcgx(self):
        class Rota(io.RawIOBase):
            def readable(self):
                return True

            def read(self, n=-1):
                raise OSError("tubería rota")
        with self.assertRaises(cgx.ErrorCgx):
            cgx.cifrar_con_frase(Rota(), 100, io.BytesIO(), FRASE, kdf=(14, 8, 1))

    def test_lecturas_cortas_no_rompen_ficheros_validos(self):
        datos = os.urandom(cgx.TROZO + 100)
        blob = self.cifra_dest(datos)
        salida = io.BytesIO()
        cgx.descifrar_con_privada(LecturaCorta(blob), salida, self.priv, [self.firma_pub])
        self.assertEqual(salida.getvalue(), datos)
        self.assertEqual(abre_frase(cifra_frase(datos)), datos)
        entrada = LecturaCorta(datos)
        s2 = io.BytesIO()
        cgx.cifrar_con_frase(entrada, len(datos), s2, FRASE, kdf=(14, 8, 1))
        self.assertEqual(abre_frase(s2.getvalue()), datos)

    def test_verificar_con_la_clave_del_propio_fichero(self):
        import hashlib
        datos = os.urandom(2 * cgx.TROZO + 3)
        salida = io.BytesIO()
        r = cgx.cifrar_a_destinatario(io.BytesIO(datos), len(datos), salida, self.pub, self.firma_priv)
        self.assertEqual(r["sha256_claro"], hashlib.sha256(datos).hexdigest())
        blob = salida.getvalue()
        self.assertEqual(cgx.verificar_con_clave(io.BytesIO(blob), r["clave"], [self.firma_pub]), r["sha256_claro"])
        malo = bytearray(blob)
        malo[cgx.TAM_CABECERA + cgx.TROZO + 40] ^= 1
        with self.assertRaises(cgx.ErrorCgx):
            cgx.verificar_con_clave(io.BytesIO(bytes(malo)), r["clave"], [self.firma_pub])
        with self.assertRaises(cgx.ErrorCgx):
            cgx.verificar_con_clave(io.BytesIO(blob[:-70]), r["clave"], [self.firma_pub])
        with self.assertRaises(cgx.ErrorCgx):
            cgx.verificar_con_clave(io.BytesIO(blob), r["clave"], [])
        r2 = cgx.cifrar_con_frase(io.BytesIO(datos), len(datos), (s2 := io.BytesIO()), FRASE, kdf=(14, 8, 1))
        self.assertEqual(cgx.verificar_con_clave(io.BytesIO(s2.getvalue()), r2["clave"], None, tipo=cgx.TIPO_EXPORT), r["sha256_claro"])

    def test_sin_cryptography_las_funciones_de_claves_dan_errorcgx(self):
        with mock.patch.dict(sys.modules, {"cryptography": None, "cryptography.exceptions": None}):
            for f in (cgx.generar_par, cgx.generar_par_firma, lambda: cgx.publica_de(bytes(32))):
                with self.assertRaisesRegex(cgx.ErrorCgx, "cryptography"):
                    f()


if __name__ == "__main__":
    unittest.main()
