# Filtro de detección de gestos con umbral adaptativo

Última actualización: 27/10/2026.

## Qué archivo uso

```
filtro/
  gestos_tiempo_real.py   GRABAR: se conecta a la Raspberry, aplica el filtro en vivo, muestra el tablero y guarda la sesión.
                          Con --imagenes revisa los errores de una sesión (falsos positivos y gestos perdidos).
  gestos_patron.py        PATRON DE DOS GESTOS: decide "hay gesto" solo si aparecen dos gestos seguidos. Evalúa y explica
                          los errores de una sesión grabada (--evaluar, --explicar) y tiene un modo en vivo (--consola).
  sesiones/               las grabaciones (<nombre>_<fecha>_<hora>_eeg.csv, _eventos, _rangos, _resumen)
  analisis/               herramientas de análisis (no hacen falta para grabar):
      ventanas_deslizantes.py   el filtro sobre archivos (fuente de verdad del filtro) y la evaluación de los 30 datasets
      ver_errores.py            imágenes de falsos positivos y negativos de los 30 datasets
      prueba_recepcion.py       comprueba que el flujo de la Raspberry llega completo (muestras por segundo, cortes)
```

**Diferencia entre `gestos_tiempo_real.py` y `gestos_patron.py`.** Los dos usan el *mismo filtro*. `gestos_tiempo_real.py` avisa
cada vez que el filtro ve un gesto, aunque sea suelto, y es el que graba la sesión y la barra espaciadora. `gestos_patron.py`
va encima del filtro y solo avisa cuando hay **dos gestos seguidos** (con silencio entre ellos); sirve para activar/desactivar
sin que un gesto aislado, un parpadeo natural o ruido dispare algo. Se graba siempre con `gestos_tiempo_real.py` (el nombre de la
persona se pone en la variable `NOMBRE` al principio del archivo o con `--nombre`) y después se analiza con cualquiera de los dos.

Detecta movimientos faciales (levantar cejas, dos parpadeos) en 4 canales EEG (TP9, FP1, FP2, TP10) y los separa del ruido.
Está pensado para tiempo real: es **causal** y **no tiene entrenamiento**, solo parámetros y lógica difusa con pertenencias
fijas.

## 0. Garantías para tiempo real

- **Causal.** Cada decisión usa solo muestras hasta el instante actual (filtros con `sosfilt`, medianas y promedios móviles
  hacia atrás, diferencias hacia atrás). Prueba: se corta cada grabación al 60 % y se compara con la grabación completa.
  Resultado: **0 de 480 señales internas y 0 de 930 decisiones cambian**. (El único dato "global" es la frecuencia de
  muestreo `fs`, que en vivo se conoce por el dispositivo.)
- **Sin entrenamiento.** El puntaje de regresión logística y la plantilla aprendida de ráfaga quedan fuera del filtro final
  (siguen como opciones experimentales apagadas). La convolución usa una plantilla paramétrica (ráfaga de 12 Hz con desvío
  de 0.1 s). Los umbrales de las evidencias son parámetros (percentil 90 del reposo de las grabaciones).
- **Latencia:** como mucho una ventana (0.5 s) más un paso (0.25 s). Las memorias hacia atrás (2 s, 10 s) solo fijan el umbral.

## 1. Cómo funciona (paso a paso)

1. **Pasabanda 8–30 Hz** (Butterworth de orden 8) en cada canal.
2. **Desplazamiento** `d = |señal − base|`, con la base = promedio de los últimos 2 s.
3. **Ventanas deslizantes** de 0.5 s que avanzan 0.25 s. En cada una y por canal se mide el **pico** de `d`.
4. **Umbral por canal (margen).** Pico sobre el margen: *movimiento*; si no, *ruido*.
5. **Decisión de la ventana** (sección 3). El 1 se mantiene 1 s después de la última detección (antirrebote).

## 2. Cómo se fija el umbral y por qué

`margen = máx( k·ruido , piso_pico·(mayor pico de ruido de los últimos 2 s) , piso , pico_ruido + 0.1·(pico_gestos − pico_ruido) )`, acotado a 1000 µV.

| Elemento | Valor | Por qué |
|---|---|---|
| `k` | 6 en frontales, 8 en temporales | Margen sobre el nivel de ruido típico. |
| Mayor pico de ruido reciente | `piso_pico 1.0`, 2 s | El umbral no baja del mayor pico de ruido visto hace poco. |
| Piso | 20 µV y `1.5 ×` la mediana de los picos de ruido de los últimos 10 s | Un canal tranquilo no deja caer el umbral hasta un valor que cualquier ruido supere (problema de los umbrales AV y RM descrito por Moya et al. 2021). |
| Hacia el nivel de los gestos | `frac_spk 0.1` | Umbral entre el ruido y la señal (Pan-Tompkins; Moya et al.). Con 0.25–0.5 se pierden muchos gestos. |
| Qué ventanas enseñan el ruido | Solo las clasificadas como ruido | Un gesto no debe subir el umbral. |
| Calibración | 4 s, percentil 25 | Un gesto en la calibración no define el ruido. |
| Espera tras un gesto | 0.1 s | La cola del gesto no se usa para aprender. |
| Ruido sostenido | `persist_s 1.5` | Si un canal está en movimiento 1.5 s seguidos se acepta como ruido nuevo. |
| Adaptación | `alfa 0.2` | Más rápido aprende lo que no debe. |
| Canal ignorado | ruido > 3× el de su par (`excl_piso 10 µV`) | Un electrodo ruidoso no debe decidir. |

## 3. Cómo se decide que hay un gesto

