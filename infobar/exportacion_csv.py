"""Exportación a CSV del histórico. Lo que sale solo puede ser lo que `historico.historico()` devuelve: tokens y turnos por periodo y
grupo, sin hosts, IPs ni rutas completas. Defensas: cada celda de texto sale sin caracteres de control ni invisibles y con las
fórmulas neutralizadas; las cifras son enteros; el nombre del fichero lo genera la aplicación; la carpeta es privada; el CSV en claro
se avisa como tal y el cifrado por frase lleva un tipo de contenido propio (nunca se confunde con una copia o una base)."""
import csv
import io
import json
import os
import re
import secrets
import time
import unicodedata

from . import cgx, exportacion, rutas

MAX_FILAS = 100_000
CABECERA = ("inicio_periodo", "paso", "agrupacion", "grupo", "tokens", "turnos")
SEPARADOR = ";"                    # Excel en español abre así los CSV; con todo entrecomillado cualquier otro lector también los lee
_FORMULA = ("=", "+", "-", "@")
_INVISIBLES = re.compile("[­؜᠎​-‏ -‮⁠-⁯﻿￹-￻]")


class ErrorCsv(ValueError):
    """Se puede enseñar tal cual."""


def neutralizar(texto):
    """Texto de una celda: sin controles ni invisibles (incluidos los de dirección de texto), con espacios en lugar de saltos de línea o
    tabuladores, y con una comilla simple delante si una hoja de cálculo lo tomaría por fórmula."""
    t = _INVISIBLES.sub("", unicodedata.normalize("NFC", str(texto)))
    t = "".join(" " if c in "\t\r\n\v\f\x85" else ("" if unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co") else c) for c in t)
    if unicodedata.normalize("NFKC", t.lstrip())[:1] in _FORMULA:       # también las variantes de ancho completo
        t = "'" + t
    return t


def _entero(v):
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ErrorCsv("el histórico trae una cifra que no es un entero")
    return v


def filas(r):
    """Filas (periodo, paso, agrupación, grupo, tokens, turnos) de un resultado de `historico()`; omite los huecos y los ceros."""
    salida = []
    for s in r["series"]:
        for i, c in enumerate(r["cubos"]):
            tok, turnos = s["tokens"][i], s["turnos"][i]
            if tok is None or (not tok and not turnos):
                continue
            salida.append((c, r["paso"], r["agrupa"], s["nombre"], _entero(tok), _entero(turnos)))
            if len(salida) > MAX_FILAS:
                raise ErrorCsv(f"el CSV tendría más de {MAX_FILAS} filas: acorta el rango o usa un paso mayor")
    return salida


def construir(r):
    """El CSV como bytes: UTF-8 con BOM, `;`, todo entrecomillado, fin de línea CRLF."""
    sal = io.StringIO(newline="")
    w = csv.writer(sal, delimiter=SEPARADOR, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerow(CABECERA)
    for f in filas(r):
        w.writerow([neutralizar(x) if isinstance(x, str) else x for x in f])
    return sal.getvalue().encode("utf-8-sig")


def nombre(r, ahora=None, cifrado=False):
    """Nombre generado por la aplicación: solo caracteres de una lista cerrada."""
    t = time.strftime("%Y%m%d-%H%M%S", time.localtime(ahora if ahora is not None else time.time()))
    return f"consumo-{r['desde']}-{r['hasta']}-{r['paso']}-{r['agrupa']}-{t}-{secrets.token_hex(2)}.csv" + (".cgx" if cifrado else "")


def carpeta():
    d = os.path.join(rutas.DATOS, "exportaciones")
    if os.path.islink(d):
        raise ErrorCsv("la carpeta de exportaciones es un enlace simbólico: no se usa")
    os.makedirs(d, mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(d, 0o700)
    return d


def auditar(r, filas_n, cifrado, origen):
    """Una línea por exportación: cuándo, qué rango y cuántas filas; nunca contenido ni nombres de proyecto."""
    linea = json.dumps({"ts": int(time.time()), "desde": r["desde"], "hasta": r["hasta"], "paso": r["paso"], "agrupa": r["agrupa"],
                        "filas": filas_n, "cifrado": bool(cifrado), "origen": origen})
    os.makedirs(rutas.DATOS, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(rutas.DATOS, "exportaciones.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def _abre_exclusivo(ruta):
    banderas = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    return os.fdopen(os.open(ruta, banderas, 0o600), "wb")


def escribir_claro(r, ahora=None):
    """CSV sin cifrar en la carpeta privada de exportaciones. Devuelve (ruta, filas)."""
    datos = construir(r)
    ruta = os.path.join(carpeta(), nombre(r, ahora))
    with _abre_exclusivo(ruta) as f:
        f.write(datos)
    n = datos.count(b"\r\n") - 1
    auditar(r, n, False, "cli")
    return ruta, n


def escribir_cifrado(r, frase, ahora=None):
    """CSV cifrado con una frase (`.csv.cgx`, tipo propio), verificado por relectura antes de dejarlo en su sitio."""
    datos = construir(r)
    destino = os.path.join(carpeta(), nombre(r, ahora, cifrado=True))
    parte = destino + ".part"
    try:
        with _abre_exclusivo(parte) as salida:
            res = cgx.cifrar_con_frase(io.BytesIO(datos), len(datos), salida, frase, tipo=cgx.TIPO_CSV)
        with open(parte, "rb") as releido:
            if cgx.verificar_con_clave(releido, res["clave"], None, tipo=cgx.TIPO_CSV) != res["sha256_claro"]:
                raise ErrorCsv("lo escrito no coincide con el origen")
        exportacion.reemplazar(parte, destino)
        parte = None
    finally:
        if parte:
            exportacion._borra(parte)
    n = datos.count(b"\r\n") - 1
    auditar(r, n, True, "cli")
    return destino, n


def descifrar(ruta_cgx, frase, ahora=None):
    """Abre un `.csv.cgx` con su frase y deja el CSV en la carpeta de exportaciones. Si algo falla no queda nada a medias."""
    destino = os.path.join(carpeta(), "descifrado-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(ahora if ahora is not None else time.time()))
                           + "-" + secrets.token_hex(2) + ".csv")
    parte = destino + ".part"
    try:
        with open(ruta_cgx, "rb") as entrada, _abre_exclusivo(parte) as salida:
            cgx.descifrar_con_frase(entrada, salida, frase, tipo=cgx.TIPO_CSV)
        exportacion.reemplazar(parte, destino)
        parte = None
    finally:
        if parte:
            exportacion._borra(parte)
    return destino
