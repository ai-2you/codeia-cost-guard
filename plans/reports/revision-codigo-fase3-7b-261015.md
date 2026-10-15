# Revisión de código — 3.7b detección de webs (commit 81ee921)

Revisión independiente de solo lectura del último commit de `feat/fase1-ingesta-hermes`. Contexto: diseño v2 §3.7b, pre-mortem M1/M2/M9 y red team H13/H14/H16/H17/H21.
`[V]` = verificado leyendo el código o ejecutándolo en local (copias en el scratchpad, docker falso; sin tocar servidores ni la red). `[S]` = supuesto.

## Resultado

La extracción en root es segura en lo esencial: por la salida solo pueden pasar cadenas con forma de nombre DNS que estén dentro de `Host(...)`. Los enlaces simbólicos, las FIFO y los
subdirectorios quedan fuera (lo he comprobado ejecutándolo). La clasificación del Mac y `cambiar` son correctas y la página no mete HTML. Hay **dos hallazgos altos**:

1. La sección `webs` no tiene un tope de tiempo global y puede hacer que `recoger-root` muera antes de `fin_root`, con lo que se pierden **todas** las secciones de root.
2. La página adopta la revisión nueva que devuelve `POST /servidores/webs` sin haber visto la lista nueva, lo que reabre la pérdida de escrituras que corregía C1.

Los 30 tests pasan (el plan dice 31). Cuatro mutaciones de las protecciones del script sobreviven.

## Crítico

Ninguno.

## Alto

### A1. La sección `webs` puede pasarse de tiempo y tirar con ello todas las secciones de root (fail2ban, bastionado, inventario) [V mecanismo + medición local; S frecuencia]
- **Evidencia:**
  - La sección solo mira el reloj al empezar (`recoger-root.sh:95`, `-ge 6`). Después encadena `timeout 2` + `timeout 3` + `timeout 2` + `timeout 3` + 3 × `timeout 3` (`:101-107`), así que el peor caso son unos 19 s más.
  - `recoger.sh:120-121` solo le da a root `restante = 13 − transcurrido` (como mínimo 4 s) y el vigilante mata el grupo a los 14 s (`recoger.sh:14`).
  - Si root muere antes de `### fin_root`, el Mac descarta todas las `SECCIONES_ROOT` (`servidores_ssh.py:158-160`).
- **Medición:** con un docker falso que solo se cuelga en las órdenes de esta sección, `recoger-root` tardó **10,3 s** en total, aunque las demás secciones fueron instantáneas.
- **Escenario:** en un servidor con muchos contenedores parados (`docker ps -aq` no tiene tope, a diferencia del inventario, que lee 200) o con el daemon lento, cada `inspect` se acerca a sus 3 s. El sudo muere a mitad de `webs` y ese ciclo sale «sin dato» de fail2ban y del bastionado, justo cuando el servidor está cargado. Además se ejecuta en **cada** ciclo: la mitigación de M2 («como mucho una vez al día, con un sello en `/var/lib/infobar`») no se aplicó.
- **Cambio:**
  - Poner un plazo global a la sección: `lim=$(( 9 - ($(date +%s) - INICIO) ))`, envolver toda la tubería en un solo `timeout "$lim" bash -c '…'` y no arrancar si `lim < 2`.
  - Usar `docker ps -q` (sin `-a`) con un tope (`head -n 300`).
  - Añadir el sello diario de M2.
  - Como alternativa robusta, emitir `webs` **después** de `### fin_root` (su marco propio ya la protege). Así, un corte a mitad solo pierde `webs`.
  - Corregir el comentario de cabecera («acaba en LIMITE + 3 s»), que ya no es cierto.

### A2. `POST /servidores/webs` devuelve una `servidores_rev` que la página adopta sin haber visto la lista: un segundo escritor se pierde (vuelve C1) [V código; S frecuencia]
- **Evidencia:**
  - `cambiar` no recibe la revisión que tiene la página (`servidores_webs.py:173`, `:258`) y devuelve la huella de la lista **entera** del disco (`:261`).
  - `ajustes.js:141` hace `A.config.servidores_rev = r.servidores_rev` sin más y conserva su `A.config.servidores` viejo.
- **Escenario:**
  1. La pestaña A abre Ajustes.
  2. La pestaña B (o la CLI, u otro proceso que use `modificar_servidores`) añade o cambia un servidor.
  3. En A, el dueño marca una web y pulsa «Vigilar». El POST va bien y A recibe la revisión que ya incluye el cambio de B, que A nunca ha visto.
  4. En A, el dueño pulsa «Guardar». `/guardar` acepta la revisión (`servidor_local.py:415`) y escribe la copia vieja de A, **borrando el servidor de B**.
