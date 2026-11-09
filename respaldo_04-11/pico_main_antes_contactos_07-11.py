from machine import Pin, I2C, UART, PWM, WDT
import ssd1306
import time

# --- CONFIGURACION GENERAL ---
led = Pin(25, Pin.OUT)
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
# El UART ya recibe por interrupcion: el hardware llena un buffer circular y el programa lo lee cuando puede. Un buffer mas grande
# aguanta que el bucle se demore (dibujar la pantalla tarda ~30 ms) sin perder bytes.
uart_pico = UART(1, baudrate=115200, tx=Pin(4), rx=Pin(5), rxbuf=1024)
# --- FRENOS ELECTROMAGNETICOS (GP20 = pin fisico 26) ---
# 1 = frenos aplicados, 0 = sueltos. Arranca en 1: la plataforma queda frenada hasta que la Pi active la marcha.
# Se sueltan cuando llega activacion = 1 (y estado de marcha); se vuelven a aplicar recien cuando la rampa llevo avance y giro a 0.
PIN_FRENO = 20
FRENO_APLICADO = 1
FRENO_SUELTO = 0
pin_freno = Pin(PIN_FRENO, Pin.OUT, value=FRENO_APLICADO)
ESTADOS_MARCHA = ("MARCHA", "MARCHA LIMITADA", "---")   # "---": la Pi no manda estado, alcanza la activacion

# --- CONFIGURACION MOTORES: MOVIMIENTO DIFERENCIAL (puente H, dos PWM por motor) ---
# Cada motor usa dos pines: IN1 gira hacia adelante, IN2 hacia atras (nunca los dos a la vez).
# Los pines GP16 y GP17 siguen siendo los de avance de cada motor; GP18 y GP19 son los NUEVOS de retroceso (revisar el cableado).
PIN_IZQ_ADELANTE, PIN_IZQ_ATRAS = 16, 18
PIN_DER_ADELANTE, PIN_DER_ATRAS = 17, 19
INVERTIR_IZQ = False             # True si al avanzar el motor izquierdo gira al reves
INVERTIR_DER = False
FREQ_PWM = 21000
PWM_MAX = 16000                  # duty maximo (0-65535) cuando la Pi manda 100 o -100. Subirlo de a poco en las pruebas
TIEMPO_MUERTO_MS = 20            # al invertir el sentido de un motor, los dos pines quedan en 0 este tiempo antes de usar el otro
TIEMPO_RAMPA_S = 1.0             # segundos para ir de 0 a PWM_MAX (invertir el sentido a fondo tarda el doble)

izq_av = PWM(Pin(PIN_IZQ_ADELANTE)); izq_at = PWM(Pin(PIN_IZQ_ATRAS))
der_av = PWM(Pin(PIN_DER_ADELANTE)); der_at = PWM(Pin(PIN_DER_ATRAS))
for p in (izq_av, izq_at, der_av, der_at):
    p.freq(FREQ_PWM)
    p.duty_u16(0)

# La Pi manda m1 = avance (eje X, -100..100) y m2 = giro (eje Z, -100..100).
# Compensacion de ruedas desbalanceadas: multiplica el duty de cada motor. Bajar (por ejemplo 0.92) el de la rueda que anda mas rapido;
# conviene dejar la mas lenta en 1.0 para no pasar PWM_MAX. Se ajusta probando avance recto en el piso (giro 0) y corrigiendo hasta que no se desvie.
COMPENSACION_IZQ = 1.0
COMPENSACION_DER = 1.0
GANANCIA_GIRO = 1.0              # 1.0 = el giro pesa igual que el avance; menor = giros mas suaves

# La Pi manda OBJETIVOS de avance y giro (porcentaje con signo). La Pico lleva los valores ACTUALES y los acerca de a poco:
# la rampa es sobre avance y giro (no sobre cada rueda), asi la proporcion entre los dos se mantiene mientras arranca.
avance_objetivo = 0.0
giro_objetivo = 0.0
avance_actual = 0.0
giro_actual = 0.0
# Duty que sale a cada motor, con signo (-PWM_MAX..PWM_MAX): negativo = retroceso.
izq_actual = 0
der_actual = 0
estado_pi = "---"                # estado en texto que informa la Pi: INICIO, CALIBRACION, REPOSO, MARCHA o EMERGENCIA
activacion = 0                   # 0 = motores detenidos; 1 = la cabeza los maneja (alterna con cada gesto doble)

