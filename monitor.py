from pythonosc import dispatcher, osc_server
import time

# Control de tiempo para no saturar la consola con datos continuos
ultimo_print_continuo = time.time()

# --- HANDLERS DE EVENTOS CLAROS ---

def handler_parpadeo(address, *args):
    valor = args[0]
    if valor == 1 or valor == 1.0:
        print("\n" + "="*50)
        print("👁️  [!!!] EVENTO DETECTADO: PARPADEO")
        print("="*50 + "\n")

def handler_mandibula(address, *args):
    valor = args[0]
    if valor == 1 or valor == 1.0:
        print("\n" + "="*50)
        print("🦷  [!!!] EVENTO DETECTADO: MANDÍBULA APRETADA")
        print("="*50 + "\n")

# --- HANDLER PARA EL RESTO DEL PAQUETE ---

def handler_global(address, *args):
    global ultimo_print_continuo
    
    # 1. Filtramos el EEG crudo para que no explote la consola (256 líneas por segundo)
    if "/muse/eeg" in address and "quantization" not in address:
        pass # Ignorar silenciosamente
        
    # 2. Imprimimos el Acelerómetro y Giroscopio pero limitamos la velocidad visual
    elif "/muse/acc" in address or "/muse/gyro" in address:
        if time.time() - ultimo_print_continuo > 0.5: # Imprime cada medio segundo
            print(f"[SENSOR MOVIMIENTO] {address} -> Valores: {args}")
            ultimo_print_continuo = time.time()
            
    # 3. Imprimimos cualquier otra variable interesante que llegue (Batería, Herradura, etc.)
    else:
        # Excluimos rutas de ruido interno de muse para mantener limpio
        if "elements" not in address: 
            print(f"[PAQUETE OSC] {address} -> Valores: {args}")

def iniciar_servidor():
    # El Dispatcher se encarga de enrutar cada dirección OSC a su función
    disp = dispatcher.Dispatcher()
    
    # Mapeos específicos para tus gestos
    disp.map("/muse/elements/blink", handler_parpadeo)
    disp.map("/muse/elements/jaw_clench", handler_mandibula)
    
    # Todo lo que no sea parpadeo o mandíbula, cae en el global
    disp.set_default_handler(handler_global)

    # Iniciamos servidor UDP en localhost
    ip = "127.0.0.1"
    puerto = 5000
    server = osc_server.ThreadingOSCUDPServer((ip, puerto), disp)
    
    print(f"[*] Escuchando todo el tráfico OSC de Muse en {ip}:{puerto}")
    print("[*] Haz parpadeos fuertes o aprieta la mandíbula para probar los detectores...")
    print("[*] (Presiona Ctrl+C para detener)\n")
    
    server.serve_forever()

if __name__ == "__main__":
    try:
        iniciar_servidor()
    except KeyboardInterrupt:
        print("\n[*] Monitor OSC apagado.")