- **Cambio:**
  - Mandar `servidores_rev` en el cuerpo del POST y comprobarlo **dentro** de la función que se pasa a `modificar_servidores` (`revision_servidores(sin_secretos(lista)) == rev`). Si no coincide, responder 409 «la lista cambió: recarga».
  - Solo con éxito adoptar la revisión nueva, que entonces corresponde a «lo que vi + mi cambio».
  - Test: un cambio de `modificar_servidores` entre la lectura y el POST debe dar 409.

## Medio

### M1. Un docker colgado o con el tiempo agotado se declara «fuente ok, total 0», contra el propio contrato («nunca cero webs») [V]
- **Evidencia:**
  - Los códigos 124 de `timeout` (`recoger-root.sh:101-103`, `:106`) se ignoran.
  - En la medición de A1, la salida fue `fuente ok / total 0 / truncado no`.
  - El recorte global `head -c 4194304` (`:107`) tampoco se declara como `truncado`.
- **Escenario:** el Mac dice «Ningún dominio detectado apunta a este servidor» cuando en realidad no ha llegado a leer nada.
- **Cambio:**
  - Anotar en un fichero temporal (como `MARCAS`, pero propio) cada salida 124, y si hay alguna emitir `fuente error`.
  - Con el plazo global de A1, si ese `timeout` expira, `fuente error`.
  - Declarar `truncado si` si se llegó a los 4 MiB.

### M2. La clasificación depende de que `host_de_ssh` dé la IP **pública** del servidor; si no, todo cae en «otros» sin explicación. Con Cloudflare, el caso habitual sale como «sospechoso» [V código; S frecuencia]
- **Evidencia:**
  - `clasificar` compara con `todo.get(host_servidor, set())` (`servidores_webs.py:116`, `:124`).
  - Si el SSH va por Tailscale/VPN/LAN (IP 100.64/10 o privada), o si el host no resuelve, `del_servidor` no coincide con nada. Hay un test que lo da por bueno (`test_si_el_servidor_no_resuelve_nada_apunta_a_nada`).
  - Las webs detrás de Cloudflare también acaban en «otros», con un texto que habla de «un tercero» (`ajustes.js:150`).
- **Escenario:** el dueño típico de Dokploy con Cloudflare y SSH por Tailscale ve «Apuntan a este servidor: ninguno» y todo plegado bajo una advertencia. La función no le sirve, aunque puede marcarlas igualmente.
- **Cambio:**
  - (a) Si la IP del servidor no es pública o no resuelve, devolver `estado_ip: "privada" | "sin_resolver"` y que la página lo diga («no puedo comprobar a dónde apuntan: el SSH va por una red privada»).
  - (b) Añadir el grupo «Detrás de una CDN» con los rangos publicados de Cloudflare (lista estática pequeña) y un texto neutro.
  - (c) La prueba más fuerte e independiente de la red: abrir TLS contra la IP del servidor con SNI = dominio y comprobar que el certificado que presenta cubre el nombre (sin validar la cadena, porque los certificados de origen de Cloudflare no son públicos). Si lo cubre, «lo sirve este servidor», tanto detrás de una CDN como con SSH privado.
  - Con (c), M9 (dominio que apunta a otro servidor) queda bien resuelto. Sigue faltando el aviso de duplicados entre servidores.

### M3. H21 no se cumple: quitar una web no borra «de inmediato» su estado, y quitar por dominio borra también las URL manuales con ruta [V]
- **Evidencia:**
  - `cambiar` solo toca `servidores.json`.
  - `logs/webs.json` se limpia en la **siguiente** `pasada` (`webs_sondeo.py:173-174`).
  - Las medidas cacheadas en las tarjetas siguen ahí hasta el siguiente ciclo.
  - El diseño (§3.7b, último punto) dice «borra de inmediato su estado».
  - Además, `quitar` compara por host (`servidores_webs.py:187`): quitar `a.example.com` borra también una `https://a.example.com/salud` que el dueño había escrito a mano.
- **Cambio:**
  - En `atiende_cambio` (y en `/guardar`), purgar de `webs.json` las URL que han dejado de estar configuradas, bajo el cerrojo del sondeo.
  - Que la página mande en `quitar` las **URL exactas** que salen en «Ya vigiladas» (la respuesta ya puede incluirlas) y que `cambiar` quite por URL exacta y, solo como respaldo, por host.
  - Cuando exista el historial (3.7c), borrar ahí también.

### M4. Tests: protecciones de seguridad sin test (sobreviven 4 mutaciones) y un test que no comprueba nada [V ejecutado]
- **Mutaciones que sobreviven** al cambiarlas en una copia del script:
  - `head -c 1048576 "$f"` → `cat "$f"`;
  - quitar `| head -c 4194304`;
  - quitar `-maxdepth 1`;
  - quitar `-type f`.
