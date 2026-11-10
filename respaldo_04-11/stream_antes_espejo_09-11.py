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
# Espejo UDP: copia sin procesar de cada mensaje OSC hacia la PC (opcional, se activa en config.py)
cliente_espejo = udp_client.SimpleUDPClient(config.ESPEJO_IP, config.ESPEJO_PUERTO) if config.ESPEJO_UDP else None

def espejo(address, args):
    if cliente_espejo is not None:
        cliente_espejo.send_message(address, args)

context = zmq.Context()
zmq_socket = context.socket(zmq.PUB)
zmq_socket.bind("tcp://0.0.0.0:5555")
zmq_lock = threading.Lock()

buffer_eeg = []
buffer_eventos = []
buffer_lock = threading.Lock()
buffer_t0 = [None]       # hora (monotonic) en que llego el primer dato del lote actual: punto de partida de la latencia

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
CANALES = ("TP9", "FP1", "FP2", "TP10")
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
estado_det = {"activacion": 0, "limpio": True, "fallo": False, "calibrando": True,
              "senal_sin_contacto": {"FP": True, "TP": True}}   # lo actualiza el detector; True = no confirma nada (decide solo el horseshoe)
acc_det = deque(maxlen=2000)     # (monotonic, x, y, z) del acelerometro para el veto por movimiento de cabeza del detector

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
                estado_det["senal_sin_contacto"] = {"FP": True, "TP": True}
                estado_det["activacion"] = 0
                estado_det["calibrando"] = True
                continue
            cont = estado_det.get("contacto")
            if cont and hasattr(detector, "contacto"):       # (un detector_gestos.py viejo no lo tiene)
                detector.contacto(cont)
            if "frente" in estado_det and hasattr(detector, "frente"):
                detector.frente(estado_det["frente"])
            while acc_det:                   # ACC llegado desde el lote anterior (mismo reloj monotonic que usa el detector)
                t_a, x_a, y_a, z_a = acc_det.popleft()
                if hasattr(detector, "acc"):
                    detector.acc(x_a, y_a, z_a, t_a)
            t0 = time.perf_counter()
            paquetes = detector.procesar(lote)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            publicar_eeg_detector(lote, detector)
            for paquete in paquetes:
                paquete["ms"] = ms                   # cuanto tardo decidir (si se acerca a 250 ms no llega en tiempo real)
                paquete["cola"] = cola_det.qsize()   # lotes esperando: si crece, el detector se atrasa
                estado_det["activacion"] = paquete["activacion"]
                estado_det["calibrando"] = paquete["calibrando"]
                if "senal_sin_contacto" in paquete:
                    estado_det["senal_sin_contacto"] = paquete["senal_sin_contacto"]
                with zmq_lock:
                    zmq_socket.send_json(paquete)
        except Exception:
            traceback.print_exc()            # el hilo sigue vivo aunque falle una ventana

def publicar_eeg_detector(lote, detector):
    """Publica por ZMQ, en UN mensaje por lote (EEG_DET), el EEG que analiza el detector: crudo (submuestreado a 110 Hz) y
    filtrado por SU pasabanda de 8-30 Hz (no hay un filtro aparte). Las ultimas len(lote) muestras de flujo.sig son las de este lote."""
    n = len(lote)
    if n == 0:
        return
    sig = detector.motor.flujo.sig
    if sig.shape[1] < n:
        return
    filt = sig[:, -n:]
    paquete = {"tipo": "EEG_DET", "fs": fs_det}
    for k, c in enumerate(CANALES):
        paquete[c + "_raw"] = [float(m[k]) for m in lote]
        paquete[c + "_filtrado"] = [round(float(v), 3) for v in filt[k]]
    with zmq_lock:
        zmq_socket.send_json(paquete)

def calcular_estado(hubo_datos, cortado, invertida, falla_contacto, verificando, calibrando, activacion, limitada=False):
    """Estado que se informa a la Pico (texto). De mayor a menor prioridad:
    INICIO (todavia no llegaron datos) > EMERGENCIA (se cortó el flujo, vincha al revés, falla de 2 FP por 0,7 s o de 2 TP por 5 s)
    > CALIBRACION (verificando el eje Y o calibrando el filtro) > MARCHA LIMITADA (activacion en 1 con 1 FP y 1 TP en falla 5 s)
    > MARCHA (activacion en 1) > REPOSO."""
    if not hubo_datos:
        return "INICIO"
    if cortado or invertida or falla_contacto:
        return "EMERGENCIA"
    if verificando or calibrando:
        return "CALIBRACION"
    if activacion:
        return "MARCHA LIMITADA" if limitada else "MARCHA"
    return "REPOSO"

