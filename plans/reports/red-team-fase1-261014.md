# Red team de la fase 1 (almacén de turnos en SQLite), segunda ronda

Fecha: 2026-10-14 (Europe/Madrid) · Alcance: lo **nuevo** del plan v2 (`plans/261013-2350-sqlite-historico/plan.md`,
fase 1 y decisiones del dueño) contrastado con el código de `main` tras la fase 0. No se repiten los hallazgos de
`red-team-261013-sqlite-historico.md`, `pre-mortem-261013-sqlite-historico.md` ni `revision-fase0-261014.md`; solo se
comprueba si la v2 los resuelve y qué riesgos nuevos introduce. Solo lectura; no se ha tocado el repo. Mediciones sobre
logs reales reducidas a contadores (sin nombres, ids ni rutas); valores personales como [redactado].

## TL;DR

La v2 resuelve bien lo estructural de la v1 (clave de contenido, UTC, dueño por fila, sin DELETE por fuente, copias con
`VACUUM INTO`, sombra con divergencias firmadas). Lo que falla es lo que la v2 deja implícito:

1. **`INSERT OR REPLACE` = "la última lectura gana"**, y eso convierte cualquier línea posterior con el mismo id (log hostil,
   log basura, fork con contenido distinto) en una **reescritura permanente** del histórico. La regla tiene que ser
   "el máximo gana" con validación de rangos y registro de anomalías.
2. **"Solo la barra escribe" no tiene punto de corte en el código**: `escanear()` escribe el estado en los cuatro procesos
   que lo llaman (`infobar/agregacion.py:84`) y los hilos HTTP de la propia barra también escanean. Hace falta un dueño
   explícito (latido en `meta`) y conexiones `mode=ro` para todo lo demás.
3. **El cifrado propio no está especificado** (ni formato, ni AEAD, ni librería); la stdlib no trae AEAD y el camino
   `VACUUM INTO` deja la base en claro en disco antes de cifrar.
4. **Tres PRAGMA no hacen lo que el plan cree**: `secure_delete` y `foreign_keys` son por conexión (verificado), el WAL no
   se trunca ni se limpia con `secure_delete`, y `STRICT` exige SQLite ≥ 3.37 mientras el README promete Python 3.9+.
5. **El checkout está en un volumen con propiedad desactivada** (`Owners: Disabled` en EVO990, verificado): allí
   `0600`/`0700` no protegen nada y un tirón del disco con WAL activo corrompe. La instalación real (`~/.cost-guard`)
   está en el disco interno, así que el riesgo es para la base de desarrollo y para quien instale en un disco externo.

## Mediciones hechas para este informe (solo lectura)

| Medida | Resultado |
|---|---|
| 300 logs recientes de Claude Code: líneas con uso / `message.id` distintos | 58.177 / 30.330; **0 líneas sin id** |
| Ids cuyo uso (`gi`,`cr`,`cw`) **cambia entre líneas** del mismo mensaje | **11** (todos en un solo fichero; la última línea tiene `gi` 2→4, caché ≈ ×2, salida 5→~1000: acumula dos peticiones) |
| Ids presentes en varios ficheros | 6 (igual que el pre-mortem) |
| Ids que **no** tienen la forma `msg_[A-Za-z0-9]{10,}` | **297**, todos de un backend no-Anthropic (293 `qwen`, 4 `synthetic`); forma `msg_<13 dígitos ms>_<5-6 chars>` |
| Máximo entrada+caché en un turno | 1.022.583 tokens |
| Marcas de tiempo con zona `Z` | 100 % en Claude Code (58.177) y Codex (2.562) |
| Codex: cabecera de sesión | `payload.{id, cwd, timestamp, originator}` en 45/45; cada línea lleva `ordinal` |
| pi: turnos con id | 9.620/9.620 |
| OpenCode: `opencode.db` / `opencode-.db` | 4.885 mensajes, ids únicos con prefijo **`msg_`** / **2 mensajes, 4 KB**; 0 ids comunes |
| Hermes `sessions.started_at` / `id` | `real` (epoch con decimales) / `xxxxxxxx_xxxxxx_xxxxxx` |
| `PRAGMA secure_delete` y `foreign_keys` en una **segunda** conexión | **0 y 0** (son por conexión) |
| `INSERT OR REPLACE` sobre PK existente | cambia el `rowid` (es DELETE + INSERT) |
| `file:...?mode=ro` en WAL | escribir falla con "attempt to write a readonly database" |
| `hashlib.scrypt` N=2^17, r=8, p=1 / `shake_256` 64 MiB | 0,24 s / 0,06 s (Python 3.14.6, SQLite 3.53.4) |
| `cryptography` / `pynacl` instalados | no / no |
| Volumen del checkout | APFS local, `Owners: Disabled`; `~/.cost-guard` en disco interno, `drwx------` |

