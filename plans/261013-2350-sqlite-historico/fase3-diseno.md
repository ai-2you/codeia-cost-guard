# Fase 3 — historial de servidores e IPs de quien intenta entrar (diseño v1, pendiente de pre-mortem y red team)

Estado: **v1 sin código; pasa a ser la fase 3.1 de `fase3-plan-monitorizacion.md`** (alcance ampliado el 2026-10-14). Su revisión es `../reports/revision-diseno-fase3-261014.md`. Fecha: 2026-10-14. Parte de `plan.md` (sección "Fase 3"), de las decisiones del dueño (IPs de atacantes con
caducidad de **90 días**, configurable; servidores en una base aparte) y de lo construido en las fases 1 y 2.

## Resultado buscado
1. Ver cómo ha ido cada servidor **más allá de lo que dura la caché** (hoy solo el último dato bueno): carga, memoria, disco, intentos
   de acceso, puertos, protección y estado, por hora y por día.
2. Saber **quién ha intentado entrar** (IPs públicas, con cuántos intentos y cuándo) durante un plazo corto, y olvidarlas después.
3. Que ninguna de las dos cosas aumente la superficie de ataque: lo que cuenta un servidor comprometido no puede dañar la base ni la
   app, y las IPs (dato personal) no se conservan más de lo necesario ni salen de la base por accidente.

## No objetivos
Alertas nuevas (las del ciclo actual no cambian) · enviar nada fuera del equipo · geolocalizar IPs · guardar nombres de contenedores,
dominios de las webs medidas, usuarios ni rutas · guardar las IPs propias (las de tus sesiones) · cambiar `recoger.sh` en los servidores
en esta fase · mezclar estos datos con `consumo.sqlite`.

## Lo que ya existe y se reutiliza
- `servidores_ciclo.ciclo()` corre en el hilo trabajador de la barra, cada 300 s por servidor (60 s en incidencia). Fusiona el resultado en
  `logs/servidores.json` (una entrada por servidor: último dato bueno, hora y error). El ciclo **repite lecturas** del mismo dato (~12 por hora).
- `recoger.sh` (en el servidor) entrega, por secciones: sistema (load, nproc, uptime), memoria, disco, sesiones, puertos, **intentos**
  (total y última hora de intentos rechazados en una ventana deslizante de 24 h, y las 4 IPs con más intentos), actualizaciones, bastionado,
  servicios, contenedores, fail2ban. `servidores_ssh.leer()` ya acota tamaño (64 KiB) y tiempo.
- Infraestructura SQLite de la fase 1 (`almacen.py`): `_privado`, PRAGMA seguros (WAL, `secure_delete`, `foreign_keys`), lectura `mode=ro`,
  rechazo de rutas en la nube o de red, clasificación por columna (`CLASES`).

## Diseño

### 3.1 Base aparte: `DATOS/servidores.sqlite`
Mismo directorio de datos y mismas reglas de ruta que `consumo.sqlite`, fichero distinto, `STRICT`, `user_version` propio, migraciones
numeradas con copia previa. **Un único escritor: el hilo trabajador de la barra** (es quien ejecuta el ciclo; la CLI no recoge). El panel y el
servidor local la abren `mode=ro`. Sin `espacio_id` multiusuario todavía, pero sí `dispositivo_id` en cada fila para poder fundir equipos.

Tablas (todas con `CHECK` de rango y longitud):
- `servidores(id, dispositivo_id, nombre UNIQUE, creado_en)`. Solo el **nombre** que el dueño dio en Ajustes. **Nunca** host, usuario ni IP
  del servidor. Renombrar en Ajustes crea un servidor nuevo (decisión abierta 1).
- `muestras(servidor_id, ts, estado, carga_pct, mem_pct, disco_pct, uptime_s, intentos_24h, intentos_1h, baneadas, sesiones, sesiones_ajenas,
  puertos_n, puertos_expuestos, servicios_n, servicios_caidos, webs_n, webs_caidas, web_ms_max, faltas_proteccion, reinicio_pendiente,
  PRIMARY KEY(servidor_id, ts))` — una fila por **ciclo con dato nuevo**, no por lectura. `ts` es el del Mac al recibir (nunca uno que
  mande el servidor). Se conservan **14 días**.
- `horas_servidor(servidor_id, hora, n, carga_max, carga_media, mem_max, disco_max, intentos_pico_24h, intentos_pico_1h, baneadas_max,
  sesiones_ajenas_max, estado_peor, PRIMARY KEY(servidor_id, hora))` — compactación **permanente** de las muestras cuando salen de los
  14 días (y mantenida al insertar, como `horas` en consumo). Los valores de ventana deslizante (`intentos_24h`) se guardan como **picos**
  y se rotulan así: sumar ventanas de 24 h solapadas daría un número falso (decisión abierta 2).
