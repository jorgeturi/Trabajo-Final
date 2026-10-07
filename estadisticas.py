import pandas as pd
import numpy as np

# ==========================================
# 1. PARAMETROS DE CONFIGURACION
# ==========================================
nombre_archivo = "dataset_levantar_cejas_7-10_test_1791390459.csv"
frecuencia_teorica = 220.0
canales = ['TP9', 'FP1', 'FP2', 'TP10']

def analizar_dataset_eeg(ruta_archivo, freq_teorica, lista_canales):
    # ==========================================
    # 2. CARGA DE DATOS
    # ==========================================
    try:
        df = pd.read_csv(ruta_archivo)
    except FileNotFoundError:
        print(f"Error: No se encontro el archivo {ruta_archivo}")
        return

    # ==========================================
    # 3. ANALISIS DE TIEMPOS Y FRECUENCIA
    # ==========================================
    tiempos = df['Timestamp'].values
    
    # Calcular las diferencias de tiempo entre cada muestra consecutiva
    diferencias_tiempo = np.diff(tiempos)
    diferencia_promedio = np.mean(diferencias_tiempo)
    
    # Frecuencia real de muestreo (inversa del periodo promedio)
    frecuencia_real = 1.0 / diferencia_promedio if diferencia_promedio > 0 else 0
    
    duracion_total = tiempos[-1] - tiempos[0]
    muestras_reales = len(df)
    
    # Calculo de perdida de datos
    muestras_esperadas = int(duracion_total * freq_teorica)
    muestras_perdidas = muestras_esperadas - muestras_reales
    porcentaje_perdida = (muestras_perdidas / muestras_esperadas) * 100 if muestras_esperadas > 0 else 0

    print("==========================================")
    print("REPORTE DE MUESTREO Y TIEMPOS")
    print("==========================================")
    print(f"Archivo analizado:    {ruta_archivo}")
    print(f"Duracion total:       {duracion_total:.2f} segundos")
    print(f"Frecuencia teorica:   {freq_teorica} Hz")
    print(f"Frecuencia real:      {frecuencia_real:.2f} Hz")
    print(f"Muestras esperadas:   {muestras_esperadas}")
    print(f"Muestras capturadas:  {muestras_reales}")
    
    if muestras_perdidas > 0:
        print(f"Muestras perdidas:    {muestras_perdidas} ({porcentaje_perdida:.2f}%)")
    else:
        print("Muestras perdidas:    0 (Muestreo optimo o superior al teorico)")

    print("\n==========================================")
    print("ESTADISTICAS DE SEÑAL POR CANAL")
    print("==========================================")

    # ==========================================
    # 4. ANALISIS ESTADISTICO (GESTO VS REPOSO)
    # ==========================================
    # Verificar si existe la columna Trigger
    if 'Trigger' not in df.columns:
        print("La columna 'Trigger' no existe en este dataset.")
        return

    mascara_gesto = df['Trigger'] == 1
    mascara_reposo = df['Trigger'] == 0

    df_gesto = df[mascara_gesto]
    df_reposo = df[mascara_reposo]

    print(f"Muestras en reposo:   {len(df_reposo)}")
    print(f"Muestras en gesto:    {len(df_gesto)}\n")

    # Imprimir encabezado de la tabla
    print(f"{'Canal':<10} | {'Estado':<10} | {'Media (uV)':<15} | {'Varianza':<15}")
    print("-" * 57)

    for canal in lista_canales:
        if canal not in df.columns:
            print(f"{canal:<10} | NO ENCONTRADO EN EL CSV")
            continue

        # Estadisticas en reposo
        if not df_reposo.empty:
            media_reposo = df_reposo[canal].mean()
            varianza_reposo = df_reposo[canal].var()
        else:
            media_reposo = 0.0
            varianza_reposo = 0.0

        # Estadisticas en gesto
        if not df_gesto.empty:
            media_gesto = df_gesto[canal].mean()
            varianza_gesto = df_gesto[canal].var()
        else:
            media_gesto = 0.0
            varianza_gesto = 0.0

        # Formateo de impresion
        print(f"{canal:<10} | {'Reposo':<10} | {media_reposo:<15.4f} | {varianza_reposo:<15.4f}")
        print(f"{'':<10} | {'Gesto':<10} | {media_gesto:<15.4f} | {varianza_gesto:<15.4f}")
        print("-" * 57)

# ==========================================
# 5. EJECUCION
# ==========================================
if __name__ == "__main__":
    analizar_dataset_eeg(nombre_archivo, frecuencia_teorica, canales)