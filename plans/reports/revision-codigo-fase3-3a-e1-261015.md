# Revisión de código independiente — Fase 3.3a, entrega E1 (commit 639a960)

Fecha: 2026-10-15. Alcance: `git diff 639a960~1 639a960` (12 ficheros, +778/−60) contra `fase3-3a-diseno.md` §E1 y los informes de pre-mortem y red team.
Modo: solo lectura. El único fichero creado en el repo es este informe; las pruebas se hicieron en una copia (`git archive`) en el scratchpad.

## Veredicto

E1 está bien encaminada. El parser no se deja reabrir, el marco funciona, el filtro por `_SYSTEMD_UNIT` es el correcto, la migración 2 es sólida
y el Mac no se puede agotar (128 KiB, 25 cubos exactos, 12 IPs, enteros acotados). **No recomiendo fusionar todavía** por tres hallazgos altos:

- el vigilante no cumple el tope de tiempo;
- un atacante remoto sin autenticar puede ocultar sus intentos eligiendo el nombre de usuario;
- el presupuesto nuevo de `recoger-root` produce ceros y alarmas falsas que no se distinguen de datos buenos.

Además, la pasada de mutaciones deja vivas 8 de 10 mutaciones nuevas (los 2 controles sí mueren). La afirmación «solo sobrevive una equivalente»
no se sostiene para estas zonas.

Tests: `test_servidores_v2`, `test_servidores` y `test_servidores_historico` dan **100 tests OK** en macOS (bash 3.2.57, `timeout` de Homebrew) [V].

## Crítico

Ninguno.

## Alto

### A1. El vigilante mata solo al shell, no a sus hijos: la conexión sigue abierta y el Mac pierde la lectura entera [V mecanismo, S tiempos]
- **Evidencia:** en `infobar/remoto/recoger.sh:11` se hace `kill -TERM $$`. Los hijos (`timeout 12 sudo …` en la :104, `timeout 6 journalctl` en la :50) quedan
  huérfanos con el stdout del canal SSH abierto, y sshd no cierra el canal hasta el EOF.
- **Prueba local:** un script con un vigilante de 1 s y un hijo `timeout 4 sh -c 'sleep 3; echo "### fail2ban"…'`. El shell muere a 1 s (rc −15),
  pero la tubería se cierra a los **3,0 s** y la salida del huérfano llega **después** de la muerte del shell.
- **Escenario:** un servidor atacado. Las tres llamadas a journalctl (5+3+6 s) y `apt list` dos veces (hasta 5+5 s) superan los 14 s antes del `sudo`.
  El vigilante dispara a los 14 s y el `sudo` sigue hasta 12 s más, en total ~26 s, por encima de `TOPE_S`=20. `ejecutar()`
  (`servidores_ssh.py:102-103`) devuelve entonces `"", "timeout"` y se pierde la lectura entera justo cuando más interesa.
- **Fallo del test:** `PresupuestoDeTiempoTest.test_los_topes_encajan` comprueba root < sudo < vigilante ≤ TOPE_S−5. Pero el invariante real es
  «momento en que se cierra el último escritor ≤ TOPE_S − conexión», y no lo comprueba.
- **Cambio propuesto:**
  1. Dar al `sudo` el tiempo que quede, no un fijo: `resta=$(( 13 - ($(date +%s) - INICIO) )); [ $resta -gt 1 ] && timeout -k 1 $resta sudo -n …`.
  2. Aplicar el mismo presupuesto por comando que en root (`t` que no arranca pasado un límite) a los comandos del usuario.
  3. En el vigilante, matar al grupo: `kill -TERM -- -$$ 2>/dev/null || kill -TERM $$`. El script es líder de sesión con `command=`; el `sudo` de root
     recibe la señal a través de `timeout`.
  4. Añadir un test que ejecute el script con un `apt`/`journalctl` falsos lentos y mida el cierre de stdout.

### A2. Evasión e inflado por nombre de usuario: el awk busca subcadenas en la línea entera, incluido el nombre que elige el atacante [V awk, S formato real de sshd]
- **Evidencia:** en `recoger.sh:53-54` la expresión canónica y el `next` por `/Failed password for invalid user/` se aplican a `$0`.
- **Prueba local:** se extrajo el programa awk del script y se ejecutó con líneas simuladas.
  - Con el usuario `Failed password for invalid user`, las tres líneas de un intento (`Invalid user …`, `Failed password for invalid user …` y
    `Disconnected from invalid user …`) dan **total 0, ninguna IP**.
  - Con el usuario `Invalid user q from`, un solo intento cuenta **3** (también entran las líneas `Connection closed by invalid user` y
    `Disconnected from invalid user`).
