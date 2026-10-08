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

# Etiqueta superior
badge_label = win.addLabel("Monitor ZMQ - Mostrando datos procesados por el Controlador", size="13pt", color="w", colspan=2)
win.nextRow()

# --- PANEL IZQUIERDO: SIMULADOR DE PUNTERO DOBLE ---
p_plot = win.addPlot(title="Simulador: Crudo (Rojo) vs Filtrado (Azul)")
p_plot.showGrid(x=True, y=True, alpha=0.4)
p_plot.addLegend()

# Rango ajustado (amplía si tu Muse entrega valores mayores a 800)
p_plot.setXRange(-800, 800)
p_plot.setYRange(-800, 800)

# Gráficos del dato CRUDO (Rojo)
rastro_crudo = p_plot.plot(pen=pg.mkPen(color=(255, 50, 50, 100), width=2, style=QtCore.Qt.DashLine))
puntero_crudo = p_plot.plot(pen=None, symbol='x', symbolPen='r', symbolBrush='r', symbolSize=15, name="Crudo")

# Gráficos del dato FILTRADO (Azul)
rastro_suave = p_plot.plot(pen=pg.mkPen(color=(0, 200, 255, 120), width=3))
puntero_suave = p_plot.plot(pen=None, symbol='o', symbolPen='c', symbolBrush='b', symbolSize=25, name="Filtrado")

# --- PANEL DERECHO: ESTADO DE MOTORES PWM ---
p_motores = win.addPlot(title="Motores - Salida PWM (-100 a +100%)")
p_motores.showGrid(y=True, alpha=0.5)
p_motores.setYRange(-100, 100) # Soporta reversa (negativos)
p_motores.setXRange(-0.5, 1.5)

eje_x = p_motores.getAxis('bottom')
eje_x.setTicks([[(0, 'M1 (Eje X)'), (1, 'M2 (Eje Z)')]])

barras_motores = pg.BarGraphItem(x=[0, 1], height=[0, 0], width=0.6, brushes=['#0088FF', '#FF8800'])
p_motores.addItem(barras_motores)

win.nextRow()

# Etiqueta inferior
motor_label = win.addLabel("Datos de motores: Esperando...", size="12pt", color="yellow", colspan=2)

# ==========================================
# VARIABLES GLOBALES
# ==========================================
SENSIBILIDAD = 1.0  # Ajuste de amplitud en pantalla (solo visual)

# Historiales para dibujar el "rastro" o estela de los punteros
hist_x_suave, hist_y_suave = [], []
hist_x_crudo, hist_y_crudo = [], []

# Variables de estado que recibiremos por ZMQ
motor_pwm1, motor_pwm2 = 0, 0
motor_x_filt, motor_z_filt = 0.0, 0.0
motor_x_crudo, motor_z_crudo = 0.0, 0.0

def actualizar():
    global motor_pwm1, motor_pwm2, motor_x_filt, motor_z_filt, motor_x_crudo, motor_z_crudo
    
    hay_datos_nuevos = False
    
    try:
        while True:
            msg = sock.recv_json(flags=zmq.NOBLOCK)
            tipo = msg.get("tipo")
            
            # Solo escuchamos el paquete de diagnóstico que ya trae toda la matemática hecha
            if tipo == "DEBUG_MOTOR":
                motor_pwm1 = msg.get("pwm1", 0)
                motor_pwm2 = msg.get("pwm2", 0)
                motor_x_crudo = msg.get("x_crudo", 0.0)
                motor_z_crudo = msg.get("z_crudo", 0.0)
                motor_x_filt = msg.get("x_filt", 0.0)
                motor_z_filt = msg.get("z_filt", 0.0)
                hay_datos_nuevos = True
                
    except zmq.Again:
        pass

    # 1. ACTUALIZAR GRÁFICOS (Solo si llegó información)
    if hay_datos_nuevos:
        
        # --- MAPEO VISUAL ---
        # Mantenemos tu lógica original para dibujar: Z = Eje Horizontal, -X = Eje Vertical
        pos_x_crudo = -motor_z_crudo * SENSIBILIDAD
        pos_y_crudo = -motor_x_crudo * SENSIBILIDAD

        pos_x_suave = -motor_z_filt * SENSIBILIDAD
        pos_y_suave = -motor_x_filt * SENSIBILIDAD

        # --- DIBUJAR PUNTERO CRUDO (Rojo) ---
        puntero_crudo.setData([pos_x_crudo], [pos_y_crudo])
        hist_x_crudo.append(pos_x_crudo)
        hist_y_crudo.append(pos_y_crudo)
        if len(hist_x_crudo) > 40:
            hist_x_crudo.pop(0)
            hist_y_crudo.pop(0)
        rastro_crudo.setData(hist_x_crudo, hist_y_crudo)

        # --- DIBUJAR PUNTERO FILTRADO (Azul) ---
        puntero_suave.setData([pos_x_suave], [pos_y_suave])
        hist_x_suave.append(pos_x_suave)
        hist_y_suave.append(pos_y_suave)
        if len(hist_x_suave) > 40:
            hist_x_suave.pop(0)
            hist_y_suave.pop(0)
        rastro_suave.setData(hist_x_suave, hist_y_suave)

        # --- ACTUALIZAR ETIQUETAS Y BARRAS ---
        badge_label.setText(
            f"Crudos -> X: {motor_x_crudo:.1f} | Z: {motor_z_crudo:.1f} <br>"
            f"<b>Filtrados -> X: {motor_x_filt:+.1f} | Z: {motor_z_filt:+.1f}</b>"
        )

        barras_motores.setOpts(height=[motor_pwm1, motor_pwm2])
        
        motor_label.setText(
            f"<b>M1 (X)</b> -> Crudo: {motor_x_crudo:>6.2f} | Filt: {motor_x_filt:>6.2f} => <b>PWM: {motor_pwm1:>5.1f}%</b>   |||   "
            f"<b>M2 (Z)</b> -> Crudo: {motor_z_crudo:>6.2f} | Filt: {motor_z_filt:>6.2f} => <b>PWM: {motor_pwm2:>5.1f}%</b>"
        )

timer = QtCore.QTimer()
timer.timeout.connect(actualizar)
timer.start(16) # ~60 FPS

if __name__ == '__main__':
    sys.exit(app.exec_())