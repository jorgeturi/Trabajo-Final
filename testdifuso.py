import numpy as np
from pythonosc import dispatcher, osc_server
import threading
import time
from collections import deque

FS = 220.0
BUFFER_SIZE = 256

ch_data = [deque(np.zeros(BUFFER_SIZE), maxlen=BUFFER_SIZE) for _ in range(4)]
window = np.hanning(BUFFER_SIZE)
freqs = np.fft.rfftfreq(BUFFER_SIZE, d=1.0/FS)
idx_eval = np.where((freqs >= 15.0) & (freqs <= 45.0))[0]
num_bins = len(idx_eval)

historial_fft = [deque(maxlen=40) for _ in range(4)]
nombres = ['TP9', 'FP1', 'FP2', 'TP10']

ultimo_disparo = [0.0, 0.0, 0.0, 0.0]
TIEMPO_COOLDOWN = 2.0 

UMBRAL_ENERGIA_MINIMA = 600.0  
TECHO_SATURACION = 6000.0       

def grado_membresia(valor, min_val, max_val):
    if valor <= min_val: return 0.0
    if valor >= max_val: return 1.0
    return (valor - min_val) / (max_val - min_val)

def inferencia_difusa(energia_ratio, coherencia_pct):
    energia_fuerte = grado_membresia(energia_ratio, 1.3, 2.5) 
    coherencia_alta = grado_membresia(coherencia_pct, 20.0, 70.0) 
    certeza_base = min(energia_fuerte, coherencia_alta)
    return certeza_base * 100.0

def eeg_handler(address, *args):
    for i in range(4):
        ch_data[i].append(args[i])

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

def depurador_blindado_log():
    print("=======================================================================================")
    print("🧠 BCI - Modo Log Continuo (Para Depuración)")
    print("Formato: Certeza% (Energía Absoluta / Ratio Relativo) Estado")
    print("=======================================================================================\n")
    
    while True:
        interfaz = ""
        tiempo_actual = time.time()
        
        for i in range(4):
            señal = np.array(ch_data[i])
            if len(señal) < BUFFER_SIZE: continue
            
            señal = señal - np.mean(señal)
            fft_actual = np.abs(np.fft.rfft(señal * window))[idx_eval]
            energia_actual = np.mean(fft_actual)
            
            if len(historial_fft[i]) < 40:
                historial_fft[i].append(fft_actual)
                interfaz += f"{nombres[i]}: [CALIBRANDO...] | "
                continue
                
            mediana_base = np.median(historial_fft[i], axis=0) + 1.0
            energia_base = np.mean(mediana_base)
            
            ratio_energia = energia_actual / energia_base
            bins_encendidos = np.sum(fft_actual > (mediana_base * 1.5))
            coherencia_actual = (bins_encendidos / num_bins) * 100.0
            
            estado = "  silencio"
            certeza = 0.0
            
            if energia_actual > TECHO_SATURACION:
                estado = "⚠️ SATURAC"
            elif energia_actual < UMBRAL_ENERGIA_MINIMA:
                estado = "  silencio"
                historial_fft[i].append(fft_actual) 
            elif (tiempo_actual - ultimo_disparo[i]) < TIEMPO_COOLDOWN:
                estado = "⏳ ENFRIAM "
            else:
                certeza = inferencia_difusa(ratio_energia, coherencia_actual)
                
                if certeza > 75.0:
                    estado = "🟢 CEJAS! "
                    ultimo_disparo[i] = tiempo_actual
                else:
                    estado = "  evaluando"
                    if certeza < 20.0:
                        historial_fft[i].append(fft_actual)
            
            interfaz += f"{nombres[i]}: {certeza:3.0f}% ({energia_actual:4.0f} / {ratio_energia:3.1f}x) {estado} | "
            
        if interfaz:
            print(interfaz)
            
        time.sleep(0.15) # Más lento para que puedas copiar tranquilo

if __name__ == "__main__":
    threading.Thread(target=iniciar_osc, daemon=True).start()
    depurador_blindado_log()