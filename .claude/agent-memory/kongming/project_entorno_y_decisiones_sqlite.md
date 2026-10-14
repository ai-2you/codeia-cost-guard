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
- No hay `cryptography` ni `pynacl` instalados; Python 3.14.6 con SQLite 3.53.4 en el sistema.

Decisiones del dueño cerradas (plan v2): export cifrado propio portable a Windows (no Time Machine), sombra de 7 días, IPs de atacantes 90 días, bajada ~40 % de cifras de Claude aceptada, cada fase pasa por pre-mortem + red team antes de codificar.

**Why:** los informes de red team/pre-mortem se encadenan por fases y cada ronda debe no repetir hallazgos; estos hechos evitan volver a medir.
**How to apply:** en consultas sobre fases 1-3 del plan SQLite, partir de `plans/reports/red-team-fase1-261014.md` y verificar si algo cambió antes de reutilizar estas cifras.
