import serial
import time

PUERTO_COM = "COM3" 
BAUDRATE = 115200 

print(f"Abriendo puente serial Bluetooth en {PUERTO_COM}...")

try:
    # Usamos un timeout bajo para no bloquear el procesador
    puerto = serial.Serial(PUERTO_COM, BAUDRATE, timeout=0.01)
    print("¡Conexión establecida!")
    
    puerto.write(b's\n') 
    ultimo_latido = time.time()

    buffer_paquete = bytearray()
    print("Iniciando decodificador de paquetes... Presioná Ctrl+C para salir.\n")

    while True:
        tiempo_actual = time.time()
        
        # Mantener viva la conexión
        if tiempo_actual - ultimo_latido > 3.0:
            puerto.write(b'k\n')
            ultimo_latido = tiempo_actual
            
        # Leemos todo lo que haya en el buffer de Windows
        if puerto.in_waiting > 0:
            datos_crudos = puerto.read(puerto.in_waiting)
            
            # Procesamos la trama byte por byte
            for byte in datos_crudos:
                if byte == 0xC0: # ¡Encontramos el delimitador SLIP!
                    longitud = len(buffer_paquete)
                    
                    # Ignoramos paquetes vacíos (a veces mandan dos C0 seguidos)
                    if longitud > 0:
                        # Extraemos los primeros bytes para ver la firma
                        cabecera = buffer_paquete[0:2].hex().upper()
                        
                        # --- CLASIFICADOR EMPÍRICO ---
                        # Identificamos el tipo de sensor por la longitud del paquete
                        if longitud > 45:
                            tipo = "🧠 EEG (Bioseñal)"
                        elif 30 < longitud <= 45:
                            tipo = "🚀 IMU (Acel/Giroscopio)"
                        elif longitud <= 30:
                            tipo = "🔋 Telemetría / Estado"
                        else:
                            tipo = "❓ Desconocido"
                            
                        # Imprimimos el tipo, tamaño y los datos (limitados para no saturar la pantalla)
                        hex_str = " ".join(f"{b:02X}" for b in buffer_paquete)
                        print(f"[{tipo:<25}] Len: {longitud:<3} | Datos: {hex_str}")
                        
                        # Limpiamos el buffer para armar el próximo paquete
                        buffer_paquete.clear()
                else:
                    # Si no es C0, seguimos agregando bytes al paquete actual
                    buffer_paquete.append(byte)

except serial.SerialException as e:
    print(f"\n❌ Error del Puerto Serie: {e}")
except KeyboardInterrupt:
    print("\nDeteniendo captura...")
finally:
    if 'puerto' in locals() and puerto.is_open:
        puerto.write(b'h\n')
        puerto.close()
        print("Puerto serial cerrado y diadema en reposo.")