- La de `-type f` es la que impide leer enlaces simbólicos y FIFO como root: funciona (lo he probado con un enlace a un fichero de fuera, una FIFO y un subdirectorio: solo salió el fichero normal), pero nada la protege de una regresión.
- `grande2.yml` (`tests/test_servidores_webs.py:168`) se crea pero no se comprueba nada sobre él. Además pesa ~1,2 MB, así que `-size -2M` lo excluye entero y el `head -c` nunca llega a ejecutarse.
- No hay ningún test del tiempo de la sección (A1, M1).
- Los tests dependen de `timeout` y `tac` de GNU en el PATH (Homebrew). Si faltan fallan de forma ruidosa, pero no está documentado.
- **Cambio:**
  - Tests con un enlace `*.yml` a un fichero con `Host(...)` fuera del directorio, con una FIFO `*.yml` (con un tiempo máximo) y con un subdirectorio.
  - Un fichero de 1 MiB − 100 B con el dominio pasado el primer MiB… o mejor bajar el tope de `-size` a `-1025k` y comprobar el corte de `head`.
  - Un docker que se cuelga solo en `webs` → `fuente error` y `fin_root` presente.
  - Corregir «31 tests» en el diseño.

## Bajo

- **B1. Fuga acotada de otros valores de la misma línea [V experimento].** `[^)]*` (`recoger-root.sh:107`) cruza de una etiqueta a otra dentro del JSON de un mismo servicio o contenedor. Con `{"a.rule":"Host(\`x.example.com\`", "b.token":"\`tok3n-secreto.abc\`", "c":"fin)"}`
  salieron `x.example.com` y **`tok3n-secreto.abc`**. Solo puede escapar algo que tenga forma DNS, vaya entre comillas inversas y sea del mismo contenedor, y la persona que lo recibe es el propio dueño (que ya es root). Aun así, eso acaba en la caché del Mac.
  Cambio: un patrón con la gramática real, ``grep -oE 'Host(SNI)?\(( *`[^`]{1,253}` *,?)+\)'``.
- **B2. Cobertura [V detección; S sintaxis de Traefik].**
  - `Host("a.example.com")` (comillas dobles, que Traefik admite) no se detecta.
  - Los IDN en Unicode crudo se descartan, porque no se pasan a punycode.
  - `-a` incluye contenedores parados (apps borradas).
  - Los autogenerados `*.traefik.me` / `*.sslip.io` resuelven a la IP que llevan incrustada y salen **primeros**, en «apuntan» (M1 del pre-mortem pedía marcarlos).
  - Ni los comodines ni `HostRegexp` se cuentan, así que la página no puede decir «hay N reglas que no se pueden vigilar».
- **B3. Hilos de DNS [V].** La docstring dice «hilos daemon», pero los de `ThreadPoolExecutor` no lo son en Python 3.9+. Un `getaddrinfo` colgado retrasa la salida del proceso (medido: un pool con una tarea de 4 s bloqueó la salida 4 s). Además, cuando se agota el presupuesto de 10 s, lo que no ha llegado se presenta como «no resuelven» y se guarda 10 min.
  Cambio: hilos `threading.Thread(daemon=True)` propios y un grupo `sin_tiempo` aparte, que no se guarde en la caché.
- **B4. `host_valido` deja pasar `.onion`, `.test`, `.invalid` e `in-addr.arpa` [V].** Esos nombres se resuelven igualmente: consultas inútiles, y en el caso de `.onion`, una fuga de la que avisa el RFC 7686. Añadirlos a `_DNS_INTERNOS`.
  Aparte, y es inherente: resolver hasta 200 dominios de clientes con el resolver del Mac se los enseña a ese resolver. Conviene decirlo en el README.
- **B5. El error 500 promete un registro que no se escribe [V].** `atiende_cambio` captura `Exception` y responde «queda en el log» (`servidores_webs.py:264-265`), pero no llama a `log`. Registrar el tipo de la excepción, sin dominios.
- **B6. `_CACHE` no se poda nunca [V].** Guarda en memoria listas de dominios de servidores borrados y más allá de las 48 h. Podar las entradas de más de `CACHE_S`.
- **B7. JS [V código].**
  - Tras «Vigilar», `s.webs = r.webs` pisa lo que se hubiera tecleado sin guardar en el textarea de webs de **ese** servidor (`leer()` lo había copiado antes).
  - «Vigilar las marcadas» sin ninguna marcada no hace nada y no lo dice.
  - El parámetro `grupo` de `fila` no se usa.
- **B8. `interpretar` acepta `total < len(hosts)` [V]**, una incoherencia del servidor. Exigir `total >= len(hosts) + descartados`, o al menos `>= len(hosts)`.
- **B9. TOCTOU entre `find -type f` y `head` [S].** Quien pueda escribir en el directorio del proxy podría cambiar el fichero por un enlace a `/etc/shadow` en ese intervalo. El efecto queda acotado por el doble filtro: solo saldría algo que esté dentro de `Host(...)` y tenga forma DNS. Se podría cerrar con `head` sobre `/proc/self/fd` o comprobando con `[ -L ]` dentro del `sh -c`, pero no es urgente.
- **B10. Homógrafos [V].** `decodificado("xn--pple-43d.com")` da «аpple.com» (con una а cirílica). La página enseña el `xn--` junto al Unicode, cosa que H14 pedía evitar, pero no avisa de que mezcla alfabetos.
  Cambio: si la etiqueta decodificada mezcla alfabetos, añadir «(mezcla alfabetos)».

## Comprobado y correcto

- Por la salida solo pueden pasar nombres con forma DNS en minúsculas; los secretos de las etiquetas y de los ficheros de la prueba no salen [V tests + B1].
- `find` sin `-L` no sigue los enlaces; una FIFO o un subdirectorio no se leen [V en macOS; S que con GNU `find` (`-P` por defecto) es igual].
- `timeout` mata el grupo de procesos, `sh -c` incluido [S por el comportamiento conocido de GNU timeout].
- No hay límite de argumentos que preocupe: son ids de 12 a 25 caracteres. No hay `set -e` ni `pipefail`, y por eso `[ -d … ] &&` no corta nada.
- Ningún glob se aplica a nombres de contenedor o de servicio (H17) [V].
- Un salto de línea en una etiqueta no puede abrir secciones nuevas [V test].
- El GET exige sesión (cookie `HttpOnly; SameSite=Strict`) y Host correcto; el POST exige además Origin [V test]. Hay un solo escaneo a la vez (`_BUSCANDO`, 429) y los parámetros se validan (id `[0-9a-f]{32}`, un solo `servidor`).
- `host_de_ssh` usa `ssh -G` con el `ssh_config` del dueño, lo mismo que hace la lectura (`OPCIONES_SEGURAS` no lleva `-F`), así que hay coherencia. Ojo: `-G` evalúa los `Match exec` del dueño, que son órdenes suyas.
- Rebinding y SSRF: la clasificación es solo orientativa. `medir_web` vuelve a resolver, rechaza el dominio si alguna IP no es pública y se conecta a la IP ya comprobada (`servidores_externo.py:18-26`, `:76`) [V]. NAT64 y 6to4 se tratan por la IPv4 que llevan dentro [V test].
- `cambiar` funciona por id, dentro del cerrojo de `servidores.json`. Si lanza `ErrorWebs` dentro de la función, no se escribe nada [V test «nada a medias»]. Solo se acepta lo detectado y con forma válida, con un tope de 100.
- La página construye todo con `createElement`/`createTextNode`: no hay `innerHTML` [V `ajustes.js:7-14`]. La respuesta pesa poco (≤ 200 nombres), y ni el log ni los mensajes llevan dominios.

## Acciones recomendadas (por orden)
1. A1 + M1: plazo global en `webs`, detectar los `timeout`, `docker ps -q` con tope, sello diario y, quizá, `webs` después de `fin_root`.
2. A2: `servidores_rev` en el POST, comprobada bajo el cerrojo, y 409 si no coincide.
3. M4: tests de enlace, FIFO, subdirectorio, tope de `head` y docker colgado.
4. M2: estado de la IP del servidor, grupo CDN y comprobación por SNI y certificado.
5. M3: purga inmediata de `webs.json` y quitar por URL exacta.
6. Bajos según convenga (B1 y B3 son baratos).

## Preguntas abiertas
- ¿Se acepta que la detección vaya después de `fin_root`, fuera de la regla «omitidos > 0 descarta root»? Simplifica A1.
- ¿Basta la comprobación por SNI y certificado (M2c) como prueba de «lo sirve este servidor», o el dueño quiere además los rangos de CDN?
- ¿Hay que purgar de inmediato también cuando las webs se quitan desde el textarea con `/guardar`, y no solo desde el detector?

Status: DONE_WITH_CONCERNS
Summary: La tubería de root no filtra nada que no tenga forma DNS y la parte del Mac está bien protegida, pero la sección `webs` no tiene un plazo global (puede tirar todas las secciones de root) y el POST de webs entrega una revisión que reabre la pérdida de escrituras de C1. Hay además huecos de producto (Cloudflare, SSH por red privada), H21 solo a medias y cuatro mutaciones de las protecciones del script que ningún test detecta.
