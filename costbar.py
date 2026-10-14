#!/usr/bin/env python3
"""CostBar - consumo de tokens de la flota de agentes, en la barra de menus del Mac.

Lee los logs de Claude Code / Codex / OpenCode / pi (agent) y muestra, en primer plano, **tokens**: entrada,
salida, cache leida y cache escrita, mas los turnos, la **ventana de 5 horas** (la que se agota en
las suscripciones) y la **semana**. Los dolares quedan solo como referencia (equivalente API),
porque con suscripcion no se paga por token.

Desduplica los turnos copiados por los forks de Claude Code (el sesgo que inflaba ~15x el panel de
Orca). Cuenta los tokens de TODOS los modelos, tengan tarifa conocida o no.

Uso:
    ./venv/bin/python costbar.py --print      # imprime el informe y sale (sin GUI)
    ./venv/bin/python costbar.py              # icono en la barra de menus
    python3 costbar.py --clave nan            # guarda una clave en el Llavero (sin eco)
"""
import json, os, subprocess, sys, time  # noqa: F401  (time: popover.py usa costbar.time)

# El motor vive en el paquete infobar/; este fichero es la entrada (CLI) y la fachada que
# reexporta lo que usan barra.py, popover.py, panel.py, analizar_gasto.py y los tests.
from infobar import rutas  # noqa: F401
from infobar.agregacion import DIAS, VENTANA_H, agregar, escanear, informe, suma, vacio  # noqa: F401
from infobar.claves import guardar_clave, leer_clave  # noqa: F401
from infobar.config_y_estado import (VERSION_ESTADO, cfg, guardar_config, guardar_estado,  # noqa: F401
                                     guardar_ui, leer_estado, leer_ui, log)
from infobar.cuota_nan import clave_nan, uso_nan  # noqa: F401
from infobar.formato_e_icono import barra, color_magnitud, fila, fmt_tok, icono_png, informa  # noqa: F401
from infobar.lectores_logs import a_local, leer_bases, parsear, parsear_pi  # noqa: F401
from infobar.planes_y_tarifas import (CLIFF, TARIFAS, corto, familia_de, proveedor_de,  # noqa: F401
                                      proveedores_de, tarifa)
from infobar.permisos import endurecer  # noqa: F401
from infobar.servidor_local import PUERTO, servir, url_panel  # noqa: F401

AQUI = rutas.RAIZ


def ingerir_cli():
    """python3 costbar.py --ingerir [--una]: ingiere los logs a la base (sin la barra: Windows, servidores, cron).
    Sin --una se queda en bucle. Si la barra ya es la escritora, espera su turno sin escribir."""
    from infobar import almacen, ingestor
    ing = ingestor.Ingestor(rol="cli")
    try:
        while True:
            try:
                r = ing.vuelta()
            except almacen.SinEscritura:
                print("ingesta: otro proceso es ahora el escritor; se espera su turno", flush=True)
                time.sleep(ingestor.PAUSA_AL_DIA_S)
                continue
            print(f"ingesta: escritor={r['escritor']} nuevos={r['nuevos']} subidos={r['subidos']} anomalias={r['anomalias']} "
                  f"ficheros={r['ficheros']} completo={r['completo']} ({r['s']} s)", flush=True)
            if "--una" in sys.argv and (r["completo"] or not r["escritor"]):
                return
            time.sleep(ingestor.PAUSA_PENDIENTE_S if (r["escritor"] and not r["completo"]) else ingestor.PAUSA_AL_DIA_S)
    except KeyboardInterrupt:
        pass
    finally:
        ing.cierra()


