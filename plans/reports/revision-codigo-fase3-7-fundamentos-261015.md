# Revisión de código independiente: fundamentos F1–F4 de la 3.7 (commits 923b495 y c9a4dbc)

Fecha: 2026-10-15 · Alcance: `git diff 4497972 c9a4dbc` (infobar/servidores.py, servidor_local.py, servidores_ciclo.py, webs_sondeo.py, trabajador_barra.py,
panel_assets/ajustes.js y los tests). Solo lectura: no se ha tocado código ni tests, ni servidores ni red.
Contexto: `plans/261013-2350-sqlite-historico/fase3-7-alta-y-webs-diseno.md` (F1–F4), `premortem-fase3-7-261015.md` (C1–C5, A6) y `redteam-fase3-7-261015.md` (H14, H15, H19).

Leyenda: **[V]** verificado (lectura del código y, donde se indica, ejecución local); **[S]** supuesto (depende del entorno o de una frecuencia que no he podido medir).

## Resultado

Los cuatro fundamentos están implementados y funcionan en el caso que prueban sus tests. Los 51 tests (`test_servidores_guardado`, `test_webs_sondeo`
y `test_servidores_certificados`) pasan con Python 3.14.6, y los 32 de los dos módulos nuevos también pasan con `/usr/bin/python3` 3.9.6.

No he encontrado nada crítico. Hay dos hallazgos **altos**, ambos en el sondeador (F3):

- El canario de red da por «sin red» una pasada en la que todas las webs responden 5xx. Así puede esconder para siempre una caída real.
- Un `webs.json` con una entrada que no es un objeto rompe también la lectura SSH de todos los servidores.

En F1, la revisión cierra la carrera «página contra página» y la de «alta contra página». No cierra la carrera con `Historial.preparar`, que sigue
sustituyendo la lista entera fuera de cualquier cerrojo de leer, modificar y escribir. Además, un 409 tira todo lo que el usuario había tecleado, no solo
lo de los servidores.

## Críticos

Ninguno.

## Altos

### A1. El canario descarta como «sin red» pasadas en las que las webs sí responden (5xx), y con los canarios bloqueados esconde caídas reales sin límite [V código + ejecución; S entorno]
- **Evidencia:** `infobar/webs_sondeo.py:106` usa `_falla(r)`, que incluye `codigo >= 500` (`:64-66`). El diseño (F3) dice «fallan **por red** (DNS/conexión)».
  Lo he ejecutado: con 3 webs que devuelven 503 y `red=lambda: True`, la pasada devuelve `cambiaron=False` y no persiste nada.
- **Escenario:** el Mac está en una red que bloquea el 443 de 1.1.1.1, 8.8.8.8 y 9.9.9.9. Es habitual en redes corporativas o VPN que bloquean DoH.
  Si caen todas las webs de la pasada (un servidor que aloja todas las webs, o un proxy inverso que devuelve 502/503), la pasada se descarta. Se descarta
  también en todas las pasadas siguientes, sin límite. Un 5xx demuestra que hay red, pero aun así se descarta.
  `medidas()` (`:122-130`) sigue devolviendo la última medida buena sin mirar su antigüedad, así que la tarjeta queda en **verde falso**.
  `sin_red_en` no se guarda en disco (`:107` vuelve sin escribir) y no lo ve nadie.
- **Cambio propuesto:**
  1. En el canario, contar solo los fallos de red: `codigo is None` y un error que no empiece por «certificado». Un 4xx o 5xx demuestra conectividad.
  2. En `medidas()`, marcar como «sin datos recientes» (o no devolver) las entradas con `intento_en` de hace más de 3×`CADA_S`, para que una racha de
     pasadas descartadas acabe en gris y no en verde.
  3. Guardar `sin_red_en` y el número de descartes seguidos. Tras N descartes (p. ej. 10 min), dejar de descartar o avisar en el desplegable.
  4. (Opcional) Añadir canarios con nombre (resolución DNS más conexión) para distinguir «DNS roto» de «sin ruta».

