# Revisión de 1.5 — copias, restauración, CLI e hilo (2026-10-14)

Objeto: lo construido tras `infobar/cgx.py` (commits 469e1ae..f1f28f7): `infobar/claves_copias.py`, `exportacion.py`,
`restauracion.py`, `cli_copias.py`, `hilo_copias.py`, los cambios en `costbar.py`, `barra.py`, `consulta.py`,
`servidor_local.py`, `install.sh` y `requirements.txt`, y sus tests. Referencias: diseño v2 de 1.5 en
`plans/261013-2350-sqlite-historico/plan.md` e informe `plans/reports/revision-diseno-1-5-261014.md` (B1-B3, A1-A5).

**Cómo se ha verificado.** La batería completa pasa (404 tests, 31 s, Python 3.14.6 + `cryptography` 46.0.7 del scratchpad).
Los experimentos y las mutaciones se han hecho sobre una copia del repositorio en el scratchpad (`exp/exp1.py`…`exp5.py`,
`exp/mutar.py`, `exp/mutar2.py`); el repositorio no se ha tocado salvo este informe. Lo que lleva **[medido]** se ha
reproducido; lo que lleva **[razonado]** sale de leer el código y no se ha ejecutado.

**Resultado en una línea.** B1 (WAL huérfano), la firma del productor, la relectura verificada, la rotación posterior a la
verificación, los límites de scrypt y el endurecimiento de `getpass` están en el código y casi todos los protege un test.
Pero el caso para el que existen las copias, restaurar en un equipo nuevo o sobre una base dañada, **no funciona** con la
CLI. Además, la rotación y el hilo tienen tres modos de perder copias o llenar el disco sin que nadie se entere, porque el
estado de las copias no se muestra en ninguna parte.

---

## BLOQUEANTE

### K1. En un equipo nuevo no se puede restaurar ninguna copia automática: la firma pública del paquete no entra por ningún sitio
- **Dónde.** `cli_copias.py:76-79` (`_firmas_conocidas`): solo lee `config.json → copias.firmas_publicas` y añade la clave
  de firma del dispositivo **actual**. `claves_copias.py:77`: el fichero de la clave privada solo contiene la privada; B2
  pedía incluir en él la pública de firma. `cli_copias.ORDENES` (21-22) no tiene ninguna opción para dar una firma pública.
- **Escenario [medido, exp1 E2].** Se roba o muere el portátil. El usuario instala en otro equipo, tiene su `clave.txt` y el
  «PAQUETE DE RESTAURACIÓN» impreso y ejecuta `--importar copia.cgx --clave-privada clave.txt`. Resultado: código 1,
  «la copia no está firmada por un dispositivo conocido». La única salida es editar `config.json` a mano y añadir
  `"firmas_publicas": ["cgxs1…"]`, y eso no está documentado en ningún sitio.
- **Por qué no lo ven los tests.** `test_cli_copias.py:135-157` y la autoprueba generan, copian y restauran con el mismo
  `DATOS`, así que la firma local siempre está. Por la misma razón, `--probar-restauracion` sale bien en el equipo de origen y
  **da una confianza falsa**: ensaya justo lo que no falla.
- **Arreglo.** (1) `--firma-publica cgxs1…` en `--importar` y `--probar-restauracion`. Es pública y puede ir en argv:
  `_sin_secretos_en_argv` solo bloquea `CGX-SECRET`/`CGX-SIGN`. (2) Además, guardar la pública de firma junto a la privada
  (una segunda línea en el fichero, o en el texto cifrado si va con frase) y que `cargar_privada` la devuelva. Así el
  fichero que el usuario sí conserva basta para restaurar. (3) Si falla la firma y no hay más firmas que la local, el mensaje
  debe decir «falta la firma pública del equipo que hizo la copia: está en tu paquete de restauración (cgxs1…)». (4) Test:
  restaurar con un `rutas.DATOS` y una config vacíos, partiendo solo del fichero de la privada y del paquete.

## ALTO

