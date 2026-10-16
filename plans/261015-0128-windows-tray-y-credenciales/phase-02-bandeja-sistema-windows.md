# Fase 2 — Icono de bandeja de sistema para Windows

## Enlaces de contexto
- Informe: `reports/researcher-tray-icon-y-autoarranque.md`
- Referencia funcional (NO tocar, solo leer para replicar el subconjunto pedido):
  `barra.py` (clase `Barra`), `costbar.py` (clase `CostBar` dentro de `main()`),
  `infobar/trabajador_barra.py`
- Reutilizable tal cual (cross-platform ya hoy): `infobar/formato_e_icono.py`
  (`icono_png`, `color_magnitud`, `fmt_tok`, `fila`, `barra`), `costbar.escanear()`,
  `costbar.url_panel()`, `costbar.servir()`, `costbar.endurecer()`

## Overview
- **Prioridad:** P1 dentro del plan.
- **Estado:** completed. Aprobacion de `pystray==0.19.5` concedida por el usuario el 2026-10-15
  (Dependency Guard — ver tabla en `plan.md`); paquete instalado y confirmado (`pip show pystray`
  -> `0.19.5`).
- Icono en la bandeja de Windows con la cifra de la ventana de 5 h, menu de texto plano con
  las magnitudes clave (hoy/ritmo/5h), accion "Abrir panel" y refresco cada 60 s. **No**
  replica el desplegable "glass" con PNG renderizado por Chrome de macOS (no se ha pedido, y
  el propio encargo dice que texto plano basta) — alcance deliberadamente recortado (YAGNI).

## Key Insights (de la investigacion)
- `pystray` en Windows usa **ctypes puro** (sin pywin32); su unica dependencia extra es
  Pillow, ya opcional en el repo.
- `pystray.Icon` acepta una `PIL.Image.Image` directamente y tiene un setter `icon.icon = imagen`
  que repinta en caliente — encaja perfecto con `icono_png()`, que ya escribe un PNG en disco
  (abrir con `PIL.Image.open(ruta)` antes de pasarlo a pystray, sin modificar `icono_png`).
- El patron recomendado es correr `icon.run()` en un `threading.Thread` dedicado (pese al
  docstring conservador de la libreria, que es herencia de la restriccion real de AppKit).
- Sin release en PyPI desde 2023 pero con commits en GitHub hasta 2025 — vivo, ritmo lento.
  Monitorizar si aparecen issues de Windows 11 tras instalar.

## Requirements
### Funcionales
- Icono dinamico con la cifra de 5 h (mismo dibujo que macOS via `icono_png`).
- Menu de texto plano: "Últimas 5 h: X (Y %)" (o tokens si no hay limite declarado), "Hoy: X",
  "Ritmo última hora: X", separador, "Abrir panel", "Refrescar ahora", "Salir".
- "Abrir panel" abre `costbar.url_panel()` con el navegador por defecto via `webbrowser.open()`
  (nunca `subprocess.Popen(["open", ...])`, eso es macOS-only).
- Refresco automatico cada 60 s (mismo intervalo que `rumps.Timer` en macOS), sin bloquear el
  hilo de pystray.
- "Salir" cierra el icono (`icon.stop()`) y termina el proceso limpiamente.

### No funcionales
- Nueva dependencia `pystray==0.19.5`, **pedir aprobacion explicita antes de `pip install`**
  (ver paso 0 mas abajo). Tras instalar, pasar por el agente `quality-gate`.
- Fichero `bandeja_windows.py` bajo 200 lineas; si crece, extraer helpers a
  `infobar/bandeja_windows_menu.py` (modularizar segun regla del repo).
- Excepciones dentro del bucle de refresco se atrapan y se loguean (mismo patron que el
  `try/except` de `CostBar.refrescar` en macOS) — un fallo de refresco no debe matar el icono.

## Architecture
```
bandeja_windows.py (NUEVO, raiz del repo)
  if sys.platform != "win32": SystemExit (uso incorrecto)
  costbar.endurecer(); costbar.servir()           # mismo arranque que macOS
  hilo_refresco = threading.Thread(target=_bucle_refresco, daemon=True)
  icon = pystray.Icon("CostGuard", imagen_inicial, menu=pystray.Menu(...))
  icon.run()                                       # bloqueante, hilo principal

_bucle_refresco():
  cada 60s:
    r = costbar.escanear()
    imagen = Image.open(icono_png(ruta_tmp, fraccion, texto, color_magnitud(...), alerta))
    icon.icon = imagen
    icon.menu = pystray.Menu(<items con los datos de r>)
```
Flujo de datos: `costbar.escanear()` (ya cross-platform) -> dict de agregados -> formateo con
`fmt_tok`/`fila` (ya cross-platform) -> texto de los `MenuItem` -> pystray pinta el menu nativo
de Windows. Sin PNG de Chrome, sin `infobar/desplegable_png.py`, sin `infobar/servidores_ciclo.py`
(fuera de alcance v1, ver "No objetivos" abajo).

