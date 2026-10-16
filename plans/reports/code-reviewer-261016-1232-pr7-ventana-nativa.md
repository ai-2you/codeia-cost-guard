# Revisión de la PR #7 integrada: ventana del panel con marco nativo (rama `integracion/pr-7-ventana`, f49df50)

Fecha: 2026-10-16 (Europe/Madrid). Alcance: `git diff main...HEAD` con main = 4c614bf (0.5.0): `bandeja_windows.py`,
`infobar/bandeja_windows_popover.py` y `README.md`. Se ha contrastado con el código de pywebview 6.2.1
(`webview/platforms/winforms.py`, `webview/event.py`, `webview/window.py`). Los tests `test_bandeja_windows` y
`test_portabilidad` pasan en macOS. No se ha podido probar en Windows.

## Resultado

No hay fallos críticos ni altos. Se conserva todo lo de seguridad y ciclo de vida de la 0.5.0. La decisión de no
adoptar `events.closing` devolviendo False es correcta (S2). Quedan dos fallos medios de uso que tienen que ver con
minimizar, y un medio de diseño: qué hacer al pulsar el icono cuando la ventana está tapada.

## Medio

### P1. `_minimizada` puede desincronizarse y el clic deja de hacer nada
`infobar/bandeja_windows_popover.py:92-104` y `:154`. `minimized`, `restored` y `maximized` son `Event(self)` sin
cerrojo (`webview/window.py:168-171`). Cada `set()` lanza un hilo nuevo para el manejador (`webview/event.py`, la rama
`threading.Thread(target=execute)`), así que no hay orden garantizado entre dos eventos seguidos. Lo dispara
`on_resize` (`winforms.py:417-430`), que ve el estado real en el hilo de la interfaz.
Escenario: un minimizar y restaurar rápidos (Win+D dos veces, o minimizar y volver a pulsar enseguida el botón de la
barra de tareas). Si el hilo de `_al_restaurar` termina antes que el de `_al_minimizar`, queda `_minimizada=True` con
la ventana normal. A partir de ahí, cada clic en el icono entra en la rama de restaurar (`:154`), que no hace nada
porque `WindowState` ya es `Normal`. El icono parece muerto hasta que el usuario vuelve a minimizar y restaurar.
Arreglo: no llevar el estado aparte, sino preguntarlo al hacer clic. Por ejemplo,
`ctypes.windll.user32.IsIconic(_ventana.native.Handle.ToInt64())`, que es seguro desde cualquier hilo, o leer
`_ventana.native.WindowState`. Con eso sobran `_minimizada` y los tres manejadores.

### P2. Cerrada desde la barra de tareas estando minimizada, el siguiente clic no la enseña
`:121-137` y `:166-174`. Con marco nativo, la ventana tiene botón en la barra de tareas. Clic derecho en ese botón
y luego «Cerrar ventana» llega como `UserClosing`: se cancela, se oculta y queda `_visible=False`. Pero `WindowState`
sigue en `Minimized`. En el siguiente clic en el icono, `show()` vuelve a mostrar el formulario minimizado (WinForms
conserva el estado), `move()` lo coloca, y en pantalla no aparece nada; solo un botón en la barra de tareas.
Arreglo: al mostrar, restaurar si está minimizada (`if IsIconic(...): _ventana.restore()` después de `show()`), o
poner `WindowState = Normal` al ocultar.

### P3. Si la ventana está visible pero tapada, el clic la oculta en lugar de traerla al frente
`:160-165`. Sin el gancho de foco, «visible» ya no significa «a la vista». Escenario: el panel está abierto detrás
de un navegador a pantalla completa. El usuario pulsa el icono para verlo, el panel se oculta sin que se note, y hace
falta un segundo clic. Con una ventana con marco, lo habitual en Windows (Slack, Discord, Signal) es que el icono
siempre muestre y traiga al frente, y que solo la X oculte. Hay dos opciones:
- **Recomendada, la más simple:** con la ventana visible, el clic restaura si está minimizada y hace `show()`, que ya
  llama a `Activate()` (`winforms.py:494-497`). Nunca oculta. Para ocultar quedan la X y, si se quiere, una entrada
  «Ocultar» en el menú.
