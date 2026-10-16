# Pre-mortem y red team del colector Dokploy (fase 3.2) — 2026-10-16

Diseño: `plans/261013-2350-sqlite-historico/fase3-2-dokploy-diseno.md`. Contrastado con el código real; rutas relativas a `infobar/`. Sin claves, IPs ni dominios reales.
Gravedad: **A** = el diseño falla o filtra tal cual; **M** = fallo probable en uso real; **B** = endurecimiento. «Cambio» = lo que hay que añadir al diseño.

## Lo que el código ya da (verificado)
- `/ajustes` solo devuelve `clave_guardada` (bool) y la cuenta (`servidor_local.py:356-369`); `/clave` no repite la clave y su log lleva el nombre del servidor, no el valor (`:452-469`). La clave entra por stdin de `security -i` (`claves.py:30-34`) y sale por pipe de `-w` (`:26`): nunca en argv.
- No existe ningún cliente HTTP para paneles. El único HTTP saliente es `medir_web` (`servidores_externo.py:65-91`): IP fijada tras `ip_publica`, sin redirecciones, `http.client` directo (ignora variables de proxy). Reutilizable, pero con trampas (R1, R2, R3).
- `leer_clave` solo lo llaman `/ajustes` y `cuota_nan`; `borrar_clave` solo `/clave`. Nadie borra la clave al quitar el servidor (R9).

## Pasada 1 — Pre-mortem (a 3 meses: avisos de copia que no son la realidad)

**P1 [A] «Sin copia buena en 48 h» da falsos avisos con copias semanales o mensuales.** El umbral es fijo (diseño, Avisos) y la programación (`schedule`) solo sale por `backup.one`, que está prohibido. Una BD con copia semanal estaría «antigua» 5 días de cada 7 y avisaría cada vez.
Cambio: aprender la cadencia de lo observado. En `copias` guardar `ultima_ok`, `previa_ok` y `intervalo_s` (mediana móvil de los últimos huecos entre `done`). Umbral = `max(48 h, 2,5 × intervalo)`. Con una sola ejecución `done` conocida, umbral 8 días y nunca antes de la segunda. Regla en `tests`: serie semanal sin aviso, serie diaria con hueco de 3 días con aviso.

**P2 [A] «Sin ninguna copia y vista hace > 24 h» avisa de por vida de cada BD que el dueño nunca quiso copiar** (cachés, BD de pruebas). Sin ejecuciones no se distingue «no configurada» de «desactivada» de «nunca le tocó».
Cambio: la notificación solo para recursos que **tuvieron** una `done` y la perdieron. «Nunca tuvo copia» se muestra en la pestaña (informativo, sin notificación) y entra en el aviso solo si el dueño marca ese recurso como «debe tener copia» (lista en `servidores.json`, por id hash, como el silencio por servidor). Así «servicio nuevo» y «no configurada» no fabrican avisos.

**P3 [A] El aviso nuevo no existe para `servidores_avisos`, y fallará de dos formas.** (a) `CLAVE` (`servidores_avisos.py:45`) lista las reglas a mano: `leer()` (`:86`) descarta en silencio toda entrada con otro nombre, así que cada pasada recrea el estado desde cero: o nunca confirma o avisa en cada pasada. (b) `TEXTO[...]`/`CLASE[...]` (`:27-33`, `:211`) lanzan `KeyError` si falta la regla; `vigilar()` lo traga (`:264`) y **mueren todos los avisos de servidores** con un solo `log`.
Cambio: construir `CLAVE` con `"|".join(REGLAS)`; test que exige `set(REGLAS)==set(TEXTO)==set(CLASE)`; reglas `copia_antigua`, `despliegue_fallido` y `panel_sin_lectura` (P8) en las tres tablas.

