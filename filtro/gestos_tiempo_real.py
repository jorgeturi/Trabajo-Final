# ============================================================================================================
#  NOMBRE DE LA GRABACION  (editar aca antes de grabar)
#  Se antepone al nombre de todos los archivos de la sesion, para comparar personas y dias:
#      NOMBRE = "lara"        ->  sesiones/lara_20261028_101500_eeg.csv  (+ _eventos, _rangos, _resumen)
#      NOMBRE = "moma_dia2"   ->  sesiones/moma_dia2_20261028_101500_eeg.csv
#  Vacio: sesiones/sesion_<fecha>_<hora>_eeg.csv.   Tambien se puede pasar por consola: --nombre lara
# ============================================================================================================
NOMBRE = "yo 2 cejas"
# ============================================================================================================
__doc__ = """
Detector de gestos en tiempo real (causal, sin entrenamiento) que se suscribe al ZMQ de la Raspberry.
ESTE ES EL ARCHIVO PARA GRABAR. Avisa cada vez que el filtro ve un gesto, aunque sea suelto. Para que solo avise cuando hay
DOS gestos seguidos (activar/desactivar sin falsos disparos) esta gestos_patron.py, que va encima del mismo filtro.
Se graba siempre con este archivo (nombre de la persona: variable NOMBRE de arriba, o --nombre); gestos_patron.py analiza
esas grabaciones y tiene un modo en vivo.
  - NUESTRO filtro (ventanas deslizantes con umbral adaptativo por canal, el de filtro/analisis/ventanas_deslizantes.py) decide
    solo con la señal cruda del EEG: no usa nada del SDK.
  - El SDK (eventos /muse/elements/blink que reenvia la Raspberry como "BLINK") solo se REGISTRA, para compararlo.
  - La verdad la marcas VOS con la barra espaciadora: mantene ESPACIO apretado mientras hacés el gesto y soltalo al terminar.
Despues se cuentan, para nuestro filtro y para el SDK por separado, los aciertos, los gestos perdidos (falsos negativos) y las
detecciones fuera de todo rango marcado (falsos positivos).

Todo vive en este archivo (no importa ni modifica otros scripts; ventanas_deslizantes.py solo se usa en --verificar).

Que registra:
  - cantidad de gestos que detecta cada detector, cuantos de tus rangos marcados detecto cada uno (aciertos), cuantos se
    perdio (falsos negativos) y cuantas detecciones cayeron fuera de todo rango (falsos positivos),
  - el contacto de CADA sensor por separado (HORSESHOE: 1 bueno, 2 aceptable, 3 malo, 4 sin contacto): porcentaje de tiempo
    en cada estado y el contacto de cada sensor durante cada gesto,
  - la señal cruda en un CSV con el mismo formato que los datasets (Timestamp, Trigger, Sensores_OK, TP9, FP1, FP2, TP10)
    para volver a analizarla sin conexion con ventanas_deslizantes.py (el Trigger es tu barra espaciadora).
Todo se guarda en filtro/sesiones/ (sesion_<fecha>_eeg.csv, _rangos.csv, _eventos.csv y _resumen.json/.txt).

Uso:
  python filtro/gestos_tiempo_real.py                      # tablero en vivo (pyqtgraph) conectado a la Raspberry
  python filtro/gestos_tiempo_real.py --ip 192.168.1.50    # otra IP
  python filtro/gestos_tiempo_real.py --consola            # sin ventana: imprime las detecciones y el resumen
  python filtro/gestos_tiempo_real.py --simular dataset_xxx.csv    # reproduce un dataset como si llegara en vivo
  python filtro/gestos_tiempo_real.py --imagenes filtro/sesiones/sesion_xxx_eeg.csv  # imagen de cada falso positivo/negativo de una sesion
  python filtro/gestos_tiempo_real.py --verificar dataset_xxx.csv  # comprueba que da lo mismo que ventanas_deslizantes.py

Teclas del tablero: ESPACIO (mantener) = estoy haciendo el gesto;  Q o ESC = cerrar y guardar.
Requiere: numpy, scipy, pandas, pyzmq y (para el tablero) pyqtgraph + PyQt5.
"""
import argparse
import csv
import json
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import signal
from scipy.ndimage import binary_dilation

AQUI = Path(__file__).resolve().parent
SESIONES = AQUI / 'sesiones'

# ==========================================
# 1. CONFIGURACION
# ==========================================
IP_RASPBERRY = "192.168.1.50"
PUERTO = 5555
FS = 220.0                      # frecuencia de muestreo de la vincha (main.py de la Raspberry usa 220 Hz)
CAN = ['TP9', 'FP1', 'FP2', 'TP10']
FRONTALES = ('FP1', 'FP2')
TEMPORALES = ('TP9', 'TP10')
GRUPOS = (('FP1', 'FP2'), ('TP9', 'TP10'))

TOL_ANTES_S = 0.5               # una deteccion cuenta como acierto si cae entre (inicio - TOL_ANTES) y (fin + TOL_DESPUES)
TOL_DESPUES_S = 1.0             # (el filtro decide al final de cada ventana de 0.5 s y mantiene la salida 1 s)
CONTACTO_BUENO = 2              # HORSESHOE <= 2 se considera contacto util


@dataclass
class P:
    """Parametros del filtro (los valores finales de ventanas_deslizantes.PV)."""
    ventana_s: float = 0.5
    paso_s: float = 0.25
    k_fp: float = 6.0
    k_tp: float = 8.0
    alfa: float = 0.2
    espera_s: float = 0.1
    mem_pico_s: float = 2.0
    pct_calib: float = 25.0
    persist_s: float = 1.5
    persist_det_s: float = 3.0
    calib_s: float = 4.0
    skip_s: float = 2.0
    margen_inicial: float = 300.0
    margen_min: float = 20.0
    margen_max: float = 1000.0
    piso_pico: float = 1.0
    piso_rel: float = 1.5
    piso_vent_s: float = 10.0
    frac_spk: float = 0.1
    alfa_spk: float = 0.125
    excl_ratio: float = 3.0
    excl_piso: float = 10.0
    coinc_s: float = 0.1
    par_min: float = 1.0
    par_min_tp: float = 0.7
    par_comp: float = 0.8
    par_fuerte: float = 1.8
    par_comp_tp: float = 1.3
    par_fuerte_tp: float = 1.0
    fp_solo: float = 4.5
    fp_solo_otro: float = 2.5       # regla solo frontales: el otro FP debe superar esto (dobles con pausa 55 -> 58 de 63, sin FP nuevos en las sesiones originales) (09-11)
    tp_inutil: float = 300.0
    fp_rel: float = 1.2
    parcial_umbral: float = 0.5
    parcial_grado: float = 0.9
    inc_a: float = 3.0
    inc_b: float = 5.0
    tp_crece: float = 1.3
    tp_crece_f: float = 2.0
    tp_crece_grado: float = 0.7
    difuso_canales: float = 0.4
    grado_resc_vivos: bool = False  # las vias de rescate tampoco cuentan la correlacion de un temporal ignorado
    grado_veto_vivos: bool = False  # el veto difuso no cuenta las correlaciones de un frontal / temporal ignorado
    evid_k: int = 6
    evid_amin: float = 1.0
    rebote_frac: float = 0.6
    rebote_s: float = 1.0
    sat_min: int = 2
    sat_veto: int = 3
    sat_persist: int = 4            # la saturacion veta solo si dura esto en ventanas seguidas (~1 s): la de un gesto es momentanea (06-11)
    amp_fuerza: float = 2.5         # regla de amplitud: el mayor cociente frontal por el mayor temporal debe llegar a esto; si no, la rafaga
                                    # es debil y tampoco la rescatan las otras reglas (FP de 70 s, giro suave: 1.8; gestos reales >= 3.1) (06-11)
    veto_acc: bool = True           # descartar el gesto si hubo un movimiento brusco de cabeza (acelerometro en mg; sin datos no veta)
    acc_cambio_mg: float = 700.0    # cambio del ACC de mas de esto en 100 ms dentro del ultimo segundo
    acc_cambio2_mg: float = 900.0   # o de mas de esto en 250 ms dentro del ultimo segundo
    acc_salto_mg: float = 1400.0    # o un salto (rango XYZ en +/- 0.25 s) de mas de esto en los ultimos 2 s
    mantener_s: float = 1.0
    n_pulsos: int = 2               # rafagas separadas que debe haber para contar un gesto (1 = desactivado)
    reposo_s: float = 0.15          # dos rafagas son distintas solo si entre ellas los frontales quedan bajo su margen al menos esto (0 = no se exige):
                                    # un apreton simple que se corta un instante ya no cuenta como 2 rafagas (17 simples: 3 -> 0; dobles con pausa 55/63). El doble se hace con pausa (09-11)
    sep_pulso_s: float = 0.12       # sin superacion durante este tiempo = rafagas distintas
    doble_s: float = 2.5            # las rafagas deben caer dentro de este tiempo
    consumir_pulsos: bool = True    # al contar un gesto se descartan sus rafagas: el siguiente necesita dos rafagas NUEVAS
    rescate_pulso: bool = True      # una ventana aceptada por una regla de rescate (sin frontal y temporal a la vez) cuenta su rafaga frontal como pulso
    bloqueo_post_s: float = 0.0     # si > 0, las rafagas que lleguen dentro de este tiempo despues de contar un gesto tampoco cuentan (0 = no; con 1 s se
                                    # pierden los intentos que se hacen enseguida de otro: 45 -> 43 de 62 marcas)
    exigir_contacto: bool = True    # sin contacto en los frontales (horseshoe) no se detecta: la vincha no esta puesta
    contacto_min_s: float = 1.5     # el sin contacto debe durar al menos esto (una ventana): un gesto lo produce un instante
    confirmar_senal: bool = True    # el sin contacto del horseshoe solo vale si la señal tambien lo confirma (el horseshoe a veces se traba en 4)
    senal_sin_contacto_uv: float = 60.0  # RMS 8-30 Hz (ventana de 0.5 s) del MENOR del par por encima de esto, durante contacto_min_s seguidos,
                                    # = par sin contacto. Sin vincha da ~240 uV (99 % > 100); puesta ~5 uV y los gestos no lo sostienen 1.5 s (06-11)
    rearme_s: float = 1.0           # tras volver el contacto sigue bloqueado este tiempo (el asentamiento de la vincha da artefactos)
    reinicio_s: float = 3.0         # si estuvo sin contacto al menos esto, al volver se vuelve a calibrar (la señal y el ruido son otros)
    bloquear_calibracion: bool = True   # mientras calibra (arranque o reinicio) no se detecta: el margen de partida es solo un valor provisorio
    evid_th: dict = field(default_factory=lambda: {'der_F': 10.6, 'hp_F': 11.0, 'lp_F': 10.3, 'ncc_F': 0.59,
                                                   'corrFF': 0.69, 'corrTT': 0.86, 'corrFT': 0.41})


GABOR = (12.0, 0.1)             # ráfaga paramétrica (Hz, desvío en s) para la convolución


# ==========================================
# 2. UTILIDADES DEL FILTRO
# ==========================================
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


def relaciones_canales(desp, fl, W, fs, ratio):
    """Correlacion TP9-TP10, FP1-FP2, frontal-temporal (desfase de hasta 30 ms) y log del cociente frontal/temporal."""
    s = {c: desp['SIG_' + c][fl - W:fl] for c in CAN}
    m = max(int(0.03 * fs), 1)
    cft = []
    for a in FRONTALES:
        for b in TEMPORALES:
            cft.append(max(_corr(s[a][max(0, k):W + min(0, k)], s[b][max(0, -k):W + min(0, -k)]) for k in range(-m, m + 1)))
    fd = np.log(max(ratio['FP1'], ratio['FP2'], 1e-3) / max(ratio['TP9'], ratio['TP10'], 1e-3))
    return {'tp': _corr(s['TP9'], s['TP10']), 'fp': _corr(s['FP1'], s['FP2']), 'ft': float(np.mean(cft)), 'fd': float(fd)}


