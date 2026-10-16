# Fase 1 — Credenciales de API en Windows (Credential Manager)

## Enlaces de contexto
- Informe: `reports/researcher-credenciales-windows.md`
- Contrato de referencia (no tocar): `infobar/claves.py`
- Consumidores actuales del contrato: `infobar/servidor_local.py` (endpoints `/ajustes`, `/clave`), `infobar/cuota_nan.py` (`clave_nan()`), `infobar/ajustes.py` (`cuenta_de`)
- Tests de referencia: `tests/test_config_y_servidor_local.py::ClavesTest`, `tests/test_ajustes.py`

## Overview
- **Prioridad:** P1 dentro del plan (habilita el guardado de claves desde el panel web, que ya funciona en Windows sin cambios adicionales).
- **Estado:** completed.
- Modulo nuevo `infobar/credenciales_windows.py` que implementa el mismo contrato que
  `infobar/claves.py` contra el Credential Manager de Windows, via `ctypes` puro (sin
  dependencias nuevas). `claves.py` gana un bloque de dispatch al final del fichero (no
  modifica ninguna linea existente).

## Key Insights (de la investigacion)
- `CRED_MAX_CREDENTIAL_BLOB_SIZE` real es **2560 bytes** (5×512), no 512 — de sobra para
  cualquier API key en UTF-16 (~1280 caracteres).
- `CredentialBlob` **no** es un `c_wchar_p` directo: se construye con
  `ctypes.create_unicode_buffer(valor)`, se castea a `LPBYTE`, y `CredentialBlobSize = sizeof(buffer) - sizeof(wchar)` (sin el nulo final).
- El puntero que devuelve `CredReadW` **debe liberarse con `CredFree`** tras copiar los datos.
- Hay que declarar `.argtypes`/`.restype` de cada funcion de `advapi32.dll` explicitamente
  (si no, ctypes asume `int` y puede truncar punteros en 64-bit).
- Gotcha de ciclo de vida: si el buffer UTF-16 se construye inline sin asignarlo a una
  variable local, el GC puede liberarlo antes de la llamada C — asignar siempre a variables.
- Referencia auditada para contrastar la implementacion (leer el codigo, **no instalar como
  dependencia**): `enthought/pywin32-ctypes`, modulo `win32ctypes/pywin32/win32cred.py`.

## Requirements
### Funcionales
- Mismo contrato exacto que `infobar/claves.py`:
  `hay_llavero()`, `cuenta_valida(cuenta)`, `leer_clave(cuenta)`, `guardar_clave(cuenta, valor=None)`, `borrar_clave(cuenta)`.
- `SERVICIO = "infobar"`; `TargetName` en Credential Manager = `f"{SERVICIO}:{cuenta}"` (mismo
  patron servicio+cuenta que macOS, para poder razonar sobre ambos backends igual).
- `cuenta_valida` se reutiliza tal cual (es una regex pura, sin dependencia de SO) —
  **importarla** de `claves.py`, no duplicar el regex (DRY).
- `guardar_clave` sin `valor` pide la clave por `getpass.getpass()` (igual que macOS).
- Mismas validaciones de caracteres que macOS (`[\x21-\x7e]+`, sin comillas ni barras) antes
  de escribir — reusar la misma comprobacion importandola o extrayendola a una funcion
  compartida si se prefiere (decision de implementacion, documentar la que se tome).

### No funcionales
- Cero dependencias nuevas (solo `ctypes`, `ctypes.wintypes`, stdlib).
- Fichero bajo 200 lineas (estimado 110-150).
- No se registra nunca el valor de la clave en logs (`log()` solo con nombres de cuenta, igual
  que `claves.py` hoy).

## Architecture
```
infobar/claves.py (SIN CAMBIOS en sus funciones existentes)
  + al final del fichero:
      import sys
      if sys.platform == "win32":
          from . import credenciales_windows as _win
          hay_llavero = _win.hay_llavero
          leer_clave = _win.leer_clave
          guardar_clave = _win.guardar_clave
          borrar_clave = _win.borrar_clave
          # cuenta_valida NO se reasigna: es la misma regex en los dos SO

infobar/credenciales_windows.py (NUEVO)
  SERVICIO = "infobar"  (re-exportado o importado de claves, a decidir sin duplicar)
  _CREDENTIALW (ctypes.Structure), _advapi32 = ctypes.WinDLL("advapi32", use_last_error=False)
  hay_llavero() -> True  (si sys.platform == "win32"; el modulo solo se importa ahi)
  leer_clave(cuenta) -> CredReadW + decode UTF-16 + CredFree
  guardar_clave(cuenta, valor=None) -> CredWriteW (Persist=CRED_PERSIST_LOCAL_MACHINE)
  borrar_clave(cuenta) -> CredDeleteW (ignora "no encontrado")
```
Flujo de datos: panel web (`/clave` POST) -> `servidor_local._clave()` -> `claves.guardar_clave(cuenta, valor)`
(ya dispatchado a Windows) -> `CredWriteW` -> Credential Manager. Lectura: `/ajustes` GET ->
`claves.leer_clave(cuenta)` -> `CredReadW` -> bool `clave_guardada` en la respuesta JSON (el
valor en si **nunca** sale por HTTP, ya filtrado por `sin_secretos()` existente).

## Related Code Files
**Crear:**
- `infobar/credenciales_windows.py`
- `tests/test_credenciales_windows.py`

**Modificar (solo apendice, cero lineas existentes tocadas):**
- `infobar/claves.py` — bloque de dispatch al final.

**No tocar:** todo lo demas.

