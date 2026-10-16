/* Pestaña Histórico: consumo por día, semana o mes más allá de los 30 días de los logs. Lee /historico (solo con el almacén activo).
   Todo se construye con el DOM y textContent: los nombres de proyecto vienen de los logs y nunca se interpretan como HTML.
   El gráfico lo dibuja graficos.js: a él solo le llegan nombres ya escapados con esc() (app.js). */
(function(){
  const {h, fmt} = window.CG;
  const $ = id => document.getElementById(id);
  const MAX_SERIES_VISIBLES = 9;
  const RANGOS = [["30", "30 días", "dia"], ["90", "90 días", "dia"], ["365", "12 meses", "semana"], ["todo", "Todo", "mes"]];
  const PASOS = [["dia", "Día"], ["semana", "Semana"], ["mes", "Mes"]];
  const GRUPOS = [["ninguno", "Nada"], ["plan", "Plan"], ["proyecto", "Proyecto"], ["agente", "Agente"]];
  const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"];
  let estado = {rango: "30", paso: "dia", agrupa: "plan"}, cargando = 0, ultimaConsulta = "";

  const iso = d => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  function fechas(rango){
    const hoy = new Date(), desde = new Date(hoy);
    if (rango === "todo") return ["2000-01-01", iso(hoy)];
    desde.setDate(desde.getDate() - (+rango - 1));
    return [iso(desde), iso(hoy)];
  }
  // iconos del kit (cadenas fijas de graficos.js, nunca datos) convertidos en nodos sin interpretar HTML
  function iconoDom(n){ return new DOMParser().parseFromString(icono(n).replace("<svg ", "<svg xmlns=\"http://www.w3.org/2000/svg\" "), "image/svg+xml").documentElement; }
  function seg(opciones, actual, cambio){
    return h("div", {class: "seg vidrio"}, ...opciones.map(([v, t]) => h("button", {class: v === actual ? "on" : "", onclick: () => cambio(v)}, t)));
  }
  // "2026-10-15" → "15 oct"; "2026-10" → "oct 26"
  function etiqueta(c, paso){
    const [a, m, d] = c.split("-");
    return paso === "mes" || !d ? MESES[+m - 1] + " " + a.slice(2) : (+d) + " " + MESES[+m - 1];
  }
  // fecha del rango con año solo si no es el actual: «13 oct 2025 → 18 oct»
  function fechaRango(c){ const a = c.slice(0, 4); return etiqueta(c, "dia") + (a !== String(new Date().getFullYear()) ? " " + a : ""); }
  function titulo(c, paso){
    const [a, m, d] = c.split("-");
    return paso === "mes" || !d ? MESES[+m - 1] + " de " + a : paso === "semana" ? "Semana del " + (+d) + " de " + MESES[+m - 1] : (+d) + " de " + MESES[+m - 1] + " de " + a;
  }

  // las series de más allá de la novena se funden en "Resto" para que el gráfico se pueda leer
  function visibles(r){
    const ord = r.series.slice();
    if (ord.length <= MAX_SERIES_VISIBLES) return ord;
    const resto = {nombre: "Resto", tokens: r.cubos.map(() => 0), turnos: []};
    ord.slice(MAX_SERIES_VISIBLES - 1).forEach(s => s.tokens.forEach((v, i) => { if (v != null) resto.tokens[i] += v; }));
    return ord.slice(0, MAX_SERIES_VISIBLES - 1).concat([resto]);
  }

  function tarjeta(ic, rot, num, pie, extra){
    return h("div", {class: "tj c3"},
      h("div", {class: "et"}, iconoDom(ic), rot, extra ? h("span", {class: "der"}, extra) : null),
      h("div", {class: "v"}, num), h("div", {class: "s"}, pie));
  }

  function pinta(r){
    const series = visibles(r), total = r.total || 0, m = r.mes_en_curso || {};
    const delta = r.anterior ? ((total - r.anterior) / r.anterior * 100) : null;
    const nombrePaso = r.paso === "dia" ? "día" : r.paso === "semana" ? "semana" : "mes";
    const kpis = h("div", {class: "rej"},
      tarjeta("caja", "Total del rango", fmt(total), fechaRango(r.desde) + " → " + fechaRango(r.hasta)),
      tarjeta("reloj", "Periodo anterior", r.anterior ? fmt(r.anterior) : "—", delta == null ? "sin datos para comparar" : "mismo número de días, justo antes",
        delta == null ? null : h("span", {class: "delta " + (delta > 0 ? "sube" : "baja")}, (delta >= 0 ? "+" : "") + delta.toFixed(0) + " %")),
      tarjeta("cal", "Mes en curso" + (m.mes ? " · " + etiqueta(m.mes, "mes") : ""), fmt(m.acumulado || 0), m.proyeccion ? "proyección a fin de mes ≈ " + fmt(m.proyeccion) : "la proyección sale desde el día 3"),
      tarjeta("capas", "Datos desde", r.datos_desde ? etiqueta(r.datos_desde, "dia") + " " + r.datos_desde.slice(0, 4) : "—", "antes de esa fecha no hay histórico"));
    const graf = h("div", {});
    const titulo_ = (t, nota) => h("div", {class: "sec"}, h("span", {}, t), nota ? h("span", {class: "nota"}, nota) : null);
    const caja = h("div", {class: "col-f"},
      titulo_("Consumo por " + nombrePaso, r.agrupa === "ninguno" ? "total" : "apilado por " + GRUPOS.find(g => g[0] === r.agrupa)[1].toLowerCase() + " · pasa el ratón para ver el detalle"),
      h("div", {class: "tj"}, graf, h("div", {class: "leyenda"}, ...series.map((s, i) => h("span", {}, h("i", {class: "p", style: "background:" + colorI(i)}), s.nombre)))));
    if (r.cubos.length) {
      grafico(graf, {tipo: r.cubos.length > 40 ? "area" : "barras", alto: 260, fmt,
        series: series.map((s, i) => ({nombre: esc(s.nombre), color: colorSerie(i), valores: s.tokens.map(v => v || 0)})),
        etiquetas: r.cubos.map(c => etiqueta(c, r.paso)), titulos: r.cubos.map(c => titulo(c, r.paso))});
    } else graf.append(h("p", {class: "vacio"}, "Sin datos en el rango."));
    const tabla = r.agrupa !== "ninguno" && series.length ? h("div", {class: "col-f"}, titulo_("Reparto por " + GRUPOS.find(g => g[0] === r.agrupa)[1].toLowerCase(), "todo el rango"), h("div", {class: "tj"},
      h("table", {}, h("tr", {}, h("th", {}, GRUPOS.find(g => g[0] === r.agrupa)[1]), h("th", {}, "Tokens"), h("th", {}, "% del total"), h("th", {style: "width:30%"}, "")),
        ...series.map((s, i) => { const t = s.tokens.reduce((a, v) => a + (v || 0), 0), p = total ? t / total * 100 : 0;
          return h("tr", {}, h("td", {}, h("span", {class: "punto", style: "background:" + colorI(i)}), s.nombre), h("td", {class: "mono"}, fmt(t)), h("td", {class: "mono"}, total ? p.toFixed(1).replace(".", ",") + " %" : "—"),
            h("td", {}, h("div", {class: "prog", style: "margin:0"}, h("i", {style: "width:" + p.toFixed(1) + "%;background:" + colorI(i)})))); })))) : null;
    return [kpis, caja, tabla,
      h("p", {class: "ayuda", style: "margin:0 3px"}, "Tokens totales: entrada, salida y caché. Los proyectos ocultos cuentan en el total y salen como (ocultos). La proyección es una estimación lineal. El CSV va sin cifrar y con ; como separador (Excel en español lo abre directo); para uno cifrado con frase: costbar.py --exportar-csv --cifrar.")];
  }

  function controles(){
    const cambiar = (k, v) => { estado[k] = v; if (k === "rango") { const rg = RANGOS.find(x => x[0] === v); estado.paso = rg[2]; } window.cargarHistorico(); };
    return h("div", {class: "filtros"},
      h("div", {class: "grupo"}, h("span", {class: "etq"}, "Rango"), seg(RANGOS.map(x => [x[0], x[1]]), estado.rango, v => cambiar("rango", v))),
      h("div", {class: "grupo"}, h("span", {class: "etq"}, "Paso"), seg(PASOS, estado.paso, v => cambiar("paso", v))),
      h("div", {class: "grupo"}, h("span", {class: "etq"}, "Agrupar"), seg(GRUPOS, estado.agrupa, v => cambiar("agrupa", v))),
      ultimaConsulta ? h("a", {class: "btn-enlace", href: "/historico.csv?" + ultimaConsulta, download: ""}, "Descargar CSV") : null);
  }

  window.cargarHistorico = function(){
    const dest = $("vista-historico"), mio = ++cargando;
    const [desde, hasta] = fechas(estado.rango);
    const q = new URLSearchParams({desde, hasta, paso: estado.paso, agrupa: estado.agrupa});
    ultimaConsulta = q.toString();
    dest.replaceChildren(controles(), h("p", {class: "vacio"}, "Cargando…"));
    fetch("/historico?" + q).then(async res => {
      if (res.status === 401) throw new Error("la sesión ha caducado o no existe (se reinició la barra o pasaron 12 h)");
      const d = await res.json().catch(() => ({}));
      if (mio !== cargando) return;
      if (res.status === 400 || res.status === 429 || res.status === 503) throw new Error(d.error || ("HTTP " + res.status));
      if (!res.ok) throw new Error("HTTP " + res.status);
      if (d.disponible === false) { dest.replaceChildren(controles(), h("p", {class: "ayuda", style: "color:var(--ambar)"}, d.motivo || "El histórico no está disponible.")); return; }
      if (estado.rango === "todo" && d.datos_desde) {                  // recorta los cubos vacíos anteriores al primer dato
        const i0 = d.totales.findIndex(t => t != null);
        if (i0 > 0) { d.cubos = d.cubos.slice(i0); d.totales = d.totales.slice(i0); d.series.forEach(s => { s.tokens = s.tokens.slice(i0); s.turnos = s.turnos.slice(i0); }); d.desde = d.cubos[0]; }
      }
      dest.replaceChildren(controles(), ...pinta(d).filter(Boolean));
    }).catch(e => { if (mio === cargando) dest.replaceChildren(controles(), h("p", {class: "ayuda", style: "color:var(--rojo)"}, "No se pudo cargar el histórico: " + e.message)); });
  };
  if (location.protocol !== "file:" && location.hash === "#historico") window.vista("historico");      // aquí y no en ajustes.js: este fichero se carga después
})();
