# Revisión de la fase 0 (rama `feat/fase0-seguridad-y-conteo`)

Fecha: 2026-10-14 · Alcance: `git diff main...feat/fase0-seguridad-y-conteo` (17 ficheros, unas 440 líneas
añadidas) · Modo: solo lectura · Plan: `plans/261013-2350-sqlite-historico/plan.md` (fase 0) · Amenazas:
`plans/reports/red-team-261013-sqlite-historico.md` §1.1 y §1.2.

## Resultado

La sesión cierra el camino de §1.1 tal como estaba descrito: un `curl` sin cookie recibe 401 en todo,
incluidas las lecturas. Los permisos de §1.2 están bien encaminados. La corrección del conteo de Claude Code
es correcta para lo que dice hacer. Los 106 tests pasan (`python3 -m unittest discover tests`, Python
3.14.6, el mismo que usa el venv instalado).

Aun así quedan dos huecos reales en la sesión:

- el ticket se puede robar desde `ps`;
- la cookie no queda aislada por puerto.

Además hay un fallo de disponibilidad: el parseo de `Cookie` puede dejar al usuario sin acceso. Del plan
0a/0b faltan cuatro cosas: la deduplicación entre ficheros, el `CLIFF` por turno, la nota de versión en la
interfaz y la limpieza de rutas en un `log()`.

## Hallazgos (por gravedad)

### 1. ALTA: el ticket de un solo uso se ve en `ps` y otro usuario puede canjearlo antes que el navegador
- Evidencia:
  - `barra.py:333` abre la URL con `subprocess.Popen(["open", url])`.
  - La URL lleva el ticket: `infobar/servidor_local.py:83` (`/entrar?t=<ticket>`).
  - En macOS los argumentos de procesos de otros usuarios son legibles con `ps -axo user,args`. Comprobado en
    esta máquina con procesos de root.
- Ataque:
  1. Otro usuario local sondea `ps` en bucle, ve `open http://127.0.0.1:47391/entrar?t=…` y lanza
     `curl -H 'Host: 127.0.0.1:47391' '…/entrar?t=…'`. No envía `Sec-Fetch-Site`, así que `_host_ok` lo deja
     pasar.
  2. Su `curl` es local e inmediato, mientras que el navegador pasa antes por LaunchServices. El atacante
     gana la carrera y obtiene una sesión de 12 h con lectura y escritura completas, incluidos
     `POST /guardar` y `POST /clave`.
  3. El usuario solo ve un 401 «Ticket no válido».
- Es justo el actor de §1.1 («otro usuario/proceso del Mac»).
- Arreglo: abrir la URL dentro del proceso, sin `argv`:
  `NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))`. PyObjC ya está cargado en la barra.
  Añadir un test que compruebe que `boton("Abrir panel")` no lanza un subproceso con el ticket.

### 2. ALTA (disponibilidad): una cookie ajena «rara» en 127.0.0.1 deja al usuario sin sesión
- Evidencia: `infobar/servidor_local.py:66-71` usa `SimpleCookie(cabecera)`. Probado en el venv instalado
  (Python 3.14.6):
  - `grafana={"x":1}; cg_sesion=XYZ` devuelve que no hay `cg_sesion`;
  - `foo=bar baz; cg_sesion=XYZ` devuelve lo mismo;
  - `tok="a\"b; cg_sesion=XYZ` devuelve lo mismo.
- Motivo: `SimpleCookie` deja de parsear en la primera cookie que no cumple el RFC. Las cookies de
  `127.0.0.1` se comparten con todos los puertos (Jupyter, Grafana, servidores de desarrollo…). Basta una
  cookie con JSON o espacios puesta por otra app local para que el panel responda 401 siempre, aunque la
  sesión sea válida.
- Arreglo: parsear a mano. Recorrer `cabecera.split(";")`, aplicar `partition("=")` y `strip()` a cada trozo,
  y probar todos los valores que se llamen `cg_sesion`. Añadir tests con cookies mezcladas y duplicadas.

