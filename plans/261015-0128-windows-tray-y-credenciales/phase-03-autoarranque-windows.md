# Fase 3 — Arranque automatico al iniciar sesion en Windows

## Enlaces de contexto
- Informe: `reports/researcher-tray-icon-y-autoarranque.md` (seccion B)
- Referencia funcional (NO tocar): `install.sh` (LaunchAgent de macOS)
- Depende de: Fase 2 (`bandeja_windows.py` debe existir y funcionar manualmente)

## Overview
- **Prioridad:** P2 dentro del plan.
- **Estado:** completed.
- Instalador Python puro (`instalar_windows.py`, raiz) que copia los ficheros necesarios a
  `~/.cost-guard` (misma convencion que macOS/Linux, `permisos.py` ya sabe que ahi se activa
  el modo "instalacion"), crea un entorno virtual con `pillow` + `pystray==0.19.5`, y registra
  una tarea en el Programador de Tareas de Windows (`schtasks /create /sc onlogon`) que lanza
  `bandeja_windows.py` sin ventana de consola (`pythonw.exe`).

## Key Insights (de la investigacion)
- Crear una tarea `ONLOGON` para el propio usuario en el equipo local **no requiere
  administrador** (confirmado en documentacion oficial de Microsoft).
- `schtasks /delete /tn <nombre> /f` desinstala limpio y es auditable con
  `schtasks /query /tn <nombre> /v /fo list`.
- Gotcha de quoting: el argumento `/tr` de `schtasks` es **una sola cadena** que Windows vuelve
  a parsear como linea de comandos — hay que envolver la ruta del interprete Y la del script en
  comillas dobres **dentro** de esa cadena (`"C:\...\pythonw.exe" "C:\...\bandeja_windows.py"`),
  no basta con pasar una lista a `subprocess.run` y esperar que Windows lo resuelva solo.
