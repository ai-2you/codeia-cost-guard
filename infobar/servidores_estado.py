"""Colores de cada tarjeta, estado global del servidor y revision de bastionado ("que falta").

verde = bien · ambar = mirar · rojo = actuar · gris = sin dato (o dato viejo).
"""
from .servidores_externo import PUERTOS_PUBLICOS

ORDEN = {"gris": 0, "verde": 1, "ambar": 2, "rojo": 3}


def peor(*estados):
    estados = [e for e in estados if e]
    return max(estados, key=lambda e: ORDEN[e]) if estados else "gris"


def _pct(parte, total):
    return round(100.0 * parte / total, 1) if total else 0.0


def _umbral(pct, ambar, rojo):
    return "rojo" if pct >= rojo else ("ambar" if pct >= ambar else "verde")


def _ip_conocida(desde, conocidas):
    """`who` da la IP (o el nombre) de origen tal cual; se compara con la lista de config."""
    return (desde or "").strip() in set(conocidas)


def tarjetas(d, webs, expuestos, conocidas=(), permitidos=()):
    """{tarjeta: {"estado", ...}} a partir de los datos leidos por SSH y desde el Mac."""
    t = {}
    if "cpu" in d:
        t["cpu"] = {"estado": _umbral(d["cpu"]["pct"], 80, 95)}
    if "memoria" in d:
        t["memoria"] = {"estado": _umbral(_pct(d["memoria"]["usada"], d["memoria"]["total"]), 85, 95)}
    if "disco" in d:
        t["disco"] = {"estado": _umbral(_pct(d["disco"]["usado"], d["disco"]["total"]), 85, 95)}
    if webs:
        malas = [w for w in webs if w["error"] or (w["codigo"] or 0) >= 500]
        lentas = [w for w in webs if (w["ms"] or 0) > 1500]
        dias = [w["cert_dias"] for w in webs if isinstance(w.get("cert_dias"), int) and not isinstance(w.get("cert_dias"), bool)]
        caducan = min(dias) if dias else None                 # el certificado que antes caduca (el de la hoja que sirve cada web)
        t["webs"] = {"estado": "rojo" if (malas or (caducan is not None and caducan < 3)) else
                     ("ambar" if (lentas or (caducan is not None and caducan < 14)) else "verde"), "cert_dias": caducan}
    if "sesiones" in d:
        extranas = [s for s in d["sesiones"] if s["desde"] and not _ip_conocida(s["desde"], conocidas)]
        t["sesiones"] = {"estado": "ambar" if (extranas and conocidas) else "verde"}
    if "intentos" in d:
        it = d["intentos"]
        media_hora = it["total"] / 24.0
        # un ataque es un pico: la ultima hora muy por encima de la media del dia
        t["intentos"] = {"estado": "ambar" if it["hora"] > max(30, 3 * media_hora) else "verde"}
    if "fail2ban" in d:
        t["bloqueadas"] = {"estado": "verde" if d["fail2ban"].get("instalado") else "ambar"}
    if expuestos is not None:
        raros = [p for p in expuestos if p not in PUERTOS_PUBLICOS | set(permitidos)]
        t["puertos"] = {"estado": "rojo" if raros else "verde", "raros": raros}
    if "actualizaciones" in d:
        a = d["actualizaciones"]
        seg = int((d.get("bastionado") or {}).get("parches_seguridad") or 0)
        t["mantenimiento"] = {"estado": "ambar" if (a["reinicio"] or seg) else "verde"}
    if "servicios" in d:
        caidos = [s["nombre"] for s in d["servicios"] if not s["ok"]]
        t["servicios"] = {"estado": "rojo" if caidos else "verde", "caidos": caidos}
    return t


