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

win = pg.GraphicsLayoutWidget(show=True, title="BCI TuJo - Simulador BCI y Motores")
win.resize(1100, 850) 

badge_label = win.addLabel("Monitor ZMQ - Mapeo Directo de Acelerómetro (Escala ±800)", size="13pt", color="w", colspan=2)
win.nextRow()

# --- PANEL IZQUIERDO: SIMULADOR DE PUNTERO DOBLE ---
p_plot = win.addPlot(title="Posición de la Cabeza: Crudo (Rojo) vs Filtrado (Azul)")
p_plot.showGrid(x=True, y=True, alpha=0.4)
p_plot.addLegend()

# Rango ajustado a los valores crudos de tu Muse
p_plot.setXRange(-800, 800)
p_plot.setYRange(-800, 800)

p_plot.setLabel('bottom', 'Inclinación Lateral (Eje Z del Muse)')
p_plot.setLabel('left', 'Inclinación Frontal (Eje X del Muse)')

rastro_crudo = p_plot.plot(pen=pg.mkPen(color=(255, 50, 50, 100), width=2, style=QtCore.Qt.DashLine))
puntero_crudo = p_plot.plot(pen=None, symbol='x', symbolPen='r', symbolBrush='r', symbolSize=15, name="Crudo")

rastro_suave = p_plot.plot(pen=pg.mkPen(color=(0, 200, 255, 120), width=3))
puntero_suave = p_plot.plot(pen=None, symbol='o', symbolPen='c', symbolBrush='b', symbolSize=25, name="Filtrado")

# --- PANEL DERECHO: ESTADO DE MOTORES PWM ---
p_motores = win.addPlot(title="Motores - Salida PWM (-100 a +100%)")
p_motores.showGrid(y=True, alpha=0.5)
p_motores.setYRange(-100, 100)
p_motores.setXRange(-0.5, 1.5)

eje_x = p_motores.getAxis('bottom')
eje_x.setTicks([[(0, 'M1 (Avance)'), (1, 'M2 (Giro)')]])

barras_motores = pg.BarGraphItem(x=[0, 1], height=[0, 0], width=0.6, brushes=['#0088FF', '#FF8800'])
p_motores.addItem(barras_motores)

win.nextRow()
motor_label = win.addLabel("Datos de motores: Esperando...", size="12pt", color="yellow", colspan=2)

# ==========================================
# VARIABLES GLOBALES
# ==========================================
SENSIBILIDAD = 1.0  
hist_x_suave, hist_y_suave = [], []
hist_x_crudo, hist_y_crudo = [], []

motor_pwm1, motor_pwm2 = 0, 0

def actualizar():
    global motor_pwm1, motor_pwm2
    hay_datos_nuevos = False
    
    # Valores por defecto
    x_crudo, z_crudo = 0.0, 0.0
    x_filt, z_filt = 0.0, 0.0
    
    try:
        while True:
            msg = sock.recv_json(flags=zmq.NOBLOCK)
            tipo = msg.get("tipo")
            
            if tipo == "DEBUG_MOTOR":
                motor_pwm1 = msg.get("pwm1", 0)
                motor_pwm2 = msg.get("pwm2", 0)
                x_crudo = msg.get("x_crudo", 0.0)
                z_crudo = msg.get("z_crudo", 0.0)
                x_filt = msg.get("x_filt", 0.0)
                z_filt = msg.get("z_filt", 0.0)
                hay_datos_nuevos = True
                
    except zmq.Again:
        pass

    if hay_datos_nuevos:
        
        # --- MAPEO VISUAL ORIGINAL INVERTIDO ---
        # Respetamos la lógica que te funcionaba bien:
        # Z controla Izquierda/Derecha (eje X de la pantalla)
        # X controla Adelante/Atrás (eje Y de la pantalla)
        pos_x_crudo = -z_crudo * SENSIBILIDAD
        pos_y_crudo = -x_crudo * SENSIBILIDAD

        pos_x_suave = -z_filt * SENSIBILIDAD
        pos_y_suave = -x_filt * SENSIBILIDAD

        # --- DIBUJAR PUNTERO CRUDO ---
        puntero_crudo.setData([pos_x_crudo], [pos_y_crudo])
        hist_x_crudo.append(pos_x_crudo)
        hist_y_crudo.append(pos_y_crudo)
        if len(hist_x_crudo) > 40:
            hist_x_crudo.pop(0)
            hist_y_crudo.pop(0)
        rastro_crudo.setData(hist_x_crudo, hist_y_crudo)

        # --- DIBUJAR PUNTERO FILTRADO ---
        puntero_suave.setData([pos_x_suave], [pos_y_suave])
        hist_x_suave.append(pos_x_suave)
        hist_y_suave.append(pos_y_suave)
        if len(hist_x_suave) > 40:
            hist_x_suave.pop(0)
            hist_y_suave.pop(0)
        rastro_suave.setData(hist_x_suave, hist_y_suave)

        # --- ACTUALIZAR ETIQUETAS ---
        badge_label.setText(
            f"Crudos -> Eje X: {x_crudo:.1f} | Eje Z: {z_crudo:.1f} <br>"
            f"<b>Filtrados -> Eje X: {x_filt:+.1f} | Eje Z: {z_filt:+.1f}</b>"
        )

        barras_motores.setOpts(height=[motor_pwm1, motor_pwm2])
        
        motor_label.setText(
            f"<b>M1 (X/Adelante)</b> -> Filt: {x_filt:>6.1f} => <b>PWM: {motor_pwm1:>5.1f}%</b>   |||   "
            f"<b>M2 (Z/Giro)</b> -> Filt: {z_filt:>6.1f} => <b>PWM: {motor_pwm2:>5.1f}%</b>"
        )

timer = QtCore.QTimer()
timer.timeout.connect(actualizar)
timer.start(16)

if __name__ == '__main__':
    sys.exit(app.exec_())