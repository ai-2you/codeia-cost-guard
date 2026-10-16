# Revisión de código y seguridad: PR #6 (bandeja de Windows), rama `integracion/pr-6-windows`

Fecha: 2026-10-16 (Europe/Madrid). Alcance: `git diff main...HEAD` sin `plans/261015-0128-windows-tray-y-credenciales/`
(20 ficheros, unas 990 líneas añadidas). Se ha leído además el código de pystray 0.19.5 y pywebview 6.2.1 (ruedas
descargadas de PyPI) para razonar lo que solo pasa en Windows.

## Resultado

No hay fallos críticos ni regresiones en macOS. La suite pasa entera en macOS: 1158 tests, OK, 1 saltado, unos 154 s.
La sesión del servidor local sigue cerrada y el `title` nuevo de `app.js` no abre XSS. Lo que más pesa está en Windows:
el popover se coloca mal con el escalado de pantalla que traen casi todos los portátiles, la tarea programada hereda
los valores por defecto de `schtasks` (no arranca con batería y muere a las 72 h) y no hay nada que impida dos
instancias, que en Windows llegan a compartir el puerto en silencio.

## Alto

### A1. El popover sale fuera de pantalla con escalado distinto del 100 %
`infobar/bandeja_windows_popover.py:61-83` y `:131-136`. pywebview llama a `SetProcessDPIAware()` al arrancar
(`webview/platforms/winforms.py:820`), así que `GetCursorPos` y `SPI_GETWORKAREA` devuelven píxeles físicos. Pero
`window.move(x, y)` espera píxeles lógicos y los multiplica por la escala (`winforms.py:620-640`); con `create_window(x=, y=)`
pasa lo mismo. Escenario: un portátil de 1920×1080 al 150 % con el cursor en la bandeja en (1850, 1060). `_posicion()`
da (1430, 312) y `move` lo lleva a (2145, 468) físicos, más allá del borde derecho. El usuario pulsa el icono y no ve
nada. Además, el recorte con `ANCHO`/`ALTO` mezcla unidades lógicas con físicas.
Arreglo: calcular la escala (`GetDpiForWindow(hwnd)/96`, o `GetDpiForSystem()/96` antes del primer show), pasar el
cursor y el área de trabajo a lógicos (`/ escala`) y recortar en lógicos. Hay que probarlo en un Windows al 125 % y al 150 %.

