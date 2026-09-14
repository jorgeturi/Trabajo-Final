import sys
import zmq
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

# ==========================================
# 1. CONFIGURACIÓN DE RED (ZMQ)
# ==========================================
IP_RASPBERRY = "10.42.0.127"
context = zmq.Context()
sock = context.socket(zmq.SUB)
sock.connect(f"tcp://{IP_RASPBERRY}:5555")
sock.setsockopt_string(zmq.SUBSCRIBE, "")

# ==========================================
# 2. CONFIGURACIÓN DE LA INTERFAZ (PyQtGraph)
# ==========================================
app = QtWidgets.QApplication(sys.argv)
pg.setConfigOptions(antialias=True) # Suaviza las líneas de los gráficos

# Ventana principal
win = pg.GraphicsLayoutWidget(show=True, title="BCI TuJo - Centro de Monitoreo")
win.resize(1000, 800)

# Etiqueta superior para los datos del IMU (Acelerómetro)
imu_label = win.addLabel("Acelerómetro (ACC) -> Esperando datos...", col=0, colspan=1, size="14pt", color="w")
win.nextRow()

# Configuración de los 4 canales EEG
canales = ["TP9", "FP1", "FP2", "TP10"]
colores = [(255, 100, 100), (100, 255, 100), (100, 100, 255), (255, 255, 100)] # Rojo, Verde, Azul, Amarillo

plots = {}
curvas = {}
tamano_ventana = 500
datos_y = {ch: [0] * tamano_ventana for ch in canales}

# Crear un gráfico apilado por cada canal
for i, ch in enumerate(canales):
    p = win.addPlot(title=f"Canal {ch} (Onda Filtrada)")
    p.showGrid(x=True, y=True, alpha=0.3)
    
    # Dibujamos la línea de cada canal con su color
    c = p.plot(pen=pg.mkPen(color=colores[i], width=2))
    
    plots[ch] = p
    curvas[ch] = c
    win.nextRow()

# ==========================================
# 3. MOTOR DE ACTUALIZACIÓN EN TIEMPO REAL
# ==========================================
def actualizar_interfaz():
    actualizado_eeg = False
    texto_imu = ""
    
    try:
        # Vaciamos la cola de mensajes de ZMQ
        while True:
            datos_json = sock.recv_json(flags=zmq.NOBLOCK)
            tipo_mensaje = datos_json.get("tipo")
            
            # --- Si es un paquete CEREBRAL (EEG) ---
            if tipo_mensaje == "EEG":
                for ch in canales:
                    # Buscamos el valor filtrado de cada canal
                    valor = datos_json.get(f"{ch}_raw", 0)
                    datos_y[ch].pop(0)
                    datos_y[ch].append(valor)
                actualizado_eeg = True
                
            # --- Si es un paquete de MOVIMIENTO (ACC) ---
            elif tipo_mensaje == "ACC":
                x = datos_json.get("x", 0)
                y = datos_json.get("y", 0)
                z = datos_json.get("z", 0)
                texto_imu = f"Acelerómetro (ACC) -> X: {x:+.2f}  |  Y: {y:+.2f}  |  Z: {z:+.2f}"
                
    except zmq.Again:
        pass # No hay datos nuevos, continuamos

    # 4. Refrescar la pantalla solo si entraron datos
    if actualizado_eeg:
        for ch in canales:
            curvas[ch].setData(datos_y[ch])
            
    if texto_imu:
        imu_label.setText(texto_imu)

# Timer a 60 FPS (16 ms)
timer = QtCore.QTimer()
timer.timeout.connect(actualizar_interfaz)
timer.start(16)

print(f"📡 Conectado a {IP_RASPBERRY}:5555. Abriendo dashboard...")

if __name__ == '__main__':
    sys.exit(app.exec_())