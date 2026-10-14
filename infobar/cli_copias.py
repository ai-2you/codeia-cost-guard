"""Órdenes de línea de comandos de las copias cifradas (`costbar.py --exportar`, `--importar`, ...).

Aquí viven las frases de paso y las claves privadas, y solo aquí: la barra y el panel nunca las tocan. La frase se pide con
`getpass` (sin eco; sin terminal real se niega), nunca por argumento ni por variable de entorno; una clave privada nunca viaja
por la línea de órdenes: se lee de un fichero (0600) o se pide sin eco.
"""
import datetime
import getpass
import io
import os
import secrets
import shutil
import sys
import tempfile
import time
import warnings

from . import almacen, cgx, claves_copias, exportacion, plataforma, restauracion, rutas
from .config_y_estado import cfg, guardar_config

ORDENES = ("--exportar", "--importar", "--claves-generar", "--copia-ahora", "--probar-restauracion", "--frase-sugerir",
           "--autoprueba-copias")


class Salida(Exception):
    """Fin con mensaje para el usuario (y código de salida distinto de 0)."""


class Terminal:
    """Entrada interactiva; los tests la sustituyen."""
    def frase(self, mensaje="Frase de paso: "):
        if not sys.stdin.isatty():
            raise Salida("hace falta un terminal interactivo para introducir la frase (no se acepta por tubería ni argumento)")
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(mensaje)
        except getpass.GetPassWarning:
            raise Salida("este terminal no permite ocultar lo que escribes: usa un terminal normal") from None

    def texto(self, mensaje):
        return input(mensaje)

    def escribe(self, texto=""):
        print(texto, flush=True)


def _sin_secretos_en_argv(argv):
    if any(a.upper().startswith(("CGX-SECRET", "CGX-SIGN")) for a in argv):
        raise Salida("una clave privada no se pasa por la línea de órdenes (queda en el historial y en `ps`): usa --clave-privada RUTA")


def _valor(argv, opcion):
    if opcion in argv:
        i = argv.index(opcion)
        if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
            raise Salida(f"{opcion} necesita un valor")
        return argv[i + 1]
    return None


def _sin_volcados():
    if os.name != "nt":
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ImportError, ValueError, OSError):
            pass


def _lista(v):
    return [v] if isinstance(v, str) else list(v or [])


def _config_copias():
    c = cfg().get("copias") or {}
    return {"carpeta": c.get("carpeta") or exportacion.carpeta_por_defecto(),
            "destinatario": _lista(c.get("destinatario")), "firmas_publicas": _lista(c.get("firmas_publicas"))}


def _valores(argv, opcion):
    """Todos los valores de una opción repetible (`--firma-publica a --firma-publica b`)."""
    return [argv[i + 1] for i, a in enumerate(argv) if a == opcion and i + 1 < len(argv) and not argv[i + 1].startswith("--")]


def _firmas_conocidas(cc, extra=()):
    """Firmas públicas de confianza: las de la configuración, las que lleva el fichero de la clave privada, las que se pasen con
    `--firma-publica` y la de este equipo. Las tres primeras permiten restaurar en un equipo nuevo."""
    firmas = [cgx.decodificar_clave("firma_publica", t) for t in cc["firmas_publicas"]]
    firmas += list(extra)
    try:
        firmas.append(claves_copias.clave_de_firma()[1])
    except cgx.ErrorCgx:
        pass                                                  # un equipo nuevo sin clave de firma propia legible: no impide restaurar
    return firmas


def _confirma_si(t, pregunta):
    return t.texto(f"{pregunta} Escribe SI para continuar: ").strip() == "SI"


# ---------------------------------------------------------------- órdenes

def frase_sugerir(t):
    crudo = secrets.token_bytes(13)
    b = cgx._b32(crudo)[:20]
    t.escribe("-".join(b[i:i + 5] for i in range(0, 20, 5)))
    t.escribe("(100 bits al azar; apúntala en papel, sin ella la exportación no se puede abrir)")


