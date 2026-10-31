"""
Detector de PATRON: solo se marca un gesto si aparecen DOS gestos seguidos (dos cejas seguidas, dos parpadeos seguidos).
Diferencia con gestos_tiempo_real.py: aquel graba la sesion y avisa cada gesto suelto; este va encima del mismo filtro y avisa
solo cuando hay dos gestos con silencio entre ellos. Se graba con gestos_tiempo_real.py y aca se analiza (--evaluar, --explicar).
Un gesto suelto, aunque el filtro de siempre lo detecte, NO se marca.

No modifica nada del trabajo anterior: reutiliza el motor de `gestos_tiempo_real.py` (el mismo filtro con umbrales adaptativos,
margenes por canal, canales ignorados, etc.) y le agrega una capa encima:

  1. RAFAGAS. Por muestra se mira que canales vivos (no ignorados) superan su margen adaptativo. Hay actividad cuando un frontal
     vivo esta sobre su margen y, si hay algun temporal vivo, un temporal tambien lo esta (coincidencia +-0.15 s). Las muestras
     con actividad separadas menos de `gap_union_s` forman una misma rafaga. Una rafaga es un gesto.
  2. PATRON. Dos rafagas validas (>= `min_sobre` muestras sobre el margen y pico >= `ratio_min` veces el margen) separadas por un
     silencio de entre `gap_union_s` y `sep_max_s` forman el patron. Se marca cuando termina la segunda (causal: solo usa pasado).
  3. COMPUERTA. Por defecto se exige ademas que el filtro de siempre haya detectado al menos una ventana en el patron
     (`exigir_det`): asi esta capa solo puede quitar falsos positivos, no agregar.
  4. REFRACTARIO. Despues de marcar, se ignora todo `refractario_s` segundos (un patron no se cuenta dos veces).

Uso (todo sobre grabaciones hechas con gestos_tiempo_real.py --etiqueta 2g):
  python gestos_patron.py --evaluar sesiones/sesion_..._2g_eeg.csv            aciertos / perdidos / falsos del patron por marca
  python gestos_patron.py --evaluar ARCH.csv --detalle                         ademas, las rafagas que vio en cada marca
  python gestos_patron.py --barrer ARCH.csv [ARCH2.csv ...]                    prueba combinaciones de los valores principales
  python gestos_patron.py --consola --ip 192.168.1.50 --etiqueta 2g           en vivo, imprime cada patron y graba la sesion
Cada marca (barra espaciadora apretada) debe contener los dos gestos del intento. Los valores se cambian con --gap, --min-sobre,
--ratio, --sep-max, --refractario (ver --help).
"""
import argparse
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.ndimage import binary_dilation, label

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gestos_tiempo_real as g                                   # noqa: E402
from gestos_tiempo_real import CAN, FS, MotorGestos                # noqa: E402

TOL_ANTES_S = 0.5            # un patron cuenta como acierto si se marca desde 0.5 s antes de la marca...
TOL_DESPUES_S = 1.6          # ...hasta 1.6 s despues de soltarla (se marca al terminar la segunda rafaga)
MARCA_MIN_S = 0.5            # las marcas mas cortas son toques sueltos de la barra: no se evaluan


@dataclass
class PP:
    gap_union_s: float = 0.10      # actividad separada menos que esto = misma rafaga (y es el silencio minimo entre los dos gestos)
    min_sobre: int = 4             # muestras sobre el margen que necesita una rafaga
    ratio_min: float = 1.5         # pico / margen minimo de una rafaga (en un frontal vivo)
    sep_max_s: float = 1.6         # silencio maximo entre el fin del primer gesto y el inicio del segundo
    n_gestos: int = 2              # rafagas necesarias para el patron
    max_rafagas: int = 5           # mas rafagas que esto seguidas = ruido, no patron
    refractario_s: float = 0.5     # tras marcar un patron, tiempo en que no se acepta una rafaga nueva
    valle_max: float = 0.6         # silencio: RMS del valle entre dos gestos (y del reposo antes y despues) <= esta fraccion del RMS de los gestos
    silencio_previo_s: float = 0.25   # el primer gesto debe salir de reposo: este tiempo antes, en silencio
    silencio_post_s: float = 0.25     # y despues del ultimo gesto la señal debe volver a reposo antes de avisar (suma esa latencia)
    ratio_max: float = 20.0        # una rafaga con un pico mayor a esto (veces el margen) es un artefacto (sacarse o ponerse la vincha), no un gesto
    rafaga_max_s: float = 1.0      # una rafaga mas larga que esto es ruido sostenido, no un gesto
    validar_silencio: bool = True  # exigir silencio entre gestos, reposo previo y rafagas cortas (distingue del ruido constante)
    rebote_s: float = 1.0          # del motor: veto de la cola de una deteccion anterior (1.0 = el de siempre)
    mantener_s: float = 1.0        # del motor: retencion de la salida (no afecta a esta capa)
    coincidencia_s: float = 0.15   # un temporal sobre su margen a menos de esto de un frontal sobre el suyo
    exigir_det: bool = True        # al menos una ventana detectada por el filtro de siempre dentro del patron
    via_det: str = 'amplitud|temporales inutiles'   # la ventana de la compuerta debe haber entrado por una de estas reglas (separadas por |); '' = cualquiera
    bilateral_min: float = 0.0     # si > 0: todos los frontales vivos de la rafaga deben llegar a esta cantidad de veces su margen
    difuso: bool = False           # puntuacion difusa en vez de cortes duros (PatronDifuso)
    theta: float = 0.55            # puntuacion minima para marcar el patron (con difuso)
    analisis_s: float = 6.0        # tramo reciente que se vuelve a mirar en cada paso