### A-1. No se puede restaurar sobre una base dañada, que es cuando más falta hace
- **Dónde.** `restauracion.py:72-76`: con `--forzar`, la base actual se abre con `almacen.abrir(destino)` en escritura (pone
  WAL y migra) y, si falla, aborta con «no se ha cambiado nada».
- **Escenario [medido, exp1 E1].** Cabecera o páginas 1-2 de `consumo.sqlite` corruptas. `--importar … --forzar` responde
  «no se puede abrir la base actual (DatabaseError); no se ha cambiado nada», y no dice qué hacer. El usuario tiene que
  adivinar que debe mover la base y sus `-wal`/`-shm` a mano.
- **Arreglo.** Si `abrir` falla con `DatabaseError` y hay `--forzar`, apartar la base sin abrirla: renombrar
  `consumo.sqlite`, `-wal` y `-shm` a `consumo.sqlite.danada-<ts>` dentro de `DATOS/copias`. Así queda como «copia previa»
  y no se pierde lo que se pudiera rescatar. Antes hay que comprobar que nadie la tiene abierta (en POSIX, un
  `BEGIN EXCLUSIVE` con una conexión cruda de `sqlite3`; si eso tampoco funciona, pedir parar la barra, ver A-2). Test con
  los dos tipos de corrupción de exp1.

### A-2. «Cierra la barra» no se puede cumplir: launchd la relanza y Salir no suelta el puesto
- **Dónde.** `install.sh:61` (`KeepAlive=true`). `barra.py:449-450` (`salir_` → `terminate_`, sin `launchctl bootout`). El
  puesto solo se suelta en el `finally` de `HiloIngesta.run` (`ingestor.py:202-203`), pero nadie activa `parar` al salir y el
  hilo es de tipo daemon: con `terminate_` se mata sin pasar por ese `finally` [razonado]. Los mensajes de `restauracion.py:78` y el README («Se niega si la barra está escribiendo
  (ciérrala antes)»).
- **Escenario [razonado; launchd no ejecutado].** El usuario pulsa Salir. El latido de la barra sigue vigente 90 s
  (`almacen.LATIDO_TTL_S`) y launchd la relanza en unos 10 s. Durante esos 90 s `--importar` se niega; después compite con la
  barra relanzada para coger el puesto. En un **equipo nuevo** (K1) es peor: `install.sh` arranca la barra, la barra crea
  una base vacía y la mantiene con latido, y `--importar --forzar` se niega siempre. Si el usuario gana la carrera, la
  restauración ocurre con la barra viva y conexiones abiertas a la base, que es justo el escenario A3. Además, la restauración
  suelta el puesto **antes** del reemplazo (M-3).
- **Arreglo.** Que el mensaje dé la orden exacta (`launchctl bootout gui/$(id -u)/dev.codeia.costguard`, y en el README la
  de vuelta con `launchctl bootstrap …`). Mejor aún: que la restauración cree un marcador `DATOS/restaurando` (O_EXCL, con
  pid) que la barra y `--ingerir` respeten sin abrir la base, y que la restauración lo borre en `finally`. `salir_` debería
  llamar a `HiloIngesta.cierra()`, que suelta el puesto.

### A-3. Con la base dañada se escribe una copia `.danada` cada 15 minutos y nunca se borran
- **Dónde.** `exportacion.py:176-180`: `toca_copia` devuelve True si la última fue `danada`. `hilo_copias.py:45`: tras una
  copia correcta, aunque sea `.danada`, la espera es `REVISA_CADA_S` = 900 s. `exportacion.py:143` y `a_borrar` (84-95): las
  `.danada` no entran nunca en la rotación.
- **Escenario [medido, exp1 E3 + lectura del bucle].** Un sector malo hace fallar `quick_check`. Desde ese momento salen 96
  copias completas al día, ninguna se rota y todas son del tamaño de la base. Con la carpeta por defecto (`DATOS/copias-cifradas`,
  en el mismo volumen que la base) el disco se llena y la ingesta acaba en `SQLITE_FULL`. En una carpeta de nube, se agota la
  cuota y fallan también las copias buenas de los demás equipos.