### A2. La tarea de `schtasks /sc onlogon` hereda los valores por defecto de Windows
`instalar_windows.py:128-134`. Una tarea creada con `schtasks /create` sin `/xml` trae `DisallowStartIfOnBatteries=true`,
`StopIfGoingOnBatteries=true` y `ExecutionTimeLimit=PT72H`. Escenario: en un portátil que inicia sesión con batería, la
bandeja no arranca. Si se desenchufa el cargador, la mata. Y con la sesión abierta más de 3 días, el Programador la
termina. En los tres casos no queda nada en el log, porque se mata el proceso.
Arreglo: registrar la tarea con `/xml` y esos tres ajustes desactivados (más `MultipleInstancesPolicy=IgnoreNew`).
Una opción más sencilla es usar como mecanismo principal `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, que no
pide administrador y no tiene esas condiciones, y dejar `schtasks` o el `.vbs` como alternativa.

### A3. Nada impide dos instancias, y en Windows el segundo servidor comparte el puerto sin error
`bandeja_windows.py:159-193` no toma ningún cerrojo de instancia única (en macOS lo da launchd). En
`infobar/servidor_local.py:492-494`, `allow_reuse_address = True` pone `SO_REUSEADDR`, y en Windows eso deja enlazar
un puerto que otro proceso ya escucha. El reintento cada 30 s de `servir()` nunca llega a saltar. Hay tres escenarios
concretos:
1. El primer `instalar_windows.py` falla con `schtasks` y crea el `.vbs`. Al reinstalar, `schtasks` sí funciona, pero
   el `.vbs` antiguo no se borra (`:131-138`). Desde entonces, cada inicio de sesión lanza dos bandejas.
2. Se reinstala con la bandeja en marcha (el instalador no la para, al contrario que `install.sh` con `launchctl bootout`)
   y se ejecuta el "Arrancar ya" que imprime (`:186`).
3. El usuario hace doble clic en el acceso directo.

Consecuencias: las conexiones al puerto se reparten entre los dos procesos. Un ticket emitido por A y canjeado en B da
"Ticket no válido", y la cookie de una sesión vale solo en uno de los dos, así que el panel falla a ratos. El
arrendamiento del almacén (`almacen.py`, latido) protege la ingesta. No protege `servidores.sqlite`, `avisos.json` ni
`avisos_servidores.json`, ni el ciclo SSH: hay dos "escritores únicos", avisos duplicados y el doble de conexiones SSH.
Arreglo: (a) un cerrojo de instancia única al principio de `main()`, ya sea un mutex con nombre
(`CreateMutexW("Local\\CostGuardTray")` y `GetLastError()==ERROR_ALREADY_EXISTS`, y entonces salir) o
`plataforma.bloqueo_de_fichero` no bloqueante sobre `logs/bandeja.lock`; (b) en Windows, sustituir `allow_reuse_address`
por `SO_EXCLUSIVEADDRUSE` (por ejemplo, `allow_reuse_address = os.name != "nt"` y un `server_bind` que ponga
`socket.SO_EXCLUSIVEADDRUSE`); (c) que el instalador borre el `.vbs` cuando `schtasks` funciona y viceversa, y que pare
la instancia anterior antes de copiar.

## Medio

### M1. Con Alt+F4 sobre el popover, el panel queda inaccesible hasta reiniciar
`bandeja_windows.py:179-190`. El popover toma el foco (`show()` llama a `Activate()`), así que Alt+F4 lo cierra. Al
cerrarse la única ventana, `webview.start()` vuelve con normalidad. El proceso sigue vivo porque el hilo de
`run_detached` no es daemon (`pystray/_win32.py:131`), pero `_usa_popover` sigue a `True`. A partir de ahí, cada clic
llama a `load_url` sobre una ventana destruida y solo deja errores en el log.
Arreglo: enganchar `window.events.closing` con un manejador que oculte la ventana y cancele el cierre salvo que se esté
saliendo (una bandera `_saliendo` que pone `_accion_salir`). Como alternativa, si `webview.start()` vuelve sin
`_saliendo`, pasar `_usa_popover=False` para caer al navegador.

### M2. El popover oculto sigue regenerando el panel cada minuto, sin fin
`panel_assets/ajustes.js:435` recarga la página cada 60 s, y cada `GET /` lanza `panel.py` en un subproceso
(`servidor_local.py:308-311`). En macOS la pestaña se cierra; aquí la ventana solo se oculta (`alternar`, `:164-170`).
Así que, mientras viva el proceso, se regenera el panel cada minuto aunque nadie lo mire, con su coste de CPU y disco.
Arreglo: al ocultar, `_ventana.load_url("about:blank")` (cada show ya carga una URL nueva con ticket). Otra opción es
recargar en `ajustes.js` solo si `document.visibilityState === "visible"`, aunque con una ventana WinForms oculta no
está garantizado que cambie.

### M3. Ventanas de consola que parpadean con cada ciclo SSH
La bandeja corre con `pythonw.exe`, sin consola. Cada hijo de consola que se lanza sin `CREATE_NO_WINDOW` abre su
propia ventana: `ssh` en `servidores_ssh.py:86` y `servidores_externo.py:97`, y `taskkill` en `plataforma.py:112`. Con
servidores configurados, aparece una ventana negra cada 5 min (cada minuto en modo incidencia). `grupo_nuevo_kwargs()`
(`plataforma.py:101-105`) solo pone `CREATE_NEW_PROCESS_GROUP`.
Arreglo: en Windows, `creationflags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` en `grupo_nuevo_kwargs()`, y un
`plataforma.sin_ventana_kwargs()` para los `subprocess.run` de `ssh -G` y `taskkill`.

### M4. El `.vbs` de respaldo se rompe con tildes en el nombre de usuario
`instalar_windows.py:159` escribe el `.vbs` en UTF-8 sin BOM. Windows Script Host lo lee en la página de códigos ANSI,
de modo que `C:\Users\José\...` se convierte en `C:\Users\JosÃ©\...`. `Run` falla sin decir nada en cada inicio de
sesión. Arreglo: `encoding="utf-16"` (WSH acepta UTF-16LE con BOM).

### M5. El cierre al clicar fuera oculta el popover con sus propios diálogos
`bandeja_windows_popover.py:86-98`. Cualquier ventana de primer nivel que no sea el popover lo oculta, y eso incluye
los diálogos que abre el propio WebView2: el `alert()` de `ajustes.js:329`, el diálogo de guardar de "Descargar CSV"
(`historico.js:82`) y los desplegables nativos. Escenario: el usuario pulsa "Descargar CSV", el popover desaparece y el
diálogo se queda solo. Arreglo: ignorar las ventanas que pertenecen al popover (`GetAncestor(hwnd, GA_ROOTOWNER) == _hwnd`)
o las del mismo proceso o del proceso `msedgewebview2` hijo (`GetWindowThreadProcessId`).

## Bajo

- **B1. Se lee el cuerpo antes de autenticar** (`servidor_local.py:382-390`, `:414`). No abre ninguna ruta: Host,
  Sec-Fetch-Site, sesión, Content-Type, Origin y tamaño se siguen comprobando antes de usar `crudo`. El coste es que
  cualquier petición sin sesión (también un `fetch` no-cors desde una web abierta) hace leer y guardar hasta 1 MB, y
  `timeout = 15` es por `recv`, no total: un cliente que manda un byte cada 14 s retiene un hilo durante mucho tiempo
  (`ThreadingTCPServer` no tiene tope de hilos). Antes también podía hacerse con las cabeceras, así que el agravante es
  pequeño. Arreglo: autenticar primero y, solo en las ramas que rechazan, descartar hasta un tope pequeño (por ejemplo
  64 KB) antes de responder, o `self.close_connection = True` y `shutdown(SHUT_WR)`.
- **B2. Ticket en argv y en el historial** (`bandeja_windows.py:115-130`). Ocurre en el respaldo con Edge `--app=` y
  también con `webbrowser.open` (ShellExecute lo pasa en la línea de órdenes). Además, la URL queda en el historial del
  perfil de Edge. El riesgo es aceptable porque el ticket es de un solo uso y caduca a los 60 s, como dice el docstring.
  El ticket no llega al log: `log_message` está silenciado y ningún mensaje de error lleva la URL. Detalles menores:
  `_accion_panel` pide un ticket también al ocultar (se desperdicia), y cada apertura del popover crea una sesión nueva
  que puede expulsar, al llegar a `MAX_SESIONES = 20`, la de una pestaña del navegador abierta a la vez.
- **B3. La zona de arrastre se pierde con la recarga de 60 s** (`bandeja_windows_popover.py:34-41`, `:179`). Se inyecta
  una sola vez por apertura. Arreglo: `webview.settings["DRAG_REGION_SELECTOR"] = ".cab h1, #sub"` antes de
  `webview.start()`, que pywebview aplica en cada carga, y quitar el `evaluate_js`.
- **B4. `FindWindowW(None, "CostGuard")` busca por título** (`:177-178`). Con dos instancias (A3) puede coger la ventana
  de la otra. Arreglo: `_ventana.native.Handle.ToInt64()`.
- **B5. El menú se reconstruye desde el hilo trabajador** (`bandeja_windows.py:67-72`). `Icon.menu = ...` llama a
  `_update_menu`, que hace `DestroyMenu` del menú en curso (`pystray/_win32.py:99-111`). Si el usuario tiene el menú
  abierto justo cuando llega la entrega del minuto, se destruye el menú que se está mostrando. Arreglo: reconstruir solo
  si cambió algún texto.
- **B6. Diferencias del instalador con `install.sh`** (`instalar_windows.py`): no arranca la bandeja al terminar, no para
  la instancia anterior y no acepta `--reglas`. Con `COSTGUARD_HOME` fuera del perfil, no restringe las ACL (el
  equivalente de `chmod 700`). `README.md` dice que el venv lleva "pillow + pystray==0.19.5" y faltan pywebview,
  pythonnet y cryptography.
- **B7. Documentación desfasada tras la integración**: `CONTRIBUTING.md` y el comentario de
  `bandeja_windows_popover.py:52` citan `infobar/credenciales_windows.py`, que no existe porque `claves.py` de main lleva
  el Administrador de credenciales.
- **B8. Dependencias transitivas sin fijar**: `pillow` va sin fijar, igual que `bottle`, `proxy_tools` y
  `typing_extensions` (de pywebview), `clr_loader` (de pythonnet) y `six` (de pystray). Encaja con el pendiente de un
  lock con hashes que ya anota `requirements.txt`. Los marcadores `sys_platform` son correctos y arreglan, de paso,
  `pip install -r` en Linux, donde antes fallaba pyobjc.
- **B9.** `_vista()` en `app.js:20` acepta cualquier valor de `localStorage`. No es explotable: solo se compara con
  literales y no se inserta en el HTML. Con un valor raro, simplemente ninguna pestaña sale marcada.

## Lo comprobado que está bien

- **Sesión del servidor**: `do_POST` sigue comprobando `_host_ok()` y `_sesion_ok()` antes de enrutar, y `_cuerpo_json`
  mantiene Content-Type, Origin exacto y `0 < n <= MAX_CUERPO`. Un Content-Length negativo o no numérico no lee nada.
  Si la lectura agota el tiempo, el `TimeoutError` lo recoge `handle_one_request`.
- **XSS en el `title` de modelos** (`app.js:177`): `D = escapar(__DATOS__)` y `esc()` escapan `" ' \` < > &`, así que
  el atributo no se puede romper. La columna eliminada tenía la misma garantía.
