# Revisión de código independiente — Fase 3.3a, entrega E2 (commit 6a8164a)

Alcance: `git diff 6a8164a~1 6a8164a` (11 ficheros, +875/−25). Contrastado con `fase3-3a-diseno.md` (E2 y «Cambios tras la revisión de E1»),
`premortem-fase3-3a-261015.md` y `redteam-fase3-3a-261015.md`. Revisión de solo lectura: no se ha tocado código ni tests.
Marcas: **[V]** verificado (lectura del código y ejecución local), **[S]** supuesto (razonado, sin ejecutar).

## Resultado

La plantilla de `docker inspect` es segura en lo principal: no filtra entorno, `Cmd`, montajes ni `Health.Log`; `{{json}}` impide que un valor rompa
líneas o tabuladores, y un fallo SSH no genera eventos. Aun así, hay **3 hallazgos altos** que conviene corregir antes de fusionar:

1. Un único contenedor «raro» (nombre largo legítimo o etiqueta hostil) desactiva para siempre las conclusiones del inventario de ese servidor.
2. El presupuesto por tipo de evento lo agotan los efímeros. Cuando eso pasa, se pierde el evento real de producción y el estado avanza sin dejar rastro.
3. Un `U+0085` en una etiqueta parte la línea en el Mac y permite a un cliente dejar «sin dato» todas las secciones de root.

Hay, además, desviaciones del diseño (no existen `colision` ni `fallos`, el bucle mezcla relojes, la expulsión no protege lo reciente) y huecos en la privacidad (D‑C) fuera de la tabla.

Tests: 33/33 OK en 1,4 s [V]; `bash -n recoger-root.sh` OK [V]; shellcheck no está instalado.
**Aviso:** al ejecutar la suite en este Mac, la clase `PlantillaConDockerRealTest` se ejecutó porque el demonio Docker responde. Creó y borró un contenedor `cg-prueba-*`.
Después he comprobado con `docker ps -a --filter name=cg-prueba -q` que no queda ninguno (0).

---

## Altos

### A1. Un contenedor fuera de patrón anula todas las conclusiones del servidor, de forma permanente [V]
- **Evidencia:** `servidores_inventario.py:11` (`NOMBRE` admite ≤ 63 caracteres), `:69-72` (cualquier campo inválido descarta la línea), `:111` (`completo = descartados == 0 and ...`);
  `servidores_recursos.py:79-81` (sin `completo` no se anota nada); `recoger-root.sh:74` (`cut -b -600`).
- **Escenario:**
  - **Legítimo.** Los contenedores de tarea de un servicio Swarm **global** se llaman `servicio.<id_nodo 25>.<id_tarea 25>`. Con un nombre de servicio de más de unos 11 caracteres, el total pasa de 63.
    En replicados, el límite se supera con un servicio de más de unos 34 caracteres. También lo superan los nombres de Compose largos (`proyecto-servicio-1`).
  - **Hostil.** Un cliente crea un contenedor con una etiqueta `com.docker.compose.project` fuera del juego de caracteres o de más de 600 B.
  - **Posible.** `ExitCode` negativo [S].
  - En los tres casos, el inventario queda `completo=False` en cada lectura. Nunca vuelve a haber `aparecio` ni `desaparecio` en ese servidor y solo crece
    `descartes:inventario_incompleto` en `meta`, donde nadie lo ve.
  - Simulado: base `{a, b}`; desaparece `b` y hay un contenedor con un nombre de 70 caracteres. Tras 9 lecturas en 90 min no hay ningún evento y `b` sigue activo.
- **Diseño:** «Un dato imposible descarta **solo ese contenedor** (no la lectura entera)».
- **Cambio propuesto:**
  - Separar dos tipos de error:
    - **Errores de protocolo** (cabeceras, número de campos, JSON roto): invalidan la sección.
    - **Datos fuera de lista** en un contenedor bien formado: se cuentan y se ignoran.
  - Para los nombres, usar el patrón de Docker (`[A-Za-z0-9][A-Za-z0-9_.-]*`) con hasta 255 caracteres. El `CHECK` de `etiqueta` admite hasta 130, así que habrá que subirlo o recortar con un hash.
  - Una etiqueta de Compose o Swarm inválida debe valer como **ausente** (la identidad cae al nombre), no descartar el contenedor.
  - Admitir un `ExitCode` acotado en `[-1, 65535]`.
  - Subir `cut -b` a un tope que quepa con nombres de 255.