### Reglas duras
- **Amplitud:** al menos un frontal y un temporal (no ignorados) superan su margen **en el mismo instante** (tolerancia ±0.1 s,
  `coinc_s`). Basta uno de cada grupo: el filtro no necesita que respondan los cuatro canales.
- **Pares activos** (`par_min 1` en los frontales, `par_min_tp 0.7` en los temporales): si los dos canales de un par están
  activos (ninguno ignorado), ambos deben superar su margen (los temporales, al menos 0.7 veces). Es lo que más contiene los
  falsos positivos: sin exigir pares los falsos positivos de los 30 archivos pasan de 1 a 10. **Excepciones por grupo fuerte:**
  - *par frontal* (`par_comp 0.8`, `par_fuerte 1.8`): si ambos temporales superan 0.8 veces su margen y un frontal supera 1.8
    veces el suyo, no se exige que el otro frontal también supere (puede tener ruido o mal contacto en ese instante);
  - *par temporal* (`par_comp_tp 1.3`, `par_fuerte_tp 1.0`): si ambos frontales superan 1.3 veces su margen y un temporal supera
    el suyo, no se exige el otro temporal.
- **Solo frontal** (`fp_solo 4.5`): ambos frontales 4.5 veces sobre su margen aunque ningún temporal responda.
- **Temporales inútiles** (`tp_inutil 300`, `fp_rel 1.2`): si el margen de un temporal supera 300 µV es puro ruido (contacto
  malo) y se decide solo con los frontales (ambos sobre 1.2 veces su margen).

### Lógica difusa (pertenencias fijas)
- **Relación entre canales** (`difuso_canales 0.4`), veto sobre la regla de amplitud: promedio ponderado de la correlación
  TP9–TP10 (0.4), la dominancia frontal sobre temporal (0.3), la correlación FP1–FP2 (0.15) y la frontal–temporal (0.15). En un
  gesto los canales se correlacionan (TP9–TP10: mediana 0.93 contra 0.39–0.53 en los falsos positivos) y el frontal domina.
  Las vías de rescate (abajo) descartan del cálculo la correlación de un frontal ignorado (`grado_sin_ign`).
- **Incremento parcial** (`parcial_umbral 0.5`, `parcial_grado 0.9`): frontales activos y un temporal que, sin llegar a su
  umbral, sube de 3 a 5 veces su propio ruido (pico, no promedio) justo en los instantes en que un frontal superó el suyo.
- **Crecimiento del temporal** (`tp_crece 1.3`, `tp_crece_f 2.0`, `tp_crece_grado 0.7`): un frontal vivo a 2.0 veces su margen o
  más y un temporal vivo cuyo pico supera 1.3 veces el mayor pico de ruido reciente de ese temporal.

### Capa de evidencias paralelas (complemento)
Si las reglas no detectaron la ventana pero un frontal está activo (pico/margen ≥ 1) y coinciden **6 de 7 evidencias**, se
rescata. Cada evidencia se calcula en paralelo sobre la misma ventana, normalizada por la mediana de sus propios valores de
los 10 s previos (comparación con el ruido previo), y vota si supera su umbral (`EVID_TH`):

| Evidencia | Qué mide | Umbral (veces el ruido previo, o correlación) |
|---|---|---|
| Derivada | máximo de la diferencia hacia atrás del frontal | 10.6 |
| Pasa altos 25–45 Hz | energía de alta frecuencia del frontal | 11.0 |
| Pasa bajos 0.5–8 Hz | energía lenta del frontal (forma del parpadeo) | 10.3 |
| Convolución con la plantilla de ráfaga | correlación normalizada con una ráfaga paramétrica | 0.59 |
| Correlaciones | frontal–temporal, TP9–TP10, FP1–FP2 | 0.41, 0.86, 0.69 |

### Vetos
- **Saturación** (`sat_veto 3`, `sat_min 2`): al menos 3 canales con 2 o más muestras en 0 o 1682 µV dentro de la ventana
  (contacto perdido). Con `sat_min 1` un solo roce de un frontal durante un gesto, sumado a dos temporales sin contacto (que
  tocan el límite por ruido), anulaba gestos reales.
- **Rebote** (`rebote_frac 0.6`, `rebote_s 1`): una detección cuyo pico es menor que el 60 % de la anterior, a menos de 1 s, es
  la cola del mismo gesto y no extiende la salida (idea del descarte de la onda T de Pan-Tompkins).
- **Persistencia tras un gesto** (`persist_det_s 3`): una ventana que se detectó como gesto tarda 3 s (no 1.5 s) en aprenderse
  como ruido sostenido; si no, un primer gesto largo subía el margen de golpe y tapaba el segundo.

## 4. Parámetros iniciales y cómo se eligieron

Se hizo una búsqueda automática de parámetros (descenso por coordenadas sobre 33 parámetros, objetivo = gestos detectados −
6 × falsos positivos) con validación por personas: elegir con una mitad de las personas y probar en la otra.

| Parámetros elegidos con… | En su propia mitad | En la otra mitad (falsos positivos) |
|---|---|---|
| Mitad A (grabaciones sin nombre) | 192 / 193, 0 FP | 195 / 211, **10 FP** (con los valores por defecto: 196, 4 FP) |
| Mitad B (8 personas) | 199 / 211, 0 FP | 188 / 193, 1 FP (por defecto: 187, 0 FP) |
| Todos los archivos | 380 / 404, 1 FP | A: 191, 0 FP; B: 189, 1 FP |