class Patron:
    """Capa de patron sobre un MotorGestos creado con vis=True (usa sus series por muestra: señal, margen, canal ignorado)."""

    def __init__(self, fs=FS, pp=None):
        self.fs = fs
        self.pp = pp or PP()
        self.rafagas = []                 # (inicio, fin, n_sobre, ratio) de cada rafaga valida ya cerrada, indices absolutos
        self.eventos = []                 # muestra (absoluta) en que se marco cada patron
        self.detalle = []                 # por evento: las rafagas del patron
        self.det_fins = []                # ventanas con deteccion del filtro de siempre (indice `fin`)
        self.puntajes = []                # con difuso: (puntuacion, componentes) de cada patron marcado
        self.ultimo_inicio = -1
        self.bloqueado_hasta = -1
        self.W = None

    def _rms(self, motor, i0, i1, c):
        i0 = max(i0, 0)
        if i1 - i0 <= 0:
            return 0.0
        idx = np.arange(i0, i1) % motor.R
        return float(np.sqrt(np.mean(motor.v_xs[c, idx] ** 2)))

    def _dominante(self, motor, i0, i1):
        """Frontal vivo con mayor pico en [i0, i1) (None si los dos estan ignorados)."""
        idx = np.arange(max(i0, 0), i1) % motor.R
        vivos = [c for c in (1, 2) if motor.v_ign[c, idx].mean() < 0.5]
        if not vivos:
            return None
        return max(vivos, key=lambda c: float(np.max(np.abs(motor.v_xs[c, idx]))))

    def _reposo(self, motor, i0, i1, a, b):
        """True si el tramo [i0, i1) esta en reposo respecto a los gestos que ocupan [a, b]: su RMS (en el frontal dominante)
        no supera valle_max veces el de los gestos."""
        c = self._dominante(motor, a, b + 1)
        if c is None:
            return True
        ref = self._rms(motor, a, b + 1, c)
        return self._rms(motor, i0, i1, c) <= self.pp.valle_max * ref

    def _extraer(self, motor, n0, n1):
        pp, fs = self.pp, self.fs
        R = motor.R
        idx = np.arange(n0, n1) % R
        xs, mg, ig = motor.v_xs[:, idx], motor.v_mg[:, idx], motor.v_ign[:, idx]
        sobre = (np.abs(xs) > np.where(np.isfinite(mg), mg, np.inf)) & ~ig
        F = sobre[1] | sobre[2]
        T = sobre[0] | sobre[3]
        t_vivo = ~(ig[0] & ig[3])
        k = max(int(pp.coincidencia_s * fs), 1)
        Tc = binary_dilation(T, structure=np.ones(2 * k + 1, dtype=bool))
        A = F & (Tc | ~t_vivo)
        if not A.any():
            return []
        gap = max(int(pp.gap_union_s * fs), 1)
        lab, nl = label(binary_dilation(A, structure=np.ones(gap + 1, dtype=bool)))
        out = []
        ratio = np.max(np.where(~ig[1:3] & np.isfinite(mg[1:3]), np.abs(xs[1:3]) / np.where(mg[1:3] > 0, mg[1:3], np.inf), 0.0), axis=0)
        for kx in range(1, nl + 1):
            pos = np.flatnonzero((lab == kx) & A)
            if len(pos) == 0:
                continue
            if n0 > 0 and (lab == kx)[0]:             # toca el borde del tramo: ya se vio completa cuando se cerro
                continue
            ini, fin = int(pos[0]), int(pos[-1])
            if (n1 - n0 - 1) - fin < gap:             # todavia puede seguir: no esta cerrada
                continue
            if pp.bilateral_min > 0:                  # los frontales vivos tienen que responder todos
                lo, hi = max(ini - k, 0), min(fin + k + 1, len(A))
                vivos = [i_ for i_ in (1, 2) if ig[i_, ini:fin + 1].mean() < 0.5]
                rc = [np.max(np.abs(xs[i_, lo:hi]) / np.where(np.isfinite(mg[i_, lo:hi]) & (mg[i_, lo:hi] > 0), mg[i_, lo:hi], np.inf)) for i_ in vivos]
                if rc and min(rc) < pp.bilateral_min:
                    continue
            out.append((n0 + ini, n0 + fin, int(len(pos)), float(ratio[ini:fin + 1].max())))
        return out

    def actualizar(self, motor, n, decisiones=()):
        """Llamar despues de procesar muestras hasta `n` (cantidad de muestras que ya vio el motor). Devuelve los patrones
        marcados en este paso (lista de indices de muestra)."""
        pp, fs = self.pp, self.fs
        self.W = motor.W
        for q in decisiones:
            if q['det'] and (not pp.via_det or q['via'].startswith(tuple(pp.via_det.split('|')))):
                self.det_fins.append(q['fin'])
        n0 = max(0, n - int(pp.analisis_s * fs))
        nuevos = []
        if n - n0 < 10:
            return nuevos
        for ini, fin, ns, rt in self._extraer(motor, n0, n):
            if ini <= self.ultimo_inicio:
                continue
            self.ultimo_inicio = ini
            if ini < self.bloqueado_hasta:
                continue
            if pp.validar_silencio and (fin - ini) > pp.rafaga_max_s * fs:
                self.rafagas = []                     # ruido sostenido: rompe cualquier cadena
                continue
            if pp.ratio_max > 0 and rt > pp.ratio_max:
                self.rafagas = []                     # artefacto enorme: rompe cualquier cadena
                continue
            if ns < pp.min_sobre or rt < pp.ratio_min:
                continue
            if self.rafagas:
                ult = self.rafagas[-1]
                if (ini - ult[1]) > pp.sep_max_s * fs or (pp.validar_silencio and not self._reposo(motor, ult[1] + 1, ini, ult[0], fin)):
                    self.rafagas = []                 # demasiado lejos, o entre medio la señal no bajo: es ruido seguido, no dos gestos
            if not self.rafagas and pp.validar_silencio and not self._reposo(motor, ini - int(pp.silencio_previo_s * fs), ini, ini, fin):
                continue                              # el primer gesto no sale de reposo: ruido constante
            self.rafagas.append((ini, fin, ns, rt))
        cadena = list(self.rafagas)
        if len(cadena) > pp.max_rafagas:
            self.rafagas, cadena = [], []
        if len(cadena) >= pp.n_gestos and pp.validar_silencio:
            ult = cadena[-1]
            if n - ult[1] < int(pp.silencio_post_s * fs):
                return nuevos                         # todavia no paso el tiempo de verificar que volvio a reposo
            if not self._reposo(motor, ult[1] + 1, ult[1] + 1 + int(pp.silencio_post_s * fs), cadena[0][0], ult[1]):
                cadena = []                           # despues del segundo gesto sigue el ruido: no es un patron
        if len(cadena) >= pp.n_gestos:
            ok = True
            if pp.exigir_det:
                a, b = cadena[0][0] - int(0.1 * fs), cadena[-1][1] + int(0.1 * fs)
                ok = any(f - self.W <= b and f >= a for f in self.det_fins)
            if ok:
                self.eventos.append(n)
                self.detalle.append(list(cadena))
                nuevos.append(n)
                self.bloqueado_hasta = n + int(pp.refractario_s * fs)
                self.rafagas = []
            elif len(cadena) > pp.max_rafagas:
                self.rafagas = []
        # rafagas viejas que ya no pueden formar cadena
        self.rafagas = [r_ for r_ in self.rafagas if n - r_[1] <= (pp.sep_max_s + 1.0) * fs]
        return nuevos


