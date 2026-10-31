"""
Captura de momentos de una sesión grabada y cuenta de falsos positivos y falsos negativos del filtro.

Cuenta SOLO los eventos "gesto" del filtro (el filtro espera dos superaciones antes de emitirlo):
  - verdadero positivo: un "gesto" del filtro dentro de un gesto marcado con la barra espaciadora;
  - falso positivo: un "gesto" del filtro fuera de toda marca. Un gesto que se empezó y se abandonó sin llegar a emitir "gesto"
    no cuenta (el filtro nunca avisó);
  - falso negativo: un gesto marcado con duración (> 0 s) sin ningún "gesto" del filtro adentro. Las marcas de 0 s (toque
    de la barra sin mantenerla) se listan aparte y no cuentan como gesto.

Dibuja, para cada momento pedido, las cuatro señales filtradas (8-30 Hz) con las marcas de la barra, el "gesto" del filtro,
los parpadeos del SDK y los eventos de mandíbula, y deja la imagen en filtro/capturas.

Uso:
  python filtro/analisis/captura_momento.py                       # última sesión, momentos de sus falsos positivos
  python filtro/analisis/captura_momento.py --sesion yo_2_cejas   # parte del nombre de la sesión
  python filtro/analisis/captura_momento.py --t 117.8 32.4        # momentos concretos (segundos)
"""
import argparse
import glob
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import signal

AQUI = Path(__file__).resolve().parent
FILTRO = AQUI.parent
SESIONES = FILTRO / 'sesiones'
SALIDA = FILTRO / 'capturas'
CAN = ['TP9', 'FP1', 'FP2', 'TP10']


def elegir_sesion(parte):
    archivos = sorted(glob.glob(str(SESIONES / '*_eeg.csv')), key=os.path.getmtime)
    if parte:
        archivos = [a for a in archivos if parte.lower() in os.path.basename(a).lower()]
    if not archivos:
        sys.exit("No hay sesiones que coincidan")
    return archivos[-1][:-len('_eeg.csv')]


def senales(df, fs):
    nyq = fs / 2.0
    sos = signal.butter(8, [8 / nyq, 30 / nyq], btype='band', output='sos')
    out = {}
    for c in CAN:
        x = df[c].values.astype(float)
        y, _ = signal.sosfilt(sos, x, zi=signal.sosfilt_zi(sos) * x[0])
        out[c] = y
    return out


def desfase(df, t, ev):
    """Segundos que el tiempo del archivo EEG (desde su primera muestra) va adelantado respecto del tiempo de los eventos.
    Se mide con la columna Trigger del EEG contra los inicios de marca de los eventos (gestos con duración)."""
    trig = df['Trigger'].values
    subidas = t[np.flatnonzero(np.diff(np.r_[0, trig]) == 1)]
    m = ev[ev.detector == 'marca']
    ini = m[m.evento == 'inicio'].sort_values('t_s')
    fin = m[m.evento == 'fin'].sort_values('t_s')
    largos = [a for a, b in zip(ini.t_s.values, fin.t_s.values) if b - a > 0.05]
    if len(subidas) == 0 or len(subidas) != len(largos):
        print("Aviso: no se pudo medir el desfase entre el EEG y los eventos; se supone 0 s")
        return 0.0
    return float(np.median(subidas - np.array(largos)))


def marcas(ev):
    """Lista de (n, inicio, fin) a partir de los eventos 'marca'."""
    res = []
    m = ev[ev.detector == 'marca']
    for n in sorted(m.gesto_marcado_n.dropna().unique()):
        ini = m[(m.gesto_marcado_n == n) & (m.evento == 'inicio')].t_s
        fin = m[(m.gesto_marcado_n == n) & (m.evento == 'fin')].t_s
        if len(ini) and len(fin):
            res.append((int(n), float(ini.iloc[0]), float(fin.iloc[0])))
    return res


def contar(ev, mrc):
    gestos = ev[(ev.detector == 'filtro') & (ev.evento == 'gesto')]
    fps = gestos[gestos.resultado == 'falso_positivo']
    fns, aciertos, vacias = [], [], []
    for n, ini, fin in mrc:
        if fin - ini <= 0.0:
            vacias.append((n, ini))
            continue
        dentro = gestos[(gestos.t_s >= ini) & (gestos.t_s <= fin + 0.5)]
        (aciertos if len(dentro) else fns).append((n, ini, fin))
    return fps, aciertos, fns, vacias


