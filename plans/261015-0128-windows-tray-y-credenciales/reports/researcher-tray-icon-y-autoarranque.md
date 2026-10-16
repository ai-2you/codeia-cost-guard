# Research: icono de bandeja (tray) y autoarranque en Windows

Contexto confirmado en `requirements.txt`: `pillow>=10.0` y `pyobjc-framework-Cocoa>=9.2` estan marcadas como "solo para la barra de menus en macOS"; `rumps` esta comentado (opcional). `install.sh` hoy solo instala LaunchAgent en Darwin; en otros SO deja "sin barra de menus".

---

## A. Libreria de system tray icon

### A.1 `pystray` — candidato principal

- **Ultima version PyPI: `0.19.5`**, publicada **2023-09-17**. No hay release nueva en PyPI desde entonces (confirmado via `pypi.org/pypi/pystray/json`).
- **Actividad de mantenimiento real (GitHub, no PyPI):** el repo `moses-palmer/pystray` SI tiene commits posteriores a la ultima release — el mas reciente es **2025-03-27** ("Updated copyright", "Propagate all exceptions from the mainloop", limpieza, eliminar soporte Python 2). O sea: mantenido mas alla de lo que sugiere PyPI, pero sin cortar una release nueva en >2 anos — riesgo de adopcion bajo-medio, no abandonware pero ritmo lento.
- **Repo:** 569 stars, 71 forks, 27 issues abiertos (snapshot actual). Un solo mantenedor (Moses Palmer), sin bus factor.
- **Dependencias declaradas (`setup.py` del repo, confirmado leyendo fuente):**
  - Runtime: `Pillow` (siempre).
  - Extra solo si `sys_platform == "darwin"`: `pyobjc-framework-Quartz>=7.0`.
  - Extra solo si `sys_platform == "linux"`: `python-xlib>=0.17`.
  - **Windows: NINGUNA dependencia extra.** `pip install pystray` en Windows solo trae Pillow.
