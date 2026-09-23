import zmq
import time

IP_RASPBERRY = "192.168.1.50" 

def crear_suscriptor():
    context = zmq.Context()
    sock = context.socket(zmq.SUB)
    sock.connect(f"tcp://{IP_RASPBERRY}:5555")
    sock.setsockopt_string(zmq.SUBSCRIBE, "")
    return context, sock

context, sock = crear_suscriptor()
poller = zmq.Poller()
poller.register(sock, zmq.POLLIN)

print(f"👂 Escuchando ZMQ en tcp://{IP_RASPBERRY}:5555...")

while True:
    try:
        # Esperar datos con un timeout de 2000 ms (2 segundos)
        sockets = dict(poller.poll(2000))
        
        if sock in sockets:
            mensaje = sock.recv_json(flags=zmq.NOBLOCK)
            print(f"📥 RECIBIDO -> Tipo: {mensaje.get('tipo')} | Contenido: {mensaje}")
        else:
            # Si pasan 2 segundos sin recibir nada, avisa (posible corte de red / cambio de IP)
            print("⚠️ Sin datos... Verificá si sigues en la misma red Wi-Fi de la Raspberry.")
            
    except KeyboardInterrupt:
        print("\nSaliendo...")
        break
    except Exception as e:
        print(f"❌ Error de red/lectura: {e}. Intentando reconectar en 3 segundos...")
        time.sleep(3)
        try:
            sock.close()
            context.term()
            context, sock = crear_suscriptor()
            poller = zmq.Poller()
            poller.register(sock, zmq.POLLIN)
            print("🔄 ¡Reconectado al socket ZMQ!")
        except Exception as recon_err:
            print(f"Fallo al reconectar: {recon_err}")