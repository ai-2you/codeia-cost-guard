# Plan: histórico permanente en SQLite (v2, tras pre-mortem y red team)

Estado: **v2 para revisar**, sin código todavía. Fecha: 2026-10-14.
Informes que lo respaldan: `../reports/red-team-261013-sqlite-historico.md` y
`../reports/pre-mortem-261013-sqlite-historico.md`. La v1 (agregados por hora con clave de ruta) queda
sustituida: ambos informes demostraron que fijaba errores y fugas.

## Resultado buscado
Una base SQLite local y permanente para el consumo medido (y después el estado de los servidores), con
histórico más allá de lo que duren los logs, presupuestos con alertas y exportación. La configuración
(`config.json`, `servidores.json`) sigue siendo JSON editable desde Ajustes. El destino final es una
**aplicación** que haga todo esto: nada del diseño puede suponer "un solo usuario en un solo Mac".

Decisiones del dueño: objetivos = histórico permanente, menos trabajo por minuto, presupuestos y
alertas, histórico de servidores; retención por hora para siempre; abordaje por fases; exportador sí;
presupuestos por proyecto y por plan; se guardan las IPs que intentan entrar; todo plan y toda fase
pasa por pre-mortem y red team antes de codificar (información sensible).

## No objetivos
Mover la configuración a SQLite · subir datos a la nube · defender los datos frente a malware que ya corre
con tu usuario y puede leer el Llavero y todo `~/.cost-guard` (se documenta como fuera de alcance; la
defensa real ahí es FileVault, minimización y retención).

## Hallazgos que cambian el plan (verificados)
1. **La app ya cuenta de más el consumo de Claude Code.** Claude Code escribe varias líneas por mensaje con la
   misma entrada y caché; la deduplicación de `parsear()` incluye el segundo de la marca de tiempo y no las
   une. Comprobado con una muestra independiente de los 150 logs más recientes: 31.261 líneas con uso para
   16.121 mensajes únicos; con la regla actual salen **1,70×** los tokens que por mensaje único. Los forks
   tampoco se deduplican entre ficheros. Si esto se grabara en un histórico permanente, el error sería
   irreparable. **Se corrige antes de acumular nada.**
2. **El servidor local no tiene autenticación**: Host/Origin/Content-Type frenan a las webs del navegador, no a
   otro proceso del Mac. `/ajustes` entrega hosts, usuarios SSH y proyectos con solo poner la cabecera Host.
3. **Datos sensibles legibles por cualquiera del Mac hoy:** `~/.cost-guard` en 0755, `panel.html` y
   `costbar.log` en 0644, y los PNG del desplegable en `logs/render` (con host e IPs de servidores).
4. Las claves de cifrado en el Llavero no protegen frente a un proceso del mismo usuario; el HMAC de IPs
   se invierte (2^32 valores) y sigue siendo dato personal; Time Machine incluye la carpeta hoy.
5. Claude Code borra sus logs a los 30 días: cada semana de retraso es histórico que se pierde.

## Fase 0 — antes de tocar la base (rama propia, entregable por sí misma)
**0a. Cerrar la app actual.**
- Autenticación del servidor local: token aleatorio por arranque (fichero 0600), entregado a la barra, que abre
  el panel con él; el servidor lo cambia por una cookie `HttpOnly; SameSite=Strict` y **rechaza todo sin ella**,
  lectura incluida. La puerta Host/Origin se mantiene como anti-CSRF.
- Permisos: `umask 0077` en todos los procesos, `chmod 700` de `~/.cost-guard` en `install.sh` y `Umask` en el
  plist; `panel.html`, `costbar.log` y copias en 0600; renders del desplegable en un directorio privado y
  borrados tras usarlos.
