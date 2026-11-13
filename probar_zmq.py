"""Diagnostico rapido: se conecta a la Pi por ZeroMQ y muestra, cada 2 s, cuantos mensajes de cada tipo llegaron.
- Si no llega NADA: problema de red o stream.py no esta publicando (IP, puerto, firewall, stream caido).
- Si llegan TRAMA_PICO / ESTADO pero no EEG_LOTE: stream.py anda, pero no le llegan datos de la vincha (muse-io / Bluetooth).
Ctrl+C para salir."""
import collections, sys, time, zmq
IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.50"
ctx = zmq.Context(); s = ctx.socket(zmq.SUB)
s.connect(f"tcp://{IP}:5555"); s.setsockopt_string(zmq.SUBSCRIBE, "")
p = zmq.Poller(); p.register(s, zmq.POLLIN)
print(f"Escuchando tcp://{IP}:5555 ...")
c = collections.Counter(); t = time.time(); total = 0
try:
    while True:
        if dict(p.poll(200)).get(s):
            try: c[s.recv_json().get("tipo", "?")] += 1; total += 1
            except Exception as e: c["ERROR_JSON"] += 1
        if time.time() - t >= 2:
            print(time.strftime("%H:%M:%S"), dict(c) if c else "NADA en 2 s", "| total", total); c.clear(); t = time.time()
except KeyboardInterrupt:
    pass