- **Mantener el conmutador:** ocultar solo si la ventana estaba activa justo antes del clic. No sirve consultar
  `GetForegroundWindow()` en ese momento, porque al pulsar la bandeja el primer plano ya es la barra de tareas. Hay
  que guardar la hora del `Deactivate` nativo (enganchado en `_engancha_cierre`, en el hilo de la interfaz) y ocultar
  si fue hace menos de unos 500 ms, o traerla al frente en caso contrario.

Llevar la ventana al frente funciona sin `on_top`: al pulsar un icono de la bandeja, Explorer concede permiso de primer
plano al proceso del icono (por eso pystray puede hacer `SetForegroundWindow` para su menú).

## Bajo

- **B1. Colocación con el tamaño fijo cuando la ventana es redimensionable** (`:63-69`). `_posicion()` recorta con
  `ANCHO`/`ALTO` (420×720). Si el usuario la agranda a, por ejemplo, 1000×900 y la oculta, al volver a abrirla se
  coloca en `cursor - 420` y se sale por la derecha y por abajo. Arreglo: recortar con el tamaño actual
  (`_ventana.width`/`_ventana.height`, ya en lógicos y sin espera porque `shown` está puesto). También cabe la duda de
  si conviene recolocarla junto al cursor en cada apertura o respetar dónde la dejó el usuario; con marco nativo es lo
  esperable (decisión de producto).
- **B2. Maximizada** (`:154-157`). `restore()` pone `WindowState = Normal` (`winforms.py:657-661`), así que una ventana
  que estaba maximizada y luego se minimizó vuelve sin maximizar. Además, al mostrar una ventana que se ocultó
  maximizada, `move()` hace `SetWindowPos` sobre una ventana maximizada y la descoloca. Arreglo: no llamar a `move()`
  si `WindowState == Maximized`. Para restaurar sin perder el maximizado, `ShowWindow(hwnd, SW_RESTORE)`, que vuelve
  al estado anterior.
- **B3. Textos que quedan de antes:** «popover nativo» sigue en `bandeja_windows.py:44` y `:124` y en
  `instalar_windows.py:5` y `:99`. El nombre `bandeja_windows_popover.py` también se queda corto, aunque cambiarlo
  ahora mueve imports y tests (opcional).

## ¿Se ha perdido algo de la 0.5.0?

No. Siguen en su sitio:
- el mutex de instancia única con `use_last_error` y `os.chdir(costbar.AQUI)` (`bandeja_windows.py:167-183`);
- la página en blanco como contenido (`html=_EN_BLANCO`, `load_html`) y la comprobación de `http://127.0.0.1:` antes de
  `load_url`, así que no se levanta el servidor de pywebview;
- `FormClosing` nativo enganchado en `before_show` (hilo de la interfaz) y comparado con el enum `CloseReason`, con
  `str()` como respaldo; el cierre de sesión y el apagado se dejan pasar;
- `salida_pedida()`: si el sistema cierra la ventana sin Salir, se cae al navegador;
- la escala DPI en la posición, y el ticket que solo se pide al mostrar.

Lo que se ha quitado (gancho de WinEvent, `GA_ROOTOWNER`, `DRAG_REGION_SELECTOR`, `_hwnd`, `on_top`, `frameless`) ya no
hace falta con marco nativo: la barra de título arrastra, y los diálogos del WebView2 ya no ocultan nada. Con eso
desaparecen además M5 y B3/B4 de la revisión anterior. `threading` y `wintypes` se siguen usando y no hay referencias
huérfanas en el código ni en los tests.

## Las preguntas concretas

- **¿`events.minimized` y `restored` desincronizan `_minimizada`?** Sí, puede pasar (P1), porque cada evento corre en
  su propio hilo. Hay que preguntar el estado real en el momento del clic.
- **¿Ventana visible pero tapada?** Hoy el clic la oculta. Es mejor traerla al frente (P3).
- **¿`move()`/`show()` sin `on_top`?** Sí funcionan. `show()` hace `Show()` y `Activate()`, y `move()` usa
  `SetWindowPos` con píxeles lógicos convertidos (`winforms.py:620-640`), con o sin `on_top`. Las excepciones son las
  de B1 y B2 (tamaño cambiado y ventana maximizada).

