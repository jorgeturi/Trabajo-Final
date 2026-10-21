import statistics
from collections import deque

import config

class ControladorMotores:
    def __init__(self):
        self.pwm1_actual = 0
        self.pwm2_actual = 0
        self.x_filtrado = 0.0
        self.z_filtrado = 0.0
        self.ultimo_x_crudo = 0.0
        self.ultimo_z_crudo = 0.0
        self.factor_suavizado = 0.05 
        
        self.ventana_x = deque(maxlen=35)
        self.ventana_z = deque(maxlen=35)

        # Verificación del eje Y: hasta llenar la ventana (y mientras esté al revés) los motores salen en 0
        self.ventana_y = deque(maxlen=config.ACC_VERIF_MUESTRAS)
        self.vincha_ok = False

    def _zona_muerta(self, valor, zona, maximo):
        """Dentro de la zona -> 0. Fuera, se reescala desde 0 para que no haya salto."""
        if abs(valor) <= zona:
            return 0.0
        potencia = (abs(valor) - zona) / (maximo - zona) * 100.0
        return potencia if valor > 0 else -potencia

    def forzar_parada(self):
        """Reinicia los valores y limpia el historial si hay desconexión."""
        self.pwm1_actual = 0
        self.pwm2_actual = 0
        self.x_filtrado = 0.0
        self.z_filtrado = 0.0
        self.ventana_x.clear()
        self.ventana_z.clear()
        self.ventana_y.clear()
        self.vincha_ok = False

    def procesar_eventos(self, lote_eventos):
        for evento in lote_eventos:
            if evento["tipo"] == "ACC":
                self.ultimo_x_crudo = -evento["x"] 
                self.ultimo_z_crudo = -evento["z"]
                
                self.ventana_x.append(self.ultimo_x_crudo)
                self.ventana_z.append(self.ultimo_z_crudo)

                # Y negativo (media de la ventana) = vincha al revés
                self.ventana_y.append(evento["y"])
                self.vincha_ok = (len(self.ventana_y) == self.ventana_y.maxlen and
                                  statistics.fmean(self.ventana_y) >= config.ACC_Y_UMBRAL_INVERTIDA)

                if len(self.ventana_x) > 2:
                    x_mediana = statistics.median(self.ventana_x)
                    z_mediana = statistics.median(self.ventana_z)
                else:
                    x_mediana = self.ultimo_x_crudo
                    z_mediana = self.ultimo_z_crudo
                
                self.x_filtrado = (self.factor_suavizado * x_mediana) + ((1.0 - self.factor_suavizado) * self.x_filtrado)
                self.z_filtrado = (self.factor_suavizado * z_mediana) + ((1.0 - self.factor_suavizado) * self.z_filtrado)
                
                VALOR_MAXIMO_CABEZA = 600.0 
                
                # YA NO USAMOS abs(). Conservamos el signo (positivo o negativo)
                potencia_x = self._zona_muerta(self.x_filtrado, config.ZONA_MUERTA_X, VALOR_MAXIMO_CABEZA)
                potencia_z = self._zona_muerta(self.z_filtrado, config.ZONA_MUERTA_Z, VALOR_MAXIMO_CABEZA)
                
                # Limitamos entre -100 (máxima reversa) y +100 (máximo adelante)
                self.pwm1_actual = max(-100.0, min(100.0, potencia_x))
                self.pwm2_actual = max(-100.0, min(100.0, potencia_z))

    def obtener_estado_completo(self):
        return {
            "pwm1": self.pwm1_actual if self.vincha_ok else 0,
            "pwm2": self.pwm2_actual if self.vincha_ok else 0,
            "x_crudo": self.ultimo_x_crudo,
            "z_crudo": self.ultimo_z_crudo,
            "x_filt": self.x_filtrado,
            "z_filt": self.z_filtrado
        }