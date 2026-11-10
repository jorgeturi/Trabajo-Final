from machine import Pin, I2C, UART, PWM, WDT
import ssd1306
import time

# --- CONFIGURACION GENERAL ---
try:
    led = Pin("LED", Pin.OUT)      # LED de la placa (Pico y Pico W): cambia con cada trama valida de la Pi
except Exception:
    led = Pin(25, Pin.OUT)
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
# El UART ya recibe por interrupcion: el hardware llena un buffer circular y el programa lo lee cuando puede. Un buffer mas grande
# aguanta que el bucle se demore (dibujar la pantalla tarda ~30 ms) sin perder bytes.
uart_pico = UART(1, baudrate=115200, tx=Pin(4), rx=Pin(5), rxbuf=1024)
# --- FRENOS ELECTROMAGNETICOS (GP26) ---
# 0 = frenos aplicados, 1 = sueltos (09-11: el modulo de frenos frena con 0). Lleva pull-down externo a GND
# para que los frenos queden aplicados mientras la Pico arranca o se reinicia. Arranca en 0: la plataforma queda frenada hasta que la Pi active la marcha.
# Se sueltan cuando llega activacion = 1 (y estado de marcha); se vuelven a aplicar recien cuando la rampa llevo avance y giro a 0.
PIN_FRENO = 26
FRENO_APLICADO = 0
FRENO_SUELTO = 1
pin_freno = Pin(PIN_FRENO, Pin.OUT, value=FRENO_APLICADO)
ESTADOS_MARCHA = ("MARCHA", "MARCHA LIMITADA", "---")   # "---": la Pi no manda estado, alcanza la activacion

