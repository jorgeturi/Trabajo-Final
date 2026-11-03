"""
Ventanas deslizantes con umbral por canal que se ajusta solo con las ventanas de ruido.
Todo lo que genera este script (gráficos, CSV) queda en la carpeta filtro/analisis/resultados.

Qué hace, con la señal de un archivo (causal, solo muestras pasadas):
  1. Filtra cada canal (pasabanda 8-30 Hz) y calcula d = |señal - base| (base = promedio de los últimos 2 s).
  2. Recorre ventanas deslizantes (--ventana s, avanzando --paso s). En cada ventana y para CADA canal mide el nivel
     (promedio de d) y el pico (máximo de d).
  3. Clasifica cada canal en cada ventana:
       "movimiento" si el pico supera el margen vigente del canal;  "ruido" si no;  "ignorado" si el canal está mucho
       más ruidoso que el otro de su grupo (se ignora hasta que vuelva a la normalidad).
  4. Ajusta el umbral de cada canal por separado:
       - solo se usan para ajustar las ventanas clasificadas como ruido (así un gesto no sube el umbral);
       - si un canal sigue en "movimiento" durante --persist s seguidos, se asume que el ruido subió y se acepta el nivel;
       - al comienzo (--calib s) se ajusta con todo, porque todavía no hay un umbral confiable.
     margen = máx( k * ruido , piso_pico * pico típico del ruido , margen_min ), con tope margen_max.
  5. Regla de detección por ventana: al menos 1 FP Y al menos 1 TP en "movimiento" (canales ignorados no cuentan).
     Si se cumple, la ventana envía 1; si no, 0.
  6. Salida: el 1 se mantiene --mantener s después de la última detección (antirrebote: si la señal vuelve a bajar no se
     apaga enseguida); si no hay detección, 0.

Evalúa contra la columna Trigger: gestos detectados, falsos negativos y falsos positivos, y mira además qué hizo la
clasificación: cuántas ventanas de ruido clasificó como ruido, cuántas usó para ajustar el umbral y cuántas ventanas con
gesto se usaron por error para ajustarlo.

Uso:
  python filtro/ventanas_deslizantes.py                              # un archivo, con gráfico
  python filtro/ventanas_deslizantes.py --archivo 8-10
  python filtro/ventanas_deslizantes.py --todos                      # tabla de todos los de cejas y dos parpadeos
  python filtro/ventanas_deslizantes.py --barrido --ventana 0.5 1 2 --k 5 6 8
"""
import argparse
from collections import deque
import itertools
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.ndimage import binary_dilation, label, maximum_filter1d
from scipy.stats import kurtosis

AQUI = Path(__file__).resolve().parent
BASE_S = 2.0                       # segundos del promedio que se resta a la señal (base) para quitar la deriva lenta
ORDEN = 8                          # orden de diseño del Butterworth pasa-banda principal (el orden total es el doble)
RAIZ = AQUI.parent.parent          # carpeta con los datasets (Trabajo Final)
SALIDA = AQUI / 'resultados'
from scipy import signal  # noqa: E402

PLANTILLA_TIPO = 'gabor'       # forma de la ráfaga para la convolución: 'gabor' (paramétrica) o 'aprendida' (promedio de ráfagas fuertes)
GABOR = (12.0, 0.1)          # frecuencia (Hz) y desvío (s) de la ráfaga paramétrica
LF_BANDA = (1.0, 6.0)        # rama paralela de baja frecuencia (Hz); se cambia con --lf-banda
BANDA = (8.0, 30.0)          # pasabanda (Hz) de la señal; se cambia con --banda
CAN = ['TP9', 'FP1', 'FP2', 'TP10']
FRONTALES = ('FP1', 'FP2')


# ==========================================
# Carga, señal y evaluación (autónomo: no depende de otros scripts)
# ==========================================
def cargar(ruta):
    df = pd.read_csv(ruta)
    df['Tiempo_s'] = df['Timestamp'] - df['Timestamp'].iloc[0]
    fs = round((len(df) - 1) / df['Tiempo_s'].iloc[-1], 2)    # sin el ruido de 1e-7 que hace que int(0.5 * fs) caiga en 109 en vez de 110
    return df, fs


def _mulin(x, a, b):
    """Pertenencia lineal: 0 hasta a, 1 desde b."""
    if b <= a:
        return float(x > a)
    return float(np.clip((x - a) / (b - a), 0.0, 1.0))


def _corr(a, b):
    a = a - a.mean()
    b = b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float(abs((a * b).sum() / d)) if d > 0 else 0.0


def relaciones_canales(desp, fin, W, fs, ratio):
    """Cómo se relacionan los canales en la ventana: correlación TP9-TP10, correlación FP1-FP2, correlación frontal-temporal
    (desfase de hasta 30 ms) y logaritmo del cociente frontal/temporal de pico/margen."""
    s = {c: desp['SIG_' + c][fin - W:fin] for c in CAN}
    m = max(int(0.03 * fs), 1)
    cft = []
    for a in FRONTALES:
        for b in ('TP9', 'TP10'):
            cft.append(max(_corr(s[a][max(0, k):W + min(0, k)], s[b][max(0, -k):W + min(0, -k)]) for k in range(-m, m + 1)))
    fd = np.log(max(ratio['FP1'], ratio['FP2'], 1e-3) / max(ratio['TP9'], ratio['TP10'], 1e-3))
    return {'tp': _corr(s['TP9'], s['TP10']), 'fp': _corr(s['FP1'], s['FP2']), 'ft': float(np.mean(cft)), 'fd': float(fd)}


def grado_canales(r, sin_fp=False, sin_tp=False):
    """Lógica difusa sobre la relación entre canales: pertenencias lineales a 'se comportan como un gesto' y promedio
    ponderado (Sugeno)."""
    def mu(x, a, b):
        return float(np.clip((x - a) / (b - a), 0.0, 1.0))
    mus = (mu(r['tp'], 0.3, 0.8), mu(r['fd'], -0.5, 0.5), mu(r['fp'], 0.1, 0.6), mu(r['ft'], 0.2, 0.7))
    pesos = (0.4, 0.3, 0.15, 0.15)
    if sin_fp or sin_tp:            # un canal ignorado: su correlación con el otro del par no informa; se reparte el peso
        pesos = (0.0 if sin_tp else 0.4, 0.3, 0.0 if sin_fp else 0.15, 0.15)
        return sum(w * m for w, m in zip(pesos, mus)) / sum(pesos)
    return sum(w * m for w, m in zip(pesos, mus))


FEATURES_SCORE = ['rFmax', 'rFmin', 'rTmax', 'rTmin', 'corrFF', 'corrTT', 'corrFT', 'incTP', 'conc3', 'curt', 'ciclos',
                  'F_b1_4', 'F_b30_45', 'F_centroide', 'sat']


def features_score(desp, fin, W, fs, ratio, margen, ruido, n_v):
    """Características de la ventana para el puntaje: fuerza de frontales y temporales (pico/margen), correlaciones entre
    canales, concentración de energía, curtosis y ciclos del frontal más activo, espectro (FFT) del mismo canal,
    incremento de los temporales en los instantes en que un frontal superó su umbral y saturación."""
    s = {c: desp['SIG_' + c][fin - W:fin] for c in CAN}
    raw = {c: desp['RAW_' + c][fin - W:fin] for c in CAN}
    top = max(FRONTALES, key=lambda c: ratio[c])
    fx = s[top]
    e = np.sort(fx ** 2)[::-1]
    P = np.abs(np.fft.rfft((raw[top] - raw[top].mean()) * np.hanning(W), n=max(256, W))) ** 2
    f = np.fft.rfftfreq(max(256, W), 1 / fs)
    hi = 0.9 * fs / 2
    m = (f >= 1) & (f <= hi)
    tot = P[m].sum() + 1e-12
    fm = np.logical_or.reduce([desp[c][fin - W:fin] > margen[c] for c in FRONTALES])
    inc = 0.0
    if fm.any():
        if n_v > 0:
            fm = binary_dilation(fm, structure=np.ones(2 * n_v + 1, dtype=bool))
        inc = max(float(desp[c][fin - W:fin][fm].mean()) / max(ruido[c] or 1e-3, 1e-3) for c in ('TP9', 'TP10'))
    return {'rFmax': max(ratio['FP1'], ratio['FP2']), 'rFmin': min(ratio['FP1'], ratio['FP2']),
            'rTmax': max(ratio['TP9'], ratio['TP10']), 'rTmin': min(ratio['TP9'], ratio['TP10']),
            'corrFF': _corr(s['FP1'], s['FP2']), 'corrTT': _corr(s['TP9'], s['TP10']),
            'corrFT': float(np.mean([_corr(s[a], s[b]) for a in FRONTALES for b in ('TP9', 'TP10')])),
            'incTP': inc, 'conc3': float(e[:3].sum() / (e.sum() + 1e-12)), 'curt': float(kurtosis(fx)),
            'ciclos': float((np.diff(np.sign(fx - fx.mean())) != 0).sum() / 2),
            'F_b1_4': float(P[(f >= 1) & (f < 4)].sum() / tot), 'F_b30_45': float(P[(f >= 30) & (f < min(45, hi))].sum() / tot),
            'F_centroide': float((f[m] * P[m]).sum() / tot),
            'sat': float(np.mean([bool(((raw[c] <= 1) | (raw[c] >= 1675)).any()) for c in CAN]))}


FEATURES_FORMA = ['bifF', 'p2pF', 'bifT']
PLANTILLA_RAFAGA = [-0.01260, -0.01063, -0.00854, -0.00523, -0.00120, 0.00229, 0.00470, 0.00683, 0.00790, 0.00823, 0.00880, 0.00924, 0.00796, 0.00662, 0.00448, 0.00191, 0.00001, -0.00033, -0.00050, 0.00001, -0.00051, -0.00155, -0.00357, -0.00518, -0.00598, -0.00621, -0.00629, -0.00686, -0.00810, -0.01008, -0.01174, -0.01229, -0.01195, -0.00884, -0.00446, 0.00158, 0.01004, 0.01803, 0.02429, 0.02833, 0.02822, 0.02730, 0.02623, 0.02540, 0.02443, 0.02008, 0.00856, -0.00386, -0.01577, -0.02659, -0.03591, -0.05107, -0.08991, -0.14417, -0.17550, -0.18259, -0.12565, 0.02785, 0.18753, 0.31611, 0.40107, 0.33316, 0.19621, 0.04809, -0.09642, -0.19304, -0.19971, -0.19630, -0.18397, -0.17267, -0.15554, -0.13194, -0.08255, -0.00670, 0.06202, 0.10562, 0.14081, 0.13701, 0.12662, 0.11844, 0.11010, 0.09465, 0.06817, 0.03694, -0.00361, -0.04216, -0.06239, -0.07574, -0.08165, -0.08322, -0.08304, -0.07927, -0.07168, -0.05311, -0.03264, -0.01114, 0.00805, 0.02253, 0.03412, 0.04328, 0.05111, 0.05716, 0.05645, 0.05111, 0.04347, 0.03190, 0.02096, 0.01363, 0.00453, -0.00454, -0.01314, -0.02250, -0.02954, -0.03281, -0.03319, -0.03194, -0.02975, -0.02725, -0.02485, -0.02104, -0.01464, -0.00908, -0.00072, 0.00727, 0.01323, 0.02001, 0.02599, 0.02954, 0.03127, 0.03159, 0.02712, 0.02036, 0.01380, 0.00508, -0.00319, -0.00876, -0.01377, -0.01762, -0.01871, -0.01933, -0.01882, -0.01693, -0.01380, -0.01045, -0.00658, -0.00259, 0.00004, 0.00201, 0.00390, 0.00525, 0.00677, 0.00806, 0.00925, 0.00945, 0.00905, 0.00850, 0.00794, 0.00698, 0.00597, 0.00533]   # forma típica (160 puntos entre -0.3 y 0.5 s) de una ráfaga fuerte, alineada en su pico
EVIDENCIAS = ['der_F', 'hp_F', 'lp_F', 'ncc_F', 'corrFT', 'corrTT', 'corrFF']
EVID_TH = {'der_F': 10.6, 'hp_F': 11.0, 'lp_F': 10.3, 'ncc_F': 0.59, 'tkeo_F': 53.0, 'rz_F': 10.3, 'corrFF': 0.69, 'corrTT': 0.86, 'corrFT': 0.41}   # percentil 90 de cada evidencia en las ventanas de reposo (parámetros iniciales)
PROTO_FEATS = ['rFmax', 'corrTT', 'corrFF', 'conc3', 'F_centroide', 'ciclos', 'incTP']