## No objetivos (recortado deliberadamente, YAGNI)
- Pestaña de servidores (SSH) en el menu de Windows: no pedida en el encargo, añade
  dependencia logica en `servidores_ciclo`/`servidores_ssh` sin necesidad confirmada.
- Desglose por proveedor/modelo/proyecto en el menu (si en macOS): el encargo pide solo
  "hoy, ritmo, 5h". Si se quiere paridad completa, es una fase futura a decidir con el usuario.
- Temas claro/oscuro: los menus nativos de Windows siguen el tema del sistema automaticamente,
  no hace falta logica propia (a diferencia del PNG "glass" de macOS).

## Related Code Files
**Crear:**
- `bandeja_windows.py` (raiz)
- `tests/test_bandeja_windows.py`

**Modificar:**
- `requirements.txt` — añadir `pystray==0.19.5 ; sys_platform == "win32"` y actualizar el
  comentario de `pillow` (ya no es "solo macOS", tambien lo usa la bandeja de Windows).

**No tocar:** `barra.py`, `costbar.py`, `infobar/trabajador_barra.py`, `infobar/desplegable_png.py`.

## Implementation Steps
0. **Gate de aprobacion:** antes de escribir codigo que importe `pystray`, pedir al usuario
   confirmacion explicita para instalar `pystray==0.19.5` (version exacta, no un rango).
1. `pip install pystray==0.19.5` en el entorno de Windows; delegar verificacion post-instalacion
   al agente `quality-gate`.
2. Escribir `bandeja_windows.py`: guarda de plataforma, construccion inicial del `Icon`,
   `_bucle_refresco` con manejo de excepciones, acciones de menu (`webbrowser.open`,
   refrescar-ahora, salir).
3. Probar manualmente en Windows 11 real: icono visible, cifras coherentes con
   `python analizar_gasto.py` corrido en paralelo, refresco tras 60 s, "Abrir panel" abre el
   navegador con sesion valida, "Salir" no deja proceso zombie (comprobar en Task Manager).
4. `tests/test_bandeja_windows.py`: solo las partes **puras** (formateo de texto del menu a
   partir de un `r` de `escanear()` de prueba), sin crear un icono real de pystray en CI —
   `@unittest.skipUnless` si `pystray` no esta instalado (para que el resto de la suite no
   dependa de la aprobacion de la dependencia).

## Todo List
- [x] Aprobacion explicita del usuario para `pystray==0.19.5`
- [x] `pip install pystray==0.19.5` (confirmado instalado; ver nota sobre `quality-gate` abajo)
- [x] `bandeja_windows.py`
- [x] `tests/test_bandeja_windows.py` (logica de formateo, sin tray real)
- [ ] Prueba manual en Windows 11 real (icono, menu, refresco, abrir panel, salir) — pendiente de
      una pasada interactiva del usuario, ver checklist de fase 4.
- [x] `requirements.txt` actualizado con marker `sys_platform == "win32"`

## Success Criteria
- Icono visible en la bandeja tras `python bandeja_windows.py`, cifra de 5 h coincide con
  `python analizar_gasto.py` ejecutado a la vez.
- Menu muestra hoy/ritmo/5h con los mismos numeros que el informe de texto.
- "Abrir panel" abre el navegador por defecto en la URL de `costbar.url_panel()` con sesion
  valida (sin pedir login de nuevo).
- Tras >60 s sin cerrar el proceso, el log (`costbar.log`) registra al menos un refresco nuevo.
- "Salir" termina el proceso (verificado en Task Manager, sin `pythonw.exe` residual).

## Risk Assessment
| Riesgo | Likelihood | Impact | Mitigacion |
|---|---|---|---|
| Reasignar `icon.menu`/`icon.icon` desde otro hilo causa crash o icono no se actualiza | Media | Media | Prueba manual explicita (paso 3); si falla, alternativa: recrear el `Icon` entero en vez de reasignar, o encolar la actualizacion al hilo de pystray |
| `pystray` sin release desde 2023: bug no corregido en Windows 11 | Baja | Media | Pin exacto de version; vigilar GitHub antes de subir de version en el futuro |
| Menu con caracteres de bloque (`█`/`░` de `barra()`) no se ve bien en la fuente de menu nativa de Windows | Baja | Baja | Probar visualmente (paso 3); si falla, usar porcentaje en texto plano en vez de `barra()` |
| Excepcion no atrapada en `_bucle_refresco` mata el hilo silenciosamente (icono se queda congelado) | Media | Media | `try/except` envolviendo todo el cuerpo del bucle + log, igual que `CostBar.refrescar` en macOS |

## Security Considerations
- `webbrowser.open()` solo con la URL que devuelve `costbar.url_panel()` (ya lleva su propio
  ticket de un solo uso) — no construir URLs a mano ni aceptar entrada externa.
- No pasar ninguna clave/secreto como argumento de proceso (el icono no necesita leer claves
  de API para nada de lo que pinta: datos de consumo, no credenciales).

## Next Steps
- Fase 3 depende de que `bandeja_windows.py` exista y funcione manualmente antes de
  automatizar su arranque.