## 1. Lo que la v2 resuelve bien (una línea cada uno)

- Clave de contenido en vez de ruta, `hora_utc`, `espacio_id`/`dispositivo_id`, `fuentes` sin rutas, upsert sin DELETE por
  fuente, único borrado permitido acotado y tras `backup()`: cierra 4.1–4.6 del red team v1 y las causas 2–4 y 12 del pre-mortem.
- Copias con `VACUUM INTO`/`backup()` y no copiando el fichero: cierra la causa 7.
- Sombra con divergencias firmadas, reloj inyectable y corpus sintético: cierra las causas 1 y 5.
- Export cifrado propio como copia del histórico (decisión 2): cierra la causa 8 y evita depender de `tmutil`.
- Fase 0 en `main`: cookie por puerto (`infobar/servidor_local.py:64-65`), parseo manual de `Cookie` (`:68-75`), ticket en
  memoria (`:37-45`), `umask 0077` (`infobar/permisos.py:35`), conteo por `message.id` (`infobar/lectores_logs.py:136-142`).

## 2. Ataques y fallos nuevos del diseño v2

### 2.1 CRÍTICA — "La última lectura gana" hace permanente cualquier línea posterior con el mismo id
- Evidencia: `parsear` guarda por id y se queda con la **última** línea (`infobar/lectores_logs.py:141`, `:149-150`). El plan
  fija `INSERT OR REPLACE` por clave (plan, línea 73), que es DELETE+INSERT (verificado).
- Quién puede escribir un log: no hace falta malware. Claude Code ejecuta herramientas: una sesión con inyección de prompt
  (un repo clonado con un hook, un `postinstall`, un MCP) puede hacer `echo '{...}' >> ~/.claude/projects/<x>/<sesión>.jsonl`.
  Hoy eso solo ensucia `state.json`, que se reconstruye; con la base permanente y los logs borrados a los 30 días, queda para siempre.
- Tres formas del ataque, todas con una línea:
  1. **Inflar**: id nuevo con `input_tokens: 10^9`. Los presupuestos de la fase 2 saltan y el histórico queda falso.
  2. **Borrar**: misma `message.id` que un turno real y uso en cero, escrita después. "La última gana" → `REPLACE` deja el turno a cero.
  3. **Mover de día**: misma id con `timestamp` de hace un mes → el turno cambia de día y de ventana.
- Hay un caso **legítimo** que exige la misma regla: en 11 de 30.330 mensajes la última línea acumula dos peticiones (caché ≈ ×2,
  salida ×200). "La última gana" acierta ahí porque la última es la mayor; "el máximo gana" también. Un fork con contenido
  distinto o una línea truncada no.
- Qué hacer:
  - Regla de conflicto **monótona**: `INSERT ... ON CONFLICT(clave) DO UPDATE SET go=excluded.go, ... WHERE excluded.gi+excluded.cr+excluded.cw >= turnos.gi+turnos.cr+turnos.cw AND excluded.go >= turnos.go`;
    nunca se reduce un contador ni se cambia `hora_utc` (la hora se fija en la primera inserción). Una línea que
    intente reducir o mover se cuenta en `anomalias` (tabla o contador por fuente) y se muestra en el panel.
  - **Rangos** antes de persistir: cada contador `0 ≤ x ≤ 5·10^6` (el máximo real medido es ~1·10^6), `hora_utc` dentro de
    `[mtime del fichero − 7 días, ahora + 5 min]`, `clave` en `[A-Za-z0-9_.:-]{1,128}`, `modelo` ≤ 100 caracteres imprimibles.
    Fuera de rango → anomalía, no fila.
  - `productor_version` en la fila (ya está) y `fuente_id` para poder auditar de qué fichero salió una fila.

