"""
Concatena los errores con un poco de contexto antes y después: falsos positivos (salida = 1 fuera de un gesto) y falsos
negativos (gesto sin ningún 1). Una columna por evento, una fila por canal.
Cada recuadro muestra la señal filtrada, base ± margen, las muestras sobre el umbral (rojo), el gesto real (gris), la
detección cruda (naranja) y los tramos en que el canal estuvo ignorado (rosa).

Con --todos genera UNA imagen por archivo (solo los de cejas y dos parpadeos con contacto bueno, ver --contacto-min);
sin --todos, la de --archivo. Las imágenes quedan en filtro/analisis/resultados/errores_<archivo>.png.

Uso:
  python filtro/ver_errores.py --archivo levantar_cejas_yo_perturbado
  python filtro/ver_errores.py --todos
  python filtro/ver_errores.py --todos --contacto-min 0 --contexto 1
"""
import argparse
import glob
import os
import sys

import numpy as np
from scipy.ndimage import binary_dilation, label

import ventanas_deslizantes as v


def analizar(ruta, p, a):
    """Simula un archivo y devuelve todo lo necesario para listar y graficar sus errores."""
    nombre = os.path.basename(ruta)[8:-4]
    df, fs = v.cargar(ruta)
    trigger = (df['Trigger'] == 1).values
    sig, base, desp = v.preparar_senales(df, fs)
    sim = v.simular(desp, fs, p)
    n = len(trigger)
    desde = int(p.skip_s * fs)
    W, paso = sim['W'], int(p.paso_s * fs)

    # margen que decidió en cada ventana (el registro guarda el ya ajustado)
    previo = {c: p.margen_inicial for c in v.CAN}
    usado, pico, clase = {}, {}, {}
    for w in sim['ventanas']:
        usado[(w['fin'], w['ch'])] = previo[w['ch']]
        previo[w['ch']] = w['margen']
        pico[(w['fin'], w['ch'])] = w['pico']
        clase[(w['fin'], w['ch'])] = w['clase']
    fines = sorted(sim['det_por_fin'])

    fn = v.evaluar(sim['salida'], trigger, fs, 0.0, desde)['fn_bloques']
    zona = binary_dilation(trigger, structure=np.ones(2 * int((p.tolerancia_s + p.mantener_s) * fs) + 1, dtype=bool))
    fuera = (np.arange(n) >= desde) & ~zona
    epis, n_fp = label((sim['salida'] & fuera).astype(int))

    eventos = []
    for i in range(1, n_fp + 1):
        idx = np.flatnonzero(epis == i)
        dets = [f for f in fines if sim['det_por_fin'][f]
                and idx[0] - int(p.mantener_s * fs) - paso <= f <= idx[-1]] or [idx[0]]
        eventos.append(('FP', dets[0] - W, dets[-1], dets[0] - W, dets[-1]))
    for ini, fin_ in fn:
        eventos.append(('FN', ini, min(fin_, ini + int(a.max_gesto * fs)), ini, fin_))
    eventos.sort(key=lambda e: e[1])
    return {'nombre': nombre, 'df': df, 'fs': fs, 'trigger': trigger, 'sig': sig, 'base': base, 'desp': desp,
            'sim': sim, 'W': W, 'paso': paso, 'usado': usado, 'pico': pico, 'clase': clase, 'fines': fines,
            'eventos': eventos, 'n': n, 'n_fp': n_fp, 'n_fn': len(fn)}