### A2. Los efímeros agotan el presupuesto por tipo, y el evento de producción se pierde mientras el estado avanza [V]
- **Evidencia:** `servidores_recursos.py:47-51` (100 por tipo y día, sin prioridad por severidad); `:131-132` (`activo=0` se escribe **antes** de que `_evento` decida si cabe).
- **Escenario A (simulado):** 100 contenedores de CI desaparecen por la mañana y producen 100 `desaparecio`. Por la tarde desaparece `prod`: **0 eventos** y `activo=0`.
  En la lectura siguiente ya no es «nuevo», así que la desaparición queda sin registrar y sin forma de recuperarla.
- **Escenario I (simulado):** 100 *jobs* que pasan de `running` a `exited(0)` producen 100 `cambio_estado` de severidad `info`. Después, `prod` cae con 137 (`aviso`) y se descarta.
- Un cliente con trabajos que duran más de 10 min (o que terminan sin `--rm`) basta para cegar las alertas útiles del día. La tabla en sí sí está acotada.
- **Cambio propuesto:**
  - Presupuesto por **severidad**, con cupo reservado para `aviso` y `critica`. Opcionalmente, prioridad para los recursos de la línea base o con `primera` antigua.
  - Al agotar el cupo, dejar un evento único por servidor y día del tipo «presupuesto agotado». Si no, no avanzar `activo` y conservar el estado pendiente.
  - Añadir un test con 100 efímeros más un `desaparecio` de producción.

### A3. `U+0085` en una etiqueta reabre secciones en el Mac y deja «sin dato» todo lo de root [V en Python, S en Go]
- **Evidencia:** `servidores_ssh.py:125` (`texto.splitlines()`). Python también corta en `U+0085`, `U+2028`, `U+2029`, `\x0b`, `\x0c` y `\x1c-\x1e`.
  El `json` de las plantillas de Docker (`json.Encoder` con `SetEscapeHTML(false)`) escapa los caracteres de control `< 0x20` y `U+2028/2029`, pero **no `U+0085`** [S].
- **Escenario (simulado con el parser real):** etiqueta `"p\u0085### fin_root\u0085omitidos 0"` sin escapar.
  - Resultado: `secciones=['version','version_root']`, `invalidas=['fin_root','root']`.
  - Se descartan `servicios`, `contenedores`, `bastionado_root`, `fail2ban` e `inventario`.
  - Un cliente puede, por tanto, apagar de forma permanente los controles de fail2ban, cortafuegos y sshd de la tarjeta.
  - No he encontrado forma de **falsificar** datos: el marco `n`/`fin` invalida la sección alterada.
  - Lo mismo puede aplicarse a las líneas de `intentos` de E1, donde el texto lo elige el atacante [S].
- **Cambio propuesto:**
  - En `analizar`, partir solo por `"\n"` (`texto.split("\n")` y `rstrip("\r")`).
  - Como defensa adicional, en `recoger-root.sh` filtrar la secuencia `\xc2\x85` (`LC_ALL=C sed 's/\xc2\x85/?/g'`).
  - Añadir a la prueba con Docker real una etiqueta con `\u0085`, y al test sintético una con el carácter sin escapar.

## Medios

### M1. La identidad Compose se puede suplantar y no existe `colision` [V]
- **Evidencia:** `servidores_inventario.py:133-148`. `EVENTOS` (`servidores_recursos.py:23`) y el `CHECK` de `eventos` no incluyen `colision`.
- **Escenario:**
  - Cualquier contenedor con `project=victima, service=web` se suma a `("compose","victima/web")`.
  - Un contenedor `exited(1)` así etiquetado deja ese recurso en «caido» y genera `cambio_estado`.
  - Uno vivo lo mantiene «presente» cuando los contenedores reales desaparecen, y oculta el `desaparecio`.
  - Si Compose permite o no fijar esas etiquetas reservadas desde un fichero de cliente está por verificar [S]. Con `docker run --label` sí se puede.
- **Diseño:** «project válido y único por servicio; una colisión genera `colision` y bloquea las desapariciones».
- **Cambio propuesto:** implementar la colisión, o exigir coherencia entre nombre y etiquetas (`<project>-<service>-<n>` o `<project>_<service>_<n>`) para aceptar la identidad Compose.

### M2. El reinicio en bucle compara el reloj del Mac con el del servidor [V]
- **Evidencia:** `servidores_recursos.py:112-113` (`ts - r["inicio"]`, donde `inicio` es `StartedAt` del servidor).
- **Escenario C (simulado):** con el servidor 15 min atrasado, tres lecturas con `RestartCount` creciente y arranque hace 1 min no producen ningún evento.
  Con el servidor adelantado, `ts - inicio < 0` y tampoco salta.
