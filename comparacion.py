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
        b_notch, a_notch = iirnotch(f0, 30.0, fs)
        señal = filtfilt(b_notch, a_notch, señal)
        
    lowcut = 1.0
    highcut = min(40.0, nyq - 2.0) 
    b_band, a_band = butter(4, [lowcut/nyq, highcut/nyq], btype='band')
    return filtfilt(b_band, a_band, señal)

def obtener_morfologia_multipeak(archivo, canales, ventana_pre, ventana_post):
    df = pd.read_csv(archivo)
    
    # 1. Cálculo dinámico de la frecuencia de muestreo
    col_tiempo = next((col for col in df.columns if 'time' in col.lower()), None)
    
    if col_tiempo is not None:
        diffs = df[col_tiempo].diff().dropna()
        promedio_diff = diffs.mean()
        if promedio_diff > 1:  
            fs_real = 1000.0 / promedio_diff 
        else:
            fs_real = 1.0 / promedio_diff    
    else:
        print(f"Advertencia: No se encontró columna de tiempo en {archivo}. Asumiendo 220 Hz.")
        fs_real = 220.0
        
    print(f"[{os.path.basename(archivo)}] - Frecuencia real calculada: {fs_real:.2f} Hz")
    
    # 2. Filtrar canales
    for ch in canales:
        señal_centrada = df[ch].values - np.mean(df[ch].values)
        df[f'{ch}_Filtrada'] = aplicar_filtros(señal_centrada, fs_real)
        
    # 3. Extraer eventos del Trigger
    trigger = df['Trigger'].fillna(0).values
    trigger_diff = np.diff(trigger, prepend=0)
    
    inicios = np.where(trigger_diff >= 1)[0]
    fines = np.where(trigger_diff <= -1)[0]
    
    if len(inicios) > len(fines):
        fines = np.append(fines, len(df)-1)
        
    picos_indices = []
    
    # 4. Encontrar el extremo más significativo por evento (sin usar abs)
    for i in range(len(inicios)):
        inicio_gesto = inicios[i]
        fin_gesto = fines[i]
        
        segmento_fp1 = df['FP1_Filtrada'].values[inicio_gesto:fin_gesto]
        
        if len(segmento_fp1) > 0:
            # Buscar el índice del pico más alto y del valle más profundo
            idx_max_local = np.argmax(segmento_fp1)
            idx_min_local = np.argmin(segmento_fp1)
            
            # Obtener sus valores reales
            val_max = segmento_fp1[idx_max_local]
            val_min = segmento_fp1[idx_min_local]
            
            # Comparar magnitudes invirtiendo el signo del valle
            if val_max >= -val_min:
                idx_extremo = idx_max_local
            else:
                idx_extremo = idx_min_local
            
            idx_max_global = inicio_gesto + idx_extremo
            picos_indices.append(idx_max_global)
            
    print(f"[{os.path.basename(archivo)}] - Picos extremos alineados: {len(picos_indices)}")
    
    if len(picos_indices) == 0:
        return None, fs_real

    # 5. Cortar ventanas centradas en el extremo
    samples_pre = int(ventana_pre * fs_real)
    samples_post = int(ventana_post * fs_real)
    
    ventanas_crudas = {ch: [] for ch in canales}
    
    for idx in picos_indices:
        inicio = idx - samples_pre
        fin = idx + samples_post
        
        if inicio >= 0 and fin < len(df):
            for ch in canales:
                ventanas_crudas[ch].append(df[f'{ch}_Filtrada'].iloc[inicio:fin].values)

    # 6. Promediar la morfología
    patron_promedio = {}
    for ch in canales:
        patron_promedio[ch] = np.mean(ventanas_crudas[ch], axis=0)
        
    return patron_promedio, fs_real

def comparar_sujetos_morfologia(lista_datasets, ventana_pre=0.5, ventana_post=0.5):
    canales = ['TP9', 'FP1', 'FP2', 'TP10']
    
    coleccion_patrones = {}
    coleccion_tiempos = {}
    print("--- Extrayendo morfología real por sujeto ---")
    
    for archivo in lista_datasets:
        nombre_base = os.path.basename(archivo).replace('.csv', '')
        patron, fs_real = obtener_morfologia_multipeak(archivo, canales, ventana_pre, ventana_post)
        
        if patron is not None:
            largo_esperado = len(patron[canales[0]])
            eje_tiempo_local = np.linspace(-ventana_pre, ventana_post, largo_esperado)
            
            coleccion_patrones[nombre_base] = patron
            coleccion_tiempos[nombre_base] = eje_tiempo_local
            
            df_export = pd.DataFrame(patron)
            df_export.insert(0, 'Tiempo_Relativo', eje_tiempo_local)
            df_export.to_csv(f"morfologia_real_{nombre_base}.csv", index=False)
            
    if not coleccion_patrones:
        print("No se extrajeron patrones. Revisa que el dataset tenga eventos de Trigger.")
        return

    # ==========================================
    # GRÁFICA COMPARATIVA INTER-SUJETO
    # ==========================================
    fig, axs = plt.subplots(2, 2, figsize=(16, 10), sharex=True, sharey=False)
    fig.suptitle('Comparación Morfológica Real (Alineada por Extremo Máximo)')
    axs = axs.flatten()
    
    colores = plt.cm.tab10(np.linspace(0, 1, len(coleccion_patrones)))
    
    for idx_ch, ch in enumerate(canales):
        for idx_sujeto, (nombre, patron) in enumerate(coleccion_patrones.items()):
            etiqueta = nombre.split('_')[1] if '_' in nombre else nombre
            eje_tiempo = coleccion_tiempos[nombre]
            
            axs[idx_ch].plot(eje_tiempo, patron[ch], color=colores[idx_sujeto], linewidth=2.5, alpha=0.8, label=etiqueta)
            
        axs[idx_ch].axvline(x=0, color='red', linestyle='--', alpha=0.6, label='Pico de Detección')
        axs[idx_ch].axhline(y=0, color='black', linestyle='-', linewidth=0.8, alpha=0.5)
        
        axs[idx_ch].set_title(f'Canal {ch}')
        axs[idx_ch].grid(True, alpha=0.3)
        if idx_ch > 1: axs[idx_ch].set_xlabel('Tiempo relativo al pico (s)')
        if idx_ch % 2 == 0: axs[idx_ch].set_ylabel('Amplitud (µV)')

    axs[0].legend(loc="upper left", bbox_to_anchor=(1, 1))
    plt.tight_layout()
    plt.show()

# --- EJECUCIÓN ---
lista_datasets = [
    'dataset_levantar cejas 29-9_1790704494.csv', 
    'dataset_levantar cejas 28-9_1790619925.csv',
    'dataset_levantar cejas 2-10_1790963207.csv'
] 

comparar_sujetos_morfologia(lista_datasets)