def main():
    endurecer()
    if "--ingerir" in sys.argv:
        return ingerir_cli()
    if "--clave" in sys.argv:
        # python3 costbar.py --clave nan | dokploy:<host> | easypanel:<host>  -> la guarda en el Llavero
        i = sys.argv.index("--clave")
        cuenta = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
        try:
            guardar_clave(cuenta)
            print(f"Guardada en el Llavero (servicio infobar, cuenta {cuenta}).")
        except Exception as e:
            print(f"No se guardo: {e}")
            sys.exit(1)
        return
    if "--proveedores" in sys.argv:
        r = informe()
        print("Modelos usados en los ultimos 7 dias, agrupados por proveedor:")
        for n, x in r["proveedores"].items():
            print(f"  {n:<14} {', '.join(x['modelos']) or '-'}")
        print("\nCopia esto en config.json y pon tus cuotas (0 = sin declarar):\n")
        prop = [{"nombre": n, "modelos": x["modelos"], "limite_5h_tokens": 0, "limite_semana_tokens": 0}
                for n, x in r["proveedores"].items() if n != "otros"]
        print(json.dumps({"proveedores": prop}, indent=2, ensure_ascii=False))
        print("\nLas cuotas de cada plan las sabe el proveedor, no los logs: ponlas cuando las tengas")
        print("y veras el % de la ventana de 5 h y de la semana en el icono y en el panel.")
        return
    if "--print" in sys.argv:
        print(informa(informe()))
        return
    servir()        # el panel solo con la app (rumps): un --print no debe quitarle el puerto a la barra
    try:
        import rumps
    except ImportError:
        print("rumps no está instalado (solo hace falta para el icono de la barra en macOS).")
        print("Sin él sí funcionan:  python3 analizar_gasto.py   y   python3 panel.py")
        return

    class CostBar(rumps.App):
        def __init__(self):
            super().__init__("CostBar", title="…", quit_button=None)
            self.r = None
            self.refrescar(None)
            rumps.Timer(self.refrescar, 60).start()

        def abrir_panel(self, _):
            try:
                sys.path.insert(0, AQUI)
                import panel
                ruta, _, _ = panel.main()
                subprocess.Popen(["open", ruta])
            except Exception as e:
                log(f"ERROR panel: {type(e).__name__}: {e}")

        def copia(self, texto):
            def _f(_):
                try:
                    subprocess.run(["pbcopy"], input=texto.encode())
                except Exception:
                    pass
            return _f

        def _item(self, titulo):
            it = rumps.MenuItem(titulo, callback=self.copia(titulo))
            self.menu.add(it)
            return it

        _titulo = ""

        def refrescar(self, _):
            try:
                r = informe()
                self.r = r
                h, c5 = r["hoy"], r["cinco_h"]
                lim5 = r["limites"].get("limite_5h_tokens") or 0
                alto = r["ritmo"]["tok"] >= r["limites"]["limite_hora_tokens"]
                lim5v = lim5 or 0
                fraccion = (c5["tok"] / lim5v) if lim5v else (r["ritmo"]["tok"] / (r["limites"]["limite_hora_tokens"] or 1) or 0.34)
                icono = icono_png(os.path.join(AQUI, "icono.png"), fraccion, self._titulo, color_magnitud(c5["tok"]), alto)
                if icono:
                    self.icon = icono
                self.title = ("" if self.icon else self._titulo) + (" !" if (alto and not self.icon) else "")
                self.menu.clear()
                m = self._item
                # la ventana de 5 horas es el dato que se agota en las suscripciones
                m(fila("Ultimas 5 h", fmt_tok(c5["tok"]), r["pct_5h"] if lim5 else None) + (f"   {r['pct_5h']:.0f} % del limite" if lim5 else "   tokens"))
                for k, v in sorted(c5["fam"].items(), key=lambda x: -x[1]):
                    if v:
                        m(f"   {k}: {fmt_tok(v)}")
                m(fila("Semana", fmt_tok(r["semana"]["tok"]), r["pct_semana"] if r["limites"].get("limite_semana_tokens") else None) + f"   {r['semana']['turnos']} turnos")
                pv = rumps.MenuItem("Por proveedor (plan)")
                for nombre, x in r["proveedores"].items():
                    if not x["cinco_h"] and not x["semana"]:
                        continue
                    txt = fila(nombre, fmt_tok(x["cinco_h"]), x["pct_5h"] if x["pct_5h"] else None)
                    txt += "   " + f"sem {fmt_tok(x['semana']):>9}"
                    if x["pct_semana"]:
                        txt += " " + barra(x["pct_semana"], 6)
                    if x["pct_5h"]:
                        txt += f"   {x['pct_5h']:.0f} % de 5 h"
                    pv.add(rumps.MenuItem(txt))
                self.menu.add(pv)
                self.menu.add(rumps.separator)
                m(fila("Hoy", fmt_tok(h["tok"])) + f"   {h['turnos']} turnos")
                m(f"   entrada {fmt_tok(h['gi'])} · salida {fmt_tok(h['go'])}")
                m(f"   cache leida {fmt_tok(h['cr'])} ({r['cache_pct_hoy']:.0f} % de la entrada) · escrita {fmt_tok(h['cw'])}")
                m(f"Ritmo ultima hora: {fmt_tok(r['ritmo']['tok'])} tokens" + ("  ALTO" if alto else ""))
                self.menu.add(rumps.separator)
                m(f"Ayer: {fmt_tok(r['ayer']['tok'])} tokens · {r['ayer']['turnos']} turnos")
                for d, v in sorted(r["dias"].items())[-5:]:
                    m(f"   {d[5:]}: {fmt_tok(v['tok'])} ({v['turnos']} turnos)")
                mm = rumps.MenuItem("Por modelo (hoy)")
                for k, v in sorted(r["modelos_hoy"].items(), key=lambda x: -x[1]["tok"])[:8]:
                    mm.add(rumps.MenuItem(f"{corto(k)}: {fmt_tok(v['tok'])} · {v['turnos']} turnos"))
                self.menu.add(mm)
                pp = rumps.MenuItem("Por proyecto (7 dias)")
                for k, v in sorted(r["proyectos"].items(), key=lambda x: -x[1]["tok"])[:8]:
                    pp.add(rumps.MenuItem(f"{k}: {fmt_tok(v['tok'])}"))
                self.menu.add(pp)
                self.menu.add(rumps.separator)
                m(f"Equivalente API: {h['usd']:.1f} USD hoy (informativo)")
                m("Con suscripcion no se paga por token: mira tokens y turnos.")
                self.menu.add(rumps.separator)
                self.menu.add(rumps.MenuItem("Abrir panel", callback=self.abrir_panel))
                self.menu.add(rumps.MenuItem("Abrir la guia", callback=lambda _: subprocess.Popen(["open", os.path.join(AQUI, "GUIA.md")])))
                self.menu.add(rumps.MenuItem("Refrescar ahora", callback=self.refrescar))
                self.menu.add(rumps.MenuItem("Salir", callback=rumps.quit_application))
                if alto:
                    rumps.notification("CostBar", f"Ritmo alto: {fmt_tok(r['ritmo']['tok'])} tokens/h",
                                       f"5 h: {fmt_tok(c5['tok'])} · hoy {fmt_tok(h['tok'])}. Baja turnos o contexto.")
                try:
                    import panel
                    panel.main()
                except Exception as e:
                    log(f"panel: {type(e).__name__}: {e}")
                log(f"refresco ok: 5h {fmt_tok(c5['tok'])} · hoy {fmt_tok(h['tok'])} tok · {h['turnos']} turnos · nuevos {r['nuevos']}")
            except Exception as e:
                log(f"ERROR {type(e).__name__}: {e}")
                self.title = "!"

    CostBar().run()


if __name__ == "__main__":
    main()