def grado_canales(r, sin_fp=False, sin_tp=False):
    """Logica difusa sobre la relacion entre canales: pertenencias lineales a 'se comportan como un gesto', promedio ponderado."""
    def mu(x, a, b):
        return float(np.clip((x - a) / (b - a), 0.0, 1.0))
    mus = (mu(r['tp'], 0.3, 0.8), mu(r['fd'], -0.5, 0.5), mu(r['fp'], 0.1, 0.6), mu(r['ft'], 0.2, 0.7))
    pesos = (0.4, 0.3, 0.15, 0.15)
    if sin_fp or sin_tp:            # un canal ignorado: su correlacion con el otro del par no informa; se reparte el peso
        pesos = (0.0 if sin_tp else 0.4, 0.3, 0.0 if sin_fp else 0.15, 0.15)
        return sum(w * m for w, m in zip(pesos, mus)) / sum(pesos)
    return sum(w * m for w, m in zip(pesos, mus))


def evidencias(desp, fl, W):
    """Evidencias paralelas de la ventana: derivada, pasa altos, pasa bajos y convolucion (frontales) y tres correlaciones."""
    sl = slice(fl - W, fl)
    ev = {n + '_F': max(float(desp['EV_' + n + '_' + c][sl].max()) for c in FRONTALES) for n in ('der', 'hp', 'lp', 'ncc')}
    s = {c: desp['SIG_' + c][sl] for c in CAN}
    ev['corrFF'] = _corr(s['FP1'], s['FP2'])
    ev['corrTT'] = _corr(s['TP9'], s['TP10'])
    ev['corrFT'] = float(np.mean([_corr(s[a], s[b]) for a in FRONTALES for b in TEMPORALES]))
    return ev


def _media_movil(a, n):
    """Promedio de los ultimos n valores (con menos al principio): igual a pandas rolling(n, min_periods=1).mean()."""
    cs = np.cumsum(np.insert(np.asarray(a, dtype=float), 0, 0.0))
    i = np.arange(1, len(cs))
    return (cs[i] - cs[np.maximum(i - n, 0)]) / np.minimum(i, n)


def _suma_movil(a, n):
    """Suma de los ultimos n valores (con menos al principio): igual a pandas rolling(n, min_periods=1).sum()."""
    cs = np.cumsum(np.insert(np.asarray(a, dtype=float), 0, 0.0))
    i = np.arange(1, len(cs))
    return cs[i] - cs[np.maximum(i - n, 0)]


def _mediana_movil(a, n, nmin, sh, cola=None, salto=1):
    """Mediana de la ventana de n valores que termina sh muestras antes de cada indice (igual a pandas
    rolling(n, min_periods=nmin).median().shift(sh)); NaN si hay menos de nmin valores. Si se da `cola`, solo se calculan
    los ultimos `cola` indices (el resto queda NaN): es lo unico que usa el motor. Con salto > 1 la mediana se calcula una de
    cada `salto` muestras (contando desde la ultima) y entre medio se repite la ultima calculada (causal): la mediana de 10 s
    casi no cambia en unas decenas de milisegundos y asi cuesta salto veces menos."""
    a = np.asarray(a, dtype=float)
    out = np.full(len(a), np.nan)
    ini_i = max(sh, len(a) - cola if cola else 0)
    idx = np.arange(ini_i, len(a))
    if len(idx) == 0:
        return out
    calc_mask = ((len(a) - 1 - idx) % salto == 0)
    calc_mask[0] = True
    calc = idx[calc_mask]
    fin = calc - sh + 1                                        # ventana a[fin - n : fin]
    completas = calc[fin - n >= 0]
    if len(completas):
        v = np.lib.stride_tricks.sliding_window_view(a, n)[completas - sh + 1 - n]
        k = n // 2
        if n % 2:
            out[completas] = np.partition(v, k, axis=1)[:, k]
        else:
            q = np.partition(v, [k - 1, k], axis=1)
            out[completas] = 0.5 * (q[:, k - 1] + q[:, k])
    for i in calc[fin - n < 0]:                                # ventanas parciales del principio de la señal
        f = i - sh + 1
        if f >= nmin:
            out[i] = np.median(a[0:f])
    if salto > 1:
        ultimo = np.maximum.accumulate(np.where(calc_mask, idx, -1))
        out[idx] = out[ultimo]
    return out


# ==========================================
# 3. SEÑALES EN FLUJO (filtros con estado + series derivadas sobre la historia reciente)
# ==========================================
class FlujoSenales:
    """Filtra muestra a muestra (pasabanda 8-30 Hz, pasa bajos 0.5-8 Hz y pasa altos 25-45 Hz con estado) y guarda la
    historia reciente (13 s) para calcular las series derivadas (base de 2 s, medianas de 10 s, convolucion, etc.).
    Todo es causal: ningun valor usa muestras posteriores."""

    def __init__(self, fs, hist_s=13.0):
        self.fs = fs
        nyq = fs / 2.0
        self.sos = signal.butter(8, [8.0 / nyq, min(30.0, 0.9 * nyq) / nyq], btype='band', output='sos')
        self.sos_lp = signal.butter(4, [0.5 / nyq, min(8.0, 0.9 * nyq) / nyq], btype='band', output='sos')
        hi = min(45.0, 0.9 * nyq)
        self.sos_hp = signal.butter(4, [25.0 / nyq, hi / nyq], btype='band', output='sos') if hi > 30.0 else None
        self.zi = {}
        self.n = 0                                   # muestras recibidas en total
        self.H = int(hist_s * fs)
        self.raw = np.zeros((4, 0))
        self.sig = np.zeros((4, 0))
        self.lp = np.zeros((4, 0))
        self.hp = np.zeros((4, 0))
        L = int(0.8 * fs)
        t = np.linspace(-0.3, 0.5, L)
        tpl = np.exp(-t ** 2 / (2 * GABOR[1] ** 2)) * np.sin(2 * np.pi * GABOR[0] * t)
        self.tpl = tpl / np.linalg.norm(tpl)
        self.L = L

    def _filtrar(self, nombre, sos, x):
        """Filtra los 4 canales (x: (4, m)) con una sola llamada a sosfilt; cada canal conserva su propio estado."""
        z = self.zi.get(nombre)
        if z is None:
            z = signal.sosfilt_zi(sos)[:, None, :] * x[:, 0][None, :, None]    # estado inicial escalado a la 1a muestra de cada canal
        y, self.zi[nombre] = signal.sosfilt(sos, x, axis=-1, zi=z)
        return y

    def agregar(self, muestras):
        x = np.asarray(muestras, dtype=float).reshape(-1, 4).T      # (4, m)
        if x.shape[1] == 0:
            return
        sig = self._filtrar('b', self.sos, x)
        lp = self._filtrar('lp', self.sos_lp, x)
        hp = self._filtrar('hp', self.sos_hp, x) if self.sos_hp is not None else np.zeros_like(x)
        self.raw = np.hstack([self.raw, x])[:, -self.H:]
        self.sig = np.hstack([self.sig, sig])[:, -self.H:]
        self.lp = np.hstack([self.lp, lp])[:, -self.H:]
        self.hp = np.hstack([self.hp, hp])[:, -self.H:]
        self.n += x.shape[1]

    @property
    def offset(self):
        """Indice absoluto de la primera muestra de la historia guardada."""
        return self.n - self.sig.shape[1]

    def derivadas(self, cola=None):
        fs = self.fs
        n2, n10, nmin, sh = int(fs * 2), int(10 * fs), int(fs), int(0.5 * fs)

        def rm(a):
            return _media_movil(a, n2)

        def mediana(a):
            e = _mediana_movil(a, n10, nmin, sh, cola, salto=max(1, int(0.05 * fs)))
            return np.maximum(np.nan_to_num(e, nan=1e9), 1e-3)

        def evidencias_series(d):
            for i, c in enumerate(FRONTALES):
                i = CAN.index(c)
                sg = self.sig[i]
                der = np.abs(np.diff(sg, prepend=sg[0])) * fs
                d['EV_der_' + c] = der / mediana(der)
                if self.sos_hp is not None:
                    a = np.abs(self.hp[i] - rm(self.hp[i]))
                    d['EV_hp_' + c] = a / mediana(a)
                else:
                    d['EV_hp_' + c] = np.zeros(len(sg))
                lp = self.lp[i]
                dl = np.abs(lp - rm(lp))
                d['EV_lp_' + c] = dl / mediana(dl)
                conv = signal.fftconvolve(sg, self.tpl[::-1], mode='full')[:len(sg)]
                en = np.sqrt(np.maximum(_suma_movil(sg ** 2, self.L), 1e-9))
                d['EV_ncc_' + c] = np.abs(conv) / en

        d = _SeriesPerezosas(evidencias_series)
        for i, c in enumerate(CAN):
            sg = self.sig[i]
            d[c] = np.abs(sg - rm(sg))                         # desplazamiento |señal - base| (base = promedio de 2 s)
            d['SIG_' + c] = sg
            d['RAW_' + c] = self.raw[i]
        return d


class _SeriesPerezosas(dict):
    """Series de una ventana. Las de evidencias (EV_*) son las mas caras (medianas de 10 s) y la mayoria de las ventanas no las
    necesitan: se calculan la primera vez que alguien las pide, con el mismo resultado que si se hubieran calculado antes."""

    def __init__(self, calcular):
        super().__init__()
        self._calcular = calcular

    def __missing__(self, clave):
        if clave.startswith('EV_'):
            self._calcular(self)
            return self[clave]
        raise KeyError(clave)


