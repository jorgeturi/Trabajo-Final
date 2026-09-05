import sys
import threading
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets
from pythonosc import dispatcher, osc_server

# ==========================================
# 1. CONFIGURACIÓN DEL SISTEMA Y FFT
# ==========================================
FS = 220
IP = "127.0.0.1"
PORT = 5000

FFT_SIZE = 256        # Tamaño de la ventana FFT
HISTORY_LEN = 100     # Cantidad de columnas temporales en pantalla
FPS = 25              # Tasa de actualización (Hz)

CANALES = ['TP9 (Mandíbula Izq)', 'FP1 (Ojo Izq)', 'FP2 (Ojo Der)', 'TP10 (Mandíbula Der)']

# ==========================================
# 2. BUFFERS MULTIHILO
# ==========================================
buffer_crudo = np.zeros((4, FFT_SIZE))
num_bins = FFT_SIZE // 2 + 1

# Matriz (Tiempo, Frecuencia) inicializada
mapas_espectro = [np.full((HISTORY_LEN, num_bins), -50.0, dtype=np.float32) for _ in range(4)]
buffer_lock = threading.Lock()

hanning_window = np.hanning(FFT_SIZE).astype(np.float32)

# ==========================================
# 3. HANDLER OSC (RED)
# ==========================================
def eeg_handler(address, *args):
    global buffer_crudo
    with buffer_lock:
        buffer_crudo = np.roll(buffer_crudo, -1, axis=1)
        buffer_crudo[:, -1] = args[:4]

# ==========================================
# 4. INTERFAZ GRÁFICA (PYQTGRAPH)
# ==========================================
app = QtWidgets.QApplication(sys.argv)
win = pg.GraphicsLayoutWidget(show=True, title="Espectrograma BCI - Muse-IO (Tiempo Real)")
win.resize(1200, 900)
win.setBackground('k')

images = []
freqs = np.fft.rfftfreq(FFT_SIZE, 1.0 / FS)
escala_y = float(freqs[-1]) / float(num_bins)

colormap = pg.colormap.get('magma')

for i in range(4):
    p = win.addPlot(title=CANALES[i])
    p.setLabel('left', 'Frecuencia', units='Hz')
    p.setLabel('bottom', 'Tiempo (muestras recientes)')
    p.setYRange(0, 110)
    p.setXRange(0, HISTORY_LEN)
    
    img = pg.ImageItem()
    img.setColorMap(colormap)
    
    # Escala para que el eje vertical corresponda a frecuencias reales en Hz
    tr = pg.QtGui.QTransform()
    tr.scale(1.0, escala_y)
    img.setTransform(tr)
    
    p.addItem(img)
    images.append(img)
    
    if i < 3:
        win.nextRow()

# ==========================================
# 5. SERVIDOR OSC Y ACTUALIZACIÓN
# ==========================================
disp = dispatcher.Dispatcher()
disp.map("/muse/eeg", eeg_handler)

server = osc_server.ThreadingOSCUDPServer((IP, PORT), disp)
server_thread = threading.Thread(target=server.serve_forever)
server_thread.daemon = True
server_thread.start()

def update():
    global mapas_espectro
    
    with buffer_lock:
        copia_datos = np.copy(buffer_crudo)
        
    for i in range(4):
        senal = copia_datos[i]
        
        # 1. Quitar componente continua (DC offset)
        senal_centrada = senal - np.mean(senal)
        
        # 2. Ventana Hanning
        senal_suavizada = senal_centrada * hanning_window
        
        # 3. FFT y conversión a dB
        espectro = np.abs(np.fft.rfft(senal_suavizada))
        espectro_db = (20 * np.log10(espectro + 1e-6)).astype(np.float32)
        
        # 4. Desplazar cascada
        mapas_espectro[i] = np.roll(mapas_espectro[i], -1, axis=0)
        mapas_espectro[i][-1, :] = espectro_db
        
        # 5. CORRECCIÓN: Usar setImage() explícito en lugar de setData()
        images[i].setImage(mapas_espectro[i], autoLevels=False, levels=[-10, 60])

timer = QtCore.QTimer()
timer.timeout.connect(update)
timer.start(int(1000 / FPS))

if __name__ == '__main__':
    try:
        sys.exit(app.exec_())
    except KeyboardInterrupt:
        server.shutdown()