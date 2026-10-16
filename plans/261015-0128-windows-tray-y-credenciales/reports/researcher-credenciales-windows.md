# Research: almacen de credenciales Windows para `infobar/claves.py`

Fecha: 2026-10-15. Alcance: comparar 3 vias para implementar el mismo contrato de
`claves.py` (`hay_llavero`, `cuenta_valida`, `leer_clave`, `guardar_clave`, `borrar_clave`)
en un modulo Windows-only nuevo, sin tocar el actual. Metodologia: PyPI JSON API +
GitHub API (`gh api`) para metadatos de paquete/mantenimiento, Microsoft Learn oficial
para la API Win32, y codigo fuente real de `pywin32-ctypes` (dependencia que usa
`keyring` en Windows) como referencia cruzada de implementacion.

## Contrato a cumplir (fijo, no se toca)

```python
SERVICIO = "infobar"
def hay_llavero() -> bool
def cuenta_valida(cuenta: str) -> bool        # reutilizable tal cual, es puro regex
def leer_clave(cuenta: str) -> str            # "" si no existe / no hay almacen
def guardar_clave(cuenta, valor=None) -> bool # RuntimeError/ValueError si falla
def borrar_clave(cuenta) -> None              # no falla si no existia
```

`cuenta_valida` no depende del SO, se puede reusar o duplicar literal (es una linea).
El resto hay que implementarlo contra Windows Credential Manager.

## Opcion A: libreria `keyring`

**Version y mantenimiento** (PyPI JSON + GitHub API, `jaraco/keyring`):
- Ultima version publicada: **25.7.0** (2025-11-16). `requires_python: >=3.9` (compatible,
  repo pide 3.9+, entorno real tiene 3.14.2).
- Repo: 1514 stars, **117 issues abiertas**, ultimo push 2026-04-13 — sin tag nuevo desde
  nov-2025 pero con actividad de commits ~11 meses despues del ultimo release. No abandonado
  (jaraco mantiene docenas de libs de infraestructura Python de uso masivo, pip/twine lo usan),
  pero el ritmo de releases se ha enfriado este ultimo año.
- Backend Windows: `keyring.backends.Windows` depende de **`pywin32-ctypes>=0.2.0`**, NO de
  `pywin32`. Es un detalle importante que el brief no anticipaba: `pywin32-ctypes` es una
  reimplementacion ctypes/cffi de la superficie minima de pywin32 (incl. `win32cred`), **pura
  Python, sin dependencias, wheel de 30 KB**. O sea, si se opta por keyring, el "peso" real en
  Windows es keyring + pywin32-ctypes (ambos ligeros), no pywin32 completo.
  - Pero `pywin32-ctypes` en si mismo es un proyecto pequeño y con poca señal de mantenimiento
    propio: 23 stars, 14 issues, ultimo push 2025-06, ultimo release v0.2.3 de 2024-08-14 (2+
    años). Baja visibilidad pero uso indirecto enorme (dependencia transitiva de `pip` y
    `keyring`), lo que en la practica lo hace fiable pese a las metricas de actividad bajas —
    la superficie de la API Win32 que envuelve es estable y no necesita cambios frecuentes.
- Backend macOS: llama directamente a `Security.framework` via `ctypes.CDLL` (no usa pyobjc).
  Backend Linux: necesita `SecretStorage>=3.2` + `jeepney>=0.4.2` (dbus).
- Dependencias propias (cualquier SO): `jaraco.classes`, `jaraco.functools`, `jaraco.context`
  (+ `importlib_metadata` solo en Python <3.12).

**Riesgos si se usara para sustituir TODO `claves.py` (nota para el plan, no implementar ya)**:
- Compatibilidad con el `security` CLI actual en macOS: ambos acceden al mismo
  `login.keychain` via la misma Security framework subyacente (keyring via ctypes directo,
  `security` via el binario de Apple) — a nivel de almacenamiento son compatibles (mismo
  servicio/cuenta = mismo item de Keychain), pero la API de `keyring.get_password()` devuelve
  `None` en vez de `""`, y no valida el formato de cuenta — la capa de adaptacion en
  `claves.py` seguiria siendo necesaria igualmente.
  - El API usada (`SecKeychainAddGenericPassword`) es legacy pero sigue funcional; no hay
    evidencia encontrada de que requiera foco de UI para el item generico (eso aplica mas a
    flujos de Keychain Access / prompts de "quiere permitir acceso", que dependen de firma de
    codigo y ACL del item, no del backend elegido) — **queda como pregunta abierta**, no
    verificable sin probarlo en una Mac real con la app firmada/sin firmar.
