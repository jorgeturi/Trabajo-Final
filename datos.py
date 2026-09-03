import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from scipy import signal
from pythonosc import dispatcher, osc_server
import threading
from collections import deque
import time

import csv # Importamos la librería para guardar archivos

FS = 220.0
F0 = 50.0   
Q = 30.0
b, a = signal.iirnotch(F0, Q, FS)

MAX_SAMPLES = 440
ch_names = ['TP9 (Oreja Izq)', 'FP1 (Frente Izq)', 'FP2 (Frente Der)', 'TP10 (Oreja Der)']
raw_data = {i: deque([800]*MAX_SAMPLES, maxlen=MAX_SAMPLES) for i in range(4)}

# Umbrales temporales
UMBRAL_MANDIBULA = 400  
UMBRAL_CEJAS = 500
UMBRAL_DESCONEXION = 1100

cooldown = 0
ultimo_print = time.time()

# --- PREPARAMOS EL ARCHIVO CSV ---
archivo_csv = open('calibracion_bci.csv', mode='w', newline='')
writer = csv.writer(archivo_csv)
writer.writerow(['Timestamp', 'Energia_Orejas', 'Energia_Frente', 'Estado'])

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
    fig.suptitle('Calibrador BCI + Data Logger CSV')
    
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
            
        energia_orejas = int(np.std(np.diff(filt_data[0][-110:])) + np.std(np.diff(filt_data[3][-110:])))
        energia_frente = int(np.std(np.diff(filt_data[1][-110:])) + np.std(np.diff(filt_data[2][-110:])))

        estado_actual = "Reposo"

        if cooldown > 0:
            cooldown -= 1
            estado_actual = "En Cooldown"
        else:
            if energia_orejas > UMBRAL_DESCONEXION or energia_frente > UMBRAL_DESCONEXION:
                estado_actual = "DESCONEXION"
                print("\n[XXX] ERROR: DIADEMA DESCONECTADA")
                cooldown = 40
            elif energia_orejas > UMBRAL_MANDIBULA:
                estado_actual = "MANDIBULA"
                print(f"\n[!!!] MANDÍBULA | Orejas: {energia_orejas} | Frente: {energia_frente}")
                cooldown = 20
            elif energia_frente > UMBRAL_CEJAS and energia_orejas < (UMBRAL_MANDIBULA * 0.8):
                estado_actual = "CEJAS"
                print(f"\n[*] CEJAS | Orejas: {energia_orejas} | Frente: {energia_frente}")
                cooldown = 20
            elif time.time() - ultimo_print > 0.5: # Imprime reposo cada medio segundo
                print(f"Reposo -> Orejas: {energia_orejas} | Frente: {energia_frente}")
                ultimo_print = time.time()

        # Guardamos la fila en el CSV y forzamos la escritura en el disco
        writer.writerow([time.strftime("%H:%M:%S.%f")[:-3], energia_orejas, energia_frente, estado_actual])
        archivo_csv.flush() 

        return lines

    try:
        ani = animation.FuncAnimation(fig, update, interval=50, blit=False)
        plt.tight_layout()
        plt.show()
    finally:
        # Aseguramos que el archivo se cierre bien al cerrar la ventana
        archivo_csv.close()