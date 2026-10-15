# Revisión de código — historial de servidores (commit a048ee3, 2026-10-15)

Revisión independiente, solo lectura. Ficheros: `infobar/servidores_datos.py`, `servidores_historico.py`, `servidores_lectura.py`,
`sqlite_base.py` (y el diff de `almacen.py`), `tests/test_servidores_historico.py`. Contrastado con «Diseño v3» de
`plans/261013-2350-sqlite-historico/fase3-diseno.md` y con `plans/reports/revision-diseno-fase3-261014.md`.
Marcas: **[V]** verificado (lectura o ejecución inocua en un temporal del scratchpad, nunca en datos reales), **[S]** supuesto.

## Resultado

No hay ningún hallazgo crítico. La pieza cumple lo esencial: SQL siempre parametrizado, IPs canonizadas y filtradas, `secure_delete` más un
checkpoint con TRUNCATE que deja la base y el WAL sin IPs, la copia previa a migrar sin IPs y la retención filtrada también al leer.
Los 29 tests pasan con Python 3.14 y 3.12, y la suite completa también (595 OK). El refactor de `sqlite_base` no cambia el comportamiento
de `almacen.abrir` [V]: el código se ha movido tal cual, `abrir_base` llama a `migrar(con, ruta)` como antes, nadie usaba
`almacen.SQLITE_MIN` y ningún test parchea internos movidos.

Hay que corregir antes de dar la pieza por cerrada: el **reloj del Mac adelantado** (una IP puede sobrevivir a la retención y el registro
se queda bloqueado), la **lectura de IPs** que depende de que quien llama pase la retención y la política, el **temporal con IPs** que una
caída en mitad de la copia previa deja huérfano y varios **huecos de tests** que han sobrevivido a mutaciones.

## Alta

**A1 [V] Con el reloj del Mac adelantado, una IP sobrevive a la retención y el historial se bloquea.**
Ficheros: `servidores_historico.py:242-251`, `:271`, `:296-297`; `servidores_lectura.py:67`.
`ok_en` e `intento_en` son la hora del Mac (`servidores_ciclo.py:177,184`) y la única barrera es `<= ahora + 60`, medida con ese mismo
reloj. Lo he reproducido: con el reloj un año adelantado, `registrar` guarda la IP con `ultima` en el futuro. Después, con el reloj ya
corregido y 100 días más tarde, `mantener` con retención de 7 días no la borra e `ips()` la sigue sirviendo. Además, toda lectura posterior
devuelve `None` porque `ok_en <= previo[0]`, así que no se guardan muestras hasta que el reloj real alcanza la fecha falsa.
Con `intento_en` pasa algo parecido: `UPDATE ... intento_en=MAX(intento_en, ?)` (`:250`, `:271`) guarda el valor sin comprobar el rango,
y he verificado que un `intento_en` de +10⁷ s impide registrar los fallos siguientes.
Arreglo:
- En `mantener`, borrar las IPs con `ultima > ahora + 86400` y bajar a `ahora` los `ok_en`, `intento_en` y `muestra_en` que superen
  `ahora + margen`.
- Filtrar `ultima <= ahora + margen` también en `ips()`.
- Validar `intento_en` antes de cualquier `UPDATE`.
- Añadir `CHECK(ultima BETWEEN TS_MIN AND TS_MAX)` y `CHECK(primera <= ultima)` en `ips_atacantes`.

**A2 [V] `ips()` no conoce la política del servidor.** Fichero: `servidores_lectura.py:64-70`.
- Si se llama sin `retencion_dias`, aplica 90 días aunque el servidor tenga 7. Lo he verificado: devuelve una IP de hace 30 días.
- No mira `guardar_ips` ni `retirado_en`. Tras apagar `guardar_ips` o quitar el servidor, las IPs se siguen sirviendo hasta el siguiente
  `mantener`, que corre una vez por hora. El diseño dice que se borran al apagar.

Arreglo: que `ips(con, sid, ahora, politica)` exija la política, devuelva `[]` si `guardar_ips` es falso o el servidor está retirado, y
no tenga valor por defecto. Además, la pieza C debe llamar a `olvidar_ips` en el mismo momento en que el usuario apaga la opción o quita
el servidor.

