/* Pestaña Servidores: de un servidor, sus webs, sus contenedores y servicios y lo que ha pasado (línea de tiempo). Todo se construye con nodos del DOM (nunca con HTML armado a mano):
   los nombres vienen del servidor. Los estados salen de un mapa cerrado a clases fijas; lo que no se conoce se pinta como «desconocido», nunca como correcto.
   Nada se enseña como bueno si su última lectura es vieja: la respuesta trae la ventana de frescura y cada fila su última lectura. */
(function(){
  const {h, post} = window.CG;
  const $ = id => document.getElementById(id);
  const MAX_FILAS = 500;
  // estado → [clase fija, texto]
  const ESTADO_WEB = {ok: ["sv-ok", "Responde"], mal: ["sv-aviso", "Con error o lenta"], caida: ["sv-mal", "Caída"]};
  const ESTADO_RECURSO = {ok: ["sv-ok", "En marcha"], mal: ["sv-aviso", "Con problemas"], caido: ["sv-mal", "Caído"], reiniciando: ["sv-mal", "Reiniciando"],
                          terminado: ["sv-neutro", "Terminado"], otro: ["sv-neutro", "Otro estado"]};
  const ESTADO_GLOBAL = {verde: ["sv-ok", "verde"], ambar: ["sv-aviso", "ámbar"], rojo: ["sv-mal", "rojo"], gris: ["sv-neutro", "sin dato"], error: ["sv-mal", "con error"]};
  let servidores = [], elegido = null, peticion = 0, temporizador = null, verAltas = false, idosAbierto = false, probando = false, ultimoAviso = null;

  const hace = (ts, ahora) => {
    if (!ts) return "—";
    const s = Math.max(0, ahora - ts);
    return s < 90 ? "hace un momento" : s < 5400 ? "hace " + Math.round(s / 60) + " min" : s < 172800 ? "hace " + Math.round(s / 3600) + " h" : "hace " + Math.round(s / 86400) + " días";
  };
  const hora = ts => new Date(ts * 1000).toLocaleTimeString("es-ES", {hour: "2-digit", minute: "2-digit"});
  const dia = ts => new Date(ts * 1000).toLocaleDateString("es-ES", {weekday: "long", day: "numeric", month: "long"});
  const clase = (mapa, clave) => Object.prototype.hasOwnProperty.call(mapa, clave) ? mapa[clave] : ["sv-neutro", "Desconocido"];
  const celda = (t, extra) => h("td", extra || {}, t);
  const insignia = (cl, texto, titulo) => h("span", {class: "sv-ins " + cl, title: titulo || ""}, texto);

  function disponibilidad(w, k){
    const d = w["disp_" + k], n = w["n_" + k], mal = w["mal_" + k];
    if (d == null || !n) return h("span", {class: "sv-suave"}, "—");
    return h("span", {title: "Sondeos que respondieron, entre " + n + " (" + mal + " con error o lentos). Medido desde este Mac."},
      (d === 100 || d === 0 ? String(d) : (Math.floor(d * 10) / 10).toFixed(1).replace(".", ",")) + " %", h("span", {class: "sv-suave"}, " de " + n), n < 30 ? h("span", {class: "sv-suave"}, " · pocos datos") : null);
  }

  function tablaWebs(d, fresco){
    if (!d.webs.length) return h("p", {class: "vacio"}, "Este servidor no tiene webs vigiladas. Se añaden en Ajustes.");
    const filas = d.webs.map(w => {
      let est;
      if (!w.sondeada) est = insignia("sv-neutro", "Sin sondear todavía");
      else if (!fresco(w.ultima)) est = insignia("sv-neutro", "Sin medir " + hace(w.ultima, d.generado_en).replace("hace ", "desde hace "));
      else { const [cl, tx] = clase(ESTADO_WEB, w.estado); est = insignia(cl, tx); }
      const cert = w.cert_dias == null ? "—" : w.cert_dias + " d";
      const certCl = w.cert_dias == null ? "" : w.cert_dias <= 7 ? "sv-mal" : w.cert_dias <= 14 ? "sv-aviso" : "";
      return h("tr", {}, celda(h("span", {title: w.url, class: "sv-nombre"}, w.url.replace(/^https?:\/\//, "").replace(/\/$/, ""))), celda(est),
        celda(w.sondeada && w.codigo != null ? String(w.codigo) : "—"), celda(w.sondeada && w.ms != null ? w.ms + " ms" : "—"),
        celda(w.sondeada ? disponibilidad(w, "24h") : "—"), celda(w.sondeada ? disponibilidad(w, "7d") : "—"), celda(w.sondeada ? disponibilidad(w, "30d") : "—"),
        celda(h("span", {class: certCl}, cert)));
    });
    return h("div", {class: "sv-tabla"}, h("table", {class: "sv"}, h("thead", {}, h("tr", {}, ...["Web", "Estado", "Código", "Respuesta", "Últimas 24 h", "7 días", "30 días", "Certificado"].map(t => h("th", {}, t)))), h("tbody", {}, ...filas)),
      h("p", {class: "ayuda"}, "La disponibilidad es el porcentaje de sondeos que respondieron (los de «error o lenta» cuentan como respuesta) y solo mide mientras este Mac está encendido: no es un «uptime»."));
  }

  function agrupa(recursos){
    const prefijos = {};
    for (const r of recursos) if (r.tipo === "servicio" && r.nombre.includes("_")) { const p = r.nombre.split("_")[0]; prefijos[p] = (prefijos[p] || 0) + 1; }
    const grupos = new Map();
    for (const r of recursos) {
      let g = "Sueltos", corto = r.nombre;
      if (r.tipo === "compose" && r.nombre.includes("/")) { g = r.nombre.split("/")[0]; corto = r.nombre.slice(g.length + 1); }
      else if (r.tipo === "servicio" && r.nombre.includes("_") && prefijos[r.nombre.split("_")[0]] >= 2) { g = r.nombre.split("_")[0]; corto = r.nombre.slice(g.length + 1); }
      if (!grupos.has(g)) grupos.set(g, []);
      grupos.get(g).push(Object.assign({}, r, {corto}));
    }
    return grupos;
  }

  function filaRecurso(r, d, fresco){
    let est, reinicios = r.reinicios == null ? "—" : String(r.reinicios);
    if (r.activo && fresco(r.ultima)) { const [cl, tx] = clase(ESTADO_RECURSO, r.estado); est = insignia(cl, tx); }
    else if (r.activo) est = insignia("sv-neutro", "Sin lectura " + hace(r.ultima, d.generado_en).replace("hace ", "desde hace "));
    else if (r.visto_n > 0 && fresco(r.ultima)) est = insignia("sv-neutro", "Pendiente de confirmar");
    else est = insignia("sv-neutro", "Ya no está · visto " + hace(r.ultima, d.generado_en));
    return h("tr", {}, celda(h("span", {class: "sv-nombre", title: r.nombre}, r.corto)), celda(est), celda(h("span", {title: "Reinicios desde que se creó el contenedor"}, reinicios)),
      celda(r.tipo === "compose" ? "Compose" : r.tipo === "servicio" ? "Swarm" : "Contenedor"));
  }

  function bloqueRecursos(d, fresco){
    if (!d.recursos.length) return h("p", {class: "vacio"}, "Todavía no hay contenedores ni servicios leídos (hace falta la lectura de root con Docker).");
    const vivos = d.recursos.filter(r => r.activo || (r.visto_n > 0 && fresco(r.ultima)));
    const idos = d.recursos.filter(r => !vivos.includes(r));
    const gravedad = r => r.activo && fresco(r.ultima) ? ({caido: 0, reiniciando: 0, mal: 1, ok: 3}[r.estado] ?? 2) : 4;
    const piezas = [];
    const grupos = [...agrupa(vivos)].sort((a, b) => (a[0] === "Sueltos") - (b[0] === "Sueltos") || Math.min(...a[1].map(gravedad)) - Math.min(...b[1].map(gravedad)) || a[0].localeCompare(b[0]));
    for (const [g, rs] of grupos) {
      rs.sort((a, b) => gravedad(a) - gravedad(b) || a.corto.localeCompare(b.corto));
      const peor = Math.min(...rs.map(gravedad));
      piezas.push(h("div", {class: "sv-grupo"}, h("div", {class: "sv-grupo-cab"}, h("strong", {}, g), h("span", {class: "sv-suave"}, rs.length + (rs.length === 1 ? " elemento" : " elementos")), peor <= 1 ? insignia(peor === 0 ? "sv-mal" : "sv-aviso", "!", "Hay algo que mirar") : null),
        h("table", {class: "sv sv-rec"}, h("tbody", {}, ...rs.map(r => filaRecurso(r, d, fresco))))));
    }
    if (idos.length) piezas.push(h("details", {open: idosAbierto || null, ontoggle: ev => { idosAbierto = ev.target.open; }}, h("summary", {}, idos.length + (idos.length === 1 ? " que ya no está" : " que ya no están") + " (últimos 30 días)"),
      h("table", {class: "sv sv-rec"}, h("tbody", {}, ...idos.map(r => filaRecurso(Object.assign({}, r, {corto: r.nombre}), d, fresco))))));
    if (d.recursos_truncado) piezas.push(h("p", {class: "ayuda"}, "Hay más de " + d.recursos.length + " elementos: se enseñan los más recientes."));
    piezas.push(h("p", {class: "ayuda"}, "Agrupado según el nombre que da el servidor, no verificado. «Reinicios» cuenta desde que se creó el contenedor; el aviso de bucle sale del evento, no del contador."));
    return h("div", {}, ...piezas);
  }

  function textoEvento(e){
    if (e.origen === "web") {
      if (e.evento === "cayo") return ["sv-mal", "Una web cayó" + (e.codigo ? " (código " + e.codigo + ")" : "")];
      if (e.evento === "volvio") return e.codigo >= 400 ? ["sv-aviso", "Volvió a responder, pero con error " + e.codigo] : ["sv-ok", "Volvió a responder"];
      return ["sv-aviso", "Una web empezó a ir mal" + (e.codigo ? " (código " + e.codigo + ")" : "")];
    }
    const mapa = {aparecio: ["sv-neutro", "Apareció"], desaparecio: ["sv-aviso", "Desapareció"], reinicios_en_bucle: ["sv-mal", "En bucle de reinicios"],
                  inestable: ["sv-aviso", "Inestable (cambia de estado a menudo)"], cambio_estado: e.severidad === "aviso" || e.severidad === "critica" ? ["sv-aviso", "Empeoró"] : ["sv-neutro", "Cambió de estado"]};
    return mapa[e.evento] || ["sv-neutro", "Evento"];
  }

  function lineaTiempo(d){
    const ev = d.eventos.filter(e => verAltas || !(e.origen === "recurso" && e.tipo === "contenedor" && (e.evento === "aparecio" || e.evento === "desaparecio")));
    const control = h("label", {class: "sv-check"}, h("input", {type: "checkbox", checked: verAltas || null, onchange: ev2 => { verAltas = ev2.target.checked; pinta(d); }}), " Mostrar altas y bajas de contenedores sueltos");
    const vigil = d.servidor.vigilado_desde ? "Vigilancia desde " + new Date(d.servidor.vigilado_desde * 1000).toLocaleDateString("es-ES") : "";
    const cobertura = h("p", {class: "ayuda"}, vigil + (vigil ? " · " : "") + "con datos " + d.horas.con_datos + " de las " + d.horas.total + " horas de los últimos 14 días. Un hueco es «sin datos» (Mac apagado o sin red), no «todo bien».");
    if (!ev.length) return h("div", {}, control, h("p", {class: "vacio"}, "Sin eventos en los últimos 14 días."), cobertura);
    // un mismo evento del mismo elemento en el mismo día se junta: «cayó 4 veces»
    const porDia = new Map();
    for (const e of ev) {
      const k = dia(e.ts), clave = [e.origen, e.tipo || "", e.evento, e.severidad || "", e.nombre || "", e.codigo || ""].join("|");
      if (!porDia.has(k)) porDia.set(k, new Map());
      const m = porDia.get(k);
      if (m.has(clave)) m.get(clave).veces++; else m.set(clave, {e, veces: 1});
    }
    const piezas = [];
    for (const [k, m] of porDia) {
      piezas.push(h("div", {class: "sv-dia"}, k));
      for (const {e, veces} of m.values()) {
        const [cl, tx] = textoEvento(e);
        piezas.push(h("div", {class: "sv-evento"}, h("span", {class: "sv-hora"}, hora(e.ts)), insignia(cl, tx), h("span", {class: "sv-nombre", title: e.nombre || ""}, e.nombre ? e.nombre.replace(/^https?:\/\//, "").replace(/\/$/, "") : "(ya olvidado)"),
          veces > 1 ? h("span", {class: "sv-suave"}, "× " + veces) : null));
      }
    }
    if (d.truncado) piezas.push(h("p", {class: "ayuda"}, "Hay más eventos antiguos que no se enseñan (se muestran los " + d.eventos.length + " más recientes)."));
    return h("div", {}, control, ...piezas, cobertura);
  }

  function cabecera(d, fresco){
    const s = d.servidor, partes = [];
    const [cl, tx] = clase(ESTADO_GLOBAL, s.estado);
    partes.push(fresco(s.ok_en) ? h("span", {}, "Estado de la última lectura: ", insignia(cl, tx), " ") : h("span", {}, insignia("sv-neutro", "Sin lectura " + hace(s.ok_en, d.generado_en).replace("hace ", "desde hace ")), " "));
    partes.push(h("span", {class: "sv-suave"}, "Último dato bueno " + hace(s.ok_en, d.generado_en) + " · generado a las " + hora(d.generado_en) + ". El detalle de disco, puertos y protección está en el desplegable de la barra."));
    return h("p", {class: "sv-estado"}, ...partes);
  }

  let aviso = null;
  function probarAviso(boton){
    if (probando) return;
    probando = true; boton.disabled = true;
    post("/servidores/avisos/probar", {servidor: elegido}).then(r => {
      let t, ok = false;
      if (r.espera_s) t = "Espera " + r.espera_s + " s antes de volver a probar.";
      else if (r.motivo === "solo_macos") t = "Las notificaciones de escritorio solo funcionan en macOS: aquí los avisos quedan solo en pantalla.";
      else if (r.ok) { ok = true; t = "Enviada. Si no la ves, revisa Notificaciones de macOS (permiso de la app) y el modo No molestar."; }
      else t = r.motivo === "no_aceptado" ? "macOS no aceptó la notificación: revisa sus permisos de notificaciones." : "No se pudo probar.";
      if (r.apagados && r.apagados.length) t += " Ojo: los avisos reales están apagados " + (r.apagados[0] === "global" ? "en general" : "para este servidor") + " (Ajustes); esta prueba no lo respeta.";
      ultimoAviso = {ok, t};
    }).catch(() => { ultimoAviso = {ok: false, t: "No se pudo hablar con la barra."}; }).finally(() => { probando = false; if (aviso) { aviso.className = "msg " + (ultimoAviso.ok ? "ok" : "err"); aviso.textContent = ultimoAviso.t; } boton.disabled = false; });
  }

  // la barra (selector, actualizar, probar aviso) sale siempre, también con errores: así nunca se queda la pestaña sin forma de cambiar de servidor
  function barra(){
    const selector = h("select", {"aria-label": "Servidor", onchange: ev => { elegido = ev.target.value; ultimoAviso = null; try { sessionStorage.setItem("sv-elegido", elegido); } catch (e) {} carga(); }},
      ...servidores.map(s => h("option", {value: s.id, selected: s.id === elegido || null}, s.nombre)));
    const probar = h("button", {disabled: probando || null, onclick: ev => probarAviso(ev.target)}, "Probar aviso");
    aviso = h("span", {class: "msg" + (ultimoAviso ? (ultimoAviso.ok ? " ok" : " err") : ""), role: "status"}, ultimoAviso ? ultimoAviso.t : "");
    return h("div", {class: "sv-barra"}, selector, h("button", {onclick: () => carga()}, "Actualizar"), probar, aviso);
  }

  function pinta(d){
    const raiz = $("vista-servidores");
    const fresco = ts => ts != null && d.generado_en - ts <= d.ventana_s && d.generado_en - ts >= -60;
    if (!d.disponible) { raiz.replaceChildren(h("section", {}, h("h2", {}, "Servidores"), barra(), h("p", {class: "vacio"}, d.motivo || "Sin datos todavía."))); return; }
    raiz.replaceChildren(h("section", {}, h("h2", {}, "Servidores"), barra(), cabecera(d, fresco)),
      h("section", {}, h("h2", {}, "Webs"), tablaWebs(d, fresco)),
      h("section", {}, h("h2", {}, "Contenedores y servicios"), bloqueRecursos(d, fresco)),
      h("section", {}, h("h2", {}, "Qué ha pasado"), lineaTiempo(d)));
  }

  function mensaje(txt){ $("vista-servidores").replaceChildren(h("section", {}, h("h2", {}, "Servidores"), servidores.length ? barra() : null, h("p", {class: "vacio"}, txt))); }

  function carga(){
    if (!elegido) return;
    const mio = ++peticion;                        // una respuesta tardía de otro servidor no se pinta bajo el nombre del actual
    const pedido = elegido;
    fetch("/servidores/detalle?servidor=" + encodeURIComponent(pedido)).then(r => r.status === 429 ? new Promise(res => setTimeout(res, 2000)).then(() => fetch("/servidores/detalle?servidor=" + encodeURIComponent(pedido))) : r)
      .then(r => { if (r.status === 401) throw new Error("sesion"); if (r.status === 404) throw new Error("desconocido"); if (!r.ok && r.status !== 200) throw new Error("http"); return r.json(); })
      .then(d => { if (mio !== peticion || pedido !== elegido) return; if (d.disponible && d.servidor.id !== pedido) return; pinta(d); })
      .catch(e => { if (mio !== peticion) return;
        if (e.message === "desconocido") {          // el servidor no está entre los válidos de la configuración: se saca de la lista y se pasa al siguiente (no se queda uno atascado)
          servidores = servidores.filter(s => s.id !== pedido);
          try { if (sessionStorage.getItem("sv-elegido") === pedido) sessionStorage.removeItem("sv-elegido"); } catch (e2) {}
          if (servidores.length) { elegido = servidores[0].id; carga(); } else mensaje("Ese servidor ya no está en la configuración.");
          return;
        }
        mensaje(e.message === "sesion" ? "La sesión ha caducado: abre el panel desde el icono de la barra de menús." : "No se pudo leer el detalle ahora; vuelve a intentarlo."); });
  }

  window.cargarServidores = function(){
    fetch("/ajustes").then(r => { if (!r.ok) throw new Error("http"); return r.json(); }).then(a => {
      servidores = ((a.config && a.config.servidores) || []).filter(s => s && /^[0-9a-f]{32}$/.test(s.id || "") && typeof s.nombre === "string");
      if (!servidores.length) { mensaje("Aún no hay servidores. Da de alta uno en Ajustes."); return; }
      let guardado = null; try { guardado = sessionStorage.getItem("sv-elegido"); } catch (e) {}
      elegido = servidores.some(s => s.id === elegido) ? elegido : servidores.some(s => s.id === guardado) ? guardado : servidores[0].id;
      carga();
    }).catch(() => mensaje("No pude leer la lista de servidores. Abre el panel desde el icono de la barra de menús."));
    clearInterval(temporizador);
    temporizador = setInterval(() => { if (!$("vista-servidores").hidden && document.visibilityState === "visible") carga(); else if ($("vista-servidores").hidden) clearInterval(temporizador); }, 60000);
  };
  if (location.protocol !== "file:" && location.hash === "#servidores") window.vista("servidores");
})();