- **Arreglo.** Una `.danada` cuenta como hecha a efectos de «cuándo toca»: como mucho una al día, igual que las buenas. Hay
  que conservar como máximo las 3 `.danada` más recientes (borrarlas solo después de verificar la nueva). El aviso de «base
  dañada» se muestra en la interfaz (A-5). Test: diez revisiones seguidas con la base dañada producen una sola copia.

### A-4. Dos equipos en la misma carpeta de copias: la rotación de uno borra las del otro
- **Gravedad condicionada.** Es ALTO si se admite compartir una carpeta entre equipos, que es lo que sugieren las varias
  firmas del diseño y la carpeta sincronizada. Si no se admite, baja a BAJO y basta con documentar «una carpeta por equipo»
  (ver las preguntas abiertas).
- **Dónde.** `exportacion.py:84-95`: `a_borrar` se queda con «la última de cada día» sin distinguir de qué dispositivo viene
  la copia. El diseño admite varias firmas (varios dispositivos) y la carpeta puede estar sincronizada.
- **Escenario [medido, exp2 E5].** Durante 30 días, B copia a la 1:00 y A a las 2:00 en la misma carpeta de OneDrive.
  Quedan 14 copias de A y **2 de B** (solo las primeras de mes). Si el disco de B muere, B no tiene copias diarias.
- **Arreglo.** Incluir un identificador corto del dispositivo en el nombre (`copia-<disp8>-AAAAMMDD-…`), por ejemplo los 8
  primeros hex de la huella de la firma pública, y rotar solo las propias. Otra opción es una subcarpeta por dispositivo. Test
  con el escenario de exp2.

### A-5. Los fallos de las copias son silenciosos: el estado no se muestra en ningún sitio y un reloj adelantado las detiene un año
- **Dónde.** `consulta.py:150` calcula `r["copias"]` y `servidor_local.py:224` lo devuelve en `/origenes`, pero **ningún
  fichero de `panel_assets/`, `popover.py` ni `barra.py` lo pinta** (búsqueda de `copias` vacía). Tampoco existe el
  recordatorio de ensayo cada 90 días (B3.5: no hay `ultimo_ensayo` en el código). `exportacion.py:180` y `192-193`: edad
  negativa.
- **Escenario [medido, exp1 E4b].** Una vez el reloj se adelantó un año (batería del RTC, NTP mal configurado) y se registró
  una copia con esa fecha. Al corregirse el reloj, `toca_copia()` da False durante un año y `estado_copias` dice
  `al_dia` con `edad_h = -8760`. Igual de silenciosos: la carpeta sin montar (M-2), el `OSError` de cada hora en el hilo (que
  solo va al log) y A-3.
- **Arreglo.** (1) Usar `abs(ahora - ts)` y tratar `ts > ahora + 1 día` como «nunca» (`almacen._vigente` ya usa `abs` por lo
  mismo). (2) Pintar el estado en el desplegable: aviso persistente si es `nunca`, `atrasada`, `danada` o `sin_destinatario`.
  Guardar también el último error del hilo (`ultimo_error` en `ultima_copia.json`) para mostrarlo. (3) Recordar el ensayo cada
  90 días con `ultimo_ensayo`, que escribe `--probar-restauracion`. Test de `estado_copias` con `ts` en el futuro.

## MEDIO

### M-1. La rotación puede borrar la copia recién escrita (reloj atrasado) y el resumen falla después
- **Dónde.** `exportacion.py:143-147`: `a_borrar(os.listdir(carpeta))` no excluye `destino`, y `os.path.getsize(destino)` se
  calcula después de rotar.
- **Escenario [medido, exp2 E4].** Hay 200 días de copias y el reloj vuelve a 2001 (pila del RTC): la copia nueva,
  `copia-20010101-…`, es la más antigua, así que la rotación la borra y `getsize` lanza `FileNotFoundError`. El hilo lo apunta
  como fallo y repite cada hora: escribe una copia completa y la borra.
- **Arreglo.** `a_borrar(...)` sin `basename(destino)`; calcular `bytes` antes de rotar; si la fecha del nombre es anterior a
  la de la copia más reciente de la carpeta, no rotar y avisar de que el reloj va mal. Test con el caso de exp2.