def motivo_emergencia(cortado, invertida, falla_contacto):
    """Por qué el estado es EMERGENCIA (solo para publicar y diagnosticar)."""
    if cortado:
        return "FLUJO CORTADO"
    if invertida:
        return "VINCHA AL REVES"
    if falla_contacto:
        return falla_contacto
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
# 2b. HILO DE ENVIO UART (ritmo fijo, independiente del procesamiento)
# ==========================================
# El hilo procesador solo deja aca la ultima trama armada; este hilo la manda cada PERIODO_ENVIO_S exactos (reloj monotonic,
# con hora fija para la proxima, no un sleep despues del trabajo). No publica por ZMQ ni toma zmq_lock: lo unico que hace es escribir.
# Falla segura: si el procesador no renueva la trama en TRAMA_VIEJA_S, se deja de enviar y la Pico corta a los 500 ms como antes.
PERIODO_ENVIO_S = 0.05
TRAMA_VIEJA_S = 0.5
trama_lock = threading.Lock()
trama_actual = {"args": None, "t": 0.0, "t0": None}   # args de pico_serial.enviar_estado, cuando los dejo el procesador y llegada del lote (monotonic)
med_envio = {"trama": "", "ok": False, "dt_max_ms": 0, "retraso_max_ms": 0, "escritura_max_ms": 0.0, "viejas": 0, "latencia_uart_max_ms": 0.0}

def hilo_envio_uart():
    prox = time.monotonic()
    ultimo = None
    while True:
        prox += PERIODO_ENVIO_S
        espera = prox - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        ahora = time.monotonic()
        retraso_ms = int((ahora - prox) * 1000)       # cuanto se desperto tarde respecto de la hora prevista
        if retraso_ms > PERIODO_ENVIO_S * 1000:       # si se atraso mas de un periodo no se intenta "recuperar" mandando rafagas
            prox = ahora
        with trama_lock:
            args, t_trama, t0_lote = trama_actual["args"], trama_actual["t"], trama_actual["t0"]
        if args is None:
            continue
        if ahora - t_trama > TRAMA_VIEJA_S:           # el procesador esta colgado: no se reenvian PWM viejos
            with trama_lock:
                med_envio["viejas"] += 1
            continue
        t0 = time.monotonic()
        trama, ok = pico_serial.enviar_estado(*args)
        fin = time.monotonic()
        dt_ms = int((fin - ultimo) * 1000) if ultimo is not None else 0
        ultimo = fin
        with trama_lock:                              # maximos desde la ultima publicacion (el procesador los lee y los pone a 0)
            med_envio["trama"], med_envio["ok"] = trama, ok
            med_envio["dt_max_ms"] = max(med_envio["dt_max_ms"], dt_ms)
            med_envio["retraso_max_ms"] = max(med_envio["retraso_max_ms"], retraso_ms)
            med_envio["escritura_max_ms"] = max(med_envio["escritura_max_ms"], round((fin - t0) * 1000, 1))
            if t0_lote is not None:                   # latencia total en la Pi: llegada del dato -> trama escrita en la UART
                med_envio["latencia_uart_max_ms"] = max(med_envio["latencia_uart_max_ms"], round((fin - t0_lote) * 1000, 1))

def leer_med_envio():
    """Copia de las mediciones del hilo de envio y reinicio de los maximos."""
    with trama_lock:
        m = dict(med_envio)
        med_envio["dt_max_ms"] = 0
        med_envio["retraso_max_ms"] = 0
        med_envio["escritura_max_ms"] = 0.0
        med_envio["latencia_uart_max_ms"] = 0.0
    return m

# ==========================================
# 3. HANDLERS OSC (Mismos que antes)
# ==========================================
def marcar_llegada():
    """Guarda la hora de llegada del primer dato del lote (se llama con buffer_lock tomado)."""
    if buffer_t0[0] is None:
        buffer_t0[0] = time.monotonic()

def procesar_eeg(address, *args):
    espejo(address, args)
    with buffer_lock:
        marcar_llegada()
        buffer_eeg.append(args[:4])

def procesar_acc(address, *args):
    espejo(address, args)
    with buffer_lock:
        marcar_llegada()
        buffer_eventos.append({"tipo": "ACC", "x": float(args[0]), "y": float(args[1]), "z": float(args[2])})
    if USA_ADAPTATIVO:
        acc_det.append((time.monotonic(), float(args[0]), float(args[1]), float(args[2])))

def procesar_parpadeo(address, *args):
    espejo(address, args)
    with buffer_lock: buffer_eventos.append({"tipo": "BLINK", "valor": int(args[0])})

