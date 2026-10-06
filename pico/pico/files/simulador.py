# Reemplaza a la Pi para probar. Misma interfaz que EnlacePi.
from time import ticks_diff
import config as C

class Simulador:
    def __init__(self, now):
        self._t = now
        self._i = 0
        self._ok = True
        self.estado = self.mascara = self.pwm_l = self.pwm_r = 0
        self._aplicar()

    def _aplicar(self):
        if C.MODO == "FIJO":
            self._ok = C.FIJO_ENLACE_OK
            self.estado, self.mascara = C.FIJO_ESTADO, C.FIJO_FALLOS
            self.pwm_l, self.pwm_r = C.FIJO_PWM
            return
        n = len(C.SIM_ESTADOS) * 32
        i = self._i % (n + 1)
        if i == n:                              # última pantalla: sin enlace con la Pi
            self._ok = False
            return
        self._ok = True
        self.estado = C.SIM_ESTADOS[i // 32]
        self.mascara = i % 32                   # 0..31: sensores + vincha
        self.pwm_l, self.pwm_r = (60, 40) if self.estado == 3 else (0, 0)

    def poll(self, now):
        if C.MODO == "AUTO" and ticks_diff(now, self._t) >= C.SIM_PASO_MS:
            self._t = now
            self._i += 1
            self._aplicar()

    def enlace_ok(self, now):
        return self._ok
