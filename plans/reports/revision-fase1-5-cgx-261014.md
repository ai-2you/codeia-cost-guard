# Revisión de código 1.5, pasos 1-3 — `infobar/cgx.py` y `tests/test_cgx.py` (commit 962ce31), 2026-10-14

**Resultado:** no he encontrado ninguna vulnerabilidad explotable en el código tal como está. La construcción (trozos
ChaCha20-Poly1305 con índice real en nonce y AAD, compromiso de clave, scrypt acotado antes de derivar, X25519 + HKDF con la
cabecera en `info`, firma Ed25519 sobre cabecera + hash de trozos cifrados) es correcta y sigue el diseño v2. Los
problemas están en otros cuatro sitios:
- la batería de tests deja pasar una regresión de una línea que reutiliza el nonce en todos los trozos;
- en modo 2, el claro de un falsificador llega a `salida` antes de comprobar la firma;
- se escapan excepciones que no son `ErrorCgx`, una de ellas con la frase dentro;
- falta un campo en el formato que conviene añadir **antes** de congelar v1.

**Alcance y método.**
- He leído completos `infobar/cgx.py` (317 líneas), `tests/test_cgx.py` (247), el diseño v2 (`plan.md:160-173`) y `revision-diseno-1-5-261014.md`, del que no repito nada.
- La batería original pasa entera: 22 tests en 13,7 s, con `cryptography` 46.0.7 y Python 3.14.6.
- He hecho 25 mutaciones sobre una copia en el scratchpad. **18 sobrevivían.**
- He escrito 9 tests propuestos, también en la copia (`scratchpad/mut/repo/tests/test_cgx_extra.py`). Con ellos mueren 12 de los 18; quedan 6 supervivientes, todos equivalentes y justificados en §5.
- He escrito 10 experimentos de ataque (`scratchpad/mut/experimentos.py`).
- No he tocado el repositorio salvo este informe.

---

## ALTO

### A1. Ningún test detecta que el nonce deje de llevar el índice: la regresión rompe la confidencialidad con todo en verde
- **Dónde:** `cgx.py:177` y `cgx.py:256` (nonce = prefijo + `struct.pack(">I", indice)`), `test_cgx.py:86-101` (el único vector conocido con las primitivas tiene **un** trozo, índice 0).
- **Verificado por mutación.** Cambiar el índice del nonce por la constante 0, en el escritor y en el lector, deja los 22 tests en verde. El AAD sigue llevando el índice, así que la reordenación se sigue detectando y nadie se entera.
- Con ese mutante he recuperado el claro del trozo 1 sin la clave: XOR de los dos trozos cifrados con el claro conocido del trozo 0 (longitud y ceros). Resultado: `True`.
- Pasa lo mismo al revés: quitar el índice del AAD y dejarlo en el nonce también sobrevive. Esa variante es benigna, pero confirma que ningún test mira la construcción concreta de un trozo con índice mayor que 0.
- **Arreglo.** Añadir un vector conocido de **dos trozos** recalculado con las primitivas (`test_vector_dos_trozos`, en la copia). Mata los dos mutantes y el de la bandera.

### A2. Modo 2: el claro de cualquiera que conozca la pública se escribe en `salida` antes de comprobar la firma
- **Dónde:** `cgx.py:266` escribe trozo a trozo; la firma se comprueba en `cgx.py:271-282`. El contrato está documentado en `cgx.py:12-13`.
- **Escenario (verificado).** En modo 2 la clave simétrica se deriva solo de la pública del destinatario, que no es secreta. Por eso las etiquetas Poly1305 no autentican nada frente a un falsificador: solo lo hace la firma, y llega al final.
  - Un fichero fabricado con otra clave de firma deja `b'DATOS DEL ATACANTE'` en `salida` y después lanza «no está firmada por un dispositivo conocido» (experimento 2).
  - Todo el análisis del claro (longitud declarada, relleno) corre sobre datos del atacante. Probé trozo 0 de 3 bytes, longitud 2^64-1 y último trozo vacío: los tres acaban en `ErrorCgx`, pero en el de longitud 2^64-1 ya se habían escrito 100 bytes (experimento 3).