### A2. Un `webs.json` con una entrada que no es un objeto rompe la lectura SSH de todos los servidores y bloquea el sondeador para siempre [V ejecución]
- **Evidencia:**
  - `webs_sondeo.py:128-129`: `if e:` y luego `e.get(...)`. Con `{"webs": {"<url>": [1]}}`, `medidas()` lanza `AttributeError` (ejecutado).
  - `medidas()` se llama dentro de `recoger_uno` (`servidores_ciclo.py:75`), así que cada servidor acaba con `{"error": "AttributeError…", "estado": "rojo"}`
    (`servidores_ciclo.py:167-169`).
  - `pasada()` lanza `AttributeError` con la misma entrada y `TypeError` con `proximo_en` como texto (ejecutado).
  - El hilo `_sondea_webs` lo registra cada 15 s y no se recupera nunca.
- **Escenario:** un fichero dañado (edición a mano, disco, un fallo futuro en la escritura) deja el monitor entero en rojo y sin webs. `leer()` solo
  comprueba el nivel superior (`:35-37`).
- **Cambio propuesto:** sanear en `leer()`. Quedarse solo con las entradas `dict` cuyos campos numéricos (`proximo_en`, `intento_en`, `ok_en`, `fallos`)
  sean `int`/`float` y no `bool`, y tirar el resto. Además, en `recoger_uno`, envolver `webs_sondeo.medidas()` para que un fallo de las webs nunca tumbe
  la lectura SSH (devolver `[]` y registrar en el log). Mutaciones útiles: entrada lista, `proximo_en` texto y `fallos` `None`.

## Medios

### M1. F1: `Historial.preparar` sustituye la lista entera fuera del contrato de revisión y del cerrojo del panel [V código; S frecuencia]
- **Evidencia:**
  - `servidores_registro.py:63-69` lee `cfg()` y, en otro momento, hace `guardar_config({"servidores": nueva})`.
  - `_guarda_servidores` (`config_y_estado.py:128-136`) solo fusiona claves de primer nivel bajo el cerrojo del fichero: la lista que escribe es la que leyó
    quien llama.
  - `_CERROJO_GUARDADO` (`servidor_local.py:128`) es un `threading.Lock` del panel. `preparar` (hilo del trabajador, `trabajador_barra.py:91`, cada 15 s)
    no lo toma.
- **Escenario:** hay un servidor sin `id` en `servidores.json` (edición a mano; o, en la 3.7a, un alta que lo escriba sin `id`).
  1. `preparar` lee la lista A.
  2. El usuario guarda en el panel la lista B (pasa la revisión).
  3. `preparar` escribe A con los `id`: **se pierde B**. Es la carrera C1 al revés.

  En el otro orden, si `preparar` escribe entre la comprobación y `guardar_config` del panel, `normalizar` puede generar un `id` nuevo para un servidor al
  que `preparar` ya le había dado otro, y se parte su historial. Las ventanas son de milisegundos, pero el diseño F1 pide expresamente «leer, modificar y
  escribir dentro del cerrojo de la configuración, nunca sustituir la lista». Ese mecanismo todavía no existe y la 3.7a lo necesita.
- **Cambio propuesto:** añadir en `config_y_estado` una primitiva `modificar_servidores(funcion, rev_esperada=None)` que, dentro de
  `_bloqueo(rutas.CONFIG_SERVIDORES)`:
  1. lea `servidores.json`;
  2. si se pasa `rev_esperada`, compare la revisión y lance un 409 si no coincide;
  3. aplique `funcion(lista)` y escriba.

  El panel (`/guardar`), `preparar` (con `asegurar_ids` aplicado a la lista leída dentro del cerrojo) y las futuras altas, bajas y aceptación de webs
  deben pasar por ella. Así el cerrojo vale también entre procesos y `_CERROJO_GUARDADO` sobra. Test: un `preparar` que se ejecute entre la lectura y la
  escritura del panel no puede perder el servidor ni el `id`.

