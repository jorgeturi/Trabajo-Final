# main.py
import zmq
import threading
import time
import queue
import traceback
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

# Submuestreo del EEG hacia el detector: se pasa 1 de cada config.SUBMUESTREO_DETECTOR muestras (220 Hz -> 110 Hz)
contador_submuestreo = 0

# Inicializamos el puerto y la lógica de motores
pico_serial = PuertoSerialPico()
control_motores = ControladorMotores()

# ==========================================
# 2. CONFIGURACION DSP 
# ==========================================
fs = 220.0                                    # tasa de EEG de la vincha
fs_det = fs / config.SUBMUESTREO_DETECTOR     # tasa con la que trabaja el detector (110 Hz)
nyq = 0.5 * fs

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

# --- Detector adaptativo (detector_gestos.py) en su propio hilo: no frena el bucle de motores ni la UART ---
USA_ADAPTATIVO = (config.DETECTOR == "adaptativo")
cola_det = queue.Queue(maxsize=400)          # lotes EEG pendientes (~20 s); si se llena se descarta el lote nuevo
estado_det = {"activacion": 0, "limpio": True, "fallo": False, "calibrando": True}

def hilo_detector():
    try:
        from detector_gestos import DetectorGestos
        detector = DetectorGestos(fs_det)
    except Exception:
        traceback.print_exc()
        print("[ERROR] No arranca el detector adaptativo (falta detector_gestos.py o pandas?). Se usa el detector simple de umbral fijo.", flush=True)
        estado_det["fallo"] = True
        return
    print("[INFO] Detector adaptativo listo (calibra durante los primeros ~6 s de señal).", flush=True)
    while True:
        lote = cola_det.get()
        try:
            if isinstance(lote, str):        # "APAGAR": entro en emergencia, la activacion mantenida vuelve a 0 (hace falta un gesto nuevo)
                if hasattr(detector, "estado"):
                    detector.estado = 0
                estado_det["activacion"] = 0
                continue
            if lote is None:                 # vincha desconectada: se vuelve a calibrar al reconectar
                detector.reiniciar()
                estado_det["activacion"] = 0
                estado_det["calibrando"] = True
                continue
            cont = estado_det.get("contacto")
            if cont and hasattr(detector, "contacto"):       # (un detector_gestos.py viejo no lo tiene)
                detector.contacto(cont)
            if "frente" in estado_det and hasattr(detector, "frente"):
                detector.frente(estado_det["frente"])
            t0 = time.perf_counter()
            paquetes = detector.procesar(lote)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            publicar_eeg_detector(lote, detector)
            for paquete in paquetes:
                paquete["ms"] = ms                   # cuanto tardo decidir (si se acerca a 250 ms no llega en tiempo real)
                paquete["cola"] = cola_det.qsize()   # lotes esperando: si crece, el detector se atrasa
                estado_det["activacion"] = paquete["activacion"]
                estado_det["calibrando"] = paquete["calibrando"]
                with zmq_lock:
                    zmq_socket.send_json(paquete)
        except Exception:
            traceback.print_exc()            # el hilo sigue vivo aunque falle una ventana

def publicar_eeg_detector(lote, detector):
    """Publica por ZMQ el EEG que analiza el detector: crudo (submuestreado a 110 Hz) y filtrado por SU pasabanda de 8-30 Hz
    (no hay un filtro aparte para visualizar). Las ultimas len(lote) muestras de flujo.sig son las de este lote."""
    n = len(lote)
    if n == 0:
        return
    sig = detector.motor.flujo.sig
    if sig.shape[1] < n:
        return
    filt = sig[:, -n:]
    with zmq_lock:
        for i in range(n):
            zmq_socket.send_json({
                "tipo": "EEG",
                "TP9_raw": float(lote[i][0]),  "TP9_filtrado": float(filt[0, i]),
                "FP1_raw": float(lote[i][1]),  "FP1_filtrado": float(filt[1, i]),
                "FP2_raw": float(lote[i][2]),  "FP2_filtrado": float(filt[2, i]),
                "TP10_raw": float(lote[i][3]), "TP10_filtrado": float(filt[3, i])})

def calcular_estado(hubo_datos, cortado, invertida, sin_contacto_s, verificando, calibrando, activacion):
    """Estado que se informa a la Pico (texto). De mayor a menor prioridad:
    INICIO (todavia no llegaron datos) > EMERGENCIA (se cortó el flujo, vincha al revés o falla de 2 FP o de 2 TP por mas de 5 s)
    > CALIBRACION (verificando el eje Y o calibrando el filtro) > MARCHA (activacion en 1) > REPOSO."""
    if not hubo_datos:
        return "INICIO"
    if cortado or invertida or sin_contacto_s >= config.SIN_CONTACTO_EMERGENCIA_S:
        return "EMERGENCIA"
    if verificando or calibrando:
        return "CALIBRACION"
    return "MARCHA" if activacion else "REPOSO"

