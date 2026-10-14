"""Formato `.cgx` v1: un flujo de bytes cifrado y autenticado, sin SQLite ni rutas. Aquí solo hay criptografía.

Disposición (todo en big-endian):
  cabecera fija de 137 bytes: magia `CGX1`, versión, modo (1 frase, 2 destinatario), fecha UTC, parámetros de scrypt
  (log2 N, r, p, sal de 16), pública efímera (32), pública del destinatario (32), prefijo de nonce (8) y compromiso de clave (32);
  trozos de 1 MiB (el último, más corto) cifrados con ChaCha20-Poly1305: nonce = prefijo + índice real (4 bytes) y
  AAD = cabecera + índice + bandera de último trozo, así que el truncado, la duplicación, la reordenación y los saltos fallan;
  en el modo 2, un tráiler de 64 bytes: firma Ed25519 de (cabecera + SHA-256 de todos los trozos cifrados).
El texto en claro lleva delante su longitud (8 bytes) y detrás un relleno aleatorio hasta un múltiplo de 64 KiB, para que el tamaño
del fichero no delate el crecimiento diario de la base. Cada fichero usa una clave nueva (sal o efímera nueva).

`descifrar_*` escribe en `salida` según avanza: hasta que la función termina sin error, lo escrito NO es de fiar (hay que
descartarlo si falla). Dependencia perezosa: sin `cryptography` solo falla el uso, con un mensaje claro.
"""
import base64
import hashlib
import hmac
import os
import struct
import time

MAGIA = b"CGX1"
VERSION = 1
MODO_FRASE, MODO_DESTINATARIO = 1, 2
TROZO = 1 << 20
ETIQUETA = 16
RELLENO = 1 << 16
TAM_FIRMA = 64
SCRYPT_ESCRITURA = (17, 8, 1)
SCRYPT_MEMORIA_MAX = 256 << 20
FRASE_MIN = 12

_CAB = struct.Struct(">4sBBQBBB16s32s32s8s")           # hasta el compromiso (105 bytes)
TAM_BASE = _CAB.size
TAM_CABECERA = TAM_BASE + 32


class ErrorCgx(Exception):
    """Fichero o clave no válidos (el mensaje es apto para mostrar al usuario)."""


def _cripto():
    try:
        from cryptography.exceptions import InvalidSignature, InvalidTag
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as e:
        raise ErrorCgx("falta la librería 'cryptography': instálala (pip install cryptography) para exportar o restaurar copias") from e
    return {"InvalidSignature": InvalidSignature, "InvalidTag": InvalidTag, "hashes": hashes, "ed25519": ed25519, "x25519": x25519,
            "AEAD": ChaCha20Poly1305, "HKDF": HKDF, "Scrypt": Scrypt}


# ---------------------------------------------------------------- claves y su texto

def _b32(datos):
    return base64.b32encode(datos).decode().rstrip("=").lower()


def _de_b32(texto):
    texto = texto.strip().lower().replace("-", "").replace(" ", "")
    try:
        return base64.b32decode(texto.upper() + "=" * (-len(texto) % 8))
    except ValueError as e:
        raise ErrorCgx("clave mal escrita") from e


PREFIJOS = {"publica": "cgx1", "firma_publica": "cgxs1", "privada": "CGX-SECRET-KEY-1-", "firma_privada": "CGX-SIGN-KEY-1-"}


def codificar_clave(tipo, crudo):
    """Texto de una línea con prefijo y suma de control: una errata al teclearla se detecta."""
    if len(crudo) != 32:
        raise ErrorCgx("clave de longitud no válida")
    suma = hashlib.sha256(PREFIJOS[tipo].encode() + crudo).digest()[:4]
    return PREFIJOS[tipo] + _b32(crudo + suma)


def decodificar_clave(tipo, texto):
    prefijo = PREFIJOS[tipo]
    texto = texto.strip()
    if not texto.lower().startswith(prefijo.lower()):
        raise ErrorCgx("no es una clave de este tipo")
    datos = _de_b32(texto[len(prefijo):])
    crudo, suma = datos[:-4], datos[-4:]
    if len(crudo) != 32 or not hmac.compare_digest(hashlib.sha256(prefijo.encode() + crudo).digest()[:4], suma):
        raise ErrorCgx("clave mal escrita (la suma de control no cuadra)")
    return crudo


def huella(publica_cruda):
    h = hashlib.sha256(publica_cruda).hexdigest()[:32]
    return " ".join(h[i:i + 4] for i in range(0, 32, 4))


def generar_par():
    """(privada, pública) X25519 en bruto (32 bytes cada una)."""
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
    x = _cripto()["x25519"].X25519PrivateKey.generate()
    return (x.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()), x.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def generar_par_firma():
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
    e = _cripto()["ed25519"].Ed25519PrivateKey.generate()
    return (e.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()), e.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def publica_de(privada):
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return _cripto()["x25519"].X25519PrivateKey.from_private_bytes(privada).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


# ---------------------------------------------------------------- cabecera y claves simétricas

