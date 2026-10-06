from machine import Pin, PWM
from time import ticks_diff
import config as C

class Motores:
    def __init__(self):
        self.l = PWM(Pin(C.PIN_PWM_IZQ)); self.l.freq(C.PWM_FREQ)
        self.r = PWM(Pin(C.PIN_PWM_DER)); self.r.freq(C.PWM_FREQ)
        self.set(0, 0)

    def set(self, pl, pr):                      # porcentaje 0..100
        self.l.duty_u16(pl * 65535 // 100)
        self.r.duty_u16(pr * 65535 // 100)

class Buzzer:
    """Listo para usar: poné PIN_BUZZER en config.py. Con None no hace nada."""
    # (ms encendido, ms apagado) por nivel de alarma
    _PAT = {1: (100, 1400), 2: (250, 250)}

    def __init__(self):
        self.pin = Pin(C.PIN_BUZZER, Pin.OUT) if C.PIN_BUZZER is not None else None
        self._on = False
        self._t = 0
        if self.pin:
            self.pin.value(0)

    def actualizar(self, now, nivel):
        if not self.pin:
            return
        if nivel == 0:
            if self._on:
                self.pin.value(0); self._on = False
            return
        t_on, t_off = self._PAT[nivel]
        if ticks_diff(now, self._t) >= (t_on if self._on else t_off):
            self._on = not self._on
            self._t = now
            self.pin.value(1 if self._on else 0)
