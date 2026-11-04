# config.py - valores ajustables de la detección EEG y de los motores

# --- Qué detector produce la activación ---
# "adaptativo": filtro de detector_gestos.py (umbral por canal, 2 ráfagas); publica paquetes DETECCION por ZMQ.
# "umbral": el detector simple de abajo (UMBRAL_TP / UMBRAL_FP fijos).
DETECTOR = "adaptativo"

# --- Detección EEG (flag que se envía a la Pico) ---
BANDA_DETECTOR = (1.5, 30.0)   # Hz, pasabanda de la señal usada para el umbral
VENTANA_EEG = 100              # muestras de la ventana deslizante
UMBRAL_TP = 50.0               # pico |señal filtrada| mínimo en algún TP (TP9/TP10)
UMBRAL_FP = 100.0              # pico |señal filtrada| mínimo en algún FP (FP1/FP2)

# --- Zona muerta de los motores ---
# |valor filtrado| (mismas unidades que el acelerómetro) por debajo del cual el motor queda en 0.
ZONA_MUERTA_X = 150.0          # motor 1 (eje X)
ZONA_MUERTA_Z = 150.0          # motor 2 (eje Z)

# --- Verificación de orientación (eje Y) ---
# Media de Y por debajo del umbral = vincha al revés -> motores en 0
ACC_VERIF_MUESTRAS = 26        # muestras ACC que se promedian (~0.5 s)
ACC_Y_UMBRAL_INVERTIDA = 0.0

# --- Submuestreo del EEG hacia el detector adaptativo ---
# El detector procesa 1 de cada N muestras: 220 Hz / 2 = 110 Hz (a 220 Hz no llegaba a procesar los lotes a tiempo).
SUBMUESTREO_DETECTOR = 2

# --- Estado que se informa a la Pico (INICIO, CALIBRACION, REPOSO, MARCHA, EMERGENCIA) ---
# Un electrodo tiene falla cuando su contacto (horseshoe, 1 a 4) es mayor que CONTACTO_FALLA.
# Falla de los DOS FP (FP1 y FP2) o de los DOS TP (TP9 y TP10) durante al menos este tiempo = EMERGENCIA.
# Un gesto hace caer el contacto un instante, por eso no se declara emergencia antes.
CONTACTO_FALLA = 2
SIN_CONTACTO_EMERGENCIA_S = 5.0