- Alternativa de respaldo si `schtasks` fallara (politica corporativa que bloquee el
  Programador de Tareas): un `.vbs` en `shell:startup`
  (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`) que arranca oculto con
  `CreateObject("WScript.Shell").Run "...", 0, False` — cero dependencias, stdlib basta para
  escribir el fichero.
- Registro (`HKCU\...\Run`) descartado: sin ventaja sobre las otras dos y mas vigilado por
  AV/EDR en auditorias (MITRE T1547.001).

## Requirements
### Funcionales
- `instalar_windows.py` (ejecutable con `python instalar_windows.py`):
  1. Copia `costbar.py`, `bandeja_windows.py`, `analizar_gasto.py`, `panel.py`,
     `precio_astra.py`, `GUIA.md`, `infobar/*.py` (incluido `credenciales_windows.py`) a
     `~/.cost-guard` (mismo destino que macOS/Linux; no pisa `config.json` si ya existe, igual
     que `install.sh`).
  2. Crea `~/.cost-guard/venv` (`python -m venv`) e instala `pillow` + `pystray==0.19.5`.
  3. Registra la tarea `CostGuardTray` con `schtasks /create /tn CostGuardTray /tr "<pythonw> <bandeja_windows.py>" /sc onlogon /f`.
  4. Imprime instrucciones de verificacion y desinstalacion (igual que el resumen final de
     `install.sh`).
- Reintento/idempotencia: `/f` fuerza sobrescritura si la tarea ya existe (igual que el
  `launchctl bootout` + `bootstrap` de macOS).

### No funcionales
- Cero dependencias nuevas propias (usa `subprocess` + `schtasks.exe`, nativo de Windows).
- No requiere privilegios de administrador en ningun paso.
- Script bajo 200 lineas; si crece, separar la logica de "copiar ficheros" de la de
  "registrar tarea" en funciones o un modulo auxiliar.

## Architecture
```
instalar_windows.py (NUEVO, raiz)
  DESTINO = os.path.expanduser("~/.cost-guard")   # mismo convenio que install.sh
  copiar_ficheros(DESTINO)
  crear_venv_e_instalar(DESTINO)                  # venv + pip install pillow pystray==0.19.5
  registrar_tarea(DESTINO)                        # subprocess schtasks /create ... /f
  print(resumen_con_comandos_de_verificacion_y_desinstalacion)
```
Flujo: usuario ejecuta `python instalar_windows.py` una vez -> ficheros copiados a
`~/.cost-guard` -> venv propio -> tarea registrada -> en el siguiente inicio de sesion, Windows
lanza `pythonw.exe ~/.cost-guard/bandeja_windows.py` sin intervencion manual.

## Related Code Files
**Crear:**
- `instalar_windows.py` (raiz)

**No tocar:** `install.sh`, ningun fichero de `infobar/` (salvo los ya creados en fases 1-2,
que aqui solo se *copian*, no se modifican).

## Implementation Steps
1. Escribir `instalar_windows.py` con las 3 funciones (`copiar_ficheros`,
   `crear_venv_e_instalar`, `registrar_tarea`) + `main()`.
2. Construir con cuidado la cadena de `/tr` (comillas dobles internas, ruta absoluta de
   `pythonw.exe` del venv creado, ruta absoluta de `bandeja_windows.py` en `~/.cost-guard`).
3. Ejecutar en una maquina Windows real: `python instalar_windows.py`.
4. Verificar con `schtasks /query /tn CostGuardTray /v /fo list` que `/tr` quedo bien
   registrado (sin comillas rotas, ruta correcta).
5. Cerrar sesion y volver a iniciarla (o reiniciar) y confirmar que el icono aparece solo.
6. Documentar en el propio `print()` final del script los comandos de desinstalacion manual:
   `schtasks /delete /tn CostGuardTray /f` (equivalente al `launchctl bootout` de macOS).

## Todo List
- [x] `instalar_windows.py` (copiar + venv + tarea; incluye ademas el fallback de `.vbs` en
      `shell:startup` si `schtasks /create` rechaza el trigger `ONLOGON`, anticipado en el
      Risk Assessment de abajo y ya implementado, no solo documentado)
- [ ] Verificacion de quoting de `/tr` con `schtasks /query /v /fo list` — pendiente de ejecutar
      `instalar_windows.py` de punta a punta en una maquina real, ver checklist de fase 4
- [ ] Prueba real de cierre/apertura de sesion en Windows — pendiente, requiere sesion interactiva
- [x] Mensaje final con comandos de desinstalacion (`schtasks /delete` o borrar el `.vbs`, segun
      cual mecanismo haya quedado activo)

## Success Criteria
- `schtasks /query /tn CostGuardTray /v /fo list` muestra la tarea con el comando correcto
  tras ejecutar el instalador.
- Cerrar e iniciar sesion de nuevo arranca el icono sin accion manual.
- `schtasks /delete /tn CostGuardTray /f` elimina la tarea limpiamente (verificado con un
  segundo `schtasks /query` que falla "no existe").
- Reejecutar `instalar_windows.py` una segunda vez no falla ni duplica la tarea (idempotente).

## Risk Assessment
| Riesgo | Likelihood | Impact | Mitigacion |
|---|---|---|---|
| Quoting incorrecto en `/tr` (rutas con espacios, p. ej. "Program Files" o nombre de usuario con espacio) | Media | Alta (la tarea existe pero no lanza nada) | Verificacion explicita con `schtasks /query /v /fo list` en el paso 4, antes de dar la fase por cerrada |
| Programador de Tareas bloqueado por politica corporativa en el equipo del usuario | Baja | Media | Fallback documentado (`.vbs` en `shell:startup`); no se implementa de entrada por YAGNI, pero queda anotado y es trivial de añadir si hace falta |
| `schtasks /create` sin `/f` en una re-instalacion falla por "la tarea ya existe" | Baja | Baja | Usar siempre `/f` (ya decidido) |

## Security Considerations
- La tarea no lleva ningun secreto en su linea de comandos (solo rutas a interprete y script),
  igual que el principio ya aplicado en la correccion reciente "ticket fuera de ps" de macOS.
- `pythonw.exe` del venv propio del usuario, no un interprete de sistema compartido —
  evita conflictos de permisos con otras instalaciones de Python.

## Next Steps
- Fase 4: regresion completa (Windows + macOS) y actualizacion de README/CONTRIBUTING con la
  tabla de compatibilidad al dia.