def features_forma(desp, fin, fs):
    """Forma de la onda lenta (0.5-8 Hz) en el último segundo, normalizada por el ruido del canal. Un parpadeo tiene una
    caída y una subida que cruza la base (o al revés) en menos de 0.5 s (Shachar et al. 2022; López-Ahumada et al. 2023).
    bif = menor de las dos excursiones de la secuencia; p2p = máximo menos mínimo."""
    n1, m = int(1.0 * fs), int(0.5 * fs)
    bif, p2p = {}, {}
    for c in CAN:
        seg = desp['LP_' + c][max(fin - n1, 0):fin]
        if len(seg) < 4:
            bif[c] = p2p[c] = 0.0
            continue
        seg = seg - seg.mean()
        esc = desp['LPS_' + c][fin - 1]
        i = int(np.argmin(seg)); a1 = float(seg[i:i + m + 1].max())
        amp1 = min(-seg[i], a1) if seg[i] < 0 < a1 else 0.0
        j = int(np.argmax(seg)); b1 = float(seg[j:j + m + 1].min())
        amp2 = min(seg[j], -b1) if seg[j] > 0 > b1 else 0.0
        bif[c] = max(amp1, amp2) / esc
        p2p[c] = float(seg.max() - seg.min()) / esc
    return {'bifF': max(bif[c] for c in FRONTALES), 'p2pF': max(p2p[c] for c in FRONTALES),
            'bifT': max(bif[c] for c in ('TP9', 'TP10'))}


def evidencias_ventana(desp, fin, W, fs):
    """Valores de las evidencias paralelas en la ventana (máximo sobre el grupo de canales): derivada, pasa altos, pasa bajos y
    convolución con la plantilla en los frontales, correlaciones entre canales y subida simultánea de un frontal y un
    temporal (envolvente normalizada por encima de 3 en el mismo instante)."""
    sl = slice(fin - W, fin)
    ev = {n + '_F': max(float(desp['EV_' + n + '_' + c][sl].max()) for c in FRONTALES) for n in ('der', 'hp', 'lp', 'ncc', 'tkeo', 'rz')}
    s = {c: desp['SIG_' + c][sl] for c in CAN}
    ev['favg_F'] = float(desp['EV_favg'][sl].max())
    ev['ftd_F'] = float(desp['EV_ftd'][sl].max())
    ev['corrFF'] = _corr(s['FP1'], s['FP2'])
    ev['corrTT'] = _corr(s['TP9'], s['TP10'])
    ev['corrFT'] = float(np.mean([_corr(s[a], s[b]) for a in FRONTALES for b in ('TP9', 'TP10')]))
    Fm = np.maximum(desp['EV_env_FP1'][sl], desp['EV_env_FP2'][sl])
    Tm = np.maximum(desp['EV_env_TP9'][sl], desp['EV_env_TP10'][sl])
    ev['simFT3'] = float(((Fm > 3) & (Tm > 3)).any())
    return ev


def puntaje(feat, coef):
    """Probabilidad (0 a 1) de que la ventana sea un gesto: regresión logística sobre las características estandarizadas."""
    z = sum(w * (feat[k] - mu) / sd for k, w, mu, sd in zip(coef.get('feats', FEATURES_SCORE), coef['w'], coef['mean'], coef['std']))
    return float(1.0 / (1.0 + np.exp(-(coef['b'] + z))))


# Perfiles del puntaje (regresión logística sobre FEATURES_SCORE entrenada con las ventanas candidatas de los 30 archivos de
# cejas y dos parpadeos: gesto = ventana que toca el Trigger, reposo = a más de 1.5 s). conservador: C=0.03, rescate >= 0.97;
# balanceado: C=0.3, rescate >= 0.95. Validación dejando un archivo afuera: 380/404 con 2 FP y 390/404 con 4 FP.
SCORE_PERFILES = {
    'conservador': {'hi': 0.97, 'coef': {'w': [0.520702, 0.479713, 0.0129306, 0.137456, 0.364548, 0.550967, 0.253638, 0.39858, 1.23995, -0.548453, -0.516299, 0.0439208, -0.156253, -0.0754613, -0.164827], 'mean': [3.32176, 1.92827, 1.82726, 1.32859, 0.419708, 0.706985, 0.371584, 4.10579, 0.367364, 1.94818, 6.72627, 0.649047, 0.028137, 7.31553, 0.0949367], 'std': [2.68445, 1.86907, 1.87051, 1.54019, 0.271086, 0.281022, 0.189251, 4.71846, 0.164155, 3.4699, 1.74178, 0.239533, 0.0464908, 7.13118, 0.191336], 'b': 1.84764}},
    'balanceado': {'hi': 0.95, 'coef': {'w': [0.573118, 0.66995, -0.255514, 0.237629, 0.368967, 0.552846, 0.266481, 0.651507, 1.85725, -1.03795, -0.57029, 0.0921315, -0.294836, 0.0831527, -0.195891], 'mean': [3.32176, 1.92827, 1.82726, 1.32859, 0.419708, 0.706985, 0.371584, 4.10579, 0.367364, 1.94818, 6.72627, 0.649047, 0.028137, 7.31553, 0.0949367], 'std': [2.68445, 1.86907, 1.87051, 1.54019, 0.271086, 0.281022, 0.189251, 4.71846, 0.164155, 3.4699, 1.74178, 0.239533, 0.0464908, 7.13118, 0.191336], 'b': 2.29345}},
}


_cp = SCORE_PERFILES['conservador']['coef']
PROTO_STATS = {k: (_cp['mean'][FEATURES_SCORE.index(k)], _cp['std'][FEATURES_SCORE.index(k)]) for k in PROTO_FEATS}


def centroide(seg, fs):
    """Centroide espectral (Hz) de un tramo de señal, con FFT y ventana de Hann, entre 1 Hz y 0.9 de Nyquist."""
    seg = seg - seg.mean()
    P = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n=max(256, len(seg)))) ** 2
    f = np.fft.rfftfreq(max(256, len(seg)), 1 / fs)
    m = (f >= 1) & (f <= 0.9 * fs / 2)
    return float((f[m] * P[m]).sum() / (P[m].sum() + 1e-12))


def preparar_senales(df, fs):
    """Señal filtrada, base y desplazamiento d = |señal - base| por canal. Causal: estado inicial del filtro escalado a
    la primera muestra y base = promedio de los últimos 2 s."""
    nyq = fs / 2.0
    sos = signal.butter(ORDEN, [BANDA[0] / nyq, min(BANDA[1], 0.9 * nyq) / nyq], btype='band', output='sos')
    sig, base = {}, {}
    for c in CAN:
        x = df[c].values.astype(float)
        y, _ = signal.sosfilt(sos, x, zi=signal.sosfilt_zi(sos) * x[0])
        s = pd.Series(y)
        sig[c] = s.values
        base[c] = s.rolling(window=int(fs * BASE_S), min_periods=1).mean().values
    desp = {c: np.abs(sig[c] - base[c]) for c in CAN}
    # canales virtuales: promedio frontal y diferencia frontal-temporal (rechazan el ruido común a los electrodos)
    fav = (sig['FP1'] + sig['FP2']) / 2.0
    ftd = fav - (sig['TP9'] + sig['TP10']) / 2.0
    for nom_, x_ in (('favg', fav), ('ftd', ftd)):
        d_v = np.abs(x_ - pd.Series(x_).rolling(int(fs * 2), min_periods=1).mean().values)
        e_v = pd.Series(d_v).rolling(int(10 * fs), min_periods=int(fs)).median().shift(int(0.5 * fs)).values
        desp['EV_' + nom_] = d_v / np.maximum(np.nan_to_num(e_v, nan=1e9), 1e-3)
    # pendiente (Pan-Tompkins): derivada, al cuadrado, promedio móvil de 150 ms; se guarda su raíz (µV/s) como 'E_<canal>'
    n150 = max(int(0.15 * fs), 3)
    for c in CAN:
        dy = np.diff(sig[c], prepend=sig[c][0]) * fs      # diferencia hacia atrás (causal)
        desp['E_' + c] = np.sqrt(pd.Series(dy ** 2).rolling(n150, min_periods=1).mean().values)
    # ramas paralelas normalizadas por su propio nivel de ruido (mediana móvil causal de 10 s):
    # 'HF_<canal>' = banda principal, 'LF_<canal>' = baja frecuencia (artefactos de movimiento)
    for c in CAN:
        x = df[c].values.astype(float)
        for clave, (lo, hi) in (('HF_', (BANDA[0], BANDA[1])), ('LF_', LF_BANDA)):
            sos_b = signal.butter(4, [lo / nyq, min(hi, 0.9 * nyq) / nyq], btype='band', output='sos')
            y, _ = signal.sosfilt(sos_b, x, zi=signal.sosfilt_zi(sos_b) * x[0])
            d = np.abs(y - pd.Series(y).rolling(int(fs * 2), min_periods=1).mean().values)
            esc = pd.Series(d).rolling(int(10 * fs), min_periods=int(fs)).median().shift(int(0.5 * fs)).values
            desp[clave + c] = d / np.maximum(np.nan_to_num(esc, nan=1e9), 1e-3)
    sos_lp = signal.butter(4, [0.5 / nyq, min(8.0, 0.9 * nyq) / nyq], btype='band', output='sos')
    for c in CAN:
        desp['RAW_' + c] = df[c].values.astype(float)
        desp['SIG_' + c] = sig[c]
        x_ = df[c].values.astype(float)
        lp, _ = signal.sosfilt(sos_lp, x_, zi=signal.sosfilt_zi(sos_lp) * x_[0])
        d_ = np.abs(lp - pd.Series(lp).rolling(int(fs * 2), min_periods=1).mean().values)
        esc_ = pd.Series(d_).rolling(int(10 * fs), min_periods=int(fs)).median().shift(int(0.5 * fs)).values
        desp['LP_' + c] = lp                                                  # señal lenta (0.5-8 Hz): forma del parpadeo
        desp['LPS_' + c] = np.maximum(np.nan_to_num(esc_, nan=1e9), 1e-3)    # su escala de ruido, causal
    # evidencias paralelas, cada una normalizada por la mediana de sus propios valores de los 10 s previos (ruido previo)
    def _norm(a):
        e_ = pd.Series(a).rolling(int(10 * fs), min_periods=int(fs)).median().shift(int(0.5 * fs)).values
        return a / np.maximum(np.nan_to_num(e_, nan=1e9), 1e-3)
    L_ = int(0.8 * fs)
    if PLANTILLA_TIPO == 'gabor':
        t_ = np.linspace(-0.3, 0.5, L_)
        tpl_ = np.exp(-t_ ** 2 / (2 * GABOR[1] ** 2)) * np.sin(2 * np.pi * GABOR[0] * t_)
    else:
        tpl_ = np.interp(np.linspace(-0.3, 0.5, L_), np.linspace(-0.3, 0.5, len(PLANTILLA_RAFAGA)), PLANTILLA_RAFAGA)
    tpl_ = tpl_ / np.linalg.norm(tpl_)
    hi_ = min(45.0, 0.9 * nyq)
    sos_hp = signal.butter(4, [25.0 / nyq, hi_ / nyq], btype='band', output='sos') if hi_ > 30.0 else None
    for c in CAN:
        sg_ = sig[c]
        desp['EV_der_' + c] = _norm(np.abs(np.diff(sg_, prepend=sg_[0])) * fs)           # derivada (diferencia hacia atrás: causal)
        if sos_hp is not None:                                                            # pasa altos paralelo
            x_ = desp['RAW_' + c]
            y_, _ = signal.sosfilt(sos_hp, x_, zi=signal.sosfilt_zi(sos_hp) * x_[0])
            desp['EV_hp_' + c] = _norm(np.abs(y_ - pd.Series(y_).rolling(int(fs * 2), min_periods=1).mean().values))
        else:
            desp['EV_hp_' + c] = np.zeros(len(sg_))
        desp['EV_lp_' + c] = np.abs(desp['LP_' + c] - pd.Series(desp['LP_' + c]).rolling(int(fs * 2), min_periods=1).mean().values) / desp['LPS_' + c]   # pasa bajos paralelo
        desp['EV_env_' + c] = _norm(pd.Series(desp[c]).rolling(max(int(0.05 * fs), 1), min_periods=1).max().values)   # envolvente (máximo de los últimos 50 ms)
        conv_ = signal.fftconvolve(sg_, tpl_[::-1], mode='full')[:len(sg_)]               # convolución con la plantilla de ráfaga
        en_ = np.sqrt(np.maximum(pd.Series(sg_ ** 2).rolling(L_, min_periods=1).sum().values, 1e-9))
        desp['EV_ncc_' + c] = np.abs(conv_) / en_
        # operador de energía de Teager-Kaiser en versión causal: x(t-1)^2 - x(t-2)·x(t)
        x1 = np.concatenate(([sg_[0]], sg_[:-1])); x2 = np.concatenate(([sg_[0], sg_[0]], sg_[:-2]))
        desp['EV_tkeo_' + c] = _norm(np.maximum(x1 ** 2 - x2 * sg_, 0.0))
        # puntaje z robusto frente al ruido previo (mediana y MAD de los 10 s anteriores, como FASTER y ASR)
        d_c = desp[c]
        med_ = pd.Series(d_c).rolling(int(10 * fs), min_periods=int(fs)).median().shift(int(0.5 * fs))
        mad_ = pd.Series(np.abs(d_c - med_.values)).rolling(int(10 * fs), min_periods=int(fs)).median().shift(int(0.5 * fs))
        desp['EV_rz_' + c] = (d_c - np.nan_to_num(med_.values, nan=0.0)) / np.maximum(1.4826 * np.nan_to_num(mad_.values, nan=1e9), 1e-3)
    if 'Sensores_OK' in df.columns:
        desp['OK_'] = df['Sensores_OK'].values.astype(float)
    return sig, base, desp