### M2. F1: un 409 tira todo lo tecleado, y no solo lo de los servidores [V código]
- **Evidencia:** `ajustes.js:241` solo muestra «Error: la lista de servidores cambió: recarga la página antes de guardar». La página siempre envía
  `servidores` (`Object.assign({}, A.config)`), así que cualquier guardado se rechaza entero. El test `test_un_alta_entre_la_lectura_y_el_guardado_no_se_pisa`
  lo confirma: tampoco se guardan los alias.
- **Escenario:** el usuario cambia alias, presupuestos y planes; entre medias hay un alta (3.7a) o una asignación de `id`. Al recargar, como pide el
  mensaje, pierde todo lo demás.
- **Cambio propuesto:** al recibir un 409, hacer GET `/ajustes` y conservar en `A.config` todo menos `servidores` y `servidores_rev`, que se toman de la
  respuesta fresca. Después, repintar y decir «la lista de servidores ha cambiado: revisa y vuelve a guardar; lo demás se ha conservado». Otra opción es
  ofrecer «guardar sin servidores», enviando el cuerpo sin la clave `servidores`, que el servidor ya acepta. Hay que tener en cuenta que, si la 3.7c escribe
  propuestas de webs dentro de la lista, cada propuesta provocará un 409 a una página abierta: mejor guardarlas fuera de la lista revisada.

### M3. F2: un campo desconocido en `servidores.json` (editado a mano o de una versión anterior) para en silencio la vigilancia de todos los servidores y bloquea el panel [V código; S existencia de esos campos]
- **Evidencia:**
  - `servidores.py:92-94` añade un error por cualquier campo fuera de `CAMPOS_SERVIDOR`.
  - `servidores_ciclo.py:151-153` («configuración no válida, no se recoge nada») solo deja una línea en el log.
  - `/guardar` devuelve 400 con «campo no admitido (x)», y la página no tiene ninguna forma de quitar ese campo.
- **Escenario:** alguien añade `"nota": "…"` a mano a un servidor. Desde ese momento no se lee ningún servidor, la caché envejece hasta gris y no se puede
  guardar nada desde Ajustes, ni siquiera un alias.

  Lo que sí está bien: un campo **secreto** escrito a mano no llega a la página (`sin_secretos`), y el siguiente guardado lo elimina del disco. Lo he
  comprobado leyendo `con_revision(sin_secretos(...))` y `guardar_config(sin_secretos(...))`.
- **Cambio propuesto:** mantener el rechazo en la entrada desde el panel, pero, para lo que ya está en disco:
  - mostrar el error en Ajustes y en el desplegable, no solo en el log;
  - en `ciclo`, saltarse solo el servidor inválido en vez de la lista entera;
  - (opcional) que el guardado desde el panel quite los campos desconocidos no secretos avisando de ello.

  La lista del diseño no incluye `ssh_admin` y el código sí. Es correcto (lo usa la guía), pero conviene apuntarlo en el diseño.

### M4. F3: un reloj adelantado deja webs congeladas hasta esa fecha [V ejecución]
- **Evidencia:** `webs_sondeo.py:87` (`proximo_en <= ahora`) no tiene ningún tope. Lo he ejecutado con `proximo_en` en 2099: 0 medidas y la pasada
  devuelve `False`.
- **Escenario:** el Mac arranca con la hora mal o NTP corrige hacia atrás. Las webs se quedan sin medir y la tarjeta sigue mostrando la última medida como
  actual (no hay antigüedad: ver A1.2).
- **Cambio propuesto:** tratar como debida cualquier entrada con `proximo_en > ahora + CADA_S` (o con `intento_en > ahora`). Test con `proximo_en` muy en
  el futuro.

