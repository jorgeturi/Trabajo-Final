"""
Graba en la PC lo que publica la Raspberry Pi por ZeroMQ (stream.py, puerto 5555), con la marca manual de la barra
espaciadora, para analizar despues por que fallo (o no) la deteccion en cada ventana.

Genera 4 archivos con el mismo prefijo:
  <prefijo>_eeg220.csv        EEG crudo a 220 Hz (EEG_LOTE), mismas columnas que grabar_datos_crudos_ram.py
                              + Activacion, Estado, PWM1, PWM2 (lo que la Pi le manda a la Pico en ese momento)
  <prefijo>_eeg110.csv        EEG que analiza el detector (EEG_DET, 110 Hz): crudo submuestreado y filtrado 8-30 Hz
  <prefijo>_detecciones.csv   una fila por ventana del detector (DETECCION, cada 0,25 s): det, via, freno, margenes,
                              ratios, canales ignorados, activacion, tiempo de calculo y cola
  <prefijo>_todo.jsonl        TODOS los mensajes tal cual llegan (ESTADO, TRAMA_PICO, DEBUG_MOTOR, ACC, ...), con la
                              hora de llegada y el trigger, por si hace falta algo que no esta en los CSV

Uso: correr stream.py en la Pi, ejecutar este script en la PC, mantener apretada la BARRA ESPACIADORA mientras se hace
el gesto y soltarla al terminar. ESC termina y guarda.
"""
import csv
import json
import sys
import threading
import time

import keyboard
import zmq

# ==========================================
# CONFIGURACION
# ==========================================
IP_RASPBERRY = "192.168.1.50"
PUERTO_ZMQ = 5555
nombre_movimiento = "gestos suaves cejas 11-11"

CANALES = ['TP9', 'FP1', 'FP2', 'TP10']
prefijo = f"dataset_pi_{nombre_movimiento.replace(' ', '_')}_{int(time.time())}"

# ==========================================
# ESTADO COMPARTIDO (lo actualiza el hilo ZMQ, el trigger lo actualiza el hilo principal)
# ==========================================
estado_trigger_global = 0
horseshoe_status = [4, 4, 4, 4]
frente_status = -1
acc_status = [0.0, 0.0, 0.0]
blink_pendiente = 0
jaw_pendiente = 0
ultima_trama = {"activacion": 0, "estado": "", "pwm1": 0, "pwm2": 0}
contadores = {"EEG_LOTE": 0, "EEG_DET": 0, "DETECCION": 0, "otros": 0}
ultima_det = {}

filas_220, filas_110, filas_det, lineas_json = [], [], [], []
buffer_lock = threading.Lock()
corriendo = True

CAB_220 = (['Timestamp', 'Trigger', 'Sensores_OK'] + CANALES + ['HS_' + c for c in CANALES]
           + ['Frente', 'ACC_X', 'ACC_Y', 'ACC_Z', 'Blink', 'Jaw', 'Activacion', 'Estado', 'PWM1', 'PWM2'])
CAB_110 = (['Timestamp', 'Trigger'] + [c + '_raw' for c in CANALES] + [c + '_filtrado' for c in CANALES]
           + ['Activacion', 'Estado'])
CAB_DET = (['Timestamp', 'Trigger', 't_det', 'activacion', 'pulso', 'det', 'gesto_nuevo', 'n_gestos', 'calibrando',
            'previo', 'via', 'freno'] + ['margen_' + c for c in CANALES] + ['ratio_' + c for c in CANALES]
           + ['ignorado_' + c for c in CANALES] + ['sin_contacto_FP', 'sin_contacto_TP', 'ms', 'cola'])


def tiempos_lote(t_llegada, n, fs):
    """Reparte la hora de llegada del lote entre sus n muestras (la ultima muestra = hora de llegada)."""
    return [t_llegada - (n - 1 - i) / fs for i in range(n)]