def evaluar(flag, trigger, fs, tol_s=0.0, desde=0):
    """Gestos detectados (flag = 1 dentro del gesto +/- tolerancia), falsos negativos y falsos positivos
    (episodios de flag = 1 fuera de toda zona de gesto)."""
    n = len(flag)
    bloques, n_gestos = label(trigger)
    m = int(tol_s * fs)
    zona = np.zeros(n, dtype=bool)
    tp_bloques, fn_bloques = [], []
    for i in range(1, n_gestos + 1):
        idx = np.flatnonzero(bloques == i)
        if idx[-1] < desde:
            continue
        a, b = max(idx[0] - m, 0), min(idx[-1] + m + 1, n)
        zona[a:b] = True
        (tp_bloques if flag[a:b].any() else fn_bloques).append((idx[0], idx[-1]))
    fuera = (np.arange(n) >= desde) & ~zona
    _, fp = label((flag & fuera).astype(int))
    minutos = fuera.sum() / fs / 60.0
    return {'gestos': len(tp_bloques) + len(fn_bloques), 'tp': len(tp_bloques), 'fn': len(fn_bloques),
            'fp': int(fp), 'fp_min': fp / minutos if minutos > 0 else 0.0, 'min_fuera': float(minutos),
            'tp_bloques': tp_bloques, 'fn_bloques': fn_bloques}


def categoria(nombre):
    n = nombre.lower()
    if 'ruido' in n:
        return 'ruido'
    if 'parp' in n:
        return 'doble parpadeo'
    if 'ojo' in n or 'gui' in n:
        return 'guiño'
    if 'ceja' in n:
        return 'cejas'
    return 'otro'


def cargar_todos(carpeta, skip_s):
    """Carga los datasets de cejas y dos parpadeos (una sola vez) con su señal filtrada."""
    import glob
    datos = []
    for ruta in sorted(glob.glob(os.path.join(str(carpeta), 'dataset_*.csv'))):
        nombre = os.path.basename(ruta)[8:-4]
        if categoria(nombre) not in CATEGORIAS:
            continue
        try:
            df, fs = cargar(ruta)
            if not all(c in df.columns for c in CAN + ['Trigger']) or len(df) < fs * (skip_s + 8):
                continue
            trigger = (df['Trigger'] == 1).values
            _, _, desp = preparar_senales(df, fs)
        except Exception:
            continue
        ok = float(df['Sensores_OK'].mean()) if 'Sensores_OK' in df.columns else float('nan')
        datos.append({'nombre': nombre, 'cat': categoria(nombre), 'fs': fs, 'trigger': trigger, 'desp': desp,
                      'sens_ok': ok})
    return datos
GRUPOS = (('FP1', 'FP2'), ('TP9', 'TP10'))
CATEGORIAS = ('cejas', 'doble parpadeo')


@dataclass
class PV:
    ventana_s: float = 0.5       # tamaño de la ventana deslizante
    paso_s: float = 0.25         # cuánto avanza la ventana
    k_fp: float = 6.0            # margen FP = k_fp * ruido
    k_tp: float = 8.0            # margen TP = k_tp * ruido
    alfa: float = 0.2            # peso de la ventana nueva al ajustar el ruido
    espera_s: float = 0.1        # tras la última muestra sobre el margen, el canal no ajusta el umbral hasta pasado este tiempo
    mem_pico_s: float = 2.0      # el pico de referencia del ruido es el mayor de los picos de ruido de los últimos mem_pico_s
    pct_calib: float = 25.0      # en la calibración se toma este percentil de las ventanas (un gesto solo suma, no define el ruido)
    persist_s: float = 1.5       # un canal en "movimiento" durante este tiempo seguido => el ruido subió (un gesto dura < 1 s)
    calib_s: float = 4.0         # al comienzo se ajusta con todas las ventanas
    skip_s: float = 2.0          # transitorio inicial del filtro
    margen_inicial: float = 300.0
    margen_min: float = 20.0
    cont_min_s: float = 0.0      # continuidad: la envolvente del canal debe quedar sobre r_env * margen durante al menos este tiempo seguido (0 = no)
    env_s: float = 0.05          # la envolvente es el máximo móvil de d en este tiempo (une los cruces por cero de la oscilación)
    r_env: float = 0.5
    lf_aprende: bool = False     # las ventanas dominadas por baja frecuencia (HF/LF < hf_lf_min) se usan de inmediato como ruido para subir el umbral
    score_coef: object = None    # coeficientes del puntaje entrenado (experimental; None = no se usa)
    score_lo: float = 0.0        # una detección con puntaje menor que esto se descarta (0 = nunca)
    evid_th: object = field(default_factory=lambda: EVID_TH)   # umbral de cada evidencia (None = capa de evidencias apagada)
    evid_k: int = 6              # rescate: al menos este número de evidencias por encima de su umbral (0 = apagado)
    evid_amin: float = 1.0       # y un frontal con pico/margen de al menos esto
    evid_lo: object = None       # pertenencia 0 de cada evidencia (dict); con evid_hi y evid_fz da el rescate difuso
    evid_hi: object = None       # pertenencia 1 de cada evidencia
    evid_fz: float = 0.0         # rescate difuso: promedio de pertenencias >= esto y un frontal con pico/margen >= evid_amin (0 = apagado)
    evid_nombres: tuple = ('der_F', 'hp_F', 'lp_F', 'ncc_F', 'corrFT', 'corrTT', 'corrFF')   # evidencias que votan
    par_min_tp: float = 0.7          # par_min propio del par temporal (-1 = el mismo que par_min; 0 = el par temporal no se exige)
    parcial_pico: bool = True   # el incremento parcial de los temporales se mide con su pico (no su promedio) en los instantes en que supera un frontal
    parcial_grado: float = 0.9   # una detección por incremento parcial debe además tener este grado de relación entre canales (0 = no se exige)
    tp_crece: float = 1.3        # un temporal cuyo pico (en los instantes en que supera un frontal) excede este factor por el mayor pico de ruido reciente 'creció' aunque no llegue a su margen (0 = apagado)
    tp_crece_f: float = 2.0      # veces su margen que debe superar el frontal para esta vía
    tp_crece_grado: float = 0.7  # grado mínimo de relación entre canales para esta vía
    grado_sin_ign: bool = True  # con un frontal ignorado, el grado de relación entre canales no usa la correlación FP1-FP2
    par_comp_tp: float = 1.3     # simétrica de par_comp: si ambos frontales superan su margen por este factor y un temporal supera par_fuerte_tp, no se exige el par temporal (0 = apagado)
    par_fuerte_tp: float = 1.0   # veces su margen que debe superar el temporal dominante en esa excepción
    persist_det_s: float = 3.0   # tiempo de movimiento sostenido para aceptarlo como ruido cuando se lo está detectando como gesto (0 = igual que persist_s)
    par_vivos: bool = False      # en las excepciones de par_comp y par_comp_tp solo cuentan los canales no ignorados del otro grupo (basta uno)
    bueno_fmin: float = 0.0      # con contacto bueno en la ventana, una deteccion exige que el frontal mas debil supere su margen por este factor (0 = no)
    bueno_tmin: float = 0.0      # ... y el temporal mas debil por este (0 = no)
    bueno_frac: float = 0.9      # contacto bueno = fraccion de muestras con Sensores_OK = 1 en la ventana
    par_fuerte: float = 1.8      # con par_comp: además el frontal dominante debe superar su margen por este factor (0 = no se exige)
    par_comp: float = 0.8        # compensación entre pares: si ambos temporales superan su margen por este factor no se exige el par frontal (0 = apagado)
    par_votos: int = 3           # con par_difuso, una ventana aceptada solo porque un canal del par 'crece' necesita al menos estos votos de las evidencias
    espera_par_s: float = 0.0    # espera de la señal del otro grupo: un frontal y un temporal que superan su umbral con hasta este tiempo de diferencia (entre ventanas) cuentan como coincidentes (0 = apagado)
    evid_veto_k: int = 0         # confirmación: una detección con menos votos que esto se descarta (0 = apagado)
    score_par: float = 0.7       # con par_difuso, una ventana aceptada solo porque un canal del par 'crece' necesita al menos este puntaje (0 = sin condición)
    score_hi2: float = 1.1       # rescate por persistencia: la ventana y la anterior con puntaje de al menos esto (>1 = no se usa)
    score_hi: float = 0.97       # una ventana no detectada con puntaje de al menos esto se detecta (>1 = nunca)
    rebote_frac: float = 0.6     # una detección a menos de rebote_s de la anterior cuyo pico/margen es menor que esta fracción del de aquella es un rebote: no da 1 (0 = no)
    rebote_s: float = 1.0
    conc_min: float = 0.0        # veto: fracción de la energía del canal más activo que cae en sus 3 muestras mayores (los gestos son una ráfaga dominante); por debajo no da 1 (0 = no)
    sat_modo: int = 0            # 0 = actual; 1 = solo canales utiles; 2 = frontales saturados y algun temporal
    tp_mayor_f: float = 0.0      # veto: pico de un temporal util > f x pico del mayor frontal (0 = no)
    sat_min: int = 2             # muestras en el limite que necesita un canal en la ventana para contar como saturado
    sat_veto: int = 3            # veto: si al menos este número de canales tiene muestras en 0 o 1682 µV (saturados) en la ventana no da 1 (0 = no)
    lax_u: float = 0.5           # un canal 'crece' si su pertenencia (lax_a..lax_b veces su ruido, medido en los instantes en que otro canal superó su umbral) llega a este valor (0 = no se usa)
    lax_a: float = 1.2
    lax_b: float = 2.5
    vent_inc_s: float = 0.1      # tolerancia (s) alrededor de esos instantes para medir el crecimiento
    par_difuso: bool = False     # par_min: un canal del par cuenta si supera su margen O si crece en ese momento
    lax_modo: bool = False       # detecta si al menos un grupo (FP o TP) superó el umbral y el otro supera O crece
    parcial_umbral: float = 0.5  # lógica difusa: frontales activos + incremento de un temporal en ese mismo instante, aunque no llegue a su umbral (0 = no)
    inc_a: float = 3.0           # incremento (veces el ruido del canal) con pertenencia 0
    inc_b: float = 5.0           # incremento con pertenencia 1
    tp_inutil: float = 300.0       # si el margen de los dos temporales supera esto (µV) no sirven (mucho ruido): se decide solo con los frontales (0 = no)
    tp_inutil_rel: float = 0.0   # temporales inútiles también si su margen supera este factor por el mayor margen frontal (0 = no)
    hist_s: float = 0.0          # histéresis: tras una detección, durante este tiempo los márgenes se multiplican por hist_f (0 = apagado)
    hist_f: float = 0.6
    tp_alguno: bool = True       # los temporales se consideran inútiles si el margen de CUALQUIERA de los dos supera tp_inutil (no de los dos)
    fp_rel: float = 1.2          # con temporales inútiles, ambos frontales deben superar su margen por este factor
    corr_tp_min: float = 0.0     # veto: correlación TP9-TP10 de la ventana por debajo de este valor (0 = no)
    fd_min: float = -9.0         # veto: log(frontal/temporal) por debajo de este valor (-9 = no)
    grado_resc_vivos: bool = False  # las vias de rescate (incremento parcial, crecimiento del temporal) tampoco cuentan la correlacion de un temporal ignorado
    grado_veto_vivos: bool = False  # el veto difuso no cuenta las correlaciones de un frontal / temporal ignorado (como ya hacen las vias de rescate)
    difuso_canales: float = 0.4  # grado mínimo (0 a 1) de la lógica difusa sobre la relación entre canales (0 = no)
    centroide_max: float = 0.0   # si el centroide espectral (FFT) del canal más activo supera este valor (Hz), la ventana no da 1 (0 = no)
    hf_lf_min: float = 0.0       # la relación (máx. banda principal)/(máx. baja frecuencia) de la ventana debe llegar a este valor para dar 1 (0 = no)
    k_pend: float = 0.0          # además de la amplitud, la pendiente (derivada integrada 150 ms) debe superar k_pend * su máximo de ruido reciente (0 = no)
    frac_spk: float = 0.1        # el margen no baja de pico_ruido + frac_spk * (pico_señal - pico_ruido) (Pan-Tompkins: 0.25; 0 = no se usa)
    alfa_spk: float = 0.125      # peso de cada pico detectado al estimar el pico típico de la señal (SPK)
    piso_rel: float = 1.5        # piso = piso_rel * mediana de los picos de ruido de los últimos piso_vent_s (0 = no se usa)
    piso_vent_s: float = 10.0
    margen_min_ch: dict = field(default_factory=dict)   # piso propio por canal (µV); el que falte usa margen_min
    margen_max: float = 1000.0
    piso_pico: float = 1.0       # el margen no baja de piso_pico * mayor pico de ruido reciente
    excl_ratio: float = 3.0      # se ignora un canal si su ruido supera esto * el del otro canal de su grupo (0 = no)
    coinc_s: float = 0.1          # un frontal y un temporal deben superar su margen a la vez (+/- coinc_s); -1 = solo por ventana
    span_min_s: float = 0.0      # un canal solo cuenta si entre su primera y su última muestra sobre el margen pasó al menos este tiempo
    exc_min_s: float = 0.0       # un canal solo cuenta para detectar si superó su margen en al menos este tiempo (suma de muestras)
    par_min: float = 1.0         # si los dos canales de un grupo están activos, el más débil debe llegar a par_min * su margen
    fp_solo: float = 4.5         # si ambos frontales superan su margen por este factor se detecta aunque ningún temporal responda (0 = no)
    dur_min_s: float = 0.0       # los instantes en que FP y TP superan el margen a la vez deben abarcar al menos este tiempo
    excl_piso: float = 10.0       # al comparar el ruido de un par, el del canal más tranquilo se toma como mínimo este valor (µV)
    mantener_s: float = 1.0      # el 1 se mantiene este tiempo después de la última detección
    tolerancia_s: float = 0.5