- **Escenario:** un atacante remoto sin autenticar se vuelve invisible en la tarjeta y en el historial, o infla la cuenta ×3. OpenSSH escapa los
  caracteres de control con `strnvis`, pero no los espacios, así que el nombre sale tal cual [S]. Ya pasaba en v1, pero contradice el objetivo
  número 1 de la 3.3a («intentos fiables»).
- **Cambio propuesto:** anclar al mensaje. En `short-unix` el mensaje empieza tras el tercer campo (`TS HOST IDENT[PID]: `).
  - Quitar el prefijo: `msg = $0; sub(/^[^ ]+ [^ ]+ [^ ]+ /, "", msg)`.
  - Comparar con `msg ~ /^(Invalid user |Failed password for |Connection closed by authenticating user |Disconnected from authenticating user |error: maximum authentication attempts)/`.
  - Excluir con `msg ~ /^Failed password for invalid user /`.
  - Añadir un test con estos dos nombres de usuario.

### A3. El presupuesto nuevo de `recoger-root` convierte «no dio tiempo» en «0» y en alarmas falsas, y la lectura sigue saliendo «completa» [V]
- **Evidencia:** en `recoger-root.sh:11`, pasados 7 s `t` devuelve 1 sin salida. Las secciones salen vacías pero presentes.
- **Prueba local:** una copia del script con `docker`, `sshd`, `ufw`, `iptables` y `systemctl` falsos de 2,9 s. La salida tiene `### fail2ban` vacía,
  `fail2ban_activo ` vacío, `input_politica ` vacío, sin `sshd_*` y `### servicios` vacía.
- **Efecto en el Mac:**
  - `fail2ban` sale `{"instalado": True, "ahora": 0, "total": 0}` (`servidores_ssh.py:238-241`): «0 bloqueadas» en lugar de «sin dato».
  - La comprobación `fail2ban` y la de política de entrada fallan (`servidores_estado.py:88-91`): alarma ámbar falsa.
  - Los servicios desaparecen.
  - Como el script imprime `### fin` después, `completa` es True.
  - Esto lo introduce este commit: en v1 cada comando tenía su `timeout 3` y no había presupuesto global.
- **Cambio propuesto:**
  - Que `t` imprima una marca cuando se salta un comando, por ejemplo `echo "omitido" >&3` o una línea `presupuesto_agotado si` al final de root.
  - Que el Mac trate como ausentes las secciones afectadas (sin `fail2ban`, sin `bastionado_root`), igual que trata «sin root».
  - Que root termine con su propio cierre (`### fin_root`), para que `completa` cubra también su salida.

## Medio

### M1. `### cortada` deja pasar la sección v1 que estaba abierta, con su última línea partida [V]
- **Evidencia:** en `servidores_ssh.py:134-135`, `cortada` solo pone `actual=None`. Con `"### disco\n1000000000 9\n### cortada"` sale
  `disco {'total': 1000000000, 'usado': 9}`: un número truncado aceptado como bueno.
- **Riesgo:** el corte de 128 KiB solo llega en la parte de root (`contenedores`/`servicios`), o con un servidor hostil. Aun así es una regresión
  respecto a v1, que descartaba la lectura entera, y el diseño dice «descarta solo las secciones nuevas», no «acepta la que se cortó».
- **Cambio:** al ver `cortada`, si había una sección abierta, `out.pop(actual)` y añadirla a `invalidas`. Añadir un test.

### M2. La ventana del journal no coincide con los cubos y las líneas posteriores a `ahora` descartan el histograma [V awk]
- **Primer caso:** `ahora` se toma en `recoger.sh:42`, hasta ~8 s antes del journalctl principal, y `--since "-25h"` es relativo al reloj de
  journalctl. Si `ahora` cae en los últimos segundos de la hora, una línea escrita entre `ahora` y la lectura (`ts ≤ ahora+60`) cuenta en `total`
  y en `hora`, pero va al cubo `act+1`, que no se imprime. Prueba local: total 2 y Σh 1, así que el Mac da `histograma_incoherente`
  (`servidores_intentos.py:62`). En un servidor con ataque continuo pasa a cada cambio de hora.
