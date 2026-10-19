/* Kit de gráficos en SVG hecho a mano: sin librerías ni red (el panel se sirve con una CSP que no deja cargar nada
   de fuera). Las funciones devuelven HTML/SVG; el único estado es el que `grafico()` cuelga del contenedor para el
   tooltip. Los nombres que llegan aquí ya vienen escapados por app.js (escapar(D)). Nada de onclick: las zonas
   llevan data-a/data-v y app.js las atiende con su delegación de clics. */

// colores de serie del tema (estilos.css): coral, cian, lavanda, verde, ámbar, turquesa, rosa, gris
const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"];
function colorSerie(i){ return SERIES[i % SERIES.length]; }
function colorI(i){ return "var(" + colorSerie(i) + ")"; }            // Histórico pinta con este (fill y background)
let _uid = 0;

/* curva monótona (Fritsch–Carlson): suave sin pasar por debajo de cero ni inventar picos entre dos puntos */
function curva(pts){
  const n = pts.length; if(n < 2) return n ? `M${pts[0][0]},${pts[0][1]}` : "";
  const dx = [], m = [], t = [];
  for(let i = 0; i < n - 1; i++){ dx[i] = pts[i+1][0] - pts[i][0]; m[i] = (pts[i+1][1] - pts[i][1]) / dx[i]; }
  t[0] = m[0]; t[n-1] = m[n-2];
  for(let i = 1; i < n - 1; i++) t[i] = m[i-1] * m[i] <= 0 ? 0 : 3 * (dx[i-1] + dx[i]) / ((2*dx[i] + dx[i-1]) / m[i-1] + (dx[i] + 2*dx[i-1]) / m[i]);
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for(let i = 0; i < n - 1; i++){ const h = dx[i] / 3;
    d += `C${(pts[i][0]+h).toFixed(1)},${(pts[i][1]+t[i]*h).toFixed(1)} ${(pts[i+1][0]-h).toFixed(1)},${(pts[i+1][1]-t[i+1]*h).toFixed(1)} ${pts[i+1][0].toFixed(1)},${pts[i+1][1].toFixed(1)}`; }
  return d;
}
/* marcas del eje Y redondas (1, 2, 2,5, 5 × 10^n) que llegan al máximo */
function marcasY(max){
  const e = Math.pow(10, Math.floor(Math.log10(max || 1))), paso = [1, 2, 2.5, 5, 10].map(x => x*e).find(s => max/s <= 4) || e*10, t = [];
  for(let v = 0; v < max + paso*.001; v += paso) t.push(v);
  if(t[t.length-1] < max) t.push(t[t.length-1] + paso);
  return t;
}

/* Series en el tiempo, apiladas: barras o área. series: [{nombre, color, valores[]}]; etiquetas: texto del eje X;
   titulos: cabecera del tooltip; accion: data-a de cada zona (clic) con data-v = índice; sel: Set de índices elegidos. */