def _lin(x, a, b):
    """Pertenencia lineal: 0 hasta a, 1 desde b."""
    return float(np.clip((x - a) / (b - a), 0.0, 1.0))


class PatronDifuso(Patron):
    """Mismo detector, pero en vez de cortes duros usa una puntuacion difusa (media geometrica ponderada de pertenencias):
      Q  fuerza de las dos mejores rafagas (pico/margen de 1.0 a 1.8 y muestras sobre el margen de 2 a 8)
      V  profundidad del mejor valle entre rafagas (RMS del valle / RMS de las rafagas: 1 hasta 0.4, 0 desde 0.9)
      Pre / Post  reposo antes de la primera y despues de la ultima (RMS relativo: 1 hasta 0.5, 0 desde 1.0)
      S  separacion del mejor valle (0 por debajo de 0.04 s, 1 desde 0.15 s, 0 a los 1.8 s)
    y la multiplica por dos castigos: artefacto (pico de 15 a 30 veces el margen) y rafaga demasiado larga (0.8 a 1.5 s).
    Se marca el patron si la puntuacion llega a `theta`. Una pertenencia en 0 en Q, V o Post anula la puntuacion."""
    PESOS = dict(Q=0.35, V=0.30, Pre=0.10, Post=0.15, S=0.10)

    def _ratio_rel(self, motor, i0, i1, a, b):
        c = self._dominante(motor, a, b + 1)
        if c is None:
            return 0.0
        return self._rms(motor, i0, i1, c) / max(self._rms(motor, a, b + 1, c), 1e-9)

    def puntuar(self, motor, cadena):
        pp, fs = self.pp, self.fs
        q = [float(np.sqrt(_lin(r_[3], 1.0, 1.8) * _lin(r_[2], 2, 8))) for r_ in cadena]
        top = sorted(q, reverse=True)[:2]
        Q = float(np.sqrt(top[0] * top[1]))
        mejor = None
        for k in range(len(cadena) - 1):
            a, b = cadena[k], cadena[k + 1]
            val = self._ratio_rel(motor, a[1] + 1, b[0], a[0], b[1])
            sep = (b[0] - a[1]) / fs
            mu_v = 1.0 - _lin(val, 0.4, 0.9)
            mu_s = _lin(sep, 0.04, 0.15) * (1.0 - _lin(sep, 1.0, 1.8))
            if mejor is None or mu_v > mejor[0]:
                mejor = (mu_v, mu_s, val, sep)
        V, S = mejor[0], mejor[1]
        a0, b1 = cadena[0], cadena[-1]
        pre = self._ratio_rel(motor, a0[0] - int(pp.silencio_previo_s * fs), a0[0], a0[0], b1[1])
        pos = self._ratio_rel(motor, b1[1] + 1, b1[1] + 1 + int(pp.silencio_post_s * fs), a0[0], b1[1])
        Pre, Post = 1.0 - _lin(pre, 0.5, 1.0), 1.0 - _lin(pos, 0.5, 1.0)
        w = self.PESOS
        base = float(np.exp(sum(w[k] * np.log(max(v, 1e-6)) for k, v in dict(Q=Q, V=V, Pre=Pre, Post=Post, S=S).items())))
        art = 1.0 - _lin(max(r_[3] for r_ in cadena), 15.0, 30.0)
        lar = 1.0 - _lin(max((r_[1] - r_[0]) / fs for r_ in cadena), 0.8, 1.5)
        return base * art * lar, dict(Q=Q, V=V, Pre=Pre, Post=Post, S=S, art=art, lar=lar, valle=mejor[2], sep=mejor[3], pre=pre, post=pos)

    def actualizar(self, motor, n, decisiones=()):
        pp, fs = self.pp, self.fs
        self.W = motor.W
        for q in decisiones:
            if q['det'] and (not pp.via_det or q['via'].startswith(tuple(pp.via_det.split('|')))):
                self.det_fins.append(q['fin'])
        nuevos = []
        n0 = max(0, n - int(pp.analisis_s * fs))
        if n - n0 < 10:
            return nuevos
        for ini, fin, ns, rt in self._extraer(motor, n0, n):
            if ini <= self.ultimo_inicio:
                continue
            self.ultimo_inicio = ini
            if ini < self.bloqueado_hasta or ns < 2 or rt < 1.0:        # solo se descarta lo que ni siquiera supera su margen
                continue
            if self.rafagas and (ini - self.rafagas[-1][1]) > pp.sep_max_s * fs:
                self.rafagas = []
            self.rafagas.append((ini, fin, ns, rt))
        self.rafagas = self.rafagas[-pp.max_rafagas:]
        if len(self.rafagas) >= pp.n_gestos and n - self.rafagas[-1][1] >= int(pp.silencio_post_s * fs):
            cadena = list(self.rafagas)
            ok = True
            if pp.exigir_det:
                a, b = cadena[0][0] - int(0.1 * fs), cadena[-1][1] + int(0.1 * fs)
                ok = any(f - self.W <= b and f >= a for f in self.det_fins)
            if ok:
                sc, det = self.puntuar(motor, cadena)
                if sc >= pp.theta:
                    self.eventos.append(n)
                    self.detalle.append(cadena)
                    self.puntajes.append((sc, det))
                    nuevos.append(n)
                    self.bloqueado_hasta = n + int(pp.refractario_s * fs)
                    self.rafagas = []
        self.rafagas = [r_ for r_ in self.rafagas if n - r_[1] <= (pp.sep_max_s + 1.0) * fs]
        return nuevos


