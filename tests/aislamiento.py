"""Aisla los tests de la maquina: HOME vacio (sin ~/.claude/settings.json ni logs reales) y todas
las rutas de datos (config.json, state.json, logs) en una carpeta temporal. Importar ANTES que
costbar: HOME se fija antes de cargarlo porque los lectores lo leen al importarse."""
import os
import sys
import tempfile

HOME_FALSO = tempfile.mkdtemp(prefix="infobar-tests-")
os.environ["HOME"] = HOME_FALSO
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infobar import rutas  # noqa: E402

rutas.CONFIG = os.path.join(HOME_FALSO, "config.json")
rutas.STATE = os.path.join(HOME_FALSO, "state.json")
rutas.LOG = os.path.join(HOME_FALSO, "logs", "costbar.log")
rutas.NAN_USO_CACHE = os.path.join(HOME_FALSO, "logs", "nan_uso.json")
rutas.UI_ESTADO = os.path.join(HOME_FALSO, "logs", "ui_estado.json")
rutas.SERVIDORES = os.path.join(HOME_FALSO, "logs", "servidores.json")

# ni el Llavero real ni la API de NaN: sin clave, la cuota cae a los logs (sin red)
os.environ.pop("NAN_API_KEY", None)
from infobar import claves  # noqa: E402

claves.leer_clave = lambda cuenta: ""