### M-2. Destino sin montar: `makedirs` lo crea en silencio. La carpeta por defecto está en el mismo disco que la base y no hay forma soportada de cambiarla
- **Dónde.** `exportacion.py:124` (`os.makedirs(carpeta, exist_ok=True)`). `exportacion.py:23-25` (por defecto
  `DATOS/copias-cifradas`). `cli_copias.claves_generar` (95-120) siempre escribe `carpeta` con el valor por defecto, y el
  panel no puede escribir `copias` (la clave no está en `ajustes.normalizar`), así que solo se cambia editando `config.json`
  a mano.
- **Escenario [medido, exp1 E6].** Con la sesión de OneDrive cerrada (o la carpeta de nube quitada), la ruta configurada no
  existe: la copia se crea en una carpeta local nueva con el mismo nombre, el estado dice `al_dia` y nada sale del equipo.
  Con la carpeta por defecto, un fallo del disco se lleva la base y las 20 copias juntas. No hay exclusión de Time Machine
  (búsqueda de `tmutil` vacía fuera de `plans/`), así que con TM activado sí salen, pero la aplicación no lo sabe ni lo
  comprueba.
- **Arreglo.** `--claves-generar … --carpeta RUTA` (o `--carpeta-copias RUTA` como orden aparte), que crea la carpeta en ese
  momento y comprueba que se puede escribir en ella. En `hacer_copia`, si la carpeta configurada no existe, fallar con «la
  carpeta de copias no está disponible (¿disco o nube sin montar?)» en vez de crearla. Si la carpeta es la de por defecto,
  marcar el estado como `local` («las copias no salen de este disco»).

### M-3. Restauración: suelta el puesto antes del reemplazo, da un mensaje falso después de reemplazar y no envuelve el error de Windows
- **Dónde.** `restauracion.py:95` (`soltar_escritura` antes de `os.replace` en la línea 100), en contra de A3 («el
  `os.replace` ocurre dentro del TTL con el arrendamiento en mano»). `restauracion.py:102`: el segundo `_borra_wal`, **ya con
  la base reemplazada**, lanza «no se puede borrar … no se ha cambiado nada». `restauracion.py:100`: `exportacion.reemplazar`
  puede lanzar `PermissionError` sin envolver.
- **Escenario.** (a) [razonado] Entre la línea 95 y la 102, una barra o un `--ingerir` vivos (A-2) pueden coger el puesto y
  crear un `-wal` nuevo en la ruta. El borrado posterior lo cubre en POSIX; en Windows ese borrado falla y el mensaje
  asegura que no ha cambiado nada cuando la base ya es otra. (b) [medido, exp5] Con `os.replace` fallando como en Windows
  (fichero en uso), sale una traza `PermissionError` en vez de un mensaje. **No se pierden datos**: el lector abierto
  devuelve las filas del WAL al cerrar y la copia previa las contiene. Verificado.
- **Arreglo.** No soltar el puesto de la base vieja: se va a sustituir, y la restaurada trae su propio `meta.escritor`, ya
  caducado. Solo hay que cerrar la conexión. Envolver el reemplazo en `ErrorRestauracion` con la ruta de la copia previa. Si
  el `_borra_wal` posterior falla, el mensaje debe decir «la base YA se restauró; borra a mano X-wal/X-shm antes de abrir la
  barra». Probar con test los dos mensajes (es la mutación «sin borrado posterior», que hoy sobrevive).

### M-4. Temporales en claro: el barrido borra los de otro proceso en marcha y no ve los de la restauración
- **Dónde.** `exportacion.py:36-48`: `barrer_tmp` borra **todo** `DATOS/tmp` cuando arranca la barra.
  `restauracion.py:79`: el temporal descifrado `.restaurando-*.tmp` va en `dirname(destino)`, es decir en `DATOS`, no en
  `DATOS/tmp`.