def listar(r):
    fs, t, n, W, paso = r['fs'], r['df']['Tiempo_s'].values, r['n'], r['W'], r['paso']
    for num, (tipo, a0, b0, g0, g1) in enumerate(r['eventos'], 1):
        print(f"[{num}] {tipo} en t = {t[max(a0, 0)]:.1f}-{t[min(b0, n - 1)]:.1f} s")
        sel = [f for f in r['fines'] if f > g0 and f - W < g1 + paso]
        if tipo == 'FN':
            sel = [f for f in sel if f - W < g0 + int(4 * fs) + paso]
        if not sel:
            continue
        for c in v.CAN:
            q = max(r['pico'][(f, c)] / r['usado'][(f, c)] for f in sel)
            ign = np.mean([r['clase'][(f, c)] == 'ignorado' for f in sel])
            estado = f"ignorado {100 * ign:.0f} % de las ventanas" if ign > 0 else ''
            print(f"    {c:4s} máx pico/margen {q:5.2f} {'SUPERA' if q > 1 else 'no supera'} {estado}")
        print()


def graficar(r, p, a, ruta):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fs, n, sim, trigger = r['fs'], r['n'], r['sim'], r['trigger']
    t, sig, base, desp = r['df']['Tiempo_s'].values, r['sig'], r['base'], r['desp']
    eventos = r['eventos']
    ctx = int(a.contexto * fs)
    rangos = [(max(a0 - ctx, 0), min(b0 + ctx, n)) for _, a0, b0, _, _ in eventos]
    anchos = [(b - a_) / fs for a_, b in rangos]
    ancho = min(max(4.5 * len(eventos), 8), 48)
    fig, axes = plt.subplots(len(v.CAN), len(eventos), figsize=(ancho, 11), squeeze=False,
                             gridspec_kw={'width_ratios': anchos})
    for j, ((tipo, a0, b0, g0, g1), (ra, rb)) in enumerate(zip(eventos, rangos)):
        sl = slice(ra, rb)
        for i, c in enumerate(v.CAN):
            ax = axes[i][j]
            m = sim['margen_t'][c]
            ax.plot(t[sl], sig[c][sl], color='royalblue', lw=0.8, alpha=0.7)
            ax.plot(t[sl], (base[c] + m)[sl], color='green', lw=1.5)
            ax.plot(t[sl], (base[c] - m)[sl], color='green', lw=1.5)
            exceso = desp[c][sl] > np.nan_to_num(m[sl], nan=np.inf)
            ax.scatter(t[sl][exceso], sig[c][sl][exceso], color='red', s=6, zorder=5)
            ax.fill_between(t[sl], -1e5, 1e5, where=trigger[sl], color='gray', alpha=0.25, step='mid')
            ax.fill_between(t[sl], -1e5, 1e5, where=sim['crudo'][sl], color='orange', alpha=0.18, step='mid')
            ax.fill_between(t[sl], -1e5, 1e5, where=~np.isfinite(m[sl]), color='red', alpha=0.10)
            lim = max(np.percentile(np.abs(sig[c][sl]), 99.5) * 1.2, np.nanmax(m[sl]) * 1.3, 60)
            ax.set_ylim(-lim, lim)
            ax.set_xlim(t[ra], t[rb - 1])
            ax.grid(True, ls='--', alpha=0.4)
            if j == 0:
                ax.set_ylabel(f"{c} (µV)")
            if i == 0:
                ax.set_title(f"[{j + 1}] {'FALSO POSITIVO' if tipo == 'FP' else 'FALSO NEGATIVO'} — "
                             f"t = {t[max(a0, 0)]:.1f} s", fontsize=9,
                             color='firebrick' if tipo == 'FP' else 'navy')
            if i == len(v.CAN) - 1:
                ax.set_xlabel('Tiempo (s)')
    axes[0][0].plot([], [], color='royalblue', label='señal filtrada')
    axes[0][0].plot([], [], color='green', label='base ± margen')
    axes[0][0].scatter([], [], color='red', s=6, label='muestra sobre el umbral')
    axes[0][0].fill_between([], [], color='orange', alpha=0.3, label='detección cruda = 1')
    axes[0][0].fill_between([], [], color='gray', alpha=0.4, label='gesto real')
    axes[0][0].legend(loc='upper left', fontsize=7)
    fig.suptitle(f"Errores — {r['nombre']} | k_fp {p.k_fp:g}, k_tp {p.k_tp:g}, margen mín {p.margen_min:g}, banda {v.BANDA[0]:g}-{v.BANDA[1]:g} Hz, par-min {p.par_min:g}, fp-solo {p.fp_solo:g}, coinc {p.coinc_s:g}, piso-rel {p.piso_rel:g}, frac-spk {p.frac_spk:g}, difuso-canales {p.difuso_canales:g}, tp-inutil {p.tp_inutil:g}, fp-rel {p.fp_rel:g}, parcial {p.parcial_umbral:g}, sat-veto {p.sat_veto}, rebote {p.rebote_frac:g} | "
                 f"{r['n_fp']} falsos positivos, {r['n_fn']} falsos negativos", fontsize=11)
    fig.tight_layout()
    fig.savefig(ruta, dpi=80 if len(eventos) > 8 else 100)
    plt.close(fig)