- **¿Es aceptable?** Sí, si los llamadores de los pasos 5 a 7 cumplen estas tres reglas, que hoy no están en ningún sitio que obligue:
  1. `salida` es siempre un temporal privado (`DATOS/tmp`, `O_EXCL`, 0600), nunca la ruta final ni algo que otro proceso vigile.
  2. El temporal se borra en un `finally` con bandera de éxito, **ante cualquier excepción** y no solo ante `ErrorCgx` (ver A3).
  3. Nada abre el temporal con SQLite, ni cuenta turnos, ni lo muestra, hasta que `descifrar_*` vuelva sin error.
- **Arreglo.**
  - (a) Poner las tres reglas en la docstring de `descifrar_con_privada` y añadir un helper que las haga cumplir en `exportacion.py` (paso 5), con un test que inyecte un fallo de firma y compruebe que no queda temporal.
  - (b) Rechazar antes de leer nada si `firmas_publicas` está vacía o es `None`. Hoy descifra el fichero entero y falla al final (comprobado).
  - (c) Opcional: verificar la firma en una primera pasada (solo hash de los trozos cifrados) y descifrar en una segunda. Debe hacerse **sobre una copia privada del `.cgx`**: si se lee dos veces de la carpeta de copias, quien escribe en ella puede cambiar el fichero entre las dos pasadas. El diseño actual, de una sola pasada con el hash de exactamente los bytes descifrados, no tiene ese problema.

### A3. Excepciones que no son `ErrorCgx` y que el llamador no espera (verificado)

| Dónde | Disparador | Sale | Consecuencia |
|---|---|---|---|
| `cgx.py:100,106,112,201` | sin `cryptography` | `ModuleNotFoundError` (el import va antes de `_cripto()`) | `generar_par`, `generar_par_firma`, `publica_de` y `cifrar_a_destinatario` no dan el mensaje «instala cryptography» del diseño 1.5.10. El test `test_cgx.py:240` solo cubre `cifra_frase`. |
| `cgx.py:218` (`_tamano`) | entrada no buscable (tubería) | `OSError [Errno 29] Illegal seek` | experimento 4 |
| `cgx.py:302,306` | privada de 31 bytes o `str` | `ValueError` / `TypeError` | `publica_de` está fuera del `try` |
| `cgx.py:130` | frase con sustituto (`getpass` con bytes no UTF-8 en POSIX) | `UnicodeEncodeError` | **lleva la frase** (ver M1) |
| `cgx.py:185` | clave de firma de longitud incorrecta | `ValueError` **después de escribir el fichero entero** | ver M2 |
| `cgx.py:149` | `longitud` negativa o ≥ 2^64 | `struct.error` | entrada del propio llamador; impacto bajo |

- **Arreglo.**
  - Mover los imports de `serialization` dentro de `_cripto()`.
  - Envolver en `try/except (ValueError, TypeError, OSError, UnicodeError) → ErrorCgx(...) from None` la frontera de `descifrar_*` y `cifrar_*`. El `from None` sobra en `OSError`, pero es obligatorio en `UnicodeError`.
  - Test: `assertRaises(cgx.ErrorCgx)` para cada fila de la tabla.

## MEDIO

### M1. La frase de paso se filtra en la excepción
- **Dónde:** `cgx.py:130`, `frase.encode("utf-8")`.
- **Verificado.** `repr(e)` da `UnicodeEncodeError('utf-8', 'mi-frase-secreta-\udcffxyz', 17, 18, …)`, y `e.object` es la frase completa. Cualquier `logging` con `%r`, un `repr` en un informe de error o un depurador la vuelcan. `str(e)` solo muestra un carácter y su posición.
- **Arreglo:** `except UnicodeError: raise ErrorCgx("la frase contiene caracteres no válidos") from None`. El `from None` es imprescindible, porque `from e` conserva el objeto en `__cause__`. Ninguna otra excepción ni mensaje de `cgx.py` lleva la clave ni la frase; la huella que muestra `cgx.py:304` es pública.

### M2. La clave de firma se valida al final, con el fichero ya escrito
- **Dónde:** `cgx.py:184-186`.
- **Verificado.** Con una clave de firma de 5 bytes se escriben 65.689 bytes (cabecera y trozos) y solo entonces salta `ValueError`. En una copia automática eso deja un `.cgx` completo y sin tráiler en la carpeta, a la espera de que la verificación por relectura (diseño 1.5.6) lo borre.
- **Arreglo:** construir `Ed25519PrivateKey.from_private_bytes(firma_privada)` al principio de `cifrar_a_destinatario`, antes de `salida.write(cabecera)`, y pasar el objeto ya construido a `_cifrar`. Además, documentar que `cifrar_*` deja `salida` a medias si falla, igual que se documenta para `descifrar_*`.