**A3 [V] La copia previa a migrar pasa por disco con IPs.** Fichero: `servidores_historico.py:129-145`.
`con.backup(out)` escribe la base completa, IPs incluidas, en `*.tmp`. El destino hereda el modo WAL, así que también se crean `.tmp-wal` y
`.tmp-shm`; lo he comprobado y el `.tmp` contiene la IP antes del `DELETE`. Si el proceso muere entre el backup y el `os.replace`, queda en
`DATOS/copias` una copia entera con IPs. Solo se purgaría a los 30 días y únicamente durante otra migración (ver M3); `barrer_tmp` no
mira esa carpeta.
Arreglo: hacer el backup a una conexión `:memory:`, ejecutar allí `DELETE` y `VACUUM` y, después, `backup` de memoria al `.tmp`. Así las
IPs nunca tocan el disco. Como respaldo, al abrir, borrar los `servidores.sqlite.premigracion.*.tmp*` huérfanos.

## Media

**M1 [V] `servidores_ssh.py:183` recorta la lista a 4 IPs antes de que se validen.** Hace `sorted(...)[:4]` sobre IPs sin validar, así que
el «se recorta tras validar» de `servidores_datos.py:129` no surte efecto. La IP del propio Mac, una IP privada o una basura con un recuento
alto ocupan un hueco y desplazan a un atacante real. Es el mismo problema que señaló el pre-mortem (PM11).
Arreglo: no recortar en el parser (que se quede, por ejemplo, con 16) y dejar que `muestra_de` recorte después de canonizar y excluir.

**M2 [V] `abrir()` de servidores no tiene la protección de checkout de desarrollo.** Fichero: `servidores_historico.py:154-159`.
`almacen.abrir` (`almacen.py:119`) se niega a escribir en los datos reales desde un checkout de desarrollo. `sh.abrir()` no lo hace: un
script lanzado desde el repo sin `COSTGUARD_DATOS` crearía `servidores.sqlite` con IPs en los datos reales.
Arreglo: copiar esa misma comprobación.

**M3 [V] Las copias previas solo caducan cuando se hace otra copia.** Fichero: `servidores_historico.py:146-150`.
La purga de las copias de más de 30 días solo se ejecuta dentro de `copia_sin_ips`, así que una copia (sin IPs, pero con `nombre`, que es
dato personal) puede durar años. Lo confirma la mutación «copias sin caducar», que sobrevive.
Arreglo: sacar la purga a una función y llamarla desde `mantener`.

**M4 [V] `borrar_servidor` no comprueba ni devuelve el resultado del checkpoint.** Fichero: `servidores_historico.py:337-341`.
Con un lector abierto, las páginas con IPs siguen en `-wal` después de borrar, y siguen ahí cuando el lector se cierra porque no hay
reintento; lo he verificado. La limpieza depende de que el siguiente `mantener` haga un checkpoint (hasta una hora).
Arreglo: hacer el borrado dentro de `BEGIN IMMEDIATE` y terminar con `_consolidar(...)`, devolviendo `(n, consolidado)` como `olvidar_ips`.

**M5 [V] Un servidor hostil puede borrar su propio historial sin que se sepa quién ha sido.** Basta con un número imposible (por ejemplo,
`uptime` 1e308) para descartar la muestra entera: ni fila, ni `horas_servidor.n`, ni IPs. Eso es lo que pide el diseño, pero el contador
`meta.descartes:fuera_de_rango` es global y no dice qué servidor miente.
Arreglo: contar los descartes por servidor (columna en `servidores` o clave `descartes:<motivo>:<id>`) y apuntar `errores`/`invalidas` en
`horas_servidor` para que la serie muestre el hueco.

## Baja

- **B1 [V]** El contrato «`registrar` no lanza por datos del servidor» depende de lo que produce el parser hoy.
  - Si `sesiones[].desde` no es hashable (por ejemplo, una lista), salta `TypeError` en `servidores_datos.py:87`.
  - Si `webs[].codigo` es un `str`, salta `TypeError` en `:109`.
  - Un entero de más de 309 dígitos provoca `OverflowError` en `porcentaje` (`:34`).
  - Con `total = inf`, `porcentaje` devuelve 0.0 en vez de `None`.

  Con `servidores_ssh.interpretar` ninguno de estos casos se da hoy [V].
  Arreglo: capturar `(TypeError, ValueError, OverflowError)` en `muestra_de`, contarlo como `fuera_de_rango` y exigir `total` finito.
- **B2 [V]** Desviaciones respecto al diseño.
  - En incidencia se conserva la **primera** muestra de cada franja, no «la de mayor valor».
  - `horas_servidor` no guarda medias.
  - `intentos_propios` cuenta muestras, no intentos.

  Hay que documentarlo o ajustarlo.
- **B3 [S]** `mantener(con, {}, ...)` retira **todos** los servidores y borra sus IPs. Si la pieza C llama con `{}` cuando falla la lectura
  de la config, se pierden datos (aunque sea en la dirección segura para la privacidad).
  Arreglo: que la pieza C no llame si la config no se ha podido cargar.