# ==========================================
# EVALUACION SOBRE UNA GRABACION
# ==========================================
def _correr_motor(ruta, pp=None):
    df = pd.read_csv(ruta)
    fs = round((len(df) - 1) / (df['Timestamp'].iloc[-1] - df['Timestamp'].iloc[0]), 2)
    x = df[CAN].values.astype(float)
    p_m = replace(g.P(), rebote_s=pp.rebote_s, mantener_s=pp.mantener_s) if pp else g.P()
    m = MotorGestos(fs, p_m, vis=True)
    R = len(x) + int(10 * fs)
    m.R = R
    m.v_xs, m.v_mg = np.zeros((4, R)), np.full((4, R), np.nan)
    m.v_ign, m.v_det = np.zeros((4, R), dtype=bool), np.zeros(R, dtype=bool)
    dec = []
    pasos = []                                         # (n procesadas, decisiones nuevas) para repetir el patron sin rehacer el filtro
    n = 0
    for a in range(0, len(x), 55):
        d = m.agregar(x[a:a + 55])
        n = min(a + 55, len(x))
        dec += d
        pasos.append((n, d))
    trig = df['Trigger'].values.astype(bool) if 'Trigger' in df else np.zeros(len(df), dtype=bool)
    l, k = label(trig)
    marcas = [(int(np.flatnonzero(l == i)[0]), int(np.flatnonzero(l == i)[-1])) for i in range(1, k + 1)]
    return dict(m=m, fs=fs, pasos=pasos, dec=dec, marcas=marcas, n=len(x))


def _patron_sobre(S, pp):
    p = (PatronDifuso if pp.difuso else Patron)(S['fs'], pp)
    for n, d in S['pasos']:
        p.actualizar(S['m'], n, d)
    return p


def _comparar(S, p, marca_min_s=MARCA_MIN_S):
    fs = S['fs']
    marcas = [(a, b) for a, b in S['marcas'] if (b - a + 1) / fs >= marca_min_s]
    ta, td = int(TOL_ANTES_S * fs), int(TOL_DESPUES_S * fs)
    aciertos, perdidas = [], []
    usados = set()
    for i, (a, b) in enumerate(marcas):
        hit = [j for j, e in enumerate(p.eventos) if a - ta <= e <= b + td]
        if hit:
            aciertos.append(i)
            usados.update(hit)
        else:
            perdidas.append(i)
    falsos = [j for j in range(len(p.eventos)) if j not in usados and not any(a - ta <= p.eventos[j] <= b + td for a, b in S['marcas'])]
    return marcas, aciertos, perdidas, falsos


def evaluar(ruta, pp, detalle=False):
    S = _correr_motor(ruta, pp)
    p = _patron_sobre(S, pp)
    marcas, ac, pe, fa = _comparar(S, p)
    fs = S['fs']
    print(f"{Path(ruta).name}: {len(marcas)} marcas (>= {MARCA_MIN_S} s), {len(p.eventos)} patrones marcados")
    print(f"  aciertos {len(ac)}/{len(marcas)}   perdidos {len(pe)}   falsos positivos {len(fa)}")
    if detalle:
        raf = _todas_las_rafagas(S, pp)
        for i, (a, b) in enumerate(marcas, 1):
            en = [r_ for r_ in raf if a - int(0.5 * fs) <= r_[0] <= b + int(0.5 * fs)]
            est = 'ACIERTO' if (i - 1) in ac else 'PERDIDO'
            print(f"  marca {i:2d} {a / fs:6.1f}-{b / fs:6.1f} s  {est}  rafagas validas: {len(en)}  " +
                  ' '.join(f"[{r_[0] / fs:.2f}-{r_[1] / fs:.2f}s n{r_[2]} x{r_[3]:.1f}]" for r_ in en))
    for j in fa:
        print(f"  FALSO POSITIVO a los {p.eventos[j] / fs:.1f} s  (rafagas: " +
              ' '.join(f"{r_[0] / fs:.2f}-{r_[1] / fs:.2f}s x{r_[3]:.1f}" for r_ in p.detalle[j]) + ")")
    return len(marcas), len(ac), len(pe), len(fa)


def _todas_las_rafagas(S, pp):
    """Todas las rafagas validas de la grabacion (para --detalle), con la misma extraccion en tramos."""
    return _rafagas_sueltas(S, pp)


def _rafagas_sueltas(S, pp):
    p = Patron(S['fs'], replace(pp, n_gestos=999, refractario_s=0.0))
    out = []
    vistos = -1
    for n, d in S['pasos']:
        p.W = S['m'].W
        n0 = max(0, n - int(pp.analisis_s * S['fs']))
        for ini, fin, ns, rt in p._extraer(S['m'], n0, n):
            if ini > vistos:
                vistos = ini
                if ns >= pp.min_sobre and rt >= pp.ratio_min:
                    out.append((ini, fin, ns, rt))
    return out


def _rasgos(S, ini, fin):
    """Por canal, pico/margen y fraccion del tramo ignorado, y margen medio, en [ini, fin) muestras."""
    m = S['m']
    idx = np.arange(max(ini, 0), fin) % m.R
    out = {}
    for i, c in enumerate(CAN):
        xs, mg, ig = m.v_xs[i, idx], m.v_mg[i, idx], m.v_ign[i, idx]
        ok = np.isfinite(mg) & (mg > 0)
        out[c] = dict(ratio=float(np.max(np.abs(xs[ok]) / mg[ok])) if ok.any() else 0.0,
                      pico=float(np.max(np.abs(xs))) if len(xs) else 0.0,
                      margen=float(np.nanmedian(mg)) if ok.any() else float('nan'), ign=float(ig.mean()) if len(ig) else 0.0)
    return out


