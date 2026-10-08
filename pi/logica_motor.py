import statistics
from collections import deque

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

    def procesar_eventos(self, lote_eventos):
        for evento in lote_eventos:
            if evento["tipo"] == "ACC":
                self.ultimo_x_crudo = -evento["x"] 
                self.ultimo_z_crudo = -evento["z"]
                
                self.ventana_x.append(self.ultimo_x_crudo)
                self.ventana_z.append(self.ultimo_z_crudo)
                
                if len(self.ventana_x) > 2:
                    x_mediana = statistics.median(self.ventana_x)
                    z_mediana = statistics.median(self.ventana_z)
                else:
                    x_mediana = self.ultimo_x_crudo
                    z_mediana = self.ultimo_z_crudo
                
                self.x_filtrado = (self.factor_suavizado * x_mediana) + ((1.0 - self.factor_suavizado) * self.x_filtrado)
                self.z_filtrado = (self.factor_suavizado * z_mediana) + ((1.0 - self.factor_suavizado) * self.z_filtrado)
                
                VALOR_MAXIMO_CABEZA = 800.0 
                
                # YA NO USAMOS abs(). Conservamos el signo (positivo o negativo)
                potencia_x = (self.x_filtrado / VALOR_MAXIMO_CABEZA) * 100.0
                potencia_z = (self.z_filtrado / VALOR_MAXIMO_CABEZA) * 100.0
                
                # Limitamos entre -100 (máxima reversa) y +100 (máximo adelante)
                self.pwm1_actual = max(-100.0, min(100.0, potencia_x))
                self.pwm2_actual = max(-100.0, min(100.0, potencia_z))

    def obtener_estado_completo(self):
        return {
            "pwm1": self.pwm1_actual,
            "pwm2": self.pwm2_actual,
            "x_crudo": self.ultimo_x_crudo,
            "z_crudo": self.ultimo_z_crudo,
            "x_filt": self.x_filtrado,
            "z_filt": self.z_filtrado
        }