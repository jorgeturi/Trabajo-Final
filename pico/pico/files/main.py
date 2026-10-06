# ==========================================================
#  TEST HMI PICO  -  un solo archivo  (usa tu ssd1306.py)
#  Copiar a la Pico junto a ssd1306.py y ejecutar.
# ==========================================================

# ---------------- PARÁMETROS (tocá solo acá) ----------------
MODO = "AUTO"       # "AUTO" = recorre todas las pantallas | "FIJO" = una pantalla | "PI" = UART real

# Bits de fallas (se suman con | para combinarlas)
OIDO_IZQ, OIDO_DER, CAB_IZQ, CAB_DER, VINCHA = 1, 2, 4, 8, 16
# Estados
INICIO, CALIBRACION, REPOSO, MARCHA, EMERGENCIA = 0, 1, 2, 3, 4

# --- AUTO ---
SIM_PASO_MS = 1500
SIM_ESTADOS = (INICIO, CALIBRACION, REPOSO, MARCHA, EMERGENCIA)  # recortá para acortar
SIM_PWM_MARCHA = (250, 250)

# --- FIJO ---  (ej: FIJO_FALLAS = OIDO_IZQ | CAB_DER | VINCHA)
FIJO_ESTADO = MARCHA
FIJO_FALLAS = OIDO_IZQ | CAB_DER
FIJO_PWM = (200, 220)
FIJO_ENLACE_OK = True          # False = simula que la Pi no manda datos

# --- PI (UART1) --- línea:  ESTADO,FALLAS,PWM_L,PWM_R\n   ej: 3,5,60,40
PIN_UART_TX, PIN_UART_RX, UART_BAUD = 4, 5, 115200
LINK_TIMEOUT_MS = 500          # sin línea válida -> FALLA ENLACE PI

# --- Hardware ---
PIN_SDA, PIN_SCL, I2C_FREQ = 0, 1, 400000
OLED_W, OLED_H = 128, 64
OLED_REFRESH_MS = 100
PIN_PWM_IZQ, PIN_PWM_DER, PWM_FREQ = 16, 17, 1000
PIN_BUZZER = None              # ej: 16 cuando lo conectes (buzzer activo)
PIN_LED = 25
LOOP_MS = 10
USAR_WDT, WDT_MS = False, 2000  # en test queda apagado; ponelo True en producción
# ------------------------------------------------------------

from machine import Pin, I2C, PWM, UART, WDT
from time import ticks_ms, ticks_add, ticks_diff, sleep_ms
import ssd1306

SENSORES = OIDO_IZQ | OIDO_DER | CAB_IZQ | CAB_DER
NOMBRES = ("INICIO", "CALIBRACION", "REPOSO", "MARCHA", "EMERGENCIA")