### 2.2 CRÍTICA — No existe el punto donde "solo la barra escribe"
- Evidencia: `escanear()` termina con `guardar_estado(estado)` (`infobar/agregacion.py:84`) y lo llaman: el hilo trabajador
  (`infobar/trabajador_barra.py:48`), los hilos HTTP `/origenes` y `/ajustes` de la **misma** barra
  (`infobar/servidor_local.py:223`, `:231`), `panel.py` como subproceso (`servidor_local.py:217` → `panel.py:41`) y el CLI
  `--print` (`costbar.py:63`). El bloqueo actual es `fcntl.flock` (`infobar/config_y_estado.py:86-95`), importado a nivel de
  módulo (`:8`): en Windows el paquete ni importa.
- Si `almacen.py` se engancha donde está `guardar_estado`, escriben los cuatro. Si se engancha solo en el trabajador, los
  hilos HTTP de la barra siguen siendo "la barra" y pueden escribir por descuido.
- Qué hacer:
  - **Dueño por latido en la propia base**: `meta(dueno_id, dueno_latido_utc)`; quien quiere escribir hace
    `BEGIN IMMEDIATE`, lee el latido y solo toma el relevo si tiene más de 3 minutos; refresca el latido en cada lote. Es
    portable (sin `fcntl`) y sobrevive a procesos muertos.
  - **Todo lo que no es el hilo trabajador abre con `file:...?mode=ro`** (verificado que impide escribir), incluidos los
    hilos HTTP de la barra, `panel.py` y el CLI por defecto. El CLI puede pedir ser dueño con `--escribir` si no hay latido.
  - `escanear()` se parte en `escanear_y_persistir()` (solo el dueño) y `informe()` (lectura). Test: `panel.py` y el servidor
    no tienen ninguna ruta de código que llegue a `INSERT`.
  - El ciclo de servidores corre en el **mismo hilo** que el escaneo (`trabajador_barra.py:58`): durante el constructor
    inicial, el punto de servidores se queda gris. El pre-mortem lo pedía separado (cambio 11) y la v2 lo ha perdido.

### 2.3 ALTA — La clave de contenido colisiona entre agentes y los ids de proxies son predecibles
- OpenCode y Claude Code usan el **mismo prefijo `msg_`** (verificado); Claude Code contra un backend no-Anthropic genera ids
  `msg_<ms>_<5-6 chars>` (293 líneas medidas): ~31 bits de aleatoriedad anclados a un milisegundo, generados por un proxy
  que no garantiza unicidad entre dispositivos ni entre sesiones.
- Un `clave TEXT PK` global mezcla espacios de nombres: un id repetido entre OpenCode y Claude se funde en una fila.
- Qué hacer: `clave = agente || ':' || id` (o PK compuesta `(agente, id)`), y para ids que no cumplen la forma Anthropic
  (`msg_[A-Za-z0-9]{10,}`), `clave = agente:hash(sessionId|id|ts)`. Codex no tiene id de turno: `codex:hash(payload.id de la
  cabecera | timestamp | gi|go|cr|cw)`; así el doble registro del mismo segundo (`lectores_logs.py:143-147`) colapsa solo.
  pi: `pi:<id de sesión>:<id de mensaje>` (todos los turnos lo tienen). Hermes: `hermes:<id sesión>:<n lectura>`.

### 2.4 ALTA — El cifrado del export no está especificado y la ruta `VACUUM INTO` deja la base en claro
- Hoy no hay AEAD en la stdlib (solo `hashlib`, `hmac`, `secrets`) y no hay `cryptography` ni `pynacl` instalados.
- `VACUUM INTO 'ruta'` escribe un fichero **en claro** con la umask del proceso; si la ruta es el destino (USB, red, Dropbox),
  la base en claro toca ese disco antes de cifrarse y no se puede "borrar de verdad" (APFS/SSD con copy-on-write).
