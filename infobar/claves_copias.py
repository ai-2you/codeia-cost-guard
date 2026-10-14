"""Claves de las copias cifradas: generar, guardar, cargar y ensayar. Nada de esto abre la base ni toca la red.

La clave privada del destinatario NO se queda en la máquina: se escribe donde diga el usuario (fuera del equipo, en un
medio extraíble o un gestor) y el ensayo obligatorio la vuelve a leer desde ese fichero antes de dar por buenas las copias.
La clave de firma del dispositivo sí vive en `DATOS` (0600): sirve para que una copia fabricada por otro no se pueda restaurar.
"""
import io
import os
import stat

from . import cgx, plataforma, rutas

AVISO = ("Sin esta clave las copias son irrecuperables. No existe recuperación posible. "
         "Guárdala en dos sitios fuera de este equipo (un medio extraíble y un gestor de contraseñas, o impresa).")


def _escribe_nuevo(ruta, contenido):
    """Crea el fichero con 0600 y sin pisar uno existente."""
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(contenido)
    except BaseException:
        try:
            os.remove(ruta)
        except OSError:
            pass
        raise


def _permisos_cerrados(ruta):
    if os.name != "nt" and os.stat(ruta).st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise cgx.ErrorCgx("el fichero de clave es legible por otros usuarios: ponle permisos 0600 (chmod 600)")


# ---------------------------------------------------------------- clave de firma del dispositivo

def ruta_firma():
    return os.path.join(rutas.DATOS, "clave_firma.txt")


def clave_de_firma():
    """(privada, pública) de firma del dispositivo; se crea la primera vez."""
    ruta = ruta_firma()
    os.makedirs(rutas.DATOS, mode=0o700, exist_ok=True)
    if not os.path.exists(ruta):
        priv, _ = cgx.generar_par_firma()
        try:
            _escribe_nuevo(ruta, (cgx.codificar_clave("firma_privada", priv) + "\n").encode())
        except FileExistsError:
            pass                                             # otro proceso la creó a la vez
    _permisos_cerrados(ruta)
    with open(ruta, encoding="utf-8") as f:
        priv = cgx.decodificar_clave("firma_privada", f.read())
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pub = ed25519.Ed25519PrivateKey.from_private_bytes(priv).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv, pub


# ---------------------------------------------------------------- clave privada del destinatario

def _dentro_de(ruta, carpeta):
    """¿`ruta` es `carpeta` o está dentro? Sin distinguir mayúsculas donde el sistema no las distingue (APFS, NTFS) y siguiendo
    enlaces: la misma carpeta escrita con otro caso o por un enlace no salta la guarda."""
    ruta, carpeta = os.path.realpath(ruta), os.path.realpath(carpeta)
    if os.path.normcase(ruta) == os.path.normcase(carpeta) or os.path.normcase(ruta).startswith(os.path.normcase(carpeta).rstrip(os.sep) + os.sep):
        return True
    actual = ruta
    while actual and os.path.exists(actual):
        try:
            if os.path.exists(carpeta) and os.path.samefile(actual, carpeta):
                return True
        except OSError:
            return False
        padre = os.path.dirname(actual)
        if padre == actual:
            break
        actual = padre
    return False


def guardar_privada(ruta, privada, frase=None, carpeta_copias=None, firmas_publicas=()):
    """Escribe la clave privada (texto con suma de control; con `frase`, dentro de un .cgx). Se niega si el destino está en la
    carpeta de copias o en una carpeta sincronizada/de red (ahí el cifrado a destinatario no serviría de nada) o ya existe."""
    destino = os.path.abspath(ruta)
    if carpeta_copias and _dentro_de(os.path.dirname(destino), carpeta_copias):
        raise cgx.ErrorCgx("no guardes la clave privada en la carpeta de las copias: quien vea las copias vería también la clave")
    motivo = plataforma.ruta_no_fiable(os.path.dirname(destino))
    if motivo:
        raise cgx.ErrorCgx(f"no guardes la clave privada en {motivo}")
    lineas = [cgx.codificar_clave("privada", privada)] + [cgx.codificar_clave("firma_publica", f) for f in firmas_publicas]
    texto = ("\n".join(lineas) + "\n").encode()           # la firma pública va con la privada: el fichero que SÍ conservas basta
    if frase:
        salida = io.BytesIO()
        cgx.cifrar_con_frase(io.BytesIO(texto), len(texto), salida, frase, tipo=cgx.TIPO_CLAVE)
        texto = salida.getvalue()
    try:
        _escribe_nuevo(destino, texto)
    except FileExistsError:
        raise cgx.ErrorCgx("ya existe un fichero en esa ruta: no se sobrescribe una clave") from None