- **`/tr` de schtasks y `.vbs`**: las rutas de Windows no pueden contener `"`, y `DESTINO` sale del propio usuario
  (`COSTGUARD_HOME` o el perfil), así que no hay inyección. La lista de argumentos a `subprocess` evita el intérprete de
  órdenes. La ruta del navegador sale de `App Paths` en HKLM, que el usuario no puede escribir, y se valida con `isfile`.
- **Hilos**: `_entregar` atrapa sus excepciones y no tumba al trabajador, y `_pinta` va bajo cerrojo. El trabajador
  entrega ahora `ritmo`, `limite_5h` y `limite_hora` con valores por defecto (`or 0`, `or {}`). `bandeja_windows_menu`
  tolera un resumen sin esas claves (lo cubre un test). El callback de WinEvent tiene una referencia viva y los tipos de
  retorno HWND son de 64 bits. Al salir con el popover, `destroy()` termina `webview.start()`, y el resto de hilos son
  daemon o paran con `parar`.
- **macOS sin cambios de comportamiento**: `barra.py` solo lee `cinco_h`, `hoy`, `turnos` y `pct_5h`, y las claves
  nuevas le son indiferentes. `demonio()` arranca los mismos hilos a través de `arrancar_hilos()` (sigue cubierto por
  `TestModoSinInterfaz`). `_ZONA_PRUEBA=None` deja `astimezone()` igual que antes, y `_posix()` no cambia nada en rutas
  POSIX.
