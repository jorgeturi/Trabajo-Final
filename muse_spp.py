import socket
import sys

# La dirección física exacta de tu diadema Muse v1
MAC_ADDRESS = "00:06:66:70:5E:54"
PORT = 1  # Canal estándar para SPP

print(f"Iniciando conexión directa RFCOMM a {MAC_ADDRESS}...")

try:
    # Creamos un socket nativo de Bluetooth
    sock = socket.socket(socket.AF_BTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.connect((MAC_ADDRESS, PORT))
    
    print("¡Conexión de radiofrecuencia establecida exitosamente!")
    print("Escuchando flujo de bytes crudos... Presioná Ctrl+C para salir.\n")

    while True:
        # Leemos un bloque de la trama serial
        raw_data = sock.recv(32)
        
        if not raw_data:
            break
        
        # Convertimos el tren de bytes a un formato Hexadecimal legible
        hex_stream = " ".join(f"{b:02X}" for b in raw_data)
        print(f"RX: {hex_stream}")

except OSError as e:
    print(f"Error del socket Bluetooth: {e}")
    print("Verificá que el Muse esté encendido, emparejado a Windows y sin el muse-io corriendo.")
except KeyboardInterrupt:
    print("\nDeteniendo captura...")
finally:
    sock.close()
    print("Puerto serial cerrado.")