### M5. F3: hilos que se escapan si `medir_web` se cuelga; el presupuesto se cumple para la pasada, no para los hilos [V código; S frecuencia]
- **Evidencia:**
  - `webs_sondeo.py:105`: `shutdown(wait=False, cancel_futures=True)` deja vivos los `medir_web` que estaban en marcha.
  - `medir_web` (`servidores_externo.py:65-92`) no tiene un tope total. `getaddrinfo` no tiene tiempo límite, y el `timeout=5` de los sockets es por
    operación: un servidor que manda las cabeceras gota a gota mantiene el hilo hasta 100 líneas × 64 KiB.
  - La siguiente pasada (15 s después) vuelve a lanzar esa misma URL, que sigue siendo debida porque no tiene resultado. Cada pasada puede dejar hasta 4
    hilos más.
  - Los hilos de `ThreadPoolExecutor` se esperan al salir del intérprete, así que al cerrar la app puede quedarse colgada.
- **Cambio propuesto:**
  - Llevar un conjunto `_EN_VUELO` de URLs con medida pendiente y no reenviarlas.
  - Poner un tope global de hilos vivos; si se supera, saltarse la pasada.
  - Poner un plazo total por petición (medir el tiempo transcurrido en la lectura de la respuesta y abortar a los N s).
  - Hacer la resolución de nombres en un hilo con su propio tope.

  Esto es anterior al cambio (`servidores_ciclo` también lo tenía), pero ahora se repite cada 15 s.

### M6. `reaplicar_webs` recalcula TODAS las tarjetas a partir de `datos` ya podados y de la configuración actual [V código]
- **Evidencia:**
  - `servidores_ciclo.py:120` llama a `tarjetas(e["datos"], …)` completo.
  - `podar_nombres` (lo llama `servidores_api.py:70` al olvidar los recursos) deja `datos["servicios"] = []`. Al recalcular, la tarjeta `servicios` pasa de
    rojo (con `caidos` vacío pero estado rojo) a **verde**, y `estado_vivo` baja: la lectura SSH sale de incidencia.
  - Después de 48 h, `podar_ips` vacía `sesiones[].desde`, y la tarjeta `sesiones` pasa de ámbar a verde.
  - `conocidas` y `puerto` salen de la configuración actual, mientras que `bastionado` sale de la lectura antigua.
  - `expuestos` y `certificado` sí se conservan bien (`expuestos` se guarda en la caché vía `previo.update(r)`).
  - `estado_vivo` se calcula con el mismo criterio que `recoger_uno`: ambos pasan por `estados()`.
- **Cambio propuesto:** en `reaplicar_webs`, sustituir solo las tarjetas `webs` y `certificado`:
  `t = dict(e.get("tarjetas") or {}); t.pop("webs", None); t.pop("certificado", None); t.update(tarjetas({}, webs, None))`, y después `estados(t, …)`.
  Test: una entrada con `servicios` rojo y `datos.servicios=[]` sigue en rojo después de `reaplicar_webs`.

### M7. `ip_no_publica` depende de la versión de Python, y en 3.9 (el mínimo que se admite) deja pasar 6to4 y NAT64 de uso local con IPv4 privada [V ejecución]
- **Evidencia:** con `/usr/bin/python3` 3.9.6, `2002:a00:1::1` (6to4 de 10.0.0.1) y `64:ff9b:1::a00:1` (RFC 8215, 10.0.0.1) dan `False` («pública»), y
  `url_valida("http://[2002:a00:1::1]:8080/")` la acepta. Con 3.14.6 ambas se rechazan.

  `::a00:1` (IPv4-compatible, obsoleta) y `::ffff:0:a00:1` (IPv4-translated) se aceptan en las dos versiones. `_IPV6_NO_PUBLICAS` (`servidores.py:25`)
  solo añade `64:ff9b::/96`, `fec0::/10` y `5f00::/16`.

  Los NAT64 con prefijo propio de la red (RFC 6052: /32, /40, /48, /56, /64 dentro de un prefijo global cualquiera) no se pueden detectar sin conocer ese
  prefijo [S].
- **Escenario:** el riesgo práctico es bajo, porque 6to4 no se decapsula en local si no hay `stf` configurado [S]. Aun así, la garantía «rechaza cualquier
  IPv6 con IPv4 embebida privada» del diseño no se cumple en 3.9.
