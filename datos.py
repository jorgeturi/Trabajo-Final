import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from scipy import signal
from pythonosc import dispatcher, osc_server
import threading
from collections import deque
import time

FS = 220.0
F0 = 50.0   
Q = 30.0
b, a = signal.iirnotch(F0, Q, FS)

MAX_SAMPLES = 440
ch_names = ['TP9 (Oreja Izq)', 'FP1 (Frente Izq)', 'FP2 (Frente Der)', 'TP10 (Oreja Der)']
raw_data = {i: deque([800]*MAX_SAMPLES, maxlen=MAX_SAMPLES) for i in range(4)}

# --- UMBRALES DE PRUEBA (Los vas a cambiar tú en un momento) ---
UMBRAL_MANDIBULA = 400
UMBRAL_CEJAS = 500      

cooldown = 0
ultimo_print = time.time()

def eeg_handler(address, *args):
    for i in range(4):
        raw_data[i].append(args[i])

def start_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

if __name__ == "__main__":
    osc_thread = threading.Thread(target=start_osc, daemon=True)
    osc_thread.start()

    fig, axs = plt.subplots(4, 1, figsize=(12, 8), sharex=True)
    fig.suptitle('Calibrador BCI - Busca tus propios umbrales')
    
    lines = []
    colors = ['blue', 'cyan', 'yellow', 'magenta']
    x_axis = np.arange(MAX_SAMPLES)
    
    for i in range(4):
        line, = axs[i].plot(x_axis, np.zeros(MAX_SAMPLES), color=colors[i], label=ch_names[i])
        lines.append(line)
        axs[i].legend(loc="upper right")
        axs[i].set_facecolor('black')

    def update(frame):
        global cooldown, ultimo_print
        filt_data = {}
        
        for i in range(4):
            y_raw = np.array(raw_data[i])
            y_filt = signal.lfilter(b, a, y_raw)
            filt_data[i] = y_filt
            
            valid_y = y_filt[40:] 
            axs[i].set_ylim(np.min(valid_y) - 20, np.max(valid_y) + 20)
            lines[i].set_ydata(y_filt)
            
        # --- LÓGICA DE DETECCIÓN MEJORADA ---
        # Usamos np.diff para matar las olas lentas de la señal y quedarnos solo con el movimiento agudo
        energia_orejas = np.std(np.diff(filt_data[0][-110:])) + np.std(np.diff(filt_data[3][-110:]))
        energia_frente = np.std(np.diff(filt_data[1][-110:])) + np.std(np.diff(filt_data[2][-110:]))

        if cooldown > 0:
            cooldown -= 1
        else:
            # 1. Evaluamos Mandíbula (Prioridad por ser más fuerte)
            if energia_orejas > UMBRAL_MANDIBULA:
                print(f"\n[!!!] MANDÍBULA DETECTADA | Orejas: {int(energia_orejas)} | Frente: {int(energia_frente)}")
                cooldown = 20
                
            # 2. Evaluamos Cejas (Solo si la mandíbula está relajada)
            elif energia_frente > UMBRAL_CEJAS and energia_orejas < (UMBRAL_MANDIBULA * 0.8):
                print(f"\n[*] CEJAS DETECTADAS | Orejas: {int(energia_orejas)} | Frente: {int(energia_frente)}")
                cooldown = 20
                
            # 3. MODO TELEMETRÍA: Si no hay comando, imprime los niveles base cada 1 segundo
            elif time.time() - ultimo_print > 1.0:
                print(f"Reposo -> Energía Orejas: {int(energia_orejas)} | Energía Frente: {int(energia_frente)}")
                ultimo_print = time.time()

        return lines

    ani = animation.FuncAnimation(fig, update, interval=50, blit=False)
    plt.tight_layout()
    plt.show()