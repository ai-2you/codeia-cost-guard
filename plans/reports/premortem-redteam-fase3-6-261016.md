# Pre-mortem y red team: pestaña «Servidores» (fase 3.6) — 2026-10-16

Diseño revisado: `plans/261013-2350-sqlite-historico/fase3-6-pestana-servidores-diseno.md`, contrastado con el código. Todo lo de abajo está verificado leyendo
el código; el punto R1 además se reprodujo con una base temporal en el scratchpad. Los ficheros son de `infobar/` salvo indicación.

## Lo que el diseño supone y es cierto
- `seguimiento.lista()` devuelve url, estado (ok/mal/caida), codigo, ms, cert_dias, desde, ultima y disp_24h/7d/30d (None = sin datos) (`servidores_seguimiento.py:136`).
- `recursos` y `eventos` existen con las columnas citadas; los ids son 32 hex (`servidores.py:12`, `servidores_registro.py:13`).
- La etiqueta de recursos y webs está acotada por CHECK de charset (`servidores_historico.py` ESQUEMA_V3/V5): `[A-Za-z0-9_./-]` y 1–130 caracteres en recursos;
  las URL de webs no admiten `< > " ' espacios` ni no ASCII. El HTML, el RTL y los caracteres de control NO pueden llegar a la base. El riesgo real de nombres
  hostiles es de longitud, volumen y engaño semántico, no de XSS.
- Puerta HTTP: Host exacto + Sec-Fetch-Site + sesión en todo; el POST exige application/json y Origin exacto (`servidor_local.py:221,381,401`). Cookie SameSite=Strict.

## Hallazgos (por prioridad)

### A. Alta: la pestaña puede mentir
**A1. Datos viejos presentados como actuales** (`servidores_recursos.py:87-95`, `servidores_inventario.py:98`).
Una lectura con `fuente` distinta de docker, incompleta o con Swarm sin lista de servicios devuelve None sin tocar nada: los recursos activos conservan
estado «ok» y `ultima` antigua para siempre. Si Docker muere o el SSH falla, la pestaña enseñaría todo en verde sin evento alguno. Lo mismo con las webs si el
Mac duerme o pierde red (`webs.ultima` vieja, estado «ok»). El aviso de bucle/web ya trata esto con ventana de frescura (`servidores_avisos.py:192`).
Cambio: `detalle` devuelve por servidor `ok_en`, `intento_en`, el `estado` de la última fila de `muestras`, y por web/recurso su `ultima`. El cliente calcula
frescura con una ventana única (la de avisos: max(900 s, 3×intervalo+60)) y, si se pasa, pinta la fila en gris con «sin lectura desde hace X» en vez del estado.
Cabecera fija: «Contenedores leídos hace X · webs sondeadas hace Y · generado a las HH:MM». Sin esto no se debe publicar la pestaña.

**A2. `activo=0` significa dos cosas y el estado de un inactivo es el último visto** (`servidores_recursos.py:113-119,126-128,154`).
Un recurso nuevo entra con `activo=0` hasta verse en dos lecturas separadas 10 min (aún «no confirmado»), y uno desaparecido también queda `activo=0` con su
último estado (p. ej. «ok»). Mostrar `estado` tal cual pinta de verde algo que ya no existe.
Cambio: tres presentaciones cerradas. activo=1 y fresco: estado. activo=0 y `ultima` reciente y `visto_n>0`: «pendiente de confirmar» (neutro). activo=0 y
`ultima` antigua: «ya no está desde <ultima>» (gris, sin estado). Ordenar lo activo primero, los inactivos plegados («N desaparecidos en los últimos 30 días»).

**A3. «Disponibilidad» cuenta «mal» como disponible y no dice sobre cuántos sondeos** (`servidores_seguimiento.py:131-133`).
Una web con 404 del proxy o respuesta de más de 5 s sale con disponibilidad 100 % junto a un estado «mal». Además solo hay sondeos con el Mac despierto: 100 %
puede ser 3 sondeos, y un corte a las 3:00 con el Mac dormido es invisible.
Cambio: ampliar `lista()` con `n_24h/n_7d/n_30d` (suma ok+mal+caidas) y `mal_*`. Texto: «Responde el 100 % de 12 sondeos (2 con error o lentos)». Con menos de ~30
sondeos en la ventana, mostrar «pocos datos». Rotular «medido desde este Mac», nunca «uptime». None se pinta «—», nunca 0 % ni 100 %.

**A4. Una web configurada puede no aparecer** (`servidores_seguimiento.py:60-62`). `anota_web` descarta URL que no encajen (regex/`url_valida`) o aún sin primer
sondeo: la tabla `webs` no tiene fila y la pestaña enseña menos webs que Ajustes sin pista. Cambio: `detalle` parte de la lista configurada
(`servidores_registro.webs_vigentes`) y marca las que no tienen fila como «sin sondear todavía» (neutro); las filas sin config no se pintan.

