# Estados (los define la Pi; acá solo se nombran para mostrarlos)
INICIO, CALIBRACION, REPOSO, MARCHA, EMERGENCIA = 0, 1, 2, 3, 4
NOMBRES = ("INICIO", "CALIBRACION", "REPOSO", "MARCHA", "EMERGENCIA")

# Fallas = máscara de bits (se suman para combinarlas)
OIDO_IZQ, OIDO_DER, CAB_IZQ, CAB_DER = 1, 2, 4, 8
VINCHA = 16            # la Pi avisa "vincha desconectada"
SENSORES = OIDO_IZQ | OIDO_DER | CAB_IZQ | CAB_DER
