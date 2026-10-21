# main.py
import zmq
import threading
import time
from collections import deque
import numpy as np
from scipy import signal
from pythonosc import dispatcher, osc_server, udp_client

# Importamos nuestros nuevos módulos
from serial_manager import PuertoSerialPico
from logica_motor import ControladorMotores
import config

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

# --- Detección por umbral (flag de activación que se envía a la Pico) ---
sos_det = signal.butter(4, [config.BANDA_DETECTOR[0] / nyq, config.BANDA_DETECTOR[1] / nyq],
                        btype='band', output='sos')
zi_det = {c: None for c in ("TP9", "FP1", "FP2", "TP10")}
ventana_det = {c: deque(maxlen=config.VENTANA_EEG) for c in zi_det}

def actualizar_activacion(canales_raw):
    """Filtra el lote, actualiza la ventana deslizante de cada canal y devuelve 1 si el pico
    supera UMBRAL_TP en algún TP y UMBRAL_FP en algún FP (0 si no, o si la ventana no está llena)."""
    for i, canal in enumerate(zi_det):
        x = np.asarray(canales_raw[i], dtype=float)
        if zi_det[canal] is None:  # estado inicial escalado a la 1ª muestra (evita el transitorio por offset DC)
            zi_det[canal] = signal.sosfilt_zi(sos_det) * x[0]
        y, zi_det[canal] = signal.sosfilt(sos_det, x, zi=zi_det[canal])
        ventana_det[canal].extend(float(v) for v in y)

    if any(len(v) < config.VENTANA_EEG for v in ventana_det.values()):
        return 0
    pico = {c: max(abs(v) for v in w) for c, w in ventana_det.items()}
    tp = max(pico["TP9"], pico["TP10"]) > config.UMBRAL_TP
    fp = max(pico["FP1"], pico["FP2"]) > config.UMBRAL_FP
    return 1 if (tp and fp) else 0

def reiniciar_activacion():
    for c in zi_det:
        zi_det[c] = None
        ventana_det[c].clear()

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
    
    contacto_tp9 = 4
    contacto_fp1 = 4
    contacto_fp2 = 4
    contacto_tp10 = 4
    
    # --- WATCHDOG DE LA VINCHA ---
    tiempo_ultimo_dato = time.time()
    activacion = 0
    TIMEOUT_VINCHA = 0.5  # Segundos sin recibir datos antes de cortar motores
    
    while True:
        time.sleep(0.05) 
        
        with buffer_lock:
            lote_eeg = buffer_eeg.copy()
            lote_eventos = buffer_eventos.copy()
            buffer_eeg.clear()
            buffer_eventos.clear()
            
        # Si llegaron datos nuevos (EEG o Eventos), actualizamos el reloj del Watchdog
        if lote_eeg or lote_eventos:
            tiempo_ultimo_dato = time.time()
        
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

        # Flag de activación (EEG filtrado 1.5-30 Hz, ventana deslizante)
        if lote_eeg:
            activacion = actualizar_activacion(list(zip(*lote_eeg)))

        # --- LOGICA DE CORTE POR DESCONEXION ---
        if (time.time() - tiempo_ultimo_dato) > TIMEOUT_VINCHA:
            activacion = 0
            reiniciar_activacion()
            pwm1 = 0
            pwm2 = 0
            contacto_tp9 = 4
            contacto_fp1 = 4
            contacto_fp2 = 4
            contacto_tp10 = 4
            control_motores.forzar_parada()  # Limpia la inercia acumulada

        # ---------------------------------------------------------
        # ENVÍO 1: UART HACIA LA PICO (Físico)
        # ---------------------------------------------------------
        pico_serial.enviar_estado(
            contacto_tp9, contacto_fp1, contacto_fp2, contacto_tp10, 
            pwm1, pwm2, activacion
        )
        
        # ---------------------------------------------------------
        # ENVÍO 2: ZMQ DEBUG MOTORES (Red)
        # ---------------------------------------------------------
        if len(lote_eventos) > 0:
            paquete_debug_motor = {
                "tipo": "DEBUG_MOTOR",
                "pwm1": pwm1,
                "pwm2": pwm2,
                "x_crudo": estado_mot["x_crudo"],
                "z_crudo": estado_mot["z_crudo"],
                "x_filt": estado_mot["x_filt"],
                "z_filt": estado_mot["z_filt"],
                "activacion": activacion,
                "zona_x": config.ZONA_MUERTA_X,
                "zona_z": config.ZONA_MUERTA_Z
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