def mantener(x, fs, hold_s):
    """Retiene el 1: queda en 1 hasta hold_s después de la última muestra en 1 (se reactiva con cada detección)."""
    if hold_s <= 0:
        return x.copy()
    m = max(int(hold_s * fs), 1)
    c = np.cumsum(x.astype(np.int64))
    h = c.copy()
    h[m:] = c[m:] - c[:-m]
    return h > 0


def simular(desp, fs, p, registro=None, registro_ev=None):
    n = len(desp[CAN[0]])
    W = max(int(p.ventana_s * fs), 2)
    paso = max(int(p.paso_s * fs), 1)
    skip = int(p.skip_s * fs)
    primera = skip + W
    fin_calib = primera + int(p.calib_s * fs)
    n_persist = max(1, int(round(p.persist_s / p.paso_s)))
    n_persist_det = max(1, int(round(p.persist_det_s / p.paso_s)))
    kg = {c: (p.k_fp if c in FRONTALES else p.k_tp) for c in CAN}

    margen = {c: p.margen_inicial for c in CAN}
    margen_t = {c: np.full(n, p.margen_inicial) for c in CAN}
    ruido = {c: None for c in CAN}
    pico_r = {c: None for c in CAN}
    cuenta = {c: 0 for c in CAN}
    largos = {c: deque(maxlen=max(1, int(round(p.piso_vent_s / p.paso_s)))) for c in CAN}   # picos de ruido recientes
    cal_e = {c: [] for c in CAN}
    rec_e = {c: deque(maxlen=max(1, int(round(p.mem_pico_s / p.paso_s)))) for c in CAN}   # picos de pendiente en ruido
    margen_e = {c: None for c in CAN}
    spk = {c: None for c in CAN}                  # pico típico de los gestos detectados, por canal
    cal_n = {c: [] for c in CAN}                   # niveles y picos vistos durante la calibración
    cal_p = {c: [] for c in CAN}
    recientes = {c: deque(maxlen=max(1, int(round(p.mem_pico_s / p.paso_s)))) for c in CAN}
    pr_prev = [0.0]
    proto = [None]                                 # gesto típico (características estandarizadas) de esta grabación
    fuerza_det = [0.0]
    ult_grupo = {'F': -10 ** 9, 'T': -10 ** 9}      # última ventana en que cada grupo (frontales / temporales) superó su umbral
    hist_hasta = [-1]
    ult_det = [-10 ** 9, 0.0]                      # fin y pico/margen máximo de la última detección
    ultimo_exceso = {c: -10 ** 9 for c in CAN}     # última muestra de cada canal que superó su margen
    n_espera = int(p.espera_s * fs)
    ignorado = {c: False for c in CAN}
    crudo = np.zeros(n, dtype=bool)
    ventanas = []
    det_por_fin = {}
    n_coinc = int(round(p.coinc_s * fs))

    for fin in range(primera, n + 1, paso):
        calibrando = fin < fin_calib
        margen_orig = None
        if p.hist_s > 0 and fin <= hist_hasta[0]:
            margen_orig = dict(margen)                       # histéresis: umbral más bajo mientras dura el efecto de una detección
            for c_ in CAN:
                margen[c_] *= p.hist_f
        clase, sup = {}, {}
        nivel, pico = {}, {}
        sobre = {}
        for c in CAN:
            seg = desp[c][fin - W:fin]
            nivel[c], pico[c] = float(seg.mean()), float(seg.max())
            if ignorado[c]:
                clase[c], sup[c] = 'ignorado', False
            else:
                sobre[c] = seg > margen[c]
                sup[c] = pico[c] > margen[c]
                clase[c] = 'movimiento' if sup[c] else 'ruido'
                if sup[c]:
                    ultimo_exceso[c] = fin - W + int(np.flatnonzero(seg > margen[c])[-1])
        cuenta_exc = {c: int(sobre[c].sum()) if c in sobre else 0 for c in CAN}
        n_exc = int(np.ceil(p.exc_min_s * fs))
        n_span = int(np.ceil(p.span_min_s * fs))
        cont = {}
        if p.cont_min_s > 0:
            n_env, n_cont = max(int(round(p.env_s * fs)), 1), int(np.ceil(p.cont_min_s * fs))
            for c in CAN:
                env = maximum_filter1d(desp[c][fin - W:fin], n_env, mode='nearest') > p.r_env * margen[c]
                cambios = np.flatnonzero(np.diff(np.concatenate(([0], env.astype(np.int8), [0]))))
                cont[c] = int((cambios[1::2] - cambios[::2]).max()) if len(cambios) else 0
        abarca = {}
        for c in CAN:
            ix = np.flatnonzero(sobre[c]) if c in sobre else []
            abarca[c] = int(ix[-1] - ix[0] + 1) if len(ix) else 0
        pico_e = {c: float(desp['E_' + c][fin - W:fin].max()) for c in CAN} if p.k_pend > 0 else {}
        pend_ok = {c: (p.k_pend <= 0 or margen_e[c] is None or pico_e[c] > margen_e[c]) for c in CAN}
        cuenta_ok = {c: sup[c] and pend_ok[c] and cuenta_exc[c] >= n_exc and abarca[c] >= n_span
                     and (p.cont_min_s <= 0 or cont[c] >= n_cont) for c in CAN}
        det = (cuenta_ok['FP1'] or cuenta_ok['FP2']) and (cuenta_ok['TP9'] or cuenta_ok['TP10'])
        crec = {c: 0.0 for c in CAN}
        if p.lax_u > 0:
            n_v = int(round(p.vent_inc_s * fs))
            for c in CAN:
                otros = [o for o in CAN if o != c and o in sobre and sobre[o].any()]
                if ignorado[c] or ruido[c] is None or not otros:
                    continue
                m_ = np.logical_or.reduce([sobre[o] for o in otros])
                if n_v > 0:
                    m_ = binary_dilation(m_, structure=np.ones(2 * n_v + 1, dtype=bool))
                inc_ = float(desp[c][fin - W:fin][m_].mean()) / max(ruido[c], 1e-3)
                crec[c] = _mulin(inc_, p.lax_a, p.lax_b)
        crece = {c: crec[c] >= p.lax_u > 0 for c in CAN}
        if p.lax_modo and p.lax_u > 0 and not det:
            fp_exc = any(cuenta_ok[c] for c in FRONTALES)
            tp_exc = any(cuenta_ok[c] for c in ('TP9', 'TP10'))
            det = (fp_exc or tp_exc) and (fp_exc or any(crece[c] for c in FRONTALES))                 and (tp_exc or any(crece[c] for c in ('TP9', 'TP10')))
        relajado = False
        if det and (p.par_min > 0 or p.par_min_tp > 0):
            for a_, b_ in GRUPOS:
                um_ = p.par_min if (a_ in FRONTALES or p.par_min_tp < 0) else p.par_min_tp
                otros_t = [c_ for c_ in ('TP9', 'TP10') if not ignorado[c_]] if p.par_vivos else (['TP9', 'TP10'] if not (ignorado['TP9'] or ignorado['TP10']) else [])
                otros_f = [c_ for c_ in FRONTALES if not ignorado[c_]] if p.par_vivos else (list(FRONTALES) if not (ignorado['FP1'] or ignorado['FP2']) else [])
                if a_ in FRONTALES and p.par_comp > 0 and otros_t and                         min(pico[c_] / margen[c_] for c_ in otros_t) >= p.par_comp and                         max(pico[f_] / margen[f_] for f_ in FRONTALES) >= p.par_fuerte:
                    continue        # el par temporal responde completo: no se exige el par frontal
                if a_ not in FRONTALES and p.par_comp_tp > 0 and otros_f and                         min(pico[c_] / margen[c_] for c_ in otros_f) >= p.par_comp_tp and                         max(pico['TP9'] / margen['TP9'], pico['TP10'] / margen['TP10']) >= p.par_fuerte_tp:
                    continue        # ambos frontales responden y un temporal también: no se exige el par temporal
                if um_ > 0 and not ignorado[a_] and not ignorado[b_]:
                    sa_, sb_ = pico[a_] / margen[a_] >= um_, pico[b_] / margen[b_] >= um_
                    par_dif = p.par_difuso and (p.score_coef is not None or (p.evid_th is not None and p.par_votos > 0))   # necesita quien la confirme
                    ok_a = sa_ or (par_dif and crece[a_])
                    ok_b = sb_ or (par_dif and crece[b_])
                    if not (ok_a and ok_b):
                        det = False
                    elif not (sa_ and sb_):
                        relajado = True
        if det and p.coinc_s >= 0:
            # los dos grupos deben superar el margen en el mismo instante (con tolerancia de coinc_s)
            vacio = np.zeros(W, dtype=bool)
            f = np.logical_or.reduce([sobre.get(c, vacio) for c in FRONTALES])
            t_ = np.logical_or.reduce([sobre.get(c, vacio) for c in ('TP9', 'TP10')])
            if n_coinc > 0:
                f = binary_dilation(f, structure=np.ones(2 * n_coinc + 1, dtype=bool))
            cm = np.flatnonzero(f & t_)
            det = bool(len(cm)) and (cm[-1] - cm[0] + 1) >= p.dur_min_s * fs
        if not det and p.fp_solo > 0 and not ignorado['FP1'] and not ignorado['FP2']:
            det = pico['FP1'] > p.fp_solo * margen['FP1'] and pico['FP2'] > p.fp_solo * margen['FP2']
        lf_dom = False
        if p.hf_lf_min > 0:
            hf = max(float(desp['HF_' + c][fin - W:fin].max()) for c in CAN)
            lf = max(float(desp['LF_' + c][fin - W:fin].max()) for c in CAN)
            lf_dom = hf / max(lf, 1e-3) < p.hf_lf_min
            if lf_dom:
                det = False
        f_ahora = any(cuenta_ok[c] for c in FRONTALES)
        t_ahora = any(cuenta_ok[c] for c in ('TP9', 'TP10'))
        if not det and p.espera_par_s > 0 and not calibrando:
            lim = p.espera_par_s * fs
            det = (f_ahora and fin - ult_grupo['T'] <= lim) or (t_ahora and fin - ult_grupo['F'] <= lim)
        if f_ahora:
            ult_grupo['F'] = fin
        if t_ahora:
            ult_grupo['T'] = fin
        if det and (p.corr_tp_min > 0 or p.fd_min > -9 or p.difuso_canales > 0):
            rc = relaciones_canales(desp, fin, W, fs, {c: pico[c] / margen[c] for c in CAN})
            if p.corr_tp_min > 0 and rc['tp'] < p.corr_tp_min:
                det = False
            if p.fd_min > -9 and rc['fd'] < p.fd_min:
                det = False
            if p.difuso_canales > 0 and grado_canales(rc, p.grado_veto_vivos and bool(ignorado['FP1'] or ignorado['FP2']),
                                                      p.grado_veto_vivos and bool(ignorado['TP9'] or ignorado['TP10'])) < p.difuso_canales:
                det = False
        agr_ = any if p.tp_alguno else all
        inutiles = (p.tp_inutil > 0 and agr_(margen[c] >= p.tp_inutil for c in ('TP9', 'TP10'))) or                    (p.tp_inutil_rel > 0 and agr_(margen[c] >= p.tp_inutil_rel * max(margen['FP1'], margen['FP2']) for c in ('TP9', 'TP10')))
        if not det and inutiles:
            vivos = [c for c in FRONTALES if not ignorado[c]]
            det = bool(vivos) and all(pico[c] > p.fp_rel * margen[c] for c in vivos)                 and any(sup[c] for c in vivos) and len(vivos) == len(FRONTALES)
        if not det and p.parcial_umbral > 0:
            vivos = [c for c in FRONTALES if not ignorado[c]]
            tp_c = [c for c in ('TP9', 'TP10') if not ignorado[c]]
            if vivos and tp_c and all(ruido[c] is not None for c in CAN):
                fm = np.logical_or.reduce([sobre[c] for c in vivos])
                if fm.any():
                    if n_coinc > 0:
                        fm = binary_dilation(fm, structure=np.ones(2 * n_coinc + 1, dtype=bool))
                    inc = max(float((desp[c][fin - W:fin][fm].max() if p.parcial_pico else desp[c][fin - W:fin][fm].mean())) / max(ruido[c], 1e-3) for c in tp_c)
                    f_amb = min(_mulin(pico[c] / margen[c], 0.8, 2.0) for c in vivos)
                    det = float(np.sqrt(f_amb * _mulin(inc, p.inc_a, p.inc_b))) >= p.parcial_umbral
                    if det and p.parcial_grado > 0:
                        rc_ = relaciones_canales(desp, fin, W, fs, {c: pico[c] / margen[c] for c in CAN})
                        det = grado_canales(rc_, p.grado_sin_ign and (ignorado['FP1'] or ignorado['FP2']), p.grado_resc_vivos and bool(ignorado['TP9'] or ignorado['TP10'])) >= p.parcial_grado
        if not det and p.tp_crece > 0 and not calibrando:
            vivos = [c for c in FRONTALES if not ignorado[c] and sup[c] and pico[c] / margen[c] >= p.tp_crece_f]
            if vivos:
                fm = np.logical_or.reduce([sobre[c] for c in FRONTALES if c in sobre])
                if n_coinc > 0:
                    fm = binary_dilation(fm, structure=np.ones(2 * n_coinc + 1, dtype=bool))
                if fm.any() and any((not ignorado[c]) and pico_r[c] is not None and float(desp[c][fin - W:fin][fm].max()) > p.tp_crece * pico_r[c] for c in ('TP9', 'TP10')):
                    det = True
                    if p.tp_crece_grado > 0:
                        rc_ = relaciones_canales(desp, fin, W, fs, {c: pico[c] / margen[c] for c in CAN})
                        det = grado_canales(rc_, p.grado_sin_ign and (ignorado['FP1'] or ignorado['FP2']), p.grado_resc_vivos and bool(ignorado['TP9'] or ignorado['TP10'])) >= p.tp_crece_grado
        if det and p.conc_min > 0:
            c_top = max(CAN, key=lambda c: pico[c] / margen[c])
            e_ = np.sort(desp['SIG_' + c_top][fin - W:fin] ** 2)[::-1]
            if e_[:3].sum() / (e_.sum() + 1e-9) < p.conc_min:
                det = False
        if det and p.sat_veto > 0:
            sat_c = {c: int(((desp['RAW_' + c][fin - W:fin] <= 1) | (desp['RAW_' + c][fin - W:fin] >= 1675)).sum()) >= p.sat_min for c in CAN}
            if p.sat_modo == 1:             # solo cuentan los canales utiles (margen <= tp_inutil): un sensor muerto roza el limite por ruido
                utiles = [c for c in CAN if margen[c] <= p.tp_inutil]
                if sum(sat_c[c] for c in utiles) >= min(p.sat_veto, max(2, len(utiles))):
                    det = False
            elif p.sat_modo == 2:           # contacto perdido de verdad: los dos frontales saturados y algun temporal
                if sat_c['FP1'] and sat_c['FP2'] and (sat_c['TP9'] or sat_c['TP10']):
                    det = False
            elif sum(sat_c.values()) >= p.sat_veto:
                det = False
        if det and p.tp_mayor_f > 0:        # un movimiento de cabeza o la mandibula activan los temporales tanto o mas que los frontales
            tps = [pico[c] for c in ('TP9', 'TP10') if not ignorado[c] and margen[c] <= p.tp_inutil]
            if tps and max(tps) > p.tp_mayor_f * max(pico['FP1'], pico['FP2']):
                det = False
        if det and p.centroide_max > 0:
            c_top = max(CAN, key=lambda c: pico[c] / margen[c])
            det = centroide(desp['RAW_' + c_top][fin - W:fin], fs) <= p.centroide_max
        if registro_ev is not None or p.evid_th is not None or p.evid_fz > 0:
            evd = evidencias_ventana(desp, fin, W, fs)
            if registro_ev is not None and fin >= primera:
                registro_ev.append(dict(evd, fin=fin, det=bool(det), amp_F=max(pico[c] / margen[c] for c in FRONTALES),
                                        amp_T=max(pico[c] / margen[c] for c in ('TP9', 'TP10'))))
            if p.evid_th is not None or p.evid_fz > 0:
                votos = sum(evd[n] > p.evid_th[n] for n in p.evid_nombres) if p.evid_th is not None else 0
                f_amp = max(pico[c] / margen[c] for c in FRONTALES)
                if not det and not calibrando and p.evid_k > 0 and f_amp >= p.evid_amin and votos >= p.evid_k:
                    det = True
                elif not det and not calibrando and p.evid_fz > 0 and p.evid_lo is not None and f_amp >= p.evid_amin:
                    gr = float(np.mean([_mulin(evd[n], p.evid_lo[n], p.evid_hi[n]) for n in p.evid_nombres]))
                    det = gr >= p.evid_fz
                elif det and p.evid_veto_k > 0 and votos < p.evid_veto_k:
                    det = False
                elif det and relajado and p.par_votos > 0 and votos < p.par_votos:
                    det = False     # el par solo 'crecía': hacen falta votos de las evidencias que lo confirmen
        if registro is not None or p.score_coef is not None:
            ratio_all = {c: pico[c] / margen[c] for c in CAN}
            if max(ratio_all['FP1'], ratio_all['FP2']) >= 0.7:
                ft = features_score(desp, fin, W, fs, ratio_all, margen, ruido, int(round(0.1 * fs)))
                usa = p.score_coef.get('feats', []) if p.score_coef is not None else []
                if registro is not None or any(k in usa for k in FEATURES_FORMA):
                    ft.update(features_forma(desp, fin, fs))
                # perfil propio del usuario (Agarwal y Sivakumar 2019): distancia al gesto típico visto en esta grabación
                if registro is not None or 'dproto' in usa:
                    z_ = np.array([(ft[k] - PROTO_STATS[k][0]) / PROTO_STATS[k][1] for k in PROTO_FEATS])
                    ft['dproto'] = float(np.sqrt(np.mean((z_ - proto[0]) ** 2))) if proto[0] is not None else 1.5
                if registro is not None:
                    registro.append(dict(ft, fin=fin, det=bool(det)))
                if p.score_coef is not None:
                    pr_ = puntaje(ft, p.score_coef)
                    if det and relajado and pr_ < p.score_par:
                        det = False
                    if det and pr_ < p.score_lo:
                        det = False
                    elif not det and not calibrando and (pr_ >= p.score_hi or (pr_ >= p.score_hi2 and pr_prev[0] >= p.score_hi2)):
                        det = True
                    pr_prev[0] = pr_
            else:
                pr_prev[0] = 0.0
        if det and (p.bueno_fmin > 0 or p.bueno_tmin > 0) and 'OK_' in desp and desp['OK_'][fin - W:fin].mean() >= p.bueno_frac:
            if min(pico[c] / margen[c] for c in FRONTALES) < p.bueno_fmin or min(pico[c] / margen[c] for c in ('TP9', 'TP10')) < p.bueno_tmin:
                det = False
        if det and p.rebote_frac > 0:
            fuerza = max(pico[c] / margen[c] for c in CAN)
            if fin - ult_det[0] <= p.rebote_s * fs and fuerza < p.rebote_frac * ult_det[1]:
                det = False
            else:
                ult_det[0], ult_det[1] = fin, fuerza
        if det and 'dproto' in (p.score_coef.get('feats', []) if p.score_coef is not None else []) or (det and registro is not None):
            rr_ = {c: pico[c] / margen[c] for c in CAN}
            if max(rr_['FP1'], rr_['FP2']) >= 2.0 and fin >= primera:
                ft2 = features_score(desp, fin, W, fs, rr_, margen, ruido, int(round(0.1 * fs)))
                z2 = np.array([(ft2[k] - PROTO_STATS[k][0]) / PROTO_STATS[k][1] for k in PROTO_FEATS])
                proto[0] = z2 if proto[0] is None else 0.8 * proto[0] + 0.2 * z2
        det_por_fin[fin] = bool(det)
        if det and p.hist_s > 0:
            hist_hasta[0] = fin + int(p.hist_s * fs)
        if det:
            for c in CAN:
                if sup[c]:
                    spk[c] = pico[c] if spk[c] is None else (1 - p.alfa_spk) * spk[c] + p.alfa_spk * pico[c]
        if det:
            crudo[fin:fin + paso] = True

        if p.k_pend > 0:
            for c in CAN:
                if calibrando:
                    cal_e[c].append(pico_e[c])
                    rec_e[c].clear()
                    rec_e[c].append(float(np.percentile(cal_e[c], p.pct_calib)))
                elif clase[c] == 'ruido':
                    rec_e[c].append(pico_e[c])
                if rec_e[c]:
                    margen_e[c] = p.k_pend * max(rec_e[c])

        if margen_orig is not None:
            margen.update(margen_orig)
        # ajuste del umbral de cada canal, por separado
        for c in CAN:
            if ruido[c] is None:
                uso, motivo = True, 'calibración'
            elif calibrando:
                uso, motivo = True, 'calibración'
            elif clase[c] == 'ruido' and fin - W < ultimo_exceso[c] + n_espera:
                uso, motivo = False, 'espera'          # cola y rebotes del gesto: no se aprende de acá
            elif clase[c] == 'ruido':
                uso, motivo = True, 'ruido'
                cuenta[c] = 0
            elif clase[c] == 'movimiento' and lf_dom and p.lf_aprende:
                uso, motivo = True, 'ruido'            # ruido de baja frecuencia: se aprende ya, sin esperar la persistencia
                cuenta[c] = 0
            elif clase[c] == 'ignorado':
                uso, motivo = True, 'seguimiento'      # se sigue su nivel para saber cuándo vuelve a la normalidad
            else:
                cuenta[c] += 1
                if cuenta[c] >= (n_persist_det if (det and p.persist_det_s > 0) else n_persist):
                    uso, motivo = True, 'ruido sostenido'
                    cuenta[c] = 0
                else:
                    uso, motivo = False, 'movimiento'
            if uso:
                if motivo == 'calibración':
                    # percentil bajo de lo visto hasta ahora: si hay un gesto en la calibración no define el ruido
                    cal_n[c].append(nivel[c])
                    cal_p[c].append(pico[c])
                    ruido[c] = float(np.percentile(cal_n[c], p.pct_calib))
                    pico_r[c] = float(np.percentile(cal_p[c], p.pct_calib))
                    recientes[c].clear()
                    recientes[c].append(pico_r[c])
                elif motivo in ('ruido sostenido', 'seguimiento'):
                    ruido[c], pico_r[c] = nivel[c], pico[c]
                    recientes[c].clear()
                    recientes[c].append(pico[c])
                else:
                    ruido[c] = (1 - p.alfa) * ruido[c] + p.alfa * nivel[c]
                    # el margen queda por encima del mayor pico de ruido reciente, no de su promedio, y lo olvida
                    # cuando ese pico sale de la memoria
                    recientes[c].append(pico[c])
                    pico_r[c] = max(recientes[c])
                if motivo in ('calibración', 'ruido'):
                    largos[c].append(pico[c])
                valor = max(kg[c] * ruido[c], p.piso_pico * pico_r[c])
                if p.frac_spk > 0 and spk[c] is not None and spk[c] > pico_r[c]:
                    valor = max(valor, pico_r[c] + p.frac_spk * (spk[c] - pico_r[c]))
                piso = p.margen_min_ch.get(c, p.margen_min)
                if p.piso_rel > 0 and largos[c]:
                    piso = max(piso, p.piso_rel * float(np.median(largos[c])))
                margen[c] = float(np.clip(valor, piso, p.margen_max))
            ventanas.append({'t': fin / fs, 'fin': fin, 'ch': c, 'clase': clase[c], 'uso': uso, 'motivo': motivo,
                             'nivel': nivel[c], 'pico': pico[c], 'ruido': ruido[c], 'margen': margen[c]})

        # exclusión: un canal mucho más ruidoso que el otro de su grupo
        if p.excl_ratio > 0 and not calibrando:
            for a_, b_ in GRUPOS:
                if ruido[a_] is not None and ruido[b_] is not None:
                    ignorado[a_] = ruido[a_] > p.excl_ratio * max(ruido[b_], p.excl_piso)
                    ignorado[b_] = ruido[b_] > p.excl_ratio * max(ruido[a_], p.excl_piso)
        for c in CAN:
            margen_t[c][fin:fin + paso] = np.nan if ignorado[c] else margen[c]

    salida = mantener(crudo, fs, p.mantener_s)
    return {'crudo': crudo, 'salida': salida, 'margen_t': margen_t, 'ventanas': ventanas, 'W': W,
            'det_por_fin': det_por_fin}