def cargar_privada(ruta, frase_fn=None):
    """(privada en bruto, avisos). Ver `cargar_paquete`."""
    privada, _firmas, avisos = cargar_paquete(ruta, frase_fn)
    return privada, avisos


def cargar_paquete(ruta, frase_fn=None):
    """(privada en bruto, firmas públicas que el fichero lleva, avisos). `frase_fn()` devuelve la frase si el fichero está
    protegido. Nunca recibe la clave por argumento."""
    if not os.path.isfile(ruta):
        raise cgx.ErrorCgx("la ruta de la clave no es un fichero")
    _permisos_cerrados(ruta)
    with open(ruta, "rb") as f:
        datos = f.read(1 << 20)
    avisos = []
    motivo = plataforma.ruta_no_fiable(os.path.dirname(os.path.abspath(ruta)))
    if motivo:
        avisos.append(f"la clave privada está en {motivo}")
    if datos.startswith(cgx.MAGIA):
        if frase_fn is None:
            raise cgx.ErrorCgx("la clave está protegida con frase")
        salida = io.BytesIO()
        cgx.descifrar_con_frase(io.BytesIO(datos), salida, frase_fn(), tipo=cgx.TIPO_CLAVE)
        datos = salida.getvalue()
    lineas = [x.strip() for x in datos.decode("utf-8", "replace").splitlines() if x.strip()]
    if not lineas:
        raise cgx.ErrorCgx("el fichero de clave está vacío")
    privada = cgx.decodificar_clave("privada", lineas[0])
    firmas = [cgx.decodificar_clave("firma_publica", x) for x in lineas[1:] if x.lower().startswith(cgx.PREFIJOS["firma_publica"])]
    return privada, firmas, avisos


# ---------------------------------------------------------------- ensayo y generación

def ensayo(publica, ruta_privada, frase_fn, firma_privada, firma_publica):
    """Cifra un fichero de prueba a `publica` y lo descifra con la privada LEÍDA DEL FICHERO guardado. Si no cuadra (errata, otro
    par, fichero corrupto) lanza ErrorCgx: sin ensayo no hay copias automáticas."""
    prueba = os.urandom(200)
    cifrado = io.BytesIO()
    cgx.cifrar_a_destinatario(io.BytesIO(prueba), len(prueba), cifrado, publica, firma_privada)
    privada, _ = cargar_privada(ruta_privada, frase_fn)
    salida = io.BytesIO()
    try:
        cifrado.seek(0)
        cgx.descifrar_con_privada(cifrado, salida, privada, [firma_publica])
    except cgx.ErrorCgx as e:
        raise cgx.ErrorCgx(f"el ensayo falló: la clave guardada no descifra lo cifrado a la pública ({e})") from None
    if salida.getvalue() != prueba:
        raise cgx.ErrorCgx("el ensayo falló: el contenido descifrado no coincide")


def generar(ruta_privada, carpeta_copias=None, frase=None, frase_fn=None, crear_carpeta=False):
    """Par nuevo: guarda la privada, la ensaya y devuelve lo que hay que mostrar y guardar en la configuración.
    No toca `config.json`: lo hace el llamador solo si esto termina sin error."""
    if frase:
        cgx.comprueba_frase(frase)
    firma_priv, firma_pub = clave_de_firma()
    priv, pub = cgx.generar_par()
    if carpeta_copias and crear_carpeta:
        try:
            os.makedirs(carpeta_copias, exist_ok=True)
            comprobacion = os.path.join(carpeta_copias, f".escritura-{os.getpid()}")
            with open(comprobacion, "wb"):
                pass
            os.remove(comprobacion)
        except OSError:
            raise cgx.ErrorCgx("no se puede escribir en la carpeta de copias indicada") from None
    guardar_privada(ruta_privada, priv, frase, carpeta_copias, firmas_publicas=[firma_pub])
    try:
        ensayo(pub, ruta_privada, frase_fn or (lambda: frase), firma_priv, firma_pub)
    except cgx.ErrorCgx:
        try:
            os.remove(ruta_privada)
        except OSError:
            pass
        raise
    return {"publica": cgx.codificar_clave("publica", pub), "huella": cgx.huella(pub),
            "firma_publica": cgx.codificar_clave("firma_publica", firma_pub), "ruta_privada": os.path.abspath(ruta_privada),
            "protegida": bool(frase)}