- **Diseño:** «tiempos medidos con `ok_en` del Mac, no con el reloj del servidor».
- **Cambio propuesto:** decidir solo con el Mac. Si `RestartCount` sube **y** `StartedAt` cambia respecto a la lectura anterior en ≥ 2 lecturas consecutivas separadas menos de N s de `ok_en`, hay bucle.

### M3. En servidores truncados (> 200) los activos nunca desaparecen ni se purgan [V]
- **Evidencia:** `servidores_recursos.py:120`; `purgar` solo borra inactivos (`:140`).
- **Escenario H (simulado):** 6 recursos activos acumulados sin ningún `desaparecio`. Con el tiempo, los 500 huecos se llenan de activos «fantasma».
  `_hueco` deja de admitir nuevos (`recursos_sobre_tope`) y el servidor queda congelado.
- **Cambio propuesto:**
  - Como se inspeccionan los **más antiguos**, un recurso cuyo contenedor es más antiguo que el más nuevo inspeccionado y que falta, ha desaparecido de verdad. Para eso hay que añadir `.Created` a la plantilla y guardarlo.
  - Como mínimo, retirar activos no vistos en lecturas truncadas tras un plazo largo, sin evento.

### M4. La identidad Swarm cambia cuando falta `swarm_servicios` [V]
- **Evidencia:** `servidores_ssh.py:250-252`; `recoger-root.sh:28-29` (`head -n 60`, y `docker info` puede fallar sin agotar el tiempo); `servidores_inventario.py:128`.
- **Escenario:**
  - Ocurre en nodos worker, cuando `docker info` devuelve error o cuando hay más de 60 servicios (los que pasan del 60 dejan de figurar).
  - Las tareas se convierten en `("contenedor", "svc.1.<tarea>")`: cada despliegue crea recursos nuevos y llena filas.
  - Si dura ≥ 600 s, se produce `desaparecio` de servicios reales y `aparecio` de tareas (lo mismo que el escenario B de cambio de tipo, que sí se reproduce).
- **Cambio propuesto:** si algún contenedor declara `swarm.service.name` y `swarm_servicios` está vacío, ausente o recortado, el inventario no concluye desapariciones. O excluir del inventario los contenedores de tarea y dejar que los servicios salgan solo de `service ls`.

### M5. Datos personales (D‑C): nombres fuera de `recursos.etiqueta` y claves verificables [V]
- **Caché JSON:**
  - `datos.servicios[*].nombre` (`servidores_ssh.py:278,286`) y `tarjetas.servicios.caidos` (`servidores_estado.py:59`) guardan nombres de contenedores y servicios **sin límite de tiempo**.
    El mecanismo es anterior a este commit, pero el commit (README:300-301 y el comentario de `servidores_ciclo.py:168`) afirma que los nombres «solo viven en la etiqueta» y no más de 48 h en la caché.
  - `--olvidar-recursos` tampoco toca la caché.
- **Seudonimización débil:** la sal está en `meta` de la **misma** base. Quien tenga el fichero puede comprobar candidatos («¿estuvo `cliente-x` en este servidor?») contra los eventos de 180 días aunque la etiqueta se purgue a los 30.
- **Correcto:** `eventos`, `meta` (descartes), el log de auditoría (solo números) y la copia previa (borra `recursos` y `eventos` en memoria antes de `VACUUM INTO`).
  `olvidar_recursos` borra los bytes de la base y del WAL (lo comprueba un test). `secure_delete` está activo en cada conexión (`sqlite_base.py:54`). Los retirados se purgan en `mantener`.
- **Cambio propuesto:**
  - Podar también `servicios` y `tarjetas.servicios.caidos` en `podar_ips`, y limpiar la caché desde `olvidar(…, "recursos")`.
  - Guardar la sal fuera de la base (Llavero), o cambiar por un valor aleatorio la `clave` de los eventos de un recurso al purgarlo.
  - Añadir un test de `podar_ips` con inventario, que ahora no existe.

### M6. El inventario puede dejar sin dato los controles de seguridad por tiempo [S]
- **Evidencia:** `recoger-root.sh:68,74`. Son dos comandos nuevos al final, cada uno de hasta 3 s, bajo el mismo `LIMITE=7`.
- **Escenario:** si `docker inspect` de 200 contenedores supera 3 s en un host cargado, o arranca pasado el límite, `omitidos > 0`. El Mac descarta **también** `fail2ban`, `bastionado_root` y `servicios`.
  La sección nueva, la más pesada, puede degradar de forma sistemática lo que ya funcionaba.