- `ips_atacantes(servidor_id, ip, primera, ultima, max_24h, veces, caduca_en, PRIMARY KEY(servidor_id, ip))` — la IP en forma canónica de
  `ipaddress` (texto), **solo globales** (`is_global` y no multicast/loopback/privadas/CGNAT/documentación). Se actualiza con semántica
  monótona (`max_24h` = máximo visto, `ultima` = último ts, `veces` +1 por muestra en que sale). `caduca_en = ultima + retención`.
- `meta` (versión, última purga, contadores de lo descartado).

### 3.2 Qué se guarda y qué no
- Se guardan **números acotados y estados** (enteros con rango, porcentajes 0–100, enumeración cerrada de estado). Todo valor que llega del
  servidor se parsea con función estricta (`int` en rango, `ipaddress` para IPs, lista cerrada para estados); lo que no encaja se **descarta
  y se cuenta** en `meta`, no se persiste ni rompe el ciclo.
- **No** se guardan: host, usuario SSH, puerto SSH, nombres de servicios o contenedores, dominios o URLs de las webs, la salida cruda, ni la
  salida de `who` (contiene las IPs de tus sesiones). De `who` solo sale `sesiones` (nº) y `sesiones_ajenas` (nº de sesiones cuya IP no está
  en `ips_conocidas`); el cálculo se hace en memoria al recibir.
- Un servidor comprometido puede mentir: topes por muestra (4 IPs, 50 puertos, 1 000 000 de intentos), tope de **5 000 IPs por servidor**
  (al pasarlo se descartan las más antiguas por `ultima` y se cuenta), y un servidor que envía basura repetida no crece la base más allá de
  esas cotas.

### 3.3 Retención y borrado de verdad
- IPs: **90 días** desde `ultima` (configurable `retencion_ips_dias`, mínimo 7, máximo 365). Una purga diaria en el hilo trabajador hace
  `DELETE`, `PRAGMA wal_checkpoint(TRUNCATE)` y, si se borró algo, `VACUUM` mensual (con `secure_delete` activo desde la creación).
- Muestras crudas: 14 días; horas y contadores agregados: permanentes. Sin IPs en ninguna tabla agregada.
- `costbar.py --olvidar-ips [--servidor NOMBRE]` borra ya todas las IPs (o las de un servidor) y lo anota en el registro de auditoría.
- **Las copias cifradas automáticas no incluyen `servidores.sqlite`** en esta fase (decisión abierta 3): así una copia antigua no
  conserva IPs más allá del plazo. El histórico agregado se puede reconstruir a partir de ahora, no hacia atrás.

### 3.4 Dónde se engancha
`servidores_ciclo._guardar()` (hilo trabajador, ya con el cerrojo del ciclo) llama a `servidores_historico.registrar(nombre, entrada, ahora)`
dentro de un `try` que **nunca** propaga: un fallo de la base se registra y el ciclo sigue como hoy. Solo se registra si `generado_en` de la
entrada es nuevo (dedupe por servidor). La purga y la compactación cuelgan del mismo hilo, una vez por hora.

### 3.5 Lectura y pantalla
- `servidores_historico.historia(servidor, desde, hasta, paso)` → series por hora o día (picos y medias), con las mismas reglas de fechas
  locales y límites de la fase 2 (librería C, regex estricta de fechas, tope de puntos).
- `GET /servidores/historia?servidor=&desde=&hasta=&paso=` y `GET /servidores/ips?servidor=&dias=` (sesión + origen + semáforo como
  `/historico`). **Las IPs solo se devuelven con `?con_ips=1`** y se pintan por DOM; el primer nivel (conteos) no las lleva.
- En la pestaña Histórico, un selector "Consumo | Servidores". Servidores: gráfico de picos de carga/memoria/disco, intentos de acceso por
  día y tabla de IPs (IP, intentos máximos en 24 h, primera/última vez, **caduca el …**). Aviso fijo: "las IPs se olvidan a los N días".
- Nada de esto entra en el CSV de consumo. Si hace falta exportar servidores, será una orden aparte, **sin IPs por defecto**.

### 3.6 Clasificación por columna
`CLASES_SERVIDORES`: `ip` y `servidores.nombre` son **personales**; el resto, internos. Test de cobertura igual que `almacen.CLASES`. Toda
salida (API, CSV futuro, copia) filtra por clase.

## Decisiones abiertas para el dueño
1. Renombrar un servidor en Ajustes: ¿crea un histórico nuevo (simple) o se ofrece "fusionar" con el anterior?
2. Intentos de acceso: ¿bastan los **picos de la ventana de 24 h** (honestos con lo que mide el script) o quieres cambiar `recoger.sh` para
   contar intentos nuevos por intervalo (más exacto, pero hay que reinstalar el script en cada servidor)?