- Pulls transitivos extra (jaraco.* , SecretStorage+jeepney en Linux) que el motor actual
  (stdlib-only) no tiene en ningun SO.

**Peso para SOLO Windows**: keyring + pywin32-ctypes + jaraco.classes/functools/context — 5
paquetes nuevos, todos pequeños y puros Python, pero son 5 nombres nuevos en el arbol de
dependencias para resolver 3 funciones (leer/guardar/borrar). Rompe la filosofia "motor sin
dependencias" del repo aunque el coste en MB sea bajo.

## Opcion B: `ctypes` directo contra `advapi32.dll` (CredWriteW/CredReadW/CredDeleteW)

**100% stdlib** confirmado: `ctypes` y `ctypes.wintypes` vienen con CPython, cero
dependencias nuevas — encaja exacto con "el motor funciona solo con stdlib".

**Estructura `CREDENTIALW`** (Microsoft Learn, `ns-wincred-credentialw`, oficial):
```c
typedef struct _CREDENTIALW {
  DWORD   Flags;
  DWORD   Type;                 // CRED_TYPE_GENERIC = 1
  LPWSTR  TargetName;
  LPWSTR  Comment;
  FILETIME LastWritten;
  DWORD   CredentialBlobSize;   // bytes, no caracteres
  LPBYTE  CredentialBlob;
  DWORD   Persist;              // CRED_PERSIST_LOCAL_MACHINE = 2
  DWORD   AttributeCount;
  PCREDENTIAL_ATTRIBUTEW Attributes;
  LPWSTR  TargetAlias;
  LPWSTR  UserName;
} CREDENTIALW;
```
- `CredWriteW(PCREDENTIALW, DWORD Flags) -> BOOL`, de `Advapi32.dll`. Falla -> `FALSE` +
  `GetLastError()`.
- `CredReadW(LPCWSTR TargetName, DWORD Type, DWORD Flags, PCREDENTIALW* Credential) -> BOOL`.
  El puntero devuelto **hay que liberarlo con `CredFree(PCREDENTIAL)`** tras copiar los datos
  (igual que hace `pywin32-ctypes`, confirmado en su codigo fuente).
- `CredDeleteW(LPCWSTR TargetName, DWORD Type, DWORD Flags) -> BOOL`.

**Limite de tamaño — CORRECCION a lo que suele circular en foros/SO**: el brief apuntaba a
"512 bytes en versiones viejas, mas en Win10/11, confirma el limite actual". La doc oficial
vigente de Microsoft Learn (`ns-wincred-credentialw`, actualizada 2025-11-25) dice
textualmente: *"This member cannot be larger than CRED_MAX_CREDENTIAL_BLOB_SIZE (5\*512)
bytes"* — es decir **2560 bytes**, no 512. El numero 512 que aparece en varias fuentes de
terceros (incluida una busqueda generica) es el limite antiguo pre-Vista; la constante actual
multiplicada por 5 es la vigente desde hace años y aplica igual en Win10/11. Si el blob se
guarda como UTF-16 (ver abajo), son ~1280 caracteres utilizables — de sobra para cualquier
API key real.

**Encoding** (confirmado leyendo el codigo real de `pywin32-ctypes`, que es la referencia mas
auditada que existe de esto en Python puro):
- Los campos string (`TargetName`, `UserName`, etc.) son `c_wchar_p` — ctypes marshalla la
  `str` de Python a UTF-16 automaticamente, sin que el programador toque bytes. Ojo: ctypes
  mantiene la referencia al buffer mientras el objeto `Structure` viva, pero si se construye
  todo en una expresion inline sin guardar la `str`/buffer en una variable local, el GC puede
  liberar el buffer antes de la llamada — gotcha real, hay que asignar a variables locales.
- `CredentialBlob` (el secreto) **no es la forma directa de un `c_wchar_p`**: la implementacion
  de referencia construye un `ctypes.create_unicode_buffer(valor)`, lo castea a `LPBYTE`, y
  pone `CredentialBlobSize = sizeof(buffer) - sizeof(wchar)` (resta el terminador nulo). Es
  decir, el secreto se guarda como UTF-16LE sin null final, en bytes.
- Para leer: tras `CredReadW`, castear `CredentialBlob` (puntero) + `CredentialBlobSize` de
  vuelta a `c_char_p`/bytes y decodificar como UTF-16, luego `CredFree`.

**Errores**: patron estandar — si la llamada devuelve `FALSE`, `raise ctypes.WinError()` (sin
argumentos, en Windows llama a `GetLastError()` internamente y da el mensaje de sistema
correcto). No hace falta `ctypes.get_last_error()` (eso es solo si se carga la DLL con
`use_last_error=True` via `WinDLL`, no es necesario aqui).

