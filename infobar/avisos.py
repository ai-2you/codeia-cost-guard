"""Qué avisos de presupuesto ya se dieron. Vive en `DATOS/avisos.json`, no en `state.json`: ese lo vacían `/guardar` y cada subida de
versión del estado, y los avisos se repetirían justo al editar un presupuesto. Solo escribe el hilo trabajador de la barra; el resto
(panel, Ajustes) únicamente calcula estados y no toca este fichero."""
import json
import os
import tempfile

from . import rutas

NIVELES = (80, 100)
MAX_ENTRADAS = 400


def ruta():
    return os.path.join(rutas.DATOS, "avisos.json")


def leer():
    try:
        with open(ruta(), encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(datos, dict):
        return {}
    return {k: v for k, v in datos.items() if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("periodo"), str)
            and v.get("avisado") in (0,) + NIVELES}


def _guardar(datos):
    os.makedirs(rutas.DATOS, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".avisos-", dir=rutas.DATOS)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, ruta())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def tomar_pendientes(estados):
    """Los estados que acaban de cruzar un umbral que aún no se había avisado en su periodo; los deja anotados. Un umbral se avisa una
    vez por periodo: subir el presupuesto o volver a cruzar el mismo no repite nada, y pasar de 80 a 100 sí avisa. Se olvidan las entradas
    de periodos que ya no están en curso."""
    previo = leer()
    vigentes = {e["periodo_clave"] for e in estados}
    datos = {k: v for k, v in previo.items() if v["periodo"] in vigentes}
    pendientes = []
    for e in estados:
        if e["nivel"] <= 0:
            continue
        k = e["clave"]
        if datos.get(k, {}).get("periodo") == e["periodo_clave"] and datos[k]["avisado"] >= e["nivel"]:
            continue
        datos[k] = {"periodo": e["periodo_clave"], "avisado": e["nivel"]}
        pendientes.append(e)
    if len(datos) > MAX_ENTRADAS:
        datos = dict(sorted(datos.items())[-MAX_ENTRADAS:])
    if datos != previo:
        _guardar(datos)
    return pendientes