- Qué hacer (especificación mínima, portable a Windows):
  - **Librería**: `cryptography` (PyCA) para el AEAD, fijada en `requirements.txt` con `--require-hashes`; `hashlib.scrypt`
    de la stdlib como KDF (medido 0,24 s con N=2^17, r=8, p=1, 128 MiB). Argon2id existe en `cryptography` ≥ 44 pero
    depende del OpenSSL enlazado (`UnsupportedAlgorithm` si no); scrypt evita esa variable. Si `cryptography` no está, el
    export cifrado **se desactiva**, no se degrada a un cifrado casero. Alternativa sin dependencias, solo si el dueño
    veta la dependencia: keystream `shake_256(clave‖nonce‖n_trozo)` + `HMAC-SHA256` (cifrar-luego-autenticar); es sólido
    sobre primitivas estándar y rápido (64 MiB en 0,06 s), pero es "propio" y hay que decirlo así en el README.
  - **Formato** (`.cgx`): cabecera `magic "CGXP" | version u8 | kdf u8 | log2N u8 | r u8 | p u8 | salt[16] | aead u8 |
    tam_trozo u32 | nonce_base[8]`; la cabecera entera es **AAD** de cada trozo; trozo `i`: nonce = `nonce_base ‖ i(u32)`,
    texto = `flag_ultimo u8 ‖ datos`; AEAD = ChaCha20-Poly1305 o AES-256-GCM. El contador en el nonce impide reordenar y
    el `flag_ultimo` impide truncar. Clave por fichero (salt aleatorio) → sin reutilización de nonce. Lector: rechaza
    `version` desconocida, verifica la cabecera al primer trozo (una passphrase mala falla en el primer tag).
  - **Nada en claro fuera de `~/.cost-guard`**: `VACUUM INTO` a un temporal dentro del directorio privado, cifrar por trozos
    al destino, borrar el temporal; `PRAGMA temp_store=MEMORY` en la conexión que exporta. Con Python ≥ 3.11 vale
    `backup()` a `:memory:` + `Connection.serialize()` (sin tocar disco; verificado disponible).
  - **Passphrase**: mínimo 12 caracteres; nunca en `argv`, variable de entorno, log ni HTTP (el panel web no debe pedirla:
    pasaría por el navegador y su autorrelleno); se pide en la barra (`NSSecureTextField`) o con `getpass` en el CLI.
    **No prometer "borrado seguro en memoria"**: en CPython ni `str` ni `bytes` se pueden poner a cero; se limita la vida
    (derivar la clave y soltar referencias) y se documenta como límite.
  - **Restaurar** (criterio de salida): descifrar a temporal privado → `PRAGMA quick_check` → comparar `meta.version_esquema`
    y `dispositivo_id`; mismo dispositivo = sustituir; distinto = fundir con `INSERT OR IGNORE` por clave.

### 2.5 ALTA — PRAGMA y límites: tres no se comportan como dice el plan
- `secure_delete` y `foreign_keys` son **por conexión** (verificado: 0 en la segunda). Con "una conexión por hilo", cualquier
  conexión que no los fije deja páginas sin limpiar y claves foráneas sin comprobar. Única función `abrir(ro=False)` que
  fija los PRAGMA, y un test que los lee en cada conexión que crea el almacén.
- `secure_delete` no limpia el **WAL**: lo borrado sigue en los frames del WAL hasta que se sobrescriben; el checkpoint no
  trunca el fichero salvo `journal_size_limit` (documentación WAL §6). Y en APFS/SSD la truncación no borra bloques. Honesto:
  `secure_delete` reduce restos dentro del `.sqlite`; la defensa real sigue siendo FileVault/BitLocker. Fijar
  `journal_size_limit` (p. ej. 64 MB) y `wal_checkpoint(TRUNCATE)` desde el dueño cuando no haya lectores.
- `max_page_count` (si "tamaño máximo" significa eso) es un tope que, en un almacén de solo añadir, se convierte en
  **autodenegación**: al llegar, todos los `INSERT` fallan con `SQLITE_FULL`. Mejor vigilar el tamaño y avisar; los topes
  van en la ingesta (filas por fuente y por pasada, rangos de 2.1).