function grafico(cont, {series, etiquetas, titulos, tipo, alto, fmt, accion, sel, unidad, apilar}){
  apilar = apilar !== false && unidad !== "%";                 // los porcentajes no se suman: cada serie va por su cuenta
  const W = 960, H = alto || 250, izq = 50, der = 6, arr = 10, aba = 24, n = etiquetas.length, id = "g" + (++_uid);
  if(!n || !series.length){ cont.innerHTML = "<p class='vacio'>sin datos</p>"; cont._datos = null; return; }
  const tot = etiquetas.map((_, i) => series.reduce((a, s) => a + (s.valores[i] || 0), 0));
  const tope = apilar ? Math.max(...tot, 1) : Math.max(...series.flatMap(s => s.valores.map(v => v || 0)), 1);
  const tk = marcasY(unidad === "%" ? Math.min(Math.max(tope, 10), 100) : tope), maxY = tk[tk.length-1];
  const y = v => arr + (H - aba - arr) * (1 - v / maxY), ancho = W - izq - der, barras = tipo === "barras";
  const xc = i => barras ? izq + ancho / n * (i + .5) : izq + (n > 1 ? ancho / (n - 1) * i : ancho / 2);
  const etqY = t => t ? (unidad === "%" ? Math.round(t) + " %" : fmt(t)) : "0";
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${unidad === "%" ? "porcentaje" : "tokens"} por periodo"><defs>`;
  series.forEach((se, k) => s += `<linearGradient id="${id}-${k}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(${se.color})" stop-opacity=".42"/><stop offset="1" stop-color="var(${se.color})" stop-opacity=".02"/></linearGradient>`);
  s += "</defs>" + tk.map(t => `<line class="rej-l" x1="${izq}" x2="${W-der}" y1="${y(t)}" y2="${y(t)}"/><text x="${izq-8}" y="${y(t)+3.5}" text-anchor="end">${etqY(t)}</text>`).join("");
  if(barras){
    const w = Math.min(28, ancho / n * .56);
    etiquetas.forEach((_, i) => { let acc = 0; s += `<g class="col${sel && sel.has(i) ? " sel" : ""}" data-i="${i}">`;
      series.forEach(se => { const v = se.valores[i] || 0; if(!v) return; const y0 = y(acc), y1 = y(acc + v), arriba = acc + v === tot[i];
        s += `<rect x="${(xc(i)-w/2).toFixed(1)}" y="${y1.toFixed(1)}" width="${w.toFixed(1)}" height="${Math.max(y0-y1, .5).toFixed(1)}" fill="var(${se.color})" rx="${arriba ? 4 : 0}"/>`; acc += v; });
      s += "</g>"; });
  } else {
    let base = etiquetas.map(() => 0);
    const capas = series.map((se, k) => { const b0 = apilar ? base : base.map(() => 0), sup = b0.map((b, i) => b + (se.valores[i] || 0)), pa = sup.map((v, i) => [xc(i), y(v)]), pb = b0.map((v, i) => [xc(i), y(v)]).reverse();
      const linea = curva(pa), area = linea + "L" + curva(pb).slice(1); if(apilar) base = sup; return {se, k, linea, area, pa}; });
    capas.slice().reverse().forEach(c => s += `<path d="${c.area}Z" fill="url(#${id}-${c.k})"/>`);
    capas.forEach(c => s += `<path class="trazo" d="${c.linea}" fill="none" stroke="var(${c.se.color})" stroke-width="1.8" stroke-linejoin="round"/>`);
    (apilar ? [capas[capas.length-1]] : capas).forEach(c => s += `<circle cx="${c.pa[n-1][0]}" cy="${c.pa[n-1][1]}" r="3.5" fill="var(${c.se.color})" stroke="var(--fondo)" stroke-width="2"/>`);
    if(sel) sel.forEach(i => s += `<line x1="${xc(i)}" x2="${xc(i)}" y1="${arr}" y2="${H-aba}" stroke="var(--marca2)" stroke-width="1.5" opacity=".7"/>`);
    s += `<line class="guia" x1="0" x2="0" y1="${arr}" y2="${H-aba}"/>` + capas.map(c => `<circle class="pt" r="3.5" fill="var(${c.se.color})" stroke="var(--fondo)" stroke-width="2" opacity="0"/>`).join("");
  }
  const cada = Math.ceil(n / 10);
  s += etiquetas.map((t, i) => (i % cada && i !== n-1) ? "" :
    `<text x="${xc(i)}" y="${H-6}" text-anchor="${!barras && i === 0 ? "start" : !barras && i === n-1 ? "end" : "middle"}">${t}</text>`).join("");
  const zw = ancho / (barras ? n : Math.max(n - 1, 1));
  s += etiquetas.map((_, i) => `<rect class="zona" data-i="${i}"${accion ? ` data-a="${accion}" data-v="${i}"` : ""} x="${(barras ? izq + zw*i : xc(i) - zw/2).toFixed(1)}" y="0" width="${zw.toFixed(1)}" height="${H-aba}"/>`).join("");
  cont.innerHTML = s + `</svg><div class="tip"></div>`;
  cont.classList.add("graf"); cont.classList.toggle("con-sel", !!(sel && sel.size && barras));
  cont._datos = {series, titulos, tot, unidad, xc, y, W, fmt, apilar};
}