**A5. Hay dos fuentes de verdad para «¿está bien?»**. El desplegable usa el estado global verde/ámbar/rojo y sus razones; la pestaña solo ve webs y contenedores.
Todo verde en la pestaña con ámbar en la barra (disco, puertos, protección) se leerá como un error. Cambio: cabecera con el `estado` de la última muestra y una línea
«El resto del estado (disco, puertos, protección) está en el desplegable de la barra» o, mejor, los motivos ya calculados por `servidores_estado`.

**A6. Un servidor comprometido (o un nombre largo) deja ciega la lista de recursos** (R1, ver abajo; efecto de producto: recursos viejos sin pista).

### B. Media: interpretación y ruido
**B1. Reinicios.** `reinicios` es el `RestartCount` acumulado del contenedor actual (max entre réplicas, `servidores_inventario.py:149`); los servicios Swarm no tienen
(NULL) ni salud (NULL) (`:135`). Mostrar «0 reinicios» para NULL, o «37» de hace ocho meses como alarma permanente, engaña. Cambio: NULL como «—»; texto «reinicios
desde que se creó el contenedor»; el bucle se señala solo con el evento `reinicios_en_bucle`, no con el contador.
**B2. Agrupar por proyecto.** No existe columna proyecto: compose ya trae `proyecto/servicio` en la etiqueta (fiable); Swarm solo tiene el nombre del servicio
(`pila_servicio`, heurística); los sueltos solo el nombre. Partir por `-` agruparía mal todo. Cambio: grupo = parte antes de `/` para `tipo=compose`; para `servicio`, prefijo
antes del primer `_` solo si lo comparten ≥2 servicios; el resto en «Sueltos». Nunca partir por `-` ni por `.`. El nombre completo siempre en un `title`. Nombre y grupo
los elige el servidor: un contenedor hostil `pila_x` cae en el grupo de confianza; rotular los grupos como «según el nombre», no como verificados.
**B3. Estados.** «terminado» (salida 0, trabajos puntuales) no es fallo; «caido» incluye contenedores de inicio que acabaron con error; «otro» es creado/pausado.
Cambio: mapa cerrado estado→{texto, clase}; terminado y otro en neutro; caído/reiniciando/mal en rojo/ámbar; ordenar lo peor primero dentro de cada grupo.
**B4. Línea de tiempo con ruido.** `eventos` guarda `aparecio/desaparecio` de contenedores sueltos efímeros (CI, previews, `docker run`) hasta 100 por día y tipo
(`servidores_recursos.py:18`) y `cambio_estado` sin estado origen ni destino (solo severidad: aviso=empeoró, info=otro). `eventos` guarda la clave hash, no la etiqueta.
Cambio: unir con `recursos` por (servidor, tipo, clave) con LEFT JOIN y «recurso olvidado» si falta; por defecto ocultar `aparecio` de tipo `contenedor`
(chip «mostrar altas y bajas»); colapsar eventos repetidos del mismo recurso y día («cayó 4 veces»); agrupar por día; tope de 150 en total y «hay más antiguos». Redactar
`cambio_estado` como «empeoró»/«cambió» según severidad, nunca inventar el estado.
**B5. «Volvió» engañoso.** El evento `volvio` también se emite al pasar de caída a «mal» (`servidores_seguimiento.py:92`), con códigos 404/4xx. Cambio: mostrar el
código: «Volvió a responder (error 404)» y color de «mal», no de recuperado.
**B6. Una línea de tiempo vacía no prueba que no pasara nada**: hay huecos con el Mac apagado y la vigilancia empieza en el primer dato. Cambio: «Vigilancia desde <fecha> · sin datos en N horas de los últimos 14 días»
(sale de `horas_servidor.n` o `muestras`). Sin eso el dueño leerá silencio como salud.
**B7. «Probar aviso» no demuestra que el aviso llegue** (`plataforma.py:84-96`). `osascript` devuelve 0 aunque macOS no muestre la notificación (permiso del
origen desactivado, No molestar). `ok:true` no es «lo has visto». Además no prueba el interruptor global/por servidor (`servidores_avisos.py:266,288`) ni el tope por hora. Cambio: mensaje
«Se ha enviado; si no la ves, revisa Notificaciones y No molestar»; leer `avisos_servidores` y `avisos` del servidor elegido y avisar «los avisos están apagados: los reales no
llegarán». Texto de la prueba distinto de los reales («Prueba de CostGuard: …»). Windows/Linux: `{ok:false, motivo:"solo macOS"}` con 200.
**B8. Selector y estado del cliente.** `app.js`/`ajustes.js:410` recargan la página cada 60 s salvo en Ajustes e Histórico: la pestaña nueva se recargaría sola y volvería a
Panel (el hash `#servidores` solo se restaura para `#historico`, `historico.js:113`). Es un fallo seguro. Cambio: ampliar esa condición, y restaurar `#servidores`.
Además, usar el patrón de token de petición de `historico.js:94,102` y comparar `d.servidor` con el elegido: sin ello, al cambiar de servidor rápido, la respuesta tardía
del anterior se pinta bajo el nuevo nombre. Añadir refresco cada 60 s mientras `document.visibilityState==="visible"` (GET barato) para que «hace X» no envejezca.

