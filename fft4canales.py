import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.colors import LogNorm
from pythonosc import dispatcher, osc_server
import threading
from collections import deque

FS = 220.0
BUFFER_SIZE = 256
HIST_BINS = 60 # Reducido para no saturar la CPU

# Buffers independientes para los 4 canales
ch_data = [deque(np.zeros(BUFFER_SIZE), maxlen=BUFFER_SIZE) for _ in range(4)]

window = np.hanning(BUFFER_SIZE)
freqs = np.fft.rfftfreq(BUFFER_SIZE, d=1.0/FS)

idx_plot = np.where((freqs >= 1.0) & (freqs <= 110.0))[0]
freqs_plot = freqs[idx_plot]

# Anulamos la red eléctrica
idx_50 = np.where((freqs >= 45.0) & (freqs <= 55.0))[0]
idx_100 = np.where((freqs >= 95.0) & (freqs <= 105.0))[0]

# 4 Matrices para los mapas de calor
spectrograms = [np.full((HIST_BINS, len(idx_plot)), 10.0) for _ in range(4)]

def eeg_handler(address, *args):
    for i in range(4):
        ch_data[i].append(args[i])

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)
fig.canvas.manager.set_window_title('Matriz Espacial: 4 Canales Logarítmicos')

caxs = []
etiquetas = ['CH 0: TP9 (Oreja Izq)', 'CH 1: FP1 (Frente Izq)', 'CH 2: FP2 (Frente Der)', 'CH 3: TP10 (Oreja Der)']

for i, ax in enumerate(axes):
    cax = ax.imshow(spectrograms[i], aspect='auto', origin='lower',
                    extent=[freqs_plot[0], freqs_plot[-1], 0, HIST_BINS],
                    cmap='turbo', norm=LogNorm(vmin=10, vmax=20000))
    ax.set_ylabel(etiquetas[i], fontsize=9)
    caxs.append(cax)

axes[-1].set_xlabel('Frecuencia (Hz)')
# Una sola barra de color para ahorrar espacio y CPU
fig.colorbar(caxs[0], ax=axes, label='Magnitud FFT (Log)', fraction=0.02, pad=0.04)

def actualizar_grafico(frame):
    for i in range(4):
        señal = np.array(ch_data[i])
        señal = señal - np.mean(señal)
        fft_vals = np.abs(np.fft.rfft(señal * window))
        
        # Planchar ruido eléctrico
        fft_vals[idx_50] = 10.0
        fft_vals[idx_100] = 10.0
        
        datos_fila = np.clip(fft_vals[idx_plot], 10.0, None)
        
        # Desplazar y actualizar matriz
        spectrograms[i] = np.roll(spectrograms[i], -1, axis=0)
        spectrograms[i][-1, :] = datos_fila
        caxs[i].set_data(spectrograms[i])
        
    return caxs

if __name__ == "__main__":
    threading.Thread(target=iniciar_osc, daemon=True).start()
    # 100ms = 10 FPS. Balance perfecto entre fluidez y uso de CPU.
    ani = animation.FuncAnimation(fig, actualizar_grafico, interval=100, blit=False)
    plt.show()