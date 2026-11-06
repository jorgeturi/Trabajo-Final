"""
Visualizador en tiempo real de lo que publica la Raspberry: los 4 canales (EEG filtrado 8-30 Hz) y, debajo, la activacion.

Activacion (la que va a la Pico): se MANTIENE en 1 desde un gesto detectado hasta el siguiente gesto, que la vuelve a 0.
Un "gesto" es lo que detecta el filtro de la Pi (2 rafagas separadas dentro de 2.5 s). Debajo de los canales se dibuja:
  - la activacion mantenida (linea gruesa, 0/1),
  - la retencion corta de 1 s despues de cada deteccion ("pulso", celeste),
  - las ventanas de 0.5 s con deteccion (naranja) y el instante de cada gesto nuevo (linea roja).

La señal dibujada es la que usa el detector: el EEG crudo de la Pi filtrado 8-30 Hz (Butterworth orden 8 en sos, el mismo de
gestos_tiempo_real.py) menos su promedio de los ultimos 2 s. NO es el "_filtrado" que publica stream.py (Butterworth de orden 4,
otra forma de onda; con --senal-pi se dibuja ese).

Las detecciones se ubican en el eje por su LLEGADA (el EEG y la deteccion viajan por canales distintos de la Pi y no comparten
indice de muestra), asi que pueden quedar corridas hasta un lote (~50 ms).

Siempre se dibuja lo ultimo recibido: si la ventana se atrasa, salta a lo nuevo (nada se guarda aca; para grabar usar
gestos_tiempo_real.py). Arriba se muestran el retraso de la señal frente al reloj, la tasa y cuanto tarda el detector en la Pi.

Uso:   python filtro/visualizador_activacion.py [IP_DE_LA_PI]            (por defecto 192.168.1.50)
Prueba sin pantalla:  python filtro/visualizador_activacion.py 127.0.0.1 --prueba 45 --salida captura.png
Requiere: pyzmq, pyqtgraph y PyQt5.
"""
import argparse
import os
import sys
import time

import numpy as np

FS = 220.0
CAN = ['TP9', 'FP1', 'FP2', 'TP10']
VENT_S = 8.0
N = int(VENT_S * FS)
W = int(0.5 * FS)