- `page_size` no se puede cambiar en WAL (doc de PRAGMA): se decide antes de crear la base. 4096 por defecto vale; no tocarlo.
- `STRICT` exige SQLite ≥ 3.37 y `serialize()` Python ≥ 3.11; el README promete **Python 3.9+** (`README.md:55`, `:67`) y los
  instaladores de 3.9 llevan SQLite anterior (confianza media). Decidir: subir el mínimo a 3.11 (recomendado) o renunciar
  a `STRICT`. `almacen.abrir()` debe comprobar `sqlite3.sqlite_version_info ≥ (3, 37)` y fallar con mensaje claro.
- `busy_timeout` corto en WAL solo afecta a escritores entre sí y al checkpoint; los lectores nunca bloquean. Bien como está.
- Los lectores largos (`panel.py` con hasta 120 s) impiden que el checkpoint avance y el WAL crece (doc WAL §6): el constructor
  inicial, que es el momento de más escritura, coincide con la regeneración del panel. Lotes cortos + lector que no abra
  una transacción larga (sin `BEGIN` explícito, una consulta cada vez).

### 2.6 ALTA — Volumen externo, red y propiedad desactivada
- Verificado: el checkout está en EVO990, APFS local con **`Owners: Disabled`**. En ese volumen macOS trata a cualquier usuario
  como propietario de todo: `0600`/`0700` (`permisos.py:40-47`) no separan usuarios. La base de desarrollo nacería ahí
  (`rutas.RAIZ` sale de `__file__`, `infobar/rutas.py:9`) y además un tirón del disco con WAL activo deja `-wal`/`-shm`
  huérfanos. La instalación real está en `~/.cost-guard` (interno, `drwx------`): ahí no aplica.
- WAL **no funciona en red** (doc WAL §1 y §2.2); `locking_mode=EXCLUSIVE` lo haría funcionar sin `-shm`, pero mata el diseño
  "un escritor y varios lectores". iCloud/Dropbox/OneDrive sincronizan `-wal` a medias y corrompen.
- Qué hacer:
  - `abrir()` comprueba que `PRAGMA journal_mode=WAL` **devuelve** `wal` (en red o VFS sin memoria compartida no lo hace) y se
    niega a crear la base si la ruta cae bajo `Mobile Documents`, `Dropbox`, `OneDrive` o un sistema de ficheros de red
    (`statfs.f_fstypename` en `smbfs/nfs/webdav`); avisa si el volumen tiene propiedad desactivada
    (`MNT_IGNORE_OWNERSHIP`), que es el caso del checkout.
  - Decidir dónde vive la base **en desarrollo**: seguir la convención `rutas.RAIZ` (checkout, sin protección de usuario)
    o `COSTGUARD_DATOS`/`~/.cost-guard` también en desarrollo. Recomendación: la base siempre bajo el directorio de
    datos de la instalación, con `rutas.ALMACEN` redirigido en `tests/aislamiento.py` (`:14-19`) como las demás rutas.
  - `.gitignore` no cubre `*.sqlite*` (verificado): añadirlo antes del primer commit de la fase 1.

### 2.7 MEDIA — Hora UTC: lo que falta por fijar
- Buena noticia verificada: todas las fuentes son UTC nativo (Claude y Codex con `Z` al 100 %, pi `Z`, OpenCode ms epoch,
  Hermes epoch real). El test "misma salida con otra TZ" es viable.
- Ambigüedad del plan: `hora_utc INTEGER` ¿segundos o hora (epoch/3600, como decía el pre-mortem)? Para `turnos` deben ser
  **segundos**; la hora se deriva. Si no, la ventana de 5 h y el "ritmo" pierden precisión y no se pueden recalcular.
- Marcas sin zona: `a_local` las devuelve tal cual (`lectores_logs.py:46-48`) y hoy se interpretan como locales. En la base
  no hay que adivinar: sin zona → anomalía, no fila (hoy no ocurre ninguna).
- La ventana de 5 h y el "ritmo" se calculan hoy con `time.mktime(strptime("%Y-%m-%dT%H"))` (`agregacion.py:177`), ambiguo el
  25-10-2026 entre 02:00 y 03:00. Con `hora_utc` en segundos, las ventanas se calculan en UTC (`hora_utc ≥ ahora − 5·3600`)
  y solo los **nombres** de día/hora se derivan en local. Fijarlo como regla, no como consecuencia.