- `log()` sin rutas de logs ajenos ni IPs y con corte del bucle "Address already in use".
**0b. Corregir el conteo** (`parsear`, `parsear_pi`, lectores de bases): una vez por mensaje (`message.id`),
entrada y caché una sola vez, la última salida; deduplicación entre ficheros para forks; fórmula de coste sin
restar la caché dos veces; `CLIFF` por turno. Tests con fixtures sintéticos. **Efecto esperado: las cifras de
Claude Code bajan en torno a un 40 %**; se explica en la interfaz (nota de versión) y en el README.
**Criterio de salida:** nuevo test de abuso pasa (otro proceso sin cookie recibe 401/403 en todo), permisos
verificados por test, y el conteo corregido coincide con una cuenta independiente sobre un corpus sintético.

## Fase 1 — almacén de turnos (solo añadir)
Esquema (SQLite ≥ 3.37, `STRICT`, WAL, `secure_delete=ON`, `foreign_keys=ON`), fichero
`consumo.sqlite` aparte del futuro `servidores.sqlite`:

```
meta(clave, valor)                                   -- version_esquema, version_minima_lector, dispositivo_id
espacios(id, nombre)                                 -- dueño de cada dato; hoy una sola fila
fuentes(id, espacio_id, dispositivo_id, tipo, id_fuente, visto_en)   -- id_fuente = hash, sin rutas absolutas
turnos(clave TEXT PK, espacio_id, fuente_id, hora_utc INTEGER, agente, proveedor, modelo,
       gi, go, cr, cw, proyecto_raw, productor_version, ...)         -- clave = id de mensaje o hash de contenido
```
- **Reglas:** `INSERT OR REPLACE` por clave de turno; **nunca `DELETE` por fuente**. El único borrado
  permitido es el de filas de una versión de productor defectuosa, solo de fuentes que siguen existiendo y
  tras `backup()`.
- **Hora en UTC** (entero); lo local se deriva al leer. Fixtures del cambio de hora (29-03-2026 y 25-10-2026) y
  test "misma salida con otra TZ".
- **Datos crudos**: `proyecto`, `plan`, alias y `usd` se **derivan al leer**; si se guarda `usd`, en
  micro-USD con `tarifa_version`.
- Las agregaciones por hora/día/ventana son vistas o caché derivada y reconstruible.
- **OpenCode/Hermes:** fuente = sesión con id estable; OpenCode por id de mensaje; averiguar qué es
  `opencode-.db`; Hermes por delta entre lecturas con firma de cambios.
- **Construcción inicial:** un único constructor con bloqueo, reanudable por fuente y en lotes cortos, mientras
  los lectores siguen con `state.json`; se mide en frío y se fija un tope. Solo la barra (proceso dueño) escribe;
  panel y endpoints son de solo lectura.
- **Concurrencia:** una conexión por hilo, `busy_timeout` corto, vuelta al último resultado si hay bloqueo.
- **Copias** con `VACUUM INTO`/`backup()` (nunca copiando el fichero en caliente), con la misma caducidad que
  los datos personales; copias cifradas con passphrase si salen de `~/.cost-guard`.
- **Fase 1b en sombra:** N días con el cálculo nuevo en paralelo al actual registrando diferencias; criterio de
  salida = **prueba diferencial con lista de divergencias aceptadas y firmadas** (no "resultado idéntico":
  grabaría el error de la fase 0b y no es reproducible porque depende de la hora, de la API de NaN y de logs
  que cambian). Reloj inyectable en `agregar()`; corpus sintético en `tests/datos/`, nunca capturas reales.
- **Marcha atrás ensayada** como criterio de salida; `state.json` con doble escritura hasta cerrar la fase.
- **Huecos:** aviso en el panel de días sin fuentes vistas; revisar `cleanupPeriodDays` de Claude Code
  mientras dure la migración.
- Rutas de la base en `rutas.py` y test de aislamiento (nada fuera de `HOME_FALSO`).