## Recomendado, por orden

1. P1 y P2 juntos: preguntar `IsIconic` o `WindowState` al hacer clic y restaurar después de `show()` si hace falta.
2. P3: el icono siempre muestra y trae al frente.
3. B1 y B2, y los textos de B3.

Status: DONE_WITH_CONCERNS
Summary: La integración de la PR #7 conserva toda la seguridad y el ciclo de vida de la 0.5.0 y no tiene fallos críticos ni altos. Quedan tres puntos medios: `_minimizada` se desincroniza porque los eventos corren en hilos aparte, la ventana cerrada estando minimizada no reaparece, y con la ventana tapada el clic la oculta en vez de traerla al frente. Más tres bajos (colocación con la ventana redimensionada o maximizada, y textos).

---

## Pasada final (commit 98fa4ce)

Solo se revisa ese commit, contrastándolo con el código de pywebview 6.2.1. Los tests `test_bandeja_windows` y
`test_portabilidad` pasan en macOS. No quedan referencias a `alternar`, `_ocultar` ni a «popover nativo». P1, P2, P3,
B1, B2 y B3 están bien resueltos. **Sin hallazgos que corregir.**

### Las tres preguntas
1. **`_ventana.native.Handle` desde el hilo de pystray: seguro.** El handle se crea en el constructor del formulario,
   que ya lee `self.Handle` (`winforms.py:203`), así que desde otro hilo el getter solo devuelve el valor guardado. La
   comprobación de llamadas entre hilos de WinForms (`CheckForIllegalCrossThreadCalls`) solo está activa con un
   depurador conectado, y pywebview hace lo mismo en `move()`. Mejora opcional: guardar el HWND una vez en
   `_engancha_cierre`, que corre en el hilo de la interfaz, y no volver a leerlo en cada clic.
2. **`ShowWindow(SW_RESTORE)` entre hilos: deja WinForms coherente.** Sobre una ventana de otro hilo, `ShowWindow` es
   síncrono: envía los mensajes al hilo de la interfaz y espera. WinForms actualiza `WindowState` con los
   `WM_SIZE`/`WM_WINDOWPOSCHANGED` en su propio `WndProc`, y `on_resize` dispara `restored`/`maximized` como con un
   clic en la barra de tareas. No hay interbloqueo, porque el hilo de la interfaz nunca espera a `_lock`. Que sea
   síncrono hace además que `IsZoomed()` vea ya el estado final (si estaba maximizada, se salta `move()`), así que es
   mejor que `ShowWindowAsync`. Lo único que queda es que, si el hilo de la interfaz estuviera colgado, el clic esperaría
   con él; lo mismo pasa ya con `show()`, que usa `Invoke`.
3. **`show()` de pywebview sobre una ventana minimizada: nada raro.** `show()` hace `Show()`, que no cambia nada si ya
   está visible y, si estaba oculta, la vuelve visible pero minimizada, y después `Activate()`, que activa sin
   restaurar. Luego `SW_RESTORE` la restaura y la activa, con el maximizado previo incluido. `move()` usa
   `_ventana.width/height`, que pywebview devuelve en píxeles lógicos (`get_size` divide entre la escala), coherentes
   con `_posicion()`.

### Comprobado además
- `mostrar()` nunca oculta. Solo pide ticket y carga el panel cuando estaba oculta, y conserva la comprobación de
  `http://127.0.0.1:`.
- Si ya estaba visible, la trae al frente sin moverla, así que respeta dónde la dejó el usuario.
- `_al_cerrar` sigue dejando la página en blanco y cancela solo el cierre del usuario.

Status: DONE
Summary: 98fa4ce resuelve P1, P2, P3, B1, B2 y B3 sin introducir fallos. Leer Handle desde el hilo de pystray es seguro, ShowWindow(SW_RESTORE) síncrono deja WinForms coherente y show() sobre una ventana minimizada no hace nada raro antes de restaurar.
