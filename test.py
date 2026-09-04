import numpy as np
from pythonosc import dispatcher, osc_server
import threading
import time
import sys
from collections import deque

FS = 220.0
BUFFER_SIZE = 256

ch_data = [deque(np.zeros(BUFFER_SIZE), maxlen=BUFFER_SIZE) for _ in range(4)]
window = np.hanning(BUFFER_SIZE)
freqs = np.fft.rfftfreq(BUFFER_SIZE, d=1.0/FS)

idx_eval = np.where((freqs >= 15.0) & (freqs <= 45.0))[0]
num_bins = len(idx_eval)

# Memoria basal
historial_fft = [deque(maxlen=40) for _ in range(4)]
nombres = ['TP9', 'FP1', 'FP2', 'TP10']

# --- PARÁMETROS CALIBRADOS CON TU LOG ---
MULTIPLICADOR = 2.0          # Reducido de 2.5 a 2.0
UMBRAL_COHERENCIA = 40.0     # Reducido de 60% a 40%
TECHO_SATURACION = 6000.0    # Evita falsos positivos al mover la vincha

def eeg_handler(address, *args):
    for i in range(4):
        ch_data[i].append(args[i])

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

def depurador_calibrado():
    print("=======================================================================================")
    print("🧠 BCI - Depurador con Bloqueo de Base (Adaptado a TP9)")
    print("=======================================================================================\n")
    
    while True:
        interfaz = "\r"
        
        for i in range(4):
            señal = np.array(ch_data[i])
            if len(señal) < BUFFER_SIZE: continue
            
            señal = señal - np.mean(señal)
            fft_actual = np.abs(np.fft.rfft(señal * window))[idx_eval]
            promedio_actual = np.mean(fft_actual)
            
            # 1. PERÍODO DE CALENTAMIENTO
            if len(historial_fft[i]) < 40:
                historial_fft[i].append(fft_actual)
                interfaz += f"{nombres[i]}: [CALIBRANDO...] | "
                continue
                
            # 2. CÁLCULO DE MEDIANA (La base ahora estará protegida)
            mediana_base = np.median(historial_fft[i], axis=0) + 1.0
            promedio_base = np.mean(mediana_base)
            
            # 3. LÓGICA DE DETECCIÓN Y BLOQUEO
            if promedio_actual > TECHO_SATURACION:
                estado = "⚠️ SATURADO"
                porcentaje = 0.0
                # NO guardamos en historial (Bloqueo por saturación mecánica)
            else:
                bins_encendidos = np.sum(fft_actual > (mediana_base * MULTIPLICADOR))
                porcentaje = (bins_encendidos / num_bins) * 100.0
                
                if porcentaje > UMBRAL_COHERENCIA:
                    estado = "🟢 CEJAS"
                    # CRÍTICO: NO GUARDAMOS ESTE FRAME EN EL HISTORIAL
                    # Esto evita que el algoritmo se "acostumbre" al gesto.
                else:
                    estado = "  reposo"
                    # Solo aprendemos el ruido cuando el usuario está relajado
                    historial_fft[i].append(fft_actual)
            
            interfaz += f"{nombres[i]}: {porcentaje:3.0f}% ({promedio_actual:4.0f}/{promedio_base:4.0f}) {estado} | "
            
        sys.stdout.write(interfaz)
        sys.stdout.flush()
        time.sleep(0.1)

if __name__ == "__main__":
    threading.Thread(target=iniciar_osc, daemon=True).start()
    depurador_calibrado()