### 3. MEDIA-ALTA: la cookie no queda aislada por puerto (riesgo residual del diseño TCP) y el README promete de más
- Evidencia: `infobar/servidor_local.py:191` emite una cookie de solo host para `127.0.0.1`. Según RFC 6265
  §8.5 las cookies no se aíslan por puerto, y SameSite tampoco tiene en cuenta el puerto.
- Primer efecto: el navegador envía `cg_sesion` a cualquier otro servidor en `127.0.0.1:<otro puerto>` al
  que navegue el usuario. Basta que la página de ese servidor se recargue o navegue a sí misma (petición
  same-site). Si ese servidor es de otro usuario o es un servidor de desarrollo comprometido, se queda la
  cookie y la reutiliza con `curl` durante 12 h.
- Segundo efecto: ese mismo servidor puede sobrescribir `cg_sesion` (mismo nombre, host y ruta) y provocar un
  DoS de la sesión.
- El README (`README.md:165-167`) afirma que «ningún otro proceso del Mac puede leer tus proyectos ni tus
  servidores». Con los hallazgos 1 y 3 no es cierto.
- Arreglo mínimo:
  - documentarlo como riesgo residual y suavizar el README;
  - acortar `SESION_VIDA_S` o renovarla por uso;
  - opcionalmente, un `Path` aleatorio por arranque (`/s/<aleatorio>/`). Así la cookie solo viaja a esa
    ruta y no a otros puertos con rutas normales.
- Solución de fondo: un socket Unix `0600`, que ya proponía §1.1.

### 4. MEDIA: `CLIFF` sigue aplicándose sobre agregados, no por turno (lo pide 0b)
- Evidencia:
  - `infobar/agregacion.py:139` y `:149` hacen `over = v[0] > CLIFF`, con `v` igual a la suma de un fichero,
    un día y un modelo.
  - `infobar/lectores_logs.py:318` (`mete`) compara la suma por hora de OpenCode (`:373`, `group by …/3600000`)
    y el total de sesión de Hermes.
- Efecto: un día o una hora con más de 200K de entrada fresca se cobra entero al doble. Además, el USD
  diario (`dias[d]["usd"]`) no cuadra con la suma de `horas[*].usd`, que sí va por turno (`:101`).
- No es una regresión: ya pasaba antes. Pero el diff toca esas mismas líneas y ahora la base del doble es
  mayor, porque ya no se resta la caché.
- Arreglo: que `dias` y `modelos_hoy` sumen el USD que ya calcula `parsear` por turno, en lugar de
  recalcularlo sobre agregados. Para OpenCode, agrupar por mensaje.

### 5. MEDIA: falta la deduplicación entre ficheros (forks y reanudaciones) que pide 0b
- Evidencia: `vistos` (`infobar/lectores_logs.py:79`) y `pend` (`:141`) viven dentro de cada llamada a
  `parsear`, es decir, por fichero.
- En una muestra de 400 ficheros recientes, 6 `message.id` aparecen en más de un fichero y se cuentan dos
  veces. El efecto es pequeño hoy y no hay regresión, porque antes también se duplicaban.
- Arreglo: deduplicar por `message.id` en `agregacion.escanear` (por ejemplo, `parsear` devuelve los ids
  contados y se descartan los repetidos al unir), o dejarlo para la fase 1, donde el id será la clave del
  turno. El plan debería decir cuál de las dos.

### 6. MEDIA-BAJA: la detección de «instalación» en `permisos.py` mira la variable equivocada
- Evidencia: `infobar/permisos.py:18-20` da por instalación cualquier proceso con `COSTGUARD_HOME` definida,
  apunte adonde apunte. Pero `rutas.RAIZ` sale de `__file__`, no de esa variable (`infobar/rutas.py:9`).
- Dos efectos:
  - Si tienes `COSTGUARD_HOME` exportada y ejecutas desde el checkout de desarrollo, este pasa a `0700`
    (`:37`).
  - Al revés: bajo launchd la variable no existe, así que una instalación en una ruta propia cuyo nombre no
    es `.cost-guard` nunca se corrige al arrancar. Solo la corrige `install.sh`.