- **Separadores mezclados**: la normalización en `lectores_logs.parsear`, `parsear_pi`, `leer_bases` (incluido el perfil
  de Hermes) y `sombra.agente_de` cubre lo que devuelve `glob` en Windows, con tests.
- **Dependencias**: pystray 0.19.5, pywebview 6.2.1 y pythonnet 3.2.1 existen en PyPI. pywebview no levanta su servidor
  HTTP interno con una URL `http://` y usa `private_mode` por defecto, así que no persisten cookies ni historial del
  popover.
- **Tests**: 1158 en macOS, OK. Los tests nuevos de zona horaria con zoneinfo, rutas mezcladas y menú e icono son
  deterministas.

## Acciones recomendadas, por orden

1. A1 (escala DPI) y M1 (Alt+F4): sin ellos, el popover no es fiable en Windows.
2. A3: mutex de instancia única, `SO_EXCLUSIVEADDRUSE` en Windows y limpieza cruzada `.vbs`/tarea en el instalador.
3. A2: tarea con `/xml` sin condiciones de batería ni límite de 72 h, o la clave `Run` de HKCU.
4. M2, M3, M4 y M5: son pequeños y de bajo riesgo.
5. B1 a B9, cuando se toque cada zona.

## Preguntas abiertas

- ¿Hay acceso a una máquina Windows para validar A1, M1 y M5? Son razonamientos sobre el código de pywebview y pystray,
  no observaciones.