def pulsos(x):
    s = np.asarray(x, dtype=bool)
    return np.flatnonzero(s[1:] & ~s[:-1]) + 1


def evaluar_sim(sim, trigger, fs, p):
    desde = int(p.skip_s * fs)
    e_s = evaluar(sim['salida'], trigger, fs, 0.0, desde)
    e_sfp = evaluar(sim['salida'], trigger, fs, p.tolerancia_s + p.mantener_s, desde)
    e_c = evaluar(sim['crudo'], trigger, fs, 0.0, desde)
    e_cfp = evaluar(sim['crudo'], trigger, fs, p.tolerancia_s, desde)
    tol = int(p.tolerancia_s * fs)
    trig_dil = binary_dilation(trigger, structure=np.ones(2 * tol + 1, dtype=bool))
    W = sim['W']
    por_canal = {c: {'ruido': [0, 0, 0], 'gesto': [0, 0, 0], 'ign': 0, 'tot': 0} for c in CAN}
    ventana_det = {'gesto': [0, 0], 'ruido': [0, 0]}   # [ventanas, con detección]
    det_por_fin = {}
    for v in sim['ventanas']:
        clase_v = 'gesto' if trig_dil[v['fin'] - W:v['fin']].any() else 'ruido'
        d = por_canal[v['ch']]
        d['tot'] += 1
        if v['clase'] == 'ignorado':
            d['ign'] += 1
        b = d[clase_v]
        b[0] += 1
        b[1] += int(v['clase'] == 'ruido')      # clasificada como ruido
        b[2] += int(v['uso'] and v['motivo'] in ('ruido', 'ruido sostenido', 'calibración'))   # usada para ajustar
        v['verdad'] = clase_v
    # detección por ventana (la regla se aplica sobre las clases de los cuatro canales)
    por_fin = {}
    for v in sim['ventanas']:
        por_fin.setdefault(v['fin'], {})[v['ch']] = v
    for fin, cs in por_fin.items():
        det = sim['det_por_fin'][fin]
        ct = cs['TP9']['verdad']
        ventana_det[ct][0] += 1
        ventana_det[ct][1] += int(det)
    return {'e_s': e_s, 'e_sfp': e_sfp, 'e_c': e_c, 'e_cfp': e_cfp, 'por_canal': por_canal,
            'ventana_det': ventana_det,
            'pul_c': int(trigger[pulsos(sim['crudo'])].sum()), 'pul_s': int(trigger[pulsos(sim['salida'])].sum())}


