import numpy as np
from pythonosc import dispatcher, osc_server
import threading
import time
import sys
from collections import deque

FS = 220.0
BUFFER_SIZE = 256

ch_data = [deque(np.zeros(BUFFER_SIZE), maxlen=BUFFER_SIZE) for _ in range(4)]
window = np.hanning(BUFFER_SIZE)
freqs = np.fft.rfftfreq(BUFFER_SIZE, d=1.0/FS)
idx_eval = np.where((freqs >= 15.0) & (freqs <= 45.0))[0]
num_bins = len(idx_eval)

historial_fft = [deque(maxlen=40) for _ in range(4)]
nombres = ['TP9', 'FP1', 'FP2', 'TP10']
CANAL_MAESTRO = 0 

ultimo_disparo = [0.0, 0.0, 0.0, 0.0]
TIEMPO_COOLDOWN = 2.0 

# --- CALIBRACIÓN FINA (Según el último log) ---
UMBRAL_RUIDO_BASE = 350.0       
UMBRAL_ENERGIA_MINIMA = 600.0   # Bajado para capturar los gestos de 750-900
TECHO_SATURACION = 6000.0       

# --- VETO ESPACIAL DUAL ---
UMBRAL_VETO_FRONTAL = 2800.0    # FP1: Permite cejas fuertes, bloquea golpes
UMBRAL_VETO_LATERAL = 1300.0    # TP10: Bloquea tirones de cable o giros de cuello

analizando_forma = False
pico_transitorio = 0.0
frames_transitorio = 0
TIMEOUT_FRAMES = 15 

def grado_membresia(valor, min_val, max_val):
    if valor <= min_val: return 0.0
    if valor >= max_val: return 1.0
    return (valor - min_val) / (max_val - min_val)

def inferencia_difusa(energia_ratio, coherencia_pct):
    energia_fuerte = grado_membresia(energia_ratio, 1.3, 2.5) 
    coherencia_alta = grado_membresia(coherencia_pct, 15.0, 50.0) 
    certeza_base = min(energia_fuerte, coherencia_alta)
    return certeza_base * 100.0

def calcular_energia(canal_idx):
    señal = np.array(ch_data[canal_idx])
    if len(señal) < BUFFER_SIZE: return 0.0
    señal = señal - np.mean(señal)
    fft_actual = np.abs(np.fft.rfft(señal * window))[idx_eval]
    return np.mean(fft_actual)

def eeg_handler(address, *args):
    for i in range(4):
        ch_data[i].append(args[i])

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

def depurador_final():
    global analizando_forma, pico_transitorio, frames_transitorio
    
    print("=======================================================================================")
    print("🧠 BCI ESP32 - Calibración Final (Gestos Suaves + Veto Dual)")
    print("Formato: E=Energía Absoluta | B=Base Blindada | R=Ratio Multiplicativo")
    print("=======================================================================================\n")
    
    frame_global = 0
    
    while True:
        interfaz = f"[{frame_global:04d}] "
        tiempo_actual = time.time()
        comando_silla = False
        
        energias_actuales = [calcular_energia(i) for i in range(4)]
        
        for i in range(4):
            energia_actual = energias_actuales[i]
            if energia_actual == 0.0: continue
            
            señal = np.array(ch_data[i])
            señal = señal - np.mean(señal)
            fft_actual = np.abs(np.fft.rfft(señal * window))[idx_eval]
            
            if len(historial_fft[i]) < 40:
                historial_fft[i].append(fft_actual)
                continue
                
            mediana_base = np.median(historial_fft[i], axis=0) + 1.0
            energia_base = np.mean(mediana_base)
            
            ratio_energia = energia_actual / energia_base
            bins_encendidos = np.sum(fft_actual > (mediana_base * 1.5))
            coherencia_actual = (bins_encendidos / num_bins) * 100.0
            estado_sensor = "  silencio"
            
            # --- MÁQUINA DE ESTADOS DINÁMICA (CANAL MAESTRO) ---
            if i == CANAL_MAESTRO:
                if analizando_forma:
                    frames_transitorio += 1
                    pico_transitorio = max(pico_transitorio, energia_actual)
                    
                    # 1. VETO ESPACIAL DUAL
                    if energias_actuales[1] > UMBRAL_VETO_FRONTAL or energias_actuales[3] > UMBRAL_VETO_LATERAL:
                        estado_sensor = "🚫 VETADO (Impacto en Vincha)"
                        analizando_forma = False
                        ultimo_disparo[i] = tiempo_actual
                    
                    # 2. TIMEOUT (Movimiento lento o tensión)
                    elif frames_transitorio >= TIMEOUT_FRAMES:
                        estado_sensor = "❌ DESCARTADO (Lento)"
                        analizando_forma = False
                        ultimo_disparo[i] = tiempo_actual
                    
                    # 3. ÉXITO (Caída del 30%)
                    elif energia_actual < (pico_transitorio * 0.7):
                        comando_silla = True
                        estado_sensor = f"✅ AUTORIZADA (Pico: {pico_transitorio:.0f})"
                        analizando_forma = False
                        ultimo_disparo[i] = tiempo_actual
                        
                    else:
                        estado_sensor = f"🔍 BUSCANDO CAÍDA ({frames_transitorio}/{TIMEOUT_FRAMES})"
                        
                elif (tiempo_actual - ultimo_disparo[i]) < TIEMPO_COOLDOWN:
                    estado_sensor = "⏳ ENFRIAM "
                elif energia_actual > TECHO_SATURACION:
                    estado_sensor = "⚠️ SATURAC"
                
                # --- LÓGICA DE BLINDAJE DE BASE ---
                elif energia_actual < UMBRAL_RUIDO_BASE:
                    estado_sensor = "  silencio"
                    historial_fft[i].append(fft_actual) 
                    
                elif energia_actual < UMBRAL_ENERGIA_MINIMA:
                    estado_sensor = "  zona muerta"
                    
                else:
                    certeza = inferencia_difusa(ratio_energia, coherencia_actual)
                    if certeza > 75.0:
                        analizando_forma = True
                        pico_transitorio = energia_actual
                        frames_transitorio = 1
                        estado_sensor = "⚠️ INICIANDO ANÁLISIS"
                    else:
                        estado_sensor = "  evaluando"
                        
                interfaz += f"⭐ {nombres[i]}: (E:{energia_actual:4.0f} | B:{energia_base:4.0f} | R:{ratio_energia:4.1f}x) {estado_sensor} | "
            else:
                interfaz += f"{nombres[i]}: (E:{energia_actual:4.0f} | R:{ratio_energia:4.1f}x) | "
            
        if comando_silla:
            interfaz += " 🚀🚀 SILLA EN MOVIMIENTO 🚀🚀"
            
        if "⭐ TP9:" in interfaz: 
            sys.stdout.write(interfaz + "\n")
            sys.stdout.flush()
            
        frame_global += 1
        time.sleep(0.08)

if __name__ == "__main__":
    threading.Thread(target=iniciar_osc, daemon=True).start()
    depurador_final()