# ==========================================
# 4. MOTOR DE DECISION (ventanas deslizantes con umbral adaptativo)
# ==========================================
class MotorGestos:
    """Recibe muestras (n, 4) en el orden TP9, FP1, FP2, TP10 y devuelve una decision por ventana (cada paso_s).
    Es el filtro de ventanas_deslizantes.simular reescrito para que corra muestra a muestra."""

    def __init__(self, fs=FS, p=None, vis=False):
        self.fs = fs
        self.p = p or P()
        p = self.p
        self.W = max(int(p.ventana_s * fs), 2)
        self.paso = max(int(p.paso_s * fs), 1)
        self.primera = int(p.skip_s * fs) + self.W
        self.fin_calib = self.primera + int(p.calib_s * fs)
        self.proximo_fin = self.primera
        self.flujo = FlujoSenales(fs)
        self.n_persist = max(1, int(round(p.persist_s / p.paso_s)))
        self.n_persist_det = max(1, int(round(p.persist_det_s / p.paso_s)))
        self.n_espera = int(p.espera_s * fs)
        self.n_coinc = int(round(p.coinc_s * fs))
        self.kg = {c: (p.k_fp if c in FRONTALES else p.k_tp) for c in CAN}
        self.margen = {c: p.margen_inicial for c in CAN}
        self.ruido = {c: None for c in CAN}
        self.pico_r = {c: None for c in CAN}
        self.cuenta = {c: 0 for c in CAN}
        self.largos = {c: deque(maxlen=max(1, int(round(p.piso_vent_s / p.paso_s)))) for c in CAN}
        self.recientes = {c: deque(maxlen=max(1, int(round(p.mem_pico_s / p.paso_s)))) for c in CAN}
        self.spk = {c: None for c in CAN}
        self.cal_n = {c: [] for c in CAN}
        self.cal_p = {c: [] for c in CAN}
        self.ult_det = [-10 ** 9, 0.0]
        self.fint = []                               # tramos [inicio, fin] (muestras absolutas) con algun frontal sobre el margen (para reposo_s)
        self.pulsos = []                             # rafagas [inicio, fin] (muestras absolutas) de frontal+temporal sobre el margen
        self.n_sep_pulso = int(p.sep_pulso_s * fs)
        self.consumido = -10 ** 9                    # muestra hasta la que las rafagas ya se usaron en un gesto contado
        self.sin_cont_desde = None                   # muestra desde la que los dos frontales estan sin contacto (None = hay contacto o no se sabe)
        self.cuenta_senal_off = {'FP': 0, 'TP': 0}   # ventanas seguidas en las que la señal del par parece sin contacto
        self.senal_off = {'FP': False, 'TP': False}  # la señal confirma par sin contacto (sostenido contacto_min_s)
        self.sin_frente_desde = None                 # muestra desde la que touching_forehead vale 0 (vincha fuera de la frente)
        self.sin_vincha_desde = None                 # muestra desde la que se considera la vincha sacada (sin contacto sostenido)
        self.rearme_hasta = -1                       # muestra hasta la que sigue bloqueado despues de volver el contacto
        self.n_reinicios = 0                         # veces que se volvio a calibrar por sacar y poner la vincha
        self.acc = deque()                           # (t, x, y, z) del acelerometro de los ultimos 3 s (mg), para el veto por movimiento
        self.t_reloj = None                          # tiempo (s) del lote que se esta procesando, en el mismo reloj que el ACC
        self._sat_seg = 0                            # ventanas seguidas con saturacion
        self.n_cont = int(p.contacto_min_s * fs)
        self.ultimo_exceso = {c: -10 ** 9 for c in CAN}
        self.ignorado = {c: False for c in CAN}
        self.activa_hasta = -1                       # muestra hasta la que la salida se mantiene en 1
        self.ultima = None
        self.vis = vis                               # guarda lo necesario para dibujar (desvio, margen, ignorado, deteccion)
        if vis:
            self.R = int(14 * fs)
            self.v_xs = np.zeros((4, self.R))
            self.v_mg = np.full((4, self.R), np.nan)
            self.v_ign = np.zeros((4, self.R), dtype=bool)
            self.v_det = np.zeros(self.R, dtype=bool)

    def agregar(self, muestras):
        """Procesa un lote de muestras y devuelve la lista de decisiones (una por ventana completada)."""
        self.flujo.agregar(muestras)
        if self.vis:
            self._vis_muestras(len(np.asarray(muestras).reshape(-1, 4)))
        out = []
        if self.proximo_fin > self.flujo.n:
            return out
        desp = self.flujo.derivadas(cola=self.flujo.n - self.proximo_fin + self.W + 1)   # las medianas solo hacen falta en las ventanas por decidir
        off = self.flujo.offset
        while self.proximo_fin <= self.flujo.n:
            out.append(self._paso(self.proximo_fin, desp, off))
            self.proximo_fin += self.paso
        return out

    def _vis_muestras(self, m):
        """Guarda el desvio con signo (señal filtrada menos su base de 2 s) de las muestras nuevas."""
        n2, n = int(self.fs * 2), self.flujo.n
        m = min(m, self.flujo.sig.shape[1])
        if m <= 0:
            return
        idx = np.arange(n - m, n) % self.R
        for i in range(4):
            sg = self.flujo.sig[i]
            base = _media_movil(sg, n2)
            self.v_xs[i, idx] = sg[-m:] - base[-m:]
        self.v_det[idx] = False

    def vista(self, N):
        """Ultimas N muestras para dibujar: desvio con signo, margen vigente, canal ignorado y ventanas con deteccion."""
        n = self.flujo.n
        N = min(N, n, self.R)
        idx = np.arange(n - N, n) % self.R
        return {'n': n, 't': (np.arange(n - N, n) - n) / self.fs, 'xs': self.v_xs[:, idx], 'mg': self.v_mg[:, idx],
                'ign': self.v_ign[:, idx], 'det': self.v_det[idx]}

    def set_contacto(self, vals):
        """Contacto por sensor del horseshoe ({'TP9': 1..4, 'FP1': .., 'FP2': .., 'TP10': ..}, 4 = sin contacto). Se anota desde que
        muestra los DOS frontales estan sin contacto; con que uno tenga contacto la vincha se considera puesta. Sin datos de contacto
        (grabaciones sin horseshoe) nunca se bloquea."""
        f = [vals.get(c) for c in FRONTALES]
        if any(v is None for v in f):
            return
        if all(int(v) >= 4 for v in f):
            if self.sin_cont_desde is None:
                self.sin_cont_desde = self.flujo.n
        else:
            self.sin_cont_desde = None

    def set_acc(self, t, x, y, z):
        """Muestra del acelerometro (mg) con su tiempo en segundos (cualquier reloj, el mismo que set_reloj)."""
        self.acc.append((float(t), float(x), float(y), float(z)))
        while self.acc and self.acc[0][0] < t - 3.0:
            self.acc.popleft()

    def set_reloj(self, t):
        """Tiempo (s) del lote que se va a procesar: el veto por ACC mira hacia atras desde aca."""
        self.t_reloj = float(t)

    def _veto_acc(self):
        """Movimiento brusco de cabeza antes de la decision: devuelve el motivo, o '' si no hubo (o no hay datos del ACC)."""
        p = self.p
        if not p.veto_acc or self.t_reloj is None or len(self.acc) < 2:
            return ''
        a = np.array(self.acc)
        a = a[a[:, 0] <= self.t_reloj]
        if len(a) < 2:
            return ''
        T, X, ahora = a[:, 0], a[:, 1:], self.t_reloj
        i1 = np.flatnonzero(T >= ahora - 1.0)
        for dt, um in ((0.1, p.acc_cambio_mg), (0.25, p.acc_cambio2_mg)):
            j = np.searchsorted(T, T[i1] + dt, side='right') - 1     # el valor vigente dt despues (el ultimo recibido hasta ese instante)
            ok = T[i1] + dt <= ahora
            if ok.any() and float(np.linalg.norm(X[j[ok]] - X[i1[ok]], axis=1).max()) > um:
                return f'movimiento brusco de cabeza (ACC cambia mas de {um:.0f} mg en {dt * 1000:.0f} ms)'
        for i in np.flatnonzero(T >= ahora - 2.0):
            k = (T >= T[i] - 0.25) & (T <= T[i] + 0.25)
            if float(np.linalg.norm(X[k].max(0) - X[k].min(0))) > p.acc_salto_mg:
                return f'movimiento de cabeza (salto del ACC de mas de {p.acc_salto_mg:.0f} mg)'
        return ''

    def set_frente(self, valor):
        """/muse/elements/touching_forehead (1 = la vincha toca la frente, 0 = no): el contacto general de la vincha. Sin datos nunca bloquea."""
        if valor is None:
            return
        if int(valor) == 0:
            if self.sin_frente_desde is None:
                self.sin_frente_desde = self.flujo.n
        else:
            self.sin_frente_desde = None

    def _reiniciar(self, fin):
        """Vuelve a calibrar desde `fin` (se sacó la vincha un rato): olvida el ruido, los margenes y todo lo pendiente."""
        p, fs = self.p, self.fs
        self.margen = {c: p.margen_inicial for c in CAN}
        self.ruido = {c: None for c in CAN}
        self.pico_r = {c: None for c in CAN}
        self.cuenta = {c: 0 for c in CAN}
        self.largos = {c: deque(maxlen=max(1, int(round(p.piso_vent_s / p.paso_s)))) for c in CAN}
        self.recientes = {c: deque(maxlen=max(1, int(round(p.mem_pico_s / p.paso_s)))) for c in CAN}
        self.spk = {c: None for c in CAN}
        self.cal_n = {c: [] for c in CAN}
        self.cal_p = {c: [] for c in CAN}
        self.ult_det = [-10 ** 9, 0.0]
        self.pulsos = []
        self.consumido = fin
        self.ultimo_exceso = {c: -10 ** 9 for c in CAN}
        self.ignorado = {c: False for c in CAN}
        self.activa_hasta = -1
        self.fin_calib = fin + int((p.skip_s + p.calib_s) * fs)
        self.n_reinicios += 1

    def salida(self):
        """1 mientras dure la retencion de la ultima deteccion (mantener_s)."""
        return int(self.flujo.n < self.activa_hasta)

    def _paso(self, fin, desp, off):
        p, fs, W = self.p, self.fs, self.W
        fl = fin - off                                # indice local en la historia
        # confirmacion por señal: RMS 8-30 Hz del menor del par, sostenido contacto_min_s (sin vincha la señal es ruido grande y continuo)
        k_off = max(1, int(round(p.contacto_min_s / p.paso_s)))
        for par, (a_, b_) in (('FP', FRONTALES), ('TP', TEMPORALES)):
            rms = min(float(np.sqrt(np.mean(np.square(desp['SIG_' + c][fl - W:fl])))) for c in (a_, b_))
            self.cuenta_senal_off[par] = self.cuenta_senal_off[par] + 1 if rms > p.senal_sin_contacto_uv else 0
            self.senal_off[par] = self.cuenta_senal_off[par] >= k_off
        # vincha puesta o no: sin contacto en los dos frontales, o touching_forehead en 0, durante al menos una ventana
        # (y, con confirmar_senal, que la señal de los frontales tambien parezca sin contacto)
        sin_cont = p.exigir_contacto and ((self.sin_cont_desde is not None and fin - self.sin_cont_desde >= self.n_cont) or
                                          (self.sin_frente_desde is not None and fin - self.sin_frente_desde >= self.n_cont)) \
            and (self.senal_off['FP'] or not p.confirmar_senal)
        if sin_cont:
            if self.sin_vincha_desde is None:
                self.sin_vincha_desde = fin
            self.rearme_hasta = fin + int(p.rearme_s * fs)
        elif self.sin_vincha_desde is not None:
            if fin - self.sin_vincha_desde >= int(p.reinicio_s * fs):
                self._reiniciar(fin)                  # estuvo sacada un rato: se vuelve a calibrar
            self.sin_vincha_desde = None
        calibrando = fin < self.fin_calib
        bloqueo = ('vincha sin contacto' if sin_cont else 'vincha recien puesta' if fin < self.rearme_hasta else
                   'calibrando' if (calibrando and p.bloquear_calibracion) else '')
        margen, ruido, ignorado = self.margen, self.ruido, self.ignorado
        clase, sup, nivel, pico, sobre = {}, {}, {}, {}, {}
        for c in CAN:
            seg = desp[c][fl - W:fl]
            nivel[c], pico[c] = float(seg.mean()), float(seg.max())
            if ignorado[c]:
                clase[c], sup[c] = 'ignorado', False
            else:
                sobre[c] = seg > margen[c]
                sup[c] = pico[c] > margen[c]
                clase[c] = 'movimiento' if sup[c] else 'ruido'
                if sup[c]:
                    self.ultimo_exceso[c] = fin - W + int(np.flatnonzero(seg > margen[c])[-1])
        det = (sup['FP1'] or sup['FP2']) and (sup['TP9'] or sup['TP10'])
        via, freno = ('amplitud (un frontal y un temporal sobre su margen)' if det else ''), ''   # solo para mostrar en el tablero
        # fuerza: frontal y temporal apenas sobre su margen = rafaga debil (giro suave de cabeza); no la rescata ninguna otra regla
        debil = False
        if det and p.amp_fuerza > 0:
            f_ = max(pico[c] / margen[c] for c in FRONTALES if sup[c])
            t_ = max(pico[c] / margen[c] for c in TEMPORALES if sup[c])
            if f_ * t_ < p.amp_fuerza:
                debil = True
                det, via, freno = False, '', f'fuerza baja (frontal x temporal {f_ * t_:.2f} < {p.amp_fuerza})'
        # pares: si los dos canales de un par estan activos, ambos deben superar su margen (con excepciones por grupo fuerte)
        if det and (p.par_min > 0 or p.par_min_tp > 0):
            for a_, b_ in GRUPOS:
                um_ = p.par_min if a_ in FRONTALES else p.par_min_tp
                if a_ in FRONTALES and p.par_comp > 0 and not ignorado['TP9'] and not ignorado['TP10'] and \
                        min(pico['TP9'] / margen['TP9'], pico['TP10'] / margen['TP10']) >= p.par_comp and \
                        max(pico[f_] / margen[f_] for f_ in FRONTALES) >= p.par_fuerte:
                    continue
                if a_ not in FRONTALES and p.par_comp_tp > 0 and not ignorado['FP1'] and not ignorado['FP2'] and \
                        min(pico['FP1'] / margen['FP1'], pico['FP2'] / margen['FP2']) >= p.par_comp_tp and \
                        max(pico['TP9'] / margen['TP9'], pico['TP10'] / margen['TP10']) >= p.par_fuerte_tp:
                    continue
                if um_ > 0 and not ignorado[a_] and not ignorado[b_]:
                    if not (pico[a_] / margen[a_] >= um_ and pico[b_] / margen[b_] >= um_):
                        det = False
                        via, freno = '', freno or ('par frontal (los dos frontales deben superar su margen)' if a_ in FRONTALES
                                                  else 'par temporal (TP9 y TP10 deben llegar a 0.7 de su margen)')
        # coincidencia: un frontal y un temporal superan el margen en el mismo instante (+/- coinc_s)
        if det and p.coinc_s >= 0:
            vacio = np.zeros(W, dtype=bool)
            f = np.logical_or.reduce([sobre.get(c, vacio) for c in FRONTALES])
            t_ = np.logical_or.reduce([sobre.get(c, vacio) for c in TEMPORALES])
            if self.n_coinc > 0:
                f = binary_dilation(f, structure=np.ones(2 * self.n_coinc + 1, dtype=bool))
            det = bool((f & t_).any())
            if not det:
                via, freno = '', freno or 'coincidencia (frontal y temporal no superan el margen a la vez)'
        # solo frontales: los dos muy por encima de su margen aunque ningun temporal responda
        if not det and not debil and p.fp_solo > 0 and not ignorado['FP1'] and not ignorado['FP2']:
            # uno de los dos FP muy por encima (fp_solo) y el otro al menos fp_solo_otro: con FP1 de mal contacto casi nunca llegaban los dos (09-11)
            _r1, _r2 = pico['FP1'] / margen['FP1'], pico['FP2'] / margen['FP2']
            det = max(_r1, _r2) > p.fp_solo and min(_r1, _r2) > p.fp_solo_otro
            if det:
                via = 'solo frontales (uno 4.5 y el otro 2.5 veces sobre su margen)'
        # logica difusa sobre la relacion entre canales
        if det and p.difuso_canales > 0:
            rc = relaciones_canales(desp, fl, W, fs, {c: pico[c] / margen[c] for c in CAN})
            if grado_canales(rc, p.grado_veto_vivos and bool(ignorado['FP1'] or ignorado['FP2']),
                             p.grado_veto_vivos and bool(ignorado['TP9'] or ignorado['TP10'])) < p.difuso_canales:
                det = False
                via, freno = '', freno or 'relacion entre canales (logica difusa)'
        # temporales inutiles (margen enorme por ruido): se decide solo con los frontales
        inutiles = p.tp_inutil > 0 and any(margen[c] >= p.tp_inutil for c in TEMPORALES)
        if not det and not debil and inutiles:
            vivos = [c for c in FRONTALES if not ignorado[c]]
            det = bool(vivos) and all(pico[c] > p.fp_rel * margen[c] for c in vivos) and any(sup[c] for c in vivos) \
                and len(vivos) == len(FRONTALES)
            if det:
                via = 'temporales inutiles (margen > 300 uV): decide solo con los frontales'
        # incremento parcial: frontales activos y un temporal que sube (pico en esos instantes) aunque no llegue a su margen
        if not det and not debil and p.parcial_umbral > 0:
            vivos = [c for c in FRONTALES if not ignorado[c]]
            tp_c = [c for c in TEMPORALES if not ignorado[c]]
            if vivos and tp_c and all(ruido[c] is not None for c in CAN):
                fm = np.logical_or.reduce([sobre[c] for c in vivos])
                if fm.any():
                    if self.n_coinc > 0:
                        fm = binary_dilation(fm, structure=np.ones(2 * self.n_coinc + 1, dtype=bool))
                    inc = max(float(desp[c][fl - W:fl][fm].max()) / max(ruido[c], 1e-3) for c in tp_c)
                    f_amb = min(_mulin(pico[c] / margen[c], 0.8, 2.0) for c in vivos)
                    det = float(np.sqrt(f_amb * _mulin(inc, p.inc_a, p.inc_b))) >= p.parcial_umbral
                    if det and p.parcial_grado > 0:
                        rc_ = relaciones_canales(desp, fl, W, fs, {c: pico[c] / margen[c] for c in CAN})
                        det = grado_canales(rc_, ignorado['FP1'] or ignorado['FP2'], p.grado_resc_vivos and bool(ignorado['TP9'] or ignorado['TP10'])) >= p.parcial_grado
                    if det:
                        via = 'incremento parcial (frontales activos y un temporal sube)'
        # crecimiento de un temporal respecto del mayor pico de ruido reciente
        if not det and not debil and p.tp_crece > 0 and not calibrando:
            vivos = [c for c in FRONTALES if not ignorado[c] and sup[c] and pico[c] / margen[c] >= p.tp_crece_f]
            if vivos:
                fm = np.logical_or.reduce([sobre[c] for c in FRONTALES if c in sobre])
                if self.n_coinc > 0:
                    fm = binary_dilation(fm, structure=np.ones(2 * self.n_coinc + 1, dtype=bool))
                if fm.any() and any((not ignorado[c]) and self.pico_r[c] is not None
                                    and float(desp[c][fl - W:fl][fm].max()) > p.tp_crece * self.pico_r[c] for c in TEMPORALES):
                    det = True
                    if p.tp_crece_grado > 0:
                        rc_ = relaciones_canales(desp, fl, W, fs, {c: pico[c] / margen[c] for c in CAN})
                        det = grado_canales(rc_, ignorado['FP1'] or ignorado['FP2'], p.grado_resc_vivos and bool(ignorado['TP9'] or ignorado['TP10'])) >= p.tp_crece_grado
                    if det:
                        via = 'crecimiento del temporal sobre su ruido reciente'
        # veto por saturacion (contacto perdido)
        if p.sat_veto > 0:
            n_sat = sum(int(((desp['RAW_' + c][fl - W:fl] <= 1) | (desp['RAW_' + c][fl - W:fl] >= 1675)).sum()) >= p.sat_min for c in CAN)
            self._sat_seg = self._sat_seg + 1 if n_sat >= p.sat_veto else 0
            if det and n_sat >= p.sat_veto and self._sat_seg >= p.sat_persist:
                det = False
                via, freno = '', freno or 'saturacion (3 o mas canales tocan el limite del sensor)'
        # capa de evidencias paralelas: rescata si hay un frontal activo y al menos evid_k de 7 evidencias sobre su umbral
        votos = 0                                    # solo se calcula si la ventana lo necesita (es lo mas caro): 0 = no se calculo
        if p.evid_k > 0 and not det and not calibrando and max(pico[c] / margen[c] for c in FRONTALES) >= p.evid_amin:
            evd = evidencias(desp, fl, W)
            votos = sum(evd[n] > p.evid_th[n] for n in p.evid_th)
            if votos >= p.evid_k:
                det = True
                via = f'evidencias paralelas ({votos} de 7 votos)'
        # vincha sacada, recien puesta o calibrando: no hay gesto (ni se guardan rafagas) y la bandera del gesto previo queda en 0
        if det and bloqueo:
            det = False
            via, freno = '', freno or bloqueo
        # pulsos: un gesto solo cuenta si hubo n_pulsos rafagas separadas (frontal y temporal sobre el margen a la vez) dentro de doble_s
        if p.n_pulsos > 1:
            if bloqueo:
                self.pulsos = []                     # lo que pase sin la vincha puesta (o calibrando) no cuenta para ningun gesto
                self.consumido = max(self.consumido, fin)
            else:
                vacio = np.zeros(W, dtype=bool)
                co = np.logical_or.reduce([sobre.get(c, vacio) for c in FRONTALES]) & \
                    np.logical_or.reduce([sobre.get(c, vacio) for c in TEMPORALES])
                if p.rescate_pulso and det and not co.any():   # ventana aceptada por una regla de rescate: su rafaga frontal cuenta como pulso
                    co = np.logical_or.reduce([sobre.get(c, vacio) for c in FRONTALES if not ignorado[c]] or [vacio])
                if p.reposo_s > 0:                   # tramos de actividad frontal: los que se separan menos que reposo_s son el mismo tramo
                    # los dos frontales sobre el margen (AND): un FP con mal contacto queda siempre arriba y con OR tapaba la pausa (09-11)
                    fo = np.logical_and.reduce([sobre.get(c, vacio) for c in FRONTALES if not ignorado[c]] or [vacio])
                    nuevo = min(self.paso, W)        # solo las muestras nuevas de esta ventana: las demas ya las vio la anterior
                    df_ = np.diff(np.concatenate(([0], fo[-nuevo:].astype(np.int8), [0])))
                    n_rep = int(p.reposo_s * fs)
                    for fa, fb in zip(np.flatnonzero(df_ == 1), np.flatnonzero(df_ == -1)):
                        fa, fb = fin - nuevo + int(fa), fin - nuevo + int(fb)
                        if self.fint and fa - self.fint[-1][1] < n_rep:
                            self.fint[-1][1] = max(self.fint[-1][1], fb)
                        else:
                            self.fint.append([fa, fb])
                    self.fint = [q for q in self.fint if q[1] >= fin - int(2 * p.doble_s * fs)]
                d_ = np.diff(np.concatenate(([0], co.astype(np.int8), [0])))
                for a_, b_ in zip(np.flatnonzero(d_ == 1), np.flatnonzero(d_ == -1)):
                    a_, b_ = fin - W + int(a_), fin - W + int(b_)
                    if p.consumir_pulsos and a_ <= self.consumido:    # es (la continuacion de) una rafaga que ya se uso en un gesto
                        self.consumido = max(self.consumido, b_)
                        continue
                    # mismo tramo frontal (sin reposo entre medio): es el mismo apreton aunque la coincidencia se haya cortado
                    mismo_tramo = p.reposo_s > 0 and bool(self.pulsos) and any(q[0] <= self.pulsos[-1][1] and q[0] <= a_ <= q[1] for q in self.fint)
                    if self.pulsos and (a_ - self.pulsos[-1][1] < self.n_sep_pulso or mismo_tramo):     # misma rafaga vista por otra ventana
                        self.pulsos[-1][1] = max(self.pulsos[-1][1], b_)
                    else:
                        self.pulsos.append([a_, b_])
                self.pulsos = [q for q in self.pulsos if q[1] >= fin - int(2 * p.doble_s * fs)]
                if det and sum(q[1] >= fin - int(p.doble_s * fs) for q in self.pulsos) < p.n_pulsos:
                    det = False
                    via, freno = '', freno or f'un solo pulso (se espera el segundo, hasta {p.doble_s:.1f} s)'
        # rebote: una deteccion mas debil que la anterior (menos de rebote_frac) a menos de rebote_s es su cola
        if det and p.rebote_frac > 0:
            fuerza = max(pico[c] / margen[c] for c in CAN)
            if fin - self.ult_det[0] <= p.rebote_s * fs and fuerza < p.rebote_frac * self.ult_det[1]:
                det = False
                via, freno = '', freno or 'rebote (cola de la deteccion anterior)'
            else:
                self.ult_det[0], self.ult_det[1] = fin, fuerza
        det = bool(det)
        if det and p.n_pulsos > 1 and p.consumir_pulsos:
            self.consumido = fin + int(p.bloqueo_post_s * fs)    # las rafagas de este gesto (y las que sigan enseguida) no se reutilizan como primer disparo del siguiente
            self.pulsos = []
        # veto por movimiento de cabeza (acelerometro): el gesto se descarta, pero sus rafagas quedan consumidas
        if det and p.veto_acc:
            mov = self._veto_acc()
            if mov:
                det = False
                via, freno = '', mov
        if det:
            for c in CAN:
                if sup[c]:
                    self.spk[c] = pico[c] if self.spk[c] is None else (1 - p.alfa_spk) * self.spk[c] + p.alfa_spk * pico[c]
            self.activa_hasta = fin + self.paso + int(p.mantener_s * fs)
        ratios = {c: pico[c] / margen[c] for c in CAN}
        # ajuste del umbral de cada canal, por separado, solo con las ventanas de ruido
        motivos = {}
        for c in CAN:
            if ruido[c] is None or calibrando:
                uso, motivo = True, 'calibracion'
            elif clase[c] == 'ruido' and fin - W < self.ultimo_exceso[c] + self.n_espera:
                uso, motivo = False, 'espera'
            elif clase[c] == 'ruido':
                uso, motivo = True, 'ruido'
                self.cuenta[c] = 0
            elif clase[c] == 'ignorado':
                uso, motivo = True, 'seguimiento'
            else:
                self.cuenta[c] += 1
                if self.cuenta[c] >= (self.n_persist_det if ((det or fin - W < self.consumido) and p.persist_det_s > 0) else self.n_persist):
                    uso, motivo = True, 'ruido sostenido'
                    self.cuenta[c] = 0
                else:
                    uso, motivo = False, 'movimiento'
            motivos[c] = motivo
            if uso:
                if motivo == 'calibracion':
                    self.cal_n[c].append(nivel[c])
                    self.cal_p[c].append(pico[c])
                    ruido[c] = float(np.percentile(self.cal_n[c], p.pct_calib))
                    self.pico_r[c] = float(np.percentile(self.cal_p[c], p.pct_calib))
                    self.recientes[c].clear()
                    self.recientes[c].append(self.pico_r[c])
                elif motivo in ('ruido sostenido', 'seguimiento'):
                    ruido[c], self.pico_r[c] = nivel[c], pico[c]
                    self.recientes[c].clear()
                    self.recientes[c].append(pico[c])
                else:
                    ruido[c] = (1 - p.alfa) * ruido[c] + p.alfa * nivel[c]
                    self.recientes[c].append(pico[c])
                    self.pico_r[c] = max(self.recientes[c])
                if motivo in ('calibracion', 'ruido'):
                    self.largos[c].append(pico[c])
                valor = max(self.kg[c] * ruido[c], p.piso_pico * self.pico_r[c])
                if p.frac_spk > 0 and self.spk[c] is not None and self.spk[c] > self.pico_r[c]:
                    valor = max(valor, self.pico_r[c] + p.frac_spk * (self.spk[c] - self.pico_r[c]))
                piso = p.margen_min
                if p.piso_rel > 0 and self.largos[c]:
                    piso = max(piso, p.piso_rel * float(np.median(self.largos[c])))
                margen[c] = float(np.clip(valor, piso, p.margen_max))
        # exclusion: un canal mucho mas ruidoso que el otro de su grupo
        if p.excl_ratio > 0 and not calibrando:
            for a_, b_ in GRUPOS:
                if ruido[a_] is not None and ruido[b_] is not None:
                    ignorado[a_] = ruido[a_] > p.excl_ratio * max(ruido[b_], p.excl_piso)
                    ignorado[b_] = ruido[b_] > p.excl_ratio * max(ruido[a_], p.excl_piso)
        estado = {c: {'ruido': ruido[c], 'margen': margen[c], 'ratio': ratios[c], 'clase': clase[c], 'ajuste': motivos[c],
                      'ignorado': ignorado[c]} for c in CAN}
        if self.vis:
            fut = np.arange(fin, fin + self.paso) % self.R
            for i, c in enumerate(CAN):
                self.v_mg[i, fut] = margen[c]
                self.v_ign[i, fut] = ignorado[c]
            if det:
                self.v_det[np.arange(fin - W, fin) % self.R] = True
        previo = int(p.n_pulsos > 1 and any(q[1] >= fin - int(p.doble_s * fs) for q in self.pulsos))     # 1 = hay un gesto esperando su pareja
        self.ultima = {'senal_off': dict(self.senal_off), 'fin': fin, 't': fin / fs, 'det': det, 'calibrando': calibrando, 'ratios': ratios, 'votos': votos, 'previo': previo,
                       'margen': dict(margen), 'ignorado': dict(ignorado), 'via': via if det else '', 'freno': '' if det else freno,
                       'estado': estado}
        return self.ultima