def _cabecera_base(modo, fecha, kdf, sal, efimera, destinatario, prefijo):
    return _CAB.pack(MAGIA, VERSION, modo, fecha, kdf[0], kdf[1], kdf[2], sal, efimera, destinatario, prefijo)


def _compromiso(c, clave):
    return c["HKDF"](algorithm=c["hashes"].SHA256(), length=32, salt=None, info=b"cgx1 compromiso").derive(clave)


def _clave_frase(c, frase, kdf, sal):
    log2n, r, p = kdf
    if not (14 <= log2n <= 24) or not (1 <= r <= 16) or not (1 <= p <= 2) or 128 * (1 << log2n) * r * p > SCRYPT_MEMORIA_MAX:
        raise ErrorCgx("parámetros de scrypt fuera de los límites admitidos")
    return c["Scrypt"](salt=sal, length=32, n=1 << log2n, r=r, p=p).derive(frase.encode("utf-8"))


def _clave_destinatario(c, base, compartido):
    return c["HKDF"](algorithm=c["hashes"].SHA256(), length=32, salt=None, info=b"cgx1 clave" + base).derive(compartido)


def comprueba_frase(frase):
    if len(frase) < FRASE_MIN or len(set(frase)) < 4:
        raise ErrorCgx(f"la frase de paso necesita al menos {FRASE_MIN} caracteres y no ser repetitiva")


# ---------------------------------------------------------------- cifrado

def _cifrar(entrada, longitud, salida, clave, cabecera, aleatorio, firma_privada=None):
    c = _cripto()
    aead = c["AEAD"](clave)
    prefijo = cabecera[TAM_BASE - 8:TAM_BASE]
    relleno = (-(8 + longitud)) % RELLENO
    pendiente = struct.pack(">Q", longitud)
    restante = longitud
    falta_relleno = relleno
    hash_trozos = hashlib.sha256()
    salida.write(cabecera)
    indice = 0

    def siguiente(n):
        nonlocal pendiente, restante, falta_relleno
        while len(pendiente) < n and (restante or falta_relleno):
            if restante:
                dato = entrada.read(min(TROZO, restante))
                if not dato:
                    raise ErrorCgx("la entrada terminó antes de lo indicado")
                restante -= len(dato)
                pendiente += dato
            else:
                pendiente += aleatorio(falta_relleno)
                falta_relleno = 0
        trozo, pendiente = pendiente[:n], pendiente[n:]
        return trozo

    actual = siguiente(TROZO)
    while True:
        proximo = siguiente(TROZO)
        ultimo = not proximo
        if indice >= 1 << 32:
            raise ErrorCgx("fichero demasiado grande")
        cifrado = aead.encrypt(prefijo + struct.pack(">I", indice), actual, cabecera + struct.pack(">IB", indice, int(ultimo)))
        salida.write(cifrado)
        hash_trozos.update(cifrado)
        indice += 1
        if ultimo:
            break
        actual = proximo
    if firma_privada is not None:
        firmante = c["ed25519"].Ed25519PrivateKey.from_private_bytes(firma_privada)
        salida.write(firmante.sign(cabecera + hash_trozos.digest()))


def cifrar_con_frase(entrada, longitud, salida, frase, kdf=SCRYPT_ESCRITURA, ahora=None, aleatorio=os.urandom):
    """Cifra `longitud` bytes leídos de `entrada` con una clave derivada de la frase."""
    comprueba_frase(frase)
    c = _cripto()
    sal, prefijo = aleatorio(16), aleatorio(8)
    base = _cabecera_base(MODO_FRASE, int(time.time() if ahora is None else ahora), kdf, sal, bytes(32), bytes(32), prefijo)
    clave = _clave_frase(c, frase, kdf, sal)
    _cifrar(entrada, longitud, salida, clave, base + _compromiso(c, clave), aleatorio)


def cifrar_a_destinatario(entrada, longitud, salida, publica, firma_privada, ahora=None, aleatorio=os.urandom, efimera_privada=None):
    """Cifra a la pública del destinatario y firma con la clave de firma del dispositivo."""
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
    c = _cripto()
    x = c["x25519"]
    efimera = x.X25519PrivateKey.from_private_bytes(efimera_privada) if efimera_privada else x.X25519PrivateKey.generate()
    efimera_pub = efimera.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    try:
        compartido = efimera.exchange(x.X25519PublicKey.from_public_bytes(publica))
    except ValueError as e:
        raise ErrorCgx("clave pública del destinatario no válida") from e
    base = _cabecera_base(MODO_DESTINATARIO, int(time.time() if ahora is None else ahora), (0, 0, 0), bytes(16), efimera_pub, publica, aleatorio(8))
    clave = _clave_destinatario(c, base, compartido)
    _cifrar(entrada, longitud, salida, clave, base + _compromiso(c, clave), aleatorio, firma_privada)


# ---------------------------------------------------------------- descifrado

def _tamano(entrada):
    entrada.seek(0, os.SEEK_END)
    n = entrada.tell()
    entrada.seek(0)
    return n