def claves_generar(argv, t):
    destino = _valor(argv, "--claves-generar")
    if not destino:
        raise Salida("uso: costbar.py --claves-generar RUTA_DE_LA_CLAVE_PRIVADA [--con-frase] [--carpeta CARPETA_DE_COPIAS]")
    if plataforma.es_checkout_de_desarrollo(rutas.RAIZ):
        raise Salida("estás en un checkout del repositorio: la configuración se guardaría en su config.json y la barra no la vería. "
                     "Ejecuta la orden desde la instalación (~/.cost-guard/costbar.py) o define COSTGUARD_DATOS")
    cc = _config_copias()
    t.escribe(claves_copias.AVISO)
    if not _confirma_si(t, "¿Entiendes que no hay forma de recuperar la clave si la pierdes?"):
        raise Salida("cancelado")
    frase = None
    if "--con-frase" in argv:
        frase = t.frase("Frase para proteger la clave privada: ")
        if t.frase("Repite la frase: ") != frase:
            raise Salida("las frases no coinciden")
    try:
        carpeta = _valor(argv, "--carpeta") or cc["carpeta"]
        r = claves_copias.generar(destino, carpeta, frase=frase, crear_carpeta=bool(_valor(argv, "--carpeta")))
    except cgx.ErrorCgx as e:
        raise Salida(f"no se generaron claves: {e}") from None
    nuevo = {"carpeta": carpeta, "destinatario": [r["publica"]] + [x for x in cc["destinatario"] if x != r["publica"]],
             "firmas_publicas": sorted(set(cc["firmas_publicas"]) | {r["firma_publica"]})}
    guardar_config({"copias": nuevo})
    t.escribe("\nPAQUETE DE RESTAURACIÓN (guárdalo fuera de este equipo junto a la clave privada):")
    t.escribe(f"  clave privada:   {r['ruta_privada']}  ({'con frase' if r['protegida'] else 'SIN frase: protégela tú'})")
    t.escribe(f"  pública:         {r['publica']}")
    t.escribe(f"  huella:          {r['huella']}")
    t.escribe(f"  firma pública:   {r['firma_publica']}")
    t.escribe(f"\nLas copias diarias irán a {carpeta}. El ensayo de descifrado con la clave guardada ha salido bien.")
    if os.path.abspath(carpeta) == os.path.abspath(exportacion.carpeta_por_defecto()):
        t.escribe("AVISO: esa carpeta está en el mismo disco que la base: un fallo del disco se llevaría base y copias. "
                  "Elige otra con --claves-generar … --carpeta RUTA (un disco externo o una carpeta de nube; va cifrado).")


def copia_ahora(t, ahora=None):
    cc = _config_copias()
    if not cc["destinatario"]:
        raise Salida("no hay clave de destinatario: ejecuta primero costbar.py --claves-generar")
    publica = cgx.decodificar_clave("publica", cc["destinatario"][0])
    firma_priv, firma_pub = claves_copias.clave_de_firma()
    try:
        con = almacen.abrir(solo_lectura=True)
    except (FileNotFoundError, almacen.ErrorAlmacen) as e:
        raise Salida(f"no hay base que copiar ({type(e).__name__})") from None
    try:
        r = exportacion.hacer_copia(con, cc["carpeta"], publica, firma_priv, firma_pub, ahora=ahora)
    except cgx.ErrorCgx as e:
        raise Salida(f"la copia no se hizo: {e}") from None
    finally:
        con.close()
    exportacion.registrar_copia(r)
    t.escribe(f"Copia {'DE UNA BASE DAÑADA ' if r['danada'] else ''}{r['archivo']} ({r['bytes']} bytes) verificada; "
              f"rotadas {r['rotadas']}; destinatario {r['huella']}")
    return r


