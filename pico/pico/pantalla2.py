from machine import Pin, I2C
import ssd1306
import time

# Configuración del LED integrado
led = Pin(25, Pin.OUT)

# Configuración de los pines I2C (SCL en Pin 1, SDA en Pin 0)
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=400000)

# Dimensiones de la pantalla OLED
ancho = 128
alto = 64
oled = ssd1306.SSD1306_I2C(ancho, alto, i2c)

def dibujar_cara(sensor_fallo, estado):
    # 1. Limpiar pantalla
    oled.fill(0)
    
    # 2. Dibujar el estado arriba (Zona amarilla)
    oled.text("EST:", 0, 0, 1)
    oled.text(estado, 35, 0, 1)
    oled.hline(0, 10, 128, 1)  # Línea divisoria superior
    
    # 3. Dibujar la cabeza (Contorno principal)
    oled.rect(48, 20, 32, 28, 1)  
    
    # Orejas izquierda y derecha
    oled.rect(42, 28, 6, 8, 1)   
    oled.rect(80, 28, 6, 8, 1)   
    
    # Dibujar ojos fijos en la cara (píxeles simples)
    oled.pixel(56, 30, 1)  # Ojo izquierdo
    oled.pixel(71, 30, 1)  # Ojo derecho
    
    # Dibujar una pequeña boca
    oled.hline(60, 40, 8, 1)

    # 4. Marcar visualmente el sensor con error según lo que reciba
    if sensor_fallo == "oido_izq":
        oled.fill_rect(42, 28, 6, 8, 1)      # Rellena oreja izquierda
    elif sensor_fallo == "oido_der":
        oled.fill_rect(80, 28, 6, 8, 1)      # Rellena oreja derecha
    elif sensor_fallo == "cabeza_izq":
        oled.fill_rect(50, 22, 14, 24, 1)    # Rellena mitad izquierda de la cabeza
    elif sensor_fallo == "cabeza_der":
        oled.fill_rect(64, 22, 14, 24, 1)    # Rellena mitad derecha de la cabeza
    elif sensor_fallo == "ambos_oidos":
        oled.fill_rect(42, 28, 6, 8, 1)
        oled.fill_rect(80, 28, 6, 8, 1)

    # 5. Texto descriptivo abajo (Zona celeste)
    oled.text(f"Err: {sensor_fallo}", 0, 54, 1)
    
    # Enviar los cambios al display físico
    oled.show()

# --- BUCLE DE PRUEBA ---
estados = ["INICIO", "CALIBRACION", "MARCHA", "EMERGENCIA"]
errores = ["normal", "oido_izq", "oido_der", "cabeza_izq", "cabeza_der", "ambos_oidos"]

while True:
    for est in estados:
        for err in errores:
            led.toggle()
            dibujar_cara(err, est)
            time.sleep(2)