- **Segundo caso:** el primer cubo empieza ≤ 1 s después de `ahora−25h`. Si journalctl arranca unos segundos tarde, sus primeros segundos no se
  leen, el cubo se marca completo y se congela así.
- **Cambio:** `--since "@$(( (ahora/3600 - 24) * 3600 ))" --until "@$ahora"` y, en awk, `if (ts > ahora) next`. Así coinciden ventana, cubos y total.

### M3. Un `parcial` pierde las líneas más RECIENTES: `hora` baja a ~0 justo con un ataque fuerte y la tarjeta queda verde [V lectura, S volumen]
- **Evidencia:** journalctl escribe en orden cronológico y el `timeout 6` corta por el final, que son las líneas recientes. `hora` y `total` se
  muestran y se guardan en `muestras` aunque venga `parcial si`. La tarjeta (`servidores_estado.py:44-48`) compara `hora` con la media y sale verde.
- **Cambio:**
  - Leer con `journalctl -r`, de más nueva a más vieja, para que un corte pierda lo más antiguo.
  - O hacer una lectura aparte, corta, de la última hora.
  - En el Mac, con `parcial`, mostrar «≥ N» y no alimentar la regla de estado.

### M4. El histograma se guarda aunque no haya origen fiable y aunque la lectura no esté completa, en contra del diseño [V]
- **Evidencia:** `horas_de` (`servidores_datos.py:135-159`) y `registrar` (`servidores_historico.py:294-297`) no miran `origen_fiable` ni
  `datos["completa"]`. El diseño (§Protocolo) dice: «Una lectura con código ssh ≠ 0 **no** produce … histograma aunque lleve `### version`».
- **Escenario:** con `aislamiento no`, un proceso local sin privilegios dentro del cgroup de ssh escribe líneas falsas, que reciben
  `_SYSTEMD_UNIT=ssh.service`. Como `_anota_horas` solo sube (`MAX`), el pico falso de la hora en curso queda guardado para siempre.
- **Cambio:** escribir horas exactas solo si `origen_fiable` es True y `completa` es True. Si se quieren conservar las de origen dudoso, guardarlas
  aparte con una marca, nunca mezcladas con un `MAX` que no se puede deshacer.

### M5. Desviaciones del diseño E1 [V]
- `MAX_IPS_POR_MUESTRA` sigue en **4** (`servidores_datos.py:10`). `interpretar` entrega 12, pero solo se guardan 4. El diseño (M1) pide hasta 12.
- `### version_root 2` no está implementado (`recoger-root.sh`). Si es de E2, hay que moverlo explícitamente en el diseño.
- Las secciones inválidas no se cuentan en `meta` (H6). Solo van a la caché (`secciones_invalidas`) y nadie las lee.
- No hay rótulo «sin garantía de origen» en la interfaz con `origen_fiable` False (`desplegable_servidores.py`), ni indicación de `parcial`.
- La comprobación es `Σh ≥ total` y no `== total`. Es razonable, porque el primer cubo puede contener líneas de más de 24 h, pero se puede acotar:
  `total ≤ Σh ≤ total + h[primero]`.
- La ventana escribible es de 26 h (`servidores_datos.py:157`) frente a las 25 del diseño (menor).

### M6. La serie diaria da por «completo» un día al que le faltan horas [V]
- **Evidencia:** `_junta` (`servidores_lectura.py:29-32`) ignora las horas con `intentos_exactos` NULL. Un día con 3 horas anotadas devuelve la suma
  de 3 horas y `intentos_completa=True`. Ocurre el primer día de v2, con huecos de cobertura o con el Mac apagado.
- **Cambio:** en paso `dia`, `completa` exige 24 horas con dato y todas completas (o devolver `horas_con_dato`).

### M7. Huecos de test: mutaciones que sobreviven [V]
Se ejecutó en la copia `test_servidores_v2`, `test_servidores` y `test_servidores_historico` con cada mutación. Sobreviven:

| Mutación | Fichero |
|---|---|
| quitar la ventana `[ok_en−26h, ok_en]` de `horas_de` | servidores_datos.py:157 |
| `completa` con `ok_en` en vez de `min(ok_en, ahora_srv)` | servidores_datos.py:158 |
| `ca and cb` → `ca or cb` en `_junta` | servidores_lectura.py:32 |
| el script dice siempre `aislamiento si` | recoger.sh:47 |
| quitar `ts > ahora + 60` en awk | recoger.sh:63 |
| `cob == 0` deja de significar «desconocido» | recoger.sh:71 |
| `DESFASE_MAX_S` 900 → 3000 | servidores_intentos.py:56 |
| quitar el recorte `${cuerpo:0:6000}` | recoger.sh:84 |

Mueren las dos de control (quitar «incoherente» y dejar que `completa` vuelva a 0), además de «ignorar `origen_fiable`» y «ignorar cabeceras tras
`### fin`». Lo más grave es `aislamiento`: es la llave para guardar IPs y ningún test ejecuta la rama `si` del script real (en macOS no existe
`/proc/self/cgroup`, así que siempre sale `no`, y no se comprueba).

**Propuesta:**
- Hacer configurable la ruta del cgroup (variable `CGROUP=/proc/self/cgroup` en el script, sustituida en el test igual que `PATH`) y probar las dos ramas.
- Añadir casos en el borde de 899/901 s.
- Añadir una lectura con el reloj del servidor por detrás.
- Añadir `cob` vacío.
- Comprobar `completa` en paso `dia`.

### M8. Si journalctl falla por algo que no es timeout, sale «fuente unit, total 0» y no «sin dato» [S]
- **Evidencia:** en `recoger.sh:79` solo cuenta el código 124. Un journalctl que sale con 1 tiene ese efecto: por ejemplo con systemd < 233, que no
  admite `-o short-unix` pero sí `-o cat` en la sonda de la :45. También un error de permisos a mitad de lectura.
- **Cambio:** `parcial si` (o un `fuente error` nuevo) para cualquier código distinto de 0, y el Mac lo trata como «sin dato».

## Bajo

- **B1. El código de cierre siempre es 0 y el recorte puede partir una línea** [V]. `echo "fin $nlin 0"` (`recoger.sh:86`) no refleja nada, así que
  la comprobación `código == 0` del Mac es vacía. `${cuerpo:0:6000}` puede cortar a media línea y el marco sigue cuadrando. Si se pierde la línea
  `parcial`, el Mac pone `intentos_sin_dato` y la tarjeta dice «el servidor no registra intentos», que es falso. En la práctica es inalcanzable: el
  cuerpo normal ronda 1,5 KiB, y con 12 IPs hostiles de ~390 bytes son ~5,3 KiB. Aun así conviene recortar por línea y emitir `fin $nlin 1` si se recortó.
- **B2. `PIPESTATUS` dentro de un `$(...)` anidado** [V en 3.2, S en 5.x]. Funciona en bash 3.2 (el test de lentitud pasa). No pude probar bash 5:
  no hay ninguno local y no se usó docker. Es más robusto capturar el valor justo tras la tubería, `st=${PIPESTATUS[0]}`, y usarlo después.
- **B3. Test frágil por carga** [S]. `ScriptRealTest` sustituye `timeout 6 journalctl` por `timeout 1`. En una máquina cargada, el arranque del
  journalctl falso en Python puede pasar de 1 s, y entonces `parcial si`, no llega `horas` y salta un `KeyError` en
  `test_cuenta_solo_lineas_canonicas…`. No hay dependencia de la hora del día: `AHORA` es fijo en los tests de base y el script usa
  desplazamientos con margen. La tolerancia `(5, 6)` de la :289 sobra, porque la línea de hace 90 000 s nunca cae en el primer cubo.
- **B4. Por defecto se confía** [V]. `origen_fiable is not False` (`servidores_datos.py:116`) da por fiable una entrada sin la clave. Hoy no es
  explotable, porque `_guardar` sustituye `datos` entero, pero es mejor `is True`.
- **B5. Dos cuentas por conexión** [V]. Un usuario válido con contraseña fallida cuenta `Failed password` y además
  `Connection closed by authenticating user`. Viene de v1 y el test lo fija (`…198.51.100.7 → 2`). Conviene documentar que se cuentan eventos,
  no conexiones.
- **B6. Confusión de mensajes** [V]. `intentos_sin_dato` se usa también cuando la sección v2 es inválida por su contenido, no solo con
  `fuente ninguna`. La tarjeta debería distinguir «sin fuente» de «lectura no válida».
