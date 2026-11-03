"""
Estabilidad, retardo y forma de la respuesta del filtro Butterworth pasa-banda de 8 a 30 Hz, para distintos ordenes.
Calcula los polos y ceros (los calcula SciPy a partir del orden y de las frecuencias de corte, no se eligen a mano), comprueba que todos los
polos esten dentro del circulo unidad (condicion de estabilidad de un filtro IIR) y mide el retardo y el tiempo de asentamiento.
Guarda analisis/figuras/fig4_polos_y_ceros.png.
Uso: python filtro/analisis/estabilidad_filtro.py
"""
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

FS = 220.0
BANDA = (8.0, 30.0)
SALIDA = Path(__file__).resolve().parent / 'figuras'
SALIDA.mkdir(exist_ok=True)


def diseno(orden, fs=FS, forma='sos'):
    return signal.butter(orden, [BANDA[0] / (fs / 2), BANDA[1] / (fs / 2)], btype='band', output=forma)


print(f'fs = {FS:g} Hz, banda {BANDA[0]:g} a {BANDA[1]:g} Hz')
print(f"{'orden de diseño':>15s} {'orden total':>11s} {'polos':>6s} {'ceros':>6s} {'|polo| max':>10s} {'estable':>8s} "
      f"{'retardo 10/20/30 Hz (ms)':>26s} {'asentamiento (ms)':>18s} {'coef. ba estable':>17s}")
for orden in (2, 4, 6, 8, 10, 12):
    z, p, k = diseno(orden, forma='zpk')
    sos = diseno(orden)
    # retardo de grupo (ms) en la banda: suma de los retardos de cada seccion de segundo orden (evita el polinomio de grado alto)
    gd = np.zeros(3)
    for fila in sos:
        _, g_ = signal.group_delay((fila[:3], fila[3:]), w=[10, 20, 30], fs=FS)
        gd += g_
    # tiempo de asentamiento de la respuesta al impulso: hasta que queda por debajo del 1 % de su maximo
    n = 4000
    imp = np.zeros(n)
    imp[0] = 1.0
    h = signal.sosfilt(sos, imp)
    ult = np.flatnonzero(np.abs(h) > 0.01 * np.abs(h).max())[-1]
    # la misma funcion de transferencia en forma b, a (un solo polinomio): se puede calcular mal?
    b, a = diseno(orden, forma='ba')
    polos_ba = np.roots(a)
    est_ba = bool(np.all(np.abs(polos_ba) < 1.0))
    print(f'{orden:15d} {2 * orden:11d} {len(p):6d} {len(z):6d} {np.abs(p).max():10.4f} {str(bool(np.all(np.abs(p) < 1))):>8s} '
          f"{gd[0] / FS * 1000:7.0f}/{gd[1] / FS * 1000:3.0f}/{gd[2] / FS * 1000:3.0f}{'':>9s} {ult / FS * 1000:18.0f} {str(est_ba):>17s}")

# polos y ceros del filtro de orden total 16
z, p, k = diseno(8, forma='zpk')
print('\norden total 16: ceros =', np.unique(np.round(z.real, 6)), '(cantidad de cada uno:', {float(v): int(np.sum(np.isclose(z.real, v))) for v in np.unique(np.round(z.real, 6))}, ')')
print(f'orden total 16: radio de los polos de {np.abs(p).min():.4f} a {np.abs(p).max():.4f}; el mas cercano al borde del circulo esta a {1 - np.abs(p).max():.4f}')
for fs2 in (89.0, 115.0, 220.0):
    z2, p2, _ = signal.butter(8, [8 / (fs2 / 2), min(30.0, 0.9 * fs2 / 2) / (fs2 / 2)], btype='band', output='zpk')
    print(f'  fs = {fs2:5.0f} Hz: |polo| max = {np.abs(p2).max():.4f} -> estable: {bool(np.all(np.abs(p2) < 1))}')

fig, ax = plt.subplots(1, 2, figsize=(11, 5))
for a, orden, ttl in ((ax[0], 4, 'Orden total 8'), (ax[1], 8, 'Orden total 16')):
    z, p, k = diseno(orden, forma='zpk')
    th = np.linspace(0, 2 * np.pi, 400)
    a.plot(np.cos(th), np.sin(th), 'k', lw=1)
    a.plot(p.real, p.imag, 'x', color='tab:blue', ms=7, label=f'polos ({len(p)})')
    a.plot(z.real, z.imag, 'o', mfc='none', color='tab:red', ms=8, label=f'ceros ({len(z)}: en z = 1 y z = −1)')
    a.set_aspect('equal')
    a.set_xlim(-1.4, 1.4)
    a.set_ylim(-1.4, 1.4)
    a.set_title(f'{ttl}   (|polo| máximo = {np.abs(p).max():.3f})')
    a.set_xlabel('Parte real')
    a.set_ylabel('Parte imaginaria')
    a.grid(alpha=0.3)
    a.legend(loc='upper right', fontsize=8)
fig.suptitle('Polos y ceros del filtro Butterworth pasa-banda de 8 a 30 Hz (fs = 220 Hz): todos los polos dentro del círculo unidad')
fig.tight_layout()
fig.savefig(SALIDA / 'fig4_polos_y_ceros.png', dpi=160)
plt.close(fig)
print('figura en', SALIDA / 'fig4_polos_y_ceros.png')