- **Escenario [razonado].** (a) El usuario lanza `--probar-restauracion` y launchd relanza la barra en ese momento: el
  barrido borra `prueba-*.tmp` a mitad, `_comprueba` no encuentra el fichero y el ensayo dice «no es una base SQLite» de una
  copia que está bien. Lo mismo pasa con `--exportar` (sale una traza `FileNotFoundError`). (b) Un `kill -9` o un apagón
  durante `--importar` deja en `DATOS` la copia entera en claro como `.restaurando-*.tmp`, y nadie la barre nunca. La
  exposición es de la misma clase que la base viva, pero queda indefinidamente y duplica el tamaño.
- **Arreglo.** Barrer solo los ficheros con más de 6 h de `mtime`, y también los `DATOS/.restaurando-*.tmp`. Cuando el
  destino es `rutas.ALMACEN`, crear el temporal de la restauración en `dir_tmp()` (mismo sistema de ficheros, así que
  `os.replace` sigue siendo atómico).

### M-5. La restauración se salta la guarda de «checkout de desarrollo no escribe en los datos reales»
- **Dónde.** `restauracion.py:59` y `74`: se llama a `almacen.abrir(destino)` con una ruta explícita, y entonces `por_defecto`
  es False y `almacen.py:110-111` no actúa.
- **Escenario [medido, exp1 E8].** Desde el checkout, sin `COSTGUARD_DATOS`, `almacen.abrir()` se niega, pero
  `restaurar(..., forzar=True)` sustituye `rutas.ALMACEN` sin problema. Basta un `--importar --forzar` de prueba desde el
  repositorio para pisar la base real.
- **Arreglo.** En `restaurar`, si `destino` es None y `plataforma.es_checkout_de_desarrollo(rutas.RAIZ)`, negarse con el
  mismo mensaje. Test.

### M-6. Un `.cgx` enorme llena el disco de `DATOS` antes de que se rechace
- **Dónde.** `restauracion.py:79-84` y `cgx.py:325-336`: la firma se comprueba al final, cuando el texto en claro ya está
  entero en el temporal de `dirname(destino)`. `probar` hace lo mismo en `DATOS/tmp`.
- **Escenario [razonado].** Alguien con acceso a la carpeta sincronizada deja un `.cgx` de 50 GB con una cabecera válida a la
  pública del usuario (no hace falta firma para llegar hasta el final). `--probar-restauracion` o `--importar` llenan el disco
  donde vive la base viva antes de decir «no está firmada», y la ingesta falla con `SQLITE_FULL` mientras tanto. El temporal
  se borra después, pero el daño ya está hecho. En modo frase pasa lo mismo con cualquier fichero cifrado con esa frase.
- **Arreglo.** Antes de descifrar, comparar el tamaño del fichero con `shutil.disk_usage(dirname)` y dejar un margen (por
  ejemplo, que quede al menos el doble del tamaño de la base actual más 1 GiB). Si no cabe, negarse. Opcional: mostrar el
  tamaño en la confirmación.

### M-7. La autoprueba de Windows no prueba lo que falla en Windows
- **Dónde.** `cli_copias.py:273-278`: la base que se sustituye se cierra antes de restaurar (`almacen.abrir(base_vieja).close()`).
  `cli_copias.py:265`: el paso de la copia se da por bueno siempre (`True`).
- **Escenario [razonado].** En Windows el riesgo real es `os.replace` o el borrado del `-wal` con la base abierta por otro
  proceso (la barra) y el antivirus. La autoprueba sale «todo correcto» y la primera restauración real termina con una traza.
- **Arreglo.** Añadir a la autoprueba un paso con una conexión abierta en otro hilo sobre la base que se sustituye: debe
  terminar con un `ErrorRestauracion` legible, sin traza, y la base intacta. Comprobar el resultado real de cada paso.

## BAJO

- **Trazas en la CLI [medido, exp4].** `--importar /no/existe.cgx` → `FileNotFoundError`; `--probar-restauracion DIRECTORIO`
  → `IsADirectoryError`; `--claves-generar` con stdin cerrado → `EOFError` en `input()`; un FIFO como `.cgx` se queda colgado
  en la lectura. `--exportar` sin base escribe «Error: /ruta/consumo.sqlite». Arreglo: en `main` (`cli_copias.py:335-340`)
  capturar `OSError` y `EOFError` y responder con un mensaje y código 1, y rechazar lo que no sea un fichero regular
  (`os.path.isfile`).