# Variables de Seguridad (Watchdog UART)
TIMEOUT_MS = 500
ultimo_mensaje_ms = time.ticks_ms()
ultimo_rampa_ms = time.ticks_ms()


# --- MEZCLA DIFERENCIAL ---
def mezclar(avance, giro):
    """Avance y giro en porcentaje (-100..100) -> (izquierdo, derecho) en porcentaje (-100..100).
    izquierdo = avance + giro, derecho = avance - giro. Si alguno pasa de 100, se escalan los dos para mantener la curva."""
    izq = avance + giro * GANANCIA_GIRO
    der = avance - giro * GANANCIA_GIRO
    mayor = max(abs(izq), abs(der))
    if mayor > 100:
        izq = izq * 100 / mayor
        der = der * 100 / mayor
    return izq, der


def a_duty(porcentaje):
    """Porcentaje -100..100 -> duty con signo -PWM_MAX..PWM_MAX."""
    return int(max(-100, min(100, porcentaje)) * PWM_MAX / 100)


_sentido = {}                   # por motor: ultimo sentido aplicado (1 adelante, -1 atras) y cuando estuvo activo por ultima vez
_sentido_ms = {}


def escribir_motor(nombre, pin_adelante, pin_atras, valor, invertir, ahora_ms):
    """valor con signo: positivo usa el pin de adelante, negativo el de atras, nunca los dos a la vez.
    Si el sentido se invierte, los dos pines quedan en 0 durante TIEMPO_MUERTO_MS (tiempo muerto) antes de usar el otro."""
    if invertir:
        valor = -valor
    sentido = 1 if valor > 0 else (-1 if valor < 0 else 0)
    previo = _sentido.get(nombre, 0)
    if sentido != 0 and previo != 0 and sentido != previo and time.ticks_diff(ahora_ms, _sentido_ms[nombre]) < TIEMPO_MUERTO_MS:
        pin_adelante.duty_u16(0)
        pin_atras.duty_u16(0)
        return
    if sentido > 0:
        pin_atras.duty_u16(0)
        pin_adelante.duty_u16(valor)
    elif sentido < 0:
        pin_adelante.duty_u16(0)
        pin_atras.duty_u16(-valor)
    else:
        pin_adelante.duty_u16(0)
        pin_atras.duty_u16(0)
    if sentido != 0:
        _sentido[nombre] = sentido
        _sentido_ms[nombre] = ahora_ms


def rampa(avance, giro, avance_obj, giro_obj, dt_ms):
    """Mueve (avance, giro) hacia su objetivo en linea recta y a velocidad fija: la mayor de las dos diferencias avanza
    100 % en TIEMPO_RAMPA_S (medido con el tiempo real, porque dibujar la pantalla demora el bucle). La otra avanza
    proporcionalmente, asi las dos llegan a la vez y la relacion avance/giro se mantiene durante todo el arranque."""
    d_av = avance_obj - avance
    d_gi = giro_obj - giro
    mayor = max(abs(d_av), abs(d_gi))
    if mayor == 0:
        return avance, giro
    paso = 100.0 * dt_ms / (TIEMPO_RAMPA_S * 1000)
    if paso >= mayor:
        return avance_obj, giro_obj
    f = paso / mayor
    return avance + d_av * f, giro + d_gi * f
# --- FIN MEZCLA DIFERENCIAL ---


# --- TRAMAS ---
def parsear(linea):
    """Una linea COMPLETA (sin el salto de linea) -> (tp9, fp1, fp2, tp10, avance, giro, activacion, estado) o None si no es valida.
    Formato de la Pi: 7 numeros y, opcional, el estado en texto: "tp9,fp1,fp2,tp10,m1,m2,activacion[,ESTADO]"."""
    try:
        partes = linea.decode('utf-8').strip().split(',')
        if len(partes) not in (7, 8):
            return None
        v = [int(x) for x in partes[:7]]
    except (ValueError, UnicodeError):
        return None
    return v[0], v[1], v[2], v[3], v[4], v[5], v[6], (partes[7] if len(partes) == 8 else "---")


def sacar_lineas(buf, nuevos):
    """Agrega los bytes recibidos al buffer y devuelve (lineas completas, resto sin terminar). Una linea a medias queda en el resto
    hasta que llegue su salto de linea: asi nunca se interpreta una trama cortada."""
    buf = buf + nuevos
    if len(buf) > 1024:                  # sin saltos de linea no hay trama: se descarta lo viejo
        buf = buf[-256:]
    lineas = buf.split(b"\n")
    return lineas[:-1], lineas[-1]
