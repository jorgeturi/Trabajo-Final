"""
Genera pi/detector_gestos.py (el filtro para la Raspberry) a partir de filtro/gestos_tiempo_real.py.

Copia tal cual las secciones 1 a 4 (parametros P, utilidades, FlujoSenales y MotorGestos) y agrega un envoltorio, DetectorGestos,
que stream.py llama con cada lote de muestras. Asi el filtro de la Raspberry es SIEMPRE el que se ajusta en la PC: despues de
cambiar parametros o reglas en gestos_tiempo_real.py hay que volver a correr este script y copiar pi/detector_gestos.py a la Pi.

Uso: python filtro/exportar_a_pi.py
"""
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ORIGEN = AQUI / 'gestos_tiempo_real.py'
DESTINO = AQUI.parent / 'pi' / 'detector_gestos.py'

CABECERA = '''# detector_gestos.py - GENERADO por filtro/exportar_a_pi.py a partir de filtro/gestos_tiempo_real.py. NO EDITAR A MANO:
# los cambios se hacen en gestos_tiempo_real.py y se vuelve a generar este archivo.
# Filtro de gestos con umbral adaptativo por canal (ventana 0.5 s, paso 0.25 s, 2 rafagas separadas). Requiere solo numpy y scipy.
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from scipy import signal
from scipy.ndimage import binary_dilation

'''

ENVOLTORIO = '''

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

    def procesar(self, lote):
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
            paquetes.append({
                "tipo": "DETECCION", "t": round(t, 3), "activacion": self.activacion(), "pulso": self.motor.salida(), "det": bool(d['det']),
                "gesto_nuevo": bool(nuevo), "n_gestos": self.n_gestos, "calibrando": bool(d['calibrando']),
                "via": d['via'], "freno": d['freno'],
                "margen": {c: round(float(d['margen'][c]), 1) for c in CAN},
                "ratios": {c: round(float(d['ratios'][c]), 2) for c in CAN},
                "ignorado": {c: bool(d['ignorado'][c]) for c in CAN}})
        return paquetes

    def activacion(self):
        return self.estado
'''

lineas = ORIGEN.read_text(encoding='utf-8').split('\n')
ini = next(i for i, l in enumerate(lineas) if l.startswith('# 1. CONFIGURACION')) - 1
fin = next(i for i, l in enumerate(lineas) if l.startswith('# 5. REGISTRO')) - 1
cuerpo = '\n'.join(lineas[ini:fin]).rstrip('\n')
DESTINO.write_text(CABECERA + cuerpo + '\n' + ENVOLTORIO, encoding='utf-8', newline='\n')
print(f"Generado {DESTINO} ({DESTINO.stat().st_size} bytes)")
