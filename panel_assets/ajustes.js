/* Pestaña Ajustes: edita config.json por /ajustes, /guardar y /clave del servidor local.
   Todo se construye con el DOM (nunca innerHTML con datos): los nombres de proyecto vienen de los logs. */
(function(){
  let A = null, enAjustes = false;
  const $ = id => document.getElementById(id);
  const fmt = v => { v = +v || 0; const u = ["","K","M","B"]; let i = 0; while (v >= 1000 && i < 3) { v /= 1000; i++; } return (i ? v.toFixed(1) : v) + (u[i] ? " " + u[i] : ""); };
  function h(tag, attrs, ...hijos){
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v; else if (k.startsWith("on")) e[k] = v; else if (v === true) e.setAttribute(k, ""); else if (v !== false && v != null) e.setAttribute(k, v);
    }
    for (const c of hijos.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(c));
    return e;
  }
  const entero = v => Math.max(0, parseInt(String(v).replace(/[\s._]/g, ""), 10) || 0);
  const lista = t => t.split(",").map(x => x.trim()).filter(Boolean);
  const post = (ruta, cuerpo) => fetch(ruta, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(cuerpo)})
    .then(r => r.ok || r.status === 400 || r.status === 409 || r.status === 429 ? r.json() : r.text().then(t => ({ok: false, error: t || ("HTTP " + r.status)})));

  let enHistorico = false;
  window.vista = function(v){
    enAjustes = v === "ajustes"; enHistorico = v === "historico";
    $("vista-panel").hidden = enAjustes || enHistorico; $("vista-ajustes").hidden = !enAjustes; $("vista-historico").hidden = !enHistorico;
    $("nav-panel").classList.toggle("on", !enAjustes && !enHistorico); $("nav-ajustes").classList.toggle("on", enAjustes);
    $("nav-historico").classList.toggle("on", enHistorico);
    history.replaceState(null, "", enAjustes ? "#ajustes" : enHistorico ? "#historico" : location.pathname);
    if (enAjustes) cargar();
    if (enHistorico && window.cargarHistorico) window.cargarHistorico();
  };
  window.CG = {h, fmt, post};

  function cargar(){
    const dest = $("vista-ajustes");
    fetch("/ajustes").then(r => { if (r.status === 401) throw new Error("la sesión ha caducado o no existe (se reinició la barra o pasaron 12 h)"); if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }).then(d => {
      A = d; A.config.proveedores = A.config.proveedores || []; A.config.aliases = A.config.aliases || {}; A.config.proyectos = A.config.proyectos || {};
      dest.replaceChildren(...pinta());
    }).catch(e => dest.replaceChildren(h("section", {}, h("p", {}, "No pude leer los ajustes: " + e.message + ". Ábrelo desde el icono de la barra de menús (hace falta su sesión), no como fichero ni con una URL escrita a mano."))));
  }

  /* fila de clave en el Llavero: `cuerpo` identifica la cuenta ({proveedor} o {cuenta}); `info` lleva clave_guardada */
  function filaClave(cuerpo, info, etiqueta){
    const msg = h("span", {class: "msg"});
    const clave = h("input", {type: "password", autocomplete: "off", placeholder: info.clave_guardada ? "•••••••• (guardada; escribe otra para cambiarla)" : etiqueta});
    const quitar = h("button", {hidden: !info.clave_guardada, onclick: () => envia(true)}, "Quitar");
    const estado = h("span", {class: "chip " + (info.clave_guardada ? "ok" : "")}, info.clave_guardada ? "● clave en el Llavero" : "sin clave");
    function envia(borrar){
      if (!borrar && !clave.value.trim()) { msg.className = "msg err"; msg.textContent = "Pega primero la clave."; return; }
      post("/clave", Object.assign({}, cuerpo, {clave: clave.value.trim(), borrar})).then(j => {
        msg.className = "msg " + (j.ok ? "ok" : "err"); msg.textContent = j.ok ? (borrar ? "Clave borrada." : "Clave guardada.") : j.error;
        if (j.ok) { clave.value = ""; info.clave_guardada = !borrar; quitar.hidden = borrar; estado.className = "chip " + (borrar ? "" : "ok"); estado.textContent = borrar ? "sin clave" : "● clave en el Llavero"; }
      }).catch(e => { msg.className = "msg err"; msg.textContent = String(e); });
    }
    return {estado, fila: A.keychain ? h("div", {class: "clave"}, clave, h("button", {onclick: () => envia(false)}, "Guardar clave"), quitar, msg)
      : h("p", {class: "ayuda"}, "El Llavero solo existe en macOS: usa la variable de entorno del proveedor.")};
  }

  function proveedor(p){
    const info = A.proveedores.find(x => x.nombre === p.nombre) || {};
    const campo = (et, input) => h("label", {}, et, input);
    const num = (k) => h("input", {class: "num-in", "data-k": k, value: p[k] || 0, inputmode: "numeric"});
    const cuotas = h("textarea", {rows: 4, "data-k": "cuotas_mes", placeholder: "modelo = tokens, uno por línea"},
      Object.entries(p.cuotas_mes || {}).map(([m, v]) => m + " = " + v).join("\n"));
    const k = filaClave({proveedor: p.nombre}, info, "API key del proveedor");
    const api = info.api_soportada
      ? h("label", {style: "flex-direction:row;align-items:center;gap:8px;text-transform:none;letter-spacing:0;color:var(--texto)"},
          h("input", {type: "checkbox", "data-k": "uso_api", checked: !!p.uso_api}), "Leer el consumo real por API (cuota en vivo)")
      : h("span", {class: "chip aviso"}, "consumo por API aún no soportado: se mide con los logs");
    return h("div", {class: "prov", "data-nombre": p.nombre},
      h("div", {class: "prov-cab"}, h("strong", {}, p.nombre), k.estado, api),
      h("div", {class: "campos"},
        campo("Modelos que cuentan (separados por coma)", h("input", {class: "ancho", "data-k": "modelos", value: (p.modelos || []).join(", ")})),
        campo("Límite 5 h (tokens)", num("limite_5h_tokens")), campo("Límite semanal", num("limite_semana_tokens")), campo("Límite mensual", num("limite_mes_tokens")),
        info.api_soportada ? campo("Cuotas mensuales por modelo", cuotas) : null),
      k.fila);
  }

  /* Alta guiada: comprueba el servidor (solo lectura) y ofrece instalar con tu clave SSH o hacerlo en el Terminal. La contraseña NUNCA pasa por esta página.
     Todo lo que pinta viene de mensajes cerrados del servidor local, aun así va por el DOM. */
  function altaGuiada(){
    const nombre = h("input", {class: "ancho", placeholder: "produccion", maxlength: 40}), destino = h("input", {class: "ancho", placeholder: "root@203.0.113.10"});
    const puerto = h("input", {class: "num-in", placeholder: "22", inputmode: "numeric"});
    const salida = h("div", {style: "margin-top:10px"});
    let id = null, reloj = null;
    const parar = () => { if (reloj) { clearTimeout(reloj); reloj = null; } };
    const boton = (texto, f, extra) => h("button", Object.assign({onclick: f}, extra || {}), texto);
    const envia = (accion, cuerpo) => { parar(); return post("/servidores/alta/" + accion, cuerpo).then(r => { if (r.id) id = r.id; dibuja(r); if (r.estado === "en_curso") sondea(); }).catch(e => dibuja({ok: false, mensaje: "No se pudo hablar con la barra: " + e})); };
    function sondea(){
      parar();
      reloj = setTimeout(() => fetch("/servidores/alta/estado?id=" + encodeURIComponent(id)).then(r => r.json()).then(r => { dibuja(r); if (r.estado === "en_curso") sondea(); }).catch(() => {}), 1000);
    }
    function recarga(){
      leer();
      fetch("/ajustes").then(r => r.json()).then(d => { A.config.servidores = (d.config && d.config.servidores) || []; A.config.servidores_rev = d.config && d.config.servidores_rev; $("vista-ajustes").replaceChildren(...pinta()); });
    }
    function terminal(r){
      if (!r.linea_terminal) return [];
      return [h("p", {class: "ayuda"}, "O hazlo en el Terminal de tu Mac (ahí es donde tecleas tu contraseña, no aquí):"),
              h("pre", {style: "user-select:all;white-space:pre-wrap;word-break:break-all"}, r.linea_terminal),
              h("div", {}, boton("Copiar la línea", () => navigator.clipboard && navigator.clipboard.writeText(r.linea_terminal)), " ",
                boton("Comprobar conexión", () => envia("comprobar", {id})))];
    }
    function dibuja(r){
      parar();
      const msg = h("p", {class: "msg " + (r.estado === "hecho" ? "ok" : (r.estado === "error" || r.ok === false || r.estado === "no_soportado") ? "err" : "")}, r.mensaje || "");
      const piezas = [msg];
      if (r.estado === "huella") {
        piezas.push(h("p", {}, "Huella del servidor: ", h("code", {}, r.huella)),
          h("p", {class: "ayuda"}, "Compárala con la del propio servidor (ejecuta allí: ", h("code", {}, r.comprobar_en_servidor), "). Si no coincide, no la aceptes."),
          h("div", {}, boton("Es esta", () => envia("huella", {id, acepto: true})), " ", boton("No es esta", () => { id = null; salida.replaceChildren(); })));
      } else if (r.estado === "listo") {
        piezas.push(h("div", {}, boton(r.ya_instalado ? "Actualizar con mi clave" : "Instalar con mi clave", () => envia("instalar", {id}))), ...terminal(r));
      } else if (r.estado === "terminal" || r.estado === "error") {
        piezas.push(...terminal(r));
      } else if (r.estado === "hecho") {
        piezas.push(h("div", {}, boton("Ver la lista de servidores", recarga)));
      }
      salida.replaceChildren(...piezas);
    }
    return h("div", {class: "prov"},
      h("div", {class: "prov-cab"}, h("strong", {}, "Dar de alta un servidor")),
      h("p", {class: "ayuda"}, "Comprueba el servidor sin tocarlo y te dice si se puede instalar con tu clave SSH o si hay que hacerlo en el Terminal. Crea en él el usuario «infobar» (solo lectura)."),
      h("div", {class: "campos"}, h("label", {}, "Nombre", nombre), h("label", {}, "Usuario y servidor", destino), h("label", {}, "Puerto SSH", puerto)),
      h("div", {style: "margin-top:8px"}, boton("Comprobar servidor", () => {
        salida.replaceChildren(h("p", {}, "Comprobando…"));
        envia("diagnostico", {nombre: nombre.value.trim(), ssh: destino.value.trim(), puerto: puerto.value.trim() ? (parseInt(puerto.value, 10) || 0) : 22});
      })),
      salida);
  }

  /* Webs detectadas en un servidor (por su proxy): se proponen las que apuntan a ese servidor y TÚ marcas las que quieres vigilar. Nada se vigila sin marcarlo.
     Los dominios vienen del servidor: van siempre por el DOM, y los xn-- se enseñan decodificados junto al original. */
  function detectorWebs(s){
    if (!s.id) return null;
    const salida = h("div", {style: "margin-top:8px"});
    const fila = (x, grupo, marcas) => h("label", {style: "display:flex;gap:8px;align-items:center;text-transform:none;letter-spacing:0;font-size:13px;color:var(--texto)"},
      h("input", {type: "checkbox", onchange: ev => { ev.target.checked ? marcas.add(x.host) : marcas.delete(x.host); }}), h("code", {}, x.host), x.unicode ? h("span", {class: "ayuda", style: "margin:0"}, "(" + x.unicode + ")") : null);
    function envia(cuerpo){
      leer();
      post("/servidores/webs", Object.assign({servidor: s.id}, cuerpo)).then(r => {
        if (!r.ok) { salida.replaceChildren(h("p", {class: "msg err"}, r.mensaje || "No se pudo guardar.")); return; }
        s.webs = r.webs.length ? r.webs : undefined; A.config.servidores_rev = r.servidores_rev;
        $("vista-ajustes").replaceChildren(...pinta());
      }).catch(e => salida.replaceChildren(h("p", {class: "msg err"}, "No se pudo hablar con la barra: " + e)));
    }
    function dibuja(r){
      if (!r.ok) return salida.replaceChildren(h("p", {class: "msg err"}, r.mensaje || "No se pudo consultar."));
      if (r.estado === "desactivada") return salida.replaceChildren(h("p", {class: "ayuda"}, "La detección está desactivada para este servidor."));
      if (r.estado === "sin_dato") return salida.replaceChildren(h("p", {class: "ayuda"}, "El servidor aún no ha enviado la lista de dominios (hace falta la lectura nueva: «Actualizar con mi clave» en el alta, o espera un ciclo)."));
      const marcar = new Set(), quitar = new Set(), piezas = [];
      piezas.push(r.apuntan.length ? h("div", {}, h("strong", {}, "Apuntan a este servidor"), ...r.apuntan.map(x => fila(x, "a", marcar))) : h("p", {class: "ayuda"}, "Ningún dominio detectado apunta a este servidor (o ya los vigilas)."));
      if (r.otros.length) piezas.push(h("details", {}, h("summary", {}, "Apuntan a otro sitio (" + r.otros.length + ")"),
        h("p", {class: "ayuda"}, "Resuelven a una IP distinta (CDN u otro servidor). Un cliente del servidor podría haber puesto aquí el dominio de un tercero: vigílalos solo si son tuyos."),
        ...r.otros.map(x => fila(x, "o", marcar))));
      if (r.vigiladas.length) piezas.push(h("details", {}, h("summary", {}, "Ya vigiladas (" + r.vigiladas.length + ")"), ...r.vigiladas.map(x => fila(x, "v", quitar)),
        h("div", {}, h("button", {onclick: () => envia({quitar: [...quitar]})}, "Dejar de vigilar las marcadas"))));
      const nota = [r.internos.length ? r.internos.length + " apuntan a una IP local o privada (no se ofrecen)" : "", r.sin_resolver.length ? r.sin_resolver.length + " no resuelven" : "",
        r.truncado ? "la lista del servidor estaba recortada (200)" : "", r.edad_s > 900 ? "dato de hace " + Math.round(r.edad_s / 60) + " min" : ""].filter(Boolean).join(" · ");
      if (nota) piezas.push(h("p", {class: "ayuda"}, nota));
      piezas.push(h("div", {}, h("button", {onclick: () => marcar.size ? envia({aceptar: [...marcar]}) : null}, "Vigilar las marcadas")));
      salida.replaceChildren(...piezas);
    }
    return h("div", {}, h("button", {onclick: () => { salida.replaceChildren(h("p", {}, "Buscando…")); fetch("/servidores/webs/detectadas?servidor=" + encodeURIComponent(s.id)).then(r => r.json()).then(dibuja)
      .catch(e => salida.replaceChildren(h("p", {class: "msg err"}, "No se pudo consultar: " + e))); }}, "Buscar webs de este servidor"), salida);
  }

  function servidor(s, i){
    const info = (A.paneles || {})[s.nombre];
    const f = (et, k, ph) => h("label", {}, et, h("input", {class: "ancho", "data-s": i, "data-k": k, value: s[k] == null ? "" : s[k], placeholder: ph || ""}));
    const panel = h("select", {"data-s": i, "data-k": "panel"}, ...[["", "ninguno"], ["dokploy", "Dokploy"], ["easypanel", "Easypanel"]].map(([v, t]) => h("option", {value: v, selected: (s.panel || "") === v}, t)));
    const k = info ? filaClave({cuenta: info.cuenta}, info, "API key del panel (solo lectura)") : null;
    return h("div", {class: "prov", "data-servidor": i},
      h("div", {class: "prov-cab"}, h("strong", {}, s.nombre || "(sin nombre)"), k ? k.estado : null,
        h("button", {onclick: () => { leer(); A.config.servidores.splice(i, 1); $("vista-ajustes").replaceChildren(...pinta()); }}, "Quitar servidor")),
      h("div", {class: "campos"}, f("Nombre", "nombre", "produccion"), f("SSH (usuario@IP)", "ssh", "infobar@203.0.113.10"), f("SSH admin (botón Terminal)", "ssh_admin", "tuusuario@203.0.113.10"),
        f("Puerto SSH", "puerto", "22"), h("label", {}, "Panel", panel), f("URL del panel (https)", "panel_url", "https://dokploy.example.com"),
        h("label", {}, "Webs a medir (una por línea, máx. 100)", h("textarea", {rows: 3, "data-s": i, "data-k": "webs"}, (s.webs || []).join("\n")))),
      detectorWebs(s),
      k ? k.fila : (s.panel ? h("p", {class: "ayuda"}, "Guarda los ajustes y aquí podrás poner la clave del panel.") : null));
  }

  const AVISO_COPIAS = {
    sin_destinatario: ["Sin copias cifradas", "No hay copias automáticas. Actívalas con: python3 ~/.cost-guard/costbar.py --claves-generar RUTA_CLAVE --carpeta CARPETA", true],
    nunca: ["Todavía no hay ninguna copia", "Se hará la primera en unos minutos.", false],
    al_dia: ["Copias al día", "", false],
    atrasada: ["Copias atrasadas", "La última copia buena tiene más de 48 h. Revisa que la carpeta esté montada y mira el último error.", true],
    danada: ["La última copia salió de una base dañada", "Las copias siguen, pero comprueba la base antes de fiarte de ellas.", true],
    reloj: ["El reloj del equipo parece mal", "La última copia tiene fecha futura; no se rota nada hasta que se corrija.", true],
  };
  function copias(c){
    if (!c) return h("p", {class: "vacio"}, "Sin información de copias.");
    const [titulo, texto, alerta] = AVISO_COPIAS[c.estado] || [c.estado, "", true];
    const edad = c.edad_h == null ? "" : (c.edad_h < 48 ? Math.round(c.edad_h) + " h" : Math.round(c.edad_h / 24) + " d");
    return h("div", {},
      h("p", {style: alerta ? "color:#b45309;font-weight:600" : "font-weight:600"}, titulo + (edad ? " · última hace " + edad : "")),
      texto ? h("p", {class: "ayuda"}, texto) : null,
      c.local && c.estado !== "sin_destinatario" ? h("p", {class: "ayuda"}, "La carpeta está en este mismo equipo: si pierdes el equipo, pierdes las copias. Elige otra con --claves-generar --carpeta.") : null,
      c.ensayo_atrasado && c.estado !== "sin_destinatario" ? h("p", {class: "ayuda"}, "Hace tiempo que no pruebas a restaurar: costbar.py --probar-restauracion COPIA.cgx --clave-privada RUTA.") : null,
      c.ultimo_error ? h("p", {class: "ayuda"}, "Último error: " + c.ultimo_error) : null);
  }

  const nivelColor = n => n >= 100 ? "#c0392b" : n >= 80 ? "#d68910" : "";
  function filaPresupuesto(f, st){
    const sel = (k, opciones, actual) => h("select", {"data-pk": k}, ...opciones.map(([v, t]) => h("option", {value: v, selected: v === actual}, t)));
    const barra = st ? h("div", {class: "pista mini"}, h("span", {class: "barra", style: "width:" + Math.min(100, st.pct) + "%" + (nivelColor(st.nivel) ? ";background:" + nivelColor(st.nivel) : "")})) : null;
    const uso = st ? h("span", {class: "pista-texto", style: nivelColor(st.nivel) ? "color:" + nivelColor(st.nivel) + ";font-weight:600" : ""},
      fmt(st.usado) + " de " + fmt(st.tokens) + " · " + st.pct + " %" + (st.visto ? "" : " · sin consumo con ese nombre")) : null;
    return h("tr", {"data-pres": ""},
      h("td", {}, sel("ambito", [["proyecto", "Proyecto"], ["plan", "Plan"]], f.ambito)),
      h("td", {}, h("input", {class: "ancho", "data-pk": "nombre", value: f.nombre || "", list: "nombres-presupuesto", maxlength: 200, placeholder: "nombre mostrado"})),
      h("td", {}, sel("periodo", [["mes", "Mes"], ["semana", "Semana (lunes)"]], f.periodo)),
      h("td", {}, h("input", {class: "num-in", "data-pk": "tokens", value: f.tokens || "", inputmode: "numeric", placeholder: "tokens"})),
      h("td", {}, uso, barra),
      h("td", {}, h("button", {onclick: ev => { leer(); const i = [...ev.target.closest("tbody, table").querySelectorAll("[data-pres]")].indexOf(ev.target.closest("tr"));
        A.config.presupuestos.splice(i, 1); $("vista-ajustes").replaceChildren(...pinta()); }}, "Quitar")));
  }
  function seccionPresupuestos(c){
    const P = A.presupuestos || {disponible: false, motivo: "", estados: []};
    const lista = c.presupuestos || [];          // no se crea si no existe: guardar una lista vacía impediría la migración de los presupuestos antiguos
    const estado = f => (P.estados || []).find(e => e.clave === f.ambito + "|" + f.nombre + "|" + f.periodo);
    const nombres = [...new Set([...Object.keys(A.proyectos || {}).map(k => ((c.proyectos[k] || {}).alias || k)), ...c.proveedores.map(p => p.nombre)])].sort();
    return h("section", {}, h("h2", {}, "Presupuestos"),
      h("p", {class: "ayuda"}, "Un tope de tokens por proyecto o por plan, por mes o por semana natural (de lunes a domingo). Cuentan entrada, salida y caché, como tus cuotas. ",
        "Avisa al llegar al 80 % y al 100 %, una vez por periodo. El nombre es el que ves en el panel (el alias si lo has puesto)."),
      P.disponible ? null : h("p", {class: "ayuda", style: "color:#d68910"}, P.motivo || "Los presupuestos no están disponibles."),
      h("datalist", {id: "nombres-presupuesto"}, ...nombres.map(n => h("option", {value: n}))),
      lista.length ? h("table", {id: "pres-lista"}, h("tr", {}, h("th", {}, "Ámbito"), h("th", {style: "text-align:left"}, "Nombre"), h("th", {}, "Periodo"), h("th", {}, "Tokens"), h("th", {}, "Uso"), h("th", {})),
        ...lista.map(f => filaPresupuesto(f, estado(f)))) : h("p", {class: "vacio", id: "pres-vacio"}, "Aún no hay presupuestos."),
      h("div", {class: "prov-cab", style: "margin-top:8px"}, h("button", {onclick: () => { leer(); (c.presupuestos = c.presupuestos || []).push({ambito: "proyecto", nombre: "", periodo: "mes", tokens: 0}); $("vista-ajustes").replaceChildren(...pinta()); }}, "Añadir presupuesto"),
        h("label", {style: "margin-left:14px"}, h("input", {type: "checkbox", id: "g-avisos", checked: c.avisos_presupuesto !== false}), " Avisar con una notificación del sistema")));
  }

  function pinta(){
    const c = A.config;
    const orig = Object.entries(A.origenes).sort((a, b) => b[1] - a[1]);
    const proy = Object.entries(A.proyectos).sort((a, b) => b[1] - a[1]);
    const nuevoProv = h("input", {placeholder: "Nombre del proveedor nuevo", id: "nuevo-prov"});
    return [
      h("section", {}, h("h2", {}, "Proveedores, planes y claves"),
        h("p", {class: "ayuda"}, "Cuotas en tokens (0 = sin declarar). Las claves se guardan en el Keychain de macOS, nunca en config.json ni en git."),
        ...c.proveedores.map(proveedor),
        h("div", {class: "prov-cab", style: "margin-top:8px"}, nuevoProv, h("button", {onclick: () => {
          const n = nuevoProv.value.trim(); if (!n) return;
          if (c.proveedores.some(p => p.nombre === n)) { alert("Ya existe ese proveedor."); return; }
          leer(); c.proveedores.push({nombre: n, modelos: [], limite_5h_tokens: 0, limite_semana_tokens: 0});
          A.proveedores.push({nombre: n, clave_guardada: false, api_soportada: n.toLowerCase() === "nan"});
          $("vista-ajustes").replaceChildren(...pinta());
        }}, "Añadir proveedor"))),
      h("section", {}, h("h2", {}, "Origen (según el log) → plan"),
        h("p", {class: "ayuda"}, "Lo que dice cada log y a qué plan lo asignas. Se aplica en el siguiente refresco."),
        h("table", {}, h("tr", {}, h("th", {}, "Origen"), h("th", {}, "Tokens 30 d"), h("th", {style: "text-align:left"}, "Plan")),
          ...orig.map(([k, v]) => h("tr", {}, h("td", {}, h("code", {}, k)), h("td", {}, fmt(v)),
            h("td", {}, h("input", {class: "ancho", "data-origen": k, value: c.aliases[k] || "", list: "planes", placeholder: "p. ej. NaN, OpenCode Go"})))))),
      h("datalist", {id: "planes"}, ...c.proveedores.map(p => h("option", {value: p.nombre}))),
      h("section", {}, h("h2", {}, "Proyectos"),
        h("p", {class: "ayuda"}, "Renombra o oculta de las listas. Ocultar no cambia los totales. Los presupuestos se ponen en su propia sección."),
        proy.length ? h("table", {}, h("tr", {}, h("th", {}, "Detectado"), h("th", {}, "Tokens 30 d"), h("th", {style: "text-align:left"}, "Nombre a mostrar"), h("th", {}, "Ocultar")),
          ...proy.map(([k, v]) => { const e = c.proyectos[k] || {};
            return h("tr", {}, h("td", {}, h("code", {}, k)), h("td", {}, fmt(v)),
              h("td", {}, h("input", {class: "ancho", "data-proy": k, "data-c": "alias", value: e.alias || ""})),
              h("td", {}, h("input", {type: "checkbox", "data-proy": k, "data-c": "oculto", checked: !!e.oculto}))); }))
          : h("p", {class: "vacio"}, "Aún no hay proyectos detectados.")),
      seccionPresupuestos(c),
      h("section", {}, h("h2", {}, "Servidores (VPS)"),
        h("p", {class: "ayuda"}, "Monitor por SSH de solo lectura: CPU, memoria, disco, servicios, contenedores, intentos de acceso, fail2ban, puertos abiertos y protección. Para dar de alta uno nuevo usa el formulario de abajo (o, a mano, ", h("code", {}, "infobar/remoto/alta-servidor.sh usuario@IP"), "). Guía: docs/guia-servidores.md."),
        ...(c.servidores || []).map(servidor),
        altaGuiada(),
        h("div", {class: "prov-cab", style: "margin-top:8px"}, h("button", {onclick: () => { leer(); (c.servidores = c.servidores || []).push({nombre: "", ssh: ""}); $("vista-ajustes").replaceChildren(...pinta()); }}, "Añadir servidor")),
        h("div", {class: "campos", style: "margin-top:10px"},
          h("label", {}, "IPs conocidas (coma): una sesión desde otra sale en ámbar", h("input", {class: "ancho", id: "g-ips", value: (c.ips_conocidas || []).join(", ")})),
          h("label", {}, "Revisión normal (s)", h("input", {class: "num-in", id: "g-int-n", value: c.intervalo_normal_s || "", placeholder: "300", inputmode: "numeric"})),
          h("label", {}, "Revisión en incidencia (s)", h("input", {class: "num-in", id: "g-int-i", value: c.intervalo_incidencia_s || "", placeholder: "60", inputmode: "numeric"})))),
      h("section", {}, h("h2", {}, "Copias cifradas"),
        h("p", {class: "ayuda"}, "Copias diarias de la base de consumo, cifradas con una clave pública. La clave privada no está en este equipo."),
        copias(A.copias)),
      h("section", {}, h("h2", {}, "General"),
        h("div", {class: "campos"},
          h("label", {}, "Alerta de ritmo por hora (tokens)", h("input", {class: "num-in", id: "g-hora", value: c.limite_hora_tokens || 0, inputmode: "numeric"})),
          h("label", {}, "Modelos a ignorar (coma)", h("input", {class: "ancho", id: "g-ignorar", value: (c.ignorar || []).join(", ")})))),
      h("div", {class: "guardar"}, h("button", {class: "primario", onclick: guardar}, "Guardar ajustes"), h("span", {id: "msg-guardar", class: "msg"}))
    ];
  }

  /* vuelca lo escrito en el formulario a A.config */
  function leer(){
    const c = A.config, raiz = $("vista-ajustes");
    raiz.querySelectorAll(".prov").forEach(div => {
      const p = c.proveedores.find(x => x.nombre === div.dataset.nombre); if (!p) return;
      div.querySelectorAll("[data-k]").forEach(i => {
        const k = i.dataset.k;
        if (k === "modelos") p.modelos = lista(i.value);
        else if (k === "uso_api") p.uso_api = i.checked ? "nan" : "";
        else if (k === "cuotas_mes") { p.cuotas_mes = {}; i.value.split("\n").forEach(l => { const m = l.split("="); if (m.length === 2 && m[0].trim()) p.cuotas_mes[m[0].trim()] = entero(m[1]); }); }
        else p[k] = entero(i.value);
      });
    });
    // los alias de orígenes que ya no salen en los logs (no hay fila) se conservan tal cual
    const al = Object.assign({}, c.aliases);
    raiz.querySelectorAll("[data-origen]").forEach(i => { if (i.value.trim()) al[i.dataset.origen] = i.value.trim(); else delete al[i.dataset.origen]; }); c.aliases = al;
    const py = Object.assign({}, c.proyectos); raiz.querySelectorAll("[data-proy]").forEach(i => { const e = py[i.dataset.proy] = py[i.dataset.proy] || {};
      e[i.dataset.c] = i.dataset.c === "oculto" ? i.checked : (i.dataset.c === "alias" ? i.value.trim() : entero(i.value)); });
    c.proyectos = py;
    if ($("pres-lista")) {        // se conservan también las filas a medio rellenar: "Quitar" borra por posición; se descartan al guardar
      c.presupuestos = [...raiz.querySelectorAll("[data-pres]")].map(tr => {
        const v = k => tr.querySelector('[data-pk="' + k + '"]').value;
        return {ambito: v("ambito"), nombre: v("nombre").trim(), periodo: v("periodo"), tokens: entero(v("tokens"))};
      });
    }
    if ($("g-avisos")) c.avisos_presupuesto = $("g-avisos").checked;
    if (c.servidores) {
      raiz.querySelectorAll("[data-servidor]").forEach(div => {
        const s = c.servidores[+div.dataset.servidor]; if (!s) return;
        div.querySelectorAll("[data-k]").forEach(i => {
          const k = i.dataset.k, v = i.value.trim();
          if (k === "webs") { const w = v.split("\n").map(x => x.trim()).filter(Boolean); if (w.length) s.webs = w; else delete s.webs; }
          else if (k === "puerto") { if (v) s.puerto = parseInt(v, 10) || v; else delete s.puerto; }
          else if (v || k === "nombre" || k === "ssh") s[k] = v; else delete s[k];
        });
        if (!s.panel) delete s.panel_url;
      });
    }
    if ($("g-ips")) {
      const ips = lista($("g-ips").value); if (ips.length || c.ips_conocidas) c.ips_conocidas = ips;
      [["g-int-n", "intervalo_normal_s"], ["g-int-i", "intervalo_incidencia_s"]].forEach(([id, k]) => { const v = entero($(id).value); if (v) c[k] = v; else delete c[k]; });
    }
    if ($("g-hora")) { c.limite_hora_tokens = entero($("g-hora").value); c.ignorar = lista($("g-ignorar").value); }
  }

  function guardar(){
    const m = $("msg-guardar"); leer();
    const envio = Object.assign({}, A.config);
    if (envio.presupuestos) envio.presupuestos = envio.presupuestos.filter(f => f.nombre && f.tokens > 0);
    post("/guardar", envio).then(j => {
      if (j.conflicto) {      // un alta (u otra pestaña) cambió la lista de servidores: se trae la actual y se conserva todo lo demás que se haya escrito
        return fetch("/ajustes").then(r => r.json()).then(d => {
          A.config.servidores = (d.config && d.config.servidores) || []; A.config.servidores_rev = d.config && d.config.servidores_rev;
          $("vista-ajustes").replaceChildren(...pinta());
          const m2 = $("msg-guardar"); m2.className = "msg err";
          m2.textContent = "La lista de servidores había cambiado (por ejemplo, por un alta): la he actualizado. Revisa los servidores y vuelve a guardar; lo demás que escribiste se conserva.";
        });
      }
      m.className = "msg " + (j.ok ? "ok" : "err"); if (j.ok && j.servidores_rev) A.config.servidores_rev = j.servidores_rev; m.textContent = j.ok ? "✓ Guardado. Se aplica en el próximo refresco (≤ 1 min)." : "Error: " + (j.error || "?"); })
      .catch(e => { m.className = "msg err"; m.textContent = "Error al guardar: " + e; });
  }

  // el panel se recarga solo cada minuto, salvo que estés editando o mirando el histórico (la recarga volvería al panel)
  setInterval(() => { if (!enAjustes && !enHistorico) location.reload(); }, 60000);
  if (location.protocol === "file:") { $("nav-ajustes").hidden = true; $("nav-historico").hidden = true; }
  else if (location.hash === "#ajustes") vista("ajustes");
})();
