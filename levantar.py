import numpy as np
from scipy import signal
from pythonosc import dispatcher, osc_server
import threading
from collections import deque
import time

FS = 220.0
F0 = 50.0   
Q = 30.0
b, a = signal.iirnotch(F0, Q, FS)

MAX_SAMPLES = 440
raw_data = {i: deque([800]*MAX_SAMPLES, maxlen=MAX_SAMPLES) for i in range(4)}

# --- VARIABLES DINÁMICAS ---
UMBRAL_MANDIBULA = 0  
UMBRAL_CEJAS = 0
UMBRAL_DESCONEXION = 1500

cooldown = 0
ultimo_print = time.time()

# --- ESTADO DE CALIBRACIÓN ---
fase_calibracion = True
muestras_calibracion_frente = []
muestras_calibracion_orejas = []
tiempo_inicio = time.time()
TIEMPO_CALIBRACION = 5.0 # Segundos de recolección de baseline

def eeg_handler(address, *args):
    for i in range(4):
        raw_data[i].append(args[i])

def start_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

if __name__ == "__main__":
    osc_thread = threading.Thread(target=start_osc, daemon=True)
    osc_thread.start()

    print("[*] INICIANDO SISTEMA BCI...")
    print("[!] Por favor, quédese quieto y relajado durante 5 segundos.\n")

    try:
        while True:
            time.sleep(0.05) 
            filt_data = {}
            
            for i in range(4):
                y_raw = np.array(raw_data[i])
                y_filt = signal.lfilter(b, a, y_raw)
                filt_data[i] = y_filt
                
            energia_orejas = int(np.std(np.diff(filt_data[0][-110:])) + np.std(np.diff(filt_data[3][-110:])))
            energia_frente = int(np.std(np.diff(filt_data[1][-110:])) + np.std(np.diff(filt_data[2][-110:])))

            # --- FASE 1: RECOLECCIÓN ---
            if fase_calibracion:
                tiempo_transcurrido = time.time() - tiempo_inicio
                if tiempo_transcurrido < TIEMPO_CALIBRACION:
                    # Ignoramos el primer segundo para evitar el ruido de encendido
                    if tiempo_transcurrido > 1.0: 
                        muestras_calibracion_orejas.append(energia_orejas)
                        muestras_calibracion_frente.append(energia_frente)
                    continue
                
                # --- FASE 2: CÁLCULO DE UMBRALES ---
                else:
                    promedio_orejas = np.mean(muestras_calibracion_orejas)
                    promedio_frente = np.mean(muestras_calibracion_frente)
                    
                    # Establecemos los umbrales sumando un margen matemático al ruido base (Ajustable)
                    UMBRAL_MANDIBULA = int(promedio_orejas * 1.8) # 80% por encima del reposo
                    UMBRAL_CEJAS = int(promedio_frente * 1.6)     # 60% por encima del reposo
                    
                    print("-" * 50)
                    print("CALIBRACIÓN COMPLETADA CON ÉXITO")
                    print(f"Ruido Base Orejas: {int(promedio_orejas)} -> Umbral Fijado: {UMBRAL_MANDIBULA}")
                    print(f"Ruido Base Frente: {int(promedio_frente)} -> Umbral Fijado: {UMBRAL_CEJAS}")
                    print("-" * 50)
                    print("\n[*] SISTEMA ACTIVO. Puede comenzar a operar.")
                    fase_calibracion = False
                    continue

            # --- FASE 3: OPERACIÓN (Lógica Difusa) ---
            if cooldown > 0:
                cooldown -= 1
            else:
                if energia_orejas > UMBRAL_DESCONEXION or energia_frente > UMBRAL_DESCONEXION:
                    print("\n[XXX] ERROR: DIADEMA DESCONECTADA")
                    cooldown = 40
                elif energia_orejas > UMBRAL_MANDIBULA:
                    print(f"\n[!!!] MANDÍBULA | Umbral superado: {energia_orejas} > {UMBRAL_MANDIBULA}")
                    cooldown = 20
                elif energia_frente > UMBRAL_CEJAS and energia_orejas < (UMBRAL_MANDIBULA * 0.8):
                    print(f"\n[*] CEJAS | Umbral superado: {energia_frente} > {UMBRAL_CEJAS}")
                    cooldown = 20

    except KeyboardInterrupt:
        print("\n[*] Sistema detenido.")