# --- CONFIGURACION MOTORES: MOVIMIENTO DIFERENCIAL (puente H, dos PWM por motor) ---
# Cada motor usa dos pines: IN1 gira hacia adelante, IN2 hacia atras (nunca los dos a la vez).
# Los pines GP16 y GP17 siguen siendo los de avance de cada motor; GP18 y GP19 son los NUEVOS de retroceso (revisar el cableado).
PIN_IZQ_ADELANTE, PIN_IZQ_ATRAS = 18, 20
PIN_DER_ADELANTE, PIN_DER_ATRAS = 19, 21
INVERTIR_IZQ = False             # True si al avanzar el motor izquierdo gira al reves
INVERTIR_DER = False
FREQ_PWM = 21000
PWM_MAX = 16000                  # duty maximo (0-65535) cuando la Pi manda 100 o -100. Subirlo de a poco en las pruebas
TIEMPO_MUERTO_MS = 40            # al invertir el sentido de un motor, los dos pines quedan en 0 este tiempo antes de usar el otro
TIEMPO_RAMPA_S = 1.5             # segundos para ir de 0 a PWM_MAX (invertir el sentido a fondo tarda el doble)

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
tp9 = fp1 = fp2 = tp10 = 4         # contacto de cada electrodo que informa la Pi (1 bueno ... 4 sin contacto)
CONTACTO_OK_MAX = 2              # 1 o 2 = bien puesto ("ok"); 3 o 4 = mal puesto ("__"), igual que CONTACTO_FALLA de la Pi
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
    Formato de la Pi: "tp9,fp1,fp2,tp10,avance,giro,activacion,ESTADO". Se exigen los 8 campos y que los 7 numeros esten en su rango:
    contactos 1 a 4, avance y giro -100 a +100, activacion 0 o 1. El estado (texto) no se valida: se muestra tal cual."""
    try:
        partes = linea.decode('utf-8').strip().split(',')
        if len(partes) != 8:
            return None
        v = [int(x) for x in partes[:7]]
    except (ValueError, UnicodeError):
        return None
    if not all(1 <= c <= 4 for c in v[:4]):
        return None
    if not (-100 <= v[4] <= 100 and -100 <= v[5] <= 100) or v[6] not in (0, 1):
        return None
    estado = partes[7].strip()
    if not estado:
        return None
    return v[0], v[1], v[2], v[3], v[4], v[5], v[6], estado


def sacar_lineas(buf, nuevos):
    """Agrega los bytes recibidos al buffer y devuelve (lineas completas, resto sin terminar). Una linea a medias queda en el resto
    hasta que llegue su salto de linea: asi nunca se interpreta una trama cortada."""
    buf = buf + nuevos
    if len(buf) > 1024:                  # sin saltos de linea no hay trama: se descarta lo viejo
        buf = buf[-256:]
    lineas = buf.split(b"\n")
    return lineas[:-1], lineas[-1]
# --- FIN TRAMAS ---


# Zona de la cara que se pinta por electrodo (dibujo espejado: el oido izquierdo del usuario queda a la derecha de la pantalla)
ZONAS_CARA = {"oido_izq": (80, 22, 6, 8), "oido_der": (42, 22, 6, 8), "cabeza_izq": (64, 16, 14, 24), "cabeza_der": (50, 16, 14, 24)}
CORTO_CARA = {"oido_izq": "OI", "cabeza_izq": "FI", "cabeza_der": "FD", "oido_der": "OD"}   # texto corto: "Err:OI+FD"


def dibujar_cara_espejada(sensor_fallo):
    """sensor_fallo: tupla con los electrodos en falla (por ejemplo ("oido_izq", "cabeza_der")), o un texto ("normal", "DESC")."""
    oled.fill(0)

    # Textos
    oled.text(f"EST:{estado_pi}", 0, 0, 1)
    oled.hline(0, 10, 128, 1)

    # Contorno y ojos
    oled.rect(48, 14, 32, 28, 1)   # cara 6 px mas arriba para no pisar la linea Err (09-11)
    oled.rect(42, 22, 6, 8, 1)
    oled.rect(80, 22, 6, 8, 1)
    oled.pixel(56, 24, 1)
    oled.pixel(71, 24, 1)
    oled.hline(60, 34, 8, 1)

    # --- DIBUJO INVERTIDO (espejo) --- se marcan TODOS los electrodos con falla a la vez (09-11)
    fallas = sensor_fallo if isinstance(sensor_fallo, tuple) else ()
    for f in fallas:
        oled.fill_rect(*ZONAS_CARA[f], 1)

    texto = "+".join(CORTO_CARA[f] for f in fallas) if fallas else sensor_fallo
    oled.text(f"Err:{texto}", 0, 46, 1)
    # Calidad de cada electrodo (donde antes iban los motores), en el orden TP9 FP1 FP2 TP10:
    # "ok" = bien puesto (contacto 1 o 2), "__" = mal puesto (3 o 4), "??" = sin tramas de la Pi
    marcas = ["??" if estado_pi == "SIN ENLACE" else ("ok" if v <= CONTACTO_OK_MAX else "__") for v in (tp9, fp1, fp2, tp10)]
    oled.text(" ".join(marcas), 0, 56, 1)
    oled.text(f"A{activacion}", 112, 56, 1)

    oled.show()


# --- BUCLE PRINCIPAL ---
error_actual = "normal"
resto_uart = b""
tramas_ok = 0
tramas_mal = 0
hueco_max_ms = 0
ultima_mal = b""                  # ultima linea rechazada (para ver en Thonny que llega realmente)
ultima_ok_txt = b""               # ultima linea aceptada
LOG_TRAMAS = True                 # imprime la ultima trama buena y la ultima mala en cada informe
bytes_rx = 0                      # bytes recibidos por la UART desde el ultimo informe (validos o no)
vuelta_max_ms = 0                 # vuelta mas larga del bucle desde el ultimo informe
errores_pantalla = 0              # dibujos de la pantalla que fallaron (I2C)
ultimo_ok_ms = time.ticks_ms()
informe_ms = time.ticks_ms()
firma_dibujo = None
ultimo_dibujo_ms = 0


def refrescar_pantalla(ahora_ms, forzar=False):
    """Redibuja solo si cambio algo visible (estado, falla de sensor, activacion o motores redondeados a 5 %) y como mucho cada 150 ms:
    mandar el cuadro por I2C demora ~30 ms y retrasaria el resto del bucle."""
    global firma_dibujo, ultimo_dibujo_ms, errores_pantalla
    firma = (estado_pi, error_actual, activacion, tp9 <= CONTACTO_OK_MAX, fp1 <= CONTACTO_OK_MAX, fp2 <= CONTACTO_OK_MAX, tp10 <= CONTACTO_OK_MAX)
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
#    Con el cable USB conectado (Thonny) NO se activa: asi detener el programa no reinicia la Pico ni corta Thonny.
#    En la plataforma, sin USB, queda activo. OJO: una vez activo no se puede apagar (enchufar el USB despues no lo desactiva).
USAR_WATCHDOG = True
try:
    _vbus = Pin('WL_GPIO2', Pin.IN)       # Pico W: el sensado de VBUS esta en el chip inalambrico
except Exception:
    _vbus = Pin(24, Pin.IN)               # Pico: GP24 en 1 = hay 5 V en el USB


def usb_conectado():
    try:
        return _vbus.value() == 1
    except Exception:
        return False


WATCHDOG_MS = 500
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
            t0_vuelta = time.ticks_ms()

            # 1. Leer UART: se juntan los bytes disponibles, se separan las lineas completas y se usa la ULTIMA valida
            ultima_trama = None
            n = uart_pico.any()
            if n:
                nuevos = uart_pico.read(n)
                if nuevos:
                    bytes_rx += len(nuevos)
                    lineas, resto_uart = sacar_lineas(resto_uart, nuevos)
                    for linea in lineas:
                        t = parsear(linea)
                        if t is None:
                            tramas_mal += 1
                            ultima_mal = linea[:80]
                        else:
                            tramas_ok += 1
                            ultima_trama = t
                            ultima_ok_txt = linea[:80]

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
                # todos los electrodos con falla a la vez (antes un error tapaba al otro)
                fallas = tuple(n for n, v in (("oido_izq", tp9), ("cabeza_izq", fp1), ("cabeza_der", fp2), ("oido_der", tp10)) if v > CONTACTO_OK_MAX)
                error_actual = fallas if fallas else "normal"

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

            # Vuelta mas larga del bucle (lectura + motores + pantalla, sin la pausa): si se acerca a 500 ms la Pico misma provoca el SIN ENLACE
            vuelta_ms = time.ticks_diff(time.ticks_ms(), t0_vuelta)
            if vuelta_ms > vuelta_max_ms:
                vuelta_max_ms = vuelta_ms

            # Informe cada 2 s por la consola (Thonny): sirve para ver si la Pi manda tramas parejas o con cortes
            if time.ticks_diff(ahora_ms, informe_ms) > 2000:
                print("[UART] bytes:", bytes_rx, "tramas ok:", tramas_ok, "mal:", tramas_mal, "hueco max:", hueco_max_ms, "ms | vuelta max:", vuelta_max_ms,
                      "ms | estado:", estado_pi, "act:", activacion, "| err bucle:", errores_bucle, "pantalla:", errores_pantalla)
                if LOG_TRAMAS:
                    print("   ok :", ultima_ok_txt)
                    print("   mal:", ultima_mal)
                    ultima_ok_txt = b""
                    ultima_mal = b""
                vuelta_max_ms = 0
                bytes_rx = 0
                tramas_ok = 0
                tramas_mal = 0
                hueco_max_ms = 0
                informe_ms = ahora_ms

            # Pausa minúscula
            time.sleep(0.01)

            if wdt is not None:
                wdt.feed()
            elif USAR_WATCHDOG and tramas_ok > 0 and not usb_conectado():
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

