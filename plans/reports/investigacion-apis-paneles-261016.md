# Investigación: APIs de Dokploy y EasyPanel para un colector de solo lectura (2026-10-16)

Alcance: copias (última buena por BD/volumen), estado del último despliegue y árbol proyecto → servicio + dominios. Sin claves ni
servidores reales. Confianza: **[V]** verificado en docs oficiales, **[C]** visto en el código fuente oficial (Dokploy, `main` 53464e9 del
2026-10-15 y etiquetas v0.30.0/v0.30.7), **[T]** visto en código o documentación de terceros, **[S]** suposición.

## Conclusión (leer primero)
1. **Dokploy sin licencia Enterprise no tiene clave de solo lectura.** El rol `member` por defecto, sobre los servicios que tiene
   asignados, puede desplegar, parar y cancelar (`deployment: create/cancel`), leer y escribir variables (`envVars: read/write`), crear y
   borrar dominios y copias, y restaurar copias [C `packages/server/src/lib/access-control.ts` L176-215, igual en v0.30.7]. Los roles a
   medida (p. ej. todo en «Read») exigen licencia Enterprise [V docs.dokploy.com/docs/core/enterprise/custom-roles] [C `resolveRole`
   en `services/permission.ts`: sin licencia, un rol que no sea owner/admin/member es «Invalid role»]. **D-A tal como está escrita
   (member recortado + comprobación 403) solo se cumple con Enterprise.**
2. **EasyPanel no tiene roles ni ámbitos**: solo `admin: true/false` por usuario y acceso por proyecto (`updateAccess`). La clave «actúa
   como su usuario y puede modificar o borrar lo que ese usuario gestiona» [V easypanel.io/llms-full.txt, sección CLI y MCP]. Varios
   usuarios exige licencia [V misma fuente: «If your license supports multiple users»]; en el plan gratuito la única clave posible es la
   del administrador. **Ninguna clave de EasyPanel puede pasar una prueba de solo lectura.**
3. Recomendación priorizada: (a) Dokploy: usar la API **solo** si la clave supera la sonda de escritura de §1e (en la práctica, Enterprise
   con rol de solo lectura); si no, el dueño decide de forma explícita si acepta una clave `member` con permisos de escritura sobre sus
   servicios. (b) EasyPanel: **no** guardar clave de API mientras D-A exija solo lectura; obtener el estado por SSH/Docker (las fuentes
   locales de EasyPanel están sin investigar, ver «No verificado») o reabrir D-A con el dueño.
4. En ambos paneles, la **lista blanca de campos es obligatoria**: los listados que parecen inocuos traen secretos (Dokploy `project.all`
   trae `env`; EasyPanel `listProjectsAndServices` trae, según un SDK de terceros, `env`, `password`, `token`…).

## 1. Dokploy
**a) Clave.** Se crea en `/settings/profile` → API/CLI [V docs.dokploy.com/docs/api]; el miembro necesita el permiso «Access API/CLI»
para ver esa sección [V docs.dokploy.com/docs/core/permissions] [C `pages/dashboard/settings/profile.tsx`]. Cabecera **`x-api-key: <clave>`**
[V; C `packages/server/src/lib/auth.ts` `validateRequest`]. La clave queda ligada a una organización (`metadata.organizationId`) y hereda
el rol del usuario en ella [C]. Caducidad opcional: Never/1 d/7 d/30 d/90 d/1 año; límite de tasa por clave opcional (ventana de 1 min a 1 d,
desactivado por defecto en la UI) [C `components/dashboard/settings/api/add-api-key.tsx`]. Base: `https://<panel>/api/<router>.<proc>`,
las consultas van por `GET` y las mutaciones por `POST` con JSON (fork `@dokploy/trpc-openapi` 0.0.18) [V ejemplo `GET /api/project.all`; C].