**P4 [A] Tarjeta nueva = modo incidencia permanente.** `estados()` toma como «viva» toda tarjeta que no esté en `NO_VIVAS` (`servidores_ciclo.py:97-102`) y el estado vivo ámbar/rojo pasa la lectura SSH a 60 s (`:54-60`). Una tarjeta de copias o despliegues, con P1/P2, dejaría el servidor en ámbar y el punto de la barra en ámbar para siempre, martilleando SSH.
Cambio: añadir las tarjetas del panel a `NO_VIVAS` y a la lista de `peor(...)` de `estados` solo como información; el panel nunca decide `estado_vivo`. Test: un panel con todo mal no cambia `estado_vivo`.

**P5 [A] Un panel lento rompe el ciclo SSH y fabrica «sin conexión».** `recoger_uno` (`servidores_ciclo.py:72-92`) es secuencial y el ciclo espera `ceil(n/4)·32 s` (`:176-186`); lo que no termina se marca `{"error":"timeout","estado":"rojo"}` y se pierde la lectura SSH buena. Cinco GET con 5-10 s cada uno consumen el margen (SSH 20 s + puertos). Con `error` y `ok_en>0`, `_desde_cache` (`servidores_avisos.py:165`) dispara `sin_conexion` (crítica) por un fallo del panel.
Cambio: el panel se lee **fuera de `recoger_uno`**, en su propio hilo/calendario (como `webs_sondeo`), con plazo total propio (p. ej. 12 s para las cinco rutas) y escribe `entrada["panel"]` bajo `_bloqueo(rutas.SERVIDORES)` con el patrón de `reaplicar_webs` (`:106-136`). Sus errores van a `entrada["panel"]["error"]`, nunca a `entrada["error"]`/`estado`. Cadencia fija de 5 min, **sin** seguir el modo incidencia ni gastarse con «Refrescar» más de 1 vez/30 s; 401 espera 30 min como `ESPERA_TRAS_RECHAZO` (`:27`).

**P6 [A] Copias sin id de configuración: dos destinos se tapan entre sí.** `overview.backups` no trae id de configuración (investigación §1c). Una BD con copia a dos destinos, uno roto, queda «con copia buena» por el otro; y las de varias BD de un compose se mezclan.
Cambio: la clave del recurso-copia es hash de `(serviceOwnerId, kind, destinationId, databaseType, backupType)` y se evalúa cada una; si falta alguno de esos campos, el recurso se descarta y se cuenta (`descartar("panel_copia_sin_identidad")`), no se junta con otros.

**P7 [A] Contrato roto o alcance recortado se lee como «no hay copias».** (a) Miembro sin servicios asignados (o al que quitan uno) recibe `[]` válido. (b) Un Dokploy actualizado renombra campos: la lista blanca descarta todo y «no hay nada» parece buena noticia. (c) `limit: 500` en copias: con más de ~50 configuraciones (10 ejecuciones cada una) lo más antiguo se corta y una BD diaria se ve «antigua».
Cambio: una lectura **solo vale** si cumple: tipo JSON esperado, ≥ 80 % de filas válidas, y, si `services` viene vacío o cae > 50 % respecto a la última lectura buena, queda como «alcance cambiado» (no se evalúa, se conserva lo anterior, la pestaña lo dice). Si `backups` trae exactamente 500 filas, los recursos sin filas pasan a «no sé». Versión: ≥ 0.30.0 y, si es mayor que la probada, se lee igual pero la pestaña avisa «versión no probada». Estados fuera del conjunto cerrado (`queued`, etc.) son «no sé», no `error`.

**P8 [A] El monitor puede morir sin que nadie se entere.** Clave caducada (Dokploy permite caducidad), rotada o revocada → 401 → «no se reintenta» (diseño) → las copias dejan de vigilarse y nada avisa; los avisos de copia pasan a «no sé» por frescura.
Cambio: regla `panel_sin_lectura` (confirma 6 h, rearme 1 h, no crítica, texto «CostGuard no puede leer el panel de uno de tus servidores») para un panel con clave y sin lectura válida en > 6 h. La pestaña muestra la frescura y el motivo cerrado (`clave rechazada`, `contrato`, `sin red`, `alcance`).