- **`_dentro_de` distingue mayúsculas en APFS [medido].** `claves_copias.py:63-65`: `…/COPIAS` frente a `…/Copias` da False
  aunque sea el mismo directorio (`samefile` True). La guarda «no guardes la privada en la carpeta de las copias» se salta
  escribiendo la ruta con otro caso. Arreglo: comparar con `os.path.normcase` y además `os.path.samefile` sobre los ancestros
  que existan. Tampoco avisa si la privada va dentro de `DATOS`: la regla del diseño es que la privada nunca se quede en el
  equipo.
- **`.part` huérfanos en la carpeta de copias [medido, exp1 E7].** Si el proceso muere a mitad, `.copia-*.part` (cifrado) se
  queda para siempre: `a_borrar` no lo reconoce y `barrer_tmp` no mira ahí. Arreglo: borrar los `.copia-*.part` de más de un
  día al empezar `hacer_copia`. Igual con `.export-*.part`.
- **URI sin escapar en `_comprueba` [razonado].** `restauracion.py:36` (`f"file:{ruta}?mode=ro"`): un `#` o un `?` en
  `COSTGUARD_DATOS` cortan la ruta. `almacen.abrir` ya usa `pathlib.Path(ruta).as_uri()`; usar lo mismo. En Windows, con
  `C:\…`, no está verificado.
- **`destinatario` escrito a mano como cadena.** `cli_copias.py:73` hace `list("cgx1…")`, que da una lista de caracteres, y
  `hilo_copias.py:25` toma `"c"`: error de decodificación cada hora. Normalizar: si es `str`, convertirlo en `[str]`.
- **Dependencias (M1 del informe previo, a medias).** `requirements.txt` fija `cryptography==46.0.7`, pero sin hashes ni
  `cffi`/`pycparser` fijados, y `install.sh:44` lo instala aparte. El propio fichero lo reconoce. Queda pendiente el lock por
  plataforma.
- **La copia previa siempre va a `rutas.COPIAS`** (`restauracion.py:93`) aunque el `destino` sea otro, y rota con
  `conservar=5`: después de cinco restauraciones se pierde la primera copia previa. Basta con mencionarlo en el mensaje final.

## Tests que no prueban lo que dicen, o dependen del reloj o del terminal

- `test_hilo_copias.py:83-103` (`test_una_copia_lenta_no_retrasa…`). El `sleep(2.5)` va **antes** de llamar a la
  `hacer_copia` real, así que la vuelta de ingesta corre cuando todavía no hay ningún backup en marcha y no mide
  contención. Además depende del reloj (`dt < 1.0`) y puede fallar en una máquina cargada. Arreglo: un backup real
  bloqueado a mitad (por ejemplo, un `con.backup` con `pages=1` y `progress` que espere en un `Event`) y comprobar que la
  vuelta escribe y que el latido avanza.
- `test_cli_copias.py:135-157` y la autoprueba: mismo `DATOS` en todo el ciclo, por eso no ven K1.
- `test_cli_copias.py:76-80` (clave por argumento): con la guarda quitada, el test también falla, pero porque `x.cgx` no
  existe y salta `FileNotFoundError` sin capturar (exp4), no por la aserción. Hay que dar un `.cgx` que exista.
- `test_exportacion.py:97-98`: `unittest.main()` está a mitad del fichero; ejecutado directamente
  (`python tests/test_exportacion.py`), `TestCopia` y `TestReemplazo` no corren. Con `discover` sí.
- `.danada`: que queden fuera de la rotación sí está probado (`test_conserva_14_diarias…` las incluye entre los nombres
  ajenos). Lo que ningún test cubre es el límite de una `.danada` al día ni su número máximo (A-3).
- No hay ningún test que dependa de un terminal real: `Terminal.frase` solo se prueba con `isatty` simulado.

## Cobertura de B1-B3 y A1-A5 (código y test que muere al quitarlo)