def imagenes(ruta, pp, contexto_s=2.0):
    """Una imagen por cada marca (con las rafagas que vio el detector y si marco el patron) y una por cada falso positivo,
    mas una tabla de rasgos de cada patron marcado (acierto o falso) para ver en que se diferencian."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    S = _correr_motor(ruta, pp)
    p = _patron_sobre(S, pp)
    marcas, ac, pe, fa = _comparar(S, p)
    fs, m = S['fs'], S['m']
    raf = _rafagas_sueltas(S, pp)
    carpeta = Path(str(ruta).replace('_eeg.csv', '') + '_patron')
    carpeta.mkdir(exist_ok=True)
    colores = [(0.35, 0.5, 1.0), (0.2, 0.7, 0.2), (0.8, 0.3, 0.8), (0.7, 0.6, 0.0)]
    casos = [(f'marca{i + 1:02d}_{"acierto" if i in ac else "perdido"}', a, b) for i, (a, b) in enumerate(marcas)]
    for j in fa:
        e = p.eventos[j]
        casos.append((f'falso_positivo_{e / fs:.0f}s', p.detalle[j][0][0] - int(0.3 * fs), e))

    def dibujar(nombre, a, b):
        i0, i1 = max(int(a - contexto_s * fs), 0), min(int(b + contexto_s * fs), S['n'])
        idx = np.arange(i0, i1) % m.R
        t = (np.arange(i0, i1)) / fs
        fig, ejes = plt.subplots(4, 1, figsize=(13, 8.5), sharex=True)
        for i, c in enumerate(CAN):
            ax = ejes[i]
            xs, mg, ig = m.v_xs[i, idx], m.v_mg[i, idx], m.v_ign[i, idx]
            for ma, mb in S['marcas']:
                if mb >= i0 and ma <= i1:
                    ax.axvspan(ma / fs, mb / fs, color='0.85', lw=0)
            tr = np.flatnonzero(np.diff(np.concatenate(([0], ig.astype(int), [0]))))
            for s0, s1 in zip(tr[::2], tr[1::2]):
                ax.axvspan(t[s0], t[min(s1, len(t)) - 1], color='pink', alpha=0.5, lw=0)
            ax.plot(t, xs, color=colores[i], lw=0.8)
            ax.plot(t, mg, color='green', lw=1.3)
            ax.plot(t, -mg, color='green', lw=1.3)
            sobre = (np.abs(xs) > np.where(np.isfinite(mg), mg, np.inf)) & ~ig
            ax.plot(t[sobre], xs[sobre], 'r.', ms=3)
            for ri, rf, ns, rt in raf:
                if rf >= i0 and ri <= i1:
                    ax.axvspan(ri / fs, rf / fs, ymin=0.9, ymax=1.0, color='orange', lw=0)
            for e in p.eventos:
                if i0 <= e <= i1:
                    ax.axvline(e / fs, color='k', ls='--', lw=1)
            ax.set_ylabel(f'{c} (uV)')
            ax.grid(alpha=0.3)
            top = max(1.3 * float(np.nanmax(mg)) if np.isfinite(mg).any() else 50, 1.15 * float(np.abs(xs).max()), 40)
            ax.set_ylim(-top, top)
        fig.suptitle(nombre.replace('_', ' ') + '   |   naranja arriba = rafaga valida, linea negra = patron marcado, gris = tu marca, rosa = canal ignorado', fontsize=10)
        ejes[-1].set_xlabel('segundos de la grabacion')
        fig.tight_layout(rect=(0, 0, 1, 0.96))
        fig.savefig(carpeta / f'{nombre}.png', dpi=88)
        plt.close(fig)

    for nombre, a, b in casos:
        dibujar(nombre, a, b)
    print(f'{len(casos)} imagenes en {carpeta}')
    print(f"{'caso':26s} {'rafagas (pico/margen, muestras, duracion s)':58s} {'sep s':>6s}  FP1  FP2  TP9 TP10  (pico/margen del tramo)")
    filas = [(f'marca {i + 1}', [r_ for r_ in raf if marcas[i][0] - int(0.5 * fs) <= r_[0] <= marcas[i][1] + int(0.5 * fs)], marcas[i][0], marcas[i][1]) for i in range(len(marcas))]
    for j in fa:
        filas.append((f'FALSO {p.eventos[j] / fs:.0f} s', p.detalle[j], p.detalle[j][0][0], p.detalle[j][-1][1]))
    for nombre, rs, a, b in filas:
        rs = list(rs)
        rafs = ' '.join(f"[x{r_[3]:.1f} n{r_[2]} {(r_[1] - r_[0]) / fs:.2f}]" for r_ in rs[:4])
        sep = ' '.join(f"{(rs[k + 1][0] - rs[k][1]) / fs:.2f}" for k in range(min(len(rs) - 1, 2))) if len(rs) > 1 else '-'
        rz = _rasgos(S, int(a - 0.2 * fs), int(b + 0.2 * fs))
        print(f"{nombre:26s} {rafs:58s} {sep:>6s}  " + ' '.join(f"{rz[c]['ratio']:4.1f}" for c in CAN) +
              '   ign: ' + ','.join(c for c in CAN if rz[c]['ign'] > 0.5))


class DosDetecciones:
    """Version simple, con el filtro de siempre como caja negra: cuando detecta un gesto espera otra deteccion dentro de `sep_max_s`
    y recien ahi avisa. Funciona solo si los dos gestos estan separados lo suficiente como para que el filtro los vea como dos
    detecciones distintas (el rebote y el mantener de 1 s lo impiden si van pegados)."""

    def __init__(self, fs=FS, paso=55, sep_max_s=4.0, n_gestos=2, refractario_s=2.0):
        self.fs, self.paso, self.sep_max_s, self.n, self.ref = fs, paso, sep_max_s, n_gestos, refractario_s
        self.cadena = []                 # grupos de ventanas detectadas [primera, ultima]
        self.eventos = []
        self.bloqueado = -1

    def actualizar(self, decisiones):
        nuevos = []
        for q in decisiones:
            if not q['det'] or q['fin'] <= self.bloqueado:
                continue
            f = q['fin']
            if self.cadena and f - self.cadena[-1][1] <= self.paso * 1.01:
                self.cadena[-1][1] = f                      # misma deteccion: sigue el mismo gesto
                continue
            if self.cadena and f - self.cadena[-1][1] > self.sep_max_s * self.fs:
                self.cadena = []                            # paso demasiado tiempo: empieza de nuevo
            self.cadena.append([f, f])
            if len(self.cadena) >= self.n:
                self.eventos.append(f)
                nuevos.append(f)
                self.bloqueado = f + int(self.ref * self.fs)
                self.cadena = []
        return nuevos


def evaluar_simple(ruta, sep_max_s, refractario_s):
    S = _correr_motor(ruta)
    d = DosDetecciones(S['fs'], S['m'].paso, sep_max_s, 2, refractario_s)
    for n, dec in S['pasos']:
        d.actualizar(dec)
    marcas, ac, pe, fa = _comparar(S, d)
    print(f"{Path(ruta).name}: {len(marcas)} marcas, {len(d.eventos)} avisos (dos detecciones del filtro de siempre a menos de {sep_max_s} s)")
    print(f"  aciertos {len(ac)}/{len(marcas)}   perdidos {len(pe)}   falsos positivos {len(fa)}")
    for j in fa:
        print(f"  FALSO POSITIVO a los {d.eventos[j] / S['fs']:.1f} s")
    return len(marcas), len(ac), len(pe), len(fa)


def explicar(ruta, pp, solo=None, contexto_s=1.5):
    """Para cada marca NO detectada (o las indicadas en `solo`): una imagen con la señal, el margen de cada canal y el umbral que
    debia alcanzar cada rafaga, y en la consola el resultado de cada comprobacion (que umbral falla y por cuanto)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    S = _correr_motor(ruta, pp)
    p = _patron_sobre(S, pp)
    marcas, ac, pe, fa = _comparar(S, p)
    fs, m = S['fs'], S['m']
    nombres = ('TP9', 'FP1', 'FP2', 'TP10')
    todas = Patron(fs, replace(pp, min_sobre=0, ratio_min=0.0, bilateral_min=0.0))     # todas las rafagas, sin filtrar por fuerza
    todas.W = m.W
    cand = []
    vistos = -1
    for n, d in S['pasos']:
        n0 = max(0, n - int(pp.analisis_s * fs))
        for ini, fin, ns, rt in todas._extraer(m, n0, n):
            if ini > vistos:
                vistos = ini
                cand.append((ini, fin, ns, rt))
    carpeta = Path(str(ruta).replace('_eeg.csv', '') + '_patron')
    carpeta.mkdir(exist_ok=True)
    chk = Patron(fs, pp)
    chk.W = m.W
    fins_det = [(q['fin'], q['via'], q['freno']) for q in S['dec']]
    objetivo = [i for i in range(len(marcas)) if ((i + 1) in solo if solo else i in pe)]
    for i in objetivo:
        a, b = marcas[i]
        lo, hi = a - int(0.5 * fs), b + int(0.5 * fs)
        cs = [c for c in cand if lo <= c[0] <= hi]
        print(f"\n===== MARCA {i + 1}  ({a / fs:.1f}-{b / fs:.1f} s)  {'DETECTADA' if i in ac else 'NO DETECTADA'} =====")
        lineas = []
        if not cs:
            lineas.append("No hay ninguna rafaga: ningun frontal vivo con un temporal superando su margen (coincidencia) en la marca.")
        validas = []
        for k, (ini, fin, ns, rt) in enumerate(cs, 1):
            f_n, f_r = ns >= pp.min_sobre, rt >= pp.ratio_min
            ok = f_n and f_r
            if ok:
                validas.append((ini, fin, ns, rt))
            lineas.append(f"rafaga {k}: {ini / fs:.2f}-{fin / fs:.2f} s  muestras sobre el margen {ns} ({'>=' if f_n else '<'} {pp.min_sobre} {'ok' if f_n else 'FALLA'})  "
                          f"pico {rt:.2f} x margen ({'>=' if f_r else '<'} {pp.ratio_min} {'ok' if f_r else 'FALLA'})  -> {'VALIDA' if ok else 'descartada'}")
        pares = validas if len(validas) >= 2 else cs         # si hay dos validas, el silencio que cuenta es el que las separa
        for k in range(len(pares) - 1):
            c0, c1 = pares[k], pares[k + 1]
            sep = (c1[0] - c0[1]) / fs
            cdom = chk._dominante(m, c0[0], c1[1] + 1)
            if cdom is None:
                continue
            ref = chk._rms(m, c0[0], c1[1] + 1, cdom)
            val = chk._rms(m, c0[1] + 1, c1[0], cdom) / max(ref, 1e-9)
            lineas.append(f"entre {k + 1} y {k + 2}: separacion {sep:.2f} s ({'<=' if sep <= pp.sep_max_s else '>'} {pp.sep_max_s}), valle {val:.2f} del nivel de los gestos "
                          f"({'<=' if val <= pp.valle_max else '>'} {pp.valle_max} {'ok' if val <= pp.valle_max else 'FALLA'}) en {nombres[cdom]}")
        if len(validas) < pp.n_gestos:
            lineas.append(f"=> MOTIVO: solo {len(validas)} rafaga(s) valida(s); hacen falta {pp.n_gestos}. "
                          "El segundo gesto no llega a los umbrales de fuerza (ver FALLA arriba).")
        else:
            lineas.append("=> hay rafagas validas suficientes; el rechazo viene de las comprobaciones de silencio o de la compuerta:")
            ini0, fin1 = validas[0][0], validas[-1][1]
            c0 = chk._dominante(m, ini0, fin1 + 1)
            if c0 is not None:
                ref = chk._rms(m, ini0, fin1 + 1, c0)
                pre = chk._rms(m, ini0 - int(pp.silencio_previo_s * fs), ini0, c0) / max(ref, 1e-9)
                pos = chk._rms(m, fin1 + 1, fin1 + 1 + int(pp.silencio_post_s * fs), c0) / max(ref, 1e-9)
                lineas.append(f"   reposo previo {pre:.2f} (<= {pp.valle_max})   reposo posterior {pos:.2f} (<= {pp.valle_max})")
            win = [(f, v, fr) for f, v, fr in fins_det if ini0 - int(0.1 * fs) <= f and f - m.W <= fin1 + int(0.1 * fs)]
            dets = [(f, v) for f, v, fr in win if v]
            lineas.append("   filtro de siempre en ese tramo: " + (', '.join(f"{f / fs:.2f}s por '{v[:22]}'" for f, v in dets[:4]) if dets else
                          "NINGUNA ventana detectada (frenos: " + '; '.join(sorted({fr[:34] for f, v, fr in win if fr})[:3]) + ")"))
        for l_ in lineas:
            print("  " + l_)
        # --- imagen
        i0, i1 = max(int(a - contexto_s * fs), 0), min(int(b + contexto_s * fs), S['n'])
        idx = np.arange(i0, i1) % m.R
        t = np.arange(i0, i1) / fs
        fig, ejes = plt.subplots(4, 1, figsize=(13, 10), sharex=True)
        colores = [(0.35, 0.5, 1.0), (0.2, 0.7, 0.2), (0.8, 0.3, 0.8), (0.7, 0.6, 0.0)]
        for c in range(4):
            ax = ejes[c]
            xs, mg, ig = m.v_xs[c, idx], m.v_mg[c, idx], m.v_ign[c, idx]
            ax.axvspan(a / fs, b / fs, color='0.88', lw=0)
            tr = np.flatnonzero(np.diff(np.concatenate(([0], ig.astype(int), [0]))))
            for s0, s1 in zip(tr[::2], tr[1::2]):
                ax.axvspan(t[s0], t[min(s1, len(t)) - 1], color='pink', alpha=0.5, lw=0)
            ax.plot(t, xs, color=colores[c], lw=0.8)
            ax.plot(t, mg, color='green', lw=1.4, label='margen (umbral de amplitud)')
            ax.plot(t, -mg, color='green', lw=1.4)
            ax.plot(t, pp.ratio_min * mg, color='darkorange', lw=1.0, ls='--', label=f'{pp.ratio_min} x margen (pico minimo de una rafaga)')
            ax.plot(t, -pp.ratio_min * mg, color='darkorange', lw=1.0, ls='--')
            sobre = (np.abs(xs) > np.where(np.isfinite(mg), mg, np.inf)) & ~ig
            ax.plot(t[sobre], xs[sobre], 'r.', ms=3)
            for k, (ini, fin, ns, rt) in enumerate(cs, 1):
                ok = ns >= pp.min_sobre and rt >= pp.ratio_min
                ax.axvspan(ini / fs, (fin + 1) / fs, ymin=0.0, ymax=0.12, color='tab:green' if ok else 'tab:red', alpha=0.8, lw=0)
                if c == 0:
                    ax.text((ini + fin) / 2 / fs, 0.92, f"{k}: {ns} muestras, x{rt:.1f}", transform=ax.get_xaxis_transform(), ha='center', fontsize=8,
                            color='tab:green' if ok else 'tab:red')
            ax.set_ylabel(f"{nombres[c]}{' (IGNORADO)' if ig.mean() > 0.5 else ''} uV")
            ax.grid(alpha=0.3)
            en = (t > a / fs - 0.3) & (t < b / fs + 0.3)
            pico = float(np.abs(xs[en]).max()) if en.any() else 50.0
            top = max(2.5 * float(np.nanmedian(mg[en])) if np.isfinite(mg[en]).any() else 50.0, 1.2 * pico, 40.0)
            ax.set_ylim(-top, top)
            if c == 0:
                ax.legend(loc='upper left', fontsize=7)
        fig.suptitle(f"Marca {i + 1} ({a / fs:.1f}-{b / fs:.1f} s) - {'DETECTADA' if i in ac else 'NO DETECTADA'}.  Barras de abajo: verde = rafaga valida, roja = descartada.  "
                     "Gris = tu marca, rosa = canal ignorado", fontsize=10)
        fig.text(0.01, 0.005, '\n'.join(l_.replace('=> ', '') for l_ in lineas[-6:])[:1100], fontsize=7.5, va='bottom', family='monospace')
        fig.tight_layout(rect=(0, 0.12, 1, 0.96))
        arch = carpeta / f"explicacion_marca{i + 1:02d}.png"
        fig.savefig(arch, dpi=88)
        plt.close(fig)
        print(f"  imagen: {arch}")


