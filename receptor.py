import zmq

# Reemplazá esto con la IP que te dio la Raspberry
IP_RASPBERRY = "192.168.100.45"  

# 1. Configurar el cliente ZMQ
context = zmq.Context()
sock = context.socket(zmq.SUB)

# 2. Conectarse al servidor ZMQ de la Raspberry
sock.connect(f"tcp://{IP_RASPBERRY}:5555")

# 3. Suscribirse a TODOS los mensajes (dejamos el filtro vacío)
sock.setsockopt_string(zmq.SUBSCRIBE, "")

print(f"📡 Conectado a la Raspberry Pi en {IP_RASPBERRY}:5555")
print("Esperando stream de datos JSON...\n")

try:
    while True:
        # Recibir el JSON directamente decodificado como un diccionario de Python
        datos_json = sock.recv_json()
        
        # Imprimir en la consola
        print(datos_json)
        
except KeyboardInterrupt:
    print("\nDesconectado. Cerrando cliente.")
    sock.close()
    context.term()