- **Backend Windows confirmado leyendo el codigo fuente (`lib/pystray/_win32.py` y `lib/pystray/_util/win32.py`):** usa **`ctypes` + `ctypes.wintypes` puros**, llamando directamente a la API Win32 (Shell_NotifyIcon, mensajes de ventana oculta). **NO usa `pywin32`/`win32api` en absoluto.** Esto es relevante para el Dependency Guard: cero dependencias nativas/compiladas extra en Windows, solo Pillow (ya opcional en el repo) + stdlib.
- **Pillow:** confirmado que `pystray.Icon` recibe directamente una `PIL.Image.Image` como icono, y tiene una property `icon` con setter (`icon.icon = nueva_imagen`) que dispara `_update_icon()` internamente — soporta refresco dinamico en caliente sin recrear el Icon. Encaja con la dependencia opcional Pillow ya existente en el repo.
- **API basica (confirmada en docs + fuente):**
  - `Icon(name, image, title=None, menu=None)`, `Menu(MenuItem('Texto', callback), ...)`, `MenuItem` admite texto plano y `checked=`.
  - Actualizar icono: asignar `icon.icon = nueva_imagen_PIL` (setter en `_base.py`, confirmado).
  - Ejecutar sin bloquear: `icon.run(setup=...)` es bloqueante y su docstring dice "debe llamarse desde el hilo principal" (esto es conservador, pensado sobre todo por la restriccion real de macOS/AppKit). **En la practica, en Windows es un patron comun lanzar `icon.run()` dentro de un `threading.Thread` secundario** porque el backend win32 es solo un bucle de mensajes ctypes, no AppKit (visto en issue #94 del repo: usuarios lo hacen asi habitualmente al combinarlo con tkinter). Alternativa oficial: `run_detached()`, pensada para integrarse con el mainloop de otra libreria.
  - `icon.visible = True/False` existe y es necesario tras crear el Icon si no se usa el `setup` por defecto.
- **Limitaciones/issues conocidos:**
  - Sin submenus anidados nativos mas alla de lo que expone `Menu`/`MenuItem` (anidar MenuItems con su propio Menu funciona, pero no hay API de "dynamic menu" reactivo sin reconstruir el Menu).
  - Issue #94 (cerrado): parar el icono (`stop()`) desde el hilo que no es el que lo creo puede no terminar limpio el hilo si no se gestiona con cuidado — hay que seguir el patron recomendado (clase propia heredando `threading.Thread`, ver issue).
  - No se encontraron reportes de crashes graves ni memory leaks especificos de Windows 11 en la busqueda realizada (puede deberse a la baja actividad de issues nuevos, no necesariamente a ausencia de problemas).

### A.2 Alternativas comparadas

| Libreria | Ultima release | Mantenimiento | SO soportados | Dependencias | Veredicto |
|---|---|---|---|---|---|
| **pystray** | 0.19.5 (2023-09-17); commits hasta 2025-03-27 | Lento pero vivo, 1 mantenedor | Win/macOS/Linux real | Pillow (+ extras no-Windows) | **Ganador** |
| `infi.systray` | `0.1.12.1` (2025-01-11 en PyPI) | **Sospechoso**: el commit real mas reciente en GitHub (rama `master`/`develop`) es de **2019-12-23**; no hay tag/release en GitHub que corresponda a la subida de 2025 en PyPI. Repo "pushed_at" 2023-10-22 pero sin commits nuevos visibles. Señal de alarma de mantenimiento, no de confianza para adoptar hoy. | **Solo Windows** (no sirve para abstraer multiplataforma si algun dia se quisiera compartir codigo) | `setuptools` | Descartado: solo-Windows + historial de release inconsistente |
| `plyer` | 2.1.0 (2022-11-12) | Activo como proyecto pero **no implementa systray en absoluto** (es notificaciones, GPS, bateria, etc. — confirmado que "systray" no aparece en su lista de facades) | N/A | — | Descartado: no resuelve el problema |
| `tkinter.systray` (stdlib) | Solo en **Python 3.16.0a1** (alpha, no lanzado) | Nuevo en stdlib, en desarrollo | Win/macOS/Linux (futuro) | Ninguna (stdlib) | **No usable hoy** — mencion para radar a futuro si el repo algun dia exige Python 3.16+ |
| `win10toast` | — | Solo notificaciones toast, no icono persistente de bandeja | Solo Windows | — | Descartado: no es un tray icon, es notificaciones puntuales |

### A.3 Por que pystray gana
Es la unica opcion multiplataforma real y activamente usable hoy, con cero dependencias nativas nuevas en Windows (reutiliza Pillow, ya presente), backend Windows confirmado en ctypes puro (sin pywin32), y API capaz de icono dinamico + menu de texto plano que es justo lo que necesita este caso (refresco cada ~60s). El riesgo de adopcion es el ritmo de releases (ninguna en PyPI desde 2023) pero el codigo sigue vivo en GitHub y es records-simple (single-purpose, sin superficie grande que rompa).

---

## B. Arranque automatico al iniciar sesion (Windows)

Comparativa de las 3 vias pedidas, todas sin dependencia nueva pesada:

| Mecanismo | Como se crea | Permisos | Fiabilidad | Facil desinstalar | Dependencias |
|---|---|---|---|---|---|
| **`.vbs`/`.bat` en carpeta Startup** (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`, alias `shell:startup`) | Escribir un fichero de texto ahi con `open(path, 'w')` (stdlib). Un `.bat` abre consola visible salvo que llame a `pythonw.exe`; un `.vbs` con `CreateObject("WScript.Shell").Run "...", 0, False` arranca oculto sin parpadeo de consola. | Ninguno especial, zona de usuario | Alta — Explorer la ejecuta siempre al iniciar sesion de ese usuario | Trivial: `os.remove()` del fichero | **Cero** (stdlib basta) |
| **Programador de tareas (`schtasks /create /sc onlogon`)** | `subprocess.run(["schtasks", "/create", "/tn", "...", "/tr", "...", "/sc", "onlogon", "/f"])`. Confirmado en docs oficiales de Microsoft: sin `/ru`/`/s` (remoto) usa los permisos del usuario actual — **crear una tarea para uno mismo en el equipo local NO requiere elevacion/administrador**. Para desinstalar: `schtasks /delete /tn "..." /f`. | Ninguno especial si es tarea local para el propio usuario | Alta, y con mas control (reintentos, retraso `/delay`, condiciones) que el metodo de carpeta | Trivial: un `subprocess` mas | **Cero** (`subprocess` + binario `schtasks.exe` nativo de Windows) |
| **Clave de registro `HKCU\...\Run`** via `winreg` (stdlib) | `winreg.SetValueEx(key, "CostGuard", 0, winreg.REG_SZ, comando)` | Ninguno especial, `HKCU` es por-usuario | Alta, es el mecanismo clasico, pero **mas vigilado por AV/EDR** (ver nota abajo) | Trivial: `winreg.DeleteValue()` | **Cero** (`winreg` stdlib) |
| *(descartado por el usuario)* `.lnk` via pywin32 (`win32com.client`+`winshell`) | — | — | — | — | Dependencia **pesada** (wheel pywin32 ~6-7 MB por combinacion Python/arch, confirmado en PyPI) — choca con CONTRIBUTING.md |
| *(descartado)* `.lnk` via COM + ctypes/comtypes puro | `comtypes` SI esta activamente mantenido (ultima release `1.4.17`, 2026-09-21, sin dependencias) y es mucho mas ligero que pywin32, pero implementar `IShellLinkW`/`IPersistFile` a mano anade complejidad bastante por encima de KISS/YAGNI para este caso | — | — | — | `comtypes` es dependencia nueva (ligera) o `ctypes` crudo (viable pero fragil, mucho codigo COM boilerplate) |

### Nota de deteccion/AV
MITRE ATT&CK cataloga tanto **Registry Run Keys** como **Startup Folder** bajo la misma tecnica de persistencia **T1547.001** ("Boot or Logon Autostart Execution"), vigilada por software EDR/antivirus por ser el vector mas comun de malware persistente. Scheduled Tasks es una tecnica distinta (T1053.005), igual de vigilada pero el patron de "tarea ONLOGON para el propio usuario, sin privilegios elevados" es mucho mas comun en software legitimo (instaladores de apps de escritorio lo usan rutinariamente) y mas facil de inspeccionar/auditar para el propio usuario (`schtasks /query`) o para quien audite la maquina, que buscar en el registro.

### Recomendacion dentro de B
**Programador de tareas (`schtasks /sc onlogon`)** es la mejor relacion fiabilidad/simplicidad/cero-dependencias:
- No requiere admin si se crea para el propio usuario en el equipo local (confirmado en doc oficial Microsoft).
- Instalar/desinstalar es un unico `subprocess.run(...)` reversible y auditable con `schtasks /query /tn ...`.
- Permite extras sin esfuerzo si hiciera falta mas adelante (retraso de arranque con `/delay`, condicion "solo si el usuario X esta logueado" con `/it`), cosa que el `.vbs` en Startup no ofrece.
- Alternativa igualmente valida y mas "universal" (funciona hasta en entornos donde Task Scheduler este restringido por politica corporativa): **`.vbs` en la carpeta Startup**, que es el equivalente mas directo al LaunchAgent de macOS que ya usa `install.sh` (un fichero declarativo que el SO lee al iniciar sesion). Usar esta como *fallback* si `schtasks` fallase (carpeta Startup bloqueada es mucho menos comun que Task Scheduler bloqueado, pero ambos existen en entornos corporativos restringidos).
- Registry `Run` key: funcionalmente equivalente pero sin ventaja sobre las otras dos, y es la mas asociada culturalmente a malware/"bloatware" en auditorias — sin razon para preferirla aqui.

---

## Recomendacion final

**(a) Tray icon:** `pystray==0.19.5` (version exacta a pedir aprobacion de Dependency Guard). Reutiliza Pillow (ya opcional en el repo), backend Windows 100% `ctypes` sin pywin32, icono dinamico via `icon.icon = imagen_PIL`, menu de texto plano via `Menu`/`MenuItem`. Ejecutar `icon.run()` en un `threading.Thread` dedicado (patron habitual en Windows pese al docstring conservador) para no bloquear el resto de la app.

**(b) Autoarranque:** Tarea de Programador de Tareas de Windows via `subprocess` + `schtasks /create /tn CostGuardTray /tr "<pythonw.exe> <ruta>\barra_windows.py" /sc onlogon /f` (sin `/ru`, sin admin). Desinstalar con `schtasks /delete /tn CostGuardTray /f`. Cero dependencias nuevas (solo `subprocess` + binario nativo de Windows). Fallback opcional: `.vbs` en `shell:startup` si en algun entorno el Task Scheduler estuviera restringido por politica.

## Preguntas sin resolver
- No se pudo confirmar con certeza por que `infi.systray` tiene una subida a PyPI en 2025-01-11 sin commit/tag correspondiente en GitHub (podria ser un empaquetado tardio de un commit ya existente, o un detalle de infraestructura del mantenedor) — irrelevante para la decision final porque igualmente queda descartado por ser solo-Windows, pero se senala por si interesa auditarlo antes de considerarlo en el futuro.
- No se encontraron reportes especificos y recientes (2025-2026) de bugs de pystray en Windows 11 — la ausencia de hallazgos puede deberse a baja actividad de issues nuevos en el repo, no necesariamente a estabilidad confirmada en Win11; conviene una prueba manual corta en la maquina real del usuario antes de dar por buena la eleccion.