def procesar(msg, t):
    global horseshoe_status, frente_status, acc_status, blink_pendiente, jaw_pendiente
    tipo = msg.get("tipo", "")
    trig = estado_trigger_global
    lineas_json.append(json.dumps({"t_pc": round(t, 4), "trigger": trig, **msg}, ensure_ascii=False))

    if tipo == "HORSESHOE":
        horseshoe_status = [msg.get(c, 4) for c in CANALES]
    elif tipo == "FRENTE":
        frente_status = int(msg.get("valor", -1))
    elif tipo == "ACC":
        acc_status = [msg.get("x", 0.0), msg.get("y", 0.0), msg.get("z", 0.0)]
    elif tipo == "BLINK":
        if int(msg.get("valor", 0)):
            blink_pendiente = 1
    elif tipo == "JAW_CLENCH":
        if int(msg.get("valor", 0)):
            jaw_pendiente = 1
    elif tipo in ("TRAMA_PICO", "DEBUG_MOTOR"):
        for k in ("activacion", "estado", "pwm1", "pwm2"):
            if k in msg:
                ultima_trama[k] = msg[k]
    elif tipo == "ESTADO":
        ultima_trama["estado"] = msg.get("estado", ultima_trama["estado"])

    if tipo == "EEG_LOTE":
        fs = float(msg.get("fs", 220.0))
        datos = [msg[c] for c in CANALES]
        n = len(datos[0])
        sensores_ok = 1 if all(h <= 2 for h in horseshoe_status) else 0
        for i, ts in enumerate(tiempos_lote(t, n, fs)):
            filas_220.append([ts, trig, sensores_ok] + [datos[k][i] for k in range(4)] + list(horseshoe_status)
                             + [frente_status] + list(acc_status) + [blink_pendiente, jaw_pendiente]
                             + [ultima_trama["activacion"], ultima_trama["estado"], ultima_trama["pwm1"], ultima_trama["pwm2"]])
            blink_pendiente = 0
            jaw_pendiente = 0
        contadores["EEG_LOTE"] += 1
    elif tipo == "EEG_DET":
        fs = float(msg.get("fs", 110.0))
        raw = [msg[c + "_raw"] for c in CANALES]
        fil = [msg[c + "_filtrado"] for c in CANALES]
        n = min(len(raw[0]), len(fil[0]))
        for i, ts in enumerate(tiempos_lote(t, n, fs)):
            filas_110.append([ts, trig] + [raw[k][i] for k in range(4)] + [fil[k][i] for k in range(4)]
                             + [ultima_trama["activacion"], ultima_trama["estado"]])
        contadores["EEG_DET"] += 1
    elif tipo == "DETECCION":
        m, r, ig = msg.get("margen", {}), msg.get("ratios", {}), msg.get("ignorado", {})
        sc = msg.get("senal_sin_contacto", {})
        filas_det.append([t, trig, msg.get("t"), msg.get("activacion"), msg.get("pulso"), int(bool(msg.get("det"))),
                          int(bool(msg.get("gesto_nuevo"))), msg.get("n_gestos"), int(bool(msg.get("calibrando"))),
                          msg.get("previo"), msg.get("via"), msg.get("freno")]
                         + [m.get(c, "") for c in CANALES] + [r.get(c, "") for c in CANALES]
                         + [int(bool(ig.get(c, False))) for c in CANALES]
                         + [int(bool(sc.get("FP", True))), int(bool(sc.get("TP", True))), msg.get("ms"), msg.get("cola")])
        ultima_det.update(msg)
        contadores["DETECCION"] += 1
    else:
        contadores["otros"] += 1


def hilo_zmq():
    context = zmq.Context()
    sock = context.socket(zmq.SUB)
    sock.connect(f"tcp://{IP_RASPBERRY}:{PUERTO_ZMQ}")
    sock.setsockopt_string(zmq.SUBSCRIBE, "")
    poller = zmq.Poller()
    poller.register(sock, zmq.POLLIN)
    while corriendo:
        if dict(poller.poll(100)).get(sock):
            try:
                msg = sock.recv_json()
            except Exception:
                continue
            t = time.time()
            with buffer_lock:
                procesar(msg, t)
    sock.close()
    context.term()


def vaciar(writers, f_json):
    with buffer_lock:
        l220, l110, ldet, ljs = filas_220[:], filas_110[:], filas_det[:], lineas_json[:]
        filas_220.clear(); filas_110.clear(); filas_det.clear(); lineas_json.clear()
    writers[0].writerows(l220)
    writers[1].writerows(l110)
    writers[2].writerows(ldet)
    if ljs:
        f_json.write("\n".join(ljs) + "\n")


if __name__ == "__main__":
    archivos = [open(f"{prefijo}_{s}.csv", "w", newline="", encoding="utf-8") for s in ("eeg220", "eeg110", "detecciones")]
    writers = [csv.writer(f) for f in archivos]
    for w, cab in zip(writers, (CAB_220, CAB_110, CAB_DET)):
        w.writerow(cab)
    f_json = open(f"{prefijo}_todo.jsonl", "w", encoding="utf-8")

    threading.Thread(target=hilo_zmq, daemon=True).start()

    print("==================================================")
    print("Logger desde la Raspberry Pi (ZeroMQ)")
    print(f"Conectado a {IP_RASPBERRY}:{PUERTO_ZMQ}")
    print(f"Movimiento: {nombre_movimiento.upper()}")
    print(f"Archivos: {prefijo}_*.csv / _todo.jsonl")
    print("MANTENER PRESIONADA LA BARRA ESPACIADORA mientras se hace el gesto. ESC para terminar.")
    print("==================================================\n")

    try:
        while True:
            estado_trigger_global = 1 if keyboard.is_pressed('space') else 0
            if keyboard.is_pressed('esc'):
                break
            vaciar(writers, f_json)
            vis = "GRABANDO [ESPACIO]" if estado_trigger_global else "Reposo..."
            sys.stdout.write(f"\rLotes 220: {contadores['EEG_LOTE']} | 110: {contadores['EEG_DET']} | "
                             f"Ventanas: {contadores['DETECCION']} | {vis} | Act: {ultima_trama['activacion']} | "
                             f"Estado: {ultima_trama['estado']} | Det: {int(bool(ultima_det.get('det', 0)))} "
                             f"via: {ultima_det.get('via', '')}      ")
            sys.stdout.flush()
            time.sleep(0.02)
    finally:
        corriendo = False
        time.sleep(0.3)
        vaciar(writers, f_json)
        for f in archivos:
            f.close()
        f_json.close()
        print(f"\n\nGrabacion terminada. Lotes EEG 220 Hz: {contadores['EEG_LOTE']}, lotes 110 Hz: {contadores['EEG_DET']}, "
              f"ventanas del detector: {contadores['DETECCION']}.")
        if contadores["EEG_LOTE"] == 0:
            print("No llego ningun dato: revisar que stream.py este corriendo y la IP de la Raspberry Pi.")
