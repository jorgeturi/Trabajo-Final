# Recibe de la Pi, sin bloquear. Una línea por mensaje:
#       ESTADO,FALLAS,PWM_L,PWM_R\n        ej:  3,5,60,40
#   ESTADO 0..4 | FALLAS = máscara (1 OI, 2 OD, 4 CI, 8 CD, 16 vincha) | PWM 0..100
from machine import UART, Pin
from time import ticks_ms, ticks_diff
import config as C

class EnlacePi:
    def __init__(self):
        self.uart = UART(1, baudrate=C.UART_BAUD, tx=Pin(C.PIN_UART_TX),
                         rx=Pin(C.PIN_UART_RX), timeout=0)
        self.buf = bytearray()
        self.hay_datos = False
        self.ultimo_ms = ticks_ms()
        self.estado = self.mascara = self.pwm_l = self.pwm_r = 0

    def poll(self, now):
        n = self.uart.any()
        if not n:
            return
        for b in self.uart.read(min(n, 64)) or b"":
            if b == 10:
                self._parse(self.buf, now)
                self.buf = bytearray()
            elif b != 13 and len(self.buf) < 32:
                self.buf.append(b)
            elif b != 13:
                self.buf = bytearray()          # basura: descartar

    def _parse(self, linea, now):
        try:
            e, f, l, r = (int(x) for x in bytes(linea).split(b","))
            if not 0 <= e <= 4:
                return
        except Exception:
            return                              # línea inválida: no cuenta como dato
        self.estado, self.mascara = e, f & 31
        self.pwm_l, self.pwm_r = max(0, min(100, l)), max(0, min(100, r))
        self.ultimo_ms = now
        self.hay_datos = True

    def enlace_ok(self, now):
        return self.hay_datos and ticks_diff(now, self.ultimo_ms) < C.LINK_TIMEOUT_MS