def pct(a, b):
    return 100.0 * a / b if b else float('nan')


def reporte(nombre, fs, dur, p, ev, sim):
    es, esfp, ec, ecfp = ev['e_s'], ev['e_sfp'], ev['e_c'], ev['e_cfp']
    tp = max(es['tp'], 1)
    print(f"Archivo: {nombre} | fs {fs:.0f} Hz | {dur:.0f} s")
    print(f"Ventana {p.ventana_s:g} s (paso {p.paso_s:g} s), k_fp {p.k_fp:g}, k_tp {p.k_tp:g}, ajuste alfa {p.alfa:g}, "
          f"persistencia {p.persist_s:g} s, retención de la salida {p.mantener_s:g} s")
    print()
    print("DETECCIÓN (regla: al menos 1 FP y 1 TP en movimiento; si no, salida 0)")
    print(f"  gestos reales: {es['gestos']}  |  detectados (salida = 1 dentro del gesto): {es['tp']}  |  "
          f"falsos negativos: {es['fn']}")
    print(f"  falsos positivos (episodios de salida = 1 fuera de los gestos): {esfp['fp']}  "
          f"({esfp['fp_min']:.1f} por minuto)   [sin retención serían {ecfp['fp']}]")
    print(f"  pulsos por gesto detectado: ventana cruda {ev['pul_c'] / tp:.1f} -> salida retenida {ev['pul_s'] / tp:.1f}")
    vd = ev['ventana_det']
    print(f"  ventanas con detección: {pct(vd['gesto'][1], vd['gesto'][0]):.0f} % de las que tocan un gesto, "
          f"{pct(vd['ruido'][1], vd['ruido'][0]):.1f} % de las de ruido")
    print()
    print("CLASIFICACIÓN DE VENTANAS POR CANAL (la verdad solo se usa para evaluar, nunca para decidir)")
    print(f"  {'canal':5s} | {'ventanas de ruido':^36s} | {'ventanas con gesto':^38s} | {'ignorado':>8s}")
    print(f"  {'':5s} | {'n':>5s} {'clasif. ruido':>13s} {'ajustan umbral':>14s} | {'n':>5s} {'clasif. movim.':>14s} "
          f"{'ajustan (error)':>15s} | {'%':>8s}")
    for c in CAN:
        d = ev['por_canal'][c]
        r, g = d['ruido'], d['gesto']
        print(f"  {c:5s} | {r[0]:5d} {pct(r[1], r[0]):12.0f}% {pct(r[2], r[0]):13.0f}% | {g[0]:5d} "
              f"{pct(g[0] - g[1], g[0]):13.0f}% {pct(g[2], g[0]):14.0f}% | {pct(d['ign'], d['tot']):7.1f}%")
    print()
    print("UMBRAL (margen en µV) por canal: mínimo / mediana / máximo durante el archivo")
    for c in CAN:
        m = sim['margen_t'][c]
        m = m[np.isfinite(m)]
        print(f"  {c:5s} {np.min(m):6.0f} / {np.median(m):6.0f} / {np.max(m):6.0f}")


