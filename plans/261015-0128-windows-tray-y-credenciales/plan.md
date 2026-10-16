---
title: "Bandeja de sistema y credenciales en Windows"
description: "Icono de bandeja (pystray) y almacen de claves de API (Credential Manager) para Windows, sin tocar nada de macOS"
status: in-progress
priority: P2
effort: 11h
branch: ai-2you/adaptacion-windows
tags: [windows, tray-icon, pystray, credential-manager, credenciales]
created: 2026-10-15
---

# Bandeja de sistema y credenciales en Windows

Portar a Windows las dos unicas piezas que hoy son solo-macOS: el icono de bandeja
(`barra.py`/`costbar.py`, usa `rumps`/AppKit) y el guardado de claves de API
(`infobar/claves.py`, usa el comando `security` del Keychain). El motor
(`analizar_gasto.py`, `panel.py`, `infobar/*` salvo `claves.py`) ya es 100 % multiplataforma.

## Restriccion de diseno (no negociable)

Todo lo de macOS/Linux existente queda **exactamente igual**: ni una linea de `barra.py`,
`costbar.py`, `popover.py`, `install.sh`, `infobar/desplegable_png.py`,
`infobar/geometria_desplegable.py`, `infobar/servidores_ciclo.py` ni
`infobar/formato_e_icono.py` cambia. Cada pieza nueva vive en un fichero Windows-only; el
unico punto de dispatch por plataforma es un bloque añadido al **final** de
`infobar/claves.py` (ver fase 1) que no toca ni una linea de las funciones existentes.
**Verificacion de "aditivo" (correr antes de cerrar la fase 4):**
```
git diff -- barra.py costbar.py popover.py install.sh infobar/desplegable_png.py \
  infobar/geometria_desplegable.py infobar/servidores_ciclo.py infobar/formato_e_icono.py
```
Debe salir vacio.

## Hallazgo clave de arquitectura

El panel web de Ajustes (`infobar/servidor_local.py`, endpoints `/ajustes` y `/clave`) ya
llama a `claves.leer_clave/guardar_clave/borrar_clave/hay_llavero` directamente — **no** a
traves de `costbar.py`. En cuanto la fase 1 añada el dispatch en `claves.py`, el guardado de
claves desde el panel (ya servido y abierto en el navegador en Windows, ver README) funciona
sin tocar `servidor_local.py`, `infobar/ajustes.py` ni `panel_assets/ajustes.js`. La fase 1 no
depende de la bandeja (fase 2) ni viceversa: son paralelizables.

## Dependencias nuevas propuestas (requieren aprobacion explicita antes de instalar)

| Paquete | Version exacta | Para que | Peso | Aprobacion |
|---|---|---|---|---|
| `pystray` | `0.19.5` | Icono de bandeja (fase 2) | Solo Pillow como dependencia extra en Windows (backend ctypes puro, sin pywin32) | **Aprobada por el usuario el 2026-10-15 e instalada** (confirmado con `pip show pystray` -> `0.19.5`, fase 2 completada). |

Credenciales (fase 1) usa **solo** `ctypes`/`ctypes.wintypes` de la libreria estandar: **cero
dependencias nuevas**.

## Fases

| Fase | Fichero | Depende de | Esfuerzo | Estado |
|---|---|---|---|---|
| 1 | [phase-01-credenciales-windows.md](phase-01-credenciales-windows.md) | — | 3h | completed |
| 2 | [phase-02-bandeja-sistema-windows.md](phase-02-bandeja-sistema-windows.md) | — | 4h | completed |
| 3 | [phase-03-autoarranque-windows.md](phase-03-autoarranque-windows.md) | Fase 2 | 2h | completed |
| 4 | [phase-04-tests-regresion-y-documentacion.md](phase-04-tests-regresion-y-documentacion.md) | Fases 1-3 | 2h | in-progress |

Fases 1 y 2 no comparten ningun fichero: ejecutables en paralelo si se reparten entre dos
sesiones. Fase 3 solo empieza cuando `bandeja_windows.py` (fase 2) existe. Fase 4 cierra el
plan: regresion completa + documentacion.

## Informes de investigacion

- `reports/researcher-tray-icon-y-autoarranque.md` — pystray vs alternativas, Task Scheduler vs Startup folder vs registro.
- `reports/researcher-credenciales-windows.md` — keyring vs ctypes/advapi32 vs pywin32, con la estructura `CREDENTIALW` documentada.

## Preguntas sin resolver (heredadas de la investigacion, a validar en pruebas manuales)

1. ¿Reasignar `icon.menu`/`icon.icon` de pystray desde un hilo distinto al que corre
   `icon.run()` es seguro en Windows 11 real? No hay reportes de bugs recientes, pero tampoco
   confirmacion positiva — validar en la maquina real del usuario (fase 2).
2. ¿`CRED_PERSIST_LOCAL_MACHINE` pide UAC en una cuenta no-administradora? La documentacion de
   Microsoft no lo sugiere, pero no esta confirmado empiricamente (fase 1).
3. Si en el futuro se quisiera unificar los 3 backends de claves (macOS/Windows/Linux) en
   `keyring`, es una decision de arquitectura **distinta** a este ticket (ver informe B) — no
   se resuelve aqui, queda anotada para cuando/si se plantee.

## Hallazgos de code-reviewer ya corregidos (fases 1-2)

El codigo final difiere ligeramente del pseudocodigo original de las fases 1 y 2 por dos
correcciones que aplico el `code-reviewer` durante la implementacion:

1. **`ctypes.WinDLL("advapi32", use_last_error=True)`** en `infobar/credenciales_windows.py`
   (el pseudocodigo de la fase 1 decia `use_last_error=False`). Con `True`, ctypes captura el
   error de la llamada C inmediatamente despues de ejecutarla, antes de que otra llamada Win32
   intermedia (incluso interna de CPython) lo sobrescriba; `guardar_clave`/`borrar_clave` leen
   ese error con `ctypes.get_last_error()` para componer el mensaje de `ctypes.WinError(...)`.
2. **Lock de concurrencia (`_lock_refresco`)** en `bandeja_windows.py`, no previsto explicitamente
   en el pseudocodigo de arquitectura de la fase 2. Protege `_refrescar()` porque dos caminos
   pueden llamarla a la vez: el hilo de refresco cada 60 s y el click de "Refrescar ahora" desde
   el hilo de pystray; sin el lock, ambos podrian pisarse escribiendo el mismo PNG temporal
   (`RUTA_ICONO`) o reasignando `icon.icon`/`icon.menu` a medio camino.

Ninguno de los dos cambia el contrato ni el alcance descritos en las fases 1/2; son correcciones
de robustez de bajo nivel (gestion de errores de ctypes y seguridad de hilos) detectadas en
revision de codigo.
