import sys
import zmq
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

IP_RASPBERRY = "192.168.1.50"
context = zmq.Context()
sock = context.socket(zmq.SUB)
sock.connect(f"tcp://{IP_RASPBERRY}:5555")
sock.setsockopt_string(zmq.SUBSCRIBE, "")

app = QtWidgets.QApplication(sys.argv)
pg.setConfigOptions(antialias=True)

win = pg.GraphicsLayoutWidget(show=True, title="BCI TuJo - Diagnóstico IMU (Ejes X, Y, Z)")
win.resize(1000, 800)

win.addLabel("Monitoreo de Acelerómetro - Todos los Ejes con Filtro Suavizador", size="14pt", color="w")
win.nextRow()

ejes = ["X", "Y", "Z"]
colores = [(255, 100, 100), (100, 255, 100), (100, 100, 255)]
plots = {}
curves_raw = {}
curves_smooth = {}

tamano_ventana = 250
buff_raw = {e: [0] * tamano_ventana for e in ejes}
buff_smooth = {e: [0] * tamano_ventana for e in ejes}
prev_smooth = {e: 0.0 for e in ejes}
ALFA = 0.12 # Factor de suavizado (más bajo = más filtro contra estornudos/vibraciones)

for i, e in enumerate(ejes):
    p = win.addPlot(title=f"Acelerómetro - Eje {e}")
    p.showGrid(x=True, y=True, alpha=0.3)
    p.setYRange(-1.5, 1.5)
    
    c_raw = p.plot(pen=pg.mkPen(color=(120, 120, 120), width=1, style=QtCore.Qt.DashLine))
    c_smooth = p.plot(pen=pg.mkPen(color=colores[i], width=2))
    
    plots[e] = p
    curves_raw[e] = c_raw
    curves_smooth[e] = c_smooth
    win.nextRow()

def actualizar_imu_multi():
    global prev_smooth
    actualizado = False
    datos_actuales = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    
    try:
        while True:
            msg = sock.recv_json(flags=zmq.NOBLOCK)
            if msg.get("tipo") == "ACC":
                datos_actuales["X"] = msg.get("x", 0.0)
                datos_actuales["Y"] = msg.get("y", 0.0)
                datos_actuales["Z"] = msg.get("z", 0.0)
                actualizado = True
    except zmq.Again:
        pass

    if actualizado:
        for e in ejes:
            val_raw = datos_actuales[e]
            # Filtro exponencial (EMA) para evitar saltos bruscos
            val_smooth = ALFA * val_raw + (1 - ALFA) * prev_smooth[e]
            prev_smooth[e] = val_smooth
            
            buff_raw[e].pop(0)
            buff_raw[e].append(val_raw)
            
            buff_smooth[e].pop(0)
            buff_smooth[e].append(val_smooth)
            
            curves_raw[e].setData(buff_raw[e])
            curves_smooth[e].setData(buff_smooth[e])

timer = QtCore.QTimer()
timer.timeout.connect(actualizar_imu_multi)
timer.start(20)

if __name__ == '__main__':
    sys.exit(app.exec_())