# ==========================================
# 5. REGISTRO: nuestro filtro vs SDK vs Pico vs lo que marcas con la barra espaciadora
# ==========================================
class Registro:
    """Guarda las detecciones de cada detector, los rangos marcados y el contacto por sensor, y arma la comparacion."""
    DETECTORES = ('filtro', 'sdk')

    def __init__(self, paso_s=0.25, mantener_s=1.0):
        self.sep_filtro = paso_s + mantener_s       # ventanas con deteccion separadas menos que esto son el mismo gesto
        self.rangos = []                             # [ini, fin|None]
        self.eventos = {d: [] for d in self.DETECTORES}
        self.eventos['mandibula'] = []
        self.ult_filtro = -1e9
        self.contacto = {c: [] for c in CAN}         # (t, valor)
        self.t0 = None
        self.log = []                                # (t, detector, texto)

    def _t(self, t):
        if self.t0 is None:
            self.t0 = t
        return t

    # --- rangos marcados con la barra espaciadora ---
    def marcar_inicio(self, t):
        if not self.rangos or self.rangos[-1][1] is not None:
            self.rangos.append([self._t(t), None])

    def marcar_fin(self, t):
        if self.rangos and self.rangos[-1][1] is None:
            self.rangos[-1][1] = t

    @property
    def marcando(self):
        return bool(self.rangos) and self.rangos[-1][1] is None

    # --- detectores ---
    def det_filtro(self, t):
        """Una ventana con deteccion; devuelve True si empieza un gesto nuevo (la salida retenida pasa de 0 a 1)."""
        self._t(t)
        nuevo = (t - self.ult_filtro) > self.sep_filtro
        self.ult_filtro = t
        if nuevo:
            self.eventos['filtro'].append(t)
            self.log.append((t, 'filtro', 'gesto'))
        return nuevo

    def det_sdk(self, t):
        self._t(t)
        if not self.eventos['sdk'] or t - self.eventos['sdk'][-1] > 0.3:
            self.eventos['sdk'].append(t)
            self.log.append((t, 'sdk', 'blink'))

    def det_mandibula(self, t):
        self._t(t)
        self.eventos['mandibula'].append(t)
        self.log.append((t, 'mandibula', 'jaw_clench'))

    def horseshoe(self, t, vals):
        self._t(t)
        for c in CAN:
            if c in vals:
                self.contacto[c].append((t, int(vals[c])))
        estado = ' '.join(f"{c}={int(vals[c])}" for c in CAN if c in vals)
        if estado != getattr(self, '_ult_contacto', None):          # se anota solo cuando cambia: sirve para medir cuanto dura un falso contacto
            self._ult_contacto = estado
            self.log.append((t, 'contacto', estado))

    def contacto_en(self, c, t):
        """Ultimo valor de contacto del sensor c en el instante t (4 si no hay datos)."""
        v = 4
        for tt, x in self.contacto[c]:
            if tt > t:
                break
            v = x
        return v

    # --- comparacion ---
    def _ext(self, r, t_fin):
        ini, fin = r[0], (r[1] if r[1] is not None else t_fin)
        return ini - TOL_ANTES_S, fin + TOL_DESPUES_S

    def resumen(self, t_fin):
        rangos = [[a, b if b is not None else t_fin] for a, b in self.rangos]
        res = {'gestos_marcados': len(rangos), 'detectores': {}, 'contacto': {}, 'rangos': []}
        for d in self.DETECTORES:
            ev = self.eventos[d]
            ac = 0
            for a, b in rangos:
                if any(a - TOL_ANTES_S <= t <= b + TOL_DESPUES_S for t in ev):
                    ac += 1
            fp = sum(1 for t in ev if not any(a - TOL_ANTES_S <= t <= b + TOL_DESPUES_S for a, b in rangos))
            res['detectores'][d] = {'detecciones': len(ev), 'aciertos': ac, 'perdidos': len(rangos) - ac, 'falsos_positivos': fp,
                                    'sensibilidad': (ac / len(rangos)) if rangos else None}
        res['mandibula_eventos'] = len(self.eventos['mandibula'])
        for c in CAN:
            hist = self.contacto[c]
            tiempos = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
            for i, (t, v) in enumerate(hist):
                t2 = hist[i + 1][0] if i + 1 < len(hist) else t_fin
                tiempos[v if v in tiempos else 4] += max(t2 - t, 0.0)
            tot = sum(tiempos.values())
            res['contacto'][c] = {'segundos_por_estado': {str(k): round(v, 1) for k, v in tiempos.items()},
                                  'pct_bueno': round(100 * (tiempos[1] + tiempos[2]) / tot, 1) if tot > 0 else None}
        for i, (a, b) in enumerate(rangos, 1):
            fila = {'n': i, 'ini': round(a - (self.t0 or 0), 2), 'dur': round(b - a, 2)}
            for d in self.DETECTORES:
                fila[d] = int(any(a - TOL_ANTES_S <= t <= b + TOL_DESPUES_S for t in self.eventos[d]))
            for c in CAN:
                vals = [self.contacto_en(c, a)] + [x for t, x in self.contacto[c] if a < t <= b]
                fila['contacto_' + c] = max(vals)           # el peor estado durante el gesto
            res['rangos'].append(fila)
        return res

    def texto_resumen(self, t_fin):
        r = self.resumen(t_fin)
        l = [f"Gestos marcados con la barra espaciadora: {r['gestos_marcados']}", '',
             f"{'detector':8s} {'detecciones':>11s} {'aciertos':>9s} {'perdidos':>9s} {'falsos+':>8s} {'sensib.':>8s}"]
        for d, x in r['detectores'].items():
            s = f"{100 * x['sensibilidad']:.0f}%" if x['sensibilidad'] is not None else '-'
            l.append(f"{d:8s} {x['detecciones']:11d} {x['aciertos']:9d} {x['perdidos']:9d} {x['falsos_positivos']:8d} {s:>8s}")
        l.append(f"\nEventos de mandibula (SDK): {r['mandibula_eventos']}")
        l.append("\nContacto por sensor (1 bueno, 2 aceptable, 3 malo, 4 sin contacto):")
        for c, x in r['contacto'].items():
            if x['pct_bueno'] is None:
                l.append(f"  {c:5s} sin datos de contacto (no llegaron paquetes HORSESHOE)")
            else:
                l.append(f"  {c:5s} segundos por estado {x['segundos_por_estado']}  -> {x['pct_bueno']}% con contacto util")
        if r['rangos']:
            l.append("\nPor gesto (contacto = peor estado de cada sensor durante el gesto):")
            l.append(f"{'n':>3s} {'ini(s)':>8s} {'dur':>5s} {'filtro':>7s} {'sdk':>4s}   " + ' '.join(f'{c:>5s}' for c in CAN))
            for f in r['rangos']:
                l.append(f"{f['n']:3d} {f['ini']:8.1f} {f['dur']:5.1f} {f['filtro']:7d} {f['sdk']:4d}   " +
                         ' '.join(f"{f['contacto_' + c]:5d}" for c in CAN))
        return '\n'.join(l)

    def guardar(self, prefijo, t_fin):
        r = self.resumen(t_fin)
        with open(f'{prefijo}_resumen.json', 'w', encoding='utf-8') as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        with open(f'{prefijo}_resumen.txt', 'w', encoding='utf-8') as f:
            f.write(self.texto_resumen(t_fin) + '\n')
        with open(f'{prefijo}_rangos.csv', 'w', newline='', encoding='utf-8') as f:
            if r['rangos']:
                w = csv.DictWriter(f, fieldnames=list(r['rangos'][0].keys()))
                w.writeheader()
                w.writerows(r['rangos'])
        with open(f'{prefijo}_eventos.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['t_s', 'detector', 'evento', 'resultado', 'gesto_marcado_n'])
            rangos = [(a, b if b is not None else t_fin) for a, b in self.rangos]
            filas = [(a, 'marca', 'inicio', '', i) for i, (a, b) in enumerate(rangos, 1)] +                     [(b, 'marca', 'fin', '', i) for i, (a, b) in enumerate(rangos, 1)]
            for t, d, txt in self.log:
                if d in self.DETECTORES:
                    n = next((i for i, (a, b) in enumerate(rangos, 1) if a - TOL_ANTES_S <= t <= b + TOL_DESPUES_S), None)
                    filas.append((t, d, txt, 'acierto' if n else 'falso_positivo', n or ''))
                else:
                    filas.append((t, d, txt, '', ''))
            for t, d, txt, res_, n in sorted(filas, key=lambda x: x[0]):
                w.writerow([round(t - (self.t0 or 0), 3), d, txt, res_, n])


class GrabadorEEG:
    """Guarda la señal cruda en el formato de los datasets: Timestamp, Trigger (barra espaciadora), Sensores_OK, TP9..TP10."""

    def __init__(self, ruta, fs):
        self.f = open(ruta, 'w', newline='', encoding='utf-8')
        self.w = csv.writer(self.f)
        self.w.writerow(['Timestamp', 'Trigger', 'Sensores_OK', 'TP9', 'FP1', 'FP2', 'TP10'])
        self.fs = fs
        self.n = 0
        self.t0 = None

    def escribir(self, t_llegada, muestras, trigger, sensores_ok):
        if self.t0 is None:
            self.t0 = t_llegada
        for m in muestras:
            self.w.writerow([f'{self.t0 + self.n / self.fs:.6f}', int(trigger), int(sensores_ok), *[f'{v:.4f}' for v in m]])
            self.n += 1

    def cerrar(self):
        self.f.close()


# ==========================================
# 6. SESION (une motor + registro + grabacion; sirve en vivo, en consola y en simulacion)
# ==========================================
class Sesion:
    def __init__(self, fs=FS, guardar=True, prefijo=None, vis=False, etiqueta='', fluido=False):
        self.fs = fs
        self.fluido = fluido                         # True: cada lote va al motor al llegar (la curva del tablero avanza sin esperar el paso)
        self.motor = MotorGestos(fs, vis=vis)
        self.reg = Registro(self.motor.p.paso_s, self.motor.p.mantener_s)
        self.contacto = {c: 4 for c in CAN}
        self.t_ult = None
        self.t_base = None                           # instante (de pared) de la muestra 0
        self.t_pared = None                          # llegada del ultimo lote
        self.pend = 0                                # muestras EEG ya leidas del socket pero aun no procesadas
        self.cola, self.n_cola = [], 0               # muestras recibidas que esperan completar un paso para ir al motor
        self.t_rx0 = None                          # llegada del primer lote y total de muestras: tasa efectiva de la red
        self.n_rx = 0
        self.max_pausa = 0.0                         # mayor pausa entre dos lotes con EEG (cortes del flujo)
        self.n_ventanas = 0
        self.n_det_ventanas = 0
        self.grabador = None
        self.prefijo = None
        if guardar:
            SESIONES.mkdir(exist_ok=True)
            inicio = (NOMBRE.strip().replace(' ', '_') + '_') if NOMBRE.strip() else 'sesion_'
            self.prefijo = prefijo or str(SESIONES / (inicio + time.strftime('%Y%m%d_%H%M%S') + (f'_{etiqueta}' if etiqueta else '')))
            self.grabador = GrabadorEEG(self.prefijo + '_eeg.csv', fs)

    def reloj(self):
        """Tiempo en el eje de las muestras recibidas (n / fs). Las marcas, los BLINK y las detecciones se fechan con este reloj,
        asi quedan alineados con el EEG aunque el flujo llegue con cortes o en rafagas (lo que importa es el orden causal)."""
        if self.t_base is None:
            return time.time()
        return self.t_base + (self.n_rx + self.pend) / self.fs

    def sensores_ok(self):
        return all(v <= CONTACTO_BUENO for v in self.contacto.values())

    def eeg(self, t, muestras):
        """Un lote de muestras (n, 4) llegado en el instante t. Devuelve las decisiones por ventana."""
        if len(muestras) == 0:
            return []
        if self.t_rx0 is None:
            self.t_rx0 = t
            self.t_base = t - len(muestras) / self.fs
        elif self.t_pared is not None:
            self.max_pausa = max(self.max_pausa, t - self.t_pared)
        self.t_pared = t
        self.n_rx += len(muestras)
        t = self.reloj()                             # de aca en adelante todo va en tiempo de muestras, no de reloj de pared
        self.t_ult = t
        if self.grabador is not None:                # se graba al llegar: el Trigger queda en la muestra en que apretaste
            self.grabador.escribir(t, muestras, self.reg.marcando, self.sensores_ok())
        self.cola.append(np.asarray(muestras, dtype=float).reshape(-1, 4))
        self.n_cola += len(muestras)
        if not self.fluido and self.n_cola < self.motor.paso:   # el motor solo decide cada `paso` muestras: procesar de a menos es solo costo
            return []
        return self._procesar_cola()

    def _procesar_cola(self):
        if not self.cola:
            return []
        bloque = np.vstack(self.cola)
        self.cola, self.n_cola = [], 0
        decisiones = self.motor.agregar(bloque)
        t_proc = self.t_base + self.motor.flujo.n / self.fs       # fin de la ultima muestra procesada
        for d in decisiones:
            self.n_ventanas += 1
            if d['det']:
                self.n_det_ventanas += 1
                self.reg.det_filtro(t_proc)
        return decisiones

    def evento(self, t, msg):
        """Un paquete no-EEG del ZMQ (BLINK, JAW_CLENCH, HORSESHOE...), fechado en el eje de las muestras recibidas hasta ese momento."""
        t = self.reloj()
        tipo = msg.get('tipo')
        if tipo == 'BLINK':
            if int(msg.get('valor', 0)) == 1:
                self.reg.det_sdk(t)
        elif tipo == 'JAW_CLENCH':
            if int(msg.get('valor', 0)) == 1:
                self.reg.det_mandibula(t)
        elif tipo == 'FRENTE':
            self.motor.set_frente(msg.get('valor'))
        elif tipo == 'HORSESHOE':
            vals = {c: int(msg.get(c, 4)) for c in CAN}
            self.contacto.update(vals)
            self.motor.set_contacto(vals)
            self.reg.horseshoe(t, vals)

    def cerrar(self):
        self._procesar_cola()
        t_fin = self.t_ult if self.t_ult is not None else time.time()
        self.reg.marcar_fin(t_fin)
        texto = self.reg.texto_resumen(t_fin)
        if self.t_rx0 is not None and self.t_pared - self.t_rx0 > 1:
            dur = self.t_pared - self.t_rx0
            tasa = self.n_rx / dur
            texto += (f"\n\nFlujo EEG: {self.n_rx} muestras en {dur:.1f} s de reloj = {tasa:.0f} Hz efectivos (esperado {self.fs:.0f}), "
                      f"mayor pausa entre lotes {self.max_pausa:.2f} s")
            if tasa < 0.97 * self.fs or self.max_pausa > 0.5:
                texto += "\n  AVISO: el flujo llego con perdidas o cortes; las marcas (reloj de la PC) pueden quedar desalineadas del EEG."
        if self.grabador is not None:
            self.grabador.cerrar()
            self.reg.guardar(self.prefijo, t_fin)
            texto += f"\n\nArchivos guardados con el prefijo {self.prefijo}"
        return texto


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


# ==========================================
# 7. ENTRADA ZMQ
# ==========================================
def conectar(ip, puerto=PUERTO):
    import zmq
    context = zmq.Context()
    sock = context.socket(zmq.SUB)
    sock.connect(f"tcp://{ip}:{puerto}")
    sock.setsockopt(zmq.RCVHWM, 0)                   # sin tope de cola: si la interfaz se atrasa, no se descartan muestras
    sock.setsockopt_string(zmq.SUBSCRIBE, "")
    return zmq, context, SockLotes(sock)


def drenar(zmq, sock, sesion):
    """Lee todo lo pendiente del socket. Los EEG se juntan en un lote; los demas paquetes se registran con su hora de llegada."""
    lote, n = [], 0
    try:
        while True:
            msg = sock.recv_json(flags=zmq.NOBLOCK)
            n += 1
            if msg.get('tipo') == 'EEG':
                lote.append([float(msg.get(f'{c}_raw', 0.0)) for c in CAN])
            else:
                sesion.pend = len(lote)
                sesion.evento(time.time(), msg)
    except zmq.Again:
        pass
    sesion.pend = 0
    return np.asarray(lote, dtype=float).reshape(-1, 4), n


# ==========================================
# 8. MODO CONSOLA
# ==========================================
def modo_consola(ip, puerto=PUERTO, etiqueta=''):
    zmq, context, sock = conectar(ip, puerto)
    ses = Sesion(FS, etiqueta=etiqueta)
    print(f"Escuchando tcp://{ip}:{puerto} (Ctrl+C para terminar y guardar). Sin barra espaciadora en este modo: "
          f"solo se cuentan las detecciones.")
    ultimo_print = 0
    try:
        while True:
            lote, n = drenar(zmq, sock, ses)
            t = time.time()
            for d in ses.eeg(t, lote):
                if d['det']:
                    print(f"[{time.strftime('%H:%M:%S')}] filtro: ventana con gesto (ratios {{{', '.join(f'{c}:{r:.1f}' for c, r in d['ratios'].items())}}})")
            if t - ultimo_print > 5:
                ultimo_print = t
                e = ses.reg.eventos
                print(f"  [{time.strftime('%H:%M:%S')}] muestras={ses.motor.flujo.n} gestos: filtro={len(e['filtro'])}  sdk(blink)={len(e['sdk'])}  "
                      f"contacto={ses.contacto}")
            time.sleep(0.02)
    except KeyboardInterrupt:
        print("\n" + ses.cerrar())


# ==========================================
# 9. TABLERO EN VIVO (pyqtgraph)
# ==========================================
def modo_tablero(ip, puerto=PUERTO, liviano=False, etiqueta=''):
    """Tablero igual a los graficos de errores: por canal la señal filtrada (sin su base), el margen adaptativo (verde), las
    muestras sobre el margen (rojo), las ventanas con deteccion (naranja), los tramos en que el canal esta ignorado (rosa),
    lo que marcas con ESPACIO (gris) y los BLINK del SDK (celeste).
    La recepcion corre en un temporizador rapido (20 ms) y el dibujo en otro mas lento (100 ms): si dibujar tarda, no se atrasa
    la lectura del socket. Los textos van en etiquetas de ancho fijo para que la ventana no cambie de tamaño al cambiar el texto."""
    import pyqtgraph as pg
    from pyqtgraph.Qt import QtCore, QtWidgets

    zmq, context, sock = conectar(ip, puerto)
    ses = Sesion(FS, vis=True, etiqueta=etiqueta, fluido=True)   # el EEG se graba al llegar; "fluido" solo cambia cuando se actualiza el dibujo
    app = QtWidgets.QApplication(sys.argv)
    VENT_S = 5.0 if liviano else 8.0
    N = int(VENT_S * FS)

    class Ventana(QtWidgets.QWidget):
        def keyPressEvent(self, ev):
            if ev.key() == QtCore.Qt.Key_Space and not ev.isAutoRepeat():
                ses.reg.marcar_inicio(ses.reloj())
            elif ev.key() in (QtCore.Qt.Key_Q, QtCore.Qt.Key_Escape):
                self.close()

        def keyReleaseEvent(self, ev):
            if ev.key() == QtCore.Qt.Key_Space and not ev.isAutoRepeat():
                ses.reg.marcar_fin(ses.reloj())

    class Regiones:
        """Franjas de color de fondo (una por tramo)."""

        def __init__(self, plot, color, z):
            self.plot, self.z, self.items = plot, z, []
            self.brush = pg.mkBrush(color)

        def poner(self, tramos_):
            while len(self.items) < len(tramos_):
                it = pg.LinearRegionItem(values=(0, 0), brush=self.brush, pen=pg.mkPen(None), movable=False)
                it.setZValue(self.z)
                for ln in it.lines:
                    ln.hide()
                self.plot.addItem(it)
                self.items.append(it)
            for it, (x0, x1) in zip(self.items, tramos_):
                it.setRegion((x0, x1))
                it.show()
            for it in self.items[len(tramos_):]:
                it.hide()

    def tramos(mascara, t):
        """Tramos contiguos de una mascara booleana, como (x_ini, x_fin) en segundos."""
        if not mascara.any():
            return []
        d = np.diff(np.concatenate(([0], mascara.astype(np.int8), [0])))
        ini, fin = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        return [(float(t[a]), float(t[min(b, len(t)) - 1])) for a, b in zip(ini, fin)]

    win = Ventana()
    win.setWindowTitle("Gestos en tiempo real - umbral adaptativo, deteccion, SDK y barra espaciadora")
    win.setFocusPolicy(QtCore.Qt.StrongFocus)
    win.setStyleSheet("background-color: #14161a;")
    win.resize(1250, 900)
    caja = QtWidgets.QVBoxLayout(win)
    caja.setContentsMargins(6, 4, 6, 4)
    caja.setSpacing(2)

    def etiqueta(pt, alto):
        lb = QtWidgets.QLabel("")
        lb.setWordWrap(True)
        lb.setTextFormat(QtCore.Qt.RichText)
        lb.setStyleSheet(f"color: #e6e6e6; font-size: {pt}pt;")
        lb.setFixedHeight(alto)
        lb.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)   # el texto nunca ensancha la ventana
        caja.addWidget(lb)
        return lb

    lbl_estado = etiqueta(13, 26)
    lbl_estado.setText("Esperando datos...")
    lbl_cont = etiqueta(11, 22)
    lbl_flujo = etiqueta(10, 22)
    lbl_dec = etiqueta(10, 36)
    lbl_cmp = etiqueta(10, 22)
    lbl_ult = etiqueta(9, 36)
    graf = pg.GraphicsLayoutWidget()
    graf.setFocusPolicy(QtCore.Qt.NoFocus)           # las teclas (ESPACIO, Q) las recibe la ventana
    caja.addWidget(graf, 1)
    colores = [(120, 160, 255), (120, 255, 120), (255, 150, 255), (255, 255, 120)]
    P_ = {}
    for i, c in enumerate(CAN):
        pl = graf.addPlot(row=i, col=0)
        if not liviano:
            pl.showGrid(x=True, y=True, alpha=0.25)
        pl.setXRange(-VENT_S, 0, padding=0)
        pl.setLabel('left', f'{c} (uV)')
        pl.setMouseEnabled(x=False, y=False)
        pl.getAxis('left').setWidth(62)              # ejes alineados entre canales aunque cambie el rango
        P_[c] = {
            'plot': pl,
            'sig': pl.plot(pen=pg.mkPen(color=colores[i], width=1)),
            'mp': pl.plot(pen=pg.mkPen((0, 200, 0), width=2), connect='finite'),
            'mn': pl.plot(pen=pg.mkPen((0, 200, 0), width=2), connect='finite'),
            'sobre': pg.ScatterPlotItem(size=5, pen=None, brush=pg.mkBrush(255, 0, 0)),
            'det': Regiones(pl, (255, 170, 0, 70), -30), 'ign': Regiones(pl, (255, 100, 160, 80), -20),
            'marca': Regiones(pl, (160, 160, 160, 70), -40), 'sdk': [], 'ym': 60.0, 'titulo': None}
        pl.addItem(P_[c]['sobre'])
        if i < 3:
            pl.getAxis('bottom').setStyle(showValues=False)
        else:
            pl.setLabel('bottom', 'segundos de señal (0 = ultima muestra recibida)')
    NOMBRE = {1: 'bueno', 2: 'ok', 3: 'malo', 4: 'sin'}
    COL = {1: '#4caf50', 2: '#c0ca33', 3: '#ff9800', 4: '#f44336'}
    ultimos = deque(maxlen=6)
    ult_det_t = [None]
    hist = deque(maxlen=600)                         # (reloj de pared, muestras recibidas) para la tasa de los ultimos segundos
    st = {'rx': time.time(), 'lote_max': 0, 'lote_t': time.time(), 'lote_5s': 0}
    ciclos = deque(maxlen=40)                        # duracion (ms) de cada dibujo
    textos = {}

    def poner(lb, texto):
        """setText solo si cambio: cada setText vuelve a calcular la disposicion."""
        if textos.get(id(lb)) != texto:
            textos[id(lb)] = texto
            lb.setText(texto)

    def recibir():
        lote, _ = drenar(zmq, sock, ses)
        ahora = time.time()
        if len(lote):
            st['rx'] = ahora
            if ahora - st['lote_t'] > 5:
                st['lote_t'], st['lote_5s'] = ahora, 0
            st['lote_5s'] = max(st['lote_5s'], len(lote))
        for d in ses.eeg(ahora, lote):
            if d['det'] and ult_det_t[0] != d['fin']:
                ult_det_t[0] = d['fin']
                ultimos.append(f"{time.strftime('%H:%M:%S')}  {d['via']}")
        hist.append((ahora, ses.n_rx))

    def dibujar():
        t_ini = time.perf_counter()
        m = ses.motor
        if m.flujo.n < 10:
            return
        ahora = ses.t_base + m.flujo.n / ses.fs      # eje de muestras: fin de la ultima muestra dibujada
        v = m.vista(N)
        t = v['t']
        rang = [(max(a - ahora, -VENT_S), (b if b is not None else ahora) - ahora) for a, b in ses.reg.rangos
                if (b if b is not None else ahora) - ahora > -VENT_S]
        sdk = [e - ahora for e in ses.reg.eventos['sdk'] if -VENT_S <= e - ahora <= 0]
        for i, c in enumerate(CAN):
            q = P_[c]
            xs, mg = v['xs'][i], v['mg'][i]
            q['sig'].setData(t, xs)
            q['mp'].setData(t, mg)
            q['mn'].setData(t, -mg)
            sobre = np.abs(xs) > np.where(np.isnan(mg), np.inf, mg)
            if not liviano:
                q['sobre'].setData(t[sobre], xs[sobre])
            q['det'].poner(tramos(v['det'], t))
            q['ign'].poner(tramos(v['ign'][i], t))
            q['marca'].poner(rang)
            while len(q['sdk']) < len(sdk):
                ln = pg.InfiniteLine(angle=90, pen=pg.mkPen((0, 220, 255), width=2))
                q['plot'].addItem(ln)
                q['sdk'].append(ln)
            for ln, x in zip(q['sdk'], sdk):
                ln.setValue(x)
                ln.show()
            for ln in q['sdk'][len(sdk):]:
                ln.hide()
            tope = max(1.4 * (np.nanmax(mg) if np.isfinite(mg).any() else 60.0), 1.2 * float(np.abs(xs).max()), 40.0)
            nuevo = max(min(tope, 1400.0), 0.92 * q['ym'])
            if abs(nuevo - q['ym']) > 0.06 * q['ym']:    # el rango solo se mueve si cambia de verdad (evita recalcular a cada cuadro)
                q['ym'] = nuevo
                q['plot'].setYRange(-nuevo, nuevo, padding=0)
            e = m.ultima['estado'][c] if m.ultima else None
            if e:
                rz = '-' if e['ruido'] is None else f"{e['ruido']:.0f}"
                est = 'IGNORADO' if e['ignorado'] else e['clase']
                titulo = f"{c}  ruido {rz} uV  margen {e['margen']:.0f} uV  pico/margen {e['ratio']:.1f}  {est}"
                if titulo != q['titulo']:
                    q['titulo'] = titulo
                    q['plot'].setTitle(titulo, size='9pt')
        ev = ses.reg.eventos
        activo = m.salida()
        marca = "   |   GESTO MARCADO (espacio)" if ses.reg.marcando else ""
        calib = "   |   CALIBRANDO (4 s)" if m.ultima and m.ultima['calibrando'] else ""
        poner(lbl_estado, f"FILTRO: {len(ev['filtro'])} gestos   SDK(blink): {len(ev['sdk'])}   MARCADOS: {len(ses.reg.rangos)}"
                          f"   {'[ DETECTADO ]' if activo else ''}{marca}{calib}")
        poner(lbl_cont, "Contacto: " + "   ".join(
            f"{c}: <span style='color:{COL[ses.contacto[c]]}'>{NOMBRE.get(ses.contacto[c], '?')}</span>" for c in CAN))
        # estado del flujo: tasa de los ultimos segundos, pausa desde la ultima muestra y retraso de la señal frente al reloj
        ahora_p = time.time()
        while len(hist) > 2 and ahora_p - hist[0][0] > 5:
            hist.popleft()
        tasa = (hist[-1][1] - hist[0][1]) / max(hist[-1][0] - hist[0][0], 1e-6) if len(hist) > 2 else 0.0
        pausa = ahora_p - st['rx']
        retraso = (ahora_p - ses.t_base) - ses.n_rx / ses.fs if ses.t_base is not None else 0.0
        cic = (sum(ciclos) / len(ciclos), max(ciclos)) if ciclos else (0.0, 0.0)
        bien = tasa >= 0.9 * FS and pausa < 0.3 and cic[0] < 70
        color = '#4caf50' if bien else '#ff9800'
        poner(lbl_flujo, f"<span style='color:{color}'>FLUJO: {tasa:.0f} muestras/s (esperado {FS:.0f})   sin datos hace {pausa:.1f} s   "
                         f"mayor lote {st['lote_5s']}   señal atrasada {retraso:+.1f} s   dibujo {cic[0]:.0f} ms (max {cic[1]:.0f})</span>")
        u = m.ultima
        if u:
            rt = '  '.join(f"{c} {u['ratios'][c]:.1f}" for c in CAN)
            if u['det']:
                poner(lbl_dec, f"Ultima ventana: DETECTADA por {u['via']}   |   pico/margen: {rt}")
            else:
                poner(lbl_dec, "Ultima ventana: sin deteccion" + (f" (frenada por: {u['freno']})" if u['freno'] else "")
                      + f"   |   pico/margen: {rt}")
        r = ses.reg.resumen(ses.t_ult or ahora)
        poner(lbl_cmp, "   ".join(
            f"{d.upper()}: {x['aciertos']}/{r['gestos_marcados']} aciertos, {x['perdidos']} perdidos, {x['falsos_positivos']} falsos+"
            for d, x in r['detectores'].items()))
        poner(lbl_ult, "Detecciones del filtro (ultimas): " + (" | ".join(ultimos) if ultimos else "-") +
              "<br>verde = margen, rojo = sobre el margen, naranja = ventana detectada, rosa = canal ignorado, gris = ESPACIO, "
              "celeste = BLINK del SDK")
        ciclos.append((time.perf_counter() - t_ini) * 1000)
        # si dibujar tarda, se dibuja menos seguido (la lectura y el filtro siguen a su ritmo); si sobra tiempo, se vuelve a 10 cuadros/s
        medio = sum(ciclos) / len(ciclos)
        nuevo_ms = int(min(500, max(100, 3 * medio)))
        if abs(nuevo_ms - t_dib.interval()) >= 50:
            t_dib.setInterval(nuevo_ms)

    t_rec = QtCore.QTimer()
    t_rec.timeout.connect(recibir)
    t_rec.start(20)
    t_dib = QtCore.QTimer()
    t_dib.timeout.connect(dibujar)
    t_dib.start(100)
    win.show()
    app.aboutToQuit.connect(lambda: print("\n" + ses.cerrar()))
    print(f"Conectado a {ip}:{puerto}. Mantene ESPACIO mientras hacés el gesto. Q/ESC para cerrar y guardar.")
    sys.exit(app.exec())


