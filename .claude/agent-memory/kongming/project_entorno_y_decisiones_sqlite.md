---
name: entorno-y-decisiones-sqlite
description: Hechos del entorno real (volumen EVO990 sin propiedad, instalación en ~/.cost-guard, formatos de ids de los logs) y decisiones del dueño para el plan SQLite, verificados el 2026-10-14
metadata:
  type: project
---

Hechos verificados el 2026-10-14 (no derivables del código):
- El checkout vive en el volumen externo EVO990 (APFS local, `Owners: Disabled`): ahí 0600/0700 no separan usuarios. La instalación real está en `~/.cost-guard` en el disco interno (0700).
- Logs reales: Claude Code y Codex llevan marcas `Z` al 100 %; un backend no-Anthropic usado desde Claude Code genera ids `msg_<ms>_<5-6 chars>`; OpenCode usa prefijo `msg_` también; `opencode-.db` está vacía (2 mensajes); Hermes `started_at` es epoch real.
- En 11 de ~30k mensajes de Claude la última línea acumula dos peticiones (caché ≈ ×2): la regla "el máximo gana" es la correcta.
- No hay `cryptography` ni `pynacl` en el Python 3.14.6 del proyecto (SQLite 3.53.4); **sí** hay `cryptography` 46.0.7 en `/usr/bin/python3` (3.9.6, site del usuario; ejecutar sin `-I`): sirve para comprobar la API. Medido: scrypt N=2^17 r=8 → 187 MiB/0,7 s, N=2^20 → 1,2 GiB/5,4 s; `BestAvailableEncryption` = PBKDF2 2.048 iteraciones; X25519 rechaza puntos de orden bajo; `hashlib.scrypt` no existe en el Python de Apple.
- Verificado 2026-10-14: un `-wal` huérfano de la base antigua se aplica sobre una base restaurada con `os.replace` y `quick_check` dice `ok` (bloqueante B1 de `plans/reports/revision-diseno-1-5-261014.md`).
- `~/.cost-guard/config.json` (2026-10-14): 13 claves en `proyectos` (12 ya con forma unificada `a/b/c`, 1 con prefijo `pi · `), 22 `aliases` (3 con `://`, ninguno con `?` o `@`). `~/.ccs/backups` tiene 65 ficheros pero `plan_ccs` es código muerto. La base `consumo.sqlite` instalada aún no existe. Claude Code borra logs a los 30 días (`cleanupPeriodDays: 30`).
- Revisión del diseño 1.3 en `plans/reports/revision-diseno-1-3-261014.md` (bloqueantes: sombra por agente/día y latido que caduca con la barra ociosa).

Decisiones del dueño cerradas (plan v2): export cifrado propio portable a Windows (no Time Machine), sombra de 7 días, IPs de atacantes 90 días, bajada ~40 % de cifras de Claude aceptada, cada fase pasa por pre-mortem + red team antes de codificar.

**Why:** los informes de red team/pre-mortem se encadenan por fases y cada ronda debe no repetir hallazgos; estos hechos evitan volver a medir.
**How to apply:** en consultas sobre fases 1-3 del plan SQLite, partir de `plans/reports/red-team-fase1-261014.md` y verificar si algo cambió antes de reutilizar estas cifras.