def dibujar(col_ax, t, sig, ev, mrc, t0, ancho, titulo):
    a, b = t0 - ancho, t0 + ancho
    m = (t >= a) & (t <= b)
    for i, c in enumerate(CAN):
        ax = col_ax[i]
        ax.plot(t[m], sig[c][m], color='royalblue', lw=0.8)
        lim = max(np.abs(sig[c][m]).max() * 1.15, 15)
        ax.set_ylim(-lim, lim)
        for n, ini, fin in mrc:
            if fin >= a and ini <= b:
                ax.axvspan(ini, max(fin, ini + 0.05), color='limegreen', alpha=0.25)
        for _, r in ev[(ev.t_s >= a) & (ev.t_s <= b)].iterrows():
            if r.detector == 'filtro':
                ax.axvline(r.t_s, color='red' if r.resultado == 'falso_positivo' else 'green', ls='--', lw=1.6)
            elif r.detector == 'sdk' and r.evento == 'blink':
                ax.axvline(r.t_s, color='royalblue', ls=':', lw=1.2)
            elif r.detector == 'mandibula':
                ax.plot([r.t_s], [lim * 0.9], marker='|', color='gray', ms=6)
        ax.set_xlim(a, b)
        ax.set_ylabel(f"{c} (µV)", fontsize=8)
        ax.grid(True, ls='--', alpha=0.4)
        ax.text(0.01, 0.92, f"pico {np.abs(sig[c][m]).max():.0f}", transform=ax.transAxes, fontsize=7,
                va='top', color='dimgray')
    col_ax[0].set_title(titulo, fontsize=9)
    col_ax[-1].set_xlabel('Tiempo de la sesión (s)', fontsize=8)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sesion', help="parte del nombre de la sesión (default: la última)")
    ap.add_argument('--t', type=float, nargs='+', help="momentos a mostrar, en segundos (default: los falsos positivos)")
    ap.add_argument('--ancho', type=float, default=4.0, help="segundos a cada lado del momento")
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    base = elegir_sesion(a.sesion)
    nombre = os.path.basename(base)
    df = pd.read_csv(base + '_eeg.csv')
    ev = pd.read_csv(base + '_eventos.csv')
    t = df['Timestamp'].values - df['Timestamp'].values[0]
    fs = (len(t) - 1) / t[-1]
    sig = senales(df, fs)
    ofs = desfase(df, t, ev)
    t = t - ofs      # todo queda en el tiempo de los eventos
    mrc = marcas(ev)
    fps, aciertos, fns, vacias = contar(ev, mrc)

    print(f"Sesión {nombre} | {t[-1]:.0f} s | fs {fs:.0f} Hz")
    print(f"Desfase entre el tiempo del archivo EEG y el de los eventos: {ofs:.2f} s (ya corregido en los gráficos)")
    print(f"Gestos marcados con duración: {len(aciertos) + len(fns)}  |  detectados por el filtro: {len(aciertos)}  |  "
          f"falsos negativos: {len(fns)}")
    for n, ini, fin in fns:
        print(f"   falso negativo: marca {n} ({ini:.1f}-{fin:.1f} s)")
    print(f"Marcas sin duración (no cuentan como gesto): {', '.join(f'{n} ({ini:.1f} s)' for n, ini in vacias) or 'ninguna'}")
    print(f"Falsos positivos (el filtro emitió \"gesto\" fuera de toda marca): {len(fps)}")
    for _, r in fps.iterrows():
        print(f"   {r.t_s:.1f} s")

    momentos = a.t if a.t else [float(x) for x in fps.t_s]
    paneles = [(f"Momento a los {x:.1f} s" + (" (falso positivo del filtro)" if (abs(fps.t_s - x) < 0.5).any() else ""), x)
               for x in momentos]
    if aciertos:
        n, ini, fin = aciertos[-1]
        gest = ev[(ev.detector == 'filtro') & (ev.evento == 'gesto') & (ev.t_s >= ini) & (ev.t_s <= fin + 0.5)].t_s.iloc[0]
        paneles.append((f"Para comparar: gesto marcado {n}, detectado a los {gest:.1f} s", gest))

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(CAN), len(paneles), figsize=(5.2 * len(paneles), 8), squeeze=False)
    for j, (titulo, t0) in enumerate(paneles):
        dibujar([axes[i][j] for i in range(len(CAN))], t, sig, ev, mrc, t0, a.ancho, titulo)
    fig.suptitle(f"{nombre} | verde = marca de la barra | rojo/verde discontinuo = \"gesto\" del filtro (falso positivo / acierto) | "
                 f"azul punteado = parpadeo del SDK | marca gris = mandíbula", fontsize=8)
    fig.tight_layout()
    os.makedirs(SALIDA, exist_ok=True)
    ruta = SALIDA / f"captura_{nombre}.png"
    fig.savefig(ruta, dpi=110)
    print("Captura guardada en", ruta)


if __name__ == '__main__':
    main()