# ---------------- FUENTES DE DATOS ----------------
class Simulador:
    """AUTO / FIJO. Misma interfaz que EnlacePi."""
    def __init__(self, now):
        self._t, self._i, self._ok = now, 0, True
        self.estado = self.mascara = self.pwm_l = self.pwm_r = 0
        self._aplicar()

    def _aplicar(self):
        if MODO == "FIJO":
            self._ok = FIJO_ENLACE_OK
            self.estado, self.mascara = FIJO_ESTADO, FIJO_FALLAS & 31
            self.pwm_l, self.pwm_r = FIJO_PWM
            return
        n = len(SIM_ESTADOS) * 32
        i = self._i % (n + 1)
        if i == n:                      # última pantalla: sin enlace
            self._ok = False
            return
        self._ok = True
        self.estado = SIM_ESTADOS[i // 32]
        self.mascara = i % 32           # 0..31 = sensores + vincha
        self.pwm_l, self.pwm_r = SIM_PWM_MARCHA if self.estado == MARCHA else (0, 0)

    def poll(self, now):
        if MODO == "AUTO" and ticks_diff(now, self._t) >= SIM_PASO_MS:
            self._t = now
            self._i += 1
            self._aplicar()

    def enlace_ok(self, now):
        return self._ok


class EnlacePi:
    """Recepción UART no bloqueante."""
    def __init__(self):
        self.uart = UART(1, baudrate=UART_BAUD, tx=Pin(PIN_UART_TX),
                         rx=Pin(PIN_UART_RX), timeout=0)
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
                self.buf = bytearray()

    def _parse(self, linea, now):
        try:
            e, f, l, r = (int(x) for x in bytes(linea).split(b","))
            if not 0 <= e <= 4:
                return
        except Exception:
            return
        self.estado, self.mascara = e, f & 31
        self.pwm_l, self.pwm_r = max(0, min(100, l)), max(0, min(100, r))
        self.ultimo_ms = now
        self.hay_datos = True

    def enlace_ok(self, now):
        return self.hay_datos and ticks_diff(now, self.ultimo_ms) < LINK_TIMEOUT_MS


# ---------------- SALIDAS ----------------
class Motores:
    def __init__(self):
        self.l = PWM(Pin(PIN_PWM_IZQ)); self.l.freq(PWM_FREQ)
        self.r = PWM(Pin(PIN_PWM_DER)); self.r.freq(PWM_FREQ)
        self.set(0, 0)

    def set(self, pl, pr):
        self.l.duty_u16(pl * 65535 // 100)
        self.r.duty_u16(pr * 65535 // 100)


class Buzzer:
    _PAT = {1: (100, 1400), 2: (250, 250)}   # (ms on, ms off)
    def __init__(self):
        self.pin = Pin(PIN_BUZZER, Pin.OUT) if PIN_BUZZER is not None else None
        self._on, self._t = False, 0
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


# ---------------- PANTALLA ----------------
# OLED bicolor:  AMARILLO y=0..15 (2 líneas de texto)  |  AZUL y=16..63
#   Amarillo: línea 1 = estado, línea 2 = aviso (enlace / vincha / sensores)
#   Azul    : cara grande a la izquierda + 4 filas con nombre completo;
#             la fila (y la zona de la cara) que falla se ve en VIDEO INVERSO
class Pantalla:
    def __init__(self):
        self.oled, self.ultimo, self._t = None, None, ticks_ms()
        self._init()

    def _init(self):
        try:
            i2c = I2C(0, scl=Pin(PIN_SCL), sda=Pin(PIN_SDA), freq=I2C_FREQ)
            self.oled = ssd1306.SSD1306_I2C(OLED_W, OLED_H, i2c)
        except Exception:
            self.oled = None

    def actualizar(self, now, ok, estado, mask):
        vista = (ok, estado, mask)
        if vista == self.ultimo or ticks_diff(now, self._t) < OLED_REFRESH_MS:
            return
        self._t = now
        if self.oled is None:
            self._init()
            if self.oled is None:
                return
        try:
            self._dibujar(ok, estado, mask)
            self.oled.show()
            self.ultimo = vista
        except Exception:
            self.oled = None            # no rompe el lazo; reintenta luego

    def _inv(self, x, y, w=1, h=1):
        fb = self.oled.framebuf         # tu driver no lee pixeles: se lee del FrameBuffer
        for i in range(w):
            for j in range(h):
                fb.pixel(x + i, y + j, 1 - fb.pixel(x + i, y + j))

    def _dibujar(self, ok, estado, mask):
        o = self.oled
        o.fill(0)

        # ---- ZONA AMARILLA (y 0..15) ----
        o.text("EST:" + (NOMBRES[estado] if ok else "SIN DATOS"), 0, 0, 1)
        if not ok:
            msg = "FALLA ENLACE PI"
        elif mask & VINCHA:
            msg = "VINCHA DESCONEC."
        elif mask & SENSORES:
            msg = "FALLA SENSOR"
        else:
            msg = None
        if msg:
            o.fill_rect(0, 8, 128, 8, 1)        # franja inversa = alerta
            o.text(msg, 0, 8, 0)
        else:
            o.text("SENSORES OK", 0, 8, 1)

        # ---- ZONA AZUL (y 16..63) ----
        # cara
        o.rect(12, 20, 24, 40, 1)               # cabeza
        o.rect(5, 32, 7, 14, 1)                 # oído izq
        o.rect(36, 32, 7, 14, 1)                # oído der
        f = ok and mask
        if f & OIDO_IZQ: o.fill_rect(5, 32, 7, 14, 1)
        if f & OIDO_DER: o.fill_rect(36, 32, 7, 14, 1)
        if f & CAB_IZQ:  o.fill_rect(14, 22, 10, 36, 1)
        if f & CAB_DER:  o.fill_rect(24, 22, 10, 36, 1)
        self._inv(18, 30, 2, 2); self._inv(28, 30, 2, 2)   # ojos
        self._inv(19, 50, 10, 1)                           # boca

        # filas con nombre completo (inversa = falla)
        for y, nom, bit in ((18, "OIDO IZQ", OIDO_IZQ), (30, "OIDO DER", OIDO_DER),
                            (42, "CABEZA IZQ", CAB_IZQ), (54, "CABEZA DER", CAB_DER)):
            if f & bit:
                o.fill_rect(45, y - 1, 83, 10, 1)
                o.text(nom, 46, y, 0)
            else:
                o.text(nom, 46, y, 1)


# ---------------- LAZO PRINCIPAL ----------------
def main():
    now = ticks_ms()
    fuente = EnlacePi() if MODO == "PI" else Simulador(now)
    motores, buzzer, pantalla = Motores(), Buzzer(), Pantalla()
    led = Pin(PIN_LED, Pin.OUT)
    wdt = WDT(timeout=WDT_MS) if USAR_WDT else None
    proximo, t_led = ticks_add(now, LOOP_MS), now

    while True:
        now = ticks_ms()
        fuente.poll(now)
        ok = fuente.enlace_ok(now)
        if ok:
            est, mask, pl, pr = fuente.estado, fuente.mascara, fuente.pwm_l, fuente.pwm_r
        else:                           # sin datos de la Pi: PWM a 0 + falla
            est, mask, pl, pr = 0, 0, 0, 0

        motores.set(pl, pr)
        buzzer.actualizar(now, 2 if (not ok or est == EMERGENCIA) else (1 if mask else 0))
        pantalla.actualizar(now, ok, est, mask)

        if ticks_diff(now, t_led) >= 500:
            led.toggle(); t_led = now
        if wdt:
            wdt.feed()
        d = ticks_diff(proximo, ticks_ms())
        if d > 0:
            sleep_ms(d)
            proximo = ticks_add(proximo, LOOP_MS)
        else:
            proximo = ticks_add(ticks_ms(), LOOP_MS)

main()