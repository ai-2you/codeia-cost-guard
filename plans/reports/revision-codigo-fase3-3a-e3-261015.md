# Revisión de código — Fase 3.3a, entrega E3 (certificados) — commit 02badc5

Alcance: `git show 02badc5` frente a su padre (49e1e38). Ficheros: `infobar/servidores_externo.py`, `servidores_datos.py`, `servidores_historico.py`,
`servidores_estado.py`, `desplegable_servidores.py`, `tests/test_servidores_certificados.py`, `tests/test_servidores_recursos.py`, `README.md`.
Referencias: diseño `fase3-3a-diseno.md` §E3, pre-mortem M1, red team H17/H18. Revisión de solo lectura; sin red ni servidores reales.

## Resultado

La lectura del certificado es correcta y segura: el `notAfter` solo sale de un handshake con verificación de cadena y nombre contra la IP ya fijada,
se captura en `connect()` (antes de que un `Connection: close` cierre el socket) y está acotado. Hay **un hallazgo alto**: un certificado con menos de
14 días pone `estado_vivo` en ámbar y mete el servidor en modo incidencia (SSH cada 60 s en vez de 300 s) durante hasta dos semanas, lo que contradice
el invariante documentado en `servidores_ciclo.py:57-58`. El resto son huecos semánticos del histórico (caducado frente a caído, columna que nadie lee
ni agrega) y detalles menores.

Tests: `test_servidores_certificados` 12/12 OK con `cryptography`; sin ella, 3 omitidos (los del servidor TLS real). Suite `test_servidores*`: 204 OK,
1 omitido. [V]

## Alto

### A1. El certificado próximo a caducar activa el modo incidencia durante días [V]
- **Evidencia.** `servidores_estado.py:40-43`: `cert_dias < 14` da ámbar a la tarjeta `webs` y `< 3` da rojo. `servidores_ciclo.py:85`: `estado_vivo` es el
  peor de todas las tarjetas salvo `mantenimiento`. `servidores_ciclo.py:57-59`: el comentario dice que solo la salud viva acelera la recogida y que lo
  que «no cambia por mirarlo cada minuto» no debe hacerlo; `en_incidencia` devuelve True con `estado_vivo` ámbar o rojo. Comprobado por ejecución: una
  web con `cert_dias=10` da `vivo='ambar'` y `en_incidencia(...) = True`.
- **Escenario.** Falla la renovación automática (Let's Encrypt renueva a 30 días, así que llegar a <14 es justo el caso de fallo). Durante 11 días
  (ámbar) y 3 más (rojo) el Mac lee ese servidor por SSH cada 60 s: 5 veces más conexiones, tiempo de CPU del script de root y entradas en el journal,
  sin ganar nada (los días cambian una vez al día). Además, `muestras.estado` y `horas_servidor.estado_peor` quedan en ámbar o rojo todo ese tiempo,
  así que el histórico de estado deja de distinguir un incidente real de un aviso administrativo, y un ámbar real (carga, intentos) queda tapado.
- **Gravedad.** Alta: no rompe datos, pero cambia la carga sobre los servidores y la semántica del histórico durante semanas, y contradice una
  decisión explícita. Antes de este commit un certificado ya inválido ponía la tarjeta en rojo (vía `error`), y eso sí es justificable porque la web
  está caída para los usuarios. Lo nuevo es el aviso anticipado.
- **Cambio propuesto.** Sacar el aviso de caducidad de la salud viva. La opción más simple es una tarjeta propia, `t["certificado"] = {"estado": ...,
  "cert_dias": caducan}`, y excluirla igual que `mantenimiento`, tanto en `estado_vivo` (`servidores_ciclo.py:85`: `k not in ("mantenimiento",
  "certificado")`) como sumándola al `estado` global (`peor(vivo, _estado_t(t, "mantenimiento"), _estado_t(t, "certificado"), ...)`). El error de
  handshake se queda en `webs` (sigue siendo rojo y vivo). Test: con `cert_dias=10`, `estado` ámbar, `estado_vivo` verde y `en_incidencia` False.

## Medio

### M1. El histórico no distingue «certificado caducado o no válido», «web caída» y «sin dato» [V]
- **Evidencia.** `servidores_externo.py:87-88`: cualquier `SSLError` da `error="certificado: …"` y `cert_dias=None`. `servidores_datos.py:76-82`: si
  alguna https no tiene fecha, `cert_dias_min` es None. `servidores_datos.py:119`: `webs_caidas` cuenta cualquier `error`. Comprobado: una sola web con
  error de certificado da `cert_dias_min=None`, `webs_caidas=1`, exactamente igual que un timeout o un fallo de DNS.
- **Escenario.** Al revisar el histórico tras un incidente no se puede saber si la web cayó por un certificado caducado o por red. La señal de
  «caducado» no se pierde del todo (está en `webs_caidas`, como decía el diseño), pero queda mezclada con todo lo demás. El pre-mortem M1 proponía
  `cert_dias=0` o una columna `webs_cert_mal`; se adoptó la variante «solo si todas dieron fecha», que evita mentir con el mínimo pero no conserva la causa.
- **Cambio propuesto.** Añadir en esta misma migración 4 (no está desplegada) una métrica `webs_cert_mal INTEGER CHECK(BETWEEN 0 AND 1000)`, que cuente
  las webs cuyo `error` empieza por `certificado`. Con eso, los tres casos quedan separados: `cert_dias_min` con valor (hay fecha), `webs_cert_mal>0`
  (certificado no válido) y ambos nulos o cero con `webs_caidas>0` (sin dato). Opcional: guardar en `medir_web` el `verify_code` de
  `ssl.SSLCertVerificationError` (10 = caducado, 62 = nombre) para que el rótulo diga «caducado» en vez de `CERTIFICATE_VERIFY_FAILED`.
- **Sobre la regla «solo si todas dieron fecha».** Es correcta para el valor que se guarda: un mínimo parcial escondería precisamente la web
  problemática. Su coste es que cualquier timeout puntual de una web borra el dato de las demás en esa muestra. Es aceptable con el contador de M1.

### M2. `cert_dias_min` no tiene lector ni agregado: se borra a los 14 días [V]
- **Evidencia.** Solo `servidores_historico.py` hace `FROM muestras`. `servidores_lectura.py:58` lee solo `horas_servidor`, y `_suma_hora`
  (`servidores_historico.py:234-247`) no agrega `cert_dias_min`. `RETENCION_CRUDO_S = 14 días` (`:20`, `:388`).
- **Escenario.** La columna se escribe pero nadie la lee: `/servidores/historia` no la devuelve y a los 14 días desaparece sin dejar rastro en las
  horas. Aún no es un fallo (la tarjeta en vivo funciona), pero el «histórico de caducidad» que anuncia el README no existe más allá de dos semanas.
- **Cambio propuesto.** Agregar `cert_dias_min` en `horas_servidor` como **mínimo** (columna `cert_dias_min` con `COALESCE(MIN(...))`) en esta misma
  migración y exponerlo en la serie de lectura. Otra opción es documentar que se difiere a la 3.5.

### M3. Con SSH caído no se guarda la caducidad, aunque no dependa de SSH [V]
- **Evidencia.** `servidores_ciclo.py:79-80` y `:192-195`: con `error` de SSH la caché no se actualiza con las webs. `servidores_historico.py:302-308`:
  se escribe una fila `estado='error'` sin métricas. Es el tercer punto del pre-mortem M1 y no está resuelto ni registrado como límite aceptado.
- **Cambio propuesto.** Documentar el límite en el diseño (lo más simple) o, si se quiere la señal, incluir `cert_dias_min` y `webs_caidas` en la fila
  de estado. Para eso, en el ciclo hay que guardar `webs` en la caché también cuando SSH falla.

### M4. Los tests del servidor TLS real se omiten sin `cryptography`: la captura en `connect()` queda sin cubrir por defecto [V]
- **Evidencia.** `test_servidores_certificados.py:52-58` hace `skipTest` si no está `cryptography`. Sin el `PYTHONPATH` de dependencias: `OK (skipped=3)`.
- **Escenario.** Si alguien mueve `getpeercert()` después de `getresponse()`, la suite normal sigue en verde. El servidor de prueba responde con
  HTTP/1.0 y cierra el socket, y la regresión solo la detecta el test omitido.
- **Cambio propuesto.** Añadir un test sin dependencias que parchee `ssl.SSLContext.wrap_socket` (o `_context`) con un socket falso cuyo `getpeercert()`
  devuelva `{"notAfter": ...}` y cuyo `close()` anule el certificado, y que mida `cert_dias` tras una respuesta con cierre. Otra opción es generar el
  certificado con la CLI `openssl` si está en el PATH.

## Bajo

### B1. Las URL `HTTPS://` se miden pero se excluyen del mínimo y pueden esconder la web que falla [V]
- **Evidencia.** `url_valida` acepta el esquema en mayúsculas (`urlsplit` lo pasa a minúsculas), y `medir_web` usa `u.scheme`. Pero `_cert_min`
  (`servidores_datos.py:79`) filtra con `str(url).startswith("https://")`. Comprobado: con `[HTTPS://a con timeout, https://b con 50 días]` se guarda
  `cert_dias_min=50`, justo lo que la regla «todas o ninguna» quiere evitar.
- **Cambio.** Filtrar con `urllib.parse.urlsplit(url).scheme == "https"` o con `url[:8].lower() == "https://"`. Test con la URL en mayúsculas.

### B2. El rótulo «certificado: N d» ignora las webs con certificado inválido [V por lectura]
- **Evidencia.** `servidores_estado.py:40` toma el mínimo solo entre las webs con fecha; `desplegable_servidores.py:61` lo muestra.
- **Escenario.** Hay dos webs: una con certificado caducado (error) y otra a 60 días. La tarjeta está en rojo (bien), pero el subtítulo dice
  «certificado: 60 d».
- **Cambio.** Si alguna web tiene un `error` que empieza por `certificado`, mostrar «certificado: no válido» en lugar de los días.

### B3. `notAfter` con segundos fraccionarios daría None permanente [S]
- **Evidencia.** OpenSSL imprime `GeneralizedTime` con fracción (`… 12:00:00.5 2050 GMT`), y `cert_time_to_seconds` no lo acepta, así que el resultado
  sería None y el servidor quedaría sin mínimo para siempre. RFC 5280 prohíbe la fracción y las CA públicas no la usan, así que el riesgo es teórico.
  No requiere cambio; como mucho, un test que fije el comportamiento.

### B4. Varias IPs y SNI [V por lectura, sin cambio]
- `ip_publica` usa `ips[0]` (`servidores_externo.py:26`). Con CDN o doble pila, el frente medido puede cambiar entre lecturas, y durante una rotación el
  número de días puede saltar entre muestras. `server_hostname=self.host` asegura que el certificado es el del nombre configurado, nunca el de otro
  dominio. Una redirección no se sigue (HEAD sin seguir), así que la fecha es siempre la del host configurado. Es coherente con el rotulado de H18.

### B5. El test deja la clave privada de la CA de prueba en el directorio temporal [V]
- `tempfile.mkdtemp` (`test_servidores_certificados.py:59`) no se borra. Basta con `self.addCleanup(shutil.rmtree, self.dir, True)`.

## Verificaciones sin hallazgo

- **Handshake verificado** [V]: `create_default_context()` da `CERT_REQUIRED` y `check_hostname`, y `wrap_socket(server_hostname=host)` se hace sobre la IP
  ya filtrada contra rebinding. Con `CERT_REQUIRED`, `getpeercert()` nunca devuelve `{}` tras un handshake completo. Si lo hiciera, `KeyError` daría
  None (cubierto en `test_dato_imposible_o_ilegible_es_none`). Como cada medida usa un contexto nuevo, no hay sesión reanudada.
- **`Connection: close`** [V]: el certificado se guarda en el objeto conexión dentro de `connect()`, así que el cierre del socket no le afecta.
  `http.client` no reconecta dentro de una sola petición.
- **Datos hostiles en `dias_de_caducidad`** [V]: un mes en minúsculas se acepta (por `.title()`, correcto). `UTC` en vez de `GMT`, el año 10000, el día 99,
  un NUL, `None`, un número o una lista dan todos None. El 9999 da None por el tope de ±36500. «Feb 30» se normaliza a «Mar 2» (diferencia 0 s, comprobado);
  no importa, porque la cadena la produce OpenSSL a partir de un certificado firmado y verificado, no el servidor como texto libre. Las excepciones
  posibles (`TypeError`, `KeyError`, `ValueError`, `OverflowError`) están cubiertas.
- **Caché manipulada en `muestra_de`** [V]: booleanos, texto y floats dan None. Un entero fuera de ±36500 lanza `FueraDeRango` y descarta la muestra entera,
  igual que el resto de métricas. Como `cert_dias` lo produce el propio Mac, esto solo ocurre si se manipula la caché local, que queda fuera del modelo
  de amenaza. `tarjetas` excluye los booleanos, y el `isinstance(..., int)` del desplegable solo podría mostrar «True d» con una caché editada a mano.
- **Migración 4** [V]: `ALTER TABLE … ADD COLUMN … CHECK` es válido en una tabla STRICT WITHOUT ROWID (SQLite 3.53.4 local; el CHECK de una columna
  añadida se admite y, siendo nullable, las filas existentes pasan). Se ejecuta dentro de `BEGIN IMMEDIATE` con `_rollback` (`servidores_historico.py:144-157`),
  y el DDL de SQLite es transaccional. La copia previa sin IPs se hace antes porque `version > 0`. `CLASES_SERVIDORES["muestras"]` se genera desde `METRICAS`,
  así que la columna queda clasificada sola. El ensayo v3→v4 y el v2→v4 (`test_servidores_recursos.py:665-669`, ahora con `max(sh.MIGRACIONES)`) pasan.
  `servidores_lectura` no lee `muestras`, así que no hay incompatibilidad. Una app anterior abriendo una base v4 se niega a escribir (`ErrorAlmacen`,
  límite M2 del pre-mortem ya conocido).
- **Test con servidor HTTPS**: puerto 0, hilo daemon, `shutdown` y `server_close` en la limpieza; la aserción `in (20, 21)` aguanta el cambio de día.
  No veo fragilidad de puertos ni de tiempo. El único riesgo es que un handshake fallido bloquee el hilo del servidor en `accept()`, y no ocurre porque
  el cliente cierra.

## Mutaciones que faltan

1. Cambiar `< 14` por `<= 14` o `< 3` por `<= 3`: cubiertas por `test_umbrales`. Bien.
2. Quitar la exclusión de booleanos en `tarjetas`: no hay test (`cert_dias=True` daría `min=True`, que es `1 < 3`, y pondría la tarjeta en rojo). Falta añadirlo.
3. Cambiar en `_cert_min` el `any(...)` por un filtro de las que sí tienen fecha: cubierta. Bien.
4. Mover `getpeercert()` fuera de `connect()`: solo cubierta con `cryptography` (M4).
5. Que `estado_vivo` incluya el aviso de caducidad: no hay test (A1).
6. URL en mayúsculas (B1): no hay test.

## Acciones recomendadas (por prioridad)
1. A1: sacar la caducidad de `estado_vivo` (tarjeta `certificado` excluida como `mantenimiento`) y añadir un test de `en_incidencia`.
2. M1 y M2, aprovechando que la migración 4 aún no está desplegada: añadir `webs_cert_mal` en `muestras`, y `cert_dias_min` (mínimo) en `horas_servidor` con su lectura.
3. M4: añadir un test de captura sin `cryptography`.
4. B1, B2 y B5, y las mutaciones 2 y 6.
5. M3: decidir y documentar.

## Preguntas abiertas
- A1: ¿el aviso de caducidad debe contar para el estado global (icono ámbar en la barra) o solo para la tarjeta? Propongo que cuente para el global y no para la salud viva.
- M2: ¿el histórico de caducidad tiene que estar en la API de la 3.3a o se difiere a la 3.5 junto con las reglas?

Status: DONE_WITH_CONCERNS
Summary: La captura del certificado es segura (handshake verificado, IP fijada, lectura en `connect()`, fecha acotada) y la migración 4 es correcta y reversible. Pero un certificado con menos de 14 días mete el servidor en modo incidencia durante días (alto), y el histórico ni distingue «caducado» de «caído» ni conserva `cert_dias_min` más de 14 días (medios).
