# serial_manager.py
import serial

class PuertoSerialPico:
    def __init__(self, puerto='/dev/serial0', baudrate=115200):
        try:
            self.conexion = serial.Serial(puerto, baudrate, write_timeout=0)
            print(f"[*] Conexion UART establecida en {puerto}")
        except Exception as e:
            self.conexion = None
            print(f"[!] Advertencia: No se pudo abrir UART ({e})")

    def enviar_estado(self, tp9, fp1, fp2, tp10, pwm1, pwm2):
        """Arma el texto con los 6 números y lo dispara por el cable"""
        if self.conexion is not None:
            # Formato exacto que espera la Pico: "TP9,FP1,FP2,TP10,M1,M2\n"
            trama = f"{tp9},{fp1},{fp2},{tp10},{int(pwm1)},{int(pwm2)}\n"
            
            try:
                self.conexion.write(trama.encode('utf-8'))
            except Exception:
                pass # Si el cable falla momentáneamente, no bloqueamos el sistema

    def cerrar(self):
        if self.conexion is not None:
            self.conexion.close()