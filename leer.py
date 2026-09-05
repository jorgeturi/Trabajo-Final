import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

ARCHIVO_CSV = "dataset_cejas_1788634723.csv" # Reemplaza con tu archivo real

def auditar_banda_ancha(csv_path):
    print("==================================================")
    print(f"🔍 Análisis de Banda Ancha (Pico Máximo vs Ruido Base)")
    print("==================================================\n")
    
    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print("❌ Archivo no encontrado.")
        return

    df_reposo = df[df['Trigger'] == 0]
    df_cejas = df[df['Trigger'] == 1]
    
    canales = ['TP9', 'FP1', 'FP2', 'TP10']
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)
    
    for i, canal in enumerate(canales):
        cols_canal = [c for c in df.columns if c.startswith(canal + "_")]
        freqs = [float(c.split('_')[1].replace('Hz', '')) for c in cols_canal]
        
        # MATEMÁTICA CORREGIDA: 
        # Buscamos el PICO MÁXIMO durante el gesto, no el promedio.
        # Comparamos contra la MEDIANA (el centro del ruido) en reposo.
        pico_cejas = df_cejas[cols_canal].max().values
        base_reposo = df_reposo[cols_canal].median().values
        
        ax = axes[i]
        
        # Ignoramos matemáticamente el ruido de la red eléctrica para la gráfica
        idx_50 = [j for j, f in enumerate(freqs) if 45.0 <= f <= 55.0]
        pico_cejas[idx_50] = base_reposo[idx_50] 
        
        ax.plot(freqs, base_reposo, label='Fondo (Mediana)', color='blue', alpha=0.7)
        ax.plot(freqs, pico_cejas, label='Gesto (Pico Máximo)', color='orange', alpha=0.9)
        
        # Rellenar el diferencial (lo que tu ojo ve como una franja)
        ax.fill_between(freqs, base_reposo, pico_cejas, where=(pico_cejas > base_reposo), color='red', alpha=0.4)
        
        ax.set_title(f'Canal: {canal}')
        
        # Escala logarítmica para poder ver la señal biológica y el ruido a la vez sin que se aplasten
        ax.set_yscale('log')
        ax.set_xlim(1, 100) 
        ax.grid(True, which="both", linestyle='--', alpha=0.4)
        ax.legend(loc='upper right', fontsize=8)

    axes[-1].set_xlabel('Frecuencia (Hz)')
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    auditar_banda_ancha(ARCHIVO_CSV)