### M3. Modo frase: la frase autentica a quien conoce la frase, no qué fichero es
- **Escenario (punto 6, verificado).** Un atacante que no conoce la frase no puede fabricar nada. Sí puede **cambiar un fichero por otro auténtico cifrado con la misma frase**: un export antiguo (vuelta atrás) o, peor, el fichero de clave privada protegido (diseño 1.5.2, «un `.cgx` modo 1 de un trozo»).
  - En el experimento 8, el fichero de clave y un export con la misma frase descifran los dos sin error y son intercambiables.
  - Al restaurar, «la base» resultaría ser 32 bytes de clave y `quick_check` fallaría. Al cargar la clave, saldría una base de 516 bytes y fallaría la longitud. No hay daño hoy, pero es confusión de tipos en el formato, y en modo 2 la vuelta atrás a una copia antigua legítima también es posible.
- **Arreglo, barato solo ahora.** Añadir a la cabecera, dentro del AAD, un byte de **tipo de contenido** (1 = base SQLite, 2 = clave privada X25519, 3 = clave de firma) y que cada llamador exija el suyo. Debe hacerse antes de congelar el vector y de que exista ningún fichero v1. Para la vuelta atrás, que `--importar` y `--probar-restauracion` muestren la **fecha autenticada de la cabecera** (`cgx.py:229`, ya firmada en modo 2 y en el AAD en modo 1) junto al nombre del fichero y avisen si no cuadran.

### M4. Desviación de enmarcado: el lector se ancla en el tamaño del fichero, no en la lectura con un byte de anticipación
- **Dónde:** `cgx.py:217-221`, `243-249`, `313-317`, frente al diseño 1.5.1, que pide leer `1 MiB + 16` con un byte de anticipación.
- **Valoración.** La desviación es **buena**:
  - Elimina la ambigüedad que tendría el diseño tal cual, porque con lectura de anticipación el tráiler de 64 bytes del modo 2 se podría confundir con un último trozo corto.
  - El cálculo de `n_trozos`/`resto` es correcto en los bordes: múltiplos exactos de `1 MiB + 16` dan un último trozo lleno con bandera 1, y `resto` ≤ 16 se rechaza.
  - El escritor nunca emite un trozo final vacío. El claro siempre ocupa al menos 64 KiB, por el relleno, así que la frase del diseño sobre el «último trozo vacío» no aplica.
  - Por la misma lógica, el `entrada.read(1)` de cola (`cgx.py:273,283`) solo se alcanza si el fichero crece durante la lectura. Lo confirman los mutantes supervivientes de §5.
- **Costes.**
  - (a) Exige una entrada buscable: con una tubería salta `OSError`, que está en A3.
  - (b) Con flujos crudos que devuelven lecturas cortas (`RawIOBase`, algunos sistemas de ficheros de red sin búfer), `cgx.py:253` no comprueba la longitud leída. Un fichero **válido** se rechaza como «dañado o ha sido manipulado» (experimento 5, con lecturas de 4 KiB). Falla cerrado, pero con un diagnóstico falso.
- **Arreglo.**
  - Anotar la desviación en el plan.
  - Envolver la entrada en `io.BufferedReader` o leer en bucle hasta completar `n` bytes o llegar a EOF.
  - Si `len(cifrado)` no es el esperado, devolver «el fichero está truncado».
  - Desviación menor y benigna: la longitud ocupa 8 bytes y no 4. Hay que corregirlo en el texto del plan.

### M5. Tests que no prueban lo que dicen