# ==========================================
# 10. SIMULACION Y VERIFICACION CON UN DATASET
# ==========================================
def _cargar_csv(ruta):
    df = pd.read_csv(ruta)
    tiempo = df['Timestamp'] - df['Timestamp'].iloc[0]
    fs = round((len(df) - 1) / tiempo.iloc[-1], 2)    # 219.9999991 haria que int(0.5 * fs) fuera 109 y no 110: otra ventana
    return df, fs


def modo_simular(ruta):
    """Reproduce un dataset en bloques como si llegara por ZMQ: el Trigger hace de barra espaciadora."""
    df, fs = _cargar_csv(ruta)
    ses = Sesion(fs, guardar=False)
    x = df[CAN].values.astype(float)
    trig = df['Trigger'].values if 'Trigger' in df else np.zeros(len(df))
    paso = 55
    for a in range(0, len(x), paso):
        t = (a + paso) / fs
        for k in range(a, min(a + paso, len(x))):
            if trig[k] and not ses.reg.marcando:
                ses.reg.marcar_inicio(k / fs)
            elif not trig[k] and ses.reg.marcando:
                ses.reg.marcar_fin(k / fs)
        ses.eeg(t, x[a:a + paso])
    ses.t_ult = len(x) / fs
    ses.reg.marcar_fin(ses.t_ult)
    print(f"{Path(ruta).name}: fs estimada {fs:.1f} Hz, {ses.n_ventanas} ventanas ({ses.n_det_ventanas} con deteccion)")
    print(ses.reg.texto_resumen(ses.t_ult))


