# config.py - valores ajustables de la detección EEG y de los motores

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
