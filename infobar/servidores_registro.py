"""Pegamento entre el ciclo de servidores y su historial: ids estables, políticas por servidor y el registrador que usa el hilo trabajador.

El registrador se llama DESPUÉS de guardar la caché y fuera de su cerrojo; si la base falla, el ciclo sigue igual: se apunta un solo `log()`
sin valores y no se vuelve a intentar hasta pasada una hora (la base rota no se martillea cada 15 s). El mantenimiento (purga, consolidación)
corre al primer ciclo y después cada hora."""
import re
import time
import uuid

from . import servidores_historico as sh
from .config_y_estado import cfg, log, modificar_servidores

ID_SERVIDOR = re.compile(r"^[0-9a-f]{32}$")
RETROCESO_S = 3600
MANTENER_CADA_S = 3600


def nuevo_id():
    return uuid.uuid4().hex


def asegurar_ids(lista, previos=()):
    """Lista de servidores con `id` válido en todos. Conserva el que traen; si falta, hereda el del servidor del mismo nombre en `previos`
    (la interfaz puede haberlo perdido) y, si no hay, genera uno nuevo. Un id repetido se regenera para el segundo. Devuelve (lista, cambió)."""
    por_nombre = {p.get("nombre"): p.get("id") for p in previos or [] if isinstance(p, dict) and ID_SERVIDOR.fullmatch(str(p.get("id") or ""))}
    usados, salida, cambio = set(), [], False
    for s in lista or []:
        if not isinstance(s, dict):
            salida.append(s)
            continue
        s = dict(s)
        sid = s.get("id")
        if not (isinstance(sid, str) and ID_SERVIDOR.fullmatch(sid)) or sid in usados:
            heredado = por_nombre.get(s.get("nombre"))
            s["id"] = heredado if heredado and heredado not in usados else nuevo_id()
            cambio = True
        usados.add(s["id"])
        salida.append(s)
    return salida, cambio


def politicas(conf=None):
    """{id: {"guardar_ips": bool, "retencion_dias": int}} de los servidores de la config (override por servidor sobre el valor global)."""
    conf = conf or cfg()
    glob_guardar = conf.get("guardar_ips") is not False
    glob_ret = sh.retencion_dias(conf.get("retencion_ips_dias"))
    out = {}
    for s in conf.get("servidores") or []:
        if isinstance(s, dict) and ID_SERVIDOR.fullmatch(str(s.get("id") or "")):
            out[s["id"]] = {"guardar_ips": (s["guardar_ips"] is not False) if "guardar_ips" in s else glob_guardar,
                            "retencion_dias": sh.retencion_dias(s.get("retencion_ips_dias", glob_ret))}
    return out


class Historial:
    """Registrador para `servidores_ciclo.ciclo(historial=...)`. Una instancia por trabajador; solo se usa desde su hilo."""
    def __init__(self, reloj=time.time):
        self.reloj = reloj
        self.no_antes = 0
        self.ultimo_mantenimiento = 0
        self.firma = None

    def preparar(self):
        """Da id a los servidores guardados que aún no lo tienen (instalaciones anteriores). Nunca lanza."""
        try:
            cfg()                                           # migra la lista a servidores.json si aún estaba en config.json
            modificar_servidores(lambda lista: asegurar_ids(lista, lista)[0])      # sobre la lista ACTUAL y bajo el cerrojo: no pisa un alta o un guardado de Ajustes
        except Exception as e:
            log(f"servidores: no se pudieron asignar ids ({type(e).__name__})")

    def __call__(self, servidores, salida, nuevos, ahora):
        if self.reloj() < self.no_antes:
            return
        conf = cfg()
        pol = politicas(conf)
        conocidas = conf.get("ips_conocidas") or []
        firma = repr(sorted(pol.items()))
        if firma != self.firma:                 # quitar un servidor o apagar guardar_ips borra las IPs ya, no a la próxima hora
            self.firma, self.ultimo_mantenimiento = firma, 0
        try:
            con = sh.abrir()
            try:
                for s in servidores:
                    if s.get("nombre") in nuevos and s.get("id") in pol:
                        sh.registrar(con, s, salida.get(s["nombre"]) or {}, ahora, conocidas, pol[s["id"]]["guardar_ips"])
                if self.reloj() - self.ultimo_mantenimiento >= MANTENER_CADA_S:
                    sh.mantener(con, pol, self.reloj())
                    self.ultimo_mantenimiento = self.reloj()
            finally:
                con.close()
        except Exception as e:
            # un solo aviso por fallo y sin valores: ni filas ni str(e), que en un IntegrityError las repite
            self.no_antes = self.reloj() + RETROCESO_S
            log(f"historial de servidores: {type(e).__name__}; no se reintenta hasta dentro de una hora")
