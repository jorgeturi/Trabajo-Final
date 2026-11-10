"""Suscriptor de la PC que guarda TODO lo que publica la Pi por ZMQ, para analizar despues la sesion.

Uso:   python grabar_todo.py                 (Pi en 192.168.1.50)
       python grabar_todo.py 192.168.1.60    (otra IP)
       python grabar_todo.py 192.168.1.50 prueba_frenos   (nombre de la sesion)
Se corta con Ctrl+C.

Guarda en sesiones_pc/<fecha_hora>_<nombre>/:
  todo.jsonl        cada mensaje tal cual llego, una linea por mensaje, con la hora de la PC (t_pc)
  <TIPO>.csv        un CSV por tipo de mensaje (ESTADO, TRAMA_PICO, DEBUG_MOTOR, DETECCION, HORSESHOE, ACC, ...)
                    con las columnas aplanadas (por ejemplo margen.FP1). Los lotes de EEG (EEG_LOTE, EEG_DET)
                    se abren en una fila por muestra.
  linea_tiempo.csv  solo lo importante para saber que paso: cambios de estado (con el motivo de la Emergencia),
                    cambios de activacion y de freno del detector, tramas rechazadas, huecos de mas de 0,5 s sin mensajes.

Los mensajes se reciben en un hilo y se guardan en un buffer; otro hilo los escribe a disco cada 1 s,
asi la escritura no hace perder mensajes.
"""
import csv
import json
import os
import queue
import sys
import threading
import time

import zmq

IP_RASPBERRY = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.50"
NOMBRE = sys.argv[2] if len(sys.argv) > 2 else ""
PUERTO_ZMQ = 5555
TIPOS_LOTE = ("EEG_LOTE", "EEG_DET")      # mensajes con listas de muestras: una fila por muestra
HUECO_S = 0.5                             # sin mensajes durante esto = se anota un hueco

carpeta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sesiones_pc",
                       time.strftime("%Y-%m-%d_%H-%M-%S") + (f"_{NOMBRE}" if NOMBRE else ""))
os.makedirs(carpeta, exist_ok=True)

buffer = queue.Queue()
corriendo = threading.Event()
corriendo.set()


def aplanar(d, prefijo=""):
    """{'margen': {'FP1': 3}} -> {'margen.FP1': 3}. Las listas quedan como texto JSON."""
    plano = {}
    for k, v in d.items():
        clave = f"{prefijo}{k}"
        if isinstance(v, dict):
            plano.update(aplanar(v, clave + "."))
        elif isinstance(v, list):
            plano[clave] = json.dumps(v)
        else:
            plano[clave] = v
    return plano


class Escritor:
    """Un CSV por tipo. Las columnas salen del primer mensaje de ese tipo; si despues aparece un campo nuevo,
    va a la columna 'extra' (JSON) para no perderlo."""

    def __init__(self):
        self.archivos = {}

    def escribir(self, tipo, fila):
        if tipo not in self.archivos:
            f = open(os.path.join(carpeta, f"{tipo}.csv"), "w", newline="", encoding="utf-8")
            columnas = list(fila.keys()) + ["extra"]
            w = csv.DictWriter(f, fieldnames=columnas)
            w.writeheader()
            self.archivos[tipo] = (f, w, set(columnas))
        f, w, columnas = self.archivos[tipo]
        extra = {k: v for k, v in fila.items() if k not in columnas}
        fila = {k: v for k, v in fila.items() if k in columnas}
        if extra:
            fila["extra"] = json.dumps(extra)
        w.writerow(fila)

    def vaciar(self):
        for f, _, _ in self.archivos.values():
            f.flush()

    def cerrar(self):
        for f, _, _ in self.archivos.values():
            f.close()


def receptor():
    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, 100000)
    sock.setsockopt_string(zmq.SUBSCRIBE, "")
    sock.connect(f"tcp://{IP_RASPBERRY}:{PUERTO_ZMQ}")
    poller = zmq.Poller()
    poller.register(sock, zmq.POLLIN)
    while corriendo.is_set():
        if poller.poll(200):
            try:
                msg = sock.recv_json(zmq.NOBLOCK)
            except (zmq.Again, ValueError):
                continue
            buffer.put((time.time(), msg))
    sock.close(0)
    ctx.term()