### C. Red team (dato hostil, CSRF, DoS)
**R1. Media: un nombre compose largo vacía todo el inventario** (`servidores_inventario.py:139-141`, `servidores_historico.py` etiqueta ≤130). La clave es `proyecto/servicio` y
cada parte puede medir 128 (`NOMBRE`): hasta 257 > CHECK 130. REPRODUCIDO: `IntegrityError`. El `SAVEPOINT` lo deshace y solo cuenta `inventario_error` (`servidores_historico.py:129-138`):
el inventario de ese servidor deja de actualizarse, nada avisa y la pestaña (A1) seguiría en verde. Un servidor comprometido puede cegar el panel con un único contenedor. Es un fallo
previo a la pestaña, pero esta lo hace visible: arreglar en `recursos()` descartando (y contando) el recurso en vez de abortar toda la lectura (o recortando el nombre a 130 con un sufijo hash),
y mostrar en la cabecera «lectura de contenedores rechazada» cuando `ultima` envejezca (A1). Test: compose con 100+100 caracteres.
**R2. Media: la base no está lista (migrando, antigua, corrupta)**. `abrir_base(solo_lectura)` no migra ni comprueba `user_version` (`sqlite_base.py:38-67`). Con una versión < 5 faltan
`webs`/`eventos_web` y la consulta lanza `OperationalError`; una base dañada lanza `DatabaseError`; ambas acaban en 500 (`servidores_api.py:54-56`), contra el criterio 3. Cambio: en el nuevo
endpoint, comprobar `PRAGMA user_version >= 5` y devolver 200 `{disponible:false, motivo}` («el historial se está actualizando, vuelve a abrir en un minuto»); `DatabaseError` →
503 con texto fijo, sin `str(e)`; `sqlite3.OperationalError "locked"` → igual. Test con base v4 y con fichero basura.
**R3. Media: CSRF/POST de «probar aviso».** La puerta existente cubre Host, Sec-Fetch-Site, Content-Type, Origin y sesión. Pero (a) `_cuerpo_json` rechaza cuerpo vacío con 413
(`servidor_local.py:392`): el cliente debe mandar `{}`; (b) la ruta debe añadirse a la lista cerrada de `do_POST:405`; (c) el límite de 10 s debe ser global (no por sesión), con
`time.monotonic()` y un `Lock` no bloqueante tomado ANTES de lanzar `osascript` y fijando la marca al intentar (no al acertar); si no, 20 POST simultáneos lanzan 20 `osascript`
durante 5 s cada uno. Endurecer: 1 cada 30 s y máx. 10 por hora. Responder 429 con `{ok:false, espera_s}`. Un proceso local con cookie ya tiene sesión, así que
el techo es molestia, no escalada. Sin enlaces ni parámetros en el texto del aviso (ya es fijo).
**R4. Baja: enumeración por id.** Los ids son uuid4 y todo exige sesión; la enumeración no es práctica. Lo que sí importa es no distinguir en la respuesta «id válido pero sin
lectura aún» de «id ajeno» con 404 como hace `servidores_lectura.py:60` (SinDatos→404 en `historia`). Cambio: validar el id contra la CONFIG (`servidores_validos`), no contra la base: servidor configurado
sin fila → 200 `{disponible:false, motivo:"aún sin lecturas"}`; id no configurado (incluye retirados, cuyo nombre sigue en `servidores` hasta el mantenimiento) → un único 404 genérico.
No usar `sl.servidores()` para el selector (lista retirados).
**R5. Baja: fuga de nombres a logs.** Los `log()` actuales solo escriben `type(e).__name__`; `/ajustes` devuelve `str(e)[:200]` (`servidor_local.py:353,370`), patrón que NO debe copiarse
(las excepciones SQLite citan tablas/columnas, y las de `json` citan datos). El log del aviso de prueba debe decir solo «aviso de prueba enviado». No auditar nombres (`auditar_ips` ya solo cuenta).
**R6. Baja: volumen.** Con 500 recursos y filas de línea de tiempo (100/día/tipo × 14 días × 5 tipos) el JSON podría pasar de 1 MB. Cambio: `LIMIT 500` en recursos (ya es el máximo), eventos
con `ORDER BY ts DESC LIMIT 150` en CADA tabla antes de mezclar, cortar etiquetas a 130, devolver `truncado:true` y mostrarlo. CSS: `overflow-wrap:anywhere` y `unicode-bidi:plaintext`
en celdas (nombres de servidor del dueño sí pueden llevar RTL/largos, `nombre` solo tiene CHECK de longitud).
**R7. Baja: XSS por DOM.** Seguro por construcción si se usa `h()` con nodos de texto (`ajustes.js:7-14`). Reglas para el diseño: nunca `href` con datos (la URL de la web como texto, o como
enlace solo con `^https?://` + `rel="noopener noreferrer"`), nunca concatenar datos en `class`/`style` (`"chip "+estado` con `estado` del servidor): usar un mapa cerrado estado→clase y descartar lo no
conocido como «desconocido». `h()` asigna `e[k]=v` si la clave empieza por `on`: las claves son del código, mantenerlas así.
**R8. Baja: consultas y semáforo.** WAL: los lectores no bloquean al escritor único; solo retrasan el `wal_checkpoint(TRUNCATE)` mientras dure la lectura (milisegundos con estos límites). No dejar cursores abiertos
mientras se serializa (`fetchall` y cerrar). Las lecturas deben ir en UNA transacción (`BEGIN` diferido) para que webs, recursos y eventos sean coherentes: hoy `lista()` hace 3 SELECT por web (≤100 webs ⇒ 300),
y entre medias el sondeador puede escribir. El semáforo `historiando` (2 plazas) lo comparte el histórico: la pestaña daría 429 si el Histórico está calculando (`servidor_local.py:291`). Cambio:
semáforo propio de 1 plaza para `/servidores/detalle` y reintento automático una vez a los 2 s en el cliente.
**R9. Baja: parámetros.** Copiar el patrón estricto (`set(q)=={"servidor"}`, un valor, `max_num_fields`, regex `^[0-9a-f]{32}$` antes de tocar la base; `keep_blank_values=True` hace que `servidor=` llegue vacío y
se rechace por la regex). `servidores_api.PARAMS` es la lista cerrada por ruta: añadir ahí `/servidores/detalle` en vez de otro `if` suelto en `do_GET`.