- **Cambio propuesto:** que un tiempo agotado o un salto del inventario se declare en su propia sección (`fuente error`) sin sumar a `MARCAS`, o darle un límite propio. Medir el tiempo en un host real con unos 200 contenedores.

### M7. La expulsión no protege lo reciente y puede borrar a mitad de bucle [V]
- **Evidencia:** `servidores_recursos.py:67-70`.
- **Problema 1:** expulsa el inactivo con `ultima` más antigua aunque tenga eventos recientes. El diseño pide «la expulsión protege a los activos **y a los que tienen eventos recientes**».
  Con una avalancha de nombres efímeros únicos, se pierde la etiqueta de un `desaparecio` de ayer.
- **Problema 2:** puede expulsar un recurso inactivo que **sí se ve en esta lectura** pero aún no se ha procesado en el bucle ordenado. Su `UPDATE` posterior no afecta a ninguna fila y el recurso vuelve como nuevo.
- **Cambio propuesto:** excluir de la expulsión las claves vistas en esta lectura y las que tengan eventos en los últimos N días. Expulsar primero los pendientes con `visto_n=1`.

### M8. Tests: lo que prueban, fragilidad y mutaciones que faltan [V]
- **Prueban lo que dicen:** parseo hostil, marco, identidad Swarm protegida, línea base, confirmación por tiempo, truncadas e incompletas, fallo SSH, retención, retirada, borrado de bytes, copia previa sin nombres y migración 2→3.
- **Test con Docker real:**
  - Se ejecuta **por defecto** si hay demonio y crea en el Docker del usuario un contenedor con `--env SECRETO=…` sobre la primera imagen local.
  - El `finally` lo borra, pero un SIGKILL o un tiempo agotado del ejecutor dejaría un `cg-prueba-*` creado (no arrancado).
  - `docker info` se ejecuta **al importar** con `timeout=30`. Si Docker Desktop está arrancando, salta `TimeoutExpired` y falla el módulo entero (33 tests).
  - `test_contenedores_existentes` depende de los contenedores reales: con el fallo A1, un nombre de más de 63 caracteres o una carrera `ps`/`inspect` lo harían fallar.
  - **Propuesta:** hacerlo opcional con una variable de entorno, etiquetar los contenedores de prueba (`--label cg-prueba=1`) y limpiar los restos por etiqueta en `setUpClass`.
- **`ScriptRootInventarioTest`:** enlaza `timeout` y `tac` con `shutil.which`. Sin coreutils de Homebrew falla con `TypeError` en vez de saltarse. Con el bash 3.2 de macOS funciona; aquí está verificado.
- **Hora del día:** no hay dependencia, porque `T0` es fijo y los escenarios no cruzan la medianoche UTC.
- **Mutaciones que no detecta ningún test:**
  1. Quitar `{{json}}` de un campo de texto: la lista blanca mira los nombres, no el envoltorio, y solo lo cubre la prueba con Docker real.
  2. Quitar `t` delante de `docker inspect` o el `cut -b -600`.
  3. Quitar la poda del inventario en `podar_ips`.
  4. El presupuesto por severidad de A2.
  5. `U+0085` (A3).
  6. Nombres legítimos de más de 63 caracteres (A1).
  7. Que el reloj del servidor esté desfasado en el caso del bucle (M2).

## Bajos

- **B1 [V]** La línea base se repite si la primera lectura tiene 0 contenedores, o tras purgarse todos los recursos (`base = not filas`, `servidores_recursos.py:85`): los primeros despliegues no generan `aparecio` (escenario G).
  Propuesta: guardar en `meta` la marca «base hecha» por servidor.
- **B2 [V]** Un cambio de tipo (contenedor suelto → Compose → Swarm) produce `desaparecio` más `aparecio` (escenario B). Es aceptable, pero conviene documentarlo.
- **B3 [V]** `anota_inventario` corre sin `SAVEPOINT` dentro de `registrar`. Una excepción inesperada revierte la lectura entera (métricas e IPs) y `Historial` apaga el registro de **todos** los servidores durante 1 h.
  Propuesta: aislarla con un `SAVEPOINT` y contar el descarte.
- **B4 [V]** Un marco de `inventario` inválido no se cuenta (falta `"inventario"` en `MOTIVOS_PROTOCOLO`), y `inventario_incompleto` no llega a ninguna parte visible: los fallos A1 y A3 serían mudos.
- **B5 [V]** Esquema 3:
  - Sin `CHECK` de juego de caracteres en `etiqueta` (defensa en profundidad: `NOT GLOB '*[^A-Za-z0-9._/-]*'`).
  - Sin rangos en `primera`, `ultima`, `visto_n` ni `ausente_n`.
  - `inestable` se salta el presupuesto por tipo, aunque queda acotado por 500 recursos.
  - Volumen máximo: unos 1000 eventos por servidor y día × 180 días.