def exportar(argv, t):
    destino = _valor(argv, "--exportar")
    if not destino:
        raise Salida("uso: costbar.py --exportar RUTA.cgx")
    if os.path.exists(destino):
        raise Salida("ese fichero ya existe: no se sobrescribe")
    frase = t.frase("Frase de paso (mínimo 12 caracteres): ")
    if t.frase("Repite la frase: ") != frase:
        raise Salida("las frases no coinciden")
    try:
        cgx.comprueba_frase(frase)
        con = almacen.abrir(solo_lectura=True)
    except (cgx.ErrorCgx, FileNotFoundError, almacen.ErrorAlmacen) as e:
        raise Salida(str(e) or "no hay base que exportar") from None
    origen = parte = None
    try:
        origen, estado = exportacion.copiar_base(con)
        if estado != "ok":
            t.escribe(f"AVISO: la base falla la comprobación de integridad ({estado[:60]}); se exporta igualmente lo que se pueda")
        fd, parte = tempfile.mkstemp(prefix=".export-", suffix=".part", dir=os.path.dirname(os.path.abspath(destino)))
        with os.fdopen(fd, "wb") as salida, open(origen, "rb") as entrada:
            r = cgx.cifrar_con_frase(entrada, os.path.getsize(origen), salida, frase)
        with open(parte, "rb") as releido:
            if cgx.verificar_con_clave(releido, r["clave"], None, tipo=cgx.TIPO_EXPORT) != r["sha256_claro"]:
                raise cgx.ErrorCgx("lo escrito no coincide con el origen")
        exportacion.reemplazar(parte, destino)
        parte = None
    except cgx.ErrorCgx as e:
        raise Salida(f"no se exportó: {e}") from None
    finally:
        con.close()
        if origen:
            exportacion._borra(origen)
        if parte:
            exportacion._borra(parte)
    t.escribe(f"Exportado y verificado: {destino}")


def _descifrador(ruta_cgx, argv, t):
    """(función descifrar, texto de la huella) según el modo del fichero; la clave o frase se pide aquí, nunca por argumento."""
    with open(ruta_cgx, "rb") as f:
        try:
            modo, tipo, fecha, _k, _s, _e, destinatario, _p, _c = cgx.leer_cabecera(f)
        except cgx.ErrorCgx as e:
            raise Salida(str(e)) from None
    if modo == cgx.MODO_FRASE:
        frase = t.frase("Frase de paso del fichero: ")
        return (lambda e, s: cgx.descifrar_con_frase(e, s, frase, tipo=tipo)), None
    cc = _config_copias()
    ruta_clave = _valor(argv, "--clave-privada")
    de_la_clave = []
    if ruta_clave:
        try:
            privada, de_la_clave, avisos = claves_copias.cargar_paquete(ruta_clave, lambda: t.frase("Frase de la clave privada: "))
        except (cgx.ErrorCgx, OSError) as e:
            raise Salida(f"no se pudo cargar la clave: {e}") from None
        for a in avisos:
            t.escribe(f"AVISO: {a}")
    else:
        try:
            privada = cgx.decodificar_clave("privada", t.frase("Clave privada (CGX-SECRET-KEY-1-…): "))
        except cgx.ErrorCgx as e:
            raise Salida(str(e)) from None
    try:
        firmas = _firmas_conocidas(cc, de_la_clave + [cgx.decodificar_clave("firma_publica", x) for x in _valores(argv, "--firma-publica")])
    except cgx.ErrorCgx as e:
        raise Salida(f"--firma-publica no es válida: {e}") from None

    def descifrar(e, s):
        try:
            cgx.descifrar_con_privada(e, s, privada, firmas, tipo=tipo)
        except cgx.ErrorCgx as err:
            if "firmada" in str(err):
                raise cgx.ErrorCgx("la copia no está firmada por ningún equipo que conozcas: falta la firma pública del equipo que la hizo "
                                   "(cgxs1…, está en tu paquete de restauración); pásala con --firma-publica") from None
            raise
    return descifrar, cgx.huella(destinatario)


