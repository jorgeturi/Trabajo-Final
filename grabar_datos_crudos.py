import numpy as np
from pythonosc import dispatcher, osc_server
import threading
import time
import sys
import csv
import keyboard

# ==========================================
# CONFIGURACIÓN DEL EXPERIMENTO
# ==========================================
NOMBRE_MOVIMIENTO = "dosparpadeosseguidos"  # Cambia esto según el gesto que grabes (ej: "reposo", "sonrisa")
FS = 220.0                   # Frecuencia de muestreo Muse v1

# Variables globales para los datos
raw_ch_data = [0.0, 0.0, 0.0, 0.0]
horseshoe_status = [4.0, 4.0, 4.0, 4.0] # 4.0 = Malo por defecto hasta recibir lectura real
nombres_ch = ['TP9', 'FP1', 'FP2', 'TP10']

paquetes_eeg_recibidos = 0

# Manejadores específicos para filtrar lo que nos importa
def eeg_handler(address, *args):
    global raw_ch_data, paquetes_eeg_recibidos
    if len(args) >= 4:
        raw_ch_data = list(args[:4])
        paquetes_eeg_recibidos += 1

def horseshoe_handler(address, *args):
    global horseshoe_status
    if len(args) >= 4:
        horseshoe_status = list(args[:4])

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    # Filtramos únicamente las rutas que nos interesan para el dataset
    disp.map("/muse/eeg", eeg_handler)
    disp.map("/muse/elements/horseshoe", horseshoe_handler)
    
    print("Iniciando servidor OSC en 0.0.0.0:5000 (Escuchando red local)...")
    # Escuchamos en 0.0.0.0 para recibir los paquetes que vengan desde la Raspberry Pi
    server = osc_server.ThreadingOSCUDPServer(("0.0.0.0", 5000), disp)
    server.serve_forever()

def grabar_dataset():
    global paquetes_eeg_recibidos
    nombre_archivo = f"dataset_{NOMBRE_MOVIMIENTO}_{int(time.time())}.csv"
    
    print("==================================================")
    print("🧠 Data Logger BCI - Grabación de Data Pura")
    print(f"Movimiento: {NOMBRE_MOVIMIENTO.upper()}")
    print(f"Archivo: {nombre_archivo}")
    print("Instrucciones: MANTÉN PRESIONADA LA BARRA ESPACIADORA")
    print("mientras haces el movimiento. Suéltala al terminar.")
    print("Presiona 'ESC' para detener y guardar.")
    print("==================================================\n")
    
    cabeceras = ['Timestamp', 'Trigger', 'Sensores_OK'] + nombres_ch
    
    with open(nombre_archivo, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(cabeceras)
        
        filas_grabadas = 0
        
        while True:
            if keyboard.is_pressed('esc'):
                print(f"\n✅ Grabación finalizada. {filas_grabadas} filas guardadas en {nombre_archivo}")
                break
                
            # Marcador de teclado (Trigger)
            trigger = 1 if keyboard.is_pressed('space') else 0
            
            # Validación de sensores: 1 si todos los valores de horseshoe son menores a 2.0 (buen contacto)
            sensores_ok = 1 if all(h <= 2.0 for h in horseshoe_status) else 0
            
            # Construcción de la fila con data pura
            fila = [time.time(), trigger, sensores_ok] + raw_ch_data
            writer.writerow(fila)
            filas_grabadas += 1
            
            # Feedback visual por consola
            estado_trigger = "🔴 GRABANDO [ESPACIO]" if trigger == 1 else "⚪ Reposo..."
            
            ch_str = " | ".join([f"{nombres_ch[i]}: {raw_ch_data[i]:.2f}" for i in range(4)])
            hs_str = " | ".join([f"{nombres_ch[i]}_hs: {horseshoe_status[i]}" for i in range(4)])
            
            sys.stdout.write(
                f"\rEEG Pkts: {paquetes_eeg_recibidos} | {estado_trigger} | "
                f"Raw[{ch_str}] | HS[{hs_str}]   "
            )
            sys.stdout.flush()
            
            time.sleep(0.01) # Tasa de refresco fluida para la interfaz de consola

if __name__ == "__main__":
    threading.Thread(target=iniciar_osc, daemon=True).start()
    time.sleep(1.0) 
    grabar_dataset()