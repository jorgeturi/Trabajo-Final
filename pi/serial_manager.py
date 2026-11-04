# serial_manager.py
import serial

class PuertoSerialPico:
    def __init__(self, puerto='/dev/serial0', baudrate=115200):
        self.errores = 0                          # escrituras que fallaron
        try:
            self.conexion = serial.Serial(puerto, baudrate, write_timeout=0)
            print(f"[*] Conexion UART establecida en {puerto}")
        except Exception as e:
            self.conexion = None
            print(f"[!] Advertencia: No se pudo abrir UART ({e})")

    def enviar_estado(self, tp9, fp1, fp2, tp10, pwm1, pwm2, activacion=0, estado=""):
        """Arma el texto con los 7 números (y el estado en texto, si se da) y lo dispara por el cable.
        Devuelve (trama enviada sin el salto de linea, si la escritura salio bien) para poder publicarla y ver que se manda."""
        # Formato que espera la Pico: "TP9,FP1,FP2,TP10,M1,M2,ACTIVACION[,ESTADO]" terminado en salto de linea
        trama = f"{tp9},{fp1},{fp2},{tp10},{int(pwm1)},{int(pwm2)},{int(activacion)}" + (f",{estado}" if estado else "")
        ok = False
        if self.conexion is not None:
            try:
                self.conexion.write((trama + "\n").encode('utf-8'))
                ok = True
            except Exception:
                self.errores += 1   # Si el cable falla momentáneamente, no bloqueamos el sistema (pero se cuenta)
        return trama, ok

    def cerrar(self):
        if self.conexion is not None:
            self.conexion.close()