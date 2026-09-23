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

win = pg.GraphicsLayoutWidget(show=True, title="BCI TuJo - Simulador de Mouse con Ejes Ajustados")
win.resize(900, 850)

badge_label = win.addLabel("Calibrando centro... Mantén la cabeza neutral.", size="13pt", color="w")
win.nextRow()

p_plot = win.addPlot(title="Simulador de Mouse (X = Vertical / Z = Horizontal)")
p_plot.showGrid(x=True, y=True, alpha=0.4)
p_plot.setXRange(-300, 300)
p_plot.setYRange(-300, 300)

puntero = p_plot.plot(pen=None, symbol='o', symbolPen='c', symbolBrush='b', symbolSize=30)
rastro = p_plot.plot(pen=pg.mkPen(color=(0, 200, 255, 120), width=3))

# Variables de calibración y suavizado
x_centro = None
z_centro = None
pos_x_suave = 0.0
pos_y_suave = 0.0
ALFA = 0.12  # Factor de suavizado (anti-vibración / estornudos)

hist_x = []
hist_y = []

def actualizar():
    global x_centro, z_centro, pos_x_suave, pos_y_suave
    raw_x, raw_z = None, None
    
    try:
        while True:
            msg = sock.recv_json(flags=zmq.NOBLOCK)
            if msg.get("tipo") == "ACC":
                # Tomamos los valores crudos del acelerómetro
                raw_x = msg.get("x")
                raw_z = msg.get("z")
    except zmq.Again:
        pass

    if raw_x is not None and raw_z is not None:
        if x_centro is None or z_centro is None:
            # Calibramos el centro exacto la primera vez que recibimos datos
            x_centro = raw_x
            z_centro = raw_z
            print(f"🎯 Centro calibrado -> Eje X base: {x_centro:.1f} | Eje Z base: {z_centro:.1f}")

        # --- MAPEO DEFINITIVO SEGÚN TUS PRUEBAS ---
        # Eje Horizontal del Mouse (Izquierda / Derecha) gobernado por Z (Inclinación lateral)
        desplazamiento_horizontal = raw_z - z_centro
        
        # Eje Vertical del Mouse (Arriba / Abajo) gobernado por X (Cabecear)
        # Si al subir la cabeza baja, le agregamos un signo menos '-' adelante. Probá quitándolo si queda invertido.
        desplazamiento_vertical = -(raw_x - x_centro) 

        # Aplicamos filtro de suavizado exponencial independiente para cada eje
        pos_x_suave = ALFA * desplazamiento_horizontal + (1 - ALFA) * pos_x_suave
        pos_y_suave = ALFA * desplazamiento_vertical + (1 - ALFA) * pos_y_suave

        # Actualizamos el puntero en el plano cartesiano
        puntero.setData([pos_x_suave], [pos_y_suave])
        
        hist_x.append(pos_x_suave)
        hist_y.append(pos_y_suave)
        if len(hist_x) > 40:
            hist_x.pop(0)
            hist_y.pop(0)
        rastro.setData(hist_x, hist_y)

        # 📊 Badge superior para ver los valores limpios en tiempo real
        badge_label.setText(
            f"Z (Inclinación Lateral): {raw_z:.1f}  |  X (Cabeceo): {raw_x:.1f}  <br>  "
            f"<b>Cursor 2D -> X: {pos_x_suave:+.1f} | Y: {pos_y_suave:+.1f}</b>"
        )

timer = QtCore.QTimer()
timer.timeout.connect(actualizar)
timer.start(16) # 60 FPS fluidos

if __name__ == '__main__':
    sys.exit(app.exec_())