- ¿Se prefiere la clave `Run` de HKCU frente a `schtasks` como mecanismo principal (A2)?

Status: DONE_WITH_CONCERNS
Summary: Ni críticos ni regresiones en macOS: la sesión del servidor y el escape del panel siguen intactos y los 1158 tests pasan. Quedan tres fallos altos solo de Windows (popover fuera de pantalla con escalado distinto del 100 %, valores por defecto de schtasks que impiden o cortan el arranque, y sin instancia única con el puerto compartido por SO_REUSEADDR) y cinco medios por corregir antes de fusionar.

---

## Segunda pasada (commit ffdbbe3)

Solo se revisa ese commit, contrastándolo con el código de pywebview 6.2.1 y pystray 0.19.5. Los tests focalizados
pasan en macOS (`test_portabilidad`, `test_bandeja_windows`, `test_config_y_servidor_local`: 74, OK). A1, A2, A3, M3,
M5, B3, B4 y B5 están bien resueltos. M2 está bien planteado, pero su arreglo abre un fallo nuevo (S1).

### S1. Crítico: `about:blank` arranca el servidor HTTP propio de pywebview sirviendo el directorio de trabajo
`infobar/bandeja_windows_popover.py:156` (`url="about:blank"`) y `:107` (`load_url("about:blank")`). pywebview considera
"local" cualquier URL que no empiece por `http://`, `https://` o `file://` (`webview/util.py:75-84`), y `about:blank`
cae ahí. Por eso `webview.start()` levanta su servidor bottle global (`webview/__init__.py:272-279`). La raíz sale de
`os.path.commonpath(["about:blank"])`, que da `"about:blank"`; como no es un directorio, se toma `dirname`, que es `""`,
y `abspath("")` es el **directorio de trabajo del proceso** (`webview/http.py:97-101`). Ese servidor escucha en
127.0.0.1, en un puerto aleatorio, sin sesión ni comprobación de Host, y sirve con `static_file` cualquier fichero bajo
esa raíz.
Escenario: el usuario ejecuta en PowerShell, desde `C:\Users\<usuario>`, el "Arrancar" que imprime el instalador.
Entonces `~/.ssh/`, `~/.cost-guard/servidores.json` y los logs de los agentes quedan legibles por cualquier proceso
local y, mediante DNS rebinding, por una web que encuentre el puerto. Con la clave Run, el directorio lo decide quien
lanza el proceso; no lo he podido comprobar sin Windows. Efecto secundario: el popover oculto no queda en blanco, sino
en la página 404 de bottle (`http://127.0.0.1:<puerto>/about:blank`).
Arreglo: no pasar ninguna URL "local". Crear con `html="<!doctype html><title></title>"` (con `url=None`,
`is_local_url` da False) y ocultar con `_ventana.load_html("<!doctype html>")`, que usa NavigateToString y no levanta
servidor. Como defensa añadida, `os.chdir(rutas.RAIZ)` al principio de `main()`. Un test barato fijaría el
comportamiento: comprobar que ninguna URL que pase el popover a pywebview cumple `webview.util.is_local_url`.