# --- FIN TRAMAS ---


def dibujar_cara_espejada(sensor_fallo):
    oled.fill(0)

    # Textos
    oled.text(f"EST:{estado_pi}", 0, 0, 1)
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

    # Estado de motores para diagnostico visual (con signo: negativo = retroceso)
    pi_ = int((izq_actual / PWM_MAX) * 100)
    pd_ = int((der_actual / PWM_MAX) * 100)

    oled.text(f"Err:{sensor_fallo}", 0, 46, 1)
    oled.text(f"I:{pi_} D:{pd_}", 0, 56, 1)
    oled.text(f"A{activacion}", 112, 56, 1)

    oled.show()


# --- BUCLE PRINCIPAL ---
error_actual = "normal"
resto_uart = b""
tramas_ok = 0
tramas_mal = 0
hueco_max_ms = 0
errores_pantalla = 0              # dibujos de la pantalla que fallaron (I2C)
ultimo_ok_ms = time.ticks_ms()
informe_ms = time.ticks_ms()
firma_dibujo = None
ultimo_dibujo_ms = 0


def refrescar_pantalla(ahora_ms, forzar=False):
    """Redibuja solo si cambio algo visible (estado, falla de sensor, activacion o motores redondeados a 5 %) y como mucho cada 150 ms:
    mandar el cuadro por I2C demora ~30 ms y retrasaria el resto del bucle."""
    global firma_dibujo, ultimo_dibujo_ms, errores_pantalla
    firma = (estado_pi, error_actual, activacion, int(izq_actual * 100 / PWM_MAX) // 5, int(der_actual * 100 / PWM_MAX) // 5)
    if firma == firma_dibujo:
        return
    if not forzar and time.ticks_diff(ahora_ms, ultimo_dibujo_ms) < 150:
        return
    firma_dibujo = firma
    ultimo_dibujo_ms = ahora_ms
    try:
        dibujar_cara_espejada(error_actual)
    except Exception:                    # un error de I2C (ruido de los motores) no debe tirar abajo el control de los motores
        errores_pantalla += 1


# --- SEGURIDAD DEL PROGRAMA ---
# Si el programa de la Pico se cae (excepcion) o se cuelga, los pines PWM siguen sacando el ultimo valor y los motores quedan andando.
# 1) Cualquier error dentro del bucle: motores a 0, frenos aplicados, se anota y se sigue.
# 2) Watchdog de hardware: si el bucle deja de dar vueltas, la Pico se reinicia sola (al arrancar los PWM quedan en 0 y los frenos aplicados).
#    Arranca recien con la primera trama valida de la Pi, para poder detener el programa desde Thonny al encender.
#    OJO: una vez activo no se puede apagar; para editar en Thonny, desconecta o apaga la Pi y reinicia la Pico.
USAR_WATCHDOG = True
WATCHDOG_MS = 2000
wdt = None
errores_bucle = 0


def parar_todo():
    """Motores a 0 y frenos aplicados, sin depender de nada mas (se usa ante un error)."""
    global avance_objetivo, giro_objetivo, avance_actual, giro_actual, izq_actual, der_actual, activacion
    for p in (izq_av, izq_at, der_av, der_at):
        try:
            p.duty_u16(0)
        except Exception:
            pass
    try:
        pin_freno.value(FRENO_APLICADO)
    except Exception:
        pass
    avance_objetivo = giro_objetivo = avance_actual = giro_actual = 0.0
    izq_actual = der_actual = 0
    activacion = 0


refrescar_pantalla(time.ticks_ms(), True)

try:
    while True:
        try:

            # 1. Leer UART: se juntan los bytes disponibles, se separan las lineas completas y se usa la ULTIMA valida
            ultima_trama = None
            n = uart_pico.any()
            if n:
                nuevos = uart_pico.read(n)
                if nuevos:
                    lineas, resto_uart = sacar_lineas(resto_uart, nuevos)
                    for linea in lineas:
                        t = parsear(linea)
                        if t is None:
                            tramas_mal += 1
                        else:
                            tramas_ok += 1
                            ultima_trama = t

            if ultima_trama:
                tp9, fp1, fp2, tp10, avance, giro, activacion, estado_pi = ultima_trama
                if estado_pi == "EMERGENCIA":         # emergencia: los motores se cortan de golpe
                    activacion = 0
                    avance_actual = 0.0
                    giro_actual = 0.0

                # REINICIAMOS EL RELOJ DE SEGURIDAD (y medimos el hueco entre tramas validas, para detectar cortes)
                ultimo_mensaje_ms = time.ticks_ms()
                hueco = time.ticks_diff(ultimo_mensaje_ms, ultimo_ok_ms)
                if hueco > hueco_max_ms:
                    hueco_max_ms = hueco
                ultimo_ok_ms = ultimo_mensaje_ms

                # Movimiento diferencial: avance (eje X) y giro (eje Z) -> motor izquierdo y derecho, con signo.
                # Sin activacion (flag en 0) los motores quedan detenidos.
                if activacion:
                    avance_objetivo = max(-100, min(100, avance))
                    giro_objetivo = max(-100, min(100, giro))
                else:
                    avance_objetivo = 0.0
                    giro_objetivo = 0.0

                # Lógica de detección de error
                if tp9 > 2 and tp10 > 2: error_actual = "ambos_oidos"
                elif tp9 > 2: error_actual = "oido_izq"
                elif tp10 > 2: error_actual = "oido_der"
                elif fp1 > 2: error_actual = "cabeza_izq"
                elif fp2 > 2: error_actual = "cabeza_der"
                else: error_actual = "normal"

                led.toggle()

            # ==========================================
            # 2. CONTROL DE SEGURIDAD (WATCHDOG UART)
            # ==========================================
            tiempo_actual = time.ticks_ms()
            if time.ticks_diff(tiempo_actual, ultimo_mensaje_ms) > TIMEOUT_MS:
                # CORTE DE GOLPE: Forzamos objetivos y actuales a 0
                avance_objetivo = 0.0
                giro_objetivo = 0.0
                avance_actual = 0.0
                giro_actual = 0.0
                activacion = 0
                estado_pi = "SIN ENLACE"
                error_actual = "DESC"

            # ==========================================
            # 3. MOTOR DE RAMPA SUAVE
            # ==========================================
            ahora_ms = time.ticks_ms()
            dt_ms = time.ticks_diff(ahora_ms, ultimo_rampa_ms)
            ultimo_rampa_ms = ahora_ms
            avance_actual, giro_actual = rampa(avance_actual, giro_actual, avance_objetivo, giro_objetivo, dt_ms)
            izq_pct, der_pct = mezclar(avance_actual, giro_actual)
            izq_actual = a_duty(izq_pct * COMPENSACION_IZQ)
            der_actual = a_duty(der_pct * COMPENSACION_DER)

            # Frenos: se sueltan con activacion y estado de marcha; se aplican cuando no hay activacion y la rampa ya llego a 0
            # (en EMERGENCIA o sin tramas los valores actuales ya se pusieron en 0, asi que se aplican enseguida)
            if activacion and estado_pi in ESTADOS_MARCHA:
                pin_freno.value(FRENO_SUELTO)
            elif avance_actual == 0 and giro_actual == 0:
                pin_freno.value(FRENO_APLICADO)

            # Aplicamos a los pines fisicos
            escribir_motor('izq', izq_av, izq_at, izq_actual, INVERTIR_IZQ, ahora_ms)
            escribir_motor('der', der_av, der_at, der_actual, INVERTIR_DER, ahora_ms)

            # Pantalla: solo si cambio algo (despues de mover los motores, para que dibujar no los demore)
            refrescar_pantalla(ahora_ms)

            # Informe cada 2 s por la consola (Thonny): sirve para ver si la Pi manda tramas parejas o con cortes
            if time.ticks_diff(ahora_ms, informe_ms) > 2000:
                print("[UART] tramas ok:", tramas_ok, "mal:", tramas_mal, "hueco max:", hueco_max_ms, "ms | estado:", estado_pi, "act:", activacion,
                          "| err bucle:", errores_bucle, "pantalla:", errores_pantalla)
                tramas_ok = 0
                tramas_mal = 0
                hueco_max_ms = 0
                informe_ms = ahora_ms

            # Pausa minúscula
            time.sleep(0.01)

            if wdt is not None:
                wdt.feed()
            elif USAR_WATCHDOG and tramas_ok > 0:
                wdt = WDT(timeout=WATCHDOG_MS)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            parar_todo()
            errores_bucle += 1
            print("[ERROR] bucle:", e, "| motores a 0 y frenos aplicados (errores:", errores_bucle, ")")
            if wdt is not None:
                wdt.feed()
            time.sleep(0.05)
finally:
    parar_todo()                         # al detener el programa (Ctrl+C en Thonny) los motores no quedan andando
