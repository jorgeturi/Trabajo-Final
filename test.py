import socket

# Configurar el socket UDP en localhost y puerto 5000
IP = "0.0.0.0"
PORT = 5000

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((IP, PORT))

print(f"👂 Escuchando UDP en {IP}:{PORT} (Socket puro)... Envía datos desde tu emisor.")

while True:
    data, addr = sock.recvfrom(1024) # Lee cualquier paquete que llegue
    print(f"¡Llegó algo! De {addr} -> Tamaño: {len(data)} bytes -> Contenido (hex/raw): {data[:50]}")