Ajustar muchos parámetros a la vez **sobreajusta**: cambian de una búsqueda a otra y los de una mitad empeoran la otra. Por eso
se fijaron solo los **parámetros que aparecen en dos o más búsquedas independientes**: piso del margen 20 µV, 5 votos de
evidencias, `fp_rel` 1.2 y tolerancia de coincidencia 0.1 s, y se dejaron apagados los pares que crecen (sin el puntaje
entrenado no se sostenían). Después se aflojó el par temporal (`par_min_tp` 1.0 → 0.7: 386 con 1 FP) y se agregó la excepción del par frontal (`par_comp 0.8` + `par_fuerte 2.0`). En ese momento: 389 de 404 con 1 FP (mitad A: 190 / 193, 0 FP; mitad B: 199 / 211, 1 FP; contacto bueno 188 / 188, 0 FP), frente a 383
con 4 FP de la configuración anterior.

Valores finales: ventana 0.5 s, paso 0.25 s, banda 8–30 Hz, `k_fp` 6, `k_tp` 8, piso 20 µV, `piso_pico` 1.0 (2 s), `piso_rel`
1.5 (10 s), `frac_spk` 0.1, calibración 4 s, `alfa` 0.2, `persist_s` 1.5 s, `persist_det_s` 3 s, `excl_ratio` 3, `excl_piso` 10 µV,
`par_min` 1, `par_min_tp` 0.7, `par_comp` 0.8, `par_fuerte` 1.8, `par_comp_tp` 1.3, `par_fuerte_tp` 1.0, `fp_solo` 4.5,
`tp_inutil` 300, `fp_rel` 1.2, `parcial_umbral` 0.5 (pico, `inc_a` 3, `inc_b` 5, `parcial_grado` 0.9), `tp_crece` 1.3 (`tp_crece_f`
2.0, `tp_crece_grado` 0.7), `difuso_canales` 0.4, `coinc_s` 0.1 s, `sat_veto` 3 (`sat_min` 2), `rebote_frac` 0.6, 6 votos de
evidencias, retención de la salida 1 s.

Cada regla agregada después de la búsqueda se adoptó solo si no sumaba falsos positivos en los 30 archivos y no empeoraba ninguna
de las dos mitades de personas.

## 5. Resultados (filtro final)

Archivos: levantar cejas y dos parpadeos (30 archivos, 8 personas). "Contacto bueno" = `Sensores_OK` ≥ 50 %.

| Conjunto | Gestos | Detectados | Falsos negativos | Falsos positivos |
|---|---|---|---|---|
| **Todos (30 archivos)** | 404 | **397 (98 %)** | 7 | **1** |
| Mitad A de personas (grabaciones sin nombre) | 193 | 191 | 2 | 0 |
| Mitad B (8 personas) | 211 | 206 | 5 | 1 |
| **Contacto bueno** | 188 | **188 (100 %)** | 0 | **0** |

(Hasta la versión anterior del documento: 386/404 con 1 falso positivo. Las reglas de excepción por grupo fuerte, el incremento
por pico, el crecimiento del temporal, `persist_det_s`, el grado sin canales ignorados y 6 de 7 evidencias suman 11 gestos sin
sumar falsos positivos ni empeorar ninguna mitad.)

- **Ruido puro** (4 grabaciones, contacto 0 %, 2.4 min): 1 episodio, en los primeros segundos de una de ellas.
- **Guiños** (gesto que no es el objetivo): el detector se dispara en 177 de 252 (70 %). Detecta *movimiento facial*, no
  distingue el tipo de gesto.
- **¿Se pueden rescatar los que faltan?** Cada relajación suma más falsos positivos que gestos. Ejemplos medidos sobre los 30
  archivos: sin exigir par temporal 397/404 con 2 falsos positivos (sin ganar gestos); sin exigir ningún par 397/404 con 10; sin
  pares ni veto difuso 397/404 con 15.
- Los resultados por archivo ya no se guardan en la carpeta (se regeneran con `analisis/ver_errores.py`).

## 6. Variantes probadas (resumen)

Todas medidas sobre 30 archivos con el filtro de ese momento como base (386/404 con 1 falso positivo; las tablas detalladas que
generaban `pruebas_tabla.py`, `combinaciones_criterios.py` y `canales_virtuales.py` se retiraron de la carpeta, los números de esta
sección quedan como registro):

- **Tamaño de la ventana de detección.** 0.5 s con paso 0.25 s es la mejor: con 0.35 s hay 4–5 gestos más pero 2–6 FP más; con
  0.75 s se pierden 4–15 gestos y suben los FP; con 1.0 s se pierden 49 gestos y los FP suben a 17.
- **Esperar la señal de otro canal.** Ampliar la tolerancia de coincidencia a 0.2–0.3 s no cambia el resultado. Esperar la señal
  del otro grupo entre ventanas (0.25–1 s) rescata 7–8 gestos pero suma 10–13 FP: no conviene.
- **Evidencias paralelas.** Cada evidencia aporta 1–2 gestos y ninguna sobra; las correlaciones son las que más contienen los FP.
  Agregar el operador de Teager-Kaiser o el puntaje z robusto como votos suma 8 gestos pero 9–10 FP.
- **Módulos.** Sin los pares activos: +6 gestos, +9 FP. Sin el veto por rebote: +1 gesto, +3 FP. Sin temporales inútiles: −12 gestos.
  Sin capa de evidencias: −5 gestos. Sin canal ignorado por ruido: −5 gestos.