## Fase 2 — histórico, presupuestos y exportador
- Vista histórica en el panel (meses, comparativas, proyección de fin de mes).
- Presupuestos por proyecto **y** por plan (`ambito`, `nombre`, `periodo`, `tokens`), con alertas en la barra.
- **Exportador:** CSV con `QUOTE_ALL` y neutralización de fórmulas en columnas de texto (`= + - @` y tabulador),
  fichero 0600 en una carpeta propia (no `~/Downloads`), **IPs y hosts fuera por defecto** y solo con
  confirmación explícita, tope de filas, respeta `oculto`, y cada export queda en un registro de auditoría. Las
  copias en claro son el riesgo mayor: se avisa y se ofrece export cifrado con passphrase.
- Reglas de UI: los endpoints devuelven datos crudos y el JS pinta por DOM; **prohibido** el patrón
  `innerHTML` de `app.js` para datos nuevos.
- Clasificación por columna (público / interno / personal / secreto) en `almacen.py`, para que export y API
  filtren por clase y no por lista a mano.

## Fase 3 — servidores
- `servidores.sqlite` aparte. Se graba **solo lo nuevo** por ciclo (hoy el ciclo repite lecturas ~12× por hora).
- Métricas tipadas (calibre o contador con reinicios), muestra cruda N días y luego compactada por hora.
- **IPs de quien intenta entrar:** tabla aparte con `caduca_en` (**propuesta 90 días**, configurable; contadores
  agregados permanentes), validadas con `ipaddress` (solo públicas), con topes por muestra y por día, y fuera
  de las copias pasado el plazo. Las IPs propias (`sesiones.desde`) fuera de la base o seudonimizadas. Un
  servidor comprometido no puede contaminar la base: charset, longitud y número acotados antes de persistir.
- Nunca se guarda una clave; `purga` cubierta por test (`secure_delete`, checkpoint, copias).

## Seguridad transversal
- Acceso a datos **solo** por `infobar/almacen.py` con consultas parametrizadas; toda función recibe el
  `espacio_id`; `CHECK`, tipos estrictos, longitudes acotadas; nombres de proyecto **crudos** acotados (sin
  caracteres de control), nunca saneados al guardar (el saneado es solo de presentación).
- Cifrado: FileVault como base + minimización + retención + cifrado de copias y exports con passphrase.
  **Se descarta** "cifrado de campo con clave en el Llavero" y SQLCipher con clave en el Llavero (un proceso del
  mismo usuario la lee sin diálogo) y el HMAC de IPs.
- Time Machine: exclusión del **directorio** comprobada al arrancar, con aviso si está bajo iCloud/Dropbox.
- `quick_check` diario (no `integrity_check` en cada apertura); migraciones numeradas con copia previa;
  UUIDs en vez de autoincrementales para poder fundir dispositivos.
- Camino a la aplicación: `espacio_id` y `dispositivo_id` desde la fase 1; auth y TLS como requisito de la
  API; encargado del tratamiento y supresión por inquilino cuando haya más usuarios.

## Privacidad y legal
Una IP es dato personal (TJUE C-582/14). Base proporcionada: interés legítimo (considerando 49) con plazo
real y corto (30–90 días), sin geolocalización fina. Nombres de cliente persona física también cuentan.

## Decisiones que necesito del dueño
1. **¿Aceptas que las cifras de Claude Code bajen en torno a un 40 %** al corregir el conteo (fase 0b)?
2. **Copias del histórico:** ¿Time Machine cifrado o export cifrado propio?
3. **Fase 1b en sombra:** ¿cuántos días? (propuesta: 7)
4. **Caducidad de IPs de atacantes:** ¿90 días? (propuesta) ¿otro plazo?
5. Orden: ¿fase 0 ya, y sin esperar a que esté el resto del plan cerrado? (recomendado: sí)

## Puerta de verificación (cada fase)
Pre-mortem → red team → cambios al plan → informe en `plans/reports/` → solo entonces código. Tras
implementar: revisión de código con foco en seguridad y pruebas de abuso (inyección en nombres, CSV con
fórmulas, Host/Origin falsos, petición sin cookie, base con permisos incorrectos, copia sin cifrar).
