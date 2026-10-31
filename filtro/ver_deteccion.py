"""
Escucha por ZMQ lo que publica la Raspberry y muestra los tipos de paquete que llegan y los paquetes DETECCION
(margen y pico/margen por canal). Sirve para comprobar si la Pi esta publicando la deteccion, sin pasar por el dashboard.

Uso: python filtro/ver_deteccion.py [IP_DE_LA_PI]     (por defecto 192.168.1.50)
"""
import sys
import time

import zmq

ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.50"
sock = zmq.Context().socket(zmq.SUB)
sock.connect(f"tcp://{ip}:5555")
sock.setsockopt_string(zmq.SUBSCRIBE, "")
sock.setsockopt(zmq.RCVTIMEO, 500)
print(f"Escuchando {ip}:5555 (Ctrl+C para salir)")
cuenta, t0, ultimo = {}, time.time(), 0.0
while True:
    try:
        m = sock.recv_json()
    except zmq.Again:
        m = None
    except KeyboardInterrupt:
        break
    if m:
        cuenta[m.get('tipo')] = cuenta.get(m.get('tipo'), 0) + 1
        if m.get('tipo') == 'DETECCION' and time.time() - ultimo > 0.5:
            ultimo = time.time()
            r, g = m['ratios'], m['margen']
            print(f"DETECCION t={m['t']:.2f}s act={m['activacion']} gestos={m['n_gestos']} calib={m['calibrando']} "
                  + ' '.join(f"{c} {r[c]:.1f}/{g[c]:.0f}uV{'(ign)' if m['ignorado'][c] else ''}" for c in r)
                  + (f"  [{m['via'] or m['freno']}]" if (m['via'] or m['freno']) else ''))
    if time.time() - t0 > 5:
        print("paquetes en 5 s:", cuenta, "" if 'DETECCION' in cuenta else "  <-- NO llega DETECCION: la Pi corre el stream.py viejo o el detector no arranco")
        cuenta, t0 = {}, time.time()