- **Pares simétricos (`par_min`).** Par temporal: 0.8 → 385/1, 0.7 → 386/1 (elegido), 0.6 → 387/1, 0.5 → 387/2, sin exigir → 388/2. Par frontal: flexibilizarlo sube rápido los FP (0.9 → 386/3, 0.8 → 387/5, 0 → 390/9).
- **Banda.** 8–30 Hz es la mejor: 1–40 Hz pierde 22 gestos y suma 13 FP; 13–30 Hz pierde 24 gestos.
- **Histéresis** (bajar los márgenes un rato después de una detección): no mejora; con 1.5 s y márgenes ×0.5 suma 4 FP.
- **Temporales inútiles con umbral relativo** al margen frontal: no supera al absoluto de 300 µV (2×: +5 gestos pero +3 FP; solo relativo 4×: −8 gestos).
- **Umbral.** Piso de 50 µV pierde 5 gestos; `k_fp` 8 pierde 12; adaptación más rápida suma FP; persistencia de 1.0 s pierde 10.

### Combinación de criterios sobre la misma ventana

Además de quitar criterios de a uno, se estudió cómo **combinar** distintos criterios aplicados a la misma ventana (11 criterios:
amplitud frontal y temporal, derivada, pasa altos, pasa bajos, convolución con la plantilla, tres correlaciones, Teager-Kaiser y
puntaje z robusto). Cada criterio vota si supera el percentil 90 de lo que vale en reposo en los otros archivos, y se probó:
cada uno solo, pares y tríos (todos deben cumplirse), k de n votos, las reglas del filtro combinadas con los votos, y un
promedio difuso de pertenencias. Resumen (30 archivos, 404 gestos):

| Forma de combinar (misma ventana) | Detectados | FP | Conclusión |
|---|---|---|---|
| Reglas del filtro solas (sin vetos de rebote) | 381 | 3 | Referencia. |
| Cada criterio solo (amplitud frontal, derivada, pasa altos…) | 330–384 | 24–70 | Ninguno sirve solo: marcan muchas ventanas de reposo. |
| Pares y tríos que deben cumplirse todos (los mejores) | 323–329 | 7–10 | Menos gestos que las reglas y más FP. |
| Votos: al menos 8 de los 11 criterios | 317 | 3 | Con los mismos FP que las reglas, detecta 60 gestos menos. |
| Promedio difuso de 7 criterios (umbral 0.6) | 270 | 3 | Peor que votar. |
| Reglas Y confirmación por votos (al menos 1 a 3 de 7) | 372–377 | 3 | No reduce los FP; con exigencia alta pierde gestos. |
| **Reglas O votos de 7 evidencias con un frontal activo (5 de 7)** | **386** | 5 | Suma 5 gestos sobre las reglas (en el filtro final los vetos dejan 1 FP). |
| **Ídem con 6 de 7** | **385** | **3** | Suma 4 gestos con los mismos FP. |
| Ídem con 4 de 7 | 393 | 14 | Los FP se disparan. |
| Reglas O promedio difuso de 7 evidencias ≥ 0.6, con un frontal activo | 371 | 4 | Menos que los votos. |

#### Canales virtuales (promedio frontal y diferencia frontal-temporal)

Se probaron de tres formas (umbral en el percentil 90–99 del reposo de los otros archivos):

| Forma de usarlos | Resultado | Conclusión |
|---|---|---|
| **Solos, ignorando las reglas** | El promedio frontal solo: 378 / 404 con 42 FP; exigiendo además la diferencia y la correlación TP9–TP10: 203–275 gestos con 2–6 FP | No sirven como reemplazo. |
| **Como complemento propio** (reglas O criterio virtual) | Hasta +10 gestos pero +4 a +5 FP (percentil 95: 391 con 8 contra 381 con 3) | Peor relación que el voto de evidencias. |
| **Dentro del voto** (6 de 9 evidencias) | +3 a +5 gestos con los mismos FP en los percentiles 95 y 97 (6 de 9: 387 con 4 contra 384 con 4; 386 con 3 contra 381 con 3); igual con el 90 y en la validación sin el archivo evaluado | Inestable, no se adopta. |

Conclusión: combinar criterios en la misma ventana ayuda **solo como complemento de las reglas**, para rescatar ventanas que
las reglas no detectaron cuando varias evidencias independientes coinciden. Como regla de decisión por sí sola (pares, tríos,
votos o promedio difuso) es peor, y como confirmación de las detecciones no quita falsos positivos.

## 6b. Sistema en vivo y grabación (`gestos_tiempo_real.py`)

- **Entrada.** Se suscribe al ZMQ de la Raspberry (`pi/stream.py`, puerto 5555). Cada muestra de EEG llega como un mensaje con
  `TP9_raw … TP10_raw` (y los canales ya filtrados, que no se usan). También llegan `BLINK` y `JAW_CLENCH` (SDK de la vincha) y
  `HORSESHOE` (contacto por sensor: 1 bueno, 2 ok, 3 malo, 4 sin contacto). El SDK **solo se registra para comparar**: nunca
  interviene en una decisión.
- **Mismo filtro que en archivos.** El motor en flujo da decisiones idénticas a `ventanas_deslizantes.py` (0 diferencias en los
  30 archivos y en todas las sesiones grabadas; se comprueba con `--verificar`). Es causal: historia de 13 s, series derivadas
  recalculadas sobre el buffer, decisión cada 0.25 s (por bloques de 55 muestras).
- **Qué se guarda por sesión** (`sesiones/`): `_eeg.csv` (la señal cruda, con el `Trigger` de la barra espaciadora, en el formato de
  los datasets), `_eventos.csv` (cada detección del filtro, del SDK y de mandíbula, y cada marca), `_rangos.csv` (cada gesto
  marcado con el contacto de cada sensor) y `_resumen.txt/.json` (aciertos, perdidos y falsos positivos del filtro y del SDK, y
  el contacto por sensor).