**b) Endpoints recomendados (todos `GET`, sin parámetros, uno por ciclo):**
| Uso | Ruta | Permiso | Nota |
|---|---|---|---|
| Salud | `/api/settings.health` | público | `{status:"ok"}` [C] |
| Versión | `/api/settings.getDokployVersion` | autenticado | cadena `vX.Y.Z` [C] |
| Árbol + estado + último despliegue | `/api/overview.services` | `service:read` | [C `services/overview.ts`] |
| Dominios por app/compose | `/api/overview.domains` | `domain:read` | [C] |
| Ejecuciones de copias (BD y volumen) | `/api/overview.backups` | `backup:read` o `volumeBackup:read` | [C] |
| (Opcional) historial de despliegues | `/api/deployment.allCentralized` | `deployment:read` | ver tamaño en d) |

Para no-admin, todo se filtra por `accessedServices` del miembro; sin servicios asignados la respuesta es `[]` [C].

**c) Campos (lista blanca propuesta) y secretos.**
- `overview.services`: `id, name, appName, status (idle|running|done|error), createdAt, serverId, serverName, projectId, projectName,
  environmentId, environmentName, type (application|compose|postgres|mysql|mariadb|mongo|redis|libsql), lastDeployAt` (máximo de
  `deployment.createdAt`, solo para application/compose). **Excluir `icon`** (base64). `status` = estado del último despliegue (`error` si falló).
- `overview.domains`: `domainId, host, path, port, https, certificateType, enabled, domainType, serviceOwnerId, serviceOwnerType,
  serviceName, projectId, environmentId`. Sin secretos. `serviceOwnerId` enlaza la web con su app/compose.
- `overview.backups`: `deploymentId, kind (backup|volumeBackup), status (running|done|error|cancelled), createdAt, destinationId,
  destinationName, databaseType, serviceType, backupType, serviceName, serviceOwnerId, serviceOwnerType, serverId, projectId`. Sin secretos
  (el destino solo trae id y nombre). `serviceOwnerId=null` + `serviceOwnerType="web-server"` = copia del propio Dokploy.
- `deployment.allCentralized`: usar solo `deploymentId, status, createdAt, startedAt, finishedAt, applicationId, composeId`; **excluir
  `errorMessage`** (salida de error, puede llevar cadenas de conexión) [S], `title/description` (mensajes de commit), `logPath`, `pid`, `icon`.
- **No llamar nunca**: `project.all` (devuelve `env` del proyecto y de cada entorno) [C `routers/project.ts` + `schema/project.ts`];
  `application|postgres|mysql|mariadb|mongo|redis|compose.one` (env, `databasePassword`, credenciales de registro y de Git);
  `backup.one` (fila completa de la BD con contraseña; en compose `metadata.databasePassword`) [C `services/backup.ts`, `schema/backups.ts`];
  `deployment.readLogs`, `settings.*` de administración.
- **Copia sin hacer, sin leer configuraciones:** BD de `overview.services` sin ninguna ejecución `done` en `overview.backups` = «sin copia
  registrada» (sin programación, desactivada o nunca ejecutada). La configuración (`schedule`, `enabled`) solo sale por `backup.one` /
  `*.one`, que traen secretos: no merece la pena. La explicación de «2 de 6 BD» del plan es esta, no una lista corta: la consulta tiene
  `limit: 500` [C desde v0.30.0] y Dokploy guarda las 10 últimas ejecuciones por configuración (`removeLastTenDeployments`) [C].

**d) Tasa, paginación, tamaño.** Sin paginación en ningún `overview.*` ni `allCentralized` [C]. No hay límite de tasa global en la API
REST que se haya encontrado [C, búsqueda de `rateLimit`]; solo el opcional por clave. Cuerpo máximo de petición acotado
(`OPENAPI_MAX_JSON_BODY_SIZE`) [C]. Tamaño: `overview.services` crece ~10 KB por icono (lo midió la exploración real); `allCentralized`
repite el icono en cada despliegue (hasta 10 por app) y puede llegar a varios MB [S]: si se usa, poner tope de bytes al leer.

