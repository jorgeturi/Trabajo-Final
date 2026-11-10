# detector_gestos.py - GENERADO por filtro/exportar_a_pi.py a partir de filtro/gestos_tiempo_real.py. NO EDITAR A MANO:
# los cambios se hacen en gestos_tiempo_real.py y se vuelve a generar este archivo.
# Filtro de gestos con umbral adaptativo por canal (ventana 0.5 s, paso 0.25 s, 2 rafagas separadas). Requiere solo numpy y scipy.
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from scipy import signal
from scipy.ndimage import binary_dilation

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
            det = pico['FP1'] > p.fp_solo * margen['FP1'] and pico['FP2'] > p.fp_solo * margen['FP2']
            if det:
                via = 'solo frontales (ambos 4.5 veces sobre su margen)'
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
# 5. ENVOLTORIO PARA stream.py
# ==========================================
class DetectorGestos:
    """Recibe lotes de muestras crudas (n, 4: TP9, FP1, FP2, TP10) y devuelve un paquete por ventana decidida (cada 0.25 s).
    activacion() es la salida para la Pico: se MANTIENE en 1 desde un gesto detectado hasta el siguiente gesto (que la vuelve a 0).
    "pulso" es la retencion corta de antes (1 durante mantener_s despues de cada deteccion), solo para mirar."""

    def __init__(self, fs=FS):
        self.fs = fs
        self.reiniciar()

    def reiniciar(self):
        self.motor = MotorGestos(self.fs)
        self.n_gestos = 0
        self.estado = 0                                              # salida mantenida: cambia con cada gesto detectado
        self._ult_det = -1e9
        self._sep = self.motor.p.paso_s + self.motor.p.mantener_s   # ventanas con deteccion mas juntas que esto = el mismo gesto
        self._n_reinicios = 0

    def procesar(self, lote, t=None):
        """t: tiempo del lote en el reloj del ACC (por defecto time.monotonic(), el que usa acc())."""
        self.motor.set_reloj(time.monotonic() if t is None else t)
        paquetes = []
        for d in self.motor.agregar(np.asarray(lote, dtype=float).reshape(-1, 4)):
            t = d['fin'] / self.fs
            nuevo = False
            if d['det']:
                nuevo = (t - self._ult_det) > self._sep
                self._ult_det = t
                if nuevo:
                    self.n_gestos += 1
                    self.estado ^= 1                                 # 0 -> 1 con un gesto, 1 -> 0 con el siguiente
            if d['calibrando'] or self.motor.n_reinicios != self._n_reinicios:
                self.estado = 0                                      # al arrancar y al volver a calibrar la activacion queda en 0
                self._n_reinicios = self.motor.n_reinicios
            paquetes.append({
                "tipo": "DETECCION", "t": round(t, 3), "activacion": self.activacion(), "pulso": self.motor.salida(), "det": bool(d['det']),
                "gesto_nuevo": bool(nuevo), "n_gestos": self.n_gestos, "calibrando": bool(d['calibrando']), "previo": int(d.get('previo', 0)),
                "via": d['via'], "freno": d['freno'],
                "margen": {c: round(float(d['margen'][c]), 1) for c in CAN},
                "ratios": {c: round(float(d['ratios'][c]), 2) for c in CAN},
                "ignorado": {c: bool(d['ignorado'][c]) for c in CAN},
                "senal_sin_contacto": dict(d.get('senal_off', {'FP': True, 'TP': True}))})   # stream.py lo usa para confirmar la EMERGENCIA por contacto
        return paquetes

    def acc(self, x, y, z, t=None):
        """/muse/acc (mg): sirve para descartar gestos que coinciden con un movimiento brusco de cabeza. t por defecto time.monotonic()."""
        self.motor.set_acc(time.monotonic() if t is None else t, x, y, z)

    def frente(self, valor):
        """/muse/elements/touching_forehead (1 = la vincha toca la frente, 0 = no): contacto general de la vincha."""
        self.motor.set_frente(valor)

    def contacto(self, vals):
        """Horseshoe de la vincha ({'TP9': 1..4, 'FP1': .., 'FP2': .., 'TP10': ..}, 4 = sin contacto): si los DOS frontales estan sin
        contacto durante mas de una ventana el filtro no detecta (vincha sacada). stream.py lo llama antes de cada lote."""
        self.motor.set_contacto(vals)

    def activacion(self):
        return self.estado