def leer_cabecera(entrada):
    """(modo, fecha, kdf, sal, efímera, destinatario, prefijo, cabecera completa) o ErrorCgx."""
    cab = entrada.read(TAM_CABECERA)
    if len(cab) != TAM_CABECERA:
        raise ErrorCgx("el fichero es demasiado corto: no es una copia válida")
    magia, version, modo, fecha, n, r, p, sal, efimera, destinatario, prefijo = _CAB.unpack(cab[:TAM_BASE])
    if magia != MAGIA or version != VERSION:
        raise ErrorCgx("no es un fichero .cgx de esta versión")
    if modo not in (MODO_FRASE, MODO_DESTINATARIO):
        raise ErrorCgx("modo de cifrado desconocido")
    return modo, fecha, (n, r, p), sal, efimera, destinatario, prefijo, cab


def _descifrar(entrada, salida, clave, cab, tamano_total, con_firma, firmas_publicas):
    c = _cripto()
    if not hmac.compare_digest(_compromiso(c, clave), cab[TAM_BASE:]):
        raise ErrorCgx("clave o frase incorrecta")
    aead = c["AEAD"](clave)
    prefijo = cab[TAM_BASE - 8:TAM_BASE]
    cuerpo = tamano_total - TAM_CABECERA - (TAM_FIRMA if con_firma else 0)
    paso = TROZO + ETIQUETA
    n_trozos, resto = divmod(cuerpo, paso)
    if resto:
        n_trozos += 1
    if cuerpo <= 0 or (resto and resto <= ETIQUETA) or n_trozos > 1 << 32:
        raise ErrorCgx("el fichero está truncado o dañado")
    hash_trozos = hashlib.sha256()
    longitud = vistos = None
    for i in range(n_trozos):
        cifrado = entrada.read(min(paso, cuerpo - i * paso))          # el último es más corto, y tras él puede venir el tráiler
        hash_trozos.update(cifrado)
        try:
            claro = aead.decrypt(prefijo + struct.pack(">I", i), cifrado, cab + struct.pack(">IB", i, int(i == n_trozos - 1)))
        except c["InvalidTag"]:
            raise ErrorCgx("el fichero está dañado o ha sido manipulado") from None
        if i == 0:
            if len(claro) < 8:
                raise ErrorCgx("el fichero está dañado")
            longitud = struct.unpack(">Q", claro[:8])[0]
            claro, vistos = claro[8:], 0
        util = max(0, min(len(claro), longitud - vistos))
        if util:
            salida.write(claro[:util])
        vistos += util
    total_claro = (n_trozos - 1) * TROZO + (cuerpo - (n_trozos - 1) * paso - ETIQUETA)
    if vistos != longitud or total_claro != 8 + longitud + (-(8 + longitud)) % RELLENO:
        raise ErrorCgx("el fichero está dañado: longitud incoherente")
    if con_firma:
        firma = entrada.read(TAM_FIRMA)
        if len(firma) != TAM_FIRMA or entrada.read(1):
            raise ErrorCgx("el fichero está dañado")
        mensaje = cab + hash_trozos.digest()
        for pub in firmas_publicas or ():
            try:
                c["ed25519"].Ed25519PublicKey.from_public_bytes(pub).verify(firma, mensaje)
                return
            except (c["InvalidSignature"], ValueError):
                continue
        raise ErrorCgx("la copia no está firmada por un dispositivo conocido")
    if entrada.read(1):
        raise ErrorCgx("el fichero tiene datos de más")


def descifrar_con_frase(entrada, salida, frase):
    modo, _f, kdf, sal, _e, _d, _p, cab = leer_cabecera(entrada)
    if modo != MODO_FRASE:
        raise ErrorCgx("esta copia está cifrada para una clave, no con frase")
    c = _cripto()
    clave = _clave_frase(c, frase, kdf, sal)
    _descifrar(entrada, salida, clave, cab, _tamano_desde_cabecera(entrada), False, None)


def descifrar_con_privada(entrada, salida, privada, firmas_publicas):
    modo, _f, _kdf, _s, efimera, destinatario, _p, cab = leer_cabecera(entrada)
    if modo != MODO_DESTINATARIO:
        raise ErrorCgx("esta copia está cifrada con frase, no para una clave")
    c = _cripto()
    x = c["x25519"]
    mia = publica_de(privada)
    if not hmac.compare_digest(mia, destinatario):
        raise ErrorCgx(f"esta copia es para otra clave (huella {huella(destinatario)})")
    try:
        compartido = x.X25519PrivateKey.from_private_bytes(privada).exchange(x.X25519PublicKey.from_public_bytes(efimera))
    except ValueError as e:
        raise ErrorCgx("la copia lleva una clave efímera no válida") from e
    clave = _clave_destinatario(c, cab[:TAM_BASE], compartido)
    _descifrar(entrada, salida, clave, cab, _tamano_desde_cabecera(entrada), True, firmas_publicas)


def _tamano_desde_cabecera(entrada):
    pos = entrada.tell()
    n = _tamano(entrada)
    entrada.seek(pos)
    return n
