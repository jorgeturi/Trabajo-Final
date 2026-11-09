import csv
import threading
import time
import sys
import keyboard
from pythonosc import dispatcher, osc_server

# ==========================================
# CONFIGURACION DEL EXPERIMENTO
# ==========================================
nombre_movimiento = "gestos con movimientos de cabeza 6-11"
frecuencia_teorica = 220.0

# Variables globales compartidas
horseshoe_status = [4.0, 4.0, 4.0, 4.0]
nombres_canales = ['TP9', 'FP1', 'FP2', 'TP10']
paquetes_eeg_recibidos = 0
estado_trigger_global = 0
frente_status = -1                 # /muse/elements/touching_forehead (1 = toca la frente, 0 = no, -1 = todavia no llego)
acc_status = [0.0, 0.0, 0.0]       # ultimo acelerometro recibido (x, y, z)
blink_pendiente = 0                # eventos del SDK: quedan en 1 en la primera muestra EEG que llega despues del evento
jaw_pendiente = 0

# Buffer en RAM para almacenar los datos antes de escribirlos al disco
buffer_datos = []
buffer_lock = threading.Lock()

# Configuración del archivo CSV
nombre_archivo = f"dataset_{nombre_movimiento.replace(' ', '_')}_{int(time.time())}.csv"
# Las columnas nuevas van al final: los scripts que leen por nombre (gestos_patron.py) siguen funcionando igual.
cabeceras = (['Timestamp', 'Trigger', 'Sensores_OK'] + nombres_canales
             + ['HS_' + c for c in nombres_canales] + ['Frente', 'ACC_X', 'ACC_Y', 'ACC_Z', 'Blink', 'Jaw'])

# ==========================================
# HANDLERS OSC (Hilos de recepcion de red)
# ==========================================
def eeg_handler(address, *args):
    global paquetes_eeg_recibidos, blink_pendiente, jaw_pendiente
    if len(args) >= 4:
        # Evaluacion ultra rapida de sensores
        sensores_ok = 1 if all(h <= 2.0 for h in horseshoe_status) else 0
        
        # Almacenamos en RAM instantaneamente (Sin bloqueos de disco ni de teclado)
        fila = ([time.time(), estado_trigger_global, sensores_ok] + list(args[:4])
                + list(horseshoe_status) + [frente_status] + list(acc_status) + [blink_pendiente, jaw_pendiente])
        blink_pendiente = 0
        jaw_pendiente = 0
        
        with buffer_lock:
            buffer_datos.append(fila)
            paquetes_eeg_recibidos += 1

def horseshoe_handler(address, *args):
    global horseshoe_status
    if len(args) >= 4:
        horseshoe_status = list(args[:4])

def frente_handler(address, *args):
    global frente_status
    if args:
        frente_status = int(args[0])

def acc_handler(address, *args):
    global acc_status
    if len(args) >= 3:
        acc_status = [float(a) for a in args[:3]]

def blink_handler(address, *args):
    global blink_pendiente
    if args and int(args[0]):
        blink_pendiente = 1

def jaw_handler(address, *args):
    global jaw_pendiente
    if args and int(args[0]):
        jaw_pendiente = 1

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    disp.map("/muse/elements/horseshoe", horseshoe_handler)
    disp.map("/muse/elements/touching_forehead", frente_handler)
    disp.map("/muse/acc", acc_handler)
    disp.map("/muse/elements/blink", blink_handler)
    disp.map("/muse/elements/jaw_clench", jaw_handler)
    
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