**P9 [M] Semántica de estados de Dokploy mal mapeada.** `status` de un servicio es el del **último despliegue** (`idle|running|done|error`), no el de ejecución; `done` no dice que el contenedor viva. Una ejecución de copia `running` que queda colgada (reinicio del panel) no es `done` ni `error`; `cancelled` no es fallo.
Cambio: textos «último despliegue», nunca «estado»; la salud real sigue saliendo de SSH. «Última buena» = solo `done`; `running` > 6 h cuenta como «sin resultado», no como error ni como buena. Aclarar en la interfaz: «done = la ejecución terminó sin error, no que el archivo sea restaurable».

**P10 [M] `despliegue_fallido` sin antigüedad real.** «Error y más de 10 min» se mide con `lastDeployAt`, que incluye despliegues de hace meses: al dar de alta el servidor (la línea base global ya está en `True`, `servidores_avisos.py:322`) avisaría de apps abandonadas.
Cambio: solo si `lastDeployAt` está entre 10 min y 7 días; más viejo = informativo en la pestaña. Línea base **por panel**: la primera lectura válida de cada panel marca `avisado` en lo que ya estaba mal, como hace la base global.

**P11 [M] Relojes.** `createdAt` es del panel. Un panel adelantado deja `ultima_ok` en el futuro y silencia el aviso; uno atrasado lo dispara. El Mac dormido ya lo cubre `despierta` (`:282`).
Cambio: edad = cabecera `Date` de la propia respuesta − `createdAt` (ambos del panel); sin cabecera, reloj del Mac. Marca > ahora+1 h se descarta y se cuenta (`panel_reloj`), igual que `purgar` (`servidores_recursos.py:173-175`). Formato: ISO con `Z`, con desfase o sin zona (UTC); lo demás se descarta.

**P12 [M] Servidores Dokploy remotos (`serverId`) y paneles compartidos.** Dos entradas de CostGuard con el mismo `panel_url` comparten cuenta (`servidor_local.py:178-182`) y leerían dos veces, duplicando recursos y avisos; el panel de A lista apps que corren en B (otro VPS, quizá sin SSH en CostGuard).
Cambio: validación en `errores_servidores` — `panel_url` único entre servidores. El recurso guarda `serverId`/`serverName`; el árbol agrupa por servidor del panel y no se mezcla con el inventario SSH de A. Las copias de servicios remotos se evalúan igual (la ejecución está en el panel).

**P13 [M] Copias del propio Dokploy y recursos recreados.** `serviceOwnerId=null` + `web-server` es la copia de la BD del panel (la más valiosa y la que un miembro quizá no ve). Recrear un servicio cambia su id: el viejo desaparece de `services` y el nuevo es «servicio nuevo».
Cambio: la copia del panel es un recurso más (`tipo=panel`), solo evaluable si alguna vez fue visible. Un recurso ausente de `services` sale de la evaluación **de inmediato** (no espera 30 d). Gracia de «nuevo» = `max(createdAt del servicio, primera vista) + 48 h`, no solo «primera vista».

**P14 [B] Aviso recurrente y acumulado.** Con N copias antiguas a la vez se manda un solo «N avisos…» (bien), pero rearme 30 min con copia semanal oscilante repite cada enfriamiento.
Cambio: `copia_antigua` = (confirma 1 h, rearme 6 h, enfriamiento 24 h, no crítica); `despliegue_fallido` = (600 s, 1800 s, 12 h, no crítica). Tope hora existente (`TOPE_NO_CRITICAS_HORA=3`) se mantiene.

## Pasada 2 — Red team (panel o intermediario hostil, servidor comprometido, fuga de clave)

