"""
Prueba de recepcion ZMQ SIN filtro ni graficos: solo cuenta lo que llega de la Raspberry y dice si el flujo viene completo.

Uso:   python prueba_recepcion.py --ip 192.168.1.50 [--puerto 5555] [--segundos 60]

Cada 2 s imprime, por separado:
  - muestras EEG por segundo (esperado 220) y su promedio acumulado,
  - la mayor pausa entre dos mensajes EEG consecutivos (un corte en la red o en la Raspberry),
  - el mayor lote leido de una vez (rafagas: la red o la PC se atrasaron y despues llego todo junto),
  - el retraso acumulado: segundos de reloj transcurridos menos muestras recibidas / fs. Si crece sin parar, se pierden muestras
    antes de llegar a la PC (Muse -> Raspberry o Wi-Fi). Si sube y baja, son rafagas pero no se pierde nada.
Al final resume y dice donde esta el problema. No usa pyqtgraph, asi que si aca el flujo es normal y en el tablero no, el atraso
es del dibujo.
"""
import argparse
import time
from collections import Counter

import zmq

FS = 220.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ip', default='192.168.1.50')
    ap.add_argument('--puerto', type=int, default=5555)
    ap.add_argument('--segundos', type=float, default=60.0)
    ap.add_argument('--fs', type=float, default=FS)
    a = ap.parse_args()

    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, 0)
    sock.connect(f"tcp://{a.ip}:{a.puerto}")
    sock.setsockopt_string(zmq.SUBSCRIBE, "")
    print(f"Escuchando tcp://{a.ip}:{a.puerto} durante {a.segundos:.0f} s (Ctrl+C para cortar antes)\n")

    t_ini = time.time()
    t_eeg0 = None
    n_eeg = 0
    t_prev_eeg = None
    pausa_max = 0.0
    lote_max = 0
    por_tipo = Counter()
    t_print = t_ini
    n_print = 0
    pausa_print = 0.0
    lote_print = 0
    claves = {}
    pausas = []                                      # (instante relativo, pausa) de las pausas > 0.3 s
    retraso_max = 0.0
    try:
        while time.time() - t_ini < a.segundos:
            lote = 0
            while True:
                try:
                    msg = sock.recv_json(flags=zmq.NOBLOCK)
                except zmq.Again:
                    break
                ahora = time.time()
                tipo = msg.get('tipo', '?')
                por_tipo[tipo] += 1
                if tipo not in claves:
                    claves[tipo] = sorted(msg.keys())
                if tipo == 'EEG':
                    lote += 1
                    if t_eeg0 is None:
                        t_eeg0 = ahora
                    if t_prev_eeg is not None:
                        p = ahora - t_prev_eeg
                        pausa_max = max(pausa_max, p)
                        pausa_print = max(pausa_print, p)
                        if p > 0.3:
                            pausas.append((ahora - t_ini, p))
                    t_prev_eeg = ahora
                    n_eeg += 1
                    n_print += 1
            if lote:
                lote_max = max(lote_max, lote)
                lote_print = max(lote_print, lote)
            ahora = time.time()
            if ahora - t_print >= 2.0:
                retraso = (ahora - t_eeg0) - n_eeg / a.fs if t_eeg0 else 0.0
                retraso_max = max(retraso_max, retraso)
                prom = n_eeg / (ahora - t_eeg0) if t_eeg0 else 0.0
                print(f"[{ahora - t_ini:5.0f} s] EEG {n_print / (ahora - t_print):6.1f}/s (promedio {prom:6.1f})  "
                      f"mayor pausa {pausa_print:5.2f} s  mayor lote {lote_print:4d}  retraso acumulado {retraso:+6.2f} s")
                t_print, n_print, pausa_print, lote_print = ahora, 0, 0.0, 0
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass

    fin = time.time()
    dur = fin - t_eeg0 if t_eeg0 else 0.0
    print("\n===== RESUMEN =====")
    print("Mensajes recibidos por tipo:", dict(por_tipo))
    for t, k in claves.items():
        print(f"  campos de {t}: {k}")
    if not dur:
        print("No llego ningun EEG.")
        return
    tasa = n_eeg / dur
    print(f"EEG: {n_eeg} muestras en {dur:.1f} s de reloj = {tasa:.1f} Hz (esperado {a.fs:.0f})")
    print(f"Mayor pausa entre muestras: {pausa_max:.2f} s   |   mayor lote leido de una vez: {lote_max} muestras ({lote_max / a.fs:.2f} s)")
    print(f"Retraso final {((fin - t_eeg0) - n_eeg / a.fs):+.2f} s (maximo {retraso_max:+.2f} s)")
    if pausas:
        print(f"Pausas > 0.3 s: {len(pausas)}  (ej. " + ", ".join(f"{t:.0f}s:{p:.1f}s" for t, p in pausas[:6]) + ")")
    print()
    if tasa < 0.97 * a.fs:
        print(f"-> Llegan menos muestras de las esperadas ({tasa:.0f} Hz). La perdida es ANTES de la PC: Muse -> Raspberry (UDP) o Wi-Fi.")
        print("   Si la tasa es estable (p. ej. 207 Hz todo el tiempo) el emisor entrega a otra frecuencia; hay que usar esa en FS.")
    elif tasa > 1.03 * a.fs:
        print(f"-> Llegan MAS muestras de las esperadas ({tasa:.0f} Hz): el emisor entrega a otra frecuencia (p. ej. 256 Hz). Ajustar FS.")
    else:
        print("-> La cantidad de muestras es la esperada.")
    if lote_max > 0.5 * a.fs:
        print(f"-> Hay rafagas grandes ({lote_max} muestras juntas): la red o el emisor se frenan y despues entregan todo de golpe. "
              "En pantalla se ve como señal 'tarde' que despues se pone al dia.")
    if retraso_max > 1.0:
        print("-> El retraso supero 1 s: si no baja, el atraso es acumulativo (perdida de muestras antes de llegar).")


if __name__ == '__main__':
    main()
