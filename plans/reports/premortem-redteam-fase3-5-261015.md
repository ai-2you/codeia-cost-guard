# Pre-mortem y red team del diseño de avisos de servidores (fase 3.5)

Diseño revisado: `plans/261013-2350-sqlite-historico/fase3-5-avisos-diseno.md`. Contrastado con el código real (solo lectura). Rutas relativas a `infobar/`.
Severidad: CRÍTICA = el diseño no funciona o oculta un aviso grave tal cual está; ALTA = fallo probable en uso normal; MEDIA = ruido o hueco acotado; BAJA = endurecimiento.

## Hallazgos que cambian el diseño (por prioridad)

### H1 CRÍTICA. `avisos: false` en un servidor lo deja sin vigilar
- `servidores.py:11` (lista cerrada `CAMPOS_SERVIDOR`), `:102-104` (campo extra = error), `:176` (`servidores_validos` descarta el servidor entero).
- Escenario: el dueño apaga los avisos de un servidor de cliente; el campo `avisos` no existe en la lista, así que ese servidor deja de leerse por SSH, de sondearse y de aparecer. Parece un fallo del sistema y es justo el servidor con más riesgo de pasar desapercibido.
- Cambio: añadir `avisos` a `CAMPOS_SERVIDOR`, validar `bool` estricto en `errores_servidores` (como `detectar_webs`, `:105`) y comprobar que `normalizar`/Ajustes no lo pierden al guardar. `avisos_servidores` global: añadirlo en `ajustes.normalizar` con validación booleana, como `avisos_presupuesto` (`ajustes.py:134-137`); si no, la interfaz no puede escribirlo (`PASAN`, `:51`). Test: servidor con `avisos:false` sigue en `servidores_validos`.

### H2 ALTA. `certificado` se queda ciego por diseño
- `servidores_datos.py:76-82` y `:117`: `cert_dias_min` es NULL si UNA sola web https no dio fecha (caída, timeout, certificado inválido). Con 10 webs, una web caída oculta el certificado que caduca en 3 días de otra.
- `servidores_externo.py:79-90`: un certificado caducado o inválido no completa el handshake; `cert_dias` es `None` y el error empieza por «certificado». Nunca hay `cert_dias` negativo: la rama «(o caducado)» del diseño no se puede evaluar con `cert_dias_min`.
- `servidores_seguimiento.py:93`: `webs.cert_dias` se sobrescribe con NULL en cada sondeo fallido, y la tabla `webs` no guarda el texto del error.
- Cambio: evaluar por web con la tabla `webs` (`cert_dias` no nulo y `ultima` ≤ ventana), clave `certificado|servidor|web` con el `clave` hash de la web (nunca la URL), y el mínimo solo entre las que tienen dato. «Caducado/inválido» se cubre con `web_caida` (el sondeo da `caida`), así que el texto de `web_caida` debe ser neutro («no responde correctamente»), no «caída». Un servidor hostil que sirva un certificado de más de 100 años (`dias_de_caducidad` devuelve None) ya no enmascara las demás.

### H3 ALTA. `disco_lleno` y `certificado` leen `muestras`, que se descarta justo cuando hay ataque
- `servidores_datos.py:23` (`entero` lanza `FueraDeRango`), `servidores_historico.py:363` (muestra entera descartada; `ok_en` sí avanza, `:403`).
- Escenario: una oleada de fuerza bruta supera `intentos_24h` = 1 000 000, o el servidor manda un porcentaje > 100,5: no se guarda ninguna muestra, y disco/certificado quedan ciegos mientras dura. Un servidor hostil lo provoca a voluntad. Además `muestras` se limita a una fila por 270 s (`:367`) y se detiene una hora tras cualquier excepción (`servidores_registro.py:157-160`).
- Cambio: leer disco de la caché `servidores.json` (misma entrada que `muestra_de`), con parseo estricto propio (`porcentaje` de `servidores_datos`), exigiendo `ok_en` dentro de la ventana. Dejar la base solo para lo que solo ella tiene: estado confirmado y `desde` de webs, y eventos `reinicios_en_bucle`.