**R1 [A] Fuga de la clave por el mensaje de error.** `medir_web` guarda `str(e)[:80]` (`servidores_externo.py:90`). `http.client` mete el **valor** de la cabecera en `ValueError: Invalid header value b'…'` (comprobado con Python). Una clave guardada a mano en el Llavero con un salto de línea (no pasa por `guardar_clave`, `claves.py:138`) acabaría en `servidores.json` y en pantalla. Copiar ese patrón al colector sería el fallo.
Cambio: ningún `str(e)`/`repr(e)` en el módulo del panel; solo un código de lista cerrada (`tls`, `timeout`, `red`, `redireccion`, `http_5xx`, `clave_rechazada`, `contrato`, `demasiado_grande`, `no_json`). Comprobar al leer que la clave cumple `[\x21-\x7e]{1,1024}`; si no, `clave_invalida` sin intentar. Test: clave con `\n` → ninguna salida (caché, log, respuesta) la contiene.

**R2 [A] Plazo total inexistente (slow-loris).** `timeout` de socket es por operación (`servidores_externo.py:37`): un panel que gotea 1 byte cada 4 s cuelga cada GET indefinidamente; `getaddrinfo` (`:20`) no tiene tope.
Cambio: bucle de lectura por trozos con `time.monotonic()` y plazo total por petición (4 s) y por pasada (12 s); DNS dentro del hilo del panel. Un solo hilo por panel: si el anterior sigue vivo, se salta la pasada (sin acumular hilos).

**R3 [A] Respuesta enorme / JSON hostil.** Tope de 2 MB (diseño) con `r.read(2MB+1)`; `Content-Length` miente, chunked sin fin, `Content-Encoding` ajeno. Además `json.loads` acepta `NaN`/`Infinity` (comprobado): un valor no finito acaba en `servidores.json` (`config_y_estado.py:98-104`, `json.dump` lo escribe) y rompe el `JSON.parse` de la interfaz.
Cambio: no enviar `Accept-Encoding`; rechazar `Content-Encoding` distinto de identidad y `Content-Type` que no sea `application/json` (un login HTML de un proxy delante del panel no es «clave rechazada»); `parse_constant` que rechace no finitos; capturar `RecursionError`; la raíz debe ser lista; tope de elementos (services ≤ 2000, domains ≤ 5000, backups ≤ 500) y de longitud de cada cadena (≤ 200). Tope de bytes: `overview.services` trae iconos de ~10 KB, así que 2 MB se queda corto con unos 150 servicios; usar 6 MB solo para esa ruta y 2 MB el resto.

**R4 [A] Nombres hostiles (HTML, RTL, control) y base.** `recursos` solo admite `[A-Za-z0-9_./-]{1,130}` (`servidores_recursos.py:24`, `:101-104`) y **descarta** el recurso: un nombre como «Mi tienda» (con espacio o acento, que Dokploy permite en `name`) dejaría la BD **sin vigilar** y sin aviso. Un servidor comprometido puede además usar ese descarte para esconder una BD.
Cambio: etiqueta = `appName` (slug de Dokploy) validada con esa misma regex; si no vale, etiqueta `recurso-<8 hex del hash>` y se sigue vigilando (nunca se descarta por el nombre). El nombre visible vive solo en la caché (48 h, `podar_nombres`, `servidores_ciclo.py:217-231`) tras quitar categorías Unicode `C*` y controles bidi, ≤ 80 caracteres. La interfaz ya escapa (`desplegable_comun.py:15`); en `servidores.js` usar `textContent`/`h()`, nunca `innerHTML`. Dominios: solo `_DNS` de `servidores.py:19`, puerto 1-65535, `path` ≤ 100 ASCII imprimible.

**R5 [A] Dominios del panel convertidos en sondas desde el Mac.** El diseño «cuelga las webs vigiladas por dominio». Si eso añade las URL a `webs`, un panel hostil hace que el Mac pida a hasta N hosts públicos y fije tráfico.
Cambio: los dominios del panel solo **enlazan** webs que el dueño ya vigila; nunca se añaden a `webs` ni a `webs_sondeo` solos. Tope de 200 dominios por servidor en caché.

