import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from scipy import signal
from pythonosc import dispatcher, osc_server
import threading
from collections import deque

# --- CONFIGURACIÓN DE MUESTREO ---
FS = 220.0
MAX_SAMPLES = 440 # 2 segundos de historial en pantalla
ch_names = ['TP9 (Oreja Izq)', 'FP1 (Frente Izq)', 'FP2 (Frente Der)', 'TP10 (Oreja Der)']

# 1. Filtro Notch (Elimina 50 Hz de la red eléctrica)
b_notch, a_notch = signal.iirnotch(50.0, 30.0, FS)

# 2. Filtro Bandpass (Deja pasar 10 Hz a 40 Hz, elimina latido y estática)
nyq = 0.5 * FS
low = 1 / nyq
high = 10.0 / nyq
b_band, a_band = signal.butter(4, [low, high], btype='band')

# Colas de datos crudos
raw_data = {i: deque([0]*MAX_SAMPLES, maxlen=MAX_SAMPLES) for i in range(4)}

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

    # --- CONFIGURACIÓN DE INTERFAZ GRÁFICA ---
    fig, axs = plt.subplots(4, 1, figsize=(10, 8), sharex=True)
    fig.suptitle('Osciloscopio BCI: Filtros Notch (50Hz) + Bandpass (10-40Hz)')
    
    lines = []
    colors = ['#00ff00', '#ffff00', '#00ffff', '#ff00ff']
    x_axis = np.arange(MAX_SAMPLES)
    
    for i in range(4):
        line, = axs[i].plot(x_axis, np.zeros(MAX_SAMPLES), color=colors[i], label=ch_names[i])
        lines.append(line)
        axs[i].legend(loc="upper right")
        axs[i].set_facecolor('black')
        axs[i].set_ylim(-250, 250) # Rango ajustado para la señal filtrada

    def update(frame):
        for i in range(4):
            y_raw = np.array(raw_data[i])
            
            # DSP: Filtrado en cascada
            y_notch = signal.lfilter(b_notch, a_notch, y_raw)
            y_band = signal.lfilter(b_band, a_band, y_notch)
            
            # Actualizamos la gráfica
            lines[i].set_ydata(y_band)
            
        return lines

    try:
        ani = animation.FuncAnimation(fig, update, interval=50, blit=False)
        plt.tight_layout()
        plt.show()
    except KeyboardInterrupt:
        print("\n[*] Visor cerrado.")