- **Cambio propuesto:** lista explícita, independiente de la versión:
  - `2002::/16`: evaluar la IPv4 de los bits 16–47.
  - `2001::/32` (Teredo), `64:ff9b:1::/48`, `::/96`, `::ffff:0:0/96`, `100::/64` y `2001:db8::/32`: rechazar.
  - Para `64:ff9b::/96`, evaluar la IPv4 de los últimos 32 bits **en lugar de rechazar** (ver M8).
  - Para el NAT64 propio de la red: resolver `ipv4only.arpa` (RFC 7050), sacar el prefijo y aplicar la misma extracción.

  Para que no se repita, el test `IpsNoPublicasTest` debería ejecutarse también en 3.9 (o fijar los casos de 6to4, Teredo y `/48`).

### M8. NAT64 rechazado entero: en redes solo IPv6 con DNS64 todas las webs IPv4 se dan por caídas, y con un mensaje engañoso [V código; S red]
- **Evidencia:** `servidores.py:25` rechaza todo `64:ff9b::/96`. El test fija que `64:ff9b::808:808` (8.8.8.8, pública) no es pública
  (`test_webs_sondeo.py:243`). `ip_publica` (`servidores_externo.py:19-27`) falla con «resuelve a una IP local o privada».
- **Escenario:** el Mac está en una red solo IPv6 con DNS64. macOS sintetiza la dirección a partir de la IPv4 literal, así que los canarios responden [S].
  Todas las webs que solo tienen IPv4 fallan, el canario dice que hay red y se anotan caídas falsas de todas ellas, con un motivo que apunta a una IP privada.
- **Cambio propuesto:** en `64:ff9b::/96`, usar como veredicto la IPv4 de los últimos 32 bits (pública → vale, privada → se rechaza). El diseño pide
  literalmente «rechaza 64:ff9b::/96 y cualquier IPv6 con IPv4 embebida privada», así que esto es una **decisión del usuario**:
  - **Opción a)** mantener el rechazo total: máxima seguridad, caídas falsas en redes NAT64.
  - **Opción b)** extraer la IPv4: la conexión pasa por el traductor NAT64 hacia una IPv4 pública, con el mismo riesgo que una conexión IPv4 directa.

## Bajos

- **B1. `_guardar` puede devolver las webs de un servidor recién leído a una medida de hace unos segundos** [V código]. `previo.update(r)`
  (`servidores_ciclo.py:241`) pisa con las `webs` y `tarjetas` que `recoger_uno` leyó antes de las sondas de puertos. Si `reaplicar_webs` escribió entre
  medias, la caída recién detectada vuelve a verde hasta la siguiente pasada con cambios (≤ 60 s si falla). La relectura «fresca» solo protege a los
  servidores que no se leyeron en este ciclo. **Propuesta:** dentro del cerrojo de `_guardar`, recalcular `webs`, `tarjetas` y estados con
  `webs_sondeo.medidas(s)` (como hace `reaplicar_webs`), o llamar a `reaplicar_webs()` al final de `ciclo`.
- **B2. Un servidor con la lectura SSH en error deja la tarjeta de webs congelada** [V código]: `reaplicar_webs` lo salta (`:115`) y `_guardar` no
  copia `webs` cuando hay error. Justo cuando cae el servidor, sus webs dejan de actualizarse en el desplegable. **Propuesta:** en las entradas con error,
  actualizar al menos `webs` (aunque no se recalculen las demás tarjetas).
- **B3. Pasada «sin red» sin espera entre intentos** [V ejecución]: no persiste nada, así que en la siguiente pasada (15 s) se vuelve a medir todo. Con 4
  webs y 3 pasadas seguidas sin red salieron 12 medidas. Sin red los fallos son rápidos, pero con canarios bloqueados y webs que agotan el tiempo cada
  pasada dura hasta 60 s + 6 s. **Propuesta:** guardar `sin_red_en` y no reintentar antes de 60 s.