**R6 [M] Redirección, certificado y DNS cambiante.** `http.client` no sigue redirecciones: una 3xx solo debe dar `redireccion` y **no** repetir la petición (la clave ya salió una vez al host correcto). El certificado se valida contra el nombre (`servidores_externo.py:33-38`); IP fijada tras la comprobación: bien contra rebinding. Riesgo restante: si alguien secuestra el DNS del panel, el atacante obtiene certificado válido y se lleva la clave en la primera petición.
Cambio (opcional, recomendado): enviar la clave solo si la IP resuelta es una de las del servidor monitorizado (`host_de_ssh` resuelto); si el panel va tras un proxy y no coincide, el dueño lo activa por servidor. Quitar del diseño «o la del servidor» como **excepción a la regla de IP pública**: `url_valida` (`servidores.py:70-87`) ya obliga a nombre DNS público; permitir IPs privadas ampliaría el SSRF sin necesidad. `ip_publica` devuelve solo `ips[0]` (`:26`): sin respaldo IPv4/IPv6, un panel solo con una familia mala falla sin motivo (fallo de disponibilidad, no de seguridad).

**R7 [M] SSRF vía `panel_url`.** Validación existente: solo https, sin credenciales, nombre DNS público, ≤ 300 caracteres, ASCII (`servidores.py:73-86`); IPs literales privadas, metadatos de nube (169.254.x) y CGNAT caen en `ip_no_publica`. Quedan: cualquier puerto, cualquier ruta, y «Probar conexión» como sonda.
Cambio: el cliente usa `scheme://host:puerto` de `panel_url` y **ignora** su ruta (o la exige vacía); las 5 rutas se anteponen a mano. Sin clave guardada **no se hace ninguna petición** (tampoco `settings.health`). «Probar conexión» recibe solo el nombre del servidor (la URL sale de la config, no del cuerpo) y limita 1/30 s y 10/h como `probar` (`servidores_avisos.py:215`). Bloquear puertos < 1024 distintos de 443 no aporta (el host ya es público y ajeno); no se añade.

**R8 [A] Garantía de «solo GET»: cómo probarla y que falle de verdad.** Reutilizar `_HTTPSFijo` deja `request()` abierto a cualquier método, y el módulo `servidores_externo` ya usa `HEAD`.
Cambio: una sola función `_get(ruta)` que llama a `request("GET", ruta, headers=…)` con `ruta` de `RUTAS` (tupla de 5 cadenas), sin cuerpo. Tres tests: (1) AST sobre `panel_dokploy.py`: exactamente una llamada `.request(`, con primer argumento la constante `"GET"`, sin importar `urllib.request`, `requests`, `subprocess`, `os.system`; ninguna cadena `POST|PUT|PATCH|DELETE|HEAD|OPTIONS`; `RUTAS` igual a la lista aprobada (cambiarla exige tocar el test). (2) Servidor TLS de pruebas que registra método, ruta, cabeceras y cuerpo de todo lo que recibe en los escenarios hostiles de R1-R7 y afirma: solo `GET`, solo rutas de `RUTAS`, sin query, sin cuerpo, `x-api-key` solo hacia el host configurado. (3) Cuenta de peticiones: ≤ 5 por pasada. Límite honesto: «GET» es una convención de Dokploy (queries tRPC); las 5 rutas se verificaron en el código [C] y hay que revalidarlas al subir la versión probada.