- El test `test_un_checkout_de_desarrollo…` no cubre el caso «variable definida que apunta a otro sitio».
- Arreglo: `os.path.realpath(rutas.RAIZ) == os.path.realpath(os.environ.get("COSTGUARD_HOME") or "~/.cost-guard" expandido)`.

### 7. MEDIA-BAJA: partes de 0a/0b sin hacer
- `log()` con rutas: `infobar/lectores_logs.py:400` sigue escribiendo `hermes {db}: … {e}`, una ruta absoluta
  con el usuario. Solo se limpió la línea de OpenCode.
- Renders: el plan pide «borrados tras usarlos». Solo se purgan los de más de 1 h, cuando se ejecuta
  `endurecer()` (`infobar/permisos.py:48-59`): al arrancar y cuando `panel.py` se regenera. Gracias a la
  umask quedan en 0600 dentro de un directorio 0700, así que esto es retención, no exposición.
- Nota de versión en la interfaz (0b): no existe. Solo está en el README.

### 8. BAJA: `hmac.compare_digest` lanza `TypeError` con una cookie que no es ASCII
- Evidencia: con `Cookie: cg_sesion="é"`, `SimpleCookie` devuelve `é` y `compare_digest` lanza
  `TypeError`, fuera de cualquier `try` (`infobar/servidor_local.py:75`), en cuanto existe alguna sesión.
- Efecto: la conexión se cierra sin respuesta y la traza acaba en `barra.err`. Falla cerrado (no da acceso),
  pero cualquiera puede llenar el log de trazas. Se arregla junto con el parseo manual del hallazgo 2
  (rechazar valores que no sean `[A-Za-z0-9_-]`).

### 9. BAJA: casos de uso que se rompen al exigir sesión
- Las sesiones viven en memoria (`infobar/servidor_local.py:320`). Hay tres casos en los que la pestaña abierta
  deja de funcionar:
  - Tras un reinicio de la barra (KeepAlive, inicio de sesión, actualización), el `location.reload()` cada
    60 s (`panel_assets/ajustes.js:180`) recibe un 401 en texto plano y se queda ahí, sin JS que avise.
  - Pasa lo mismo a las 12 h, porque la caducidad no se renueva con el uso.
  - Pasa lo mismo cuando se abren más de 20 pestañas, porque se expulsa la más antigua (`:58-59`).
- Si el puerto está ocupado (otra copia que aún se cierra), `url_panel()` devuelve la URL sin ticket
  (`:81-84`) y el usuario recibe un 401. Si quien ocupa el puerto es otro usuario, el navegador abre su
  panel falso, donde el usuario podría teclear claves. Este riesgo ya existía: la sesión no autentica el
  servidor ante el usuario.
- Lo demás sigue funcionando:
  - La barra con `url_panel()`.
  - `panel.py` regenerado por el servidor (es un subproceso, no habla HTTP).
  - El popover (`popover.html` es local).
  - El `abrir_panel` de rumps (`costbar.py:80-87`), que abre `panel.html` como fichero con los ajustes ya
    ocultos.

### 10. Tests que no prueban lo que dicen o que faltan
- `test_coste_no_resta_la_cache_a_la_entrada_fresca` (`tests/test_costbar.py`) solo comprueba `horas` en
  `parsear`. No cubre el cambio de fórmula en `agregacion.py:139-151`, ni `parsear_pi` (`:244`), ni
  `leer_bases` (`:318`).
- No hay tests de:
  - una línea de Codex u otro formato con `message.id` (que se mantenga la ruta antigua);
  - un id repetido en dos ficheros;
  - una cabecera `Cookie` con varias cookies o con cookies raras;
  - la secuencia real del navegador (`Sec-Fetch-Site: none` en `/entrar` y luego `same-origin` en `/`).
- `test_sin_cookie_nada_responde_ni_la_lectura` es correcto, pero no cubre un `curl` con un ticket robado
  (hallazgo 1). Ese caso no se puede cubrir mientras el ticket vaya en `argv`.