### H4 ALTA. «Base ausente o corrupta: sin avisos» silencia también la caída de un servidor
- `servidores_registro.py:136-160` (`no_antes` = 1 h) y `servidores_historico.py:349-403`. La caché (`servidores_ciclo.py:246-256`) funciona sin la base.
- Escenario: la base falla, un servidor se cae, y no llega nada porque `sin_conexion` también leía la base.
- Cambio: `sin_conexion` sale de la caché (`ok_en`, `intento_en`, `error`): además el texto del error (`ERRORES_SIN_REINTENTO`, `servidores_ciclo.py:26`) solo está ahí, no en la base. Si la base lleva ilegible más de 1 h, un único aviso genérico al día («El historial de servidores no se puede leer»), nunca silencio total.

### H5 ALTA. Se marca «avisado» aunque la notificación no se haya entregado
- `presupuestos.py:116-118` (anota y después notifica, ignora el resultado), `avisos.py:49-69`, `plataforma.py:84-98` (`avisar` devuelve False si osascript falla/caduca 5 s, y siempre en Windows).
- Escenario a los 3 meses: una actualización de macOS retira el permiso de notificaciones del origen de osascript, o hay un timeout; el estado dice «avisado» y no se reintenta. El dueño no puede saber que no llegan.
- Cambio: fijar `avisado` solo si `avisar()` devuelve True; si falla, reintentar el ciclo siguiente (máx. 3 intentos y 30 min, contados en el estado). En Windows/Linux (devuelve False por plataforma) marcar `avisado` con canal «visual» sin reintentar. Anotar `ultimo_aviso_ok` y `ultimo_aviso_fallo` y enseñarlo en Ajustes con un botón «Probar aviso». Hay que verificar a mano en un Mac real que el permiso existe (no verificable desde el código).

### H6 ALTA. Reactivar avisos disparará todo lo acumulado si se copia el patrón de presupuestos
- `presupuestos.py:111` sale antes de evaluar cuando el interruptor está apagado: no se actualiza nada. El criterio de aceptación 3 exige lo contrario.
- Cambio: con `avisos_servidores:false` o `avisos:false` en el servidor, el módulo evalúa igual y anota `avisado=ahora` para lo presente (modo silencioso), sin llamar a `avisar`. Test: apagar, provocar condición, encender, cero avisos.

### H7 ALTA. Sin conexión del propio Mac = tormenta de «un servidor no responde»
- `servidores_ssh.py:40-50` (errores `red`/`timeout`), `servidores_ciclo.py:182-186`. El sondeador de webs sí tiene canario (`webs_sondeo.py:101`), el ciclo SSH no.
- Escenario: Wi-Fi de hotel, VPN caída, cortafuegos; 15 min después todos los servidores «no responden». Es la causa más probable de que el dueño apague los avisos por ruido.
- Cambio: no evaluar `sin_conexion` si el canario `sin_red()` dice que no hay salida, o si TODOS los servidores con intento reciente fallan con error de red a la vez (sospechoso del Mac, no de los servidores). Mismo criterio para no contar el hueco como «libre» al rearmar.

### H8 ALTA. Ventanas fijas de 15 min con intervalos configurables
- `servidores_ciclo.py:34-43` (`intervalo_normal_s` ≥ 60, editable desde Ajustes: `ajustes.py:51`), `:267` (`estado_visible` usa 3 × intervalo).
- Escenario: con 30 min de intervalo, «`intento_en` ≤ 15 min» y «muestra ≤ 15 min» no se cumplen nunca: ninguna regla de servidor avisa jamás. Con 1 min, `sin_conexion` es más lento de lo debido.
- Cambio: ventana = `max(15 min, 3 × intervalo_normal + 60 s)`, la misma regla que usa `estado_visible`.