def barrer(rutas, base):
    S = [_correr_motor(r, base) for r in rutas]
    print(f"{'gap':>5s} {'min_sobre':>9s} {'ratio':>6s} {'sep_max':>7s}  | " + '  '.join(f"{Path(r).stem[-14:]:>16s}" for r in rutas) + "  |  total aciertos / marcas  perdidos  falsos")
    for gap in (0.10, 0.15, 0.25):
        for ms in (3, 4, 6):
            for rt in (1.2, 1.5, 2.0):
                for sm in (1.2, 1.6, 2.5):
                    pp = replace(base, gap_union_s=gap, min_sobre=ms, ratio_min=rt, sep_max_s=sm)
                    cols, A, M, P, F = [], 0, 0, 0, 0
                    for s in S:
                        p = _patron_sobre(s, pp)
                        marcas, ac, pe, fa = _comparar(s, p)
                        cols.append(f"{len(ac)}/{len(marcas)} FP{len(fa)}")
                        A, M, P, F = A + len(ac), M + len(marcas), P + len(pe), F + len(fa)
                    print(f"{gap:5.2f} {ms:9d} {rt:6.1f} {sm:7.1f}  | " + '  '.join(f"{c:>16s}" for c in cols) + f"  |  {A}/{M}  {P}  {F}")


