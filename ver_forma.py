import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy import signal

def analizar_ventanas_mejorado(ruta_csv):
    # 1. Cargar el dataset y calcular frecuencia
    df = pd.read_csv(ruta_csv)
    canales = ['TP9', 'FP1', 'FP2', 'TP10']
    
    dt_promedio = df['Timestamp'].diff().mean()
    frecuencia_hz = 1 / dt_promedio if dt_promedio > 0 else 220.0
    
    # 2. Filtrar señal completa (1.5 a 40 Hz)
    nyq = frecuencia_hz / 2.0
    b_bp, a_bp = signal.butter(4, [1.5 / nyq, 40.0 / nyq], btype='band')
    
    canales_filtrados = []
    for ch in canales:
        señal_centrada = df[ch] - df[ch].mean()
        nombre_fil = ch + '_fil'
        df[nombre_fil] = signal.filtfilt(b_bp, a_bp, señal_centrada)
        canales_filtrados.append(nombre_fil)

    # 3. Identificar eventos (Triggers)
    df['Inicio_Evento'] = (df['Trigger'].diff() == 1)
    df['ID_Evento'] = df['Inicio_Evento'].cumsum()
    df.loc[df['Trigger'] == 0, 'ID_Evento'] = 0 
    
    eventos = df[df['ID_Evento'] > 0]['ID_Evento'].unique()
    
    # 4. Extraer ventanas (100 atrás, 100 adelante) alineadas al pico
    muestras_atras = 100
    muestras_adelante = 100
    canal_referencia = 'FP1_fil'
    
    ventanas = []
    info_eventos = []

    for ev in eventos:
        datos_trigger = df[df['ID_Evento'] == ev]
        idx_pico = datos_trigger[canal_referencia].abs().idxmax()
        
        inicio = idx_pico - muestras_atras
        fin = idx_pico + muestras_adelante
        
        if inicio >= 0 and fin < len(df):
            ventana = df.iloc[inicio : fin + 1].copy()
            ventana['Muestra_Relativa'] = np.arange(-muestras_atras, muestras_adelante + 1)
            p2p = ventana[canal_referencia].max() - ventana[canal_referencia].min()
            
            ventanas.append((ev, p2p, ventana))
            info_eventos.append({'ID': ev, 'P2P': p2p})

    if not ventanas:
        print("No se encontraron ventanas válidas.")
        return

    # 5. Encontrar el evento principal
    df_info = pd.DataFrame(info_eventos)
    id_max_p2p = df_info.loc[df_info['P2P'].idxmax(), 'ID']
    
    # Extraer la ventana ganadora para usarla de referencia visual
    ventana_ganadora = next(v for v in ventanas if v[0] == id_max_p2p)[2]

    # 6. Graficar con encuadre inteligente
    fig, axes = plt.subplots(4, 1, figsize=(10, 8), dpi=300, sharex=True)
    fig.suptitle("Eventos Alineados | Resaltado: Mayor Pico a Pico", fontsize=12, y=0.98)
    
    colores = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']

    for i, canal_fil in enumerate(canales_filtrados):
        ax = axes[i]
        
        # Calcular los límites del eje Y basados SOLO en la señal principal (+20% de margen)
        y_max = ventana_ganadora[canal_fil].max()
        y_min = ventana_ganadora[canal_fil].min()
        rango = y_max - y_min
        ax.set_ylim(y_min - (rango * 0.2), y_max + (rango * 0.2))
        
        for ev, p2p, vent in ventanas:
            x = vent['Muestra_Relativa']
            y = vent[canal_fil]
            
            if ev == id_max_p2p:
                ax.plot(x, y, color=colores[i], linewidth=2.0, zorder=10, label=f'Mayor P2P')
            else:
                # Fondo más transparente (alpha=0.15) para que no sea un bloque de tinta
                ax.plot(x, y, color='gray', alpha=0.15, linewidth=0.8)

        ax.axvline(0, color='black', linestyle=':', alpha=0.7)
        ax.set_ylabel(f'{canales[i]}', fontsize=10)
        ax.grid(True, linestyle='--', alpha=0.3)
        
        if i == 0:
            ax.legend(loc='upper right', fontsize=9)

    axes[-1].set_xlabel('Muestras relativas al pico (-100 a +100)', fontsize=10)
    plt.tight_layout()
    
    # Guardar la imagen en alta resolución
    nombre_salida = 'grafico_erp_alineado_limpio.png'
    plt.savefig(nombre_salida, bbox_inches='tight', dpi=300)
    print(f"✅ Gráfico guardado exitosamente como '{nombre_salida}'. ¡Abre este archivo para verlo nítido!")
    
    # Mostrar en pantalla (aunque se vea mal en tu editor, el PNG estará perfecto)
    plt.show()

# Ejecución
analizar_ventanas_mejorado('dataset_levantar cejas yani 2-10_1790951036.csv')