## Implementation Steps
1. Escribir `infobar/credenciales_windows.py`:
   a. Definir `_CREDENTIALW` (`ctypes.Structure`) con los campos de `CREDENTIALW` (ver informe).
   b. Cargar `advapi32 = ctypes.WinDLL("advapi32")`; declarar `argtypes`/`restype` de
      `CredWriteW`, `CredReadW`, `CredDeleteW`, `CredFree`.
   c. Helper `_blob(valor: str) -> (buffer, size)` que construye el `create_unicode_buffer` y
      calcula `CredentialBlobSize` sin el nulo final.
   d. `hay_llavero()`: `return True` (el modulo solo se carga en Windows).
   e. `leer_clave(cuenta)`: valida con `cuenta_valida`, llama `CredReadW`, decodifica, libera
      con `CredFree`, devuelve `""` si `CredReadW` falla con `ERROR_NOT_FOUND`.
   f. `guardar_clave(cuenta, valor=None)`: mismas validaciones de caracteres que macOS, rellena
      `_CREDENTIALW`, `Persist = CRED_PERSIST_LOCAL_MACHINE`, `CredWriteW`; `raise RuntimeError`
      con `ctypes.WinError()` si falla.
   g. `borrar_clave(cuenta)`: `CredDeleteW`, ignora `ERROR_NOT_FOUND`.
2. Añadir el bloque de dispatch al final de `infobar/claves.py` (import sys + rebind
   condicional). No reordenar ni tocar ninguna linea existente del fichero.
3. Escribir `tests/test_credenciales_windows.py`:
   - `@unittest.skipIf(os.name != "nt", "solo Windows")` a nivel de clase.
   - Round-trip real: `guardar_clave("prov:cg-test", "valor-de-prueba-123")` -> `leer_clave(...)  == "valor-de-prueba-123"` -> `borrar_clave(...)` -> `leer_clave(...) == ""`. **`try/finally` con `borrar_clave` en el cleanup** aunque falle una aserción (no dejar basura en el Credential Manager real del usuario).
   - Validaciones: clave vacia o con comillas/barras -> `ValueError`, sin llegar a `CredWriteW`.
   - `cuenta_valida` sigue siendo la misma funcion importada de `claves` (test de identidad).
4. Test de no-regresion en `tests/test_config_y_servidor_local.py` (o nuevo fichero): en
   macOS/Linux, `claves.leer_clave is <referencia guardada antes de importar el bloque de
   dispatch>` — confirma que el rebind NUNCA ocurre fuera de Windows.
5. Verificacion manual en Windows real: guardar una clave desde el panel (Ajustes), confirmar
   con `cmdkey /list` que aparece `Target: infobar:nan` (o la cuenta usada), recargar el panel
   y comprobar `clave_guardada: true` en la respuesta de `/ajustes`.

## Todo List
- [x] `infobar/credenciales_windows.py` con las 4 funciones + helpers
- [x] Bloque de dispatch en `infobar/claves.py`
- [x] `tests/test_credenciales_windows.py` (round-trip, validaciones, limpieza garantizada)
- [x] Test de identidad (no-rebind) en macOS/Linux
- [x] Verificacion manual del round-trip ctypes (guardar/leer/borrar contra el Credential Manager
      real, confirmado en fase 4). La parte de "panel web" (guardar desde Ajustes en el navegador)
      queda pendiente de una pasada interactiva del usuario, ver checklist de fase 4.

## Success Criteria
- `python -m unittest tests.test_credenciales_windows` pasa en Windows real.
- `python -m unittest discover tests` en macOS/Linux da el mismo resultado que antes de esta
  fase (mismo numero de tests, 0 fallos nuevos) — comparar con baseline antes de empezar.
- Guardar/leer/borrar una clave desde el panel web en Windows funciona de punta a punta
  (verificado con `cmdkey /list` y la respuesta JSON de `/ajustes`).

## Risk Assessment
| Riesgo | Likelihood | Impact | Mitigacion |
|---|---|---|---|
| Error de encoding/memoria en el `CREDENTIALW` (crash o datos corruptos) | Media | Media | Contrastar cada campo con `pywin32-ctypes` (codigo auditado); tests de round-trip reales, no mockeados |
| Tests dejan credenciales huerfanas en el Credential Manager real si fallan a medio camino | Media | Baja-media | `try/finally` obligatorio en el test; cuenta de prueba con prefijo identificable (`prov:cg-test`) para poder borrarla a mano si hace falta |
| `CRED_PERSIST_LOCAL_MACHINE` pide UAC en cuenta no-admin (no confirmado) | Baja | Media | Verificar en maquina real sin privilegios elevados antes de cerrar la fase; si pidiera UAC, probar `CRED_PERSIST_ENTERPRISE` como alternativa |
| Edicion de `claves.py` rompe algo en macOS por error humano | Baja | Alta | Bloque estrictamente al final del fichero, nunca reordenar; test de identidad dedicado; `git diff` del fichero revisado a mano antes de cerrar |

## Security Considerations
- Nunca loguear el valor de la clave (solo nombres de cuenta), igual que el modulo macOS.
- Mismo modelo de amenaza que Keychain: cualquier proceso del mismo usuario de Windows puede
  leer el Credential Manager (no defiende frente a malware ya corriendo con el mismo usuario;
  esto ya esta documentado como fuera de alcance en el plan de histórico SQLite).
- Validacion de caracteres de la clave **antes** de tocar la API de Windows (mismo orden que
  macOS: rechazar antes de llamar, no despues).

## Next Steps
- Fase 2 (bandeja) y fase 3 (autoarranque) no dependen de esta fase.
- Fase 4 incluye la regresion completa de ambos sistemas operativos.
