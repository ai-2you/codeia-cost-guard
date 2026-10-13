"""Rutas de datos de infobar, en un solo sitio.

RAIZ es la carpeta del proyecto (la que tiene costbar.py, config.json, state.json y logs/), no la de
este paquete. Los demas modulos leen estas rutas como atributos (`rutas.CONFIG`), asi que cambiarlas
aqui (p. ej. en los tests) llega a todos. No importa nada del paquete.
"""
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
STATE = f"{RAIZ}/state.json"
CONFIG = f"{RAIZ}/config.json"
LOG = f"{RAIZ}/logs/costbar.log"
NAN_USO_CACHE = f"{RAIZ}/logs/nan_uso.json"
UI_ESTADO = f"{RAIZ}/logs/ui_estado.json"
SERVIDORES = f"{RAIZ}/logs/servidores.json"