- **Todo en el eje de las muestras.** Las marcas, los BLINK del SDK y las detecciones se fechan con el número de muestra
  recibida, no con el reloj de la PC. Así quedan alineadas con la señal aunque el flujo llegue con cortes o en ráfagas (en
  algunas sesiones llegó solo el 94–97 % de las muestras). Lo que importa es el orden causal.
- **Tablero.** Por canal: señal filtrada sin su base, margen adaptativo, muestras sobre el margen, ventanas detectadas, canal
  ignorado, marcas de la barra espaciadora y BLINK del SDK; contacto por sensor y una línea de **flujo** (muestras por segundo,
  segundos sin datos, mayor lote, atraso de la señal, tiempo de dibujo). La lectura del socket (cada 20 ms) y el dibujo (cada
  100 ms, más lento si la PC no llega) son independientes; `--liviano` achica la ventana y quita la rejilla.
- **Nombre de la grabación.** Variable `NOMBRE` al principio de `gestos_tiempo_real.py` (o `--nombre`), y `--etiqueta` para
  distinguir el tipo de sesión (por ejemplo `2g`): `lara_20261028_101500_2g_eeg.csv`.
- **Para evaluar después.** Los números válidos salen de analizar el `_eeg.csv` (`--imagenes`), no de los contadores en vivo: la
  señal y el `Trigger` están alineados por muestra.
- **Limitaciones del flujo.** `pi/stream.py` fija 220 Hz, pero en algunas sesiones llegaron entre 176 y 207 Hz efectivos (pérdida
  antes de llegar a la PC: UDP local o Wi-Fi). `analisis/prueba_recepcion.py` mide eso sin filtro ni gráficos.

## 6c. Patrón de dos gestos (`gestos_patron.py`)

**Objetivo:** activar/desactivar solo si ocurren dos gestos seguidos, no con un gesto suelto, un parpadeo natural ni ruido.

**Por qué no alcanza con pedirle dos detecciones al filtro.** Con tus gestos (silencio de 0.25 a 0.6 s entre uno y otro) el filtro ve
una sola detección por intento: en una sesión con 9 intentos dio 1 de 9 con 1.6 s de espera y 2 de 9 con 4 s. El filtro se
"duerme" después de detectar (veto de rebote 1 s, retención de la salida 1 s, margen que sube) y trabaja con ventanas de 0.5 s que
juntan dos gestos pegados. Achicar la ventana a 0.3 s con rebote y retención de 0.25 s da 8 de 9, pero con 3 falsos positivos y
deja de ser el filtro validado (en los 30 archivos de un solo gesto pasa de 1 a 12 detecciones fuera de marca, en parte inflado por
la fragmentación).

**Qué hace.** Una capa encima del filtro intacto, que mira la señal muestra por muestra:
1. **Ráfagas:** muestras en que un frontal vivo y, si hay un temporal vivo, un temporal (±0.15 s) superan su margen adaptativo;
   las separadas menos de 0.10 s son una ráfaga. Válida si tiene al menos 4 muestras y un pico de 1.5 veces el margen o más.
   Descarta como artefacto una ráfaga con un pico de más de 20 veces el margen (sacarse la vincha) o de más de 1 s.
2. **Patrón:** dos ráfagas válidas separadas por un silencio de hasta 1.6 s.
3. **Silencio verdadero entre los gestos:** el RMS del valle (en el frontal dominante) debe ser menor al 60 % del de los gestos, el
   primer gesto debe salir de reposo (0.25 s antes) y la señal debe volver a reposo (0.25 s después del último, se espera ese
   tiempo antes de avisar). Es lo que distingue dos gestos del ruido constante: en gestos reales el valle es de 0.16 a 0.61; en el
   ruido que dio un falso positivo la señal entre ráfagas valía 1.0 a 1.5 veces la de las ráfagas.
4. **Compuerta:** además el filtro de siempre debe haber detectado una ventana dentro del patrón, y por una regla de amplitud o de
   temporales inútiles (no por las vías de rescate). Así esta capa solo puede quitar falsos positivos, no agregarlos.
5. **Bloqueo** de 0.5 s después de avisar.

Se avisa unos 0.35 s después de terminar el segundo gesto (0.10 s para cerrar la ráfaga más 0.25 s de reposo posterior).
Todos los valores se pueden cambiar con opciones (`--gap`, `--min-sobre`, `--ratio`, `--sep-max`, `--valle-max`, `--post`,
`--previo`, `--ratio-max`, `--rafaga-max`, `--refractario`, `--sin-silencio`, `--sin-compuerta`).

**Resultado en la única sesión con dos gestos por marca (9 marcas válidas):** 6 patrones acertados, 0 falsos positivos. Las 3
perdidas (explicadas con `--explicar`): en dos de ellas el segundo gesto es casi inexistente (1.07 y 1.36 veces su margen) y en la
tercera las dos ráfagas son válidas pero el silencio entre ellas (0.67) supera por poco el límite de 0.6. En las sesiones de un
solo gesto, los patrones que aparecen fuera de marca son pocos (1 en una sesión, 0 en las demás con los valores por defecto) y
algunos pueden ser pares de gestos reales no marcados (guiños).

**Probado y no adoptado**
- *Puntuación difusa* en vez de cortes duros (`--difuso --theta`): recupera 1 o 2 aciertos pero suma 2 a 4 falsos positivos; el
  puntaje de los aciertos (0.12 a 1.0) se solapa con el de los falsos (0.62 a 0.97), así que no se pueden elegir pertenencias que
  los separen con tan pocos ejemplos.