**Gotcha critico de ctypes+win32 (el mas comun en foros)**: hay que declarar explicitamente
`.argtypes` y `.restype` de cada funcion ANTES de llamarla (`CredWriteW.restype = wintypes.BOOL`,
etc.) — si no, ctypes asume `int` de retorno y puede truncar punteros en 64-bit, causando
crashes silenciosos. No hay que preocuparse de 32 vs 64-bit mas alla de esto: ctypes ajusta el
tamaño de puntero automaticamente al interprete que corre, siempre que los tipos se declaren
con `ctypes.wintypes` (no hace falta rama de codigo por bitness).

**Codigo de referencia fiable encontrado**: el repo `enthought/pywin32-ctypes`
(`win32ctypes/pywin32/win32cred.py` + `win32ctypes/core/ctypes/_authentication.py`) es la
implementacion mas auditada en Python puro de esto — es dependencia transitiva de `pip` y
`keyring`, MIT, ~10 años en produccion. Sirve como oraculo para contrastar la implementacion
propuesta sin añadirla como dependencia (solo leer su codigo).

**Lineas de codigo estimadas**: la implementacion de referencia divide esto en
estructura+conversion (~80 lineas) y wrapper publico (~60 lineas), pero incluye ramas `cffi`
y `CredEnumerate` que aqui no hacen falta. Para las 3 funciones minimas
(`leer_clave`/`guardar_clave`/`borrar_clave` + helper de estructura + helper de encoding),
estimo **110-150 lineas** en un solo fichero — queda holgado bajo el limite de 200
lineas del repo.

## Opcion C: `pywin32` (`win32cred`)

**Version y mantenimiento** (GitHub API `mhammond/pywin32`, verificado):
- Ultima release: **b312**, 2026-06-04. Historial: b311 (2025-07-14), b310 (2025-03-17), b309
  (2025-03-09), b308 (2024-10-12). Repo muy activo: 5621 stars, 391 issues abiertas, push mas
  reciente 2026-10-07. Es EL paquete de facto para Win32 API en Python, con 15+ años de uso.
- `requires_python >= 3.9`. Wheels disponibles para **cp39 a cp315** win32/win_amd64/win_arm64
  (incluye cp314 — el entorno real del repo corre 3.14.2, sin problema de compatibilidad).
- **Tamaño real verificado** (PyPI JSON, no estimado): wheel `win_amd64` para cp314 =
  **7.02 MB** comprimido (el resto de versiones de Python: 6.9-7.0 MB). El brief preguntaba
  por "~15-40 MB" — el wheel descargado es ~7 MB; instalado en disco (con los .pyd
  descomprimidos: `win32`, `win32com`, `win32comext`, `isapi`, `adodbapi`, Pythonwin, etc.)
  puede acercarse a 15-25 MB, pero el numero de descarga/paquete es bastante menor de lo
  temido.
- `win32cred.CredWrite`/`CredRead`/`CredDelete`: API ya orientada a diccionario Python (recibe
  y devuelve `dict`), evita toda la gestion manual de la estructura C — estimo **20-30 lineas**
  para las 3 funciones + mapeo de excepciones (`pywintypes.error` -> `RuntimeError`), muy por
  debajo de la Opcion B.
- **Postinstall script**: `pywin32_postinstall.py` existe y se recomienda tras upgrades, pero
  es para registrar COM servers / Pythonwin / integracion de shell. Para uso puntual de un
  modulo como `win32cred` (sin COM, sin servicios), no he encontrado evidencia de que sea un
  requisito — los `.pyd` quedan instalados y funcionales por el propio wheel de pip. Confianza
  media-alta, no 100% verificado en maquina limpia.

## Tabla comparativa

