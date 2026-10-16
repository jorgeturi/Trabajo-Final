import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
    
def analizar_senales_eeg(ruta_csv):
    # 1. Cargar el dataset
    df = pd.read_csv(ruta_csv)
    canales = ['TP9', 'FP1', 'FP2', 'TP10']
    titulo = "yo perturbado cejas 14-10"
    
    # 2. Calcular e imprimir la frecuencia de muestreo
    dt_promedio = df['Timestamp'].diff().mean()
    frecuencia_hz = 1 / dt_promedio if dt_promedio > 0 else 0
    
    print("==================================================")
    print(f"Frecuencia de Muestreo estimada: {frecuencia_hz:.2f} Hz")
    print("==================================================\n")
    
    # 3. Separar los datos
    trigger_df = df[df['Trigger'] == 1].copy()
    
    # 4. Identificar cada evento de trigger individualmente
    df['Inicio_Evento'] = (df['Trigger'].diff() == 1)
    df['ID_Evento'] = df['Inicio_Evento'].cumsum()
    trigger_df['ID_Evento'] = df.loc[df['Trigger'] == 1, 'ID_Evento']
    
    # 5. Calcular estadísticas por canal
    max_por_evento = trigger_df.groupby('ID_Evento')[canales].max()
    min_por_evento = trigger_df.groupby('ID_Evento')[canales].min()
    
    promedio_maximos = max_por_evento.mean()
    promedio_minimos = min_por_evento.mean()
    promedio_general = trigger_df[canales].mean()
    
    # Resetear el índice para concatenar visualmente las muestras
    trigger_df = trigger_df.reset_index(drop=True)
    
    # Encontrar los índices de los picos máximos para graficarlos
    indices_picos = trigger_df.groupby('ID_Evento')[canales].idxmax()
    
    # 6. Generar los gráficos
    # Aumentamos el ancho (figsize de 10 a 12) para dar lugar al cuadro de texto de la leyenda
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), dpi=300, sharex=True)
    fig.suptitle(titulo, fontsize=14, y=0.98)
    
    colores = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']
    
    for i, canal in enumerate(canales):
        ax = axes[i]
        
        # Armar el texto multilínea con las estadísticas para la leyenda
        texto_leyenda = (f"{canal}\n"
                         f"Prom: {promedio_general[canal]:.1f}\n"
                         f"Mín (prom): {promedio_minimos[canal]:.1f}\n"
                         f"Máx (prom): {promedio_maximos[canal]:.1f}")
        
        # Graficar la señal con el nuevo texto en el label
        ax.plot(trigger_df.index, trigger_df[canal], label=texto_leyenda, color=colores[i], linewidth=1.2)
        
        # Marcar los máximos
        idx_maximos = indices_picos[canal].dropna()
        val_maximos = trigger_df.loc[idx_maximos, canal]
        ax.plot(idx_maximos, val_maximos, 'ro', markersize=6, zorder=5)
        
        ax.set_ylabel('Amplitud')
        ax.grid(True, linestyle='--', alpha=0.5)
        
        # Ubicar la leyenda a la derecha del gráfico, centrada verticalmente
        # bbox_to_anchor=(1.02, 0.5) empuja la caja justo afuera del área de ploteo
        ax.legend(loc='center left', bbox_to_anchor=(1.02, 0.5), fontsize=9, framealpha=0.9)

    axes[-1].set_xlabel('Muestras Concatenadas (Eventos)')
    
    # Ajustar márgenes asegurando que haya espacio a la derecha para las leyendas
    plt.tight_layout()
    
    nombre_salida = 'grafico_triggers_concatenados.png'
    plt.savefig(nombre_salida, bbox_inches='tight')
    print(f"Gráfico con estadísticas incrustadas guardado como '{nombre_salida}'")
    
    plt.show()

# Ejecución
analizar_senales_eeg('dataset_levantar_cejas_yo_perturbado_14-10_1791998493.csv')