### H9 MEDIA-ALTA. Ruido y spam por rearme demasiado barato
- Diseño: `disco_lleno` rearma con «`disco_pct` < 85 en la última muestra» y avisa con una sola muestra ≥ 90; `web_caida` rearma a los 5 min de `ok`.
- Escenarios: un `docker build`/`prune` (el propio diseño los cita como picos) cruza 90 y baja; una app que reinicia cada 10 min; un servidor comprometido que alterna 95/50 por muestra. Cada ciclo = 1 aviso, hasta agotar las 6/hora.
- Cambio: (a) exigir duración también en `disco_lleno` (≥ 10 min seguidos con la condición) y en `certificado`; (b) rearme por `libre_desde` ≥ 30 min para disco/certificado, 10 min para web/conexión (el diseño ya define `libre_desde`, pero la tabla no lo usa para disco); (c) enfriamiento mínimo por clave tras avisar: web y conexión 1 h, disco y certificado 12 h; (d) el aviso de una situación que dura sigue siendo uno solo.

### H10 MEDIA-ALTA. El tope global de 6/hora se puede agotar con avisos de baja prioridad
- Diseño: tope global; «lo que excede se resume en la siguiente» (sin definir qué pasa con lo crítico).
- Escenario: un servidor hostil o inestable gasta las 6; la caída real de una web de otro servidor se retrasa hasta una hora.
- Cambio: tope por (servidor, regla) además del global; `web_caida` y `sin_conexion` con cupo reservado (p. ej. 4 de los 6); el excedente se guarda como `pendiente` en el estado con su propio `desde`, se reevalúa en el ciclo siguiente y solo se descarta si la condición ya no existe. Bucle: el «máximo 1 por servidor y hora» no puede perder el segundo evento; se queda pendiente hasta la hora siguiente o se fusiona en el texto del siguiente.

### H11 MEDIA. Detección de «evento nuevo» de `reinicios_en_bucle` frágil
- `servidores_recursos.py:136-139` (el `ts` es el `ok_en` del Mac, la PK es `(servidor_id, ts, clave, evento)`; la tabla es `WITHOUT ROWID`, no hay rowid). `ahora_srv`, `inicio` y `reinicios` los controla el servidor (`:135`): un servidor comprometido fabrica bucles a voluntad; `_evento` los limita a 10/día/clave y 100/día/tipo (`:47-62`).
- Cambio: marca de agua `ultimo_evento_ts` por servidor, recortada a `min(marca, ahora)` en cada ciclo (si no, un reloj adelantado deja la marca en el futuro y se pierden avisos hasta que el tiempo la alcance); solo eventos con `ts` ≥ ahora − 15 min (no resucitar eventos viejos tras despertar o perder el fichero). Con base nueva (`meta.creado` cambia) tratar como línea base.

### H12 MEDIA. Un rechazo de acceso SSH que antes funcionaba queda sin aviso para siempre
- Diseño excluye `autenticacion` y `clave de host no confiada` de `sin_conexion`; `servidores_ciclo.py:26,50,147-148` reintenta cada 30 min.
- Escenario: la clave del Mac desaparece del servidor (reinstalación, atacante, rotación) o cambia la huella (reinstalado o intermediario). Es justo lo que no debe pasar en silencio.
- Cambio: regla nueva y pequeña `acceso_rechazado`: transición de «`ok_en` reciente» a esos dos errores, un solo aviso, rearme cuando vuelva una lectura buena ≥ 10 min. Texto genérico («El acceso a uno de tus servidores ha cambiado»). Decisión del dueño si no la quiere en esta fase; el hueco debe quedar documentado.

