"""Permisos de lo que guarda la app (config, estado, logs, panel, imagenes del desplegable).

Todo eso es sensible: proyectos y uso, hosts y usuarios de servidores, IPs. En macOS y Linux se trabaja con
umask 0077 (nada nuevo queda legible por otros usuarios), directorio de la instalacion en 0700 y los ficheros
que ya existian con permisos laxos se corrigen al arrancar. Las imagenes del desplegable (con host e IPs) se
purgan al pasar una hora. En Windows el perfil del usuario ya es privado por ACL y chmod no aplica: no se
toca nada (el endurecimiento por ACL va con la app para Windows).
"""
import glob, os, time

from . import rutas

RENDER_CADUCA_S = 3600
FICHEROS = ("config.json", "servidores.json", "state.json", "panel.html", "popover.html", "popover_oscuro.html",
            "popover_claro.png", "popover_oscuro.png")


def _es_instalacion():
    """Solo se cambia el modo del directorio si es la instalacion (~/.cost-guard), nunca un checkout de desarrollo."""
    destino = os.environ.get("COSTGUARD_HOME")
    return (os.path.basename(rutas.RAIZ.rstrip("/")) == ".cost-guard"
            or bool(destino and os.path.realpath(destino) == os.path.realpath(rutas.RAIZ)))


def _modo(ruta, modo):
    try:
        if os.stat(ruta).st_mode & 0o777 != modo:
            os.chmod(ruta, modo)
    except OSError:
        pass


def excluir_datos_sensibles():
    """Una vez al arrancar la barra o el demonio: la base de consumo y los logs (con IPs y nombres) fuera de las copias automáticas del
    sistema. Devuelve cuántas rutas quedaron excluidas."""
    from . import plataforma
    return sum(bool(plataforma.excluir_de_copias_del_sistema(r)) for r in (rutas.DATOS, os.path.dirname(rutas.LOG)))


def endurecer():
    """Llamar al arrancar (barra, CLI, panel). Idempotente y barata."""
    os.umask(0o077)
    if os.name == "nt":
        purgar_render()
        return
    base = os.path.dirname(rutas.LOG)                   # .../logs
    if _es_instalacion():
        _modo(rutas.RAIZ, 0o700)
    if os.path.isdir(base):
        _modo(base, 0o700)
    for nombre in FICHEROS:
        _modo(os.path.join(rutas.RAIZ, nombre), 0o600)
    for patron in ("*.log", "*.err", "*.json", "*.lock"):
        for f in glob.glob(os.path.join(base, patron)):
            _modo(f, 0o600)
    for f in glob.glob(os.path.join(rutas.RAIZ, "config.json.bak.*")) + glob.glob(os.path.join(rutas.RAIZ, "*.lock")):
        _modo(f, 0o600)
    purgar_render()


def purgar_render():
    """Las imagenes del desplegable llevan host e IPs: privadas y fuera pasada una hora. Se llama al arrancar y cada hora (en Windows solo
    borra: los permisos van por la carpeta de datos del usuario)."""
    render = os.path.join(os.path.dirname(rutas.LOG), "render")
    if not os.path.isdir(render):
        return
    if os.name != "nt":
        _modo(render, 0o700)
    ahora = time.time()
    for f in glob.glob(os.path.join(render, "*")):
        try:
            if ahora - os.stat(f).st_mtime > RENDER_CADUCA_S:
                os.remove(f)
            elif os.name != "nt":
                _modo(f, 0o600)
        except OSError:
            pass
