"""
Figuras para la seccion de EEG de la tesis (se guardan en analisis/figuras/):
  fig1_respuesta_en_frecuencia.png   que deja pasar el filtro (orden total 8 y 16) y que atenua
  fig2_un_gesto_paso_a_paso.png      un gesto (dos parpadeos) a traves del procesamiento: crudo -> filtrado -> desviacion y umbral -> rafagas
  fig3_estado_del_filtro.png         por que se conserva el estado entre lotes y se escala el estado inicial
Uso: python filtro/analisis/figuras_filtro.py
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import signal

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
import gestos_tiempo_real as g                                  # noqa: E402

SALIDA = AQUI / 'figuras'
SALIDA.mkdir(exist_ok=True)
DATASET = AQUI.parent / 'sesiones' / 'sesion_20261026_151157_eeg.csv'     # grabacion en vivo a 220 Hz
GESTO_T = 107.5                      # instante (s) en que el filtro cuenta el gesto de ejemplo
FRONTAL, TEMPORAL = 'FP2', 'TP9'   # canales que se muestran
plt.rcParams.update({'font.size': 10, 'axes.grid': True, 'grid.alpha': 0.3})
df = pd.read_csv(DATASET)
FS = round((len(df) - 1) / (df['Timestamp'].iloc[-1] - df['Timestamp'].iloc[0]), 2)
X = df[g.CAN].values.astype(float)


def sos_banda(orden, fs=FS):
    return signal.butter(orden, [8.0 / (fs / 2), 30.0 / (fs / 2)], btype='band', output='sos')


# ---------------------------------------------------------------- figura 1
fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
for orden, col in ((4, 'tab:orange'), (8, 'tab:blue')):
    sos = sos_banda(orden)
    f, h = signal.sosfreqz(sos, worN=4096, fs=FS)
    db = 20 * np.log10(np.maximum(np.abs(h), 1e-12))
    for a in ax:
        a.plot(f, db, color=col, lw=1.8, label=f'orden total {2 * orden} ({orden} secciones de segundo orden)')
ax[0].set_xlim(0, FS / 2)
ax[0].set_ylim(-120, 5)
ax[0].set_title(f'Respuesta en magnitud (0 a {FS / 2:.0f} Hz)')
ax[1].set_xlim(0, 60)
ax[1].set_ylim(-60, 3)
ax[1].set_title('Detalle: 0 a 60 Hz')
for a in ax:
    a.axvspan(8, 30, color='green', alpha=0.08, label='banda de paso (8 a 30 Hz)')
    a.axhline(-3, color='k', lw=0.8, ls=':')
    a.set_xlabel('Frecuencia (Hz)')
    a.set_ylabel('Ganancia (dB)')
atenuacion = []
for fr in (2, 4, 40, 50):
    fila = []
    for orden in (4, 8):
        _, hh = signal.sosfreqz(sos_banda(orden), worN=[fr], fs=FS)
        fila.append(20 * np.log10(abs(hh[0]) + 1e-12))
    atenuacion.append(f'{fr:>2d} Hz: {fila[0]:6.0f} dB (orden 8)   {fila[1]:6.0f} dB (orden 16)')
ax[1].text(0.98, 0.5, 'Atenuación fuera de la banda\n' + '\n'.join(atenuacion), transform=ax[1].transAxes, ha='right', va='center',
           fontsize=7.5, family='monospace', bbox=dict(boxstyle='round', fc='white', ec='0.7'))
ax[0].legend(loc='lower right', fontsize=8)
fig.suptitle('Filtro Butterworth pasa-banda de 8 a 30 Hz')
fig.tight_layout()
fig.savefig(SALIDA / 'fig1_respuesta_en_frecuencia.png', dpi=170)
plt.close(fig)

# ---------------------------------------------------------------- figura 2
m = g.MotorGestos(FS, vis=True)
R = len(X) + int(10 * FS)
m.R = R
m.v_xs, m.v_mg = np.zeros((4, R)), np.full((4, R), np.nan)
m.v_ign, m.v_det = np.zeros((4, R), dtype=bool), np.zeros(R, dtype=bool)
for a in range(0, len(X), 55):
    m.agregar(X[a:a + 55])
sos = sos_banda(8)
filtrada = np.vstack([signal.sosfilt(sos, X[:, i], zi=signal.sosfilt_zi(sos) * X[0, i])[0] for i in range(4)])
t0, t1 = GESTO_T - 3.0, GESTO_T + 2.5
i0, i1 = int(t0 * FS), int(t1 * FS)
t = np.arange(i0, i1) / FS
ix = np.arange(i0, i1)
kf, kt = g.CAN.index(FRONTAL), g.CAN.index(TEMPORAL)
fig, ejes = plt.subplots(6, 1, figsize=(10, 12), sharex=True, gridspec_kw={'height_ratios': [1, 1, 1, 1, 0.8, 0.5]})
ejes[0].plot(t, X[i0:i1, kf], color='tab:gray', lw=0.8)
ejes[0].set_ylabel(f'{FRONTAL} cruda\n(µV)')
ejes[0].set_title('Un gesto (dos ráfagas) a través del procesamiento', fontsize=11)
ejes[1].plot(t, filtrada[kf, i0:i1], color='tab:green', lw=0.8)
ejes[1].set_ylabel(f'{FRONTAL} filtrada\n8 a 30 Hz (µV)')
for fila, k, ttl in ((2, kf, 'frontal'), (3, kt, 'temporal')):
    xs, mg = m.v_xs[k, ix], m.v_mg[k, ix]
    ejes[fila].plot(t, xs, color='tab:blue' if ttl == 'frontal' else 'tab:purple', lw=0.8)
    ejes[fila].plot(t, mg, color='green', lw=1.4, label='umbral adaptativo')
    ejes[fila].plot(t, -mg, color='green', lw=1.4)
    sobre = np.abs(xs) > np.where(np.isfinite(mg), mg, np.inf)
    ejes[fila].plot(t[sobre], xs[sobre], 'r.', ms=3, label='sobre el umbral')
    ejes[fila].set_ylabel(f'{g.CAN[k]} ({ttl})\ndesviación (µV)')
    lim = max(2.2 * np.nanmedian(mg), 1.1 * np.abs(xs).max())
    ejes[fila].set_ylim(-lim, lim)
ejes[2].legend(loc='upper right', fontsize=8)
sob = (np.abs(m.v_xs[:, ix]) > np.where(np.isfinite(m.v_mg[:, ix]), m.v_mg[:, ix], np.inf)) & ~m.v_ign[:, ix]
co = (sob[1] | sob[2]) & (sob[0] | sob[3])
ejes[4].fill_between(t, 0, co.astype(float), step='mid', color='tab:orange', alpha=0.85)
ejes[4].set_ylim(0, 1.5)
ejes[4].set_yticks([0, 1])
ejes[4].set_ylabel('frontal y temporal\nsobre su umbral')
d_ = np.diff(np.concatenate(([0], co.astype(int), [0])))
ini, fin = np.flatnonzero(d_ == 1), np.flatnonzero(d_ == -1)
rafagas = []
for a, b in zip(ini, fin):
    if rafagas and a - rafagas[-1][1] < int(0.12 * FS):
        rafagas[-1][1] = b
    else:
        rafagas.append([a, b])
rafagas = [r for r in rafagas if (r[1] - r[0]) / FS >= 0.06]
for n, (a, b) in enumerate(rafagas, 1):
    ejes[4].annotate(f'ráfaga {n}', ((t[a] + t[min(b, len(t) - 1)]) / 2, 1.12), ha='center', fontsize=8)
ejes[5].fill_between(t, 0, m.v_det[ix].astype(float), step='mid', color='tab:red', alpha=0.7)
ejes[5].set_ylim(0, 1.3)
ejes[5].set_yticks([0, 1])
ejes[5].set_ylabel('gesto\ncontado')
ejes[5].set_xlabel('Tiempo (s)')
fig.align_ylabels(ejes)
fig.tight_layout()
fig.savefig(SALIDA / 'fig2_un_gesto_paso_a_paso.png', dpi=150)
plt.close(fig)

# ---------------------------------------------------------------- figura 3
x = X[:, kf]
sos = sos_banda(8)
completo = signal.sosfilt(sos, x, zi=signal.sosfilt_zi(sos) * x[0])[0]
cero = signal.sosfilt(sos, x[:int(3 * FS)])
LOTE = 11
sin_estado = np.concatenate([signal.sosfilt(sos, x[a:a + LOTE]) for a in range(0, len(x), LOTE)])
zi = signal.sosfilt_zi(sos) * x[0]
con = []
for a in range(0, len(x), LOTE):
    y, zi = signal.sosfilt(sos, x[a:a + LOTE], zi=zi)
    con.append(y)
con = np.concatenate(con)
fig, ax = plt.subplots(1, 3, figsize=(13, 4.2))
tt = np.arange(int(3 * FS)) / FS
ax[0].plot(tt, cero, color='tab:red', lw=1, label='estado inicial en cero')
ax[0].plot(tt, completo[:len(tt)], color='tab:green', lw=1.2, label='estado inicial escalado\na la primera muestra')
ax[0].set_title('Arranque del filtro')
ax[0].set_ylabel('Salida (µV)')
j0 = int(106.65 * FS)          # durante el gesto, para que la diferencia se vea
j1 = j0 + 5 * LOTE
tl = np.arange(j0, j1) / FS
lim = 1.25 * np.abs(completo[j0:j1]).max()
ax[1].plot(tl, sin_estado[j0:j1], 'o-', color='tab:red', ms=3, lw=1, label='cada lote desde cero')
ax[1].plot(tl, completo[j0:j1], color='k', lw=1.5, label='señal completa de una vez')
ax[1].set_title('Empalme entre lotes (11 muestras)')
ax[2].plot(tl, con[j0:j1], 'o', color='tab:green', ms=4, label='lotes con el estado conservado')
ax[2].plot(tl, completo[j0:j1], color='k', lw=1.5, label='señal completa de una vez')
ax[2].set_title('Con el estado conservado: idéntico')
for a in ax[1:]:
    a.set_ylim(-lim * 1.5, lim * 1.5)
for a in ax:
    a.set_xlabel('Tiempo (s)')
    a.legend(fontsize=7, loc='lower left', framealpha=0.9)
fig.suptitle(f'Canal {FRONTAL}, filtro de 8 a 30 Hz', y=1.0)
fig.tight_layout()
fig.savefig(SALIDA / 'fig3_estado_del_filtro.png', dpi=160)
plt.close(fig)

err_sin = np.abs(sin_estado - completo)
print(f'fs = {FS} Hz | rafagas mostradas: {len(rafagas)}')
print(f'pico del transitorio de arranque con estado en cero: {np.abs(cero).max():.0f} uV | con estado escalado: {np.abs(completo[:int(3 * FS)]).max():.0f} uV')
print(f'error por lotes sin conservar el estado: maximo {err_sin[int(5 * FS):].max():.1f} uV, medio {err_sin[int(5 * FS):].mean():.1f} uV | con el estado conservado: maximo {np.abs(con - completo).max():.2e} uV')
print('figuras en', SALIDA)