- Fixtures concretas: 2026-10-25 00:30Z y 01:30Z (ambos "02" local: el día tiene 25 horas y la ventana cuenta los dos);
  2026-03-29 01:30Z ("03:30" local; el día tiene 23 horas); TZ de media hora (`Asia/Kolkata`) y hemisferio sur
  (`America/Santiago`); `TZ=UTC`. Los tests ya fijan TZ con `tzset()` (`tests/test_costbar.py:29-44`).
- La sombra de 7 días: si arranca entre el 19 y el 25 de octubre cubre el cambio de hora real. Programarla para que lo cubra.

### 2.8 MEDIA — OpenCode, Hermes y el constructor
- `opencode-.db` es una base **vacía** (2 mensajes, 4 KB, 0 ids comunes): leer las dos con deduplicación por id es seguro.
  El código actual se queda con la primera que existe (`lectores_logs.py:348-357`, `break`).
- Hermes solo da totales por sesión (`lectores_logs.py:394`): el delta por lectura es correcto, pero la **primera** lectura de
  una sesión antigua mete todo su total "ahora". Regla: el primer delta se fecha en `started_at` (lo que hace hoy el código) y
  los siguientes en la hora de lectura; un total que **baja** (sesión reiniciada) es anomalía, no delta negativo.
- Constructor: cada pasada reparsea el fichero entero cuando cambia (`agregacion.py:31-41`); con `REPLACE` eso son miles de
  DELETE+INSERT por minuto en una sesión activa, con `secure_delete` poniendo a cero cada vez. El upsert condicional de 2.1 lo
  convierte en no-op para filas sin cambio (SQLite no escribe si el `WHERE` del `DO UPDATE` es falso). Medir en frío con
  `secure_delete=ON`, no solo con la caché de disco caliente.

### 2.9 MEDIA — `proyecto_raw` puede llevar el nombre de usuario del Mac
- `parsear` toma los **3 últimos** trozos del slug (`lectores_logs.py:81-83`): para un proyecto en `~/x` el resultado es
  `Users/<usuario>/x`. Hoy es efímero; en `turnos` y en el export es permanente y sale fuera del Mac. Recortar `Users/<x>` y
  `home/<x>` antes de guardar cambiaría la identidad que usan `config.proyectos` y `aliases`: hay que decidirlo ya (y, si se
  cambia, migrar las claves de `config.json`).

### 2.10 BAJA — Detalles que cuestan poco ahora y mucho después
- `INSERT OR REPLACE` dispara `ON DELETE CASCADE` de cualquier tabla hija futura (fase 2: notas, etiquetas, alertas por turno)
  y cambia `rowid`: otra razón para `ON CONFLICT DO UPDATE`.
- `fuentes.id_fuente = hash(ruta relativa)`: el universo de rutas es pequeño y el hash se invierte por diccionario; no es
  secreto, es solo "no portar la ruta". Decirlo así en el esquema para que nadie lo trate como seudonimización.
- `olvidar_cache()` borra `state.json` (`config_y_estado.py:209-214`); el plan ya dice que no tocará la base. Test explícito.
- El export cifrado necesita un registro de auditoría (fase 2) que no incluya el nombre del destino si es personal.
- `MAX_CUERPO`, cookies y Host no cambian; ningún endpoint nuevo en la fase 1 (solo lectura del mismo informe).

## 3. Cambios al plan, ordenados