- *Exigir que todos los frontales vivos respondan* (`--bilateral`): quita el falso positivo pero también aciertos reales (los
  gestos reales a veces son asimétricos).
- *Veto "un temporal supera al frontal"* (`tp_mayor_f`): quita gestos reales (393/404 con 2.0, 365/404 con 1.0) y casi no baja los
  falsos positivos.
- *Veto de saturación contando solo canales útiles* (`sat_modo`): +2 gestos pero +2 falsos positivos.
- *Veto difuso y vías de rescate sin contar un temporal ignorado* (`grado_veto_vivos`, `grado_resc_vivos`): el veto difuso cuenta la
  correlación TP9–TP10 aunque TP10 esté ignorado (para un frontal ignorado sí se descarta). Corregirlo detecta gestos que hoy se
  pierden (ej. TP9 1.40 y FP2 2.06 veces su margen con grado 0.38 contra 0.40) y no cambia los 30 archivos (397/404 con 1), pero
  empeora el patrón (6 → 4 aciertos de 9) porque los cortes de esa capa quedan justo en el borde (1.4999 contra 1.5; 0.69 contra
  0.6). Quedan como opciones apagadas.

### Qué mostró cada sesión grabada

| Sesión | Resultado del filtro (aciertos de marcas, falsos positivos) | Observaciones |
|---|---|---|
| 150228, 151157 | 7/7 con 2 y 8/8 con 4 | Primeras pruebas en vivo; falsos positivos de eventos débiles con buen contacto o gestos no marcados. |
| 204703, 204922 | — / 3/4 con 0 | Flujo con cortes (94 % y menos de las muestras) y TP10 sin contacto; marcas desalineadas con el EEG (antes de alinear por muestra). |
| 211849, 213100 | 3/3 con 0 y 8/8 con 0 | TP9 y TP10 casi sin contacto: detectó con los frontales. |
| 222918 | 13/13 con 11 | Guiños y toques en la frente sin marcar: el filtro no distingue el tipo de gesto. |
| 233819 | 11/14 con 8 | Racha de ruido de 60 a 83 s (márgenes de 300 a 1000 µV); los gestos perdidos quedaron bajo el margen o frenados por saturación. |
| 174803 (2g) | 10/10 con 4 (por gesto suelto); patrón 6/9 con 0 | Primera sesión con dos gestos por marca; la vincha se manipuló al principio y al final. |

## 6d. Cómo se evalúa y qué errores se ven

- `gestos_tiempo_real.py --imagenes SESION_eeg.csv`: una imagen por cada detección fuera de marca (posible falso positivo) y por
  cada marca no detectada, con la señal, el margen de cada canal, qué regla detectó o frenó.
- `gestos_patron.py --evaluar SESION_eeg.csv [--detalle]`: aciertos, perdidos y falsos positivos del patrón, y las ráfagas de
  cada marca.
- `gestos_patron.py --explicar SESION_eeg.csv`: para cada marca perdida, el valor medido de cada comprobación contra su umbral y
  una imagen con el margen y el pico mínimo exigido dibujados.
- Una marca cuenta como acierto si el patrón se avisa entre 0.5 s antes y 1.6 s después de la marca. Las marcas de menos de 0.5 s se
  ignoran (toques sueltos de la barra).

## 7. Trabajos relacionados y qué aportaron

Se leyeron en detalle los trabajos 1 a 3, 7 y 8 a 12; de los trabajos 4 y 5 solo se accedió al resumen, y el 6 se conoce por su
cita en el trabajo 1.