| Test | Problema | Mutante que sobrevive | Arreglo (en la copia, verificado por mutación) |
|---|---|---|---|
| `test_cgx.py:71` | `len(blob) % 1 == 0` es siempre cierto | — | `test_tamano_multiplo_de_relleno`: (cuerpo − 16·n_trozos) % 64 KiB == 0 |
| `test_cgx.py:217-219` | `blob[30:62]` no es la efímera: la efímera ocupa `[33:65]`. El comentario dice «efímera a cero» pero pisa 3 bytes de la sal y 29 de la efímera, así que lo para el compromiso o el AAD | quitar el `try` de `cgx.py:305-308` | `blob[33:65]` y `assertRaisesRegex(..., "efímera")` |
| `test_cgx.py:191-192` | solo exige `ErrorCgx`; sin compromiso, el error sale igualmente por `InvalidTag` | quitar `cgx.py:239-240` | `test_compromiso_antes_de_descifrar`: `ChaCha20Poly1305.decrypt` simulado que no debe llamarse y mensaje «incorrecta» |
| `test_cgx.py:205-211` | no prueba ningún borde **aceptado**; `(17,32,1)` lo para la memoria, no el límite de `r` | `>` → `>=` (256 MiB exactos), `r <= 16` → `r <= 64` | `test_bordes_aceptados_de_scrypt`: (18,8,1) y (16,16,2) aceptados con `derive` simulado; (14,17,1) rechazado |
| `test_cgx.py:193-194` | no mira el mensaje | quitar `cgx.py:303-304` | regex «otra clave» |
| — | nadie prueba las comprobaciones de longitud y relleno, alcanzables por un falsificador en modo 2 | quitar `cgx.py:260-261`, quitar `cgx.py:269-270`, quitar solo la parte del relleno | `test_forjas_firmadas_incoherentes`, con un fichero modo 2 fabricado y **bien firmado** |
| `test_cgx.py:200-203` | no prueba el borde 11/12 | `FRASE_MIN = 11` | `test_frase_borde_12` |
| `test_cgx.py:221-233` | nadie comprueba que la suma de control cubra el prefijo | suma sin prefijo | `test_suma_cubre_el_prefijo`: cambiar `CGX-SECRET-KEY-1-` por `CGX-SIGN-KEY-1-` conservando el cuerpo |

- **Sobre la «mutación equivalente conocida» de la bandera.** Con la batería actual, la mutación «bandera siempre 0 en ambos lados» **muere**, porque el vector congelado lleva la bandera 1 en el AAD. Lo que sí es cierto es la redundancia semántica entre la bandera y la longitud declarada frente al truncado: quitar **todo** el chequeo de longitud (`cgx.py:269-270`) sobrevivía a la batería original, porque la bandera ya detecta el truncado.

### M6. La frase no se normaliza (NFC) — **no verificado en Windows**
- **Dónde:** `cgx.py:130`.
- **Escenario.** «contraseña» tecleada en macOS y en Windows puede llegar como NFD o como NFC según el método de entrada. He comprobado que las dos formas dan bytes UTF-8 distintos. La consecuencia es que una copia hecha en un sistema no se abre en el otro con la «misma» frase. No he podido comprobar qué entrega `getpass` en cada sistema.
- **Arreglo:** decidir ahora, antes de que existan ficheros v1, si se aplica `unicodedata.normalize("NFC", frase)` antes de derivar y antes de `comprueba_frase`. Si se cambia después, las copias ya hechas dejan de abrirse.

## BAJO

- **L1. Ganchos de test en la API pública.**
  - `aleatorio` (`cgx.py:189,199`) y `efimera_privada` (`cgx.py:199,204`) permiten fijar la sal, el prefijo y la efímera.
  - Si un llamador los reutiliza, se repiten clave y nonces entre ficheros, y eso es reutilizar el flujo de cifrado.
  - Arreglo: hacerlos solo por nombre y con guion bajo (`_aleatorio`, `_efimera_privada`), y anotar que son solo para vectores.
- **L2. Separación de dominio de la clave AEAD.**
  - En `cgx.py:146` y `:241`, la salida de scrypt o HKDF se usa directamente como clave de ChaCha20 y, a la vez, como entrada del compromiso.
  - No es explotable, porque el compromiso pasa por HKDF.
  - La práctica habitual es derivar de la clave maestra dos salidas con `info` distintos («cgx1 clave datos» y «cgx1 compromiso»). Si se hace, que sea antes de congelar v1, igual que M3.
- **L3. Campos no usados.**
  - El lector no exige que estén a cero: sal y KDF en modo 2, efímera y destinatario en modo 1 (`cgx.py:229-234`).
  - No es explotable, porque están en el AAD y, en modo 2, también en la firma.
  - Rechazarlos daría una forma canónica única. Es opcional.
- **L4. `n_trozos > 2^32`** (`cgx.py:248`): coherente con el escritor (`cgx.py:175`). Probarlo exige un flujo falso que diga medir unos 4 PiB. Es opcional.

