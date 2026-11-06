import sys
import zmq
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

# ==========================================
# 1. CONFIGURACIÓN DE RED (ZMQ)
# ==========================================
IP_RASPBERRY = "192.168.1.50"
context = zmq.Context()
sock = context.socket(zmq.SUB)
sock.connect(f"tcp://{IP_RASPBERRY}:5555")
sock.setsockopt_string(zmq.SUBSCRIBE, "")

# ==========================================
# 2. CONFIGURACIÓN DE LA INTERFAZ
# ==========================================
app = QtWidgets.QApplication(sys.argv)
pg.setConfigOptions(antialias=False) # Máximo rendimiento para evitar trabazones

win = pg.GraphicsLayoutWidget(show=True, title="BCI TuJo - Panel Completo (8 Curvas EEG + Batería)")
win.resize(1200, 900)

# Barra de estado superior (Batería y Acelerómetro resumido)
estado_label = win.addLabel("🔋 Batería: --%  |  IMU -> X: -- | Y: -- | Z: --", col=0, colspan=1, size="13pt", color="w")
win.nextRow()

canales = ["TP9", "FP1", "FP2", "TP10"]
colores_filt = [(255, 90, 90), (90, 255, 90), (90, 140, 255), (255, 255, 90)]

plots = {}
curvas_raw = {}
curvas_filt = {}
tamano_ventana = 400

datos_raw = {ch: [0] * tamano_ventana for ch in canales}
datos_filt = {ch: [0] * tamano_ventana for ch in canales}

# Crear un gráfico por canal que contenga AMBAS señales (Raw y Filtrada)
for i, ch in enumerate(canales):
    p = win.addPlot(title=f"Canal {ch} (Gris = Raw | Color = Pasa-Banda 8-30Hz)")
    p.showGrid(x=True, y=True, alpha=0.2)
    p.setMouseEnabled(x=False, y=True)
    
    # Curva Raw (Gris tenue al fondo)
    c_raw = p.plot(pen=pg.mkPen(color=(100, 100, 100), width=1))
    # Curva Filtrada (Color brillante encima)
    c_filt = p.plot(pen=pg.mkPen(color=colores_filt[i], width=2))
    
    plots[ch] = p
    curvas_raw[ch] = c_raw
    curvas_filt[ch] = c_filt
    win.nextRow()

# ==========================================
# 3. MOTOR DE ACTUALIZACIÓN
# ==========================================
bateria_actual = "N/A"
imu_texto = "X: 0.00 | Y: 0.00 | Z: 0.00"

def actualizar():
    global bateria_actual, imu_texto
    actualizado_eeg = False
    
    try:
        while True:
            msg = sock.recv_json(flags=zmq.NOBLOCK)
            tipo = msg.get("tipo")
            
            if tipo == "EEG":
                for ch in canales:
                    datos_raw[ch].pop(0)
                    datos_raw[ch].append(msg.get(f"{ch}_raw", 0.0))
                    
                    datos_filt[ch].pop(0)
                    datos_filt[ch].append(msg.get(f"{ch}_filtrado", 0.0))
                actualizado_eeg = True
                
            elif tipo == "ACC":
                x, y, z = msg.get("x", 0.0), msg.get("y", 0.0), msg.get("z", 0.0)
                imu_texto = f"X: {x:+.2f} | Y: {y:+.2f} | Z: {z:+.2f}"
                
            elif tipo == "BAT":
                bateria_actual = str(msg.get("bateria", "--"))
                
    except zmq.Again:
        pass

    if actualizado_eeg:
        for ch in canales:
            curvas_raw[ch].setData(datos_raw[ch])
            curvas_filt[ch].setData(datos_filt[ch])
            
        estado_label.setText(f"🔋 Batería: {bateria_actual}%  |  IMU -> {imu_texto}")

timer = QtCore.QTimer()
timer.timeout.connect(actualizar)
timer.start(30) # ~30 FPS fluidos

if __name__ == '__main__':
    sys.exit(app.exec_())