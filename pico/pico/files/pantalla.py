# Arriba: estado. Izq: cara con la zona fallada. Der: lista por sensor.
# Abajo: aviso (enlace / vincha) en vídeo inverso, o el PWM.
from machine import Pin, I2C
from time import ticks_ms, ticks_diff
import ssd1306
import config as C
import estados as E

class Pantalla:
    def __init__(self):
        self.oled = None
        self.ultimo = None
        self._t = ticks_ms()
        self._init()

    def _init(self):
        try:
            i2c = I2C(0, scl=Pin(C.PIN_SCL), sda=Pin(C.PIN_SDA), freq=C.I2C_FREQ)
            self.oled = ssd1306.SSD1306_I2C(C.OLED_W, C.OLED_H, i2c)
        except Exception:
            self.oled = None

    def actualizar(self, now, enlace_ok, estado, mask, pl, pr):
        vista = (enlace_ok, estado, mask, pl, pr)
        if vista == self.ultimo or ticks_diff(now, self._t) < C.OLED_REFRESH_MS:
            return
        self._t = now
        if self.oled is None:
            self._init()
            if self.oled is None:
                return
        try:
            self._dibujar(enlace_ok, estado, mask, pl, pr)
            self.oled.show()
            self.ultimo = vista
        except Exception:
            self.oled = None                    # no rompe el lazo; reintenta luego

    def _inv(self, x, y):
        self.oled.pixel(x, y, 1 - self.oled.pixel(x, y))

    def _dibujar(self, ok, estado, mask, pl, pr):
        o = self.oled
        o.fill(0)
        o.text("EST:" + (E.NOMBRES[estado] if ok else "SIN DATOS"), 0, 0, 1)
        o.hline(0, 10, 128, 1)
        # cara
        o.rect(16, 16, 32, 30, 1)
        o.rect(10, 26, 6, 8, 1)
        o.rect(48, 26, 6, 8, 1)
        if ok:
            if mask & E.OIDO_IZQ: o.fill_rect(10, 26, 6, 8, 1)
            if mask & E.OIDO_DER: o.fill_rect(48, 26, 6, 8, 1)
            if mask & E.CAB_IZQ:  o.fill_rect(18, 18, 14, 26, 1)
            if mask & E.CAB_DER:  o.fill_rect(32, 18, 14, 26, 1)
        self._inv(25, 28); self._inv(39, 28)
        for x in range(28, 36):
            self._inv(x, 38)
        # lista por sensor ("--" = desconocido porque no hay enlace)
        for y, nom, bit in ((14, "OI", E.OIDO_IZQ), (24, "OD", E.OIDO_DER),
                            (34, "CI", E.CAB_IZQ), (44, "CD", E.CAB_DER)):
            o.text(nom + " " + ("--" if not ok else ("FALLA" if mask & bit else "ok")), 64, y, 1)
        # línea inferior
        if not ok:
            msg = "FALLA ENLACE PI"
        elif mask & E.VINCHA:
            msg = "VINCHA DESCONEC."
        else:
            msg = None
        if msg:
            o.fill_rect(0, 53, 128, 11, 1)
            o.text(msg, 0, 55, 0)
        else:
            o.text("L:%3d R:%3d" % (pl, pr), 0, 55, 1)
