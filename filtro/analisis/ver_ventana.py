"""
Que vio el filtro en un tramo de una sesion: por canal el desvio de la señal (8-30 Hz menos su base), el margen vigente (verde),
las muestras sobre el margen (rojo), la coincidencia frontal+temporal, las rafagas contadas y el motivo de cada ventana.
Uso: python filtro/analisis/ver_ventana.py --sesion cejas --desde 142 --hasta 149
"""
import argparse, glob, os, sys
from pathlib import Path
import numpy as np, pandas as pd
FILTRO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FILTRO))
import gestos_tiempo_real as g  # noqa: E402
CAN = ['TP9', 'FP1', 'FP2', 'TP10']
ap = argparse.ArgumentParser(); ap.add_argument('--sesion'); ap.add_argument('--desde', type=float, required=True)
ap.add_argument('--hasta', type=float, required=True); a = ap.parse_args()
arch = sorted(glob.glob(str(FILTRO / 'sesiones' / '*_eeg.csv')), key=os.path.getmtime)
if a.sesion: arch = [x for x in arch if a.sesion.lower() in os.path.basename(x).lower()]
ruta = arch[-1]; nombre = os.path.basename(ruta)[:-8]
df = pd.read_csv(ruta); x = df[CAN].values; trig = df.Trigger.values; fs = g.FS
m = g.MotorGestos(fs, vis=True); XS, MG, dec = [], [], []
for i in range(0, len(x), 12):
    dec += m.agregar(x[i:i + 12]); v = m.vista(min(12, len(x) - i)); XS.append(v['xs']); MG.append(v['mg'])
XS, MG = np.hstack(XS), np.hstack(MG); t = np.arange(XS.shape[1]) / fs
sup = np.abs(XS) > np.where(np.isnan(MG), np.inf, MG)
co = (sup[1] | sup[2]) & (sup[0] | sup[3])
mk = (t >= a.desde) & (t <= a.hasta)
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
fig, ax = plt.subplots(6, 1, figsize=(12, 10), sharex=True, gridspec_kw={'height_ratios': [3, 3, 3, 3, 1, 1.4]})
for k, c in enumerate(CAN):
    ax[k].plot(t[mk], XS[k][mk], lw=0.7, color='royalblue'); ax[k].plot(t[mk], MG[k][mk], color='green'); ax[k].plot(t[mk], -MG[k][mk], color='green')
    s = mk & sup[k]; ax[k].plot(t[s], XS[k][s], '.', color='red', ms=3)
    ax[k].set_ylabel(f"{c}\nmargen {np.nanmean(MG[k][mk]):.0f} uV", fontsize=8)
    lim = max(np.abs(XS[k][mk]).max(), np.nanmax(MG[k][mk])) * 1.1; ax[k].set_ylim(-lim, lim)
    for s_, f_ in zip(np.flatnonzero(np.diff(np.r_[0, trig]) == 1) / fs, np.flatnonzero(np.diff(np.r_[trig, 0]) == -1) / fs):
        ax[k].axvspan(s_, f_, color='limegreen', alpha=0.2)
ax[4].fill_between(t[mk], co[mk], step='mid', color='red'); ax[4].set_ylabel('frontal+\ntemporal', fontsize=8); ax[4].set_yticks([])
ax[5].set_ylim(0, 1); ax[5].set_yticks([])
for d in dec:
    tf = d['fin'] / fs
    if a.desde - 0.5 <= tf <= a.hasta + 0.5:
        ax[5].axvspan(tf - 0.25, tf, ymin=0, ymax=1, color='red' if d['det'] else 'lightgray', alpha=0.5)
        txt = 'GESTO' if d['det'] else (d['freno'].split('(')[0].strip()[:22] if d['freno'] else 'ruido')
        ax[5].text(tf - 0.125, 0.5, txt, rotation=90, ha='center', va='center', fontsize=6)
ax[5].set_ylabel('ventanas', fontsize=8); ax[5].set_xlim(a.desde, a.hasta); ax[5].set_xlabel('s')
fig.suptitle(f"{nombre} | azul = desvio | verde = margen | rojo = sobre el margen | verde claro = tu marca", fontsize=9)
fig.tight_layout(); out = FILTRO / 'capturas' / f"ventana_{nombre}_{a.desde:.0f}-{a.hasta:.0f}.png"; fig.savefig(out, dpi=100); print(out)
# resumen en texto
d_ = np.diff(np.r_[0, co[mk].astype(int), 0]); ini = t[mk][np.flatnonzero(d_[:-1] == 1)]
print('inicios de coincidencia frontal+temporal en el tramo:', [round(float(u), 2) for u in ini])
for d in dec:
    tf = d['fin'] / fs
    if a.desde <= tf <= a.hasta + 0.5: print(f"  ventana ->{tf:.2f}s det={d['det']} freno={d['freno'] or '-'} ratios={ {c: round(v,1) for c,v in d['ratios'].items()} }")
