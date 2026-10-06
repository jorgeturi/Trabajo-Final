from estados import *   # para poder escribir FIJO_FALLOS = OIDO_IZQ | CAB_DER

# ================= MODO DE PRUEBA =================
#  "AUTO" : recorre solo todas las pantallas (estados x fallas x vincha + sin enlace)
#  "FIJO" : muestra exactamente lo que pongas en FIJO_* (para ver UNA pantalla)
#  "PI"   : datos reales por UART desde la Raspberry Pi
MODO = "AUTO"

# --- AUTO ---
SIM_PASO_MS = 1500
SIM_ESTADOS = (INICIO, CALIBRACION, REPOSO, MARCHA, EMERGENCIA)  # recortá para acortar el ciclo

# --- FIJO --- (ejemplos de FIJO_FALLOS:  0 | OIDO_IZQ | CAB_DER | VINCHA | SENSORES)
FIJO_ESTADO = MARCHA
FIJO_FALLOS = OIDO_IZQ | CAB_DER
FIJO_PWM = (60, 40)
FIJO_ENLACE_OK = True      # False = simula que la Pi no manda datos

# ================= HARDWARE =================
PIN_SDA, PIN_SCL, I2C_FREQ = 0, 1, 400000
OLED_W, OLED_H = 128, 64
OLED_REFRESH_MS = 100

PIN_UART_TX, PIN_UART_RX, UART_BAUD = 4, 5, 115200   # UART1 (cruzar TX<->RX, unir GND)
PIN_PWM_IZQ, PIN_PWM_DER, PWM_FREQ = 14, 15, 1000
PIN_BUZZER = None          # ej. 16 cuando lo conectes
PIN_LED = 25

# ================= TIEMPOS =================
LOOP_MS = 10
LINK_TIMEOUT_MS = 500      # sin línea válida de la Pi -> falla de enlace
USAR_WDT = True
WDT_MS = 2000
