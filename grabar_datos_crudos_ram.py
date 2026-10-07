import csv
import threading
import time
import sys
import keyboard
from pythonosc import dispatcher, osc_server

# ==========================================
# CONFIGURACION DEL EXPERIMENTO
# ==========================================
nombre_movimiento = "levantar cejas 7-10 test"
frecuencia_teorica = 220.0

# Variables globales compartidas
horseshoe_status = [4.0, 4.0, 4.0, 4.0]
nombres_canales = ['TP9', 'FP1', 'FP2', 'TP10']
paquetes_eeg_recibidos = 0
estado_trigger_global = 0

# Buffer en RAM para almacenar los datos antes de escribirlos al disco
buffer_datos = []
buffer_lock = threading.Lock()

# Configuración del archivo CSV
nombre_archivo = f"dataset_{nombre_movimiento.replace(' ', '_')}_{int(time.time())}.csv"
cabeceras = ['Timestamp', 'Trigger', 'Sensores_OK'] + nombres_canales

# ==========================================
# HANDLERS OSC (Hilos de recepcion de red)
# ==========================================
def eeg_handler(address, *args):
    global paquetes_eeg_recibidos
    if len(args) >= 4:
        # Evaluacion ultra rapida de sensores
        sensores_ok = 1 if all(h <= 2.0 for h in horseshoe_status) else 0
        
        # Almacenamos en RAM instantaneamente (Sin bloqueos de disco ni de teclado)
        fila = [time.time(), estado_trigger_global, sensores_ok] + list(args[:4])
        
        with buffer_lock:
            buffer_datos.append(fila)
            paquetes_eeg_recibidos += 1

def horseshoe_handler(address, *args):
    global horseshoe_status
    if len(args) >= 4:
        horseshoe_status = list(args[:4])

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    disp.map("/muse/elements/horseshoe", horseshoe_handler)
    
    server = osc_server.ThreadingOSCUDPServer(("0.0.0.0", 5000), disp)
    server.serve_forever()

# ==========================================
# HILO PRINCIPAL (Logica y Escritura a disco)
# ==========================================
if __name__ == "__main__":
    file_handle = open(nombre_archivo, mode='w', newline='', encoding='utf-8')
    writer = csv.writer(file_handle)
    writer.writerow(cabeceras)

    # Lanzamos el servidor OSC en hilo daemon
    threading.Thread(target=iniciar_osc, daemon=True).start()
    time.sleep(1.0) 

    print("==================================================")
    print("Logger HMI - Grabacion Sincronizada Optimizada")
    print(f"Movimiento: {nombre_movimiento.upper()}")
    print(f"Archivo: {nombre_archivo}")
    print("Instrucciones: MANTENER PRESIONADA LA BARRA ESPACIADORA")
    print("antes de hacer el movimiento. Soltala despues de terminar.")
    print("Presiona 'ESC' para detener y guardar.")
    print("==================================================\n")

    try:
        while True:
            # 1. Leemos el teclado desde el hilo principal de forma mas relajada (20 Hz)
            estado_trigger_global = 1 if keyboard.is_pressed('space') else 0
            
            if keyboard.is_pressed('esc'):
                print(f"\nGrabacion finalizada. Total paquetes EEG guardados: {paquetes_eeg_recibidos}")
                break
            
            # 2. Extraemos los datos acumulados en el buffer y los escribimos de golpe (Lotes)
            lote_a_escribir = []
            with buffer_lock:
                if len(buffer_datos) > 0:
                    lote_a_escribir = buffer_datos.copy()
                    buffer_datos.clear()

            if lote_a_escribir:
                writer.writerows(lote_a_escribir)
            
            # 3. Feedback visual ligero
            estado_visual = "GRABANDO [ESPACIO]" if estado_trigger_global else "Reposo..."
            if lote_a_escribir:
                ultimos_datos = lote_a_escribir[-1][3:7] # TP9, FP1, FP2, TP10
                ch_str = " | ".join([f"{nombres_canales[i]}: {ultimos_datos[i]:.2f}" for i in range(4)])
                hs_str = " | ".join([f"{h}" for h in horseshoe_status])
                
                sys.stdout.write(f"\rPkts: {paquetes_eeg_recibidos} | {estado_visual} | Raw[{ch_str}] | HS[{hs_str}]   ")
                sys.stdout.flush()
            
            time.sleep(0.05) # Iteramos cada 50ms para no saturar el CPU
            
    finally:
        # Vaciamos cualquier dato remanente en el buffer antes de cerrar
        with buffer_lock:
            if buffer_datos:
                writer.writerows(buffer_datos)
                
        file_handle.close()
        print("\nArchivo cerrado y guardado correctamente.")