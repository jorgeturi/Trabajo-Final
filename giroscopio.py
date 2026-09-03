from pythonosc import dispatcher, osc_server
import time

# Memoria temporal para guardar el último paquete recibido
ultimo_print = time.time()
datos = {
    "acc": [0.0, 0.0, 0.0],
    "gyro": [0.0, 0.0, 0.0]
}

def handler_imu(address, *args):
    global ultimo_print
    
    # Guardamos los datos según la ruta que llegue
    if address == "/muse/acc":
        datos["acc"] = args
    elif address == "/muse/gyro":
        datos["gyro"] = args

    # Limitamos la impresión a 5 veces por segundo para que puedas leerlo cómodamente
    if time.time() - ultimo_print > 0.2:
        ultimo_print = time.time()
        
        ax, ay, az = datos["acc"]
        gx, gy, gz = datos["gyro"]
        
        # Limpiamos la consola y mostramos la matriz
        print(f"| ACELERÓMETRO (Inclinación/Gravedad) | GIROSCOPIO (Velocidad de Rotación) |")
        print(f"|-------------------------------------|------------------------------------|")
        print(f"| X (Adelante/Atrás): {ax:7.2f}         | X (Cabeceo): {gx:7.2f}               |")
        print(f"| Y (¿Rotación/Lado?):{ay:7.2f}         | Y (¿Rotación?): {gy:7.2f}            |")
        print(f"| Z (¿Rotación/Lado?):{az:7.2f}         | Z (¿Rotación?): {gz:7.2f}            |")
        print("-" * 76)

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    
    # Escuchamos ambos sensores
    disp.map("/muse/acc", handler_imu)
    disp.map("/muse/gyro", handler_imu)
    
    ip = "127.0.0.1"
    puerto = 5000
    server = osc_server.ThreadingOSCUDPServer((ip, puerto), disp)
    
    print("\n[*] INICIANDO TEST COMPLETO DEL IMU...")
    print("[*] 1. Asiente (Di que SÍ)")
    print("[*] 2. Gira el cuello (Di que NO)")
    print("[*] 3. Acerca la oreja al hombro")
    print("[*] Presiona Ctrl+C para salir.\n")
    
    server.serve_forever()

if __name__ == "__main__":
    try:
        iniciar_osc()
    except KeyboardInterrupt:
        print("\n[*] Test finalizado.")