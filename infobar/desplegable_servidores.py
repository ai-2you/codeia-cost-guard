"""Pestana Servidores del desplegable (como la captura de referencia): estado de UN servidor, el
elegido en el selector, con la revision de bastionado.

Lee solo la cache (logs/servidores.json): pintar nunca espera a la red.
"""
import time

from .desplegable_comun import cabecera, esc, icono, pagina, pestanas, pie, tarjeta
from .servidores_ciclo import estado_visible, intervalos, peor_estado

MAX_PILDORAS = 6
BOTONES = [("terminal", "Terminal"), ("caja", "Panel"), ("brujula", "Abrir")]


def _gb(b):
    g = (b or 0) / 1e9
    return f"{g:.1f}".rstrip("0").rstrip(".").replace(".", ",") if g < 10 else f"{g:.0f}"


def _hora(ts):
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "—"


def _hace(ts, ahora):
    if not ts:
        return "—"
    m = int((ahora - ts) / 60)
    return "ahora" if m < 1 else (f"hace {m} min" if m < 60 else f"hace {m // 60} h")


def _corto(servicio):
    """tienda-production_tienda-app -> tienda-app (Swarm antepone el proyecto con '_')."""
    return servicio.split("_", 1)[1] if "_" in servicio.strip("_") else servicio


def _estado(t, clave):
    return (t.get(clave) or {}).get("estado") or "gris"


def _cpu_texto(pct):
    return "Tranquilo" if pct < 30 else ("Con trabajo" if pct < 80 else "Al limite")


def cuerpo_servidor(nombre, e, servidor_cfg, ahora):
    """Cuerpo de la pestana para un servidor (e = su entrada en la cache)."""
    d = (e or {}).get("datos") or {}
    t = (e or {}).get("tarjetas") or {}
    h = []
    if (e or {}).get("error"):
        h.append(f'<div class="aviso">Sin conexión: {esc(e["error"])} · último dato {esc(_hace(e.get("ok_en"), ahora))}</div>')
    if not d:
        if not e:
            h.append('<div class="aviso" style="background:var(--tarjeta);border-color:var(--borde)">'
                     'Todavía sin datos: la primera lectura llega en unos segundos.</div>')
        return "".join(h)

    webs = (e.get("webs") or [])
    w0 = webs[0] if webs else None
    hw = [tarjeta("web", "Webs", f'{w0["ms"]} ms' if w0 and w0.get("ms") is not None else "—",
                  " · ".join(w["url"].split("//")[-1].strip("/") for w in webs) or "sin webs",
                  _estado(t, "webs"))]
    cpu = d.get("cpu") or {}
    hw.append(tarjeta("cpu", "Procesador", f'{cpu.get("pct", 0):.0f} %',
                      f'{_cpu_texto(cpu.get("pct", 0))} · {cpu.get("nucleos", "?")} núcleos', _estado(t, "cpu")))
    m = d.get("memoria") or {}
    hw.append(tarjeta("memoria", "Memoria", f'{_gb(m.get("usada"))} / {_gb(m.get("total"))} GB',
                      "Va holgada" if _estado(t, "memoria") == "verde" else "Va justa", _estado(t, "memoria")))
    k = d.get("disco") or {}
    hw.append(tarjeta("disco", "Disco", f'{_gb(k.get("usado"))} / {_gb(k.get("total"))} GB',
                      "Espacio de sobra" if _estado(t, "disco") == "verde" else "Se está llenando", _estado(t, "disco")))
    h.append(f'<div class="rejilla" style="margin-top:8px">{"".join(hw)}</div>')

    serv = d.get("servicios") or []
    if serv:
        caidos = [s for s in serv if not s["ok"]]
        orden = caidos + [s for s in serv if s["ok"]]
        pild = "".join(f'<div class="pil"><i class="p {"verde" if s["ok"] else "rojo"}"></i><span>{esc(_corto(s["nombre"]))}</span></div>'
                       for s in orden[:MAX_PILDORAS])
        extra = f'<span class="nota">+{len(serv) - MAX_PILDORAS} más</span>' if len(serv) > MAX_PILDORAS else ""
        h.append(f'<div class="sec"><span>Servicios</span>{extra}</div><div class="pildoras">{pild}</div>')

    ses = d.get("sesiones") or []
    it = d.get("intentos") or {}
    f2b = d.get("fail2ban") or {}
    exp = e.get("expuestos")
    res = e.get("bastionado_resumen") or {}
    seg = [tarjeta("persona", "Dentro ahora", "Nadie" if not ses else f"{len(ses)} sesión" + ("es" if len(ses) > 1 else ""),
                   "Sin sesiones SSH" if not ses else ", ".join(s["usuario"] for s in ses[:3]), _estado(t, "sesiones")),
           tarjeta("mano", "Intentos 1 h", f'{it.get("hora", 0):,}'.replace(",", "."),
                   f'{it.get("total", 0):,} en 24 h'.replace(",", "."), _estado(t, "intentos")),
           tarjeta("prohibido", "Bloqueadas", str(f2b.get("ahora", "—")) if f2b.get("instalado") else "—",
                   f'{f2b.get("total", 0)} en total (fail2ban)' if f2b.get("instalado") else "sin fail2ban",
                   _estado(t, "bloqueadas")),
           tarjeta("puerta", "Puertas abiertas", "·".join(map(str, exp)) if exp else "—",
                   "abiertas a Internet" if not (t.get("puertos") or {}).get("raros") else
                   f'¡{", ".join(map(str, t["puertos"]["raros"]))} expuesto!', _estado(t, "puertos")),
           tarjeta("candado", "Protección", f'{res.get("hechos", 0)} de {res.get("total", 0)}' if res.get("total") else "—",
                   "ajustes de seguridad" if res.get("estado") != "verde" else "todo en su sitio", res.get("estado") or "gris")]
    a = d.get("actualizaciones") or {}
    seg.append(tarjeta("llave", "Mantenimiento", "Al día" if not a.get("pendientes") else f'{a["pendientes"]} paquetes',
                       "reinicio pendiente" if a.get("reinicio") else "Paquetes del sistema", _estado(t, "mantenimiento")))
    h.append(f'<div class="sec"><span>Seguridad</span></div><div class="rejilla">{"".join(seg)}</div>')

    falta = [c for c in (e.get("bastionado") or []) if c["ok"] is False]
    if falta:
        filas = "".join(f'<div class="f falta"><div><div class="t">{esc(c["texto"])}</div>'
                        f'<div class="a">{esc(c["arreglo"][:240])}</div></div></div>' for c in falta[:3])
        h.append(f'<div class="sec"><span>Qué falta para protegerlo</span><span class="nota">{len(falta)}</span></div>'
                 f'<div class="lista">{filas}</div>')

    top = it.get("top") or []
    if top and it.get("total"):
        filas = "".join(f'<div class="f mono"><span>{esc(ip)}</span><span>{int(n)}</span></div>' for ip, n in top)
        h.append(f'<div class="sec"><span>Quién más intenta entrar (24 h)</span></div><div class="lista">{filas}</div>')
    return "".join(h)