def graficar(df, sig, base, trigger, sim, ev, p, fs, titulo, ruta, mostrar):
    import matplotlib
    if not mostrar:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    t = df['Tiempo_s'].values
    fig, axes = plt.subplots(len(CAN) + 2, 1, figsize=(15, 15), sharex=True,
                             gridspec_kw={'height_ratios': [3, 3, 3, 3, 1.8, 2.2]})
    colores = {'TP9': 'tab:blue', 'FP1': 'tab:orange', 'FP2': 'tab:green', 'TP10': 'tab:red'}
    estilo = {'calibración': ('gold', '|', 'Ventana usada para calibrar'),
              'ruido': ('limegreen', '|', 'Ruido: ajusta el umbral'),
              'ruido sostenido': ('royalblue', 'o', 'Ruido sostenido: sube el umbral'),
              'espera': ('purple', '|', 'Espera tras el gesto: NO ajusta el umbral'),
              'movimiento': ('orangered', 'x', 'Movimiento: NO ajusta el umbral'),
              'seguimiento': ('gray', '.', 'Canal ignorado')}
    for i, c in enumerate(CAN):
        ax = axes[i]
        m = sim['margen_t'][c]
        lim = max(np.percentile(np.abs(sig[c]), 99.7) * 1.15, (np.nanpercentile(m, 90) if np.isfinite(m).any() else 0) * 1.3)
        ax.plot(t, sig[c], color='royalblue', alpha=0.55, lw=0.8, label='Señal filtrada')
        ax.plot(t, base[c] + m, color='green', lw=1.5, label='Umbral (±margen)')
        ax.plot(t, base[c] - m, color='green', lw=1.5)
        ax.fill_between(t, -lim, lim, where=trigger, color='gray', alpha=0.15, label='Gesto real (trigger)')
        ign = ~np.isfinite(m)
        if ign.any():
            ax.fill_between(t, -lim, lim, where=ign, color='red', alpha=0.10, label='Canal ignorado por ruido')
        reg = [v for v in sim['ventanas'] if v['ch'] == c]
        for clave, (color, marca, etiqueta) in estilo.items():
            xs = [v['t'] for v in reg if v['motivo'] == clave]
            if xs:
                ax.scatter(xs, [-lim * 0.92] * len(xs), color=color, marker=marca, s=22, zorder=5,
                           label=etiqueta if i == 0 else None)
        ax.set_ylim(-lim, lim)
        ax.set_title(f"Canal {c}", fontsize=10)
        ax.set_ylabel('µV')
        ax.grid(True, ls='--', alpha=0.4)
        if i == 0:
            ax.legend(loc='upper right', fontsize=7, ncol=3)

    ax = axes[len(CAN)]
    zona = binary_dilation(trigger, structure=np.ones(2 * int((p.tolerancia_s + p.mantener_s) * fs) + 1, dtype=bool))
    ax.fill_between(t, 0, 1, where=trigger, color='gray', alpha=0.25, step='mid', label='Gesto real (trigger)')
    ax.fill_between(t, 0, 0.45, where=sim['crudo'], color='steelblue', alpha=0.9, step='mid',
                    label='Detección por ventana (cruda)')
    ax.fill_between(t, 0.55, 1.0, where=sim['salida'] & zona, color='green', alpha=0.9, step='mid',
                    label='Salida = 1 (positivo)')
    ax.fill_between(t, 0.55, 1.0, where=sim['salida'] & ~zona, color='red', alpha=0.9, step='mid',
                    label='Salida = 1 fuera del gesto (falso positivo)')
    for a, b in ev['e_s']['fn_bloques']:
        ax.axvspan(t[a], t[b], facecolor='none', edgecolor='red', hatch='///', lw=0.5)
    ax.plot([], [], color='red', alpha=0, label='Gesto sin 1 (falso negativo): rayado')
    ax.set_yticks([0.22, 0.78])
    ax.set_yticklabels(['cruda', 'salida'])
    ax.legend(loc='upper right', fontsize=7, ncol=3)

    ax = axes[len(CAN) + 1]
    for c in CAN:
        ax.plot(t, sim['margen_t'][c], color=colores[c], lw=1.4, label=f'Margen {c}')
    ax.set_ylabel('Margen (µV)')
    ax.set_xlabel('Tiempo (s)')
    ax.grid(True, ls='--', alpha=0.4)
    ax.legend(loc='upper right', fontsize=7, ncol=4)
    fig.suptitle(titulo, fontsize=10)
    fig.tight_layout()
    if mostrar:
        plt.show()
    else:
        fig.savefig(ruta, dpi=105)
        print("Gráfico guardado en", ruta)
    plt.close(fig)


def guardar_csv(ventanas, ruta):
    pd.DataFrame(ventanas).drop(columns=['fin']).to_csv(ruta, index=False)
    print("Ventanas guardadas en", ruta)


def evaluar_todos(datos, p):
    res = []
    for x in datos:
        sim = simular(x['desp'], x['fs'], p)
        ev = evaluar_sim(sim, x['trigger'], x['fs'], p)
        res.append({'nombre': x['nombre'], 'cat': x['cat'], 'fs': x['fs'], 'ok': x['sens_ok'], 'ev': ev})
    return res


def resumen_todos(res):
    g = sum(r['ev']['e_s']['gestos'] for r in res)
    tp = sum(r['ev']['e_s']['tp'] for r in res)
    fp = sum(r['ev']['e_sfp']['fp'] for r in res)
    mf = sum(r['ev']['e_sfp']['min_fuera'] for r in res)
    fpc = sum(r['ev']['e_cfp']['fp'] for r in res)
    pc = sum(r['ev']['pul_c'] for r in res)
    ps = sum(r['ev']['pul_s'] for r in res)
    rr = gg = ru = gu = 0
    for r in res:
        for c in CAN:
            d = r['ev']['por_canal'][c]
            rr += d['ruido'][0]
            ru += d['ruido'][2]
            gg += d['gesto'][0]
            gu += d['gesto'][2]
    return {'gestos': g, 'tp': tp, 'det': pct(tp, g), 'fp': fp, 'fp_min': fp / mf if mf else 0.0, 'fp_crudo': fpc,
            'pul_c': pc / tp if tp else 0.0, 'pul_s': ps / tp if tp else 0.0,
            'ruido_ajusta': pct(ru, rr), 'gesto_ajusta': pct(gu, gg)}


