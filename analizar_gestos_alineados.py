import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def analizar_patron_alineado_al_pico(ruta_csv, muestras_atras=50, muestras_adelante=100):
    # 1. Cargar el dataset
    df = pd.read_csv(ruta_csv)
    canales = ['TP9', 'FP1', 'FP2', 'TP10']
    titulo = "Eventos alineados por el Pico Máximo de FP1 + Promedio"
    
    # 2. Identificar y separar cada evento de trigger
    df['Inicio_Evento'] = (df['Trigger'].diff() == 1)
    df['ID_Evento'] = df['Inicio_Evento'].cumsum()
    trigger_df = df[df['Trigger'] == 1].copy()
    
    # 3. Calcular Pico a Pico para identificar la repetición con mayor variación en FP1
    max_fp1 = trigger_df.groupby('ID_Evento')['FP1'].max()
    min_fp1 = trigger_df.groupby('ID_Evento')['FP1'].min()
    ptp_fp1 = max_fp1 - min_fp1
    evento_mayor_ptp = ptp_fp1.idxmax()
    
    # Eje X común centrado en el pico (t=0 es el pico máximo)
    x_tiempo = np.arange(-muestras_atras, muestras_adelante + 1)
    
    # Almacenar los recortes alineados para poder graficar y promediar
    segmentos_canales = {canal: [] for canal in canales}
    ids_validos = []
    
    # 4. Alinear por el pico máximo de FP1 de cada evento individual
    eventos = trigger_df.groupby('ID_Evento')
    
    for id_ev, datos in eventos:
        # Encontrar la posición del pico máximo en FP1 para ESTE evento
        idx_pico_local = datos['FP1'].argmax() # Posición relativa dentro del evento
        
        # Recortar 'muestras_atras' y 'muestras_adelante' teniendo como centro el pico
        inicio = idx_pico_local - muestras_atras
        fin = idx_pico_local + muestras_adelante + 1
        
        # Validar que el recorte alrededor del pico no sobrepase los límites del evento
        if inicio >= 0 and fin <= len(datos):
            ids_validos.append(id_ev)
            for canal in canales:
                recorte = datos.iloc[inicio:fin][canal].values
                segmentos_canales[canal].append(recorte)

    # 5. Generar los gráficos
    fig, axes = plt.subplots(4, 1, figsize=(11, 9), dpi=300, sharex=True)
    fig.suptitle(titulo, fontsize=14, y=0.98)
    colores = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']
    
    for i, canal in enumerate(canales):
        ax = axes[i]
        matriz_canal = np.array(segmentos_canales[canal]) # Matriz de (Eventos x Muestras)
        
        # A) Graficar cada intento alineado por el pico
        for k, id_ev in enumerate(ids_validos):
            if id_ev == evento_mayor_ptp:
                # Resaltar en rojo el evento con mayor pico a pico en FP1
                ax.plot(x_tiempo, matriz_canal[k], color='red', linewidth=2.5, zorder=10,
                        label='Mayor Pico a Pico (FP1)' if i == 1 else "")
            else:
                # Los demás eventos en tono suave
                ax.plot(x_tiempo, matriz_canal[k], color=colores[i], alpha=0.3, linewidth=1.0)
                
        # B) Calcular y graficar el PROMEDIO de los eventos alineados por el pico
        if len(matriz_canal) > 0:
            promedio = np.mean(matriz_canal, axis=0)
            ax.plot(x_tiempo, promedio, color='black', linewidth=2.5, linestyle='--',
                    label='Patrón Promedio' if i == 0 else "", zorder=15)

        ax.set_ylabel(canal)
        ax.grid(True, linestyle='--', alpha=0.5)
        
        # Línea en x = 0 (el pico máximo alineado)
        ax.axvline(x=0, color='black', linestyle=':', alpha=0.8)
        
        if i in [0, 1]:
            ax.legend(loc='upper right', fontsize=9)

    axes[-1].set_xlabel('Muestras respecto al Pico Máximo de FP1 (t=0 es el pico)')
    plt.tight_layout()
    
    nombre_salida = 'patron_alineado_pico_fp1.png'
    plt.savefig(nombre_salida, bbox_inches='tight')
    plt.show()

# Ejecución (puedes ajustar cuantas muestras quieres tomar hacia atrás y adelante del pico)
analizar_patron_alineado_al_pico('dataset_levantar_cejas_yo_perturbado_14-10_1791998493.csv', muestras_atras=50, muestras_adelante=100)