def modo_verificar(ruta):
    """Comprueba que el motor en flujo da exactamente las mismas decisiones por ventana que ventanas_deslizantes.simular."""
    sys.path.insert(0, str(AQUI / 'analisis'))
    import ventanas_deslizantes as v
    df, fs = v.cargar(ruta)
    _, _, desp = v.preparar_senales(df, fs)
    sim = v.simular(desp, fs, v.PV())
    motor = MotorGestos(fs)
    x = df[CAN].values.astype(float)
    flujo = {}
    for a in range(0, len(x), motor.paso):
        for d in motor.agregar(x[a:a + motor.paso]):
            flujo[d['fin']] = d['det']
    comunes = sorted(set(flujo) & set(sim['det_por_fin']))
    dif = [f for f in comunes if flujo[f] != sim['det_por_fin'][f]]
    print(f"{Path(ruta).name}: fs {fs:.1f} Hz, {len(comunes)} ventanas comparadas, "
          f"{sum(sim['det_por_fin'][f] for f in comunes)} con deteccion offline / {sum(flujo[f] for f in comunes)} en flujo, "
          f"{len(dif)} diferencias" + (f" (en t = {[round(f / fs, 2) for f in dif[:8]]} s)" if dif else " -> IDENTICO"))
    return len(dif)