class SockLotes:
    """Envuelve el socket SUB: los mensajes por lote de stream.py (EEG_LOTE a 220 Hz y EEG_DET a 110 Hz) se entregan muestra
    por muestra con el formato de antes ({"tipo": "EEG", "TP9_raw": ...} y {"tipo": "EEG_DET", "TP9_raw": ..., "TP9_filtrado": ...}),
    asi el resto del programa no cambia."""
    CAN = ("TP9", "FP1", "FP2", "TP10")

    def __init__(self, sock):
        from collections import deque
        self._sock, self._pend = sock, deque()

    def __getattr__(self, nombre):
        return getattr(self._sock, nombre)

    def recv_json(self, *a, **k):
        while not self._pend:
            m = self._sock.recv_json(*a, **k)
            t = m.get("tipo")
            if t == "EEG_LOTE":
                for i in range(len(m["TP9"])):
                    self._pend.append({"tipo": "EEG", **{f"{c}_raw": m[c][i] for c in self.CAN}})
            elif t == "EEG_DET":
                for i in range(len(m["TP9_raw"])):
                    self._pend.append({"tipo": "EEG_DET", **{f"{c}_{s}": m[f"{c}_{s}"][i] for c in self.CAN for s in ("raw", "filtrado")}})
            else:
                return m
        return self._pend.popleft()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ip', nargs='?', default='192.168.1.50')
    ap.add_argument('--puerto', type=int, default=5555)
    ap.add_argument('--prueba', type=float, help="segundos que corre antes de guardar una imagen y salir (sin pantalla)")
    ap.add_argument('--salida', default='captura_activacion.png')
    ap.add_argument('--senal-pi', action='store_true', help="dibujar el _filtrado que publica stream.py en vez de la señal del detector")
    a = ap.parse_args()
    if a.prueba:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

    import pyqtgraph as pg
    import zmq
    from scipy import signal
    from pyqtgraph.Qt import QtCore, QtWidgets

    sock = zmq.Context().socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, 5000)
    sock.connect(f"tcp://{a.ip}:{a.puerto}")
    sock.setsockopt_string(zmq.SUBSCRIBE, "")
    sock = SockLotes(sock)

    sos = signal.butter(8, [8.0 / (FS / 2), 30.0 / (FS / 2)], btype='band', output='sos')
    zi = [None] * 4
    N2 = int(2 * FS)
    yf = np.zeros((4, N + N2))                  # señal filtrada (con 2 s extra para la base)
    y = np.zeros((4, N))
    act = np.zeros(N)
    pulso = np.zeros(N)
    det = np.zeros(N, dtype=bool)
    gesto = np.zeros(N, dtype=bool)
    st = {'act': 0, 'pulso': 0, 't0': None, 'n': 0, 'ult_eeg': time.time(), 'pkt': None, 'ym': [150.0] * 4,
          'hist': [], 'gestos_vistos': 0}

    def desplazar(k):
        if k >= N:
            k = N
        for arr in (y, act, pulso, det, gesto):
            if arr.ndim == 2:
                arr[:, :-k] = arr[:, k:]
            else:
                arr[:-k] = arr[k:]

    def recibir():
        nuevas = []
        paquetes = []
        try:
            while True:
                m = sock.recv_json(flags=zmq.NOBLOCK)
                tipo = m.get('tipo')
                if tipo == 'EEG':
                    nuevas.append([float(m.get(f'{c}_raw', 0.0)) for c in CAN] + [float(m.get(f'{c}_filtrado', 0.0)) for c in CAN])
                elif tipo == 'DETECCION':
                    paquetes.append(m)
        except zmq.Again:
            pass
        if nuevas:
            ahora = time.time()
            if st['t0'] is None:
                st['t0'] = ahora
            st['ult_eeg'] = ahora
            st['n'] += len(nuevas)
            st['hist'].append((ahora, st['n']))
            k = min(len(nuevas), N)
            desplazar(k)
            bloque = np.array(nuevas[-k:])
            if a.senal_pi:
                y[:, -k:] = bloque[:, 4:].T
            else:
                crudo = np.array(nuevas).T[:4]                                         # todas las muestras (el filtro tiene estado)
                f = np.empty_like(crudo)
                for i in range(4):
                    if zi[i] is None:
                        zi[i] = signal.sosfilt_zi(sos) * crudo[i, 0]
                    f[i], zi[i] = signal.sosfilt(sos, crudo[i], zi=zi[i])
                kk = min(f.shape[1], N + N2)
                yf[:, :-kk] = yf[:, kk:]
                yf[:, -kk:] = f[:, -kk:]
                cs = np.cumsum(np.concatenate([np.zeros((4, 1)), yf], axis=1), axis=1)
                base = (cs[:, N2 + 1:] - cs[:, 1:-N2]) / N2                             # promedio de las 2 s anteriores (incluida la muestra)
                y[:] = yf[:, N2:] - base[:, :N]
            act[-k:], pulso[-k:], det[-k:], gesto[-k:] = st['act'], st['pulso'], False, False
        for p in paquetes:
            st['act'], st['pulso'], st['pkt'] = p['activacion'], p.get('pulso', 0), p     # .get: la Pi puede tener un detector_gestos.py anterior
            act[-1:], pulso[-1:] = st['act'], st['pulso']
            if p['det']:
                det[-W:] = True
            if p['gesto_nuevo']:
                gesto[-1] = True
                st['gestos_vistos'] += 1

    app = QtWidgets.QApplication(sys.argv)
    win = QtWidgets.QWidget()
    win.setWindowTitle("Activacion en tiempo real - 4 canales + flag")
    win.setStyleSheet("background-color: #14161a;")
    win.resize(1250, 900)
    caja = QtWidgets.QVBoxLayout(win)
    caja.setContentsMargins(6, 4, 6, 4)
    caja.setSpacing(2)

    def etiqueta(pt, alto):
        lb = QtWidgets.QLabel("")
        lb.setTextFormat(QtCore.Qt.RichText)
        lb.setStyleSheet(f"color: #e6e6e6; font-size: {pt}pt;")
        lb.setFixedHeight(alto)
        lb.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        caja.addWidget(lb)
        return lb

    lbl_act = etiqueta(16, 34)
    lbl_est = etiqueta(10, 22)
    lbl_det = etiqueta(10, 22)
    graf = pg.GraphicsLayoutWidget()
    caja.addWidget(graf, 1)
    t = np.linspace(-VENT_S, 0, N)
    colores = [(120, 160, 255), (120, 255, 120), (255, 150, 255), (255, 255, 120)]
    curvas = []
    for i, c in enumerate(CAN):
        pl = graf.addPlot(row=i, col=0)
        pl.showGrid(x=True, y=True, alpha=0.25)
        pl.setXRange(-VENT_S, 0, padding=0)
        pl.setLabel('left', f'{c} (uV)')
        pl.setMouseEnabled(x=False, y=False)
        pl.getAxis('left').setWidth(62)
        pl.getAxis('bottom').setStyle(showValues=False)
        curvas.append((pl, pl.plot(pen=pg.mkPen(color=colores[i], width=1))))
    pf = graf.addPlot(row=4, col=0)
    pf.showGrid(x=True, y=False, alpha=0.25)
    pf.setXRange(-VENT_S, 0, padding=0)
    pf.setYRange(-0.1, 1.25, padding=0)
    pf.setMouseEnabled(x=False, y=False)
    pf.getAxis('left').setWidth(62)
    pf.setLabel('left', 'flag')
    pf.setLabel('bottom', 'segundos (0 = ultima muestra recibida)')
    graf.ci.layout.setRowStretchFactor(4, 2)
    c_det = pf.plot(pen=pg.mkPen((255, 170, 0), width=1), fillLevel=0, brush=(255, 170, 0, 90))
    c_pulso = pf.plot(pen=pg.mkPen((0, 200, 255), width=1))
    c_act = pf.plot(pen=pg.mkPen((60, 255, 90), width=4))
    marcas = pg.ScatterPlotItem(size=14, symbol='t1', pen=None, brush=pg.mkBrush(255, 60, 60))
    pf.addItem(marcas)

    def dibujar():
        for i, (pl, cv) in enumerate(curvas):
            cv.setData(t, y[i])
            tope = max(60.0, 1.2 * float(np.abs(y[i]).max()))
            st['ym'][i] = max(tope, 0.92 * st['ym'][i])
            pl.setYRange(-st['ym'][i], st['ym'][i], padding=0)
        c_act.setData(t, act)
        c_pulso.setData(t, pulso * 0.8)
        c_det.setData(t, det.astype(float) * 0.5)
        idx = np.flatnonzero(gesto)
        marcas.setData(t[idx], np.full(len(idx), 1.12))
        ahora = time.time()
        h = st['hist']
        while len(h) > 2 and ahora - h[0][0] > 5:
            h.pop(0)
        tasa = (h[-1][1] - h[0][1]) / max(h[-1][0] - h[0][0], 1e-6) if len(h) > 2 else 0.0
        pausa = ahora - st['ult_eeg']
        retraso = (ahora - st['t0']) - st['n'] / FS if st['t0'] else 0.0
        p = st['pkt']
        col = '#3cff5a' if st['act'] else '#aaaaaa'
        lbl_act.setText(f"<span style='color:{col}'>ACTIVACION: {'1  (ENCENDIDO)' if st['act'] else '0  (apagado)'}</span>"
                        f"   &nbsp; gestos detectados: {st['gestos_vistos']}")
        bien = tasa >= 0.9 * FS and pausa < 0.3
        lbl_est.setText(f"<span style='color:{'#4caf50' if bien else '#ff9800'}'>FLUJO: {tasa:.0f} muestras/s (esperado {FS:.0f})   "
                        f"sin datos hace {pausa:.1f} s   señal atrasada {retraso:+.1f} s</span>")
        if p:
            motivo = p['via'] or p['freno'] or 'sin deteccion'
            lbl_det.setText(f"Detector: {'CALIBRANDO' if p['calibrando'] else 'activo'}   decide en {p.get('ms', 0):.0f} ms (cola {p.get('cola', 0)})   "
                            f"ultima ventana: {motivo}   |   pico/margen: "
                            + '  '.join(f"{c} {p['ratios'][c]:.1f}{'(ign)' if p['ignorado'][c] else ''}" for c in CAN))
        else:
            lbl_det.setText("Esperando paquetes DETECCION de la Raspberry (¿esta corriendo el stream.py nuevo?)")

    t_rec = QtCore.QTimer()
    t_rec.timeout.connect(recibir)
    t_rec.start(15)
    t_dib = QtCore.QTimer()
    t_dib.timeout.connect(dibujar)
    t_dib.start(50)
    win.show()
    if a.prueba:
        def terminar():
            dibujar()
            app.processEvents()
            win.grab().save(a.salida)
            print("Imagen guardada en", os.path.abspath(a.salida), "| gestos vistos:", st['gestos_vistos'], "| activacion:", st['act'])
            app.quit()
        QtCore.QTimer.singleShot(int(a.prueba * 1000), terminar)
    print(f"Escuchando {a.ip}:{a.puerto}. Cierra la ventana para salir.")
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