### S2. Alto: cancelar el cierre bloquea el cierre de sesión y el apagado de Windows
`bandeja_windows_popover.py:165-177`. WinForms lanza `FormClosing` también con `WM_QUERYENDSESSION`
(`CloseReason.WindowsShutDown`), y la ventana oculta lo recibe como cualquier otra de primer nivel. `_al_cerrar`
devuelve `False` siempre que no se esté saliendo, y pywebview pone `args.Cancel = True` (`winforms.py:400-403`).
Escenario: en cada cierre de sesión o apagado, Windows muestra "CostGuard impide cerrar sesión" hasta que el usuario lo
fuerza. Lo mismo pasa con un `taskkill` sin `/F`.
Arreglo: cancelar solo cuando `CloseReason == UserClosing`. El manejador de pywebview no recibe `args`, así que hay que
añadir uno propio de .NET después de crear la ventana: `_ventana.native.FormClosing += lambda s, e: ...`, que, si
`e.CloseReason != CloseReason.UserClosing`, pone `e.Cancel = False` y `_saliendo = True` (corre detrás del de pywebview,
así que gana). Otra opción es engancharse a `Microsoft.Win32.SystemEvents.SessionEnding` para poner `_saliendo = True`.

### S3. Bajo: tras Alt+F4 el panel sigue cargado y recargándose
`_al_cerrar` oculta, pero no deja la página en blanco, así que M2 vuelve por esa vía hasta la siguiente apertura.
Arreglo: llamar también a `load_html(...)` desde ahí. Se puede hacer en el hilo de la interfaz porque `Invoke` sobre el
propio hilo es síncrono y `shown` ya está puesto.

### S4. Bajo: `GetLastError` sin `use_last_error`
`bandeja_windows.py:168-176` lee `kernel32.GetLastError()` en una llamada aparte de ctypes. Funciona casi siempre, pero
el patrón fiable es `ctypes.WinDLL("kernel32", use_last_error=True)` con `ctypes.get_last_error()`. Además, cambiar
`argtypes` en el `ctypes.windll.kernel32` compartido afecta a cualquier otro módulo que use `CreateMutexW`. Dos detalles
menores: el mutex no cubre `costbar.py --demonio` a la vez que la bandeja (el puerto exclusivo sí lo detecta, pero no
los demás escritores), y la segunda bandeja se va sin decir nada (podría abrir el panel de la primera).

### S5. Bajo: el instalador con una bandeja en marcha
Primero instala los paquetes en el venv y solo al final avisa de que hay que salir de la versión anterior. Con la
bandeja viva, `Python.Runtime.dll` de pythonnet y los `.pyd` están bloqueados: una subida de versión hace fallar
`pip` con WinError 5 y `sys.exit` corta antes de registrar el arranque. Con las versiones fijadas de ahora es un no-op,
así que hoy no pasa. Arreglo: avisar antes de empezar, o pararla.

### S6. Bajo: arranque rápido tras salir con `SO_EXCLUSIVEADDRUSE`
Con uso exclusivo, Windows puede negar el `bind` mientras queden conexiones del socket anterior en TIME_WAIT. El
servidor cierra primero en HTTP/1.0. Si se sale y se vuelve a abrir enseguida, el panel puede tardar uno o dos
reintentos de 30 s en estar disponible, y mientras tanto `url_panel()` devuelve la URL sin ticket. Lo cubre el
reintento de `servir()`, y es preferible a compartir el puerto. Solo es para tenerlo en cuenta.

