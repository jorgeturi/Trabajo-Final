import serial
import time

PUERTO_COM = "COM3" 
BAUDRATE = 115200 

print(f"Abriendo puente serial Bluetooth en {PUERTO_COM}...")

try:
    puerto = serial.Serial(PUERTO_COM, BAUDRATE, timeout=1)
    print("¡Conexión establecida!")
    
    # 1. Comando de Inicio (Abre la canilla de datos)
    print("Enviando comando de inicio (Start)...")
    puerto.write(b's\n') 
    
    # Temporizador para el Heartbeat
    ultimo_latido = time.time()

    print("Escuchando flujo de datos... Presioná Ctrl+C para salir.\n")

    while True:
        tiempo_actual = time.time()
        
        # 2. El Heartbeat: Enviamos 'k\n' cada 3 segundos
        if tiempo_actual - ultimo_latido > 3.0:
            puerto.write(b'k\n')
            ultimo_latido = tiempo_actual
            
        # 3. Lectura continua
        if puerto.in_waiting > 0:
            raw_data = puerto.read(puerto.in_waiting)
            hex_stream = " ".join(f"{b:02X}" for b in raw_data)
            print(f"RX: {hex_stream}")

except serial.SerialException as e:
    print(f"\n❌ Error del Puerto Serie: {e}")
except KeyboardInterrupt:
    print("\nDeteniendo captura...")
finally:
    if 'puerto' in locals() and puerto.is_open:
        # Buena práctica: Enviamos el comando Halt ('h') para dormir la diadema antes de cerrar
        puerto.write(b'h\n')
        puerto.close()
        print("Puerto serial cerrado y diadema en reposo.")