def motivo_emergencia(cortado, invertida, sin_contacto_s):
    """Por qué el estado es EMERGENCIA (solo para publicar y diagnosticar)."""
    if cortado:
        return "FLUJO CORTADO"
    if invertida:
        return "VINCHA AL REVES"
    if sin_contacto_s >= config.SIN_CONTACTO_EMERGENCIA_S:
        return "FALLA DE CONTACTO (2 FP o 2 TP)"
    return ""

def apagar_deteccion():
    """Al entrar en EMERGENCIA la activacion mantenida se apaga: al salir de la emergencia no vuelve sola, hace falta un gesto nuevo."""
    estado_det["activacion"] = 0
    if USA_ADAPTATIVO and not estado_det["fallo"]:
        try:
            cola_det.put_nowait("APAGAR")
        except queue.Full:
            pass

def reiniciar_deteccion():
    """Pone a cero la activación y reinicia el detector (una sola vez por desconexión)."""
    reiniciar_activacion()
    if USA_ADAPTATIVO and not estado_det["fallo"] and not estado_det["limpio"]:
        estado_det["limpio"] = True
        estado_det["activacion"] = 0
        estado_det["calibrando"] = True
        try:
            cola_det.put_nowait(None)
        except queue.Full:
            pass

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

def procesar_frente(address, *args):
    cliente_espejo.send_message(address, args)
    with buffer_lock: buffer_eventos.append({"tipo": "FRENTE", "valor": int(args[0])})

def procesar_resto(address, *args):
    cliente_espejo.send_message(address, args)
    with buffer_lock: buffer_eventos.append({"tipo": "OTRO", "address": address, "args": list(args)})

