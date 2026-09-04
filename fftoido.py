import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.colors import LogNorm  # Importamos la escala logarítmica
from pythonosc import dispatcher, osc_server
import threading
from collections import deque

FS = 220.0
BUFFER_SIZE = 256
HIST_BINS = 100 

ch_tp9 = deque(np.zeros(BUFFER_SIZE), maxlen=BUFFER_SIZE)

window = np.hanning(BUFFER_SIZE)
freqs = np.fft.rfftfreq(BUFFER_SIZE, d=1.0/FS)

idx_plot = np.where((freqs >= 1.0) & (freqs <= 110.0))[0]
freqs_plot = freqs[idx_plot]

idx_50 = np.where((freqs >= 45.0) & (freqs <= 55.0))[0]
idx_100 = np.where((freqs >= 95.0) & (freqs <= 105.0))[0]

# Matriz iniciada en 10.0 (piso artificial) para que el LogNorm no falle con log(0)
spectrogram_data = np.full((HIST_BINS, len(idx_plot)), 10.0)

def eeg_handler(address, *args):
    ch_tp9.append(args[0])

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

fig, ax = plt.subplots(figsize=(10, 6))
fig.canvas.manager.set_window_title('Espectrograma BCI: Rango Dinámico Ampliado')

# Aplicamos cmap='turbo' y LogNorm(vmin=10, vmax=20000)
cax = ax.imshow(spectrogram_data, aspect='auto', origin='lower',
                extent=[freqs_plot[0], freqs_plot[-1], 0, HIST_BINS],
                cmap='turbo', norm=LogNorm(vmin=10, vmax=20000))

ax.set_xlabel('Frecuencia (Hz)')
ax.set_ylabel('Tiempo (Frames)')
ax.set_title('Mapa de Calor Logarítmico - Canal TP9')
fig.colorbar(cax, ax=ax, label='Magnitud FFT (Log)')

def actualizar_grafico(frame):
    global spectrogram_data
    
    señal = np.array(ch_tp9)
    señal = señal - np.mean(señal)
    
    fft_vals = np.abs(np.fft.rfft(señal * window))
    
    # Planchamos los armónicos eléctricos al valor base
    fft_vals[idx_50] = 10.0
    fft_vals[idx_100] = 10.0
    
    # Recortamos los valores por debajo de 10.0 para mantener la consistencia del logaritmo
    datos_fila = np.clip(fft_vals[idx_plot], 10.0, None)
    
    spectrogram_data = np.roll(spectrogram_data, -1, axis=0)
    spectrogram_data[-1, :] = datos_fila
    
    cax.set_data(spectrogram_data)
    return [cax]

if __name__ == "__main__":
    threading.Thread(target=iniciar_osc, daemon=True).start()
    ani = animation.FuncAnimation(fig, actualizar_grafico, interval=50, blit=False)
    plt.tight_layout()
    plt.show()