- **B4. Con menos de 3 webs debidas no hay canario** [V código, decisión de diseño]. En régimen estable las webs que van bien están sincronizadas
  (mismo `proximo_en`), pero las aceleradas (60 s) se miden de una en una: un corte de wifi suma un fallo a una web que ya fallaba, y un usuario con 1–2
  webs nunca está protegido. **Propuesta:** consultar el canario (una conexión TCP, barata) siempre que haya algún fallo de red, sea cual sea el número
  de webs.
- **B5. `url_valida` acepta zona en IPv6 global** [V ejecución]: `https://[2606:4700::1111%25en0]/` → `""`. No es peligroso (fija la interfaz), pero no tiene
  sentido. **Propuesta:** rechazar cualquier `%` en `hostname`.
- **B6. Tras un guardado correcto, la página no conoce los `id` que asignó `normalizar`** [V código]. `ajustes.js:241` actualiza solo `servidores_rev`. Si
  se renombra un servidor recién añadido y se vuelve a guardar sin recargar, `asegurar_ids` no lo encuentra por nombre y le da otro `id` (se parte el
  historial). **Propuesta:** devolver en el 200 la lista guardada (sin secretos) y sustituir `A.config.servidores`.
- **B7. `webs.json` enorme o manipulado** [V código]: `leer()` carga el fichero entero en cada `recoger_uno` (una vez por servidor y ciclo) y en cada
  pasada. El tamaño legítimo está acotado (100 por servidor, se poda lo que ya no está configurado), pero no hay tope de bytes. **Propuesta:** no leer si
  pasa de 2–5 MB (tratarlo como vacío y registrarlo en el log), y aprovechar el saneado de A2.
- **B8. `preparar` reescribe tal cual un `servidores.json` inválido** (sin pasar `errores_servidores`), incluidos los campos secretos escritos a mano que ya
  estaban en disco [V código]. No introduce nada nuevo. Se arregla con la primitiva de M1 si valida antes de escribir.
- **B9. Import sin uso** en `tests/test_webs_sondeo.py:15` (`revision_servidores`).

## Respuestas a las preguntas del encargo

1. **F1.**
   - La revisión cubre «la página lee, otro cambia la lista, la página guarda» cuando el «otro» es el propio panel o cualquier escritura anterior a la
     comprobación (test con alta + copia vieja → 409, y dos guardados con la misma revisión → 200/409) [V].
   - **No** cubre `preparar` en los milisegundos entre su lectura y su escritura, ni entre la comprobación del panel y su escritura (M1).
   - La única ruta que hoy sustituye la lista entera además del panel es `preparar` (`servidores_registro.py:69`). La migración `_migra_servidores` solo
     mueve claves la primera vez. La restauración y `cli_copias` no tocan servidores [V].
   - El hash se calcula sobre lo mismo que ve la página (sin secretos, `id` incluidos). Puede haber un 409 «espurio» si `preparar` asigna `id` justo
     después de abrir la página (al arrancar), o en el futuro si la 3.7c escribe dentro de la lista.
   - Al recibir un 409, el usuario ve el mensaje y, si recarga, pierde todo lo que había tecleado (M2).
2. **F2.**
   - CLI, importación y restauración no escriben servidores [V].
   - `servidores.json` editado a mano esquiva la lista cerrada al leer: no se reintroduce nada vía panel, pero para el ciclo entero (M3).
   - `sin_secretos` se aplica en GET y al guardar desde el panel. `preparar` escribe sin aplicarlo, aunque solo reescribe lo que ya había en disco (B8).
3. **F3.**
   - La concurrencia entre hilos es correcta: `_bloqueo` usa `flock`, que excluye también entre hilos del mismo proceso porque cada `open` es una
     descripción de fichero distinta.
   - Solo escribe `webs.json` el hilo de webs, y `_EN_MARCHA` evita pasadas solapadas.
   - El presupuesto de 60 s se cumple para la pasada, pero no limita los hilos que se escapan (M5).
   - Canario: con el DNS roto y la red bien, los canarios (IP literales) responden y las caídas se fabrican [S]. Con una VPN que bloquea los canarios se
     esconden caídas, también las de 5xx (A1). En redes solo IPv6 con DNS64, M8.
   - Calendario: M4 (reloj adelantado), B3 (sin red) y B7 (tamaños).
