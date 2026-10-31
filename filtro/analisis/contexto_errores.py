"""
Contexto de los falsos positivos y falsos negativos de una sesion, a partir solo del *_eeg.csv (columna Trigger = marcas).
Pasa el EEG por el MotorGestos de gestos_tiempo_real.py (lotes de 12 muestras, como en vivo) y dibuja, por cada error, las
cuatro señales filtradas (8-30 Hz) con las marcas (verde) y las ventanas con "gesto" (rojo/naranja), mas la senal de referencia
de un acierto. Deja la imagen en filtro/capturas.

Uso: python filtro/analisis/contexto_errores.py [--sesion parte_del_nombre] [--ancho 5] [--tol 1.0]
"""
import argparse, glob, os, sys
from pathlib import Path
import numpy as np, pandas as pd
from scipy import signal

FILTRO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FILTRO))
import gestos_tiempo_real as g  # noqa: E402

CAN = ['TP9', 'FP1', 'FP2', 'TP10']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sesion'); ap.add_argument('--ancho', type=float, default=5.0)
    ap.add_argument('--tol', type=float, default=1.0, help="segundos de tolerancia alrededor de cada marca")
    ap.add_argument('--sep', type=float, default=1.26, help="ventanas con gesto a menos de esto = mismo episodio")
    a = ap.parse_args()
    arch = sorted(glob.glob(str(FILTRO / 'sesiones' / '*_eeg.csv')), key=os.path.getmtime)
    if a.sesion:
        arch = [x for x in arch if a.sesion.lower() in os.path.basename(x).lower()]
    ruta = arch[-1]; nombre = os.path.basename(ruta)[:-8]
    df = pd.read_csv(ruta); x = df[CAN].values; trig = df.Trigger.values; fs = g.FS
    t = np.arange(len(df)) / fs
    m = g.MotorGestos(fs); dets = []; motivos = {}
    for i in range(0, len(x), 12):
        for r in m.agregar(x[i:i + 12]):
            if r['det']:
                dets.append(r['fin'] / fs)
            elif r['freno']:
                motivos[round(r['fin'] / fs, 2)] = r['freno']
    ep = []
    for d in dets:
        if ep and d - ep[-1][1] <= a.sep: ep[-1][1] = d
        else: ep.append([d, d])
    ini = np.flatnonzero(np.diff(np.r_[0, trig]) == 1) / fs; fin = np.flatnonzero(np.diff(np.r_[trig, 0]) == -1) / fs
    marcas = [(s, f) for s, f in zip(ini, fin) if f - s > 0.05]
    fp = [e for e in ep if not any(s - a.tol <= e[1] and e[0] <= f + a.tol for s, f in marcas)]
    fn = [(s, f) for s, f in marcas if not any(s - a.tol <= e[1] and e[0] <= f + a.tol for e in ep)]
    tp = [(s, f) for s, f in marcas if (s, f) not in fn]
    print(f"{nombre} | {t[-1]:.0f} s | marcas {len(marcas)} | detectadas {len(tp)} | falsos negativos {len(fn)} | falsos positivos {len(fp)}")
    for e in fp: print(f"   FP  {e[0]:.1f}-{e[1]:.1f} s")
    for s, f in fn: print(f"   FN  marca {s:.1f}-{f:.1f} s")
    nyq = fs / 2
    sos = signal.butter(8, [8 / nyq, 30 / nyq], btype='band', output='sos')
    sig = {c: signal.sosfilt(sos, x[:, k] - x[0, k]) for k, c in enumerate(CAN)}
    paneles = [(f"FALSO POSITIVO {e[0]:.1f}-{e[1]:.1f} s", (e[0] + e[1]) / 2, 'fp') for e in fp] + \
              [(f"FALSO NEGATIVO marca {s:.1f}-{f:.1f} s", (s + f) / 2, 'fn') for s, f in fn]
    if tp: paneles.append((f"ACIERTO (referencia) marca {tp[0][0]:.1f}-{tp[0][1]:.1f} s", sum(tp[0]) / 2, 'tp'))
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    cols = min(len(paneles), 4); filas = -(-len(paneles) // cols)
    fig, axs = plt.subplots(4 * filas, cols, figsize=(4.6 * cols, 6.4 * filas), squeeze=False)
    for j, (tit, t0, _) in enumerate(paneles):
        r0, c0 = (j // cols) * 4, j % cols
        lo, hi = t0 - a.ancho, t0 + a.ancho; mk = (t >= lo) & (t <= hi)
        for k, c in enumerate(CAN):
            ax = axs[r0 + k][c0]; ax.plot(t[mk], sig[c][mk], lw=0.7, color='royalblue')
            lim = max(np.abs(sig[c][mk]).max() * 1.15, 20); ax.set_ylim(-lim, lim); ax.set_xlim(lo, hi)
            for s, f in marcas: ax.axvspan(s, f, color='limegreen', alpha=0.25)
            for e in ep: ax.axvspan(e[0] - m.p.ventana_s, e[1], color='red', alpha=0.18)
            ax.set_ylabel(c, fontsize=7); ax.tick_params(labelsize=6); ax.grid(ls='--', alpha=0.3)
        axs[r0][c0].set_title(tit, fontsize=8)
    for j in range(len(paneles), filas * cols):
        for k in range(4): axs[(j // cols) * 4 + k][j % cols].axis('off')
    fig.suptitle(f"{nombre} | verde = marca (barra) | rojo = ventanas con 'gesto' del filtro", fontsize=9)
    fig.tight_layout(); os.makedirs(FILTRO / 'capturas', exist_ok=True)
    out = FILTRO / 'capturas' / f"errores_{nombre}.png"; fig.savefig(out, dpi=100); print("Guardado", out)


if __name__ == '__main__':
    main()