| # | Trabajo | Cómo detecta o separa el ruido | Qué se tomó o probó |
|---|---|---|---|
| 1 | E. Shachar, A. Lev, O. Rosen, "MED: Muse-based Eye-blink Detection Algorithm Using a Single EEG Channel", IEEE SPMB 2022. <https://isip.piconepress.com/conferences/ieee_spmb/2022/papers/l01_03.pdf> | Un canal (TP9). Mínimos separados 0.1 s y 70 µV bajo la media; máximo asociado en menos de 0.5 s y de 15 µV (caída y subida); fin donde vuelve a ±5 µV. Umbrales fijos; proponen umbrales dinámicos y dos sensores como trabajo futuro. | Umbrales dinámicos y varios canales ya están en este filtro. La forma bifásica no mejoró. |
| 2 | R. López-Ahumada, R. Jiménez-Naharro, F. Gómez-Bravo, "A Hardware-Based Configurable Algorithm for Eye Blink Signal Detection Using a Single-Channel BCI Headset", *Sensors* 23(11):5339, 2023. <https://pmc.ncbi.nlm.nih.gov/articles/PMC10255990/> | Neurosky. Diferenciador (pasabajos de ~4 Hz) y umbrales positivo y negativo; el rebote se descarta porque su valle no pasa el umbral. Produce falsas dobles detecciones por rebote en parpadeos suaves. | La regla de **rebote** coincide en intención. |
| 3 | T. Kawaguchi, K. Ono, H. Hikawa, "Electroencephalogram-Based Facial Gesture Recognition Using Self-Organizing Map", *Sensors* 24(9):2741, 2024. <https://pmc.ncbi.nlm.nih.gov/articles/PMC11085705/> | 16 canales; potencia por banda con un mapa autoorganizado; no filtra los artefactos. 97.6 % con 2 gestos y 77 % con 7. | Se usó el espectro como evidencia (pasa altos y pasa bajos en paralelo). No se usa clasificador entrenado. |
| 4 | M. Agarwal, R. Sivakumar, "Blink: A Fully Automated Unsupervised Algorithm for Eye-Blink Detection in EEG Signals", 57th Allerton Conf., 2019, doi:10.1109/ALLERTON.2019.8919795. <https://par.nsf.gov/biblio/10321749> | Aprende el perfil de parpadeo de cada usuario sin entrenamiento previo. (Solo resumen.) | Un perfil propio como característica no mejoró. |
| 5 | "Algorithm for Detection of Raising Eyebrows and Jaw Clenching Artifacts in EEG Signals Using Neurosky Mindwave Headset", 2021. <https://repositorioacademico.upc.edu.pe/handle/10757/653818> | Energía, cruces por cero y bloques; 90 % de detección. (Solo resumen.) | La energía y los ciclos están en las evidencias. |
| 6 | M. Lo Giudice et al., IJCNN 2020 (red convolucional de 4 sensores para parpadeos voluntarios). Citado en el trabajo 1. | Aprendizaje supervisado. | No se probó. |
| 7 | J. Pan, W. J. Tompkins, "A Real-Time QRS Detection Algorithm", *IEEE Trans. Biomed. Eng.* 32(3):230–236, 1985. | Derivada, cuadrado e integración en 150 ms; umbrales adaptativos entre ruido y señal; refractario y descarte de la onda T. | Umbral entre ruido y señal, rebote y la derivada como evidencia. |
| 8 | S. Moya, M. Hadad, P. Donato, M. Funes, "Adaptive thresholding algorithm for detection of coded signals in narrowband powerline communication systems", *Australian J. Electrical and Electronics Eng.* 18(2):91–103, 2021, doi:10.1080/1448837X.2021.1935415. | Umbral entre ruido y señal; crítica a los umbrales que caen sin señal. | Piso por ventana de ruido y nivel de los gestos. |
| 9 | M. Carrascosa, M. Hadad, M. Funes, P. Donato, "Evaluación de métodos de umbralización dinámica para detección de señales codificadas", XIV RPIC, 2011, pp. 370–375. | Compara umbrales dinámicos y valida una detección por ventana. | Retención de la salida (`mantener_s`). |
| 10 | S. Solnik, P. DeVita, P. Rider, B. Long, T. Hortobágyi, "Teager–Kaiser Operator improves the accuracy of EMG onset detection independent of signal-to-noise ratio", *Acta Bioeng. Biomech.* 10(2):65–68, 2008. <https://pmc.ncbi.nlm.nih.gov/articles/PMC2596643> | Operador de energía de Teager-Kaiser antes de un umbral de 3 desvíos del reposo (15 con el operador), sostenido más de 25 muestras. Error de inicio de 40 ms contra 229 ms. | Se probó su versión causal como evidencia: **suma 8 gestos pero 10 FP**, no se usa. |
| 11 | H. Nolan, R. Whelan, R. B. Reilly, "FASTER: Fully Automated Statistical Thresholding for EEG artifact Rejection", *J. Neurosci. Methods* 192(1):152–162, 2010. <https://www.tara.tcd.ie/items/b3642e2f-7a4d-4ba4-a369-a39e988d985d> | Umbrales estadísticos (z) sobre propiedades de canal y componentes. | Se probó un puntaje z robusto (mediana y MAD de los 10 s previos) como evidencia: +8 gestos pero +9 FP, no se usa. |
| 12 | T. Mullen et al., "Real-time neuroimaging and cognitive monitoring using wearable dry EEG", *IEEE Trans. Biomed. Eng.* 62(11):2553–2567, 2015; C.-Y. Chang et al., "Evaluation of Artifact Subspace Reconstruction…", *IEEE Trans. Biomed. Eng.* 67(4):1114–1121, 2020; M. Miyakoshi, "Artifact subspace reconstruction: a candidate for a dream solution for EEG studies, sleep or awake", *Sleep* 46(12):zsad241, 2023. <https://pmc.ncbi.nlm.nih.gov/articles/PMC10710985/> | ASR: umbral de k desvíos sobre un nivel de referencia limpio, componente por componente; k típico de 10 a 30; la referencia fija limita la corrección cuando es mala. | Es la misma idea que el umbral adaptativo (referencia de ruido previo). Este filtro actualiza la referencia continuamente. |

Conclusión de la revisión: los detectores publicados usan un solo canal y umbrales casi fijos, y se evalúan en grabaciones cortas
con pocas personas. Este filtro suma varios canales, umbrales que se adaptan y evidencias paralelas que complementan la
decisión, y se probó en 30 archivos de 8 personas. De las ideas adicionales de esos trabajos (forma bifásica, perfil propio,
Teager-Kaiser, puntaje z, clasificadores), ninguna mejoró el resultado final.

## 7b. Variantes que todavía no se probaron

- **Más grabaciones con el patrón de dos gestos**, con buen contacto en los cuatro sensores y varias personas: 20 o más intentos de
  cada gesto (dos cejas seguidas, dos parpadeos seguidos), y varios minutos de actividad normal sin marcar (hablar, reír, fruncir el
  ceño, parpadear natural, mover la cabeza) para medir falsos positivos por hora. Es lo que más limita las conclusiones.
- **Pedir el par de canales solo si el compañero está en buen estado**, tratando como ignorado un canal cuyo ruido ya viene
  subiendo (hoy un canal "en duda", no declarado todavía ignorado, frena gestos).