Mutaciones ejecutadas: 29 (`exp/mutar.py`, `mutar2.py` y `mutar3.py`, cada una contra sus módulos de test). «Muere» quiere decir que
al menos un test falla.

| Punto del informe previo | En el código | Test que muere | Nota |
|---|---|---|---|
| B1 borrar `-wal`/`-shm` antes y después del reemplazo | sí (`restauracion.py:98-102`) | **sí** | Quitar los dos: 2 fallos. |
| B1 solo el borrado posterior | sí | no | Redundante en POSIX; en Windows importa (M-3). |
| B1 `wal_checkpoint(TRUNCATE)` | sí (94), sin mirar `busy` | no | Inocuo: la copia previa se hace con `backup()` y el WAL se borra igualmente. |
| B2 firma del productor | sí (`cgx.py:325-336`) | **sí** (4) | Pero incompleto en la práctica: K1. |
| B3.1 ensayo obligatorio en `generar` | sí (`claves_copias.py:134`) | **no** | «generar sin ensayo» sobrevive. |
| B3.1 config solo tras el ensayo | sí (`cli_copias.py:108-114`) | **no** | «escribe config aunque falle» sobrevive. |
| B3.2 relectura del fichero escrito | sí (`exportacion.py:136-138`) | **sí** | |
| B3.2 comparación del SHA-256 | sí | no | Solo detecta errores de programación; bajo. |
| B3.2 relectura en `--exportar` | sí (`cli_copias.py:167-169`) | **no** | |
| B3.3 rotación solo tras verificar | sí (143) | **sí** | Una primera variante que rota sin contar la nueva es equivalente en estado estable; no es un hueco. |
| B3.4 retención 14 + 6 | sí (`a_borrar`) | **sí** | Pero A-4 y M-1. |
| B3.5 `--probar-restauracion` | sí | no mutado aquí | Lo recorre el test de flujo completo. |
| B3.5 recordatorio de 90 días | **no** | — | A-5. |
| A1 tope de memoria de scrypt | sí (`cgx.py`) | **sí** (2) | |
| A2 suma de control de la clave | sí (`cgx.py:94`) | **sí** (3) | |
| A3 tomar el puesto al empezar | sí (77) | **sí** | |
| A3 refrescar el puesto antes del reemplazo | sí (91) | **no** | |
| A3 reemplazo con el puesto en mano | **no**: se suelta antes (95) | — | M-3. |
| A4 negarse sin TTY | sí | **sí** | |
| A4 `GetPassWarning` como error | sí (34) | no | Sin test del camino de eco. |
| A4 `RLIMIT_CORE` | sí (61-67) | no | |
| A4 la barra nunca toca secretos | sí (`hilo_copias` solo usa la pública y la firma) | — | Verificado leyendo el código. |
| A5 cero trozos | sí (`cgx.py:302`) | **sí** | |
| A5 bytes de cola en modo frase | sí (`cgx.py:337-338`) | **no** | En modo destinatario lo cubre la comprobación del tráiler (327); en modo frase no hay test. |
| M4 conexión de solo lectura del hilo | sí (`hilo_copias.py:28`) | **no** | Si se abre en escritura, la mutación sobrevive. |
| M6 clave por argumento | sí | sí, por accidente | Ver la lista de tests de arriba. |
| Fallo del hilo no se registra como copia hecha | sí | **sí** | |
| Copia previa al sustituir | sí | **sí** | |
| Temporal borrado si falla | sí | **sí** | |

## Comprobado y correcto (no hace falta tocar)

- **`os.replace` entre sistemas de ficheros:** no ocurre. El `.part` se crea en la carpeta de destino, el temporal de la
  restauración junto al destino y `ultima_copia.json` en `DATOS`.
- **Enlaces simbólicos [medido]:** `os.replace` sobre un enlace colgante en `--exportar` sustituye el enlace y no crea su
  destino; `os.remove` en la rotación borra el enlace, no el fichero al que apunta; `mkstemp` y `O_EXCL` no siguen enlaces.
  Un `copia-….cgx` que sea un enlace hacia fuera solo puede provocar que se borre el enlace.