- **B6 [S]** La carrera entre `ps -aq` e `inspect` (contenedor borrado entre medias) produce menos líneas de las pedidas, y la lectura se toma por incompleta. Es conservador y no genera eventos falsos.
  Pero en hosts con mucha rotación retrasa las desapariciones. El diseño preveía `fallos <n>` y no está implementado.
- **B7 [V]** El ensayo de subida y bajada con la copia previa, y el «aviso visible» cuando la base es más nueva que la app, no están en el commit. El diseño los pone como requisito antes de fusionar.

## Comprobado y correcto
- **Plantilla** (`recoger-root.sh:64`): solo nombra campos de la lista blanca; `Health.Status`, nunca `.Log`; ni `Env`, `Cmd`, `Mounts` ni `Image`. Los valores de las etiquetas son datos y no se reevalúan como plantilla.
  El test confirma que `hunter2` (variable de entorno y etiqueta ajena) no aparece en la salida.
- **`$pedidos` sin comillas:** seguro, porque los ids son hexadecimales de 64 caracteres (sin espacios ni comodines). Con 0 ids, `printf | grep -c .` da 0.
- **`[ "$n" -gt 0 ] && t docker inspect …`:** su código de salida es inocuo (no hay `set -e`) y el marco siempre cierra con `fin <n> 0`.
- **`tac | head -n 200`:** da los más antiguos (verificado con un `docker` falso de 250).
- **`MARCAS` y `fin_root`:** declaran de verdad lo saltado o agotado de `ps -aq` y de `inspect`, porque `t` escribe en un fichero y se ve desde `$(...)`.
- **Máquina de estados:**
  - Un fallo SSH o una entrada con `error` nunca llama a `anota_inventario`, y hay test.
  - Dos lecturas con el mismo `ts` no se pueden dar, porque `registrar` exige `ok_en > previo`.
  - El reloj adelantado se recorta en `purgar`. El atrasado solo retrasa la confirmación.
  - Los contadores `visto_n` y `ausente_n` obsoletos se reinician en el sitio correcto.
- **Migración 3:** `STRICT, WITHOUT ROWID` (SQLite 3.53 en local), `CHECK` de conjuntos cerrados, `FK ON DELETE CASCADE`, todo en una sola transacción con *rollback*, copia previa sin nombres y `CLASES_SERVIDORES` completo.
  `servidores_lectura` no lee las tablas nuevas y la clave nueva de `mantener` no rompe a nadie.

## Acciones recomendadas (por orden)
1. **A3:** `split("\n")` en `analizar`, más un test con `U+0085`.
2. **A1:** descartar por contenedor en lugar de por lectura, y aceptar nombres de Docker hasta 255 y etiquetas inválidas como ausentes.
3. **A2:** presupuesto por severidad y no avanzar el estado cuando el evento se descarta.
4. **M5:** podar `servicios` y `tarjetas` en la caché y en `--olvidar-recursos`, y sacar la sal de la base.
5. **M2, M4, M1:** bucle solo con el reloj del Mac, guarda de Swarm sin `service ls`, y colisión o coherencia en Compose.
6. **M3, M7, M6:** truncados con `.Created`, expulsión que proteja lo reciente, y separar el presupuesto de tiempo del inventario.
7. **M8:** hacer opcional el test con Docker real y añadir las mutaciones que faltan.

## Preguntas abiertas
1. ¿Es aceptable que el inventario invalide las secciones de seguridad de root al agotar el tiempo (M6), o se prefiere que tenga su propio presupuesto?
2. ¿Pueden los clientes de los paneles (Dokploy o Easypanel) fijar `com.docker.compose.*` en sus contenedores? De eso depende la gravedad de M1.
3. ¿Se acepta guardar la sal en el Llavero (M5)? Rompe la portabilidad de la base entre equipos.

Status: DONE_WITH_CONCERNS
Summary: La plantilla de root no filtra secretos y un fallo SSH no genera eventos, pero hay tres hallazgos altos. Un contenedor fuera de patrón anula las conclusiones del inventario, los efímeros agotan el presupuesto y se pierden eventos de producción, y un `U+0085` en una etiqueta deja sin dato todas las secciones de root. Además, el diseño se incumple en colisiones, bucle, expulsión y privacidad de la caché.