### Comprobado en esta pasada
- **No hay bloqueos entre hilos.** `_al_cerrar` corre en el hilo de WinForms (el evento `closing` es síncrono,
  `Event(self, True)`) y no toma `_lock`. `alternar` y el hook toman `_lock` y hacen `Invoke` al hilo de la interfaz,
  que nunca espera a `_lock`. `destroy` desde `cerrar` pasa por `_al_cerrar` con `_saliendo=True` y no se cancela.
- **El orden de `alternar` es válido.** `load_url` antes de `show()` funciona porque `hidden=True` hace `Show()`/`Hide()`
  al crear la ventana y deja puesto `shown` (`winforms.py:777-781`, `:367-369`).
- **La escala es coherente.** En `crear()`, que corre antes de `SetProcessDPIAware`, el proceso no es consciente de
  DPI: `GetDpiForSystem` da 96 y `SPI_GETWORKAREA` ya da valores lógicos. Después de `webview.start()`, los valores
  físicos se dividen por la escala del sistema, la misma que usa `_scale` de pywebview en modo consciente de sistema.
  Con varios monitores a escalas distintas puede descuadrar un poco, pero se acepta.
- **El resto de arreglos**: `_es_del_popover` con `GA_ROOTOWNER` y `native.Handle.ToInt64()` son correctos. El menú
  solo se rehace si cambia el texto. `CREATE_NO_WINDOW` se combina bien con `CREATE_NEW_PROCESS_GROUP`. La clave Run va
  con las dos rutas entre comillas y sin longitud problemática. `quitar_arranques_antiguos` borra la tarea y el `.vbs`.
  `arrancar` usa `DETACHED_PROCESS`, y un `pythonw.exe` de venv que actúe de lanzador no rompe el mutex, que vive en el
  proceso real.

Status: DONE_WITH_CONCERNS
Summary: ffdbbe3 resuelve bien A1, A2, A3, M1, M3, M5, B3, B4 y B5, pero el arreglo de M2 introduce un fallo crítico: `about:blank` hace que pywebview levante un servidor HTTP local sin sesión que sirve el directorio de trabajo. Además, cancelar el cierre bloquea el cierre de sesión y el apagado de Windows; hay que corregir las dos cosas antes de fusionar.

---

## Tercera pasada (commit 676b0df)

Solo se revisa ese commit, contrastándolo con el código de pywebview 6.2.1. Los tests `test_bandeja_windows` y
`test_portabilidad` pasan en macOS. S1, S2 (en su caso normal), S3, S4 y S5 están bien resueltos. Ninguno de los
hallazgos de esta pasada es bloqueante.

### Las cuatro preguntas
1. **Orden de los manejadores de `FormClosing`.** El de pywebview (`on_closing`) se suscribe en el constructor del
   formulario (`winforms.py:304`), así que corre antes que el nuestro. Como `events.closing` ya no tiene manejadores,
   `closing.set()` devuelve False y pywebview no toca `Cancel` (`confirm_close` está desactivado). El nuestro corre
   después y es el único que decide. `closing.set()` deja marcado `events.closing` aunque luego se cancele, pero nada
   en pywebview consulta `closing.is_set()`. **Correcto.**
2. **`str(args.CloseReason)` con pythonnet 3.x.** Desde la 3.0 los enum de .NET llegan como objetos CLR, no como
   enteros, y `str()` llama a `ToString()`, así que da `"UserClosing"`. No he podido ejecutarlo. Si en algún caso
   devolviera otra cosa (por ejemplo `"3"`), todo cierre se trataría como «no es del usuario»: Alt+F4 cerraría la
   ventana con `_saliendo=True` y se volvería al fallo M1. Recomendación (baja): comparar contra el enum para no
   depender del texto: `from System.Windows.Forms import CloseReason` y `args.CloseReason == CloseReason.UserClosing`.
   Si se mantiene `str()`, conviene un registro en el log la primera vez que salte la rama «no es del usuario».
