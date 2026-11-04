"""
Escucha por ZMQ lo que publica la Raspberry y muestra: los tipos de paquete que llegan, los paquetes DETECCION (margen y pico/margen
por canal) y las tramas que la Pi manda a la Pico (TRAMA_PICO: se imprime cuando cambia el estado o la activacion, y el motivo cuando
el estado es EMERGENCIA). Sirve para comprobar que la Pi publica la deteccion y que manda a la Pico lo que se espera, sin el dashboard.

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
ultima_trama = [None]
while True:
    try:
        m = sock.recv_json()
    except zmq.Again:
        m = None
    except KeyboardInterrupt:
        break
    if m:
        cuenta[m.get('tipo')] = cuenta.get(m.get('tipo'), 0) + 1
        if m.get('tipo') == 'TRAMA_PICO':
            # lo que la Pi manda a la Pico por UART: se muestra cuando cambia el estado o la activacion, o si hay un hueco o un error
            clave = (m['estado'], m['activacion'])
            if clave != ultima_trama[0] or m['dt_ms'] > 150 or not m['ok']:
                ultima_trama[0] = clave
                print(f"TRAMA_PICO  {m['trama']}  dt={m['dt_ms']} ms  estado={m['estado']}" + (f" ({m['motivo']})" if m.get('motivo') else "")
                      + ("  <-- NO SE PUDO ESCRIBIR" if not m['ok'] else "") + (f"  errores de escritura={m['errores']}" if m['errores'] else ""))
        if m.get('tipo') == 'DETECCION' and time.time() - ultimo > 0.5:
            ultimo = time.time()
            r, g = m['ratios'], m['margen']
            print(f"DETECCION t={m['t']:.2f}s act={m['activacion']} gestos={m['n_gestos']} calib={m['calibrando']} "
                  + ' '.join(f"{c} {r[c]:.1f}/{g[c]:.0f}uV{'(ign)' if m['ignorado'][c] else ''}" for c in r)
                  + (f"  [{m['via'] or m['freno']}]" if (m['via'] or m['freno']) else ''))
    if time.time() - t0 > 5:
        print("paquetes en 5 s:", cuenta, "" if 'DETECCION' in cuenta else "  <-- NO llega DETECCION: la Pi corre el stream.py viejo o el detector no arranco")
        cuenta, t0 = {}, time.time()
