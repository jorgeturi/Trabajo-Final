import numpy as np
from pythonosc import dispatcher, osc_server
import threading
import time
import sys
import csv
import keyboard
from collections import deque

FS = 220.0
BUFFER_SIZE = 256

ch_data = [deque(np.zeros(BUFFER_SIZE), maxlen=BUFFER_SIZE) for _ in range(4)]
window = np.hanning(BUFFER_SIZE)
freqs = np.fft.rfftfreq(BUFFER_SIZE, d=1.0/FS)

# Espectro de interés: de 1 a 110 Hz
idx_eval = np.where((freqs >= 1.0) & (freqs <= 110.0))[0]
freqs_eval = freqs[idx_eval]

nombres_ch = ['TP9', 'FP1', 'FP2', 'TP10']

def eeg_handler(address, *args):
    for i in range(4):
        ch_data[i].append(args[i])

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

def grabar_dataset():
    nombre_archivo = f"dataset_cejas_{int(time.time())}.csv"
    
    print("==================================================")
    print("🧠 Data Logger BCI - Grabación Offline")
    print(f"Archivo: {nombre_archivo}")
    print("Instrucciones: MANTÉN PRESIONADA LA BARRA ESPACIADORA")
    print("mientras levantas las cejas. Suéltala al relajarlas.")
    print("Presiona 'ESC' para detener y guardar.")
    print("==================================================\n")
    
    # 1. Preparar las cabeceras del CSV
    cabeceras = ['Timestamp', 'Trigger']
    for ch in nombres_ch:
        for f in freqs_eval:
            cabeceras.append(f"{ch}_{f:.1f}Hz")
            
    with open(nombre_archivo, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(cabeceras)
        
        filas_grabadas = 0
        
        while True:
            # Condición de salida
            if keyboard.is_pressed('esc'):
                print(f"\n✅ Grabación finalizada. {filas_grabadas} filas guardadas en {nombre_archivo}")
                break
                
            # Leer marcador de teclado
            trigger = 1 if keyboard.is_pressed('space') else 0
            
            fila = [time.time(), trigger]
            
            # Extraer y guardar la FFT de cada canal
            for i in range(4):
                señal = np.array(ch_data[i])
                if len(señal) < BUFFER_SIZE:
                    # Rellenar con ceros si el buffer aún no está lleno
                    fila.extend(np.zeros(len(idx_eval)))
                else:
                    señal = señal - np.mean(señal)
                    fft_actual = np.abs(np.fft.rfft(señal * window))[idx_eval]
                    fila.extend(fft_actual)
            
            writer.writerow(fila)
            filas_grabadas += 1
            
            # Feedback visual
            estado = "🔴 GRABANDO GESTO [ESPACIO]" if trigger == 1 else "⚪ Reposo..."
            sys.stdout.write(f"\rFilas: {filas_grabadas} | Estado: {estado}       ")
            sys.stdout.flush()
            
            time.sleep(0.05) # 20 FPS

if __name__ == "__main__":
    print("empezando")
    threading.Thread(target=iniciar_osc, daemon=True).start()
    # Pequeña pausa para que el buffer se llene antes de grabar
    time.sleep(1.5)
    grabar_dataset()