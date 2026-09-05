import sys
import time
import threading
import numpy as np
import matplotlib.pyplot as plt
from scipy import signal
from pythonosc import dispatcher, osc_server

# ==========================================
# CONFIGURACIÓN
# ==========================================
FS = 220
IP = "127.0.0.1"
PORT = 5000
DURACION_GRABACION = 3  # Segundos por cada prueba
SAMPLES_TOTALES = FS * DURACION_GRABACION

# Buffer global para acumular los datos
buffer_grabacion = []
grabando = False

# ==========================================
# HANDLER OSC
# ==========================================
def eeg_handler(address, *args):
    global buffer_grabacion, grabando
    if grabando:
        buffer_grabacion.append(args[:4])

# ==========================================
# FUNCIÓN DE CAPTURA
# ==========================================
def grabar_estado(nombre_estado):
    global buffer_grabacion, grabando
    input(f"\nPreparate para '{nombre_estado}'. Presioná ENTER para empezar a grabar (durará {DURACION_GRABACION} segs)...")
    
    buffer_grabacion = []
    grabando = True
    print("GRABANDO... Hacé el gesto y mantenelo/repetilo!")
    
    time.sleep(DURACION_GRABACION)
    
    grabando = False
    print("Grabación terminada.")
    
    # Convertimos a numpy array (Canales, Muestras)
    datos = np.array(buffer_grabacion).T
    
    # Si por algún motivo faltan samples (lag de red), rellenamos o cortamos
    if datos.shape[1] > SAMPLES_TOTALES:
        datos = datos[:, :SAMPLES_TOTALES]
    elif datos.shape[1] < SAMPLES_TOTALES:
        faltante = SAMPLES_TOTALES - datos.shape[1]
        datos = np.pad(datos, ((0,0), (0,faltante)), mode='edge')
        
    return datos

# ==========================================
# INICIO DEL SERVIDOR OSC
# ==========================================
disp = dispatcher.Dispatcher()
disp.map("/muse/eeg", eeg_handler)
server = osc_server.ThreadingOSCUDPServer((IP, PORT), disp)
server_thread = threading.Thread(target=server.serve_forever)
server_thread.daemon = True
server_thread.start()

# ==========================================
# FLUJO PRINCIPAL
# ==========================================
print("=== COMPARADOR ESTÁTICO DE FIRMAS ESPECTRALES ===")
print("Conectado a Muse-IO. Esperando para iniciar pruebas...\n")

# Capturamos los 3 estados
datos_reposo = grabar_estado("REPOSO (Quedate quieto y relajado)")
datos_ojos = grabar_estado("CERRAR OJOS (Apretalos repetidamente)")
datos_cejas = grabar_estado("LEVANTAR CEJAS (Sostenelo arriba y abajo)")

server.shutdown() # Apagamos el servidor para liberar el puerto

# ==========================================
# PROCESAMIENTO Y GRÁFICOS
# ==========================================
def calcular_espectro_promedio(datos):
    # Usamos el método de Welch para obtener un espectro muy limpio y suavizado
    freqs = []
    espectros = []
    for canal in datos:
        canal = canal - np.mean(canal) # Quitar offset
        f, pxx = signal.welch(canal, fs=FS, nperseg=FS, window='hann')
        freqs = f
        espectros.append(pxx)
    return freqs, np.array(espectros)

f, espectro_reposo = calcular_espectro_promedio(datos_reposo)
_, espectro_ojos = calcular_espectro_promedio(datos_ojos)
_, espectro_cejas = calcular_espectro_promedio(datos_cejas)

CANALES = ['TP9 (Mandíbula Izq)', 'FP1 (Ojo Izq)', 'FP2 (Ojo Der)', 'TP10 (Mandíbula Der)']

fig, axes = plt.subplots(2, 2, figsize=(14, 8))
fig.suptitle('Comparación de Firmas Espectrales (Escala Logarítmica)', fontsize=16)

axes = axes.flatten()

for i in range(4):
    ax = axes[i]
    
    # Graficamos en decibelios (escala logarítmica) para ver bien la separación
    ax.plot(f, 10 * np.log10(espectro_reposo[i] + 1e-10), label='Reposo', color='gray', linewidth=2, linestyle='--')
    ax.plot(f, 10 * np.log10(espectro_ojos[i] + 1e-10), label='Cerrar Ojos', color='blue', linewidth=2)
    ax.plot(f, 10 * np.log10(espectro_cejas[i] + 1e-10), label='Levantar Cejas', color='red', linewidth=2)
    
    ax.set_title(CANALES[i])
    ax.set_xlim(0, 50) # Miramos hasta 50Hz
    ax.set_xlabel('Frecuencia (Hz)')
    ax.set_ylabel('Potencia (dB)')
    ax.legend()
    ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.show()