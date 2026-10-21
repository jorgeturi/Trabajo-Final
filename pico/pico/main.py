from machine import Pin, I2C, UART, PWM
import ssd1306
import time

# --- CONFIGURACION GENERAL ---
led = Pin(25, Pin.OUT)
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
uart_pico = UART(1, baudrate=115200, tx=Pin(4), rx=Pin(5))

# --- CONFIGURACION MOTORES (PWM) ---
motor1 = PWM(Pin(16))
motor2 = PWM(Pin(17))
motor1.freq(1000)
motor2.freq(1000)

# Variables para la rampa suave
pwm1_actual = 0
pwm1_objetivo = 0
pwm2_actual = 0
pwm2_objetivo = 0

PASO_SUAVIZADO = 500 

# Variables de Seguridad (Watchdog UART)
TIMEOUT_MS = 500  
ultimo_mensaje_ms = time.ticks_ms()

def dibujar_cara_espejada(sensor_fallo, mensaje_crudo):
    oled.fill(0)
    
    # Textos
    oled.text(f"Val:{mensaje_crudo}", 0, 0, 1)
    oled.hline(0, 10, 128, 1)
    
    # Contorno y ojos
    oled.rect(48, 20, 32, 28, 1)  
    oled.rect(42, 28, 6, 8, 1)   
    oled.rect(80, 28, 6, 8, 1)   
    oled.pixel(56, 30, 1)
    oled.pixel(71, 30, 1)
    oled.hline(60, 40, 8, 1)

    # --- DIBUJO INVERTIDO ---
    if sensor_fallo == "oido_izq": oled.fill_rect(80, 28, 6, 8, 1)      
    elif sensor_fallo == "oido_der": oled.fill_rect(42, 28, 6, 8, 1)      
    elif sensor_fallo == "cabeza_izq": oled.fill_rect(64, 22, 14, 24, 1)    
    elif sensor_fallo == "cabeza_der": oled.fill_rect(50, 22, 14, 24, 1)    
    elif sensor_fallo == "ambos_oidos":
        oled.fill_rect(42, 28, 6, 8, 1)
        oled.fill_rect(80, 28, 6, 8, 1)

    # Estado de motores para diagnostico visual
    p1 = int((pwm1_actual / 65535) * 100)
    p2 = int((pwm2_actual / 65535) * 100)
    
    oled.text(f"Err:{sensor_fallo}", 0, 46, 1)
    oled.text(f"M1:{p1}% M2:{p2}%", 0, 56, 1)
    
    oled.show()

# --- BUCLE PRINCIPAL ---
error_actual = "normal"
valores_texto = "ESPERANDO..."
dibujar_cara_espejada(error_actual, valores_texto)

while True:
    ultima_trama = None
    
    # 1. Leer UART (Vaciamos el buffer para tener el más actual)
    while uart_pico.any():
        ultima_trama = uart_pico.readline()
        
    if ultima_trama:
        try:
            texto_limpio = ultima_trama.decode('utf-8').strip()
            valores = [int(x) for x in texto_limpio.split(',')]
            
            if len(valores) in (6, 7):
                tp9, fp1, fp2, tp10, m1_porcentaje, m2_porcentaje = valores[:6]
                activacion = valores[6] if len(valores) == 7 else 0
                valores_texto = f"{tp9},{fp1},{fp2},{tp10}"
                
                # REINICIAMOS EL RELOJ DE SEGURIDAD
                ultimo_mensaje_ms = time.ticks_ms()
                
                # Actualizamos los OBJETIVOS lógicos del PWM (0 a 65535)
                # OJO: Los negativos de la Pi se capean a 0 aquí con el max(0, ...)
                pwm1_objetivo = int((max(0, min(100, m1_porcentaje)) / 100) * 65535)
                pwm2_objetivo = int((max(0, min(100, m2_porcentaje)) / 100) * 65535)
                
                # Lógica de detección de error
                if tp9 > 2 and tp10 > 2: error_actual = "ambos_oidos"
                elif tp9 > 2: error_actual = "oido_izq"
                elif tp10 > 2: error_actual = "oido_der"
                elif fp1 > 2: error_actual = "cabeza_izq"
                elif fp2 > 2: error_actual = "cabeza_der"
                else: error_actual = "normal"
                
                led.toggle()
                dibujar_cara_espejada(error_actual, valores_texto)
                
        except (ValueError, UnicodeError):
            pass

    # ==========================================
    # 2. CONTROL DE SEGURIDAD (WATCHDOG UART)
    # ==========================================
    tiempo_actual = time.ticks_ms()
    if time.ticks_diff(tiempo_actual, ultimo_mensaje_ms) > TIMEOUT_MS:
        # CORTE DE GOLPE: Forzamos objetivos y actuales a 0
        pwm1_objetivo = 0
        pwm1_actual = 0
        pwm2_objetivo = 0
        pwm2_actual = 0
        
        if error_actual != "DESC":
            error_actual = "DESC"
            dibujar_cara_espejada(error_actual, "SIN DATOS")

    # ==========================================
    # 3. MOTOR DE RAMPA SUAVE
    # ==========================================
    
    # Rampa Motor 1
    if pwm1_actual < pwm1_objetivo:
        pwm1_actual += PASO_SUAVIZADO
        if pwm1_actual > pwm1_objetivo: pwm1_actual = pwm1_objetivo
    elif pwm1_actual > pwm1_objetivo:
        pwm1_actual -= PASO_SUAVIZADO
        if pwm1_actual < pwm1_objetivo: pwm1_actual = pwm1_objetivo
        
    # Rampa Motor 2
    if pwm2_actual < pwm2_objetivo:
        pwm2_actual += PASO_SUAVIZADO
        if pwm2_actual > pwm2_objetivo: pwm2_actual = pwm2_objetivo
    elif pwm2_actual > pwm2_objetivo:
        pwm2_actual -= PASO_SUAVIZADO
        if pwm2_actual < pwm2_objetivo: pwm2_actual = pwm2_objetivo

    # Aplicamos a los pines fisicos 
    motor1.duty_u16(pwm1_actual)
    motor2.duty_u16(pwm2_actual)

    # Pausa minúscula
    time.sleep(0.01)