## Bien resuelto (verificado)
- HEAD, OPTIONS y PUT responden 501, y no hay `do_*` sin comprobar.
- HTTP/1.0 cierra la conexión tras cada respuesta, así que `_extra` (Set-Cookie) no se filtra a otra
  respuesta. Es latente: si algún día se pasa a HTTP/1.1, hay que limpiarlo en `_manda`.
- El orden de las comprobaciones es correcto: Host y `Sec-Fetch-Site`, luego `/entrar`, luego la sesión. La
  ruta y el Origin solo se miran después. Un 404 sin sesión responde 401, así que no se pueden enumerar
  rutas.
- No hay redirección abierta, porque `Location` es fija a `/`.
- No hay fijación de sesión, porque el sid lo genera el servidor.
- El ticket se gasta bajo cerrojo antes de mirar si ha caducado, así que la carrera entre hilos no permite
  canjearlo dos veces.
- Un `/entrar` lanzado desde otra web recibe 403 por `Sec-Fetch-Site`.
- Hay `no-store`, `no-referrer` y `X-Frame-Options`, y `log_message` está silenciado, así que el ticket no
  queda en el historial útil ni en el Referer ni en los logs del servidor.
- `Umask` 63 en el plist es 0o077, correcto. `chmod 700` en `install.sh` es correcto.
- La umask global solo afecta a los hijos de la barra (`panel.py`, `ssh`). El navegador y Terminal los lanza
  launchd y no la heredan. Los ficheros de código los copia `install.sh` con la umask del shell.
- Los symlinks no son un vector: nadie más puede escribir en el directorio.
- En Windows, `os.umask` es inofensiva y se sale antes de llegar a `chmod`.
- La fórmula sin restar la caché es correcta también fuera de Claude. Comprobado en datos locales: la entrada
  es menor que `cache_read` en 9466 de 9509 filas de pi, 3650 de 4142 de OpenCode y 40 de 43 de Hermes, así
  que esas fuentes ya excluyen la caché de la entrada.
- `mid` sin Codex: las líneas de Codex no tienen `message` en el nivel superior y `es_codex` las excluye, así
  que no cambia su resultado.
- Última línea frente a la primera: en 30402 mensajes con varias líneas, la salida de la última siempre es la
  máxima. El `ts` y el modelo de la última línea solo pueden mover el mensaje de hora si cruza el límite de
  una hora, lo que es despreciable.
- La caché de `parsear` va por fichero (`mtime:size`) y se reparsea entero, así que `pend` no parte mensajes
  entre lecturas. `VERSION_ESTADO` 12 fuerza a reescanear.
- Guardar el ticket en memoria, en lugar del fichero 0600 que proponía el plan, es mejor, porque la barra y el
  servidor comparten proceso.

## Acciones recomendadas (orden)
1. Abrir el panel con `NSWorkspace.openURL_` (hallazgo 1).
2. Parsear `Cookie` a mano y validar el charset del valor (hallazgos 2 y 8).
3. Documentar el riesgo de la cookie entre puertos, corregir el README y valorar un `Path` aleatorio o el
   socket Unix (hallazgo 3).
4. Calcular el USD diario y por modelo a partir del coste por turno (hallazgo 4).
5. Decidir si la deduplicación entre ficheros entra en la fase 0 o en la 1 (hallazgo 5).
6. Corregir `_es_instalacion`, la línea de log de Hermes y añadir la nota de versión (hallazgos 6 y 7).
7. Añadir los tests del hallazgo 10.

## Métricas
- Tests: 106 que pasan, ninguno falla.
- Lint y tipos: no se han ejecutado; el proyecto no tiene configuración de lint ni de tipos.

## Preguntas abiertas
- ¿La deduplicación por `message.id` entre ficheros se hace ya o se deja como clave del turno en la fase 1?
- ¿Se acepta el riesgo residual de la cookie entre puertos hasta tener un socket Unix o autenticación real, o
  se quiere el `Path` aleatorio ya?
- Hermes: 3 de 43 filas tienen una entrada mayor o igual que `cache_read`. Hay que confirmar con su
  documentación que `input_tokens` excluye siempre la caché.
