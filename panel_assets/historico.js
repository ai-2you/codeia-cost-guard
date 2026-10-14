/* Pestaña Histórico: consumo por día, semana o mes más allá de los 30 días de los logs. Lee /historico (solo con el almacén activo).
   Todo se construye con el DOM y textContent: los nombres de proyecto vienen de los logs y nunca se interpretan como HTML. */
(function(){
  const {h, fmt} = window.CG;
  const $ = id => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";
  const MAX_SERIES_VISIBLES = 9;
  const RANGOS = [["30", "Últimos 30 días", "dia"], ["90", "Últimos 90 días", "dia"], ["365", "Últimos 12 meses", "semana"], ["todo", "Desde el principio", "mes"]];
  const PASOS = [["dia", "Día"], ["semana", "Semana"], ["mes", "Mes"]];
  const GRUPOS = [["ninguno", "Sin agrupar"], ["proyecto", "Proyecto"], ["plan", "Plan"], ["agente", "Agente"]];
  let estado = {rango: "30", paso: "dia", agrupa: "plan"}, cargando = 0;

  const iso = d => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  function fechas(rango){
    const hoy = new Date(), desde = new Date(hoy);
    if (rango === "todo") return ["2000-01-01", iso(hoy)];
    desde.setDate(desde.getDate() - (+rango - 1));
    return [iso(desde), iso(hoy)];
  }
  function svg(tag, attrs, ...hijos){
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    for (const c of hijos.flat()) if (c != null) e.append(c);
    return e;
  }
  function selector(nombre, opciones, actual, cambio){
    return h("label", {}, nombre + " ", h("select", {onchange: ev => cambio(ev.target.value)}, ...opciones.map(([v, t]) => h("option", {value: v, selected: v === actual}, t))));
  }

  // las series de más allá de la novena se funden en "Resto" para que el gráfico se pueda leer
  function visibles(r){
    const ord = r.series.slice();
    if (ord.length <= MAX_SERIES_VISIBLES) return ord;
    const resto = {nombre: "Resto", tokens: r.cubos.map(() => 0), turnos: []};
    ord.slice(MAX_SERIES_VISIBLES - 1).forEach(s => s.tokens.forEach((v, i) => { if (v != null) resto.tokens[i] += v; }));
    return ord.slice(0, MAX_SERIES_VISIBLES - 1).concat([resto]);
  }

  function grafico(r, series){
    const W = 720, H = 220, padB = 24, padT = 8, n = r.cubos.length, bw = W / n;
    const tope = Math.max(1, ...r.totales.map(t => t || 0));
    const hijos = [];
    r.cubos.forEach((c, i) => {
      if (r.totales[i] == null) return;
      let y = H - padB;
      const g = svg("g", {});
      series.forEach((s, si) => {
        const v = s.tokens[i] || 0;
        if (!v) return;
        const alto = Math.max((v / tope) * (H - padB - padT), 1);
        y -= alto;
        g.append(svg("rect", {x: (i * bw + bw * 0.12).toFixed(1), y: y.toFixed(1), width: (bw * 0.76).toFixed(1), height: alto.toFixed(1), fill: colorI(si)},
          svg("title", {}, document.createTextNode(c + " · " + s.nombre + ": " + fmt(v)))));
      });
      hijos.push(g);
    });
    const paso = Math.max(1, Math.ceil(n / 8));
    r.cubos.forEach((c, i) => { if (i % paso === 0) hijos.push(svg("text", {x: (i * bw + bw / 2).toFixed(1), y: H - 7, "text-anchor": "middle", class: "etq-eje"}, document.createTextNode(c.slice(r.paso === "mes" ? 0 : 5)))); });
    hijos.push(svg("line", {class: "eje", x1: 0, y1: H - padB, x2: W, y2: H - padB}));
    return svg("svg", {class: "grafico-svg", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Consumo por periodo"}, hijos);
  }

  function tarjeta(rot, num, pie){ return h("div", {class: "tarjeta"}, h("div", {class: "rot"}, rot), h("div", {class: "num"}, num), h("div", {class: "pie"}, pie)); }

  function pinta(r){
    const series = visibles(r);
    const total = r.total || 0;
    const delta = r.anterior ? ((total - r.anterior) / r.anterior * 100) : null;
    const m = r.mes_en_curso || {};
    const kpis = h("div", {class: "rejilla"},
      tarjeta("Total del rango", fmt(total), r.desde + " → " + r.hasta),
      tarjeta("Periodo anterior", fmt(r.anterior || 0), delta == null ? "sin datos para comparar" : (delta >= 0 ? "+" : "") + delta.toFixed(0) + " % ahora"),
      tarjeta("Mes en curso (" + m.mes + ")", fmt(m.acumulado || 0), m.proyeccion ? "proyección a fin de mes ≈ " + fmt(m.proyeccion) + " (estimación)" : "proyección desde el día 3"),
      tarjeta("Datos desde", r.datos_desde || "—", "antes de esa fecha no hay histórico"));
    const filas = series.map((s, i) => {
      const t = s.tokens.reduce((a, v) => a + (v || 0), 0);
      return h("tr", {}, h("td", {}, h("span", {class: "punto", style: "background:" + colorI(i)}), s.nombre), h("td", {}, fmt(t)), h("td", {}, total ? (t / total * 100).toFixed(1) + " %" : "—"));
    });
    return [h("section", {}, h("h2", {}, "Consumo por " + (r.paso === "dia" ? "día" : r.paso === "semana" ? "semana (de lunes)" : "mes")), kpis,
        r.cubos.length ? h("div", {class: "grafico"}, grafico(r, series)) : h("p", {class: "vacio"}, "Sin datos en el rango."),
        r.agrupa !== "ninguno" ? h("table", {}, h("tr", {}, h("th", {style: "text-align:left"}, GRUPOS.find(g => g[0] === r.agrupa)[1]), h("th", {}, "Tokens"), h("th", {}, "%")), ...filas) : null,
        h("p", {class: "ayuda"}, "Tokens totales: entrada, salida y caché. Los proyectos ocultos cuentan en el total y salen como (ocultos). La proyección es una estimación lineal.")) ];
  }

  function controles(){
    const cambiar = (k, v) => { estado[k] = v; if (k === "rango") { const rg = RANGOS.find(x => x[0] === v); estado.paso = rg[2]; } window.cargarHistorico(); };
    return h("div", {class: "fila-b"}, selector("Rango", RANGOS.map(x => [x[0], x[1]]), estado.rango, v => cambiar("rango", v)),
      selector("Paso", PASOS, estado.paso, v => cambiar("paso", v)), selector("Agrupar por", GRUPOS, estado.agrupa, v => cambiar("agrupa", v)));
  }

  window.cargarHistorico = function(){
    const dest = $("vista-historico"), mio = ++cargando;
    const [desde, hasta] = fechas(estado.rango);
    const q = new URLSearchParams({desde, hasta, paso: estado.paso, agrupa: estado.agrupa});
    dest.replaceChildren(controles(), h("p", {class: "vacio"}, "Cargando…"));
    fetch("/historico?" + q).then(async res => {
      if (res.status === 401) throw new Error("la sesión ha caducado o no existe (se reinició la barra o pasaron 12 h)");
      const d = await res.json().catch(() => ({}));
      if (mio !== cargando) return;
      if (res.status === 400 || res.status === 429 || res.status === 503) throw new Error(d.error || ("HTTP " + res.status));
      if (!res.ok) throw new Error("HTTP " + res.status);
      if (d.disponible === false) { dest.replaceChildren(controles(), h("p", {class: "ayuda", style: "color:#d68910"}, d.motivo || "El histórico no está disponible.")); return; }
      if (estado.rango === "todo" && d.datos_desde) {                  // recorta los cubos vacíos anteriores al primer dato
        const i0 = d.totales.findIndex(t => t != null);
        if (i0 > 0) { d.cubos = d.cubos.slice(i0); d.totales = d.totales.slice(i0); d.series.forEach(s => { s.tokens = s.tokens.slice(i0); s.turnos = s.turnos.slice(i0); }); d.desde = d.cubos[0]; }
      }
      dest.replaceChildren(controles(), ...pinta(d));
    }).catch(e => { if (mio === cargando) dest.replaceChildren(controles(), h("p", {class: "ayuda", style: "color:#c0392b"}, "No se pudo cargar el histórico: " + e.message)); });
  };
  if (location.protocol !== "file:" && location.hash === "#historico") window.vista("historico");      // aquí y no en ajustes.js: este fichero se carga después
})();