**e) Sonda de escritura inocua.** `POST /api/domain.generateDomain` con cuerpo `{}`. Es `withPermission("domain","create")`: el permiso se
comprueba en un middleware **antes** de validar la entrada [C `server/api/trpc.ts`]. Resultado: **401** = el rol no puede crear dominios
(buena señal); **400** = el rol tiene permiso de escritura (rechazar); incluso un 200 sería inocuo, porque solo calcula un nombre
`traefik.me` aleatorio sin escribir en la BD [C `services/domain.ts`]. Ojo: **Dokploy devuelve 401, no 403**, al denegar permisos
(`TRPCError UNAUTHORIZED`); y una clave inválida también da 401 (`{"message":"Unauthorized"}`), así que la sonda vale solo si antes
`overview.services` ha respondido 200 con la misma clave. Una `member` por defecto **fallará** la sonda (tiene `domain:create`), que es lo correcto.

**f) Versión mínima.** `overview.services/backups/domains` existen desde **v0.30.0** (2026-08-14) [C commit 046a608f, comparado con
etiquetas]; `allCentralized` ya existía en v0.29.14 [C]. Última estable: v0.30.8 (2026-09-29) [V releases de GitHub]. Versión: `settings.getDokployVersion`.

**g) Seguridad.** El panel nace en `http://IP:3000`: exigir dominio con HTTPS válido (D-D) porque la clave viaja en cabecera en cada
petición. El rol `admin/owner` ve toda la organización y puede leer todos los secretos con `*.one`: nunca usar esa clave (ni la del MCP).
`overview.backups` muestra a cualquier miembro las copias sin dueño (las del propio Dokploy) [C, comentario explícito]. Corrección reciente
de recorrido de rutas en ficheros de Traefik (PR #5070) [C]: mantener el panel actualizado.

## 2. EasyPanel
**a) Clave.** «Settings → Server → Users → Generate API Key» por usuario; un usuario tiene una clave y regenerarla o revocarla invalida la
anterior [V llms-full.txt]. Cabecera **`Authorization: Bearer <clave>`** [V easypanel.io/openapi.json, `securitySchemes.bearerAuth`]. No se
documenta caducidad [V ausencia]: permanente hasta revocar [S]. Roles: solo `admin` booleano (`createUser`/`updateUser`) más acceso por
proyecto (`updateAccess {userId, projectName, active}`) [V openapi.json]. Sin ámbitos ni solo lectura.

**b) Endpoints (API pública, `GET` con parámetros en la query; base `https://<panel>/api`)** [V openapi.json, `info.version` 2.32.2]:
| Uso | Ruta | Parámetros |
|---|---|---|
| Versión | `/api/getUpdateStatus` | — |
| Árbol proyectos + servicios con tipo | `/api/listProjectsAndServices` | — |
| Dominios | `/api/listDomains` | `projectName`, `serviceName` opcionales (sin ellos: todos los visibles) |
| Config. de copias de BD | `/api/listDatabaseBackups` | `projectName`, `serviceName` (por BD) |
| Config. de copias de volumen | `/api/listVolumeBackups` | `projectName`, `serviceName` (por servicio) |
| Últimas ejecuciones (despliegue, copia) | `/api/listActions` | `projectName`, `serviceName`, `type` ∈ {build, deployment, backup, volumeBackup, …}, `limit` (por defecto 8) |
Generaciones según un MCP de terceros: ≤2.30 tRPC `GET /api/trpc/<ns>.<proc>?input={"json":…}` con respuesta `{result:{data:{json}}}`;
2.31-2.32 `POST /api/rpc/<ns>/<proc>`; ≥2.33 la API pública plana [T github.com/helbertparanhos/easypanel-mcp-server `docs/easypanel-api.md`]
(choca con el `2.32.2` del spec oficial: averiguar por sondeo, `GET /api/getUpdateStatus` primero). El mismo tercero indica que `limit`
numérico en la query se rechaza (`Expected number, received string`) [T]: no mandar `limit` y usar el valor por defecto.

**c) Respuestas y secretos.** El spec oficial **no publica esquemas de respuesta** (todo es `anyOf: [{}, …]`) [V]. Forma de
`listProjectsAndServices`: `{projects:[{name, createdAt}], services:[{projectName, name, type, …}]}`, `type` ∈ app|compose|postgres|
mysql|mariadb|mongo|redis (también box y wordpress según el spec) [T, «confirmado en vivo v2.30.1»]. Un SDK comunitario de 2023 tipa cada
servicio con `env`, `password`, `rootPassword`, `token` (token de despliegue por webhook), `deploymentUrl`, `basicAuth`, `source.password`,
`mounts[].content` [T github.com/Easypanel-Community/easypanel `src/types/services.ts`]: **lista blanca `projectName, name, type, enabled`**.
Los `inspect*` devuelven variables y credenciales [V aviso de la doc MCP; T]. `getAction` trae el log de ejecución: no usar. Campos de
`listActions`, `listDomains` y `listDatabaseBackups`: **sin verificar** (terceros adivinan `status`, `type`, `createdAt`, `startedAt`).

**d) Tasa, paginación, tamaño.** No documentados [V ausencia]. Sin paginación; `listActions` limitado por `limit`. Las copias y despliegues
exigen N llamadas (una por servicio), no 1 como en Dokploy.

**e) Sonda de escritura.** No hay ninguna verificable como inocua y, además, no tendría sentido: ningún usuario es de solo lectura. Lo que
sí se puede comprobar sin escribir: `GET /api/getUser` devuelve el usuario sin contraseña [V]; si `admin` es `true`, rechazar la clave [S
sobre el nombre exacto del campo].

**f) Versión mínima.** API pública plana: 2.32.2-2.33 (ver b). Copias programadas de BD requieren licencia que las incluya [V doc de
copias]. Versión: `GET /api/getUpdateStatus` (≤2.30: `update.getStatus` por tRPC) [V/T].

**g) Seguridad.** Una clave filtrada permite desplegar, cambiar variables, borrar proyectos o reiniciar el servidor (`server/reboot`) [V
spec]. El mismo tercero avisa de que `inspectService` devuelve las variables «de cualquier proyecto» con clave de admin [T]. Exigir HTTPS
válido. La URL de MCP admite la clave en la ruta (`/api/mcp/<clave>`): no usar esa forma.

## No verificado (y por qué importa)
- Sin pruebas contra paneles reales (por diseño). El orden «permiso antes de validar la entrada» de la sonda de Dokploy sale del código,
  no se ha ejecutado: probarlo una vez contra un Dokploy de prueba propio antes de dar de alta.
- Esquemas reales de respuesta de EasyPanel (`listActions`, `listDomains`, `listDatabaseBackups`), límites de tasa y el estado exacto de un
  despliegue o una copia: imprescindible capturar una respuesta real (redactada) antes de escribir la lista blanca.
- Qué usuarios y roles permite cada plan de pago de EasyPanel (la página de precios no lo dice) y si un no-admin puede desplegar en sus
  proyectos (lo da a entender la doc, no lo prueba).
- Alternativa local para EasyPanel (estado en disco bajo `/etc/easypanel`, etiquetas de servicios Swarm, configuración de Traefik): no
  investigada; es el camino si se mantiene D-A.
- Dokploy en modo nube y servidores remotos (`serverId`): mismas rutas, no revisado en detalle.

## Preguntas abiertas para el dueño
1. ¿Hay licencia Enterprise de Dokploy? Si no la hay, ¿se acepta una clave `member` que puede desplegar, parar o cambiar variables en los
   servicios asignados, o se queda Dokploy por SSH?
2. EasyPanel: ¿se renuncia a la API (D-A estricta) o se acepta una clave de un usuario no-admin dedicado, si la licencia lo permite?