def documento(nombre, cache, servidores_cfg, oscuro, ahora=None):
    """HTML de la pestana Servidores con `nombre` seleccionado."""
    ahora = ahora or time.time()
    entradas = (cache or {}).get("servidores") or {}
    normal, _ = intervalos()
    global_ = peor_estado(cache, ahora) if entradas else "gris"
    n_mal = sum(1 for e in entradas.values() if estado_visible(e, ahora, normal) in ("ambar", "rojo"))
    detalle = None if not entradas else (None if global_ == "verde" else f"{n_mal} con avisos de {len(entradas)}")
    h = [cabecera("Mis servidores", global_, detalle), pestanas("servidores")]
    if not servidores_cfg:
        h.append('<div class="aviso" style="background:var(--tarjeta);border-color:var(--borde);margin-top:12px">'
                 'Aún no hay servidores. Da de alta uno con <b>infobar/remoto/alta-servidor.sh</b>.</div>')
        h.append(pie([], "—"))
        return pagina("".join(h), oscuro)
    e = entradas.get(nombre)
    est = estado_visible(e, ahora, normal)
    cfg_s = next((s for s in servidores_cfg if s["nombre"] == nombre), {})
    host = (e or {}).get("host") or cfg_s.get("ssh", "").split("@")[-1]
    h.append(f'<div class="selector"><i class="p {est}"></i><span>Servidor ·</span><b>{esc(nombre)}</b>'
             f'<span class="flecha">{icono("flecha")}</span><span class="ip">{esc(host)}</span></div>')
    h.append(cuerpo_servidor(nombre, e, next((s for s in servidores_cfg if s["nombre"] == nombre), {}), ahora))
    h.append(pie(BOTONES, f'Medido a las {_hora((e or {}).get("ok_en"))}'))
    return pagina("".join(h), oscuro)