- **B7. Detalles menores:**
  - El docstring de `servidores_lectura.py:3-4` quedó desfasado: dice que solo hay picos.
  - Un `total` entre 1 M y 10 M pasa el parser (`MAX_CUENTA`), pero `muestra_de` lanza `FueraDeRango` y se descarta la muestra entera, CPU y
    disco incluidos (viene de v1).
  - Con el límite de ritmo de journald (`Suppressed N messages`), una hora puede quedar contada de menos y congelada como completa [S].
  - La bajada a la app anterior con `user_version 2` deja el historial parado con `ErrorAlmacen` (registrado, el ciclo sigue). Conviene una línea en el README.

## Lo que está bien (verificado)

- **Parser:** una sección repetida invalida las dos apariciones (no hay reapertura). `### fin`, `### cortada` o una cabecera tras el fin hacen
  `completa=False`. Un marco con `n` mal formado invalida la sección y no cae a v1.
- **Filtro:** `_SYSTEMD_UNIT` es un campo de confianza de journald. La IP se toma antes del **último** `port`, que resiste un `port` dentro del nombre
  de usuario (hay test). `cut -b -400` con `LC_ALL=C` no rompe nada: el awk solo emite números e IPs, y el Mac decodifica con `replace`.
- **Recursos del Mac:** 128 KiB de lectura que no se acumula, 25 cubos exactos y consecutivos, claves repetidas que rechazan la sección, enteros
  de 1 a 12 cifras y `TS_MAX`. No veo forma de agotar memoria ni CPU.
- **Relojes:** `desfase` > 900 s descarta solo el histograma. `completa` usa el mínimo de los dos relojes. No se escriben horas futuras
  (`> ok_en+900` lanza error y `> ok_en` se omite). `mantener` sigue purgando `hora > ahora+3600`.
- **Filas `n=0`, `'gris'` de `_anota_horas`:** no alteran el `n`/`errores` de `historia()`, `_suma_hora` las fusiona bien (rango de `gris` = 0) y
  no crecen sin límite (una por hora y servidor).
- **Migración 2:**
  - `ALTER TABLE … ADD COLUMN … CHECK` sobre STRICT funciona: NULL pasa el CHECK.
  - Va dentro de `BEGIN IMMEDIATE` con rollback.
  - La copia previa se hace en memoria y sin IPs (el test busca la IP en los bytes).
  - `CLASES_SERVIDORES` está al día.
  - El test de migración genérica ahora usa `max(MIGRACIONES)+1`.

## Acciones recomendadas (por orden)

1. A1: dar al `sudo` el tiempo restante, poner presupuesto a los comandos de usuario y matar al grupo desde el vigilante, con un test de cierre de stdout.
2. A2: anclar el awk al mensaje, con un test de nombres de usuario hostiles.
3. A3: marcar «presupuesto agotado» y añadir un cierre propio de root, con un Mac que no convierta lo vacío en 0.
4. M1, M2 y M4: invalidar la sección cortada; `--since @…`/`--until @…`; escribir histograma solo si `origen_fiable` y `completa`.
5. M5: decidir si `MAX_IPS_POR_MUESTRA` pasa a 12 y si `version_root` se mueve a E2. Cerrar los huecos de M7 (rama `aislamiento si` incluida).
6. M3, M6, M8 y los bajos.

## Preguntas abiertas

1. ¿Se acepta guardar el histograma con `aislamiento no`, que ahora es permanente por el `MAX`, o solo con origen fiable (M4)?
2. ¿`version_root` y el cierre propio de root se dejan para E2? Si es así, A3 necesita al menos el «presupuesto agotado» ya en E1, porque la
   regresión es de este commit.
3. ¿Quedan 4 IPs guardadas por muestra a propósito? El diseño dice 12.

Status: DONE_WITH_CONCERNS
Summary: E1 cumple lo esencial del diseño v2 (parser no reabrible, marco, filtro por unidad, migración 2 segura, Mac no agotable) y los 100 tests pasan, pero hay tres hallazgos altos verificados: el vigilante no cierra el canal a tiempo, un nombre de usuario hostil oculta o triplica intentos, y el presupuesto de root convierte «no dio tiempo» en ceros y alarmas falsas. Además, 8 de 10 mutaciones nuevas sobreviven (entre ellas la rama `aislamiento si`, la llave para guardar IPs) y hay desviaciones del diseño (4 IPs en vez de 12, histograma guardado sin origen fiable ni lectura completa).