def bastionado(d, expuestos, permitidos=()):
    """Lista de comprobaciones {id, ok (True/False/None), texto, arreglo}. None = no se pudo mirar."""
    b = d.get("bastionado") or {}
    root = d.get("con_root")
    c = []

    def mete(id_, ok, texto, arreglo):
        c.append({"id": id_, "ok": ok, "texto": texto, "arreglo": "" if ok else arreglo})

    def sshd(k):
        return b.get(f"sshd_{k}") if root else None

    v = sshd("passwordauthentication")
    mete("ssh_contrasena", None if v is None else v == "no", "SSH sin contraseñas",
         "echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/00-sin-contrasena.conf && sudo sshd -t && sudo systemctl reload ssh  (en sshd gana el primer valor: 00 va antes que 50-cloud-init)")
    v = sshd("kbdinteractiveauthentication")
    mete("ssh_teclado", None if v is None else v == "no", "SSH sin login interactivo",
         "KbdInteractiveAuthentication no en sshd_config")
    v = sshd("permitrootlogin")
    mete("ssh_root", None if v is None else v in ("no", "prohibit-password", "without-password"),
         "root no entra con contraseña", "PermitRootLogin prohibit-password (o no)")
    v = sshd("permitemptypasswords")
    mete("ssh_vacias", None if v is None else v == "no", "sin contraseñas vacías", "PermitEmptyPasswords no")
    f2b = d.get("fail2ban") or {}
    activo = b.get("fail2ban_activo")
    mete("fail2ban", None if not root else (f2b.get("instalado") and activo == "active"),
         "fail2ban vigilando SSH", "apt install fail2ban (el jail sshd viene activo en Ubuntu)")
    pol = b.get("input_politica")
    ufw = b.get("ufw")
    mete("cortafuegos", None if not root else (ufw == "active" or pol in ("DROP", "REJECT")),
         "cortafuegos activo", "ufw default deny incoming; ufw allow 22,80,443/tcp; ufw enable "
         "(o el cortafuegos del proveedor). Ojo: docker publica puertos saltándose ufw")
    if expuestos is not None:
        raros = [p for p in expuestos if p not in PUERTOS_PUBLICOS | set(permitidos)]
        mete("puertos", not raros, "solo 22/80/443 abiertos a Internet",
             f"cerrar {', '.join(map(str, raros))}: sudo bash cerrar-puerto-docker.sh <puerto> "
             "(infobar/remoto; docker se salta ufw) o en el cortafuegos del proveedor")
    if "auto_upgrades" in b:
        mete("auto_updates", b.get("auto_upgrades") == "si" and b.get("unattended_instalado") == "si",
             "parches de seguridad automáticos", "apt install unattended-upgrades && dpkg-reconfigure -plow unattended-upgrades")
    if "parches_seguridad" in b:
        n = int(b.get("parches_seguridad") or 0)
        mete("parches", n == 0, "sin parches de seguridad pendientes", f"{n} pendientes: apt upgrade")
    a = d.get("actualizaciones") or {}
    if "reinicio" in a:
        mete("reinicio", not a["reinicio"], "sin reinicio pendiente", "reiniciar para cargar el kernel y las librerías nuevas")
    if "sudo_nopasswd" in b:
        ficheros = (b.get("sudo_nopasswd_ficheros") or "").strip(",").replace(",", ", ")
        mete("sudo", b.get("sudo_nopasswd") == "0", "sudo pide contraseña",
             f"regla NOPASSWD en {ficheros or '/etc/sudoers.d/'} (míralo: sudo grep -rn NOPASSWD /etc/sudoers.d/). "
             "Si es de otro usuario (p. ej. ubuntu de cloud-init): sudo rm ese fichero. Si es la tuya: "
             "antes sudo passwd tu-usuario y prueba con su tu-usuario -c true")
    if "uid0_extra" in b:
        mete("uid0", b.get("uid0_extra") == "0", "solo root con UID 0", "revisar /etc/passwd: hay cuentas con UID 0 además de root")
    if "hora_sincronizada" in b:
        recien = (d.get("uptime_s") or 0) < 900          # tras arrancar, NTP tarda unos minutos
        ok = b.get("hora_sincronizada") == "yes"
        mete("hora", True if ok else (None if recien else False), "hora sincronizada (NTP)",
             "sudo timedatectl set-ntp true")
    return c


def resumen_bastionado(checks):
    hechos = [x for x in checks if x["ok"] is True]
    sabidos = [x for x in checks if x["ok"] is not None]
    falta = [x for x in checks if x["ok"] is False]
    criticos = {"ssh_contrasena", "ssh_root", "ssh_vacias", "cortafuegos", "puertos"}
    estado = ("rojo" if any(x["id"] in criticos for x in falta) else
              "ambar" if falta else ("verde" if sabidos else "gris"))
    return {"hechos": len(hechos), "total": len(sabidos), "estado": estado}
