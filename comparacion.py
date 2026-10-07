import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, iirnotch, find_peaks
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

def obtener_morfologia_multipeak(archivo, canales, fs, ventana_pre, ventana_post):
    df = pd.read_csv(archivo)
    
    # 1. Filtrar canales conservando la polaridad original
    for ch in canales:
        señal_centrada = df[ch].values - np.mean(df[ch].values)
        df[f'{ch}_Filtrada'] = aplicar_filtros(señal_centrada, fs)
        
    # 2. RADAR: Crear señal de búsqueda usando el valor absoluto de FP1
    # Solo buscamos dentro de las zonas donde el Trigger es 1
    trigger_mask = df['Trigger'].values == 1
    señal_radar = np.where(trigger_mask, np.abs(df['FP1_Filtrada'].values), 0)
    
    # Detectar los picos en el radar (Ajusta el umbral si es necesario)
    umbral_uv = 150        # Amplitud mínima (hacia arriba o hacia abajo) para ser gesto
    distancia_seg = 1.0    # Tiempo mínimo entre un gesto y otro
    
    picos_indices, _ = find_peaks(señal_radar, height=umbral_uv, distance=int(distancia_seg * fs))
    print(f"[{os.path.basename(archivo)}] - Gestos detectados automáticamente: {len(picos_indices)}")
    
    if len(picos_indices) == 0:
        return None

    # 3. EXTRACCIÓN: Usar los índices del radar para cortar la señal ORIGINAL
    samples_pre = int(ventana_pre * fs)
    samples_post = int(ventana_post * fs)
    
    ventanas_crudas = {ch: [] for ch in canales}
    
    for idx in picos_indices:
        inicio = idx - samples_pre
        fin = idx + samples_post
        
        if inicio >= 0 and fin < len(df):
            for ch in canales:
                # Extraemos la señal filtrada normal (con negativos), no el radar
                ventanas_crudas[ch].append(df[f'{ch}_Filtrada'].iloc[inicio:fin].values)

    # 4. Promediar la morfología real para esta persona
    patron_promedio = {}
    for ch in canales:
        patron_promedio[ch] = np.mean(ventanas_crudas[ch], axis=0)
        
    return patron_promedio

def comparar_sujetos_morfologia(lista_datasets, ventana_pre=0.5, ventana_post=1.5):
    canales = ['TP9', 'FP1', 'FP2', 'TP10']
    fs = 220.0
    
    largo_esperado = int((ventana_pre + ventana_post) * fs)
    eje_tiempo = np.linspace(-ventana_pre, ventana_post, largo_esperado)
    
    coleccion_patrones = {}
    print("--- Extrayendo morfología real por sujeto ---")
    
    for archivo in lista_datasets:
        nombre_base = os.path.basename(archivo).replace('.csv', '')
        patron = obtener_morfologia_multipeak(archivo, canales, fs, ventana_pre, ventana_post)
        
        if patron is not None:
            coleccion_patrones[nombre_base] = patron
            # Exportar la plantilla real para uso futuro
            df_export = pd.DataFrame(patron)
            df_export.insert(0, 'Tiempo_Relativo', eje_tiempo)
            df_export.to_csv(f"morfologia_real_{nombre_base}.csv", index=False)
            
    if not coleccion_patrones:
        print("No se extrajeron patrones. Revisa el umbral_uv.")
        return

    # ==========================================
    # GRÁFICA COMPARATIVA INTER-SUJETO
    # ==========================================
    fig, axs = plt.subplots(2, 2, figsize=(16, 10), sharex=True, sharey=True)
    fig.suptitle('Comparación Morfológica Real (Polaridad Intacta)')
    axs = axs.flatten()
    
    colores = plt.cm.tab10(np.linspace(0, 1, len(coleccion_patrones)))
    
    for idx_ch, ch in enumerate(canales):
        for idx_sujeto, (nombre, patron) in enumerate(coleccion_patrones.items()):
            etiqueta = nombre.split('_')[1] if '_' in nombre else nombre
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