import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, iirnotch
import os

def aplicar_filtros(data, fs):
    nyq = 0.5 * fs 
    señal = data
    f0 = 50.0  
    if f0 < nyq:
        b_notch, a_notch = iirnotch(f0, 20.0, fs)
        señal = filtfilt(b_notch, a_notch, señal)
        
    lowcut = 1.0
    highcut = min(40.0, nyq - 2.0) 
    b_band, a_band = butter(4, [lowcut/nyq, highcut/nyq], btype='band')
    return filtfilt(b_band, a_band, señal)

def graficar_gestos_concatenados(archivos_csv):
    canales = ['TP9', 'FP1', 'FP2', 'TP10']
    fs = 220.0

    for archivo in archivos_csv:
        df = pd.read_csv(archivo)
        nombre_base = os.path.basename(archivo)
        
        # 1. Aplicar filtros a toda la señal para evitar artefactos de borde
        for ch in canales:
            señal_centrada = df[ch].values - np.mean(df[ch].values)
            df[f'{ch}_Filtrada'] = aplicar_filtros(señal_centrada, fs)
            
        # 2. Identificar bloques continuos donde Trigger == 1
        # Esto crea un ID único para cada vez que se presiona y suelta el botón
        df['Bloque_ID'] = (df['Trigger'].diff() != 0).cumsum()
        bloques_gestos = df[df['Trigger'] == 1]
        
        if bloques_gestos.empty:
            print(f"[{nombre_base}] No se encontraron gestos (Trigger=1).")
            continue
            
        # 3. Extraer y concatenar los segmentos
        segmentos_concatenados = {ch: [] for ch in canales}
        limites_separacion = [] 
        muestras_acumuladas = 0
        
        for _, bloque in bloques_gestos.groupby('Bloque_ID'):
            largo_bloque = len(bloque)
            muestras_acumuladas += largo_bloque
            limites_separacion.append(muestras_acumuladas)
            
            for ch in canales:
                segmentos_concatenados[ch].extend(bloque[f'{ch}_Filtrada'].values)
                
        # 4. Crear un nuevo eje de tiempo artificial continuo
        tiempo_total = np.arange(muestras_acumuladas) / fs
        
        # 5. Graficar los 4 canales
        fig, axs = plt.subplots(4, 1, figsize=(15, 10), sharex=True)
        fig.suptitle(f'Gestos Concatenados (Señal Filtrada) - {nombre_base}')
        
        for i, ch in enumerate(canales):
            axs[i].plot(tiempo_total, segmentos_concatenados[ch], color='darkblue', linewidth=1.5)
            
            # Dibujar las líneas rojas que separan cada bloque de flag
            for limite in limites_separacion[:-1]: 
                axs[i].axvline(x=limite / fs, color='red', linestyle='--', alpha=0.8)
                
            axs[i].set_ylabel(f'{ch}\n(µV)')
            axs[i].grid(True, alpha=0.3)
            
        axs[-1].set_xlabel('Tiempo acumulado de actividad (segundos)')
        plt.tight_layout()
        plt.show()

# --- EJECUCIÓN ---
lista_datasets = [
    'dataset_levantar cejas pa 30-9_1790789354.csv', 
        'dataset_levantar cejas yani 2-10_1790951036.csv'
] 

graficar_gestos_concatenados(lista_datasets)