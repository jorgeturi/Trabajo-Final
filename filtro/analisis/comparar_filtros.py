"""
Compara, sobre un gesto grabado, como lo modifica cada filtro causal frente a una referencia de fase cero.

Referencia (gris): FIR de fase lineal aplicado en los dos sentidos (filtfilt): no retrasa ni deforma la fase. No se puede usar en
vivo porque necesita muestras futuras, pero muestra "como deberia verse el gesto" en la banda 8-30 Hz.
Filtros causales (los que se pueden usar en vivo):
  - Butterworth orden 8 en secciones de segundo orden (el del detector),
  - Butterworth orden 4 (el que publica stream.py),
  - FIR de fase lineal causal de 165 coeficientes (Kaiser, 40 dB, transicion de 3 Hz).
Columna izquierda: la salida tal cual sale (con su retardo). Columna derecha: corrida hacia atras el retardo medido por correlacion
cruzada, para comparar la forma. Arriba de cada panel: retardo, correlacion de forma con la referencia y cociente de amplitud del pico.

Uso:
  python filtro/analisis/comparar_filtros.py                         # un gesto de cejas y uno de dos parpadeos de los datasets
  python filtro/analisis/comparar_filtros.py --archivo sesiones/yo_2_cejas_20261029_171930_eeg.csv --gesto 1 --canal FP2
"""
import argparse
import glob
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import signal

FILTRO = Path(__file__).resolve().parent.parent
RAIZ = FILTRO.parent
CAN = ['TP9', 'FP1', 'FP2', 'TP10']


def filtros(fs):
    ny = fs / 2
    N, beta = signal.kaiserord(40, 3.0 / ny)
    N |= 1
    taps = signal.firwin(N, [8 / ny, 30 / ny], pass_zero=False, window=('kaiser', beta))
    sos8 = signal.butter(8, [8 / ny, 30 / ny], btype='band', output='sos')
    sos4 = signal.butter(4, [8 / ny, 30 / ny], btype='band', output='sos')
    return taps, {
        'Butterworth orden 8 (detector)': lambda x: signal.sosfilt(sos8, x),
        'Butterworth orden 4 (stream.py)': lambda x: signal.sosfilt(sos4, x),
        f'FIR fase lineal causal ({N} coef.)': lambda x: signal.lfilter(taps, 1.0, x),
    }


def comparar(archivo, gesto, canal, ax_col):
    d = pd.read_csv(archivo)
    t = d['Timestamp'].values - d['Timestamp'].values[0]
    fs = (len(d) - 1) / t[-1]
    x = d[canal].values.astype(float)
    x = x - np.median(x)
    trig = (d['Trigger'].values == 1).astype(int)
    ini = np.flatnonzero(np.diff(np.r_[0, trig]) == 1)
    fin = np.flatnonzero(np.diff(np.r_[trig, 0]) == -1)
    a, b = ini[gesto], fin[gesto]
    taps, fl = filtros(fs)
    ref = signal.filtfilt(taps, 1.0, x)
    lo, hi = max(0, a - int(1.0 * fs)), min(len(x), b + int(1.5 * fs))
    tt = (np.arange(lo, hi) - a) / fs
    lim = np.abs(ref[lo:hi]).max() * 1.35
    for i, (nombre, f) in enumerate(fl.items()):
        y = f(x)
        s, r = y[lo:hi], ref[lo:hi]
        cc = signal.correlate(s, r, mode='full')
        lag = int(np.clip(cc.argmax() - (len(r) - 1), 0, int(0.6 * fs)))
        s2, r2 = s[lag:], r[:len(s) - lag]
        corr = np.corrcoef(s2, r2)[0, 1]
        amp = np.abs(s2).max() / np.abs(r2).max()
        for j, corrido in enumerate((False, True)):
            ax = ax_col[i][j]
            ax.axvspan(0, (b - a) / fs, color='limegreen', alpha=0.18)
            ax.plot(tt, r, color='gray', lw=1.6, label='referencia (fase cero)')
            ax.plot(tt - (lag / fs if corrido else 0), s, color='tab:red' if i == 0 else 'tab:blue' if i == 1 else 'tab:purple', lw=1.0,
                    label='filtro causal' + (' corrido' if corrido else ''))
            ax.set_ylim(-lim, lim)
            ax.set_xlim(tt[0], tt[-1])
            ax.grid(ls='--', alpha=0.3)
            ax.tick_params(labelsize=7)
            if j == 0:
                ax.set_title(f"{nombre}\nretardo {lag / fs * 1000:.0f} ms | forma {corr:.2f} | amplitud x{amp:.2f}", fontsize=8, loc='left')
            else:
                ax.set_title(f"corrido {lag / fs * 1000:.0f} ms (forma)", fontsize=8, loc='left')
            if i == 0 and j == 0:
                ax.legend(fontsize=7, loc='upper right')
    return os.path.basename(archivo), canal


def principal():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--archivo')
    ap.add_argument('--gesto', type=int, default=1, help="numero de gesto marcado con el Trigger (empieza en 0)")
    ap.add_argument('--canal', default='FP2', choices=CAN)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    casos = []
    if a.archivo:
        casos.append((a.archivo if os.path.isabs(a.archivo) else str(FILTRO / a.archivo), a.gesto, a.canal))
    else:
        for patron in ('dataset_levantar_cejas*', 'dataset_*parpadeo*'):
            for f in sorted(glob.glob(str(RAIZ / (patron + '.csv')))):
                casos.append((f, a.gesto, a.canal))
                break
    fig, axs = plt.subplots(3, 2 * len(casos), figsize=(7.5 * len(casos), 7.8), squeeze=False)
    for k, (f, g, c) in enumerate(casos):
        nom, can = comparar(f, g, c, [[axs[i][2 * k], axs[i][2 * k + 1]] for i in range(3)])
        axs[2][2 * k].set_xlabel(f"s (0 = inicio de la marca)   {nom[:60]}  gesto {g}  canal {can}", fontsize=7)
        axs[2][2 * k + 1].set_xlabel('s', fontsize=7)
    fig.suptitle("Como cambia cada filtro un gesto grabado (verde = marca del Trigger)", fontsize=10)
    fig.tight_layout()
    os.makedirs(FILTRO / 'capturas', exist_ok=True)
    out = FILTRO / 'capturas' / 'comparacion_filtros_gesto.png'
    fig.savefig(out, dpi=100)
    print("Guardado", out)


if __name__ == '__main__':
    principal()
