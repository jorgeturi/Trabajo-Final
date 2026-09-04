import numpy as np
from pythonosc import dispatcher, osc_server
import threading
import time
import sys
from collections import deque

FS = 220.0
BUFFER_SIZE = 256

ch_tp9 = deque(np.zeros(BUFFER_SIZE), maxlen=BUFFER_SIZE)
window = np.hanning(BUFFER_SIZE)
freqs = np.fft.rfftfreq(BUFFER_SIZE, d=1.0/FS)

# Banda de Comando (Cejas: 15-40 Hz)
idx_cejas = np.where((freqs >= 15.0) & (freqs <= 40.0))[0]
num_bins_cejas = len(idx_cejas)

# Banda de Veto (Mandíbula/Ruido Mecánico: 60-90 Hz)
idx_veto = np.where((freqs >= 60.0) & (freqs <= 90.0))[0]

# Memoria para el cálculo de coherencia
historial_fft = deque([np.zeros(num_bins_cejas) for _ in range(15)], maxlen=15)

# --- UMBRALES DE SEGURIDAD ESTRICTOS ---
MULTIPLICADOR_PICO = 2.5
ENERGIA_MINIMA_CEJAS = 300
MAX_ENERGIA_VETO = 800       # Si la alta frecuencia supera esto, bloqueamos la orden
FRAMES_REQUERIDOS = 2        # Cuántos ciclos seguidos debe durar el gesto (Anti-Glitch)
COOLDOWN = 1.2

ultimo_disparo = 0
frames_activos = 0

def eeg_handler(address, *args):
    ch_tp9.append(args[0])

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

def detector_blindado_tp9():
    print("==================================================")
    print("🧠 BCI - Modo Seguro (Anti-Rebote + Veto de Alta Freq)")
    print("==================================================\n")
    
    global ultimo_disparo, frames_activos
    
    while True:
        señal = np.array(ch_tp9)
        if len(señal) == 0: continue
        
        señal = señal - np.mean(señal)
        fft_completa = np.abs(np.fft.rfft(señal * window))
        
        fft_cejas = fft_completa[idx_cejas]
        fft_veto = fft_completa[idx_veto]
        
        # 1. Coherencia en banda baja
        promedio_bins = np.mean(historial_fft, axis=0) + 1.0 
        bins_encendidos = np.sum(fft_cejas > (promedio_bins * MULTIPLICADOR_PICO))
        porcentaje_cejas = (bins_encendidos / num_bins_cejas) * 100.0
        energia_cejas = np.mean(fft_cejas)
        
        # 2. Energía en banda alta (Ruido/Mandíbula)
        energia_veto = np.mean(fft_veto)
        
        historial_fft.append(fft_cejas)
        tiempo_actual = time.time()
        alerta = "                          "
        
        # --- LÓGICA DE DECISIÓN CON ANTI-REBOTE ---
        if porcentaje_cejas > 70.0 and energia_cejas > ENERGIA_MINIMA_CEJAS:
            if energia_veto < MAX_ENERGIA_VETO:
                frames_activos += 1
            else:
                frames_activos = 0
                alerta = "⚠️ VETO ACTIVO (Ruido)"
        else:
            frames_activos = 0

        # Disparo final
        if frames_activos >= FRAMES_REQUERIDOS:
            if (tiempo_actual - ultimo_disparo) > COOLDOWN:
                alerta = "🟢 ¡COMANDO SEGURO CONFIRMADO! 🟢"
                ultimo_disparo = tiempo_actual
                frames_activos = 0 # Reiniciar contador tras disparo
                
        # Interfaz de consola
        sys.stdout.write(f"\rTP9 -> Coherencia: {porcentaje_cejas:3.0f}% | Veto(HF): {energia_veto:5.0f} | {alerta}      ")
        sys.stdout.flush()
        
        time.sleep(0.05) 

if __name__ == "__main__":
    threading.Thread(target=iniciar_osc, daemon=True).start()
    detector_blindado_tp9()