3. ¿Las copias cifradas deben incluir `servidores.sqlite` (más completas, pero conservan IPs pasado el plazo en copias antiguas) o no?
4. ¿Quieres poder ver el histórico de servidores sin IPs en absoluto (interruptor `guardar_ips: false`)?

## Verificación prevista
Pre-mortem y red team antes de codificar. Después: tests con servidor simulado hostil (IPs privadas, cadenas gigantes, números fuera de
rango, miles de IPs distintas, tiempos del futuro), `secure_delete` y purga comprobadas leyendo los bytes del fichero tras borrar,
caducidad con reloj inyectado, el ciclo que sigue funcionando con la base bloqueada o llena, mutaciones sobre el filtro de IPs globales y
sobre la caducidad, y una revisión independiente del código con foco en privacidad.

## Diseño v3 (2026-10-15): lo que se construye en la 3.1 tras el pre-mortem y el red team (`../reports/revision-diseno-fase3-261014.md`)

Sustituye a lo anterior donde se contradicen. Se construye en tres piezas, cada una con tests, mutaciones y revisión de código.

**Pieza A — núcleo SQLite compartido.** `infobar/sqlite_base.py` recibe de `almacen.py` la apertura (rutas no fiables, PRAGMA, WAL, permisos,
reintentos, `mode=ro`) parametrizada por la función de migración. `almacen.abrir` pasa a ser una llamada al núcleo; el comportamiento no cambia.

**Pieza B — `infobar/servidores_historico.py` y `DATOS/servidores.sqlite`** (`user_version` propio, copia previa a migrar **sin IPs**, caduca a 30 días):
- `servidores(id TEXT PK, nombre, creado_en, retirado_en)`: identidad por `id` estable (uuid de `servidores.json`); renombrar cambia `nombre`; quitar un
  servidor lo marca retirado y sus IPs se borran en la siguiente purga.
- `muestras(servidor_id, ts, estado, <métricas NULL si ilegibles>)`: `ts` = `ok_en` (reloj del Mac). Una fila por lectura buena; una fila de estado
  (`error`) **sin métricas** por intento fallido. Dedupe por `ok_en` guardado en `meta`. Un máximo de una muestra por franja de 5 min (en incidencia
  se conserva la de mayor valor). Crudo 14 días.
- `horas_servidor`: compactación permanente con `n`, picos y medias; las ventanas de 24 h se rotulan **picos**, nunca sumas.
- `ips_atacantes(servidor_id, ip, primera, ultima, max_24h, horas_vista, ultima_hora)`: IP canónica (`ipv4_mapped` desenvuelta **antes** de
  `is_global`, sin `%`, ≤45 caracteres, sin reservadas ni no especificadas), **excluidas** `ips_conocidas` (se cuentan aparte como `intentos_propios`).
  Sin `caduca_en` almacenado: se calcula de `ultima` + retención vigente y **se filtra al leer**. Tope de 500 altas por servidor y día; expulsión por
  `(horas_vista, ultima)` ascendente; tope de 5 000 por servidor.
- Todo valor del servidor se parsea con función estricta; lo ilegible es `NULL`; fuera de rango descarta la muestra entera y se cuenta en `meta`.
- `guardar_ips` y `retencion_ips_dias` globales con override por servidor (retención 7–90 días, 90 por defecto; 180 solo con justificación escrita).
- Mantenimiento propio (purga al arrancar y cada hora, checkpoint reintentado hasta `busy = 0`, `VACUUM` mensual con `temp_store=MEMORY`), con
  presupuesto de tiempo y retroceso (un intento por hora y un solo `log()` sin valores).
- Orden `costbar.py --olvidar-ips [--servidor NOMBRE]` con auditoría de recuento (nunca las IPs).
- Clasificación `CLASES_SERVIDORES` con test de cobertura: `ip` y `servidores.nombre` personales.

**Pieza C — integración.** `servidores_ciclo.ciclo(..., historial=None)` llama al registrador **fuera** de `_bloqueo` y solo si el trabajador de la barra se
lo pasa (el `__main__` y los tests no escriben la base). Un fallo de la base no propaga. `normalizar` genera el `id` de cada servidor. Endpoints de solo lectura
`/servidores/historia` y `/servidores/ips` (esta última exige `servidor=` y `con_ips=1`, deja auditoría con el número de IPs servidas). La pantalla va en la 3.6.

**Fuera de la 3.1 (decisiones):** las tablas genéricas de recursos y eventos se añaden con una migración numerada en la 3.3a, cuando existe el primer
productor de esos datos (diseñarlas antes es adivinar su forma); `recoger.sh` v2 (histograma por hora y filtro por `_COMM`) va en la 3.3a. Las copias
cifradas siguen sin incluir `servidores.sqlite`. Declarado: las IPs que cuenta un servidor son «según ese servidor» y pueden estar envenenadas hasta que
`recoger.sh` v2 filtre por campos de confianza; la pantalla lo dirá.