def main():
    d = v.PV()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--archivo', default='levantar_cejas_yo_perturbado')
    ap.add_argument('--todos', action='store_true', help="una imagen por archivo (cejas y dos parpadeos)")
    ap.add_argument('--contacto-min', type=float, default=0.5, help="con --todos, mínimo de Sensores_OK (0 a 1)")
    ap.add_argument('--k', type=float, default=d.k_fp, help="k de los frontales")
    ap.add_argument('--k-tp', type=float, default=d.k_tp, help="k de los temporales")
    ap.add_argument('--margen-min', type=float, default=d.margen_min)
    ap.add_argument('--coinc', type=float, default=d.coinc_s)
    ap.add_argument('--dur-min', type=float, default=d.dur_min_s)
    ap.add_argument('--contexto', type=float, default=1.0, help="segundos mostrados antes y después del evento")
    ap.add_argument('--max-gesto', type=float, default=4.0, help="un gesto sin detectar más largo que esto se recorta")
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    p = v.PV(k_fp=a.k, k_tp=a.k_tp, margen_min=a.margen_min, coinc_s=a.coinc, dur_min_s=a.dur_min)
    os.makedirs(v.SALIDA, exist_ok=True)

    if a.todos:
        rutas = []
        for ruta in sorted(glob.glob(str(v.RAIZ / 'dataset_*.csv'))):
            if v.categoria(os.path.basename(ruta)[8:-4]) not in v.CATEGORIAS or os.path.getsize(ruta) < 100:
                continue
            try:
                df, fs = v.cargar(ruta)
                if not all(c in df.columns for c in v.CAN + ['Trigger']) or len(df) < fs * (p.skip_s + 8):
                    continue
                if 'Sensores_OK' in df.columns and df['Sensores_OK'].mean() < a.contacto_min:
                    continue
            except Exception:
                continue
            rutas.append(ruta)
    else:
        rutas = [c for c in sorted(glob.glob(str(v.RAIZ / f"dataset_*{a.archivo}*.csv"))) if os.path.getsize(c) > 100]
        if not rutas:
            sys.exit(f"No hay dataset que coincida con '{a.archivo}'")
        rutas = rutas[:1]

    print(f"k_fp {p.k_fp:g}, k_tp {p.k_tp:g}, margen mín {p.margen_min:g}, coinc {p.coinc_s:g}, dur-min {p.dur_min_s:g}"
          f" | {len(rutas)} archivo(s)\n")
    tot = {'fp': 0, 'fn': 0}
    for ruta in rutas:
        r = analizar(ruta, p, a)
        tot['fp'] += r['n_fp']
        tot['fn'] += r['n_fn']
        print(f"=== {r['nombre']}: {r['n_fp']} falsos positivos, {r['n_fn']} falsos negativos")
        if not r['eventos']:
            print()
            continue
        listar(r)
        destino = v.SALIDA / f"errores_{r['nombre']}.png"
        graficar(r, p, a, destino)
        print("Gráfico guardado en", destino, "\n")
    print(f"Total: {tot['fp']} falsos positivos, {tot['fn']} falsos negativos")


if __name__ == '__main__':
    main()