**R9 [M] Riesgo del miembro que escribe y vida de la clave.** Cualquier proceso del usuario puede leerla con `security find-generic-password -w` sin aviso (la clave la creó `/usr/bin/security`, `claves.py:30`): el texto de Ajustes debe decir «cualquier programa que se ejecute con tu usuario». Además, `ajustes.js:231` aún dice «(solo lectura)» y el diseño dice que se corrige: es un fallo de honestidad hasta entonces.
Cambio: (a) texto: «Clave de miembro de Dokploy: CostGuard solo hace consultas, pero la clave puede desplegar, parar y borrar copias en los servicios asignados». (b) Guía de alta: usuario `costguard` dedicado, rol member, solo con el permiso «Access API/CLI», asignado solo a los servicios que se quieran vigilar, clave con caducidad de 90 días. (c) Mostrar «guardada hace N días» (fecha de creación del elemento del Llavero, sin leer el valor) y avisar a los 80. (d) Borrar la clave del Llavero cuando se quita el servidor o cambia/quita `panel`/`panel_url` (hoy queda huérfana).
Normalizar la cuenta: `_cuenta_panel` usa `netloc` tal cual (`servidor_local.py:181`); `Panel.Ejemplo.com` y `panel.ejemplo.com:443` serían cuentas distintas (clave «perdida» sin explicación). Usar `hostname.lower()` y quitar el puerto 443.

**R10 [A] Datos personales en la base y en las copias previas.** `copia_sin_ips` borra una lista fija de tablas (`servidores_historico.py:221`); `copias` no está y su etiqueta (nombre de BD o de cliente) viviría 30 días en cada copia previa a migrar. Tampoco entra en `CLASES_SERVIDORES` (`:149-169`), `servidores_recursos.purgar` (`:166`), `olvidar` (`:179`) ni `olvidar_recursos` (`servidores_historico.py:484`).
Cambio: tabla `copias` con `servidor_id … REFERENCES servidores(id) ON DELETE CASCADE`, `etiqueta` clasificada `personal`; añadirla a la lista de `copia_sin_ips`, a `purgar` (retirados e inactivos 30 d), a `olvidar` y a `olvidar_recursos`. El test de clasificación de columnas ya obliga a lo primero. La caché `entrada["panel"]` entra en `podar_nombres` (48 h). Nada de nombres en `log` ni en notificaciones (ya genéricas).

**R11 [M] `meta` de descartes.** `descartar` acepta cualquier motivo (`servidores_recursos.py:30-33`): si algún motivo saliera de datos del panel, se podría llenar `meta`.
Cambio: motivos del panel como constantes (`panel_campo`, `panel_estado`, `panel_reloj`, `panel_copia_sin_identidad`, `panel_truncado`, `panel_nombre`); un test comprueba que `descartar` solo recibe elementos de ese conjunto.

**R12 [B] Servidor comprometido.** Controla el panel: puede fabricar `done` y esconder una copia ausente o ocultar un despliegue fallido. Los avisos son para accidentes, no para un adversario con el servidor; documentarlo en una línea en la guía, no hay mitigación razonable desde el cliente.

## Descartado (no es real o ya está cubierto)
- Clave en `argv`/`ps`: no; la entrada va por stdin y la salida por pipe. Clave en `/ajustes`, `/config.json` o respuestas de `/clave`: no (listas cerradas y `sin_secretos`).
- Proxy del sistema reenviando la clave: `http.client` directo no lee `HTTP_PROXY`.
- Inyección de cabeceras por la clave: `guardar_clave` solo admite ASCII visible sin comillas ni barras; el riesgo real es solo R1 (valor puesto a mano).
- Dos ciclos solapados contra el panel: el candado `_EN_MARCHA` ya existe; el hilo del panel necesita el suyo (P5).
- «Dokploy v0.30 cambia solo `status`»: cubierto por P7/P9.

## Orden de trabajo recomendado
1. Antes de escribir código: P3, P4, P5, R1, R2, R3, R8 (estructura del colector y sus tests). 2. P1, P2, P6, P7, P8 (modelo de copias y avisos). 3. R4, R5, R9, R10 (datos, texto y borrado). 4. El resto.
Tests de aceptación a añadir al diseño: copia semanal sin aviso; panel vacío/recortado sin aviso; clave con salto de línea sin fuga; panel que gotea sin bloquear SSH ni marcar `sin_conexion`; nombre con espacios/RTL sin perder el recurso; `copias` ausente de `copia_sin_ips` falla el test.

Status: DONE