4. **F4.**
   - Sin carreras de escritura entre `reaplicar_webs` y `_guardar` (mismo cerrojo y relectura dentro de él).
   - Puede quedar un estado mezclado: webs de una lectura y tarjetas recalculadas a partir de datos podados (M6, B1).
   - `estado_vivo` se calcula con el mismo criterio (`estados`), y `certificado` y `puertos` no se pierden.
5. **`ip_no_publica`.** Ver M7 y M8. `url_valida` con IPv6 literal rechaza bien `[::1]`, `[fe80::…%…]`, `[::ffff:127.0.0.1]` y `[64:ff9b::7f00:1]`, pero
   acepta zonas en direcciones globales (B5).
6. **Tests.**
   - Prueban lo que dicen y no dependen del orden. La única espera real es la del test del presupuesto (`sleep(2)` con un presupuesto de 0,3 s); el hilo
     que se escapa se cierra solo.
   - Faltan casos con mutaciones que hoy sobrevivirían:
     - 5xx con canario (A1);
     - entradas de `webs.json` que no son objetos, o con tipos malos (A2);
     - `proximo_en` en el futuro (M4);
     - `reaplicar_webs` con `expuestos` no vacío y con servicios caídos podados (cambiar `e.get("expuestos")` por `None`, o `permitidos` por `set()`,
       sobrevive hoy, M6);
     - `preparar` entre la lectura y la escritura del panel (M1);
     - quitar `sin_secretos` en la comparación del POST;
     - `IpsNoPublicasTest` solo en 3.14: quitar la rama `ipv4_mapped` sobrevive en 3.14 y solo muere en 3.9;
     - el 409 en `ajustes.js` no tiene ninguna prueba.

## Acciones recomendadas (por orden)
1. A2: sanear `leer()` y aislar `medidas()` dentro de `recoger_uno`.
2. A1: canario solo con fallos de red, antigüedad en `medidas()`, guardar `sin_red_en` y poner un tope de descartes seguidos.
3. M1: primitiva de leer, modificar y escribir bajo `_bloqueo(CONFIG_SERVIDORES)` con revisión, usada por el panel y por `preparar` (requisito de la 3.7a).
4. M6 y B1: `reaplicar_webs` sustituye solo `webs` y `certificado`; `ciclo` vuelve a aplicar las webs dentro del cerrojo.
5. M4 y M5: tope a `proximo_en`, URLs en vuelo y tope de hilos.
6. M2: un 409 que conserve lo tecleado.
7. M7 y M8: rangos explícitos independientes de la versión de Python y decisión del usuario sobre NAT64.
8. M3: errores de configuración visibles y por servidor.

## Métricas
- Tests: 51/51 OK (Python 3.14.6), 32/32 OK (Python 3.9.6, módulos nuevos).
- Lint y tipado: no hay linter configurado para estos módulos; 1 import sin uso (B9).
- Cobertura: no medida.

## Preguntas abiertas
- ¿NAT64: rechazo total (diseño actual) o extraer la IPv4 embebida (M8)?
- ¿Un campo desconocido escrito a mano en `servidores.json` debe parar todo el monitor (hoy) o solo ese servidor (M3)?
- ¿Con qué Python se ejecuta la barra en las instalaciones reales? Si puede ser el 3.9 de Apple, M7 sube de prioridad.

Status: DONE_WITH_CONCERNS
Summary: F1–F4 están implementados y probados (51 tests en verde en 3.14 y 32 en 3.9), sin críticos. Quedan dos altos en el sondeador (el canario cuenta los 5xx como «sin red» y puede esconder caídas; un `webs.json` dañado tumba la lectura SSH de todos los servidores), y F1 aún no cierra la carrera con `Historial.preparar` ni conserva lo tecleado tras un 409.
