# Fase 4 — Regresion, test matrix y documentacion

## Enlaces de contexto
- Depende de: Fases 1, 2 y 3 completas.
- Ficheros de referencia de compatibilidad: `README.md` (tabla "Compatibilidad por sistema"),
  `CONTRIBUTING.md`, `requirements.txt`.

## Overview
- **Prioridad:** P2 (cierre del plan).
- **Estado:** in-progress. Documentacion (README/CONTRIBUTING/plan) actualizada; la regresion
  manual completa en macOS queda pendiente del usuario (ver Todo List).
- Cierra el plan: confirma que no hay regresion en macOS/Linux, corre la suite completa en
  Windows con todo instalado, y deja la documentacion (README/CONTRIBUTING/tabla de
  compatibilidad) reflejando la nueva realidad.

## Requirements
### Verificacion de "aditivo" (la restriccion mas importante del plan)
```bash
git diff -- barra.py costbar.py popover.py install.sh \
  infobar/desplegable_png.py infobar/geometria_desplegable.py \
  infobar/servidores_ciclo.py infobar/formato_e_icono.py
```
Debe salir **vacio**. Si no lo esta, alguna fase se desvio de su alcance — revisar antes de
continuar.

### Test matrix
| Tipo | Que cubre | Donde |
|---|---|---|
| Unitario | `_blob()`/encoding de `credenciales_windows.py`, `cuenta_valida` compartida, formateo de texto del menu de `bandeja_windows.py` | `tests/test_credenciales_windows.py`, `tests/test_bandeja_windows.py` |
| Integracion | Round-trip real Credential Manager (guardar/leer/borrar), endpoints `/ajustes` y `/clave` de `servidor_local.py` ejercitados en Windows real (hoy se saltan ahi) | `tests/test_credenciales_windows.py`, `tests/test_config_y_servidor_local.py`, `tests/test_ajustes.py` |
| Identidad/no-regresion | `claves.leer_clave`/`guardar_clave`/`borrar_clave`/`hay_llavero` siguen siendo los objetos originales en macOS/Linux (el bloque de dispatch no se ejecuta) | nuevo test o ampliar `tests/test_config_y_servidor_local.py::ClavesTest` |
| End-to-end (manual, requiere GUI/sesion real) | Icono visible, menu correcto, abrir panel, refresco tras 60 s, salir sin proceso zombie, autoarranque tras reinicio de sesion | checklist manual (ver abajo), no automatizable sin una sesion interactiva de Windows |

### Checklist de verificacion manual (una sola pasada, en orden)
1. `python -m unittest discover tests` en Windows real — 0 fallos, 0 saltos inesperados
   (los que ya se saltaban por diseño, como `test_permisos.py` en NTFS, siguen saltandose).
2. Mismo comando en macOS (o pedir al usuario que lo corra ahi) — comparar conteo de tests
   con el baseline anterior a este plan; **0 nuevos fallos, 0 tests que desaparezcan**.
3. `python instalar_windows.py` de cero en una maquina limpia (o `$HOME` vacio) — no debe
   lanzar excepciones (mismo criterio que CONTRIBUTING.md ya exige para el motor).
4. Cerrar/abrir sesion en Windows, confirmar arranque automatico del icono.
5. Guardar una clave de API desde el panel (Ajustes), confirmar con `cmdkey /list`.
6. `schtasks /delete /tn CostGuardTray /f` + `pip uninstall pystray` — confirmar que el
   repo vuelve al estado "sin bandeja" sin dejar nada roto (rollback de las fases 2-3).

## Related Code Files
**Modificar (solo documentacion/config, sin logica):**
- `README.md` — tabla "Compatibilidad por sistema": filas de icono de bandeja y arranque
  automatico pasan de ❌ a ✅ en Windows; seccion "Windows" actualizada con los nuevos pasos
  (`instalar_windows.py`) en vez de solo "ejecuta el motor a mano".
- `CONTRIBUTING.md` — nota de como preparar el entorno de desarrollo para tocar
  `bandeja_windows.py`/`credenciales_windows.py` (venv + `pystray` en Windows).
- `requirements.txt` — ya tocado en fase 2, revisar que el comentario de cabecera siga siendo
  preciso tras las dos fases (ya no es "solo macOS").

**No crear ni tocar nada mas.**

## Implementation Steps
1. Correr el `git diff` de la seccion "Verificacion de aditivo" y confirmar que sale vacio.
2. Ejecutar la test matrix completa (tabla de arriba) en Windows.
3. Pedir/coordinar una pasada de la suite en macOS (no ejecutable desde este entorno Windows)
   y comparar contra el baseline.
4. Actualizar `README.md` (tabla de compatibilidad + seccion Windows) y `CONTRIBUTING.md`.
5. Checklist de verificacion manual (6 pasos de arriba), uno a uno.
6. Marcar las 4 fases como completadas en `plan.md` (campo `status` del frontmatter y tabla de
   fases).

## Todo List
- [x] `git diff` de ficheros macOS-only vacio (verificado en esta sesion, sin salida)
- [x] Suite completa en Windows: 0 fallos (`python -m unittest discover tests` -> OK, solo los
      saltos esperados: `test_permisos.py` en NTFS y el test de no-regresion que es NO-Windows)
- [ ] Suite completa en macOS: sin regresion frente al baseline — pendiente de que el usuario la
      corra ahi (no ejecutable desde este entorno Windows)
- [ ] `instalar_windows.py` probado en maquina/entorno limpio — pendiente (registrar una tarea
      real en el Programador de tareas del usuario no se ha hecho desde esta sesion de
      documentacion)
- [x] README.md y CONTRIBUTING.md actualizados
- [ ] Checklist manual de 6 pasos completado — pasos 1 y 5 verificados parcialmente (suite y
      round-trip de credenciales por ctypes); pasos 2, 3, 4 y 6 pendientes de sesion interactiva
- [ ] `plan.md` marcado como `completed` — las fases 1-3 ya se marcan `completed` en esta misma
      tanda; el plan completo (fase 4) sigue `in-progress` hasta cerrar la regresion macOS

## Success Criteria
- Los 4 bullets de "Checklist de verificacion manual" pasan sin intervencion manual adicional
  mas alla de la ya descrita.
- `README.md` tabla de compatibilidad sin ningun ❌ pendiente para Windows en icono de bandeja
  ni arranque automatico.
- Cero diferencias en ficheros exclusivos de macOS (verificado por `git diff`, no por memoria).

## Risk Assessment
| Riesgo | Likelihood | Impact | Mitigacion |
|---|---|---|---|
| Regresion en macOS pasa desapercibida porque el plan se ejecuto solo en Windows | Media | Alta | Paso explicito 3 (coordinar pasada en macOS) no es opcional; no cerrar la fase sin esa confirmacion |
| Documentacion desactualizada tras cambios de ultima hora en fases 1-3 | Baja | Baja | Actualizar README/CONTRIBUTING como ultimo paso, releyendo el codigo final (no lo escrito en el plan) |

## Security Considerations
- Ninguna nueva: esta fase es verificacion y documentacion, no añade superficie de ataque.

## Next Steps
- Ninguno — cierra el plan. Si el usuario quiere paridad completa del menu (servidores,
  desglose por proveedor/modelo) o migrar a `keyring` unificado, son plans nuevos, no
  continuacion de este (ver "Preguntas sin resolver" en `plan.md`).
