from machine import Pin, I2C
import ssd1306
import time

# Configuración del LED (puedes cambiar a Pin("LED") si tu placa usa ese nombre)
led = Pin(25, Pin.OUT)

# Configuración I2C (pines 0 y 1)
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=400000)

# Dimensiones de la OLED
ancho = 128
alto = 64
oled = ssd1306.SSD1306_I2C(ancho, alto, i2c)

def mostrar_interfaz(estado_actual, error_texto):
    # 1. Limpiar pantalla
    oled.fill(0)
    
    # 2. Dibujar una línea divisoria horizontal debajo de la zona amarilla (ej. en Y = 16)
    oled.hline(0, 16, 128, 1)
    
    # 3. Mostrar el Estado Arriba (Zona Amarilla / Título)
    oled.text("ESTADO:", 0, 0, 1)
    oled.text(estado_actual, 64, 0, 1)
    
    # 4. Mostrar los Errores Abajo (Zona Celeste)
    # Dividimos en dos líneas por si el texto es largo
    oled.text("AVISOS:", 0, 24, 1)
    oled.text(error_texto[:16], 0, 40, 1)    # Parte 1 del error
    oled.text(error_texto[16:], 0, 52, 1)    # Parte 2 del error (si es muy largo)
    
    # Refrescar pantalla física
    oled.show()

# --- PRUEBA DE LOS ESTADOS Y ERRORES ---
# Lista de estados que pediste
estados = ["INICIO", "CALIBRACION", "MARCHA", "EMERGENCIA"]

# Lista de errores que mencionaste
errores = [
    "Sistema OK",
    "Err: Izq Oido",
    "Err: Der Cabeza",
    "Err: Der Cab + Oido Der",
    "Err: Der Cab + Oido Izq + Oido Der + Izq Cab"
]

# Bucle de prueba para ver cómo cambian en pantalla
while True:
    for est in estados:
        for err in errores:
            led.toggle()
            mostrar_interfaz(est, err)
            time.sleep(2) # Cambia cada 2 segundos para probar