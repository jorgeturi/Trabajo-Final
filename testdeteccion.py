import sys
import time
import threading
import numpy as np
from scipy import signal
from pythonosc import dispatcher, osc_server

# ==========================================
# 1. CONFIGURACIÓN DEL SISTEMA
# ==========================================
FS = 220
IP = "127.0.0.1"
PORT = 5000
VENTANA_MUESTRAS = 110  # 0.5 segundos de datos (Latencia ideal para control motriz)

buffer_crudo = np.zeros((4, VENTANA_MUESTRAS))
buffer_lock = threading.Lock()

# ==========================================
# 2. CALIBRACIÓN DE UMBRALES (Ajustar según tus pruebas)
# ==========================================
# Estos valores representan la Varianza (energía) de la señal en el tiempo.
UMBRAL_MANDIBULA = 1500      # Energía en TP9/TP10 para considerar mordida
UMBRAL_CEJAS_FRENTE = 3000   # Energía masiva en FP1/FP2 para detectar cejas/artefacto
LIMITE_FRENTE_MANDIBULA = 800 # Si la frente supera esto, NO es una mordida limpia

# ==========================================
# 3. FILTRO DIGITAL (Banda EMG: 20 - 45 Hz)
# ==========================================
nyq = 0.5 * FS
b_filt, a_filt = signal.butter(4, [20.0 / nyq, 45.0 / nyq], btype='band')

# ==========================================
# 4. HANDLER OSC (RED)
# ==========================================
def eeg_handler(address, *args):
    global buffer_crudo
    with buffer_lock:
        buffer_crudo = np.roll(buffer_crudo, -1, axis=1)
        buffer_crudo[:, -1] = args[:4]

# ==========================================
# 5. LÓGICA DE DETECCIÓN (EL CEREBRO DEL BCI)
# ==========================================
def analizar_ventana():
    with buffer_lock:
        datos = np.copy(buffer_crudo)
        
    # Filtramos los 4 canales simultáneamente
    datos_filtrados = signal.filtfilt(b_filt, a_filt, datos, axis=1)
    
    # Calculamos la Varianza (Potencia de la señal) para cada sensor
    potencia_tp9 = np.var(datos_filtrados[0])
    potencia_fp1 = np.var(datos_filtrados[1])
    potencia_fp2 = np.var(datos_filtrados[2])
    potencia_tp10 = np.var(datos_filtrados[3])
    
    # Promediamos las zonas para mayor robustez
    energia_mandibula = (potencia_tp9 + potencia_tp10) / 2
    energia_frente = (potencia_fp1 + potencia_fp2) / 2
    
    # Árbol de Decisión Lógica
    estado = "REPOSO"
    
    if energia_frente > UMBRAL_CEJAS_FRENTE:
        estado = "⚠️ LEVANTAR CEJAS / ARTEFACTO (Ignorar)"
    elif energia_mandibula > UMBRAL_MANDIBULA and energia_frente < LIMITE_FRENTE_MANDIBULA:
        estado = "✅ MORDIDA DETECTADA (Enviar Trigger)"
        
    # Imprimimos en consola sobreescribiendo la misma línea (con los valores crudos para que puedas calibrar)
    sys.stdout.write(f"\rEstado: {estado:<45} | Frente: {int(energia_frente):<6} | Mandíbula: {int(energia_mandibula):<6}")
    sys.stdout.flush()

# ==========================================
# 6. INICIO DEL SISTEMA
# ==========================================
disp = dispatcher.Dispatcher()
disp.map("/muse/eeg", eeg_handler)

server = osc_server.ThreadingOSCUDPServer((IP, PORT), disp)
server_thread = threading.Thread(target=server.serve_forever)
server_thread.daemon = True
server_thread.start()

print("Iniciando motor de inferencia BCI... Presioná Ctrl+C para salir.\n")

try:
    while True:
        analizar_ventana()
        time.sleep(0.1)  # Evaluamos 10 veces por segundo
except KeyboardInterrupt:
    server.shutdown()
    print("\nSistema detenido.")