# ==========================================
# EN VIVO (consola)
# ==========================================
def modo_consola(ip, puerto, pp, etiqueta, segundos=0.0):
    zmq, context, sock = g.conectar(ip, puerto)
    ses = g.Sesion(FS, vis=True, etiqueta=etiqueta)
    ses.motor.p = replace(ses.motor.p, rebote_s=pp.rebote_s, mantener_s=pp.mantener_s)
    pat = (PatronDifuso if pp.difuso else Patron)(FS, pp)
    print(f"Escuchando tcp://{ip}:{puerto}. Solo se marca el PATRON de {pp.n_gestos} gestos seguidos. Ctrl+C para cerrar y guardar.")
    ultimo = 0
    t_ini = time.time()
    try:
        while not segundos or time.time() - t_ini < segundos:
            lote, _ = g.drenar(zmq, sock, ses)
            dec = ses.eeg(time.time(), lote)
            if len(lote):
                for n in pat.actualizar(ses.motor, ses.motor.flujo.n, dec):
                    print(f"[{time.strftime('%H:%M:%S')}] *** PATRON de {len(pat.detalle[-1])} gestos *** "
                          f"(rafagas: " + ' '.join(f"x{r_[3]:.1f}" for r_ in pat.detalle[-1]) + ")")
            if time.time() - ultimo > 5:
                ultimo = time.time()
                print(f"  [{time.strftime('%H:%M:%S')}] muestras={ses.motor.flujo.n} patrones={len(pat.eventos)} "
                      f"gestos sueltos del filtro={len(ses.reg.eventos['filtro'])} contacto={ses.contacto}")
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    print("\n" + ses.cerrar())