| Criterio | A: `keyring` | B: `ctypes` stdlib | C: `pywin32` (`win32cred`) |
|---|---|---|---|
| Dependencias nuevas | keyring + pywin32-ctypes + 3x jaraco.* (5 paquetes, todos puros Python y ligeros) | **Ninguna** (stdlib) | 1 paquete (pywin32), ~7 MB wheel, con extensiones compiladas |
| Lineas de codigo estimadas (modulo nuevo) | ~20-30 (solo wrapping de API ya generica) | ~110-150 (estructura C + encoding manual) | ~20-30 (API ya en forma de dict) |
| Mantenimiento/adopcion | Activo pero release mas lento (ult. tag nov-2025); 1514★, muy usado en el ecosistema (pip, twine, etc.) | N/A (es ctypes del propio CPython, garantizado mientras exista Python) | Muy activo (ult. release jun-2026), 5621★, el estandar de facto Win32+Python |
| Riesgo de abandono | Bajo-medio (proyecto maduro, pero cadencia mas lenta este año) | Nulo | Muy bajo |
| Peso/instalacion | Ligero en bytes, pesado en "nombres de paquete" (5 nuevos en el arbol) | Cero | Medio (~7 MB descarga, extensiones .pyd, pero wheels precompilados para toda version de Python soportada incl. 3.14) |
| Encaja con filosofia "motor sin dependencias" del repo | No (añade 5 paquetes a un motor que hoy es 100% stdlib) | **Si, perfecto** | No (añade 1 dependencia pesada en MB) |
| Complejidad/riesgo de implementacion propia | Baja (API ya abstrae todo) | Media-alta (hay que acertar encoding UTF-16 del blob, free de memoria, argtypes/restype) — pero existe codigo de referencia auditado para contrastar | Baja (API ya en dict, gestion de memoria la hace pywin32) |
| Limite de tamaño de clave | El que imponga el backend (mismo CRED_MAX_CREDENTIAL_BLOB_SIZE por debajo) | 2560 bytes (~1280 chars UTF-16) — confirmado en doc oficial vigente, no 512 | Igual que B, mismo limite de API subyacente |

## Recomendacion

**Opcion B (`ctypes` directo contra `advapi32.dll`)**, con `pywin32-ctypes` (su codigo fuente,
no como dependencia instalada) como referencia de contraste durante la implementacion.

Justificacion, en orden de peso:
1. El repo declara explicitamente filosofia "motor sin dependencias pesadas" (`CONTRIBUTING.md`:
   *"Lo que probablemente no encaje: dependencias nuevas pesadas"*) y hoy `analizar_gasto.py`/
   `panel.py` funcionan con stdlib puro en Windows/Linux. Añadir keyring (A) o pywin32 (C) para
   resolver 3 funciones de ~20 lineas de API rompe esa invariante sin necesidad real.
2. El coste de la Opcion B no es trivial (110-150 lineas vs 20-30), pero es acotado, cae dentro
   del limite de 200 lineas/fichero del repo, y **existe codigo de referencia auditado y
   probado en produccion** (`pywin32-ctypes`, usado transitivamente por `pip`) para contrastar
   cada detalle (estructura, encoding, limite de tamaño, gestion de memoria) sin necesidad de
   instalarlo.
3. Riesgo de mantenimiento es el minimo posible: no depende de ningun proyecto de terceros,
   solo de la API Win32 estable (CredWriteW existe desde Windows XP, sin cambios de contrato
   desde entonces segun Microsoft Learn).
4. Opcion C (pywin32) seria la alternativa si en el futuro el equipo decide que SI quiere una
   dependencia pesada a cambio de -80 lineas de codigo — es la segunda opcion mas razonable,
   nunca Opcion A para este caso concreto (Windows-only), porque A trae mas paquetes nuevos
   que C para el mismo problema, y su ventaja (API unificada entre SOs) no aplica aqui porque
   la decision de arquitectura ya fijo dispatch por plataforma sin tocar `claves.py`.

**Cuando SI reconsiderar A**: si en algun momento se decide consolidar los 3 backends (macOS +
Windows + Linux) en un solo modulo cross-platform en vez de mantener 3 implementaciones nativas
separadas — ahi keyring gana peso porque evita mantener 3 integraciones nativas. Eso es una
decision de arquitectura distinta a la de este ticket (Windows-only) y debe quedar en el plan
como "decision a confirmar", no resuelta aqui.

## Preguntas sin resolver (para dejar en el plan)

1. Si keyring sustituyera el backend macOS, ¿el primer acceso a un item recien creado dispara
   un dialogo de permiso de macOS que requiera foco de UI? No verificable sin probarlo en una
   Mac real con la app firmada — no encontre evidencia concluyente en la documentacion de
   `keyring` ni de Apple sobre esto especifico para `SecKeychainAddGenericPassword` via proceso
   en background.
2. `pywin32_postinstall.py`: confianza media-alta (no verificada en maquina limpia) en que NO
   hace falta para uso puntual de `win32cred` sin registro COM — solo relevante si el plan
   finalmente elige Opcion C.
3. No se ha probado en una maquina Windows real si Credential Manager pide elevacion/UAC para
   `CRED_PERSIST_LOCAL_MACHINE` en cuentas no-admin (la doc de Microsoft no lo menciona como
   requisito, pero no hay confirmacion empirica en este research).
