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

win = pg.GraphicsLayoutWidget(show=True, title="BCI TuJo - Test de Puntero 2D (Suavidad de Movimiento)")
win.resize(800, 800)

p_plot = win.addPlot(title="Mover la cabeza/dispositivo para controlar el puntero")
p_plot.showGrid(x=True, y=True, alpha=0.4)
p_plot.setXRange(-1.0, 1.0)
p_plot.setYRange(-1.0, 1.0)

# Curva de seguimiento (deja un pequeño rastro de puntos) y el cursor principal
cursor = p_plot.plot(pen=None, symbol='o', symbolPen='c', symbolBrush='b', symbolSize=25)
rastro = p_plot.plot(pen=pg.mkPen(color=(0, 150, 255, 100), width=3))

x_suave = 0.0
y_suave = 0.0
ALFA = 0.15  # Filtro de suavizado alto para evitar vibraciones o tirones
historial_x = []
historial_y = []

def actualizar_puntero():
    global x_suave, y_suave
    nuevo_x, nuevo_y = None, None
    
    try:
        while True:
            msg = sock.recv_json(flags=zmq.NOBLOCK)
            if msg.get("tipo") == "ACC":
                nuevo_x = msg.get("x", 0.0)
                nuevo_y = msg.get("y", 0.0)
    except zmq.Again:
        pass

    if nuevo_x is not None and nuevo_y is not None:
        # Aplicar filtro de suavizado
        x_suave = ALFA * nuevo_x + (1 - ALFA) * x_suave
        y_suave = ALFA * nuevo_y + (1 - ALFA) * y_suave
        
        cursor.setData([x_suave], [y_suave])
        
        historial_x.append(x_suave)
        historial_y.append(y_suave)
        if len(historial_x) > 30: # Mantener un rastro corto de las últimas posiciones
            historial_x.pop(0)
            historial_y.pop(0)
            
        rastro.setData(historial_x, historial_y)

timer = QtCore.QTimer()
timer.timeout.connect(actualizar_puntero)
timer.start(16) # 60 FPS para máxima fluidez visual del cursor

if __name__ == '__main__':
    sys.exit(app.exec_())