3. **`events.shown` con `hidden=True`.** Sí se dispara: con `hidden=True`, `create()` hace `Show()` y `Hide()` con
   opacidad 0 (`winforms.py:777-781`), y `Form.Shown` (una sola vez en la vida del formulario) llega a `on_shown`, que
   hace `shown.set()`. Pero `events.shown` se crea sin cerrojo (`Event(self)`, `window.py:168`), así que
   `_engancha_cierre` corre en un **hilo nuevo**, no en el de la interfaz. Que `.native` exista entonces está
   garantizado: se asigna en el constructor (`winforms.py:195`). El `+=` sobre un evento de .NET desde otro hilo
   funciona en la práctica, pero `EventHandlerList` no es seguro entre hilos y la suscripción ocurre en un momento sin
   fijar. Recomendación (baja): engancharlo en `events.before_show`, que es síncrono (`Event(self, True)`), corre en el
   hilo de la interfaz y se dispara en `create()` con `.native` ya asignado (`winforms.py:775`).
4. **`load_html` dentro de `FormClosing`.** No bloquea. `Window.load_html` espera a `shown`, que ya está puesto, limpia
   eventos y llama a `gui.load_html`. Esta hace `self.Invoke(...)` desde el propio hilo de la interfaz, lo que en
   WinForms se ejecuta en línea, y `NavigateToString` vuelve enseguida (`edgechromium.py:209-216`). `hide()` sigue el
   mismo camino. **Correcto.**

### T1. Bajo: si otra aplicación cancela el cierre de sesión, el popover queda muerto
El cierre de sesión llega como `WindowsShutDown`, se deja pasar y se marca `_saliendo=True`. Si el usuario cancela
después el cierre de sesión porque otra aplicación lo impedía, el formulario ya se ha cerrado y `webview.start()` ha
vuelto. Como `saliendo()` da True, `main()` cree que fue Salir, no cae al navegador y vuelve. El icono sigue vivo (el
hilo de pystray no es daemon), pero «Abrir panel» ya no funciona porque `_usa_popover` sigue a True.
Arreglo: separar «salir pedido por el usuario» (solo `cerrar()`) de «la ventana se cerró por el sistema». Si
`webview.start()` vuelve sin que se pidiera Salir, `_usa_popover=False` (respaldo al navegador). Como alternativa,
hacer en ese caso el mismo apagado que `_accion_salir` (`icon.stop()` y parar los hilos) para que el proceso termine
limpio.

### Comprobado
- **S1**: `html=` no levanta el servidor, porque con `url=None` `is_local_url` da False y `start()` no lo arranca.
  `load_html` usa `NavigateToString`. La comprobación de `http://127.0.0.1:` antes de `load_url` y el test por AST fijan
  el comportamiento, y `os.chdir(costbar.AQUI)` (`rutas.RAIZ`) añade defensa.
- **S4**: un `WinDLL` propio con `use_last_error` y `ctypes.get_last_error()` es el patrón correcto, y el handle se
  conserva en `_mutex`.
- **S5**: el aviso del instalador sale antes de copiar e instalar.
- **Salir**: `cerrar()` pone `_saliendo=True` antes de `destroy()`. `Close()` llega como `UserClosing`, pero la
  comprobación de `_saliendo` va primero y el cierre no se cancela.

Status: DONE_WITH_CONCERNS
Summary: 676b0df resuelve S1 a S5. El orden de los FormClosing y load_html en el hilo de la interfaz son correctos. Quedan tres puntos bajos: comparar CloseReason contra el enum en vez de str(), enganchar el cierre en before_show (el manejador de shown corre en otro hilo) y que si otra aplicación cancela el cierre de sesión el popover queda sin respaldo al navegador.
