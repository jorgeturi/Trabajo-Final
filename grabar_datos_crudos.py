import csv
import threading
import time
import sys
import keyboard

# ==========================================
# CONFIGURACIÓN DEL EXPERIMENTO
# ==========================================
NOMBRE_MOVIMIENTO = "guiñar ojo derecho 2-10"
FS = 220.0  # Frecuencia de muestreo nominal Muse v1

# Variables globales
raw_ch_data = [0.0, 0.0, 0.0, 0.0]
horseshoe_status = [4.0, 4.0, 4.0, 4.0]
nombres_ch = ['TP9', 'FP1', 'FP2', 'TP10']
paquetes_eeg_recibidos = 0

# Configuración del archivo CSV
nombre_archivo = f"dataset_{NOMBRE_MOVIMIENTO}_{int(time.time())}.csv"
cabeceras = ['Timestamp', 'Trigger', 'Sensores_OK'] + nombres_ch

# Abrimos el archivo globalmente para escritura continua de alta velocidad
file_handle = open(nombre_archivo, mode='w', newline='', encoding='utf-8')
writer = csv.writer(file_handle)
writer.writerow(cabeceras) # Escribimos cabeceras de entrada

def eeg_handler(address, *args):
    global raw_ch_data, paquetes_eeg_recibidos
    if len(args) >= 4:
        raw_ch_data = list(args[:4])
        paquetes_eeg_recibidos += 1
        
        # Obtenemos el estado actual de las teclas en el momento exacto del paquete EEG
        trigger = 1 if keyboard.is_pressed('space') else 0
        sensores_ok = 1 if all(h <= 2.0 for h in horseshoe_status) else 0
        
        # Escribimos inmediatamente al recibir el paquete del hardware (A 220 Hz reales)
        fila = [time.time(), trigger, sensores_ok] + raw_ch_data
        try:
            writer.writerow(fila)
            file_handle.flush() # Asegura que se guarde en disco al instante sin buffer lento
        except Exception:
            pass

def horseshoe_handler(address, *args):
    global horseshoe_status
    if len(args) >= 4:
        horseshoe_status = list(args[:4])

def iniciar_osc():
    from pythonosc import dispatcher, osc_server
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    disp.map("/muse/elements/horseshoe", horseshoe_handler)
    
    print("Iniciando servidor OSC en 0.0.0.0:5000...")
    server = osc_server.ThreadingOSCUDPServer(("0.0.0.0", 5000), disp)
    server.serve_forever()

if __name__ == "__main__":
    # Lanzamos el servidor OSC en hilo daemon
    threading.Thread(target=iniciar_osc, daemon=True).start()
    time.sleep(1.0) 

    print("==================================================")
    print("Logger HMI - Grabación Sincronizada a 220Hz")
    print(f"Movimiento: {NOMBRE_MOVIMIENTO.upper()}")
    print(f"Archivo: {nombre_archivo}")
    print("Instrucciones: MANTENER PRESIONADA LA BARRA ESPACIADORA")
    print("antes de hacer el movimiento. Soltala despues de terminar.")
    print("Presiona 'ESC' para detener y guardar.")
    print("==================================================\n")

    try:
        while True:
            if keyboard.is_pressed('esc'):
                print(f"\nGrabación finalizada. Total paquetes EEG guardados: {paquetes_eeg_recibidos}")
                break
            
            # Feedback visual ligero en consola
            estado_trigger = "GRABANDO [ESPACIO]" if keyboard.is_pressed('space') else "Reposo..."
            ch_str = " | ".join([f"{nombres_ch[i]}: {raw_ch_data[i]:.2f}" for i in range(4)])
            hs_str = " | ".join([f"{nombres_ch[i]}_hs: {horseshoe_status[i]}" for i in range(4)])
            
            sys.stdout.write(
                f"\rPkts: {paquetes_eeg_recibidos} | {estado_trigger} | Raw[{ch_str}] | HS[{hs_str}]   "
            )
            sys.stdout.flush()
            time.sleep(0.05)
            
    finally:
        file_handle.close()
        print("\nArchivo cerrado correctamente.")