def procesar_mandibula(address, *args):
    espejo(address, args)
    with buffer_lock: buffer_eventos.append({"tipo": "JAW_CLENCH", "valor": int(args[0])})

def procesar_herradura(address, *args):
    espejo(address, args)
    with buffer_lock: buffer_eventos.append({"tipo": "HORSESHOE", "TP9": int(args[0]), "FP1": int(args[1]), "FP2": int(args[2]), "TP10": int(args[3])})

def procesar_frente(address, *args):
    espejo(address, args)
    with buffer_lock: buffer_eventos.append({"tipo": "FRENTE", "valor": int(args[0])})

def procesar_resto(address, *args):
    espejo(address, args)
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
    dt_envio_ms = 0               # maximo tiempo entre tramas hacia la Pico (lo mide hilo_envio_uart; deberia ser ~50 ms)
    trabajo_ms = 0.0              # cuanto tardo la vuelta anterior del lazo sin contar el sleep
    estado_anterior = "INICIO"
    hubo_datos = False        # llegó algún dato de la vincha desde que arrancó (para distinguir INICIO de un corte)
    fp_falla_desde = None     # desde cuándo fallan los dos FP (contacto > config.CONTACTO_FALLA y no toca la frente; la señal se exige al final)
    tp_falla_desde = None     # desde cuándo fallan los dos TP
    fp_confirmada = False     # la EMERGENCIA por 2 FP ya se declaro: se mantiene mientras sigan el horseshoe y la frente en falla
    lim_desde = None          # desde cuándo falla al menos 1 FP y al menos 1 TP (para MARCHA LIMITADA)
    TIMEOUT_VINCHA = 0.5  # Segundos sin recibir datos antes de cortar motores
    
    while True:
        t_antes = time.monotonic()
        time.sleep(0.05)
        t_vuelta = time.monotonic()
        sleep_ms = int((t_vuelta - t_antes) * 1000)   # deberia ser ~50: si da mucho mas, el proceso o la Pi estuvieron frenados
        
        with buffer_lock:
            lote_eeg = buffer_eeg.copy()
            lote_eventos = buffer_eventos.copy()
            buffer_eeg.clear()
            buffer_eventos.clear()
            t0_lote = buffer_t0[0]
            buffer_t0[0] = None
            
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
        # Falla de un par segun el horseshoe, confirmada por la señal que mide el detector adaptativo (el horseshoe a veces se traba en 4
        # con la vincha bien puesta). Sin detector adaptativo decide solo el horseshoe, como antes.
        conf = estado_det["senal_sin_contacto"] if (USA_ADAPTATIVO and not estado_det["fallo"]) else {"FP": True, "TP": True}
        ahora = time.time()
        vigila = hubo_datos and not cortado
        # touching_forehead: el gesto no lo mueve y la vincha sacada lo pone en 0. Se exige ademas del horseshoe y la señal.
        # Sin dato de frente (nunca llego) no bloquea la emergencia.
        no_toca_frente = estado_det.get("frente") != 1
        # FP: los SIN_CONTACTO_FP_EMERGENCIA_S cuentan desde que fallan el horseshoe y la frente; la señal solo tiene que confirmarlo
        # al final (en paralelo, no en serie): el peor caso es el mayor de los dos tiempos (~0,75 s) y no la suma (09-11)
        falla_fp = vigila and contacto_fp1 > falla and contacto_fp2 > falla and no_toca_frente
        falla_tp = vigila and contacto_tp9 > falla and contacto_tp10 > falla and conf.get("TP", True)
        fp_falla_desde = (fp_falla_desde or ahora) if falla_fp else None
        tp_falla_desde = (tp_falla_desde or ahora) if falla_tp else None
        falla_contacto = ""
        # La señal solo hace falta para ENTRAR: con la vincha quieta sobre la mesa la señal se calma y la emergencia se cortaba sola (09-11)
        if fp_falla_desde is None:
            fp_confirmada = False
        elif ahora - fp_falla_desde >= config.SIN_CONTACTO_FP_EMERGENCIA_S and (fp_confirmada or conf.get("FP", True)):
            fp_confirmada = True
        if fp_confirmada:
            falla_contacto = "FALLA DE CONTACTO (2 FP)"
        elif tp_falla_desde is not None and ahora - tp_falla_desde >= config.SIN_CONTACTO_EMERGENCIA_S:
            falla_contacto = "FALLA DE CONTACTO (2 TP)"
        # MARCHA LIMITADA: al menos 1 FP y al menos 1 TP en falla (horseshoe) durante LIMITADA_FALLA_S seguidos
        falla_1y1 = vigila and (contacto_fp1 > falla or contacto_fp2 > falla) and (contacto_tp9 > falla or contacto_tp10 > falla)
        lim_desde = (lim_desde or ahora) if falla_1y1 else None
        limitada = lim_desde is not None and ahora - lim_desde >= config.LIMITADA_FALLA_S
        estado = calcular_estado(hubo_datos, cortado, control_motores.invertida, falla_contacto,
                                 control_motores.verificando, estado_det["calibrando"] and USA_ADAPTATIVO and not estado_det["fallo"],
                                 activacion, limitada)
        if estado == "EMERGENCIA" and estado_anterior != "EMERGENCIA":
            apagar_deteccion()
        if estado != estado_anterior:          # mensaje ESTADO en cada cambio (el suscriptor sabe en que estado esta la Pi)
            with zmq_lock:
                zmq_socket.send_json({"tipo": "ESTADO", "estado": estado, "anterior": estado_anterior,
                                      "motivo": motivo_emergencia(cortado, control_motores.invertida, falla_contacto) if estado == "EMERGENCIA" else ""})
        estado_anterior = estado
        if estado == "MARCHA LIMITADA":        # potencia limitada al LIMITADA_FACTOR (70 %) del maximo normal
            pwm1 = int(round(pwm1 * config.LIMITADA_FACTOR))
            pwm2 = int(round(pwm2 * config.LIMITADA_FACTOR))
        if estado == "EMERGENCIA":             # en emergencia los motores quedan quietos y sin activación hacia la Pico
            pwm1 = 0
            pwm2 = 0
            activacion = 0

        # ---------------------------------------------------------
        # ENVÍO 1: UART HACIA LA PICO (Físico)
        # ---------------------------------------------------------
        # El envio lo hace hilo_envio_uart a ritmo fijo: aca solo se deja la trama nueva
        with trama_lock:
            trama_actual["args"] = (contacto_tp9, contacto_fp1, contacto_fp2, contacto_tp10, pwm1, pwm2, activacion, estado)
            trama_actual["t"] = time.monotonic()
            if t0_lote is not None:
                trama_actual["t0"] = t0_lote
        latencia_ms = round((time.monotonic() - t0_lote) * 1000, 1) if t0_lote is not None else None   # llegada del dato -> trama lista
        med = leer_med_envio()
        dt_envio_ms = med["dt_max_ms"]
        # Lo que realmente se manda a la Pico, publicado por ZMQ para compararlo con lo que ve la Pico (ver filtro/ver_deteccion.py)
        # Medicion: dt_ms = mayor hueco entre tramas desde la publicacion anterior; retraso_ms / escritura_ms = del hilo de envio;
        # sleep_ms / trabajo_ms = del lazo procesador; cola = lotes que esperan al detector.
        with zmq_lock:
            zmq_socket.send_json({"tipo": "TRAMA_PICO", "trama": med["trama"], "ok": med["ok"], "errores": pico_serial.errores,
                                  "dt_ms": dt_envio_ms, "estado": estado, "activacion": activacion, "pwm1": pwm1, "pwm2": pwm2,
                                  "retraso_ms": med["retraso_max_ms"], "escritura_ms": med["escritura_max_ms"], "viejas": med["viejas"],
                                  "sleep_ms": sleep_ms, "trabajo_ms": trabajo_ms, "cola": cola_det.qsize(),
                                  "latencia_uart_ms": med["latencia_uart_max_ms"],
                                  "motivo": motivo_emergencia(cortado, control_motores.invertida, falla_contacto) if estado == "EMERGENCIA" else ""})
        
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
                "latencia_ms": latencia_ms,     # llegada del primer dato del lote -> trama lista para el hilo de envio (REQ-NF01)
                "dt_uart_ms": dt_envio_ms,      # tiempo desde la trama anterior hacia la Pico: si pasa de ~300 ms la Pico puede mostrar SIN ENLACE
                "zona_x": config.ZONA_MUERTA_X,
                "zona_z": config.ZONA_MUERTA_Z
            }
            with zmq_lock:
                zmq_socket.send_json(paquete_debug_motor)
                
        # 3. EEG crudo de la vuelta, a la tasa completa (220 Hz), en UN mensaje por lote (EEG_LOTE)
        if lote_eeg:
            paquete_eeg = {"tipo": "EEG_LOTE", "fs": fs}
            for k, c in enumerate(CANALES):
                paquete_eeg[c] = [float(m[k]) for m in lote_eeg]
            with zmq_lock:
                zmq_socket.send_json(paquete_eeg)

        trabajo_ms = round((time.monotonic() - t_vuelta) * 1000, 1)


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
threading.Thread(target=hilo_envio_uart, daemon=True).start()

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\nApagando sistema...")
    pico_serial.cerrar()
    zmq_socket.close()
    context.term()