import sys
import zmq
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

# ==========================================
# CONFIGURACIÓN DE RED (ZMQ)
# ==========================================
IP_RASPBERRY = "192.168.1.50"
context = zmq.Context()
sock = context.socket(zmq.SUB)
sock.connect(f"tcp://{IP_RASPBERRY}:5555")
sock.setsockopt_string(zmq.SUBSCRIBE, "")

# ==========================================
# INTERFAZ GRÁFICA
# ==========================================
app = QtWidgets.QApplication(sys.argv)
pg.setConfigOptions(antialias=True)

win = pg.GraphicsLayoutWidget(show=True, title="BCI TuJo - Simulador de Puntero Definitivo")
win.resize(900, 850)

badge_label = win.addLabel("Calibrando centro... Mantén la cabeza neutral unos segundos.", size="13pt", color="w")
win.nextRow()

p_plot = win.addPlot(title="Simulador de Puntero 2D (Inclinaciones Limpias)")
p_plot.showGrid(x=True, y=True, alpha=0.4)
p_plot.setXRange(-400, 400)
p_plot.setYRange(-400, 400)

puntero = p_plot.plot(pen=None, symbol='o', symbolPen='c', symbolBrush='b', symbolSize=30)
rastro = p_plot.plot(pen=pg.mkPen(color=(0, 200, 255, 120), width=3))

# Variables de calibración y suavizado
centro_x = None
centro_y = None
centro_z = None

pos_x_suave = 0.0
pos_y_suave = 0.0
ALFA = 0.12         # Factor de suavizado (filtro contra ruido o movimientos bruscos)
SENSIBILIDAD = 2.0  # Ajuste de velocidad del puntero
DEADZONE = 15.0     # Zona muerta central para evitar temblores en reposo

hist_x = []
hist_y = []

def actualizar():
    global centro_x, centro_y, centro_z, pos_x_suave, pos_y_suave
    raw_x, raw_y, raw_z = None, None, None
    
    try:
        while True:
            msg = sock.recv_json(flags=zmq.NOBLOCK)
            if msg.get("tipo") == "ACC":
                raw_x = msg.get("X", msg.get("x", 0.0))
                raw_y = msg.get("Y", msg.get("y", 0.0))
                raw_z = msg.get("Z", msg.get("z", 0.0))
    except zmq.Again:
        pass

    if raw_x is not None and raw_y is not None and raw_z is not None:
        if centro_x is None or centro_y is None or centro_z is None:
            centro_x = raw_x
            centro_y = raw_y
            centro_z = raw_z
            print(f"🎯 Centro calibrado -> X: {centro_x:.1f} | Y: {centro_y:.1f} | Z: {centro_z:.1f}")

        # --- MAPEO DE MOVIMIENTO ---
        # Horizontal: Movido por el eje Z (inclinación lateral hacia los hombros)
        dx = raw_z - centro_z
        # Vertical: Movido por el eje X invertido (mirar arriba / abajo)
        dy = -(raw_x - centro_x)

        # Aplicar zona muerta para que en reposo no se mueva solo
        if abs(dx) < DEADZONE: 
            dx = 0
        else: 
            dx = (dx - DEADZONE if dx > 0 else dx + DEADZONE)

        if abs(dy) < DEADZONE: 
            dy = 0
        else: 
            dy = (dy - DEADZONE if dy > 0 else dy + DEADZONE)

        delta_x = dx * SENSIBILIDAD
        delta_y = dy * SENSIBILIDAD

        # Filtro exponencial de suavizado (EMA)
        pos_x_suave = ALFA * delta_x + (1 - ALFA) * pos_x_suave
        pos_y_suave = ALFA * delta_y + (1 - ALFA) * pos_y_suave

        # Actualizar gráfico
        puntero.setData([pos_x_suave], [pos_y_suave])
        
        hist_x.append(pos_x_suave)
        hist_y.append(pos_y_suave)
        if len(hist_x) > 40:
            hist_x.pop(0)
            hist_y.pop(0)
        rastro.setData(hist_x, hist_y)

        badge_label.setText(
            f"Crudos -> X: {raw_x:.1f} | Y: {raw_y:.1f} | Z: {raw_z:.1f} <br>"
            f"<b>Puntero -> X (Horiz/Z): {pos_x_suave:+.1f} | Y (Vert/Inv X): {pos_y_suave:+.1f}</b>"
        )

timer = QtCore.QTimer()
timer.timeout.connect(actualizar)
timer.start(16) # 60 FPS

if __name__ == '__main__':
    sys.exit(app.exec_())