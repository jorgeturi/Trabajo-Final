import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy import signal

def analizar_senales_eeg_filtradas(ruta_csv):
    # 1. Cargar el dataset
    df = pd.read_csv(ruta_csv)
    canales = ['TP9', 'FP1', 'FP2', 'TP10']
    titulo = "yo perturbado 2 parp 14-10"+" (Señal Filtrada)"
    
    # 2. Calcular e imprimir la frecuencia de muestreo real
    dt_promedio = df['Timestamp'].diff().mean()
    frecuencia_hz = 1 / dt_promedio if dt_promedio > 0 else 220.0
    
    print("==================================================")
    print(f"Frecuencia de Muestreo estimada: {frecuencia_hz:.2f} Hz")
    print("==================================================\n")
    
    # 3. APLICAR FILTROS (Del segundo script)
    lowcut = 1.5   # Cortar latidos y parpadeos lentos
    highcut = 40.0 # Cortar altas frecuencias (puedes bajar a 20.0 si lo prefieres)
    nyq = frecuencia_hz / 2.0
    
    # Crear filtro Butterworth pasa-bandas
    b_bp, a_bp = signal.butter(4, [lowcut / nyq, highcut / nyq], btype='band')
    
    canales_filtrados = []
    for ch in canales:
        # Centrar la señal (quitar offset DC)
        señal_centrada = df[ch] - df[ch].mean()
        # Aplicar el filtro
        nombre_fil = ch + '_fil'
        df[nombre_fil] = signal.filtfilt(b_bp, a_bp, señal_centrada)
        canales_filtrados.append(nombre_fil)

    # 4. Separar los datos (solo donde hay Trigger)
    trigger_df = df[df['Trigger'] == 1].copy()
    
    # 5. Identificar cada evento de trigger individualmente
    df['Inicio_Evento'] = (df['Trigger'].diff() == 1)
    df['ID_Evento'] = df['Inicio_Evento'].cumsum()
    trigger_df['ID_Evento'] = df.loc[df['Trigger'] == 1, 'ID_Evento']
    
    # 6. Calcular estadísticas por canal SOBRE LA SEÑAL FILTRADA
    max_por_evento = trigger_df.groupby('ID_Evento')[canales_filtrados].max()
    min_por_evento = trigger_df.groupby('ID_Evento')[canales_filtrados].min()
    
    promedio_maximos = max_por_evento.mean()
    promedio_minimos = min_por_evento.mean()
    promedio_general = trigger_df[canales_filtrados].mean()
    
    # Resetear el índice para concatenar visualmente las muestras
    trigger_df = trigger_df.reset_index(drop=True)
    
    # Encontrar los índices de los picos máximos para graficarlos (en la señal filtrada)
    indices_picos = trigger_df.groupby('ID_Evento')[canales_filtrados].idxmax()
    
    # 7. Generar los gráficos
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), dpi=300, sharex=True)
    fig.suptitle(titulo, fontsize=14, y=0.98)
    
    colores = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']
    
    for i, (canal_orig, canal_fil) in enumerate(zip(canales, canales_filtrados)):
        ax = axes[i]
        
        # Armar el texto multilínea con las estadísticas para la leyenda
        texto_leyenda = (f"{canal_orig} (Filtrado)\n"
                         f"Prom: {promedio_general[canal_fil]:.1f} µV\n"
                         f"Mín (prom): {promedio_minimos[canal_fil]:.1f} µV\n"
                         f"Máx (prom): {promedio_maximos[canal_fil]:.1f} µV")
        
        # Graficar la señal filtrada
        ax.plot(trigger_df.index, trigger_df[canal_fil], label=texto_leyenda, color=colores[i], linewidth=1.2)
        
        # Marcar los máximos en la señal filtrada
        idx_maximos = indices_picos[canal_fil].dropna()
        val_maximos = trigger_df.loc[idx_maximos, canal_fil]
        ax.plot(idx_maximos, val_maximos, 'ro', markersize=6, zorder=5)
        
        ax.set_ylabel('Amplitud (µV)')
        ax.grid(True, linestyle='--', alpha=0.5)
        
        # Ubicar la leyenda a la derecha del gráfico
        ax.legend(loc='center left', bbox_to_anchor=(1.02, 0.5), fontsize=9, framealpha=0.9)

    axes[-1].set_xlabel('Muestras Concatenadas (Eventos)')
    
    # Ajustar márgenes
    plt.tight_layout()
    
    nombre_salida = 'grafico_triggers_filtrados_concatenados.png'
    plt.savefig(nombre_salida, bbox_inches='tight')
    print(f"Gráfico con estadísticas incrustadas guardado como '{nombre_salida}'")
    
    plt.show()

# Ejecución (reemplaza el string por tu archivo más reciente si lo necesitas automático)
analizar_senales_eeg_filtradas('dataset_dosparpadeos_yo_perturbado_14-10_1791998706.csv')