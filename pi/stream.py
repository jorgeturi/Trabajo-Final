# main.py
import zmq
import threading
import time
from scipy import signal
from pythonosc import dispatcher, osc_server, udp_client

# Importamos nuestros nuevos módulos
from serial_manager import PuertoSerialPico
from logica_motor import ControladorMotores

# ==========================================
# 1. CONFIGURACION GENERAL
# ==========================================
ip_pc = "192.168.1.55"
puerto_pc = 5000
cliente_espejo = udp_client.SimpleUDPClient(ip_pc, puerto_pc)

context = zmq.Context()
zmq_socket = context.socket(zmq.PUB)
zmq_socket.bind("tcp://0.0.0.0:5555")
zmq_lock = threading.Lock()

buffer_eeg = []
buffer_eventos = []
buffer_lock = threading.Lock()

factor_submuestreo_zmq = 1 
contador_submuestreo = 0

# Inicializamos el puerto y la lógica de motores
pico_serial = PuertoSerialPico()
control_motores = ControladorMotores()

# ==========================================
# 2. CONFIGURACION DSP 
# ==========================================
fs = 220.0 
nyq = 0.5 * fs
b_eeg, a_eeg = signal.butter(4, [8.0 / nyq, 30.0 / nyq], btype='band')

estado_filtro = {
    "TP9": signal.lfilter_zi(b_eeg, a_eeg), "FP1": signal.lfilter_zi(b_eeg, a_eeg),
    "FP2": signal.lfilter_zi(b_eeg, a_eeg), "TP10": signal.lfilter_zi(b_eeg, a_eeg)
}

# ==========================================
# 3. HANDLERS OSC (Mismos que antes)
# ==========================================
def procesar_eeg(address, *args):
    cliente_espejo.send_message(address, args)
    with buffer_lock: buffer_eeg.append(args[:4])

def procesar_acc(address, *args):
    cliente_espejo.send_message(address, args)
    with buffer_lock: buffer_eventos.append({"tipo": "ACC", "x": float(args[0]), "y": float(args[1]), "z": float(args[2])})

def procesar_parpadeo(address, *args):
    cliente_espejo.send_message(address, args)
    with buffer_lock: buffer_eventos.append({"tipo": "BLINK", "valor": int(args[0])})

def procesar_mandibula(address, *args):
    cliente_espejo.send_message(address, args)
    with buffer_lock: buffer_eventos.append({"tipo": "JAW_CLENCH", "valor": int(args[0])})

def procesar_herradura(address, *args):
    cliente_espejo.send_message(address, args)
    with buffer_lock: buffer_eventos.append({"tipo": "HORSESHOE", "TP9": int(args[0]), "FP1": int(args[1]), "FP2": int(args[2]), "TP10": int(args[3])})

def procesar_resto(address, *args):
    cliente_espejo.send_message(address, args)
    with buffer_lock: buffer_eventos.append({"tipo": "OTRO", "address": address, "args": list(args)})

# ==========================================
# 4. HILO PROCESADOR 
# ==========================================
def hilo_procesador_bci():
    global estado_filtro, contador_submuestreo
    
    while True:
        time.sleep(0.05) 
        
        # DENTRO DEL while True DE hilo_procesador_bci:
        
        with buffer_lock:
            lote_eeg = buffer_eeg.copy()
            lote_eventos = buffer_eventos.copy()
            buffer_eeg.clear()
            buffer_eventos.clear()
        
        # 1. Calculamos y pedimos el estado completo de los motores
        control_motores.procesar_eventos(lote_eventos)
        estado_mot = control_motores.obtener_estado_completo()
        
        pwm1 = estado_mot["pwm1"]
        pwm2 = estado_mot["pwm2"]
                
        # 2. Procesamos eventos (solo para ZMQ OSC original y estado HORSESHOE)
        for evento in lote_eventos:
            with zmq_lock:
                zmq_socket.send_json(evento)
            
            if evento["tipo"] == "HORSESHOE":
                contacto_tp9 = evento['TP9']
                contacto_fp1 = evento['FP1']
                contacto_fp2 = evento['FP2']
                contacto_tp10 = evento['TP10']

        # ---------------------------------------------------------
        # ENVÍO 1: UART HACIA LA PICO (Físico)
        # ---------------------------------------------------------
        pico_serial.enviar_estado(
            contacto_tp9, contacto_fp1, contacto_fp2, contacto_tp10, 
            pwm1, pwm2
        )
        
        # ---------------------------------------------------------
        # ENVÍO 2: ZMQ DEBUG MOTORES (Red)
        # ---------------------------------------------------------
        paquete_debug_motor = {
            "tipo": "DEBUG_MOTOR",
            "pwm1": pwm1,
            "pwm2": pwm2,
            "x_crudo": estado_mot["x_crudo"],
            "z_crudo": estado_mot["z_crudo"],
            "x_filt": estado_mot["x_filt"],
            "z_filt": estado_mot["z_filt"]
        }
        with zmq_lock:
            zmq_socket.send_json(paquete_debug_motor)
                
        # 3. Filtrar y enviar EEG
        if lote_eeg:
            canales_raw = list(zip(*lote_eeg)) 
            tp9_f, estado_filtro["TP9"] = signal.lfilter(b_eeg, a_eeg, canales_raw[0], zi=estado_filtro["TP9"])
            fp1_f, estado_filtro["FP1"] = signal.lfilter(b_eeg, a_eeg, canales_raw[1], zi=estado_filtro["FP1"])
            fp2_f, estado_filtro["FP2"] = signal.lfilter(b_eeg, a_eeg, canales_raw[2], zi=estado_filtro["FP2"])
            tp10_f, estado_filtro["TP10"] = signal.lfilter(b_eeg, a_eeg, canales_raw[3], zi=estado_filtro["TP10"])
            
            for i in range(len(lote_eeg)):
                contador_submuestreo += 1
                if contador_submuestreo >= factor_submuestreo_zmq:
                    paquete_eeg = {
                        "tipo": "EEG",
                        "TP9_raw": float(canales_raw[0][i]),   "TP9_filtrado": float(tp9_f[i]),
                        "FP1_raw": float(canales_raw[1][i]),   "FP1_filtrado": float(fp1_f[i]),
                        "FP2_raw": float(canales_raw[2][i]),   "FP2_filtrado": float(fp2_f[i]),
                        "TP10_raw": float(canales_raw[3][i]),  "TP10_filtrado": float(tp10_f[i])
                    }
                    with zmq_lock: zmq_socket.send_json(paquete_eeg)
                    contador_submuestreo = 0

# ==========================================
# 5. ENRUTADOR Y SERVIDOR
# ==========================================
disp = dispatcher.Dispatcher()
disp.map("/muse/eeg", procesar_eeg)
disp.map("/muse/acc", procesar_acc)
disp.map("/muse/elements/blink", procesar_parpadeo)
disp.map("/muse/elements/jaw_clench", procesar_mandibula)
disp.map("/muse/elements/horseshoe", procesar_herradura)
disp.set_default_handler(procesar_resto)

server = osc_server.BlockingOSCUDPServer(("127.0.0.1", 5000), disp)

print("===================================================")
print(" Nodo Edge BCI - RASPBERRY PI MODULAR")
print(f" ZMQ y UDP Espejo Activados")
print("===================================================")

threading.Thread(target=hilo_procesador_bci, daemon=True).start()

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\nApagando sistema...")
    pico_serial.cerrar()
    zmq_socket.close()
    context.term()