def main():
    d = PV()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--archivo', default='dosparpadeos_yo_perturbado', help="parte del nombre del dataset")
    ap.add_argument('--todos', action='store_true', help="tabla con todos los archivos de cejas y dos parpadeos")
    ap.add_argument('--barrido', action='store_true', help="barre --ventana y --k sobre todos los archivos")
    ap.add_argument('--mostrar', action='store_true', help="abre el gráfico en vez de guardarlo")
    ap.add_argument('--no-grafico', action='store_true')
    ap.add_argument('--ventana', type=float, nargs='+', default=[d.ventana_s], help="tamaño(s) de ventana en s")
    ap.add_argument('--paso', type=float, default=d.paso_s)
    ap.add_argument('--k', type=float, nargs='+', default=[d.k_fp], help="k de los frontales (FP1, FP2)")
    ap.add_argument('--k-tp', type=float, default=d.k_tp, help="k de los temporales (TP9, TP10)")
    ap.add_argument('--alfa', type=float, default=d.alfa)
    ap.add_argument('--persist', type=float, default=d.persist_s)
    ap.add_argument('--calib', type=float, default=d.calib_s)
    ap.add_argument('--margen-min', type=float, default=d.margen_min)
    ap.add_argument('--piso-pico', type=float, default=d.piso_pico)
    ap.add_argument('--excl-ratio', type=float, default=d.excl_ratio)
    ap.add_argument('--espera', type=float, default=d.espera_s)
    ap.add_argument('--coinc', type=float, default=d.coinc_s,
                    help="tolerancia (s) de la coincidencia FP-TP en el mismo instante; -1 = sin exigirla")
    ap.add_argument('--dur-min', type=float, default=d.dur_min_s,
                    help="duración mínima (s) de la superación simultánea FP-TP (de la primera a la última muestra)")
    ap.add_argument('--banda', type=float, nargs=2, default=list(BANDA), metavar=('LO', 'HI'), help="pasabanda en Hz")
    ap.add_argument('--fp-solo', type=float, default=d.fp_solo, help="detecta solo con los dos frontales si superan el margen por este factor (0 = no)")
    ap.add_argument('--exc-min', type=float, default=d.exc_min_s, help="tiempo mínimo (s) sobre el margen para que un canal cuente")
    ap.add_argument('--span-min', type=float, default=d.span_min_s, help="tiempo mínimo (s) entre la primera y la última muestra sobre el margen de cada canal")
    ap.add_argument('--par-min', type=float, default=d.par_min, help="el canal más débil de un par debe llegar a esta fracción de su margen (0 = no)")
    ap.add_argument('--excl-piso', type=float, default=d.excl_piso, help="ruido mínimo (µV) del canal más tranquilo al decidir si ignorar un canal")
    ap.add_argument('--margen-min-ch', type=float, nargs=4, metavar=('TP9', 'FP1', 'FP2', 'TP10'),
                    help="piso del margen propio de cada canal (µV); sin esto todos usan --margen-min")
    ap.add_argument('--piso-rel', type=float, default=d.piso_rel, help="piso = este factor por la mediana de los picos de ruido recientes (0 = no)")
    ap.add_argument('--piso-vent', type=float, default=d.piso_vent_s, help="ventana (s) de esos picos de ruido")
    ap.add_argument('--frac-spk', type=float, default=d.frac_spk, help="umbral entre ruido y señal: ruido + frac * (señal - ruido) (0 = no)")
    ap.add_argument('--alfa-spk', type=float, default=d.alfa_spk)
    ap.add_argument('--k-pend', type=float, default=d.k_pend, help="umbral de pendiente (Pan-Tompkins): factor sobre el máximo de ruido reciente (0 = no)")
    ap.add_argument('--cont-min', type=float, default=d.cont_min_s, help="continuidad mínima (s) de la envolvente sobre r_env * margen (0 = no)")
    ap.add_argument('--env', type=float, default=d.env_s, help="ancho (s) del máximo móvil de la envolvente")
    ap.add_argument('--r-env', type=float, default=d.r_env)
    ap.add_argument('--lf-aprende', action='store_true', help="las ventanas dominadas por baja frecuencia suben el umbral de inmediato")
    ap.add_argument('--rebote-frac', type=float, default=d.rebote_frac, help="fracción del pico anterior bajo la cual una detección cercana es un rebote (0 = no)")
    ap.add_argument('--rebote-s', type=float, default=d.rebote_s)
    ap.add_argument('--conc-min', type=float, default=d.conc_min, help="fracción mínima de energía en las 3 muestras mayores (0 = no)")
    ap.add_argument('--sat-veto', type=int, default=d.sat_veto, help="nº de canales saturados desde el que la ventana no da 1 (0 = no)")
    ap.add_argument('--lax-u', type=float, default=d.lax_u, help="pertenencia mínima para que un canal 'crezca' en el instante en que otro superó su umbral (0 = no)")
    ap.add_argument('--lax-a', type=float, default=d.lax_a)
    ap.add_argument('--lax-b', type=float, default=d.lax_b)
    ap.add_argument('--vent-inc', type=float, default=d.vent_inc_s, help="tolerancia (s) para medir el crecimiento")
    ap.add_argument('--par-difuso', action=argparse.BooleanOptionalAction, default=d.par_difuso, help="par_min: un canal del par cuenta si supera el margen O si crece (con puntaje >= --score-par)")
    ap.add_argument('--score-par', type=float, default=d.score_par, help="puntaje mínimo para aceptar una ventana en la que un canal del par solo crece")
    ap.add_argument('--lax-modo', action=argparse.BooleanOptionalAction, default=d.lax_modo, help="detecta si un grupo superó el umbral y el otro supera O crece")
    ap.add_argument('--parcial-umbral', type=float, default=d.parcial_umbral, help="lógica difusa: frontales + incremento de un temporal en ese instante (0 = no)")
    ap.add_argument('--inc-a', type=float, default=d.inc_a)
    ap.add_argument('--inc-b', type=float, default=d.inc_b)
    ap.add_argument('--tp-inutil', type=float, default=d.tp_inutil, help="margen (µV) desde el cual los temporales se consideran inútiles y se decide solo con los frontales (0 = no)")
    ap.add_argument('--tp-alguno', action=argparse.BooleanOptionalAction, default=d.tp_alguno,
                    help="los temporales son inútiles si CUALQUIERA supera --tp-inutil (--no-tp-alguno: solo si lo superan los dos)")
    ap.add_argument('--fp-rel', type=float, default=d.fp_rel, help="factor sobre el margen de ambos frontales cuando se decide sin temporales")
    ap.add_argument('--corr-tp-min', type=float, default=d.corr_tp_min, help="correlación mínima TP9-TP10 (0 = no)")
    ap.add_argument('--fd-min', type=float, default=d.fd_min, help="log(frontal/temporal) mínimo (-9 = no)")
    ap.add_argument('--difuso-canales', type=float, default=d.difuso_canales, help="grado mínimo de la lógica difusa entre canales (0 = no)")
    ap.add_argument('--centroide-max', type=float, default=d.centroide_max, help="centroide espectral máximo (Hz) del canal más activo (0 = no)")
    ap.add_argument('--espera-par', type=float, default=d.espera_par_s, help="espera (s) de la señal del otro grupo entre ventanas (0 = no)")
    ap.add_argument('--par-votos', type=int, default=d.par_votos)
    ap.add_argument('--plantilla', choices=['gabor', 'aprendida'], default=PLANTILLA_TIPO)
    ap.add_argument('--hf-lf-min', type=float, default=d.hf_lf_min, help="relación mínima banda principal / baja frecuencia (0 = no)")
    ap.add_argument('--lf-banda', type=float, nargs=2, default=list(LF_BANDA), metavar=('LO', 'HI'), help="banda de baja frecuencia en Hz")
    ap.add_argument('--score-perfil', choices=['conservador', 'balanceado', 'ninguno'], default='ninguno', help="perfil del puntaje entrenado (experimental)")
    ap.add_argument('--score-hi', type=float, default=None, help="puntaje mínimo para rescatar una ventana no detectada (por defecto el del perfil)")
    ap.add_argument('--score-hi2', type=float, default=d.score_hi2, help="rescate por dos ventanas seguidas con puntaje de al menos esto (>1 = no)")
    ap.add_argument('--score-lo', type=float, default=d.score_lo)
    ap.add_argument('--mantener', type=float, default=d.mantener_s)
    ap.add_argument('--tolerancia', type=float, default=d.tolerancia_s)
    a = ap.parse_args()
    globals()['BANDA'] = tuple(a.banda)
    globals()['LF_BANDA'] = tuple(a.lf_banda)
    globals()['PLANTILLA_TIPO'] = a.plantilla
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    os.makedirs(SALIDA, exist_ok=True)

    def score_kw(a):
        if a.score_perfil == 'ninguno':
            return {'score_coef': None, 'score_lo': a.score_lo, 'score_hi': 1.1, 'score_hi2': 1.1}
        pf = SCORE_PERFILES[a.score_perfil]
        return {'score_coef': pf['coef'], 'score_lo': a.score_lo, 'score_hi': pf['hi'] if a.score_hi is None else a.score_hi, 'score_hi2': a.score_hi2}

    def params(vent, k):
        return PV(ventana_s=vent, paso_s=a.paso, k_fp=k, k_tp=a.k_tp, alfa=a.alfa, persist_s=a.persist, calib_s=a.calib,
                  margen_min=a.margen_min, piso_pico=a.piso_pico, excl_ratio=a.excl_ratio, excl_piso=a.excl_piso, piso_rel=a.piso_rel, frac_spk=a.frac_spk, k_pend=a.k_pend, hf_lf_min=a.hf_lf_min, centroide_max=a.centroide_max, corr_tp_min=a.corr_tp_min, tp_inutil=a.tp_inutil, parcial_umbral=a.parcial_umbral, rebote_frac=a.rebote_frac, rebote_s=a.rebote_s, conc_min=a.conc_min, espera_par_s=a.espera_par, par_votos=a.par_votos, **score_kw(a), sat_veto=a.sat_veto, lax_u=a.lax_u, lax_a=a.lax_a, lax_b=a.lax_b, vent_inc_s=a.vent_inc, par_difuso=a.par_difuso, score_par=a.score_par, lax_modo=a.lax_modo, inc_a=a.inc_a, inc_b=a.inc_b, tp_alguno=a.tp_alguno, fp_rel=a.fp_rel, fd_min=a.fd_min, difuso_canales=a.difuso_canales, lf_aprende=a.lf_aprende, cont_min_s=a.cont_min, env_s=a.env, r_env=a.r_env, alfa_spk=a.alfa_spk, piso_vent_s=a.piso_vent, margen_min_ch=dict(zip(CAN, a.margen_min_ch)) if a.margen_min_ch else {}, mantener_s=a.mantener,
                  espera_s=a.espera, fp_solo=a.fp_solo, exc_min_s=a.exc_min, span_min_s=a.span_min, par_min=a.par_min, coinc_s=a.coinc, dur_min_s=a.dur_min, tolerancia_s=a.tolerancia)

    if a.todos or a.barrido:
        datos = [x for x in cargar_todos(RAIZ, d.skip_s) if x['cat'] in CATEGORIAS]
        print(f"{len(datos)} archivos de cejas y dos parpadeos ({sum(1 for x in datos if x['sens_ok'] >= 0.5)} con contacto bueno)")
        if a.barrido:
            print("det = gestos detectados / gestos. FP/min = episodios de salida = 1 fuera del gesto por minuto.")
            print("ruido ajusta = % de ventanas de ruido usadas para ajustar el umbral; gesto ajusta = % de ventanas con gesto usadas por error.")
            print()
            print(f"{'vent s':>6s} {'k':>3s} | {'contacto bueno':^20s} | {'todos':^40s}")
            print(f"{'':>6s} {'':>3s} | {'det%':>6s} {'FP/min':>6s}      | {'det%':>6s} {'FP/min':>6s} {'ruido ajusta':>12s} {'gesto ajusta':>12s}")
            for vent, k in itertools.product(a.ventana, a.k):
                res = evaluar_todos(datos, params(vent, k))
                b = resumen_todos([r for r in res if r['ok'] >= 0.5])
                t = resumen_todos(res)
                print(f"{vent:6g} {k:3g} | {b['det']:6.1f} {b['fp_min']:6.1f}      | {t['det']:6.1f} {t['fp_min']:6.1f} "
                      f"{t['ruido_ajusta']:11.0f}% {t['gesto_ajusta']:11.0f}%")
            return
        p = params(a.ventana[0], a.k[0])
        res = evaluar_todos(datos, p)
        print(f"Ventana {p.ventana_s:g} s, k {p.k_fp:g}, retención {p.mantener_s:g} s. Detectado = salida en 1 dentro del gesto.")
        print()
        print(f"{'archivo':46s} {'ok%':>4s} | {'gest':>4s} {'det':>3s} {'FN':>3s} {'FP':>3s} | {'pul cruda->salida':>17s} | "
              f"{'ruido ajusta':>12s} {'gesto ajusta':>12s}")
        print('-' * 118)
        filas = []
        for r in sorted(res, key=lambda r: (r['cat'], r['nombre'])):
            ev = r['ev']
            es = ev['e_s']
            s = resumen_todos([r])
            tp = max(es['tp'], 1)
            print(f"{r['nombre'][:46]:46s} {100 * r['ok']:4.0f} | {es['gestos']:4d} {es['tp']:3d} {es['fn']:3d} "
                  f"{ev['e_sfp']['fp']:3d} | {ev['pul_c'] / tp:8.1f} -> {ev['pul_s'] / tp:4.1f} | "
                  f"{s['ruido_ajusta']:11.0f}% {s['gesto_ajusta']:11.0f}%")
            filas.append({'archivo': r['nombre'], 'categoria': r['cat'], 'sens_ok': r['ok'], 'gestos': es['gestos'],
                          'detectados': es['tp'], 'fn': es['fn'], 'fp': ev['e_sfp']['fp'],
                          'ruido_ajusta_pct': s['ruido_ajusta'], 'gesto_ajusta_pct': s['gesto_ajusta']})
        print('-' * 118)
        for titulo, sub in (('contacto bueno (Sensores_OK >= 50 %)', [r for r in res if r['ok'] >= 0.5]),
                            ('todos', res),
                            ('  solo cejas', [r for r in res if r['cat'] == 'cejas']),
                            ('  solo dos parpadeos', [r for r in res if r['cat'] != 'cejas'])):
            if not sub:
                continue
            s = resumen_todos(sub)
            print(f"{titulo:46s} {'':>4s} | {s['gestos']:4d} {s['tp']:3d} {s['gestos'] - s['tp']:3d} {s['fp']:3d} | "
                  f"{s['pul_c']:8.1f} -> {s['pul_s']:4.1f} | {s['ruido_ajusta']:11.0f}% {s['gesto_ajusta']:11.0f}%"
                  f"   detección {s['det']:.0f} %, FP/min {s['fp_min']:.1f}")
        ruta = SALIDA / 'todos_ventanas.csv'
        pd.DataFrame(filas).to_csv(ruta, index=False)
        print()
        print("Resultados guardados en", ruta)
        return

    # un solo archivo
    import glob
    cand = sorted(glob.glob(str(RAIZ / f"dataset_*{a.archivo}*.csv")))
    cand = [c for c in cand if os.path.getsize(c) > 100]
    if not cand:
        sys.exit(f"No hay dataset que coincida con '{a.archivo}'")
    ruta_csv = cand[0]
    if len(cand) > 1:
        print("Varios coinciden; se usa el primero:", ", ".join(os.path.basename(c) for c in cand))
    nombre = os.path.basename(ruta_csv)[8:-4]
    df, fs = cargar(ruta_csv)
    trigger = (df['Trigger'] == 1).values
    sig, base, desp = preparar_senales(df, fs)
    p = params(a.ventana[0], a.k[0])
    sim = simular(desp, fs, p)
    ev = evaluar_sim(sim, trigger, fs, p)
    reporte(nombre, fs, len(df) / fs, p, ev, sim)
    guardar_csv(sim['ventanas'], SALIDA / f"ventanas_{nombre}.csv")
    if not a.no_grafico:
        titulo = (f"{nombre} | ventana {p.ventana_s:g} s, k {p.k_fp:g}, retención {p.mantener_s:g} s | gestos "
                  f"{ev['e_s']['gestos']}: detectados {ev['e_s']['tp']}, FN {ev['e_s']['fn']}, "
                  f"falsos positivos {ev['e_sfp']['fp']}")
        graficar(df, sig, base, trigger, sim, ev, p, fs, titulo, SALIDA / f"ventanas_{nombre}.png", a.mostrar)


if __name__ == '__main__':
    main()