def modo_imagenes(ruta, contexto_s=3.0):
    """Una imagen por cada deteccion fuera de tus marcas (posible falso positivo) y por cada gesto marcado que no se detecto
    (falso negativo), a partir de la grabacion cruda de una sesion (sesion_<fecha>_eeg.csv, con el Trigger = barra espaciadora).
    Por canal: señal filtrada sin su base, margen adaptativo (verde), muestras sobre el margen (rojo), ventanas con deteccion
    (naranja), canal ignorado (rosa) y tu marca (gris). El titulo dice que regla detecto o que la frenó."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from scipy.ndimage import label
    df = pd.read_csv(ruta)
    fs = round((len(df) - 1) / (df['Timestamp'].iloc[-1] - df['Timestamp'].iloc[0]), 2)   # igual que en vivo (220.0 exacto)
    x = df[CAN].values.astype(float)
    trig = df['Trigger'].values.astype(bool)
    m = MotorGestos(fs, vis=True)
    R = len(x) + int(10 * fs)                       # anillos del tamaño de toda la grabacion: indices absolutos
    m.R = R
    m.v_xs, m.v_mg = np.zeros((4, R)), np.full((4, R), np.nan)
    m.v_ign, m.v_det = np.zeros((4, R), dtype=bool), np.zeros(R, dtype=bool)
    dec = []
    for a in range(0, len(x), 55):
        dec += m.agregar(x[a:a + 55])
    l, n = label(trig)
    rangos = [(int(np.flatnonzero(l == k)[0]), int(np.flatnonzero(l == k)[-1])) for k in range(1, n + 1)]
    eventos = []                                     # grupos de ventanas con deteccion separadas menos de 1.3 s
    for q in dec:
        if not q['det']:
            continue
        if eventos and q['fin'] - eventos[-1]['fin1'] <= 1.3 * fs:
            eventos[-1]['fin1'] = q['fin']
            eventos[-1]['q'].append(q)
        else:
            eventos.append({'fin0': q['fin'], 'fin1': q['fin'], 'q': [q]})
    tol = int(1.0 * fs)

    def marcado(i0, i1):
        return any(a - tol <= i1 and i0 <= b + tol for a, b in rangos)

    casos = []
    for e in eventos:
        if not marcado(e['fin0'] - m.W, e['fin1']):
            casos.append(('falso_positivo', e['fin0'] - m.W, e['fin1'], e['q'][0]['via']))
    for a, b in rangos:
        if not any(e['fin0'] - m.W <= b + tol and a - tol <= e['fin1'] for e in eventos):
            casos.append(('falso_negativo', a, b, ''))
    carpeta = Path(str(ruta).replace('_eeg.csv', '')).parent / (Path(ruta).name.replace('_eeg.csv', '') + '_imagenes')
    carpeta.mkdir(exist_ok=True)
    colores = [(0.35, 0.5, 1.0), (0.2, 0.7, 0.2), (0.8, 0.3, 0.8), (0.7, 0.6, 0.0)]
    print(f"{Path(ruta).name}: fs {fs:.1f} Hz, {len(rangos)} gestos marcados, {len(eventos)} detecciones, {len(casos)} casos para revisar")
    for k, (tipo, i0, i1, via) in enumerate(casos, 1):
        a = max(int(i0 - contexto_s * fs), 0)
        b = min(int(i1 + contexto_s * fs), len(x))
        t = (np.arange(a, b) - i0) / fs
        fig, ejes = plt.subplots(4, 1, figsize=(13, 8.5), sharex=True)
        for i, c in enumerate(CAN):
            ax = ejes[i]
            xs, mg = m.v_xs[i, a:b], m.v_mg[i, a:b]
            for ra, rb in rangos:                    # tu marca
                if rb >= a and ra <= b:
                    ax.axvspan((ra - i0) / fs, (rb - i0) / fs, color='0.8', alpha=0.6, lw=0)
            tramos = np.flatnonzero(np.diff(np.concatenate(([0], m.v_det[a:b].astype(int), [0]))))
            for s0, s1 in zip(tramos[::2], tramos[1::2]):
                ax.axvspan(t[s0], t[min(s1, len(t)) - 1], color='orange', alpha=0.25, lw=0)
            tramos = np.flatnonzero(np.diff(np.concatenate(([0], m.v_ign[i, a:b].astype(int), [0]))))
            for s0, s1 in zip(tramos[::2], tramos[1::2]):
                ax.axvspan(t[s0], t[min(s1, len(t)) - 1], color='pink', alpha=0.5, lw=0)
            ax.plot(t, xs, color=colores[i], lw=0.8)
            ax.plot(t, mg, color='green', lw=1.5)
            ax.plot(t, -mg, color='green', lw=1.5)
            sobre = np.abs(xs) > np.where(np.isnan(mg), np.inf, mg)
            ax.plot(t[sobre], xs[sobre], 'r.', ms=3)
            ax.set_ylabel(f'{c} (uV)')
            ax.grid(alpha=0.3)
            vis_ = np.nanmax(mg) if np.isfinite(mg).any() else 100
            ax.set_ylim(-max(1.5 * vis_, 1.2 * np.abs(xs).max(), 50), max(1.5 * vis_, 1.2 * np.abs(xs).max(), 50))
        # que dijo el filtro en el instante del caso
        ventana = [q for q in dec if i0 <= q['fin'] <= i1 + m.W]
        resumen = ''
        if ventana:
            q = max(ventana, key=lambda z: max(z['ratios'].values()))
            resumen = '  |  pico/margen max: ' + ' '.join(f"{c} {q['ratios'][c]:.1f}" for c in CAN)
            if tipo == 'falso_negativo':
                frenos = [z['freno'] for z in ventana if z['freno']]
                resumen += '  |  frenos: ' + (frenos[0] if frenos else 'ninguna regla lo candidateo (no hubo frontal y temporal sobre su margen)')
        tit = {'falso_positivo': f'POSIBLE FALSO POSITIVO (sin marca tuya) a los {i0 / fs:.1f} s  |  regla: {via}',
               'falso_negativo': f'GESTO MARCADO NO DETECTADO a los {i0 / fs:.1f} s'}[tipo] + resumen
        fig.suptitle(tit, fontsize=10)
        ejes[-1].set_xlabel('segundos desde el inicio del caso   (gris = tu marca, naranja = ventana detectada, rosa = canal ignorado, rojo = sobre el margen)')
        fig.tight_layout(rect=(0, 0, 1, 0.96))
        arch = carpeta / f"{k:02d}_{tipo}_{i0 / fs:.0f}s.png"
        fig.savefig(arch, dpi=90)
        plt.close(fig)
        print('  ', arch.name, '-', tit[:150])


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ip', default=IP_RASPBERRY, help="IP de la Raspberry (donde publica el ZMQ, puerto 5555)")
    ap.add_argument('--puerto', type=int, default=PUERTO, help="puerto del ZMQ (5555 por defecto)")
    ap.add_argument('--consola', action='store_true', help="sin ventana: imprime las detecciones")
    ap.add_argument('--nombre', default=None, help="nombre que se antepone a los archivos de la sesion (por defecto NOMBRE, arriba del archivo)")
    ap.add_argument('--etiqueta', default='', help="texto que se agrega al nombre de los archivos de la sesion (p. ej. 2g: dos gestos por marca)")
    ap.add_argument('--liviano', action='store_true', help="tablero mas liviano (5 s de señal, sin rejilla ni puntos rojos) para PCs lentas")
    ap.add_argument('--simular', metavar='CSV', help="reproduce un dataset como si llegara en vivo")
    ap.add_argument('--imagenes', metavar='SESION_EEG_CSV', nargs='+', help="una imagen por posible falso positivo y por gesto perdido de una sesion grabada")
    ap.add_argument('--verificar', metavar='CSV', nargs='+', help="compara con ventanas_deslizantes.py (identico o no)")
    a = ap.parse_args()
    if a.nombre is not None:
        globals()['NOMBRE'] = a.nombre
    if a.imagenes:
        for r_ in a.imagenes:
            modo_imagenes(r_)
    elif a.verificar:
        sys.exit(1 if sum(modo_verificar(r) for r in a.verificar) else 0)
    elif a.simular:
        modo_simular(a.simular)
    elif a.consola:
        modo_consola(a.ip, a.puerto, a.etiqueta)
    else:
        modo_tablero(a.ip, a.puerto, a.liviano, a.etiqueta)


if __name__ == '__main__':
    main()