def main():
    d = PP()
    ap = argparse.ArgumentParser(description="Detector de patron: solo marca cuando hay dos gestos seguidos.")
    ap.add_argument('--evaluar', metavar='SESION_EEG_CSV', nargs='+')
    ap.add_argument('--detalle', action='store_true', help="con --evaluar: muestra las rafagas de cada marca")
    ap.add_argument('--imagenes', metavar='SESION_EEG_CSV', help="una imagen por marca y por falso positivo, y la tabla de rasgos")
    ap.add_argument('--simple', metavar='SESION_EEG_CSV', nargs='+', help="version simple: filtro de siempre como caja negra, avisa a la segunda deteccion dentro de --sep-max")
    ap.add_argument('--explicar', metavar='SESION_EEG_CSV', help="por que no se detecto cada marca perdida: umbrales, valores medidos e imagen con los umbrales dibujados")
    ap.add_argument('--marca', type=int, nargs='+', help="con --explicar: explicar estas marcas (numero de marca) en vez de solo las perdidas")
    ap.add_argument('--barrer', metavar='SESION_EEG_CSV', nargs='+', help="prueba combinaciones de gap, min-sobre, ratio y sep-max")
    ap.add_argument('--consola', action='store_true')
    ap.add_argument('--ip', default=g.IP_RASPBERRY)
    ap.add_argument('--puerto', type=int, default=g.PUERTO)
    ap.add_argument('--nombre', default=None, help="nombre que se antepone a los archivos de la sesion (por defecto NOMBRE en gestos_tiempo_real.py)")
    ap.add_argument('--etiqueta', default='2g')
    ap.add_argument('--segundos', type=float, default=0.0, help="con --consola: cortar solo despues de estos segundos (0 = hasta Ctrl+C)")
    ap.add_argument('--gap', type=float, default=d.gap_union_s, help="s: actividad separada menos que esto es una misma rafaga")
    ap.add_argument('--min-sobre', type=int, default=d.min_sobre, help="muestras sobre el margen para que cuente una rafaga")
    ap.add_argument('--ratio', type=float, default=d.ratio_min, help="pico/margen minimo de una rafaga")
    ap.add_argument('--sep-max', type=float, default=d.sep_max_s, help="s: silencio maximo entre los dos gestos")
    ap.add_argument('--gestos', type=int, default=d.n_gestos, help="cantidad de gestos seguidos del patron")
    ap.add_argument('--refractario', type=float, default=d.refractario_s)
    ap.add_argument('--via', default=d.via_det, help="reglas del filtro de siempre que valen para la compuerta, separadas por | (vacio = todas, incluidas las de rescate)")
    ap.add_argument('--bilateral', type=float, default=0.0, help="los frontales vivos de la rafaga deben llegar a esta cantidad de veces su margen (0 = no exigir)")
    ap.add_argument('--valle-max', type=float, default=d.valle_max, help="el valle entre gestos (y el reposo antes y despues) debe tener RMS <= esta fraccion del RMS de los gestos")
    ap.add_argument('--post', type=float, default=d.silencio_post_s, help="s de reposo exigidos despues del ultimo gesto antes de avisar")
    ap.add_argument('--previo', type=float, default=d.silencio_previo_s, help="s de reposo exigidos antes del primer gesto")
    ap.add_argument('--ratio-max', type=float, default=d.ratio_max, help="una rafaga con pico mayor a esto (veces el margen) es un artefacto, no un gesto (0 = no usar)")
    ap.add_argument('--rafaga-max', type=float, default=d.rafaga_max_s, help="s: una rafaga mas larga es ruido sostenido")
    ap.add_argument('--sin-silencio', action='store_true', help="no validar silencio entre gestos ni reposo previo")
    ap.add_argument('--rebote', type=float, default=d.rebote_s, help="rebote_s del motor (veto de la cola de una deteccion)")
    ap.add_argument('--mantener', type=float, default=d.mantener_s, help="mantener_s del motor")
    ap.add_argument('--difuso', action='store_true', help="puntuacion difusa en vez de cortes duros")
    ap.add_argument('--theta', type=float, default=d.theta, help="con --difuso: puntuacion minima para marcar el patron")
    ap.add_argument('--sin-compuerta', action='store_true', help="no exigir que el filtro de siempre detecte alguna ventana")
    a = ap.parse_args()
    if a.nombre is not None:
        g.NOMBRE = a.nombre
    pp = PP(gap_union_s=a.gap, min_sobre=a.min_sobre, ratio_min=a.ratio, sep_max_s=a.sep_max, n_gestos=a.gestos,
            refractario_s=a.refractario, exigir_det=not a.sin_compuerta, via_det=a.via, bilateral_min=a.bilateral,
            valle_max=a.valle_max, silencio_previo_s=a.previo, silencio_post_s=a.post, rafaga_max_s=a.rafaga_max, ratio_max=a.ratio_max, difuso=a.difuso, theta=a.theta, validar_silencio=not a.sin_silencio,
            rebote_s=a.rebote, mantener_s=a.mantener)
    if a.evaluar:
        tot = np.zeros(4, dtype=int)
        for r_ in a.evaluar:
            tot += np.array(evaluar(r_, pp, a.detalle))
        if len(a.evaluar) > 1:
            print(f"TOTAL: aciertos {tot[1]}/{tot[0]}  perdidos {tot[2]}  falsos positivos {tot[3]}")
    elif a.simple:
        for r_ in a.simple:
            evaluar_simple(r_, a.sep_max, a.refractario)
    elif a.explicar:
        explicar(a.explicar, pp, set(a.marca) if a.marca else None)
    elif a.imagenes:
        imagenes(a.imagenes, pp)
    elif a.barrer:
        barrer(a.barrer, pp)
    elif a.consola:
        modo_consola(a.ip, a.puerto, pp, a.etiqueta, a.segundos)
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