def importar(argv, t):
    ruta = _valor(argv, "--importar")
    if not ruta:
        raise Salida("uso: costbar.py --importar RUTA.cgx [--clave-privada RUTA] [--forzar]")
    descifrar, huella = _descifrador(ruta, argv, t)

    def confirma(info):
        fecha = datetime.datetime.fromtimestamp(info["fecha"], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        t.escribe(f"La copia es del {fecha}, trae {info['turnos']} turnos (esquema {info['version']})"
                  + (f", para la clave {huella}" if huella else "") + ".")
        if info["sustituye"]:
            t.escribe("Sustituirá la base actual (antes se guarda una copia de ella).")
        return _confirma_si(t, "¿Restaurar?")
    try:
        r = restauracion.restaurar(ruta, descifrar, confirma, forzar="--forzar" in argv, espera_s=100)
    except restauracion.ErrorRestauracion as e:
        raise Salida(str(e)) from None
    t.escribe(f"Restaurados {r['turnos']} turnos en {r['destino']}." + (f" Copia previa: {r['copia_previa']}" if r["copia_previa"] else ""))


def probar_restauracion(argv, t):
    ruta = _valor(argv, "--probar-restauracion")
    if not ruta:
        raise Salida("uso: costbar.py --probar-restauracion RUTA.cgx [--clave-privada RUTA]")
    descifrar, _h = _descifrador(ruta, argv, t)
    try:
        info = restauracion.probar(ruta, descifrar)
    except restauracion.ErrorRestauracion as e:
        raise Salida(str(e)) from None
    exportacion.registrar_ensayo()
    t.escribe(f"La copia se descifra y es válida: {info['turnos']} turnos, esquema {info['version']}.")


def autoprueba(t):
    """Ida y vuelta completa en un directorio temporal, sin tocar tus datos: para ejecutarla en Windows y pegar la salida."""
    pasos, raiz = [], tempfile.mkdtemp(prefix="cg-autoprueba-")
    datos, copias = rutas.DATOS, rutas.COPIAS
    try:
        rutas.DATOS, rutas.COPIAS = os.path.join(raiz, "datos"), os.path.join(raiz, "datos", "copias")
        paso = lambda nombre, ok: (pasos.append(ok), t.escribe(f"  [{'OK' if ok else 'FALLO'}] {nombre}"))      # noqa: E731
        t.escribe(f"Autoprueba de copias cifradas en {raiz} (Python {sys.version.split()[0]}, {sys.platform})")
        viva = almacen.abrir(os.path.join(raiz, "viva", "consumo.sqlite"))
        q = almacen.identidad("autoprueba")
        almacen.adquirir_escritura(viva, q)
        esp = almacen.espacio(viva, q)
        fid = almacen.fuente(viva, q, esp, "claude", "f")
        almacen.ingerir_turnos(viva, q, esp, fid, [{"clave": f"claude:m{i}", "hora_utc": 1_790_000_000 + i, "agente": "claude",
                                                     "proveedor": "", "modelo": "m", "gi": 1, "go": 2, "cr": 3, "cw": 4,
                                                     "proyecto_raw": "p"} for i in range(500)])
        almacen.soltar_escritura(viva, q)
        paso("base de prueba creada (500 turnos)", True)
        priv, pub = cgx.generar_par()
        fpriv, fpub = cgx.generar_par_firma()
        carpeta = os.path.join(raiz, "cifradas")
        os.makedirs(carpeta)
        r = exportacion.hacer_copia(viva, carpeta, pub, fpriv, fpub)
        paso(f"copia cifrada y verificada por relectura ({r['bytes']} bytes)", True)
        info = restauracion.probar(os.path.join(carpeta, r["archivo"]),
                                   lambda e, s: cgx.descifrar_con_privada(e, s, priv, [fpub]))
        paso(f"ensayo de restauración: {info['turnos']} turnos", info["turnos"] == 500)
        destino = os.path.join(raiz, "restaurada", "consumo.sqlite")
        res = restauracion.restaurar(os.path.join(carpeta, r["archivo"]), lambda e, s: cgx.descifrar_con_privada(e, s, priv, [fpub]),
                                     lambda i: True, destino=destino)
        paso("restauración sobre un destino nuevo", res["turnos"] == 500)
        base_vieja = os.path.join(raiz, "otra", "consumo.sqlite")
        almacen.abrir(base_vieja).close()
        res2 = restauracion.restaurar(os.path.join(carpeta, r["archivo"]), lambda e, s: cgx.descifrar_con_privada(e, s, priv, [fpub]),
                                      lambda i: True, destino=base_vieja, forzar=True)
        paso("restauración sustituyendo una base existente (reemplazo de fichero, -wal/-shm y copia previa)",
             res2["turnos"] == 500 and bool(res2["copia_previa"]))
        frase = "autoprueba-" + secrets.token_hex(4)
        exp = os.path.join(raiz, "export.cgx")
        origen, _estado = exportacion.copiar_base(viva)
        try:
            with open(origen, "rb") as f, open(exp, "wb") as g:
                cgx.cifrar_con_frase(f, os.path.getsize(origen), g, frase, kdf=(14, 8, 1))
        finally:
            exportacion._borra(origen)
        salida = io.BytesIO()
        with open(exp, "rb") as f:
            cgx.descifrar_con_frase(f, salida, frase)
        paso("exportación con frase: ida y vuelta", len(salida.getvalue()) > 1000)
        with open(os.path.join(carpeta, r["archivo"]), "rb") as f:
            malo = bytearray(f.read())
        malo[cgx.TAM_CABECERA + 50] ^= 1
        corrupta = os.path.join(raiz, "corrupta.cgx")
        with open(corrupta, "wb") as f:
            f.write(malo)
        try:
            restauracion.probar(corrupta, lambda e, s: cgx.descifrar_con_privada(e, s, priv, [fpub]))
            paso("una copia manipulada se rechaza", False)
        except restauracion.ErrorRestauracion:
            paso("una copia manipulada se rechaza", True)
        viva.close()
    except Exception as e:                                          # noqa: BLE001
        pasos.append(False)
        t.escribe(f"  [FALLO] excepción inesperada: {type(e).__name__}: {e}")
    finally:
        rutas.DATOS, rutas.COPIAS = datos, copias
        shutil.rmtree(raiz, ignore_errors=True)
    t.escribe("RESULTADO: " + ("todo correcto" if pasos and all(pasos) else "HAY FALLOS: copia esta salida y envíala"))
    return 0 if pasos and all(pasos) else 1


def main(argv=None, t=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    t = t or Terminal()
    try:
        _sin_secretos_en_argv(argv)
        _sin_volcados()
        if "--frase-sugerir" in argv:
            frase_sugerir(t)
        elif "--autoprueba-copias" in argv:
            return autoprueba(t)
        elif "--claves-generar" in argv:
            claves_generar(argv, t)
        elif "--copia-ahora" in argv:
            copia_ahora(t)
        elif "--exportar" in argv:
            exportar(argv, t)
        elif "--importar" in argv:
            importar(argv, t)
        elif "--probar-restauracion" in argv:
            probar_restauracion(argv, t)
        else:
            raise Salida("orden no reconocida")
    except Salida as e:
        t.escribe(f"Error: {e}")
        return 1
    except (OSError, EOFError) as e:
        t.escribe(f"Error: {type(e).__name__}: no se pudo completar la operación ({getattr(e, 'strerror', None) or 'entrada cerrada'})")
        return 1
    except cgx.ErrorCgx as e:
        t.escribe(f"Error: {e}")
        return 1
    return 0