### Descartado (no real)
- HTML/RTL/control en etiquetas de recursos o webs: bloqueado por CHECK en la base y por regex en la ingesta.
- Bloqueo del escritor único por lecturas: no aplica en WAL con consultas acotadas.
- CSRF desde otra web: Origin exacto + Content-Type JSON + cookie SameSite=Strict + Sec-Fetch-Site.
- DNS rebinding: `_host_ok` exige Host exacto.

## Cambios concretos al diseño (resumen aplicable)
1. Contrato `detalle`: añadir `servidor:{id,nombre,estado,ok_en,intento_en,vigilado_desde}`, por web `{ultima,n_24h,n_7d,n_30d,mal_*}` y `sondeada:false` para las configuradas sin fila, por recurso `{ultima,activo,visto_n}`,
   `truncado`, y `generado_en`. Error: 200 con `disponible:false` para ausente/migrando/sin lecturas; 503 con texto fijo para dañada/ocupada; 429 por concurrencia; 404 único para id no configurado.
2. Una sola transacción de lectura y conexión `mode=ro` con comprobación de `user_version>=5`; límites por tabla antes de mezclar; ruta registrada en `PARAMS`.
3. Cliente: frescura única, presentaciones cerradas (activo/pendiente/desaparecido/sin dato), mapa estado→clase, token de petición + comprobación de `d.servidor`, refresco cada 60 s visible, y la condición de recarga
   de `ajustes.js:410` ampliada a la vista nueva (más `vista()` generalizada: hoy son dos booleanos fijos, `ajustes.js:21-30`, y `panel.py:91-96` debe incluir el nuevo JS después de `ajustes.js`).
4. POST de prueba: cuerpo `{}`, ruta en `do_POST:405`, cerrojo no bloqueante con marca al intentar, 30 s y 10/h, texto distinto de los reales, advertencia si los avisos están apagados, motivo claro fuera de macOS.
5. Corregir antes R1 (descartar el recurso largo, no el inventario) y añadir tests: compose largo, base v4, base corrupta, id no configurado, 20 POST simultáneos, dos cambios rápidos de servidor, web «mal» con disponibilidad 100 %.

Status: DONE