- **B4 [V]** `_con_reintentos(lambda: migrar(...))` repite también `copia_sin_ips`, de modo que un `locked` deja una copia por cada
  reintento. Arreglo: hacer la copia fuera del reintento o comprobar si ya existe.
- **B5 [S]** `temp_store` solo vale `MEMORY` durante `VACUUM`. Si un ordenamiento grande se desborda a disco, iría a `/tmp`/`%TEMP%`.
  Hoy es improbable (5 000 filas), pero `PRAGMA temp_store=MEMORY` en `abrir()` lo cierra del todo.
- **B6 [V]** `servidores.nombre` (dato personal) y `horas_servidor` de los servidores retirados se guardan para siempre. Si alguien pone
  como nombre una IP o un host, ese dato no caduca. Conviene decidir una caducidad para los retirados.

Descartado como ruido, tras verificarlo:
- NAT64, 6to4, Teredo, IPv4-compatible y `::ffff:0:x`: `ip_global` los rechaza con 3.12 y con 3.14.
- Inyección SQL: los f-strings solo interpolan constantes.
- Mensajes de error: no incluyen IPs (`FueraDeRango` lleva números y los errores de CHECK no llevan valores).
- `olvidar_ips` sin `VACUUM`: tras el checkpoint, ni la base ni el WAL contienen la IP.
- La expulsión sin `+1` sí la detectan los tests.

## Huecos de tests (mutaciones aplicadas en una copia del scratchpad)

Sobreviven estas mutaciones:
1. `mantener` usa 90 días fijos en vez de la retención del servidor. No hay ningún test con retención distinta de 90 en `mantener`.
2. Quitar `intento_en > previo[1]`, es decir, el dedupe de fallos. Falta un test que registre dos veces el mismo `intento_en` y compruebe
   que `errores` no se duplica.
3. `borrar_servidor` sin checkpoint. Falta un test de bytes del WAL con un lector abierto y luego cerrado.
4. Copias sin caducar. Falta un test que envejezca una copia con `os.utime` y compruebe que se purga.
5. Quitar `_descartar(con, "reloj")`.
6. `_junta` toma el último estado en vez del peor (`servidores_lectura.py:26`).

Faltan también tests de:
- el reloj adelantado y luego corregido (A1);
- `ips()` con `guardar_ips` apagado y con el servidor retirado (A2);
- un `.tmp` huérfano (A3);
- tipos hostiles que hoy hacen lanzar a `registrar` (B1).

Hay asserts flojos: `test_version_mas_nueva...` usa `assertRaises(Exception)`, cuando debería esperar `ErrorAlmacen`, y en
`test_topes...` hay un `assertLessEqual(..., 4)` donde debería ser `assertEqual(..., 3)`.
«Sin VACUUM» y «copia sin secure_delete» también sobreviven, pero son mutantes equivalentes: `secure_delete` y `VACUUM` se cubren el uno
al otro.

## Acciones recomendadas (por orden)

1. A1: límites de reloj en `mantener`, en `ips()` y en `intento_en`, y CHECK de rango en `ips_atacantes`.
2. A2: `ips()` exige la política, y `olvidar_ips` inmediato al apagar la opción o quitar el servidor (en la pieza C).
3. A3: copia previa hecha en memoria y barrido de `.tmp` huérfanos.
4. M1–M5, y después los tests que matan las seis mutaciones supervivientes.

## Métricas

- Tests: 29 del módulo y 595 de la suite, todos OK con Python 3.14.6 / SQLite 3.53.4; los 29 del módulo también OK con 3.12.13.
- Mutaciones: 14 aplicadas, 6 sobreviven (más 2 equivalentes).
- Linter o tipos: no se han ejecutado; el proyecto no tiene configuración de tipos.

## Preguntas sin resolver

1. ¿Qué margen de reloj se acepta (1 h, 1 día) para considerar futura una `ultima` y borrarla en `mantener`?
2. ¿Deben caducar `nombre` y `horas_servidor` de los servidores retirados, o el olvido total solo se hace con `borrar_servidor` a petición?
3. ¿Se toca `servidores_ssh.py:183` (fuera de esta pieza) en la 3.1 o en la 3.3a, junto con `recoger.sh` v2?
4. ¿La pieza C, ahora en curso, pasa la política completa a `ips()` y llama a `olvidar_ips` al cambiar la config?

Status: DONE_WITH_CONCERNS
Summary: No hay ningún hallazgo crítico y el refactor del núcleo no cambia el comportamiento. Hay tres altas (reloj adelantado, lectura de
IPs que depende de que quien llama pase la política y temporal con IPs en la copia previa), cinco medias y seis mutaciones que sobreviven
a los tests.