| # | Cambio | Gravedad |
|---|---|---|
| 1 | Sustituir `INSERT OR REPLACE` por `ON CONFLICT DO UPDATE` **monótono** (nunca reduce contadores ni mueve `hora_utc`), con rangos por campo y tabla/contador de `anomalias` visible en el panel (2.1) | Crítica |
| 2 | Dueño de escritura por latido en `meta`; todo lo demás abre `mode=ro`; `escanear()` partido en persistir/leer; test de que `panel.py` y el servidor no llegan a ningún `INSERT`; ciclo de servidores fuera del hilo del constructor (2.2) | Crítica |
| 3 | Clave con espacio de nombres por agente; hash para ids no-Anthropic, Codex (id de sesión de la cabecera + ts + contadores), pi y Hermes (2.3) | Alta |
| 4 | Especificación del export cifrado: `cryptography` fijada con hashes + `scrypt` stdlib, formato `.cgx` por trozos con cabecera como AAD y flag de último trozo, temporal solo en el directorio privado (o `serialize()`), `temp_store=MEMORY`, passphrase solo en barra/CLI, sin promesa de borrado en memoria, procedimiento de restauración como criterio de salida (2.4) | Alta |
| 5 | Función única `abrir()` que fija `secure_delete`, `foreign_keys`, `journal_size_limit`, `busy_timeout`; test de PRAGMA en cada conexión; checkpoint `TRUNCATE` periódico; sin `max_page_count`; comprobar SQLite ≥ 3.37 al abrir (2.5) | Alta |
| 6 | Mínimo Python 3.11 (o renunciar a `STRICT`) y actualizar el README (2.5) | Alta |
| 7 | `abrir()` verifica que `journal_mode` devuelve `wal`, rechaza rutas en nube/red y avisa si el volumen ignora la propiedad; decidir ruta de la base en desarrollo; `*.sqlite*` en `.gitignore`; `rutas.ALMACEN` en `tests/aislamiento.py` (2.6) | Alta |
| 8 | `hora_utc` en **segundos**; sin zona = anomalía; ventanas calculadas en UTC; fixtures de 2.7; sombra que cubra el 25-10-2026 (2.7) | Media |
| 9 | Hermes: primer delta en `started_at`, totales que bajan = anomalía; leer las dos bases de OpenCode con deduplicación por id (2.8) | Media |
| 10 | Medir el constructor en frío con `secure_delete=ON` y upsert condicional; tope por fuente y pasada (2.8) | Media |
| 11 | Decidir si `proyecto_raw` recorta `Users/<x>`; si sí, migración de claves de `config.proyectos` (2.9) | Media |
| 12 | Bloqueo portable (sin `fcntl` a nivel de módulo) para el camino a Windows; test de que `olvidar_cache()` no toca la base (2.2, 2.10) | Baja |

## 4. Decidir antes de codificar

1. **Regla de conflicto**: "el máximo gana" (recomendado) frente a "la última gana". Evidencia a favor: los 11 mensajes
   acumulados y los tres ataques de 2.1 se resuelven igual con "máximo".
2. **Dependencia `cryptography`**: sí (recomendado) o no. Si no, el export cifrado no existe hasta que exista una
   librería aceptada; no se hace un cifrado casero "mientras tanto".
3. **Python mínimo**: 3.11 (recomendado: `STRICT`, `serialize()`, SQLite moderno en Windows) o mantener 3.9 sin `STRICT`.
4. **Ruta de la base en desarrollo**: checkout (EVO990, sin propiedad por usuario) o directorio de datos de la instalación.
5. **Quién escribe sin barra**: el CLI puede tomar el relevo si no hay latido (recomendado) o nadie.
6. **Dónde se pide la passphrase**: barra y CLI (recomendado); nunca el panel web.
7. **`proyecto_raw`**: tal cual (3 trozos) o recortando `Users/<x>`.
8. **Hermes**: primer delta en `started_at` (recomendado) o en la hora de la primera lectura.
9. **Calendario de la sombra**: 7 días que incluyan el 25-10-2026.

## Supuestos

- La instalación real sigue en `~/.cost-guard` en el disco interno (verificado hoy por `ls`/`stat`); si alguien instala en un
  disco externo o de red, aplican 2.6 por completo (confianza alta).
- Los instaladores de Python 3.9 para Windows/macOS traen SQLite < 3.37 (confianza media; basta comprobar
  `sqlite3.sqlite_version` en una instalación 3.9 para confirmarlo).
- `max_page_count` es por conexión y no persistente (confianza media; irrelevante si se descarta, como se recomienda).
- La forma `msg_<ms>_<sufijo>` la genera el proxy del backend no-Anthropic y no Claude Code (confianza media; no cambia la
  recomendación de espacios de nombres).
- Las afirmaciones sobre WAL, `secure_delete`, `page_size` y `journal_size_limit` están tomadas de la documentación oficial de
  SQLite consultada hoy; las de comportamiento por conexión y `mode=ro`, de pruebas locales con SQLite 3.53.4.