def guardador():
    escritor = Escritor()
    f_todo = open(os.path.join(carpeta, "todo.jsonl"), "w", encoding="utf-8")
    f_lt = open(os.path.join(carpeta, "linea_tiempo.csv"), "w", newline="", encoding="utf-8")
    lt = csv.writer(f_lt)
    lt.writerow(["t_pc", "hora", "que", "detalle"])
    ult = {"activacion": None, "freno": None, "t": None}
    cuenta = {}
    t0 = None

    def anotar(t, que, detalle):
        lt.writerow([f"{t:.3f}", time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t % 1 * 1000):03d}", que, detalle])
        print(f"[{time.strftime('%H:%M:%S', time.localtime(t))}] {que}: {detalle}", flush=True)

    while corriendo.is_set() or not buffer.empty():
        hasta = time.time() + 1.0
        while time.time() < hasta:
            try:
                t, msg = buffer.get(timeout=0.2)
            except queue.Empty:
                continue
            tipo = str(msg.get("tipo", "SIN_TIPO"))
            cuenta[tipo] = cuenta.get(tipo, 0) + 1
            if t0 is None:
                t0 = t
                anotar(t, "INICIO", f"primer mensaje de la Pi ({IP_RASPBERRY})")
            if ult["t"] is not None and t - ult["t"] > HUECO_S:
                anotar(t, "HUECO", f"{(t - ult['t']) * 1000:.0f} ms sin mensajes de la Pi")
            ult["t"] = t

            f_todo.write(json.dumps({"t_pc": round(t, 4), **msg}) + "\n")

            if tipo in TIPOS_LOTE:
                listas = {k: v for k, v in msg.items() if isinstance(v, list)}
                fijos = {k: v for k, v in msg.items() if not isinstance(v, list) and k != "tipo"}
                n = max((len(v) for v in listas.values()), default=0)
                for i in range(n):
                    fila = {"t_pc": round(t, 4), "muestra": i, **fijos}
                    fila.update({k: (v[i] if i < len(v) else "") for k, v in listas.items()})
                    escritor.escribir(tipo, fila)
            else:
                escritor.escribir(tipo, {"t_pc": round(t, 4), **aplanar({k: v for k, v in msg.items() if k != "tipo"})})

            # Linea de tiempo: lo que sirve para saber por que freno o entro en Emergencia
            if tipo == "ESTADO":
                motivo = msg.get("motivo") or ""
                anotar(t, "ESTADO", f"{msg.get('anterior')} -> {msg.get('estado')}" + (f" ({motivo})" if motivo else ""))
            elif tipo == "DETECCION":
                if msg.get("activacion") != ult["activacion"]:
                    anotar(t, "ACTIVACION", f"{ult['activacion']} -> {msg.get('activacion')}")
                    ult["activacion"] = msg.get("activacion")
                freno = msg.get("freno") or ""
                if freno != ult["freno"]:
                    if freno:
                        anotar(t, "DETECTOR FRENADO", str(freno))
                    ult["freno"] = freno
            elif tipo == "TRAMA_PICO" and msg.get("ok") is False:
                anotar(t, "TRAMA MAL", str(msg.get("trama")))
        escritor.vaciar()
        f_todo.flush()
        f_lt.flush()

    escritor.cerrar()
    f_todo.close()
    f_lt.close()
    print("\nMensajes guardados por tipo:", ", ".join(f"{k}: {v}" for k, v in sorted(cuenta.items())))
    print(f"Sesion en: {carpeta}")


if __name__ == "__main__":
    print(f"Escuchando a la Pi en tcp://{IP_RASPBERRY}:{PUERTO_ZMQ}")
    print(f"Guardando en {carpeta}  (Ctrl+C para cortar)")
    hilo_r = threading.Thread(target=receptor, daemon=True)
    hilo_g = threading.Thread(target=guardador)
    hilo_r.start()
    hilo_g.start()
    try:
        while hilo_g.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nCortando, terminando de guardar lo que queda en el buffer...")
    corriendo.clear()
    hilo_r.join(timeout=2)
    hilo_g.join()