## Verificado sin problema (para no reabrirlo)

- **HKDF** (`cgx.py:123,134`):
  - `salt=None` es válido según RFC 5869 con IKM de alta entropía.
  - `info` lleva la cabecera base completa, con las dos públicas, el prefijo y la fecha.
  - Las salidas son de 32 bytes.
- **Scrypt.** Los límites se comprueban **antes** de derivar; lo prueba el test con `derive` simulado. El peor caso aceptado es (16,16,2): 256 MiB y unas 2× el coste de escritura.
- **X25519.**
  - Un punto de orden bajo da `ValueError`, que se convierte en `ErrorCgx`, en escritura y en lectura (comprobado con la efímera bien colocada).
  - La comparación del destinatario usa `compare_digest` (`cgx.py:303`), aunque la pública no es secreta.
- **Ed25519.** `InvalidSignature` y `ValueError` capturados (`cgx.py:280`). La firma cubre cabecera + SHA-256 de exactamente los bytes descifrados, en una pasada, así que no hay hueco entre comprobar y usar. La magia `CGX1` al inicio del mensaje firmado separa el dominio.
- **Oráculos.** No hay oráculo explotable:
  - Es un CLI local, sin servicio en línea.
  - Los mensajes distintos («frase incorrecta», «dañado o manipulado», «longitud incoherente», «no firmada») solo distinguen casos después de que el compromiso o la etiqueta validen, o con datos públicos de la cabecera.
  - El compromiso no abarata la fuerza bruta frente a descifrar el trozo 0, porque scrypt domina el coste.
- **Memoria.** Unos 2 MiB por trozo en lectura y escritura, sin compresión ni tamaños elegidos por la cabecera.
- **Orden de comprobaciones.** Coincide con el diseño: magia/versión/modo → límites KDF → derivación → compromiso → trozos → longitud/relleno → firma → cola.
- **Codificación de claves.** Prefijos que no se confunden, suma que cubre el prefijo (comprobado) y alfabeto base32 con `binascii.Error` capturado.

## §5. Mutantes que siguen vivos con los tests propuestos (equivalentes, sin test)

- **`log2n <= 24`:** lo implica el tope de memoria.
- **`resto <= ETIQUETA`:** con 1-15 bytes salta `InvalidTag`, y 16 solo lo fabrica quien tiene la clave.
- **`sin_tope_n_trozos`:** ver L4.
- **Cola en modo 1 y en modo 2:** ver M4.
- **`vistos != longitud`:** si cuadra `total_claro`, `vistos` es igual a `longitud` por construcción.

## Acciones recomendadas (por orden)

1. Vector de dos trozos (A1) y el resto de tests de M5. Los tienes listos en `scratchpad/mut/repo/tests/test_cgx_extra.py`; con ellos mueren 12 de los 18 mutantes vivos.
2. Decidir antes de congelar v1:
   - byte de tipo de contenido (M3);
   - normalización NFC (M6);
   - separación de dominio de la clave (L2).
   
   Después, regenerar `VECTOR_DESTINATARIO`.
3. Fronteras de excepciones (A3, M1) y la clave de firma al principio (M2).
4. Contrato de salida para los pasos 5 a 7 (A2): helper de temporal con `finally` y rechazo de `firmas_publicas` vacía.
5. Anotar en el plan la desviación de enmarcado y los 8 bytes de longitud (M4), y leer en bucle ante lecturas cortas.

## Preguntas abiertas
- ¿Acepta el dueño el byte de tipo de contenido y la NFC en v1? Cambian el vector congelado.
- ¿Debe `cgx` admitir entradas no buscables, por ejemplo una copia leída de una tubería? Si no, basta con el mensaje de M4/A3.

Status: DONE_WITH_CONCERNS
Resumen: la criptografía de los pasos 1-3 es correcta y sigue el diseño v2 (la desviación de enmarcado es una mejora), pero la batería deja pasar con todo en verde la reutilización del nonce entre trozos, y en modo 2 el claro sin verificar llega a `salida` mientras escapan excepciones que no son `ErrorCgx`, una con la frase dentro. Los tests que cierran esos huecos están escritos y verificados por mutación en el scratchpad, y el byte de tipo de contenido y la NFC hay que decidirlos antes de congelar v1.