# ==========================================
# 4. HILO PROCESADOR 
# ==========================================
def hilo_procesador_bci():
    global contador_submuestreo
    
    contacto_tp9 = 4
    contacto_fp1 = 4
    contacto_fp2 = 4
    contacto_tp10 = 4
    
    # --- WATCHDOG DE LA VINCHA ---
    tiempo_ultimo_dato = time.time()
    activacion = 0
    ultimo_envio = time.time()    # para medir cada cuánto se manda la trama por UART (debería ser ~50 ms)
    dt_envio_ms = 0
    estado_anterior = "INICIO"
    hubo_datos = False        # llegó algún dato de la vincha desde que arrancó (para distinguir INICIO de un corte)
    sin_cont_desde = None     # desde cuándo fallan los dos FP o los dos TP (contacto > config.CONTACTO_FALLA)
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
            hubo_datos = True
        
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
                # el detector adaptativo no detecta si los dos frontales (FP1, FP2) estan sin contacto (vincha sacada)
                estado_det["contacto"] = {"TP9": contacto_tp9, "FP1": contacto_fp1, "FP2": contacto_fp2, "TP10": contacto_tp10}
            elif evento["tipo"] == "FRENTE":
                estado_det["frente"] = evento["valor"]

        # Flag de activación (EEG filtrado 1.5-30 Hz, ventana deslizante)
        if USA_ADAPTATIVO and not estado_det["fallo"]:
            if lote_eeg:
                estado_det["limpio"] = False
                lote_det = []
                for muestra in lote_eeg:              # 1 de cada SUBMUESTREO_DETECTOR muestras, contando entre lotes
                    if contador_submuestreo == 0:
                        lote_det.append(muestra)
                    contador_submuestreo = (contador_submuestreo + 1) % config.SUBMUESTREO_DETECTOR
                if lote_det:
                    try:
                        cola_det.put_nowait(lote_det)
                    except queue.Full:
                        pass
            activacion = estado_det["activacion"]
        elif lote_eeg:
            activacion = actualizar_activacion(list(zip(*lote_eeg)))

        # --- LOGICA DE CORTE POR DESCONEXION ---
        if (time.time() - tiempo_ultimo_dato) > TIMEOUT_VINCHA:
            activacion = 0
            reiniciar_deteccion()
            pwm1 = 0
            pwm2 = 0
            contacto_tp9 = 4
            contacto_fp1 = 4
            contacto_fp2 = 4
            contacto_tp10 = 4
            estado_det["contacto"] = {"TP9": 4, "FP1": 4, "FP2": 4, "TP10": 4}
            if "frente" in estado_det:
                estado_det["frente"] = 0
            control_motores.forzar_parada()  # Limpia la inercia acumulada

        # --- ESTADO (texto) PARA LA PICO ---
        cortado = (time.time() - tiempo_ultimo_dato) > TIMEOUT_VINCHA
        falla = config.CONTACTO_FALLA
        falla_par = (contacto_fp1 > falla and contacto_fp2 > falla) or (contacto_tp9 > falla and contacto_tp10 > falla)
        if falla_par and hubo_datos and not cortado:
            if sin_cont_desde is None:
                sin_cont_desde = time.time()
        else:
            sin_cont_desde = None
        sin_contacto_s = (time.time() - sin_cont_desde) if sin_cont_desde is not None else 0.0
        estado = calcular_estado(hubo_datos, cortado, control_motores.invertida, sin_contacto_s,
                                 control_motores.verificando, estado_det["calibrando"] and USA_ADAPTATIVO and not estado_det["fallo"],
                                 activacion)
        if estado == "EMERGENCIA" and estado_anterior != "EMERGENCIA":
            apagar_deteccion()
        estado_anterior = estado
        if estado == "EMERGENCIA":             # en emergencia los motores quedan quietos y sin activación hacia la Pico
            pwm1 = 0
            pwm2 = 0
            activacion = 0

        # ---------------------------------------------------------
        # ENVÍO 1: UART HACIA LA PICO (Físico)
        # ---------------------------------------------------------
        ahora_envio = time.time()
        dt_envio_ms = int((ahora_envio - ultimo_envio) * 1000)
        ultimo_envio = ahora_envio
        trama_enviada, trama_ok = pico_serial.enviar_estado(
            contacto_tp9, contacto_fp1, contacto_fp2, contacto_tp10, 
            pwm1, pwm2, activacion, estado
        )
        # Lo que realmente se manda a la Pico, publicado por ZMQ para compararlo con lo que ve la Pico (ver filtro/ver_deteccion.py)
        with zmq_lock:
            zmq_socket.send_json({"tipo": "TRAMA_PICO", "trama": trama_enviada, "ok": trama_ok, "errores": pico_serial.errores,
                                  "dt_ms": dt_envio_ms, "estado": estado, "activacion": activacion, "pwm1": pwm1, "pwm2": pwm2,
                                  "motivo": motivo_emergencia(cortado, control_motores.invertida, sin_contacto_s) if estado == "EMERGENCIA" else ""})
        
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
                "estado": estado,
                "dt_uart_ms": dt_envio_ms,      # tiempo desde la trama anterior hacia la Pico: si pasa de ~300 ms la Pico puede mostrar SIN ENLACE
                "zona_x": config.ZONA_MUERTA_X,
                "zona_z": config.ZONA_MUERTA_Z
            }
            with zmq_lock:
                zmq_socket.send_json(paquete_debug_motor)
                
        # 3. EEG crudo sin detector adaptativo (si el detector no arranca, se publica solo el crudo)
        if lote_eeg and not (USA_ADAPTATIVO and not estado_det["fallo"]):
            with zmq_lock:
                for m in lote_eeg:
                    zmq_socket.send_json({"tipo": "EEG", "TP9_raw": float(m[0]), "FP1_raw": float(m[1]),
                                          "FP2_raw": float(m[2]), "TP10_raw": float(m[3])})


# ==========================================
# 5. ENRUTADOR Y SERVIDOR
# ==========================================
disp = dispatcher.Dispatcher()
disp.map("/muse/eeg", procesar_eeg)
disp.map("/muse/acc", procesar_acc)
disp.map("/muse/elements/blink", procesar_parpadeo)
disp.map("/muse/elements/jaw_clench", procesar_mandibula)
disp.map("/muse/elements/horseshoe", procesar_herradura)
disp.map("/muse/elements/touching_forehead", procesar_frente)     # contacto general de la vincha (1 = toca la frente)
disp.set_default_handler(procesar_resto)

server = osc_server.BlockingOSCUDPServer(("127.0.0.1", 5000), disp)

print("===================================================")
print(" Nodo Edge BCI - RASPBERRY PI MODULAR")
print(f" ZMQ y UDP Espejo Activados")
print("===================================================")

if USA_ADAPTATIVO:
    threading.Thread(target=hilo_detector, daemon=True).start()
threading.Thread(target=hilo_procesador_bci, daemon=True).start()

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\nApagando sistema...")
    pico_serial.cerrar()
    zmq_socket.close()
    context.term()