### H13 MEDIA. Línea base y estado
- El «primer ciclo» debe definirse como «primer ciclo con al menos una fuente leída bien», con indicador persistente `linea_base`. Si no, un primer ciclo con la caché vacía deja pasar la línea base y el siguiente avisa de todo.
- Fichero corrupto frente a ausente: renombrar a `.corrupto`, empezar vacío y anotar como avisado solo lo que ya dura más de 1 h; lo reciente sí avisa (una ráfaga única, agrupada y acotada por el tope, es mejor que perder un crítico).
- Poda («lo que ya no existe en la configuración»): si `servidores.json` no se lee, `cfg()` puede devolver una lista vacía y la poda borraría todo el estado; al volver, tormenta. Podar solo con la lista leída bien y no vacía, y con 24 h de gracia.

### H14 MEDIA. Relojes y reposo
- `servidores_historico.py:433-435` ya recorta marcas futuras en la base, pero el fichero de avisos no. Un `avisado`/`desde`/marca de agua en el futuro (reloj adelantado y luego corregido) silencia esa clave hasta que el tiempo lo alcance; edades negativas pasan los filtros «≤ 15 min».
- Cambio: al leer, recortar todo timestamp a `ahora`; edad negativa = dato desconocido (ni avisa ni rearma). Detección de reposo con reloj de pared menos `time.monotonic()` (en macOS el monotónico no cuenta el reposo): si difieren más de 120 s, el ciclo solo actualiza estado. La regla del diseño («último ciclo bueno hace > 10 min») sirve mal: un ciclo largo con muchos servidores colgados (`servidores_ciclo.py:176`: 32 s por tanda) puede superar el umbral y silenciar todo de forma permanente.
- Tri-estado en todas las reglas: verdadero / falso / desconocido (base ocupada, `sin_red`, dato viejo, edad negativa). Desconocido no mueve `desde` ni `libre_desde`; sin esto, un hueco de datos rearma y vuelve a avisar.

### H15 MEDIA. Dónde engancharlo en el hilo
- `trabajador_barra.py:74-80` (vigilar de presupuestos va dentro del bloque del escaneo de logs, cada 60 s), `:92` (`ciclo` devuelve enseguida si nada toca: `servidores_ciclo.py:170-171`), `:101` (un `except` común con la pintura).
- Cambio: llamar después de `ciclo` y de `_pinta`, en su propio `try`, cada ≥ 60 s o cuando cambie `generado_en` (no «una vez por ciclo de servidores» de 5 min: las webs las escribe otro hilo cada 15 s-5 min, y `web_caida` tardaría hasta 10 min). El `osascript` (hasta 5 s) no debe retrasar el repintado.

### H16 BAJA-MEDIA. Dos procesos escritores
- `costbar.py:70` (`--demonio`) y `barra.py:149` instancian cada uno un `Trabajador`; no encontré guarda de instancia única (no verificado si `servir()` impide la segunda). Dos escritores de `avisos_servidores.json` duplican avisos y pierden estado.
- Cambio: cerrojo de fichero para leer-modificar-escribir (hay uno en `plataforma.py`, clase en la línea ~130) o aclarar que es proceso único.

## Red team: dato hostil, fichero y fugas