- **Cerrar la conexión vieja después del reemplazo** (la ingesta reabre por inodo) **no borra el `-wal`/`-shm` de la base
  nueva [medido, exp3, SQLite 3.53.4 del Python 3.14 de Homebrew]**. Los datos escritos en la base restaurada sobreviven y `quick_check` da `ok`.
- **Permisos:** `DATOS/tmp` 0700; temporales, `.part` y `.cgx` 0600 (`mkstemp`); `clave_firma.txt` y la privada 0600 con
  `O_EXCL`; la copia previa 0600 (lo comprueba un test).
- **Secretos fuera de sitio:** `decodificar_clave` y `_de_b32` no repiten el texto recibido en sus errores. El log del hilo
  solo lleva el tipo de excepción y su mensaje, sin variables locales. El panel no puede escribir `copias` y `/origenes` solo
  expone claves públicas. El test «ni la privada ni la frase quedan en ficheros» recorre `DATOS`, la config y los logs.
- **CLI:** frase y privada solo por `getpass` y con TTY; la frase se pide dos veces; `--exportar` no sobrescribe;
  `--claves-generar` exige escribir `SI` y no pisa una clave existente.

## No verificado

- Windows real: `os.replace` y el borrado del `-wal` con la barra abierta, la URI `file:C:\…` de `_comprueba`, `getpass` en
  mintty (aquí se niega por `isatty`) y las ACL.
- Que launchd relance la barra tras Salir (A-2) se deduce de `KeepAlive=true`; no se ha ejecutado.
- exp3 se midió con SQLite 3.53.4. El entorno virtual que crea `install.sh` puede llevar otra versión de SQLite; el
  comportamiento al cerrar una conexión sobre un fichero ya sustituido no está comprobado en ella.
- Copias de conflicto de iCloud/Dropbox (`copia-… (conflicted copy).cgx`) y marcadores bajo demanda: `NOMBRE` no los
  reconoce, así que nunca rotarían y ocuparían espacio. No se ha probado.
- La relectura de B3.2 llega justo después del `fsync` y probablemente la sirve la caché del sistema: en un NAS o una nube
  con escrituras cortas no demuestra lo que quedó realmente en el servidor. Solo detecta corrupción local.

## Orden recomendado de arreglos

1. K1 (`--firma-publica` y firma dentro del fichero de la privada) con el test de equipo nuevo.
2. A-1 y A-2 (apartar la base dañada; marcador de restauración u orden de parada exacta) con M-3 y M-5, porque tocan el
   mismo flujo de `restaurar`.
3. A-3, A-4 y M-1 (rotación por dispositivo, `.danada` diaria y acotada, no borrar la recién escrita).
4. A-5 y M-2 (pintar el estado, `abs` del reloj, carpeta configurable desde la CLI y sin `makedirs` silencioso).
5. Los tests que faltan según la tabla (ensayo en `generar`, config tras un ensayo fallido, relectura en `--exportar`,
   refresco del puesto, conexión de solo lectura) y rehacer el test de contención del hilo.

## Preguntas abiertas

- ¿Se quiere que una misma carpeta de copias la compartan varios equipos (A-4), o basta con documentar «una carpeta por
  equipo»?
- ¿Debe la restauración poder parar la barra ella misma (`launchctl bootout`) o basta con indicar la orden?
- ¿La carpeta de copias por defecto dentro de `DATOS` es aceptable como valor inicial, o `--claves-generar` debe exigir una
  carpeta fuera del disco del sistema?

Status: DONE_WITH_CONCERNS
Resumen: la base criptográfica y el arreglo de B1 son sólidos y los protegen tests que mueren por mutación, pero en un equipo
nuevo o sobre una base dañada no se puede restaurar con la CLI (K1, A-1), y con la barra bajo launchd «ciérrala» no se puede
cumplir (A-2). La rotación y el hilo pueden perder copias de otro equipo, llenar el disco con `.danada` o dejar de copiar
durante un año sin que nada lo muestre (A-3, A-4, A-5), y siete protecciones del informe previo no tienen ningún test que
falle si se quitan.