- **Puntuación difusa del patrón calibrada con más ejemplos** (con 9 patrones reales no se pudo).
- **Distinguir guiños de parpadeos** por asimetría entre FP1 y FP2 cuando los dos tienen contacto.
- **Calidad de contacto por canal medida en línea** (ruido de alta frecuencia o de la red eléctrica), en lugar del indicador global.
- **Umbrales por canal que dependan de la variabilidad del ruido** en lugar de un `k` fijo para frontales y otro para temporales.
- **Plantillas distintas por gesto** (una lenta para el parpadeo y otra de ráfaga para las cejas).

## 8. Cómo usarlo

```bash
# --- grabar (antes: poner el nombre en NOMBRE arriba de gestos_tiempo_real.py, o usar --nombre) ---
python gestos_tiempo_real.py --ip 192.168.1.50 --nombre lara --etiqueta 2g        # tablero; ESPACIO apretado durante el gesto; Q para cerrar y guardar
python gestos_tiempo_real.py --ip 192.168.1.50 --liviano                           # tablero más liviano si la PC no llega
python gestos_patron.py --consola --ip 192.168.1.50 --nombre lara --etiqueta 2g    # en vivo, avisa solo los patrones de dos gestos

# --- ver los errores de una sesión grabada ---
python gestos_tiempo_real.py --imagenes sesiones/lara_..._eeg.csv                  # falsos positivos y gestos perdidos del filtro
python gestos_patron.py --evaluar sesiones/lara_..._2g_eeg.csv --detalle           # aciertos / perdidos / falsos del patrón
python gestos_patron.py --explicar sesiones/lara_..._2g_eeg.csv                    # por qué no se detectó cada marca perdida

# --- análisis (carpeta analisis/) ---
python analisis/prueba_recepcion.py --ip 192.168.1.50 --segundos 60                # ¿llega completo el flujo de la Raspberry?
python analisis/ventanas_deslizantes.py --todos                                    # tabla con los 30 datasets
python analisis/ver_errores.py --todos --contacto-min 0                            # imágenes de errores de los 30 datasets
python gestos_tiempo_real.py --verificar dataset.csv                               # el filtro en vivo da lo mismo que el de archivos
python gestos_patron.py --barrer sesiones/..._eeg.csv                              # prueba combinaciones de los valores del patrón
```

Las opciones de experimentos descartados siguen disponibles y apagadas por defecto (`--score-perfil`, `--plantilla aprendida`,
`--espera-par`, `--par-difuso`, `--lax-modo`, `--hf-lf-min`, `--centroide-max`, `--conc-min`, `--cont-min`, `--k-pend`;
`sat_modo`, `tp_mayor_f`, `grado_veto_vivos` y `grado_resc_vivos` en el filtro).

## 9. Límites y precauciones

- Los parámetros se ajustaron con estos 30 archivos. La validación por mitades de personas muestra que los parámetros buscados
  automáticamente no se sostienen del todo; se fijó solo lo que se repitió en varias búsquedas. Falta validar con grabaciones
  nuevas, sobre todo de otras personas.
- Las etiquetas del Trigger son largas (1–11 s) y varios falsos positivos parecen movimientos reales no etiquetados.
- Con contacto malo la detección es de 91 %. `Sensores_OK` es un indicador global; no hay calidad de contacto por canal.
- No distingue entre tipos de gesto (los guiños también disparan).
- Con sensores caídos el filtro sigue funcionando con los que quedan (probado con TP10 muerto y con FP1 casi sin contacto), pero
  decide con menos información y los gestos débiles se pierden; el patrón de dos gestos se vuelve más sensible a los cortes de
  umbral.
- La capa de patrón y sus valores se ajustaron con una única sesión de dos gestos por marca (9 marcas): faltan datos para
  calibrarla.
- El tiempo en reposo de las grabaciones es muy corto (menos de 6 min), así que los falsos positivos por minuto son una
  estimación pobre del comportamiento en uso real.

## 10. Qué gesto usar para activar y desactivar

El filtro es el mismo para ambos gestos; lo que cambia es cuánto se parece cada gesto a lo que una persona hace sin querer.

| Medida (filtro de 386/404) | Levantar cejas | Dos parpadeos seguidos |
|---|---|---|
| Archivos / gestos | 18 / 259 | 12 / 145 |
| Detección, todos los archivos | 245 / 259 (95 %) | 138 / 145 (95 %) |
| Detección, contacto bueno | 138 / 139 (99 %), 9 archivos | 49 / 49 (100 %), 4 archivos |
| Detección, contacto malo | 107 / 120 (89 %), 9 archivos | 89 / 96 (93 %), 8 archivos |
| Falsos positivos en reposo | 0 en 4.2 min | 1 en 1.6 min (la cola de un gesto) |
| Duración mediana del bloque del Trigger | 2.3 s | 3.2 s |

Las diferencias de detección están dentro del error estadístico y el tiempo en reposo es muy corto para comparar falsos
positivos por minuto.

**Recomendación para accionar el motor** (un falso positivo cuesta mucho más que uno perdido):
- Exigir un patrón de **dos gestos seguidos** con silencio entre ellos (`gestos_patron.py`), no un gesto suelto. Se prefiere un
  gesto deliberado y difícil de hacer sin querer; los guiños, las muecas y los movimientos de cabeza también disparan el filtro.
- **Más estricto para encender que para apagar:** encender con la confirmación de dos gestos, apagar con uno solo (un falso
  positivo al apagar solo detiene el motor). No está implementado todavía; es una decisión de la aplicación que usa la salida.
- Después de cada aviso conviene un bloqueo de 1 a 2 s para que el mismo gesto no cambie el estado dos veces.
- Si el contacto de los sensores no es aceptable, no habilitar el encendido.