- Fichero de avisos: `avisos.leer` (`avisos.py:18-27`) hace `json.load` sin tope de tamaño. Copiar el patrón permite que un fichero enorme bloquee el hilo. Cambio: leer como mucho 256 KB; validar clave con regex (`regla|32 hex[|32 hex]`), números enteros finitos (sin `bool`, ni NaN, ni valores > ahora + 60), máximo de entradas ligado a servidores × webs; lo inválido se ignora entrada a entrada. Un atacante local que pueda escribir ahí ya puede editar la configuración: es endurecimiento, no una frontera.
- Texto sin nombres: correcto en el diseño. Añadir: el resumen agrupado puede nombrar CLASES de regla («2 avisos de servidores: web, disco»), que no son datos personales y devuelven utilidad (hoy «3 avisos de servidores» no dice nada; es una causa de ruido percibido). Decisión del dueño: un modo opcional con etiqueta corta del servidor (`avisos_nombres`, desactivado por defecto) frente al texto anónimo; no lo he decidido yo.
- Claves del estado: usar `servidores.id` (32 hex) y `webs.clave` (hash con sal); nunca la URL ni el nombre. Si la base se recrea, la sal cambia y todas las claves de web son nuevas: cubierto por «base nueva = línea base» (H11/H13).
- Logs: `trabajador_barra.py:80,102` escriben `traceback.format_exc()[-400:]`; un `KeyError` con una URL o un id dentro del módulo nuevo se filtraría al log. El módulo debe capturar todo dentro y registrar solo `type(e).__name__` (como `servidores_registro.py:160`), sin cola de traza.
- Servidor comprometido: no puede ocultar lo que mide el Mac (webs, accesibilidad SSH); sí puede mentir en disco, contenedores y bucles. Falsear a la baja es indetectable y no se puede arreglar desde aquí (documentarlo); falsear al alza queda acotado por H9/H10 (duración, enfriamiento, cupos).
- `osascript`: el texto va en el argumento (visible en `ps`); al ser constante y genérico no filtra nada.
- Concurrencia: lectura con `abrir(solo_lectura=True)` y espera de 2 s (`servidores_historico.py`, `abrir`); un `VACUUM` mensual o un escritor largo da «ocupada»: tratar como desconocido (H14), no como «libre». La configuración se lee una vez al principio del ciclo y se usa el mismo valor para evaluar y para podar.

## Descartado (no es real o ya está cubierto)
- Interferencia entre la base y los avisos de webs: los sondeos las escriben en transacciones cortas (`servidores_registro.py:116-129`) y WAL permite leer a la vez.
- `desde` de `webs` como «lleva ≥ 5 min caída»: es el instante de confirmación (segundo sondeo, `servidores_seguimiento.py:85-86`), no del primer fallo; el retraso real es ~45 s más, aceptable. Una web vista por primera vez ya caída no genera evento (línea base, `:72-74`) pero sí `desde` válido.
- Reloj adelantado en `webs`: `purgar` (`servidores_seguimiento.py:126`) ya trae `ultima`/`desde` al presente.
- `ok_en`/`intento_en` en la base se actualizan como supone el diseño (`servidores_historico.py:349-403`: `intento_en` en cada fallo, `ok_en` solo en lectura buena y con máximo monótono); el único fallo es de dependencia de la base (H4).
- `ok_en` > 0 como condición de `sin_conexion`: correcto; evita avisar de servidores recién dados de alta que nunca conectaron.
- Eventos `reinicios_en_bucle`: `ts` utilizable (`ok_en` del Mac); severidad `critica` correcta. Sin rowid, ver H11.
- Qué sistema de ficheros mide `disco_pct` (la orden remota no está en este repo): no verificado; si solo mide `/`, el volumen de datos de Docker puede llenarse sin aviso. Anotar como límite conocido.

## Cambios mínimos al diseño (lista de comprobación)
1. `avisos` en `CAMPOS_SERVIDOR` y validación; `avisos_servidores` en `ajustes.normalizar`. 2. Fuentes: caché para conexión y disco; base para webs y bucles. 3. Certificado por web con `clave` hash. 4. Duración y `libre_desde` en todas las reglas; enfriamiento por clave; cupos por regla. 5. `avisado` solo si `avisar()` devuelve True, con reintento acotado. 6. Modo silencioso con interruptor apagado. 7. Ventanas escaladas por `intervalo_normal_s`; tri-estado. 8. Canario de red del Mac. 9. Reloj: recorte a ahora, monotónico para el reposo, marca de agua de eventos. 10. Línea base bien definida, fichero corrupto y poda con gracia. 11. `try` propio tras `_pinta`, cada ≥ 60 s. 12. Un solo `log()` sin trazas. 13. Regla `acceso_rechazado` (o hueco documentado).

Status: DONE
