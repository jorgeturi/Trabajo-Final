class ControladorMotores:
    def __init__(self):
        self.pwm1_actual = 0
        self.pwm2_actual = 0
        
        # Variables para el filtro de suavizado
        self.x_filtrado = 0.0
        self.z_filtrado = 0.0
        
        # Factor del filtro (0.0 a 1.0). 
        # 0.1 = Muy suave/lento (ignora movimientos rapidos)
        # 0.9 = Muy reactivo/nervioso (sigue todo el movimiento)
        self.factor_suavizado = 0.15 

    def procesar_eventos(self, lote_eventos):
        for evento in lote_eventos:
            if evento["tipo"] == "ACC":
                # 1. Capturamos los datos crudos y los invertimos según tu setup
                x_crudo = -evento["x"] 
                z_crudo = -evento["z"]
                
                # 2. Aplicamos el Filtro Exponencial (Filtro Pasa Bajos)
                self.x_filtrado = (self.factor_suavizado * x_crudo) + ((1.0 - self.factor_suavizado) * self.x_filtrado)
                self.z_filtrado = (self.factor_suavizado * z_crudo) + ((1.0 - self.factor_suavizado) * self.z_filtrado)
                
                # 3. Mapear los valores filtrados a potencia PWM (0 a 100)
                # NOTA: Ajusta estos multiplicadores según los valores máximos que tire tu vincha
                potencia_x = abs(self.x_filtrado * 100) # Ejemplo de mapeo
                potencia_z = abs(self.z_filtrado * 100)
                
                # Limitamos para que nunca pase de 100 ni baje de 0
                self.pwm1_actual = max(0, min(100, potencia_x))
                self.pwm2_actual = max(0, min(100, potencia_z))
                
            

    def obtener_potencias(self):
        return self.pwm1_actual, self.pwm2_actual