// tooltip y guía de todos los gráficos, con una sola escucha
document.addEventListener("pointermove", ev => {
  const z = ev.target.closest && ev.target.closest(".zona"), g = z && z.closest(".graf");
  document.querySelectorAll(".graf.hover").forEach(x => { if(x !== g){ x.classList.remove("hover"); const t = x.querySelector(".tip"); if(t) t.classList.remove("ver"); } });
  if(!g || !g._datos) return;
  const D_ = g._datos, i = +z.dataset.i, r = g.getBoundingClientRect(), escala = r.width / D_.W;
  g.classList.add("hover"); g.querySelectorAll(".col").forEach(c => c.classList.toggle("on", +c.dataset.i === i));
  const gu = g.querySelector(".guia");
  if(gu){ gu.setAttribute("x1", D_.xc(i)); gu.setAttribute("x2", D_.xc(i)); let acc = 0;
    g.querySelectorAll(".pt").forEach((p, k) => { const v = D_.series[k].valores[i] || 0; acc = D_.apilar ? acc + v : v; p.setAttribute("cx", D_.xc(i)); p.setAttribute("cy", D_.y(acc)); p.setAttribute("opacity", 1); }); }
  const u = D_.unidad === "%" ? v => Math.round(v) + " %" : D_.fmt;
  const tip = g.querySelector(".tip");
  tip.innerHTML = `<b>${D_.titulos[i]}</b>` + D_.series.slice().reverse().map(s => `<div class="l"><span><i class="p" style="background:var(${s.color})"></i>${s.nombre}</span><span class="mono">${s.valores[i] ? u(s.valores[i]) : "—"}</span></div>`).join("")
    + (D_.series.length > 1 && D_.unidad !== "%" ? `<div class="l tot"><span>Total</span><span class="mono"><b style="display:inline">${D_.fmt(D_.tot[i])}</b></span></div>` : "");
  tip.classList.add("ver");
  const x = D_.xc(i) * escala, tw = tip.offsetWidth;
  tip.style.left = Math.max(0, Math.min(r.width - tw, x + 14 + tw > r.width ? x - tw - 14 : x + 14)) + "px";
  tip.style.top = "6px";
});

/* anillo: partes = [[valor, color]]; centro = texto en medio */
function anillo(partes, tam, grosor, centro){
  const r = (tam - grosor) / 2, c = 2 * Math.PI * r, total = partes.reduce((a, p) => a + p[0], 0) || 1; let acc = 0;
  const arcos = partes.map(([v, col, op]) => { const l = Math.max(v / total * c - (partes.length > 1 ? 2 : 0), .5);
    const s = `<circle r="${r}" cx="${tam/2}" cy="${tam/2}" fill="none" stroke="var(${col})" stroke-opacity="${op || 1}" stroke-width="${grosor}" stroke-dasharray="${l.toFixed(2)} ${(c-l).toFixed(2)}" stroke-dashoffset="${(-acc).toFixed(2)}" transform="rotate(-90 ${tam/2} ${tam/2})"/>`;
    acc += v / total * c; return s; });
  return `<svg width="${tam}" height="${tam}" viewBox="0 0 ${tam} ${tam}" style="flex:none" aria-hidden="true"><circle r="${r}" cx="${tam/2}" cy="${tam/2}" fill="none" stroke="var(--pista)" stroke-width="${grosor}"/>${arcos.join("")}${centro ? `<text x="50%" y="50%" text-anchor="middle" dominant-baseline="central" fill="var(--texto)" style="font:600 ${Math.round(tam/7)}px var(--mono)">${centro}</text>` : ""}</svg>`;
}

/* línea mínima con relleno para las tarjetas (sin ejes) */
function chispa(vals, color, alto){
  if(vals.length < 2) return "";
  const w = 300, h = alto, max = Math.max(...vals, 1), pts = vals.map((v, i) => [i * w / (vals.length - 1), h - 2 - v / max * (h - 6)]), d = curva(pts), id = "c" + (++_uid);
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%;height:${h}px;display:block;margin-top:10px" aria-hidden="true">
    <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(${color})" stop-opacity=".35"/><stop offset="1" stop-color="var(${color})" stop-opacity="0"/></linearGradient></defs>
    <path d="${d}L${w},${h}L0,${h}Z" fill="url(#${id})"/><path d="${d}" fill="none" stroke="var(${color})" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

/* calendario mensual compacto: columnas de lunes a domingo, una fila por semana; cada día lleva su número y sus tokens
   y el fondo según la intensidad (raíz, para que los días flojos también se vean). dias = [[aaaa-mm-dd, valor]] */
function calendario(dias, {fmt, accion, indice, sel, color, nombre}){
  indice = indice || (i => i);
  if(!dias.length) return "<p class='vacio'>sin datos</p>";
  const max = Math.max(...dias.map(d => d[1]), 1), ini = (new Date(dias[0][0] + "T12:00:00").getDay() + 6) % 7, hoy = dias[dias.length-1][0];
  const MES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"];
  let s = `<div class="cal" role="grid" aria-label="Calendario de consumo">` + ["L", "M", "X", "J", "V", "S", "D"].map(t => `<div class="cal-dow">${t}</div>`).join("")
    + "<div></div>".repeat(ini);
  dias.forEach(([f, v], i) => {
    const o = v ? .14 + .7 * Math.sqrt(v / max) : 0, d = +f.slice(8), primero = d === 1 || i === 0;
    s += `<button class="cal-dia${sel && sel.has(i) ? " sel" : ""}${f === hoy ? " hoy" : ""}" data-a="${accion}" data-v="${indice(i)}" title="${nombre(f)}: ${fmt(v)}"`
      + ` style="background:${v ? `color-mix(in srgb,var(${color}) ${Math.round(o*100)}%,transparent)` : "var(--pista)"}">`
      + `<span class="cal-n">${d}${primero ? " " + MES[+f.slice(5, 7) - 1] : ""}</span><span class="cal-v">${v ? fmt(v) : "—"}</span></button>`;
  });
  return s + "</div>";
}

/* iconos de trazo (24×24), en línea: sin peticiones externas */
const ICONOS = {
  reloj: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', pulso: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  caja: '<path d="M21 8l-9-5-9 5 9 5 9-5z"/><path d="M3 8v8l9 5 9-5V8"/>', cal: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  capas: '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/>', escudo: '<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  carpeta: '<path d="M3 6h6l2 2h10v11H3z"/>', ajustes: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
  sol: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  luna: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  web: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>',
  cpu: '<rect x="7" y="7" width="10" height="10" rx="1"/><path d="M9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4"/>',
  mem: '<rect x="3" y="7" width="18" height="10" rx="1"/><path d="M7 11v2M11 11v2M15 11v2"/>',
  disco: '<path d="M4 15l2-9h12l2 9"/><rect x="3" y="15" width="18" height="5" rx="1"/>',
  persona: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="10" r="3"/><path d="M6.5 18.5c1.2-2 3.2-3 5.5-3s4.3 1 5.5 3"/>',
  mano: '<path d="M8 13V5a1.5 1.5 0 0 1 3 0v6M11 11V4a1.5 1.5 0 0 1 3 0v7M14 11V5a1.5 1.5 0 0 1 3 0v8c0 4-2.5 7-6 7-3 0-4.5-2-6-5l-1.5-3a1.5 1.5 0 0 1 2.6-1.5L8 13"/>',
  prohibido: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
  puerta: '<path d="M5 21V4h11v17M3 21h18M13 12h.5"/>',
  llave: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/>',
  moneda: '<circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.5-1-1.6-1.5-3-1.5-1.7 0-3 .9-3 2.2 0 3 6 1.6 6 4.6 0 1.3-1.3 2.2-3 2.2-1.4 0-2.6-.6-3-1.6M12 6v2M12 16v2"/>',
  ruta: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.5 6H15a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h6.5"/>',
  hucha: '<path d="M5 11a7 6 0 0 1 13-2h2v4h-2a7 6 0 0 1-3 4v3h-3v-2H9v2H6v-3.5A6 6 0 0 1 5 11z"/><path d="M10 7.5h4"/>',
  servidor: '<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  copia: '<path d="M4 7h11v13H4z"/><path d="M9 4h11v13"/>',
};
function icono(n, w){ return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w || 1.8}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONOS[n]}</svg>`; }
