import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy import signal
import glob
import os

# ==========================================
# 1. CONFIGURACIÓN DEL ANÁLISIS
# ==========================================
# Busca el último CSV generado o escribe el nombre directo
archivos = glob.glob("dataset_dosparpadeosseguidos_1789859116.csv")
if not archivos:
    print("❌ No se encontraron archivos CSV que comiencen con 'dataset_'.")
    exit()

# Selecciona el archivo más reciente automáticamente
ARCHIVO_CSV = max(archivos, key=os.path.getctime)
FS = 220.0  # Frecuencia de muestreo de Muse v1

CANALES = ['TP9', 'FP1', 'FP2', 'TP10']

# ==========================================
# 2. CARGAR Y REVISAR DATOS
# ==========================================
print(f"📂 Cargando archivo: {ARCHIVO_CSV}")
df = pd.read_csv(ARCHIVO_CSV)

# Ajustar tiempo relativo en segundos (empieza en 0)
df['Tiempo_s'] = df['Timestamp'] - df['Timestamp'].iloc[0]

print("\n--- Vista preliminar de los primeros 5 registros ---")
print(df[['Tiempo_s', 'Trigger', 'Sensores_OK'] + CANALES].head())

# ==========================================
# 3. SELECCIONAR UNA FRANJA DE INTERÉS
# ==========================================
# Podés elegir un rango de tiempo en segundos (ej. del segundo 3 al 8)
TIEMPO_INICIO = 0.0
TIEMPO_FIN = 250.0

# Opcional: Filtrar solo muestras donde los sensores estaban en buen estado (Sensores_OK == 1)
df_franja = df[(df['Tiempo_s'] >= TIEMPO_INICIO) & (df['Tiempo_s'] <= TIEMPO_FIN)].copy()

if df_franja.empty:
    print("⚠️ La franja de tiempo seleccionada no tiene datos. Usando todo el dataset.")
    df_franja = df.copy()

print(f"\n--- Imprimiendo franja temporal ({TIEMPO_INICIO}s a {TIEMPO_FIN}s) ---")
print(f"Total de muestras en franja: {len(df_franja)}")
print(df_franja[['Tiempo_s', 'Trigger', 'Sensores_OK'] + CANALES].iloc[::20]) # Muestra cada 20 filas

# ==========================================
# 4. PLOT 1: DOMINIO DEL TIEMPO (SEÑAL PURA)
# ==========================================
plt.figure(figsize=(12, 6))
for ch in CANALES:
    # Restamos la media para centrar la señal en 0 uV y quitar el offset DC
    señal_centrada = df_franja[ch] - df_franja[ch].mean()
    plt.plot(df_franja['Tiempo_s'], señal_centrada, label=ch)

# Graficar el estado del Trigger como fondo sombreado
trigger_activo = df_franja['Trigger'] == 1
if trigger_activo.any():
    plt.fill_between(df_franja['Tiempo_s'], 
                     plt.ylim()[0], plt.ylim()[1], 
                     where=trigger_activo, 
                     color='red', alpha=0.15, label='Trigger Activo (Gesto)')

plt.title(f"EEG Crudo (Offset DC removido) | Franja: {TIEMPO_INICIO}s a {TIEMPO_FIN}s")
plt.xlabel("Tiempo (segundos)")
plt.ylabel("Amplitud (µV)")
plt.grid(True, linestyle='--', alpha=0.6)
plt.legend(loc='upper right')
plt.tight_layout()

# ==========================================
# 5. PLOT 2: DOMINIO DE LA FRECUENCIA (FFT / PSD)
# ==========================================
plt.figure(figsize=(10, 5))

# Usamos el método de Welch: divide la franja en subventanas de 1 segundo (220 muestras) con 50% de solapamiento
nperseg = int(min(len(df_franja), FS * 1.0))

for ch in CANALES:
    señal = df_franja[ch].values
    señal = señal - np.mean(señal)  # Quitar componente continua (0 Hz)
    
    # Estimación espectral por ventanas
    freqs, psd = signal.welch(señal, fs=FS, window='hann', nperseg=nperseg, noverlap=nperseg // 2)
    
    # Filtramos para visualizar el rango útil de EEG (1 a 60 Hz)
    idx = (freqs >= 1.0) & (freqs <= 60.0)
    plt.plot(freqs[idx], psd[idx], label=ch, linewidth=1.5)

# Sombrear bandas de interés visual
plt.axvspan(8, 13, color='green', alpha=0.1, label='Banda Alfa (8-13 Hz)')
plt.axvspan(13, 30, color='orange', alpha=0.1, label='Banda Beta (13-30 Hz)')

plt.title("Densidad Espectral de Potencia (FFT ventaneada con Welch)")
plt.xlabel("Frecuencia (Hz)")
plt.ylabel("Potencia (µV² / Hz)")
plt.yscale('log')  # Escala logarítmica para apreciar picos pequeños
plt.grid(True, which='both', linestyle='--', alpha=0.5)
plt.legend()
plt.tight_layout()

# Mostrar ambos gráficos
plt.show()




# ==========================================
# 6. FILTRADO: Pasa-bandas ajustado (2.0 Hz a 20.0 Hz)
# ==========================================
lowcut = 1.5   # Sube de 1Hz a 2Hz para cortar latidos (~1.2Hz) y parpadeos/derivas lentas
highcut = 40.0 # Baja de 30Hz a 20Hz para eliminar espasmos musculares muy abruptos
nyq = FS / 2.0

# Crear filtro Butterworth pasa-bandas de orden 4
b_bp, a_bp = signal.butter(8, [lowcut / nyq, highcut / nyq], btype='band')

df_filtrado = df_franja.copy()
for ch in CANALES:
    señal_centrada = df_franja[ch] - df_franja[ch].mean()
    df_filtrado[ch + '_fil'] = signal.filtfilt(b_bp, a_bp, señal_centrada)


# ==========================================
# 7. PLOT 3: SEÑAL ORIGINAL VS. FILTRADA (2-20 Hz)
# ==========================================
fig, axes = plt.subplots(len(CANALES), 1, figsize=(12, 10), sharex=True)
trigger_activo = df_franja['Trigger'] == 1

for i, ch in enumerate(CANALES):
    ax = axes[i]
    señal_original = df_franja[ch] - df_franja[ch].mean()
    señal_filtrada = df_filtrado[ch + '_fil']
    
    ax.plot(df_franja['Tiempo_s'], señal_original, label=f'{ch} Original', color='gray', alpha=0.5, linewidth=1)
    ax.plot(df_franja['Tiempo_s'], señal_filtrada, label=f'{ch} Filtrada (2-20 Hz)', color='blue', linewidth=1.5)

    if trigger_activo.any():
        ylim_bottom, ylim_top = ax.get_ylim()
        ax.fill_between(df_franja['Tiempo_s'], ylim_bottom, ylim_top, where=trigger_activo, color='red', alpha=0.1, label='Trigger Activo' if i == 0 else "")

    ax.set_title(f"Canal {ch}")
    ax.set_ylabel("Amplitud (µV)")
    ax.grid(True, linestyle='--', alpha=0.5)
    if i == 0:
        ax.legend(loc='upper right')

plt.xlim(TIEMPO_INICIO, TIEMPO_FIN)
plt.xlabel("Tiempo (segundos)")
plt.tight_layout()
plt.show()


# ==========================================
# 8. PLOT 4: COMPARACIÓN DE ESPECTROS
# ==========================================
plt.figure(figsize=(10, 6))
nperseg = int(min(len(df_franja), FS * 1.0))

for ch in CANALES:
    sig_orig = (df_franja[ch] - df_franja[ch].mean()).values
    freqs_orig, psd_orig = signal.welch(sig_orig, fs=FS, window='hann', nperseg=nperseg, noverlap=nperseg // 2)
    
    sig_filt = df_filtrado[ch + '_fil'].values
    freqs_filt, psd_filt = signal.welch(sig_filt, fs=FS, window='hann', nperseg=nperseg, noverlap=nperseg // 2)
    
    idx = (freqs_orig >= 0.5) & (freqs_orig <= 50.0)
    
    plt.plot(freqs_orig[idx], psd_orig[idx], linestyle='--', alpha=0.4, label=f'{ch} Original')
    plt.plot(freqs_filt[idx], psd_filt[idx], linewidth=1.5, label=f'{ch} Filtrada (2-20Hz)')

plt.axvspan(2, 20, color='green', alpha=0.05, label='Banda de Paso (2-20 Hz)')
plt.title("Densidad Espectral de Potencia: Original vs. Filtrada (2-20 Hz)")
plt.xlabel("Frecuencia (Hz)")
plt.ylabel("Potencia (µV² / Hz)")
plt.yscale('log')
plt.grid(True, which='both', linestyle='--', alpha=0.5)
plt.legend(bbox_to_anchor=(1.02, 1), loc='upper left', fontsize='small')
plt.tight_layout()
plt.show()







# ==========================================
# 9. PLOT 5: COMPARACIÓN DE ESPECTROS (Solo durante Gestos: Original vs Filtrado)
# ==========================================
plt.figure(figsize=(10, 6))

mask_gestos = df_franja['Trigger'] == 1

if mask_gestos.any():
    # Definir tamaño de ventana adaptado a la duración total de los gestos acumulados
    nperseg_gesto = int(min(mask_gestos.sum(), FS * 0.5))
    
    if nperseg_gesto > 10:
        for ch in CANALES:
            # 1. Extraer señal ORIGINAL solo durante los gestos
            sig_orig_gesto = (df_franja.loc[mask_gestos, ch] - df_franja[ch].mean()).values
            freqs_og, psd_og = signal.welch(sig_orig_gesto, fs=FS, window='hann', nperseg=nperseg_gesto, noverlap=nperseg_gesto // 2)
            
            # 2. Extraer señal FILTRADA (2-20 Hz) solo durante los gestos
            sig_filt_gesto = df_filtrado.loc[mask_gestos, ch + '_fil'].values
            freqs_fg, psd_fg = signal.welch(sig_filt_gesto, fs=FS, window='hann', nperseg=nperseg_gesto, noverlap=nperseg_gesto // 2)
            
            # Rango útil de visualización (1 a 30 Hz)
            idx_g = (freqs_og >= 1.0) & (freqs_og <= 30.0)
            
            # Graficar ambas curvas para comparar el before/after exacto del gesto
            plt.plot(freqs_og[idx_g], psd_og[idx_g], linestyle='--', alpha=0.4, label=f'{ch} Original (Gestos)')
            plt.plot(freqs_fg[idx_g], psd_fg[idx_g], linewidth=1.5, label=f'{ch} Filtrada (Gestos)')

        plt.axvspan(2.0, 20.0, color='green', alpha=0.05, label='Banda de Filtro (2-20 Hz)')
        plt.title("Densidad Espectral de Potencia: Original vs Filtrada (Exclusivo en Gestos)")
        plt.xlabel("Frecuencia (Hz)")
        plt.ylabel("Potencia (µV² / Hz)")
        plt.yscale('log')
        plt.grid(True, which='both', linestyle='--', alpha=0.5)
        plt.legend(bbox_to_anchor=(1.02, 1), loc='upper left', fontsize='small')
        plt.tight_layout()
        plt.show()
    else:
        print("⚠️ Los segmentos de gestos juntos son muy cortos para calcular el espectro con Welch.")
else:
    print("⚠️ No hay datos con Trigger == 1 en la franja seleccionada para calcular el espectro de gestos.")







# ==========================================
# 10. DETECCIÓN ESTRICTA MUESTRA A MUESTRA (Sin desfasajes)
# ==========================================
from scipy.ndimage import label

ventana_baseline = int(FS * 2)
PERIODO_REFRACTARIO = int(FS * 0.15) # 500ms de bloqueo anti-rebote

# Márgenes independientes por canal
MARGENES_UMBRAL = {
    'FP1': 250.0,
    'FP2': 250.0,
    'TP9': 200.0,
    'TP10': 200.0
}

baseline_ch = {}
desplazamiento_ch = {}
supera_ch = {}
umbrales_sup = {}
umbrales_inf = {}

for ch in CANALES:
    sig_c = df_filtrado[ch + '_fil'] - df_filtrado[ch + '_fil'].mean()
    baseline_ch[ch] = sig_c.rolling(window=ventana_baseline, center=True, min_periods=1).mean()
    desplazamiento_ch[ch] = np.abs(sig_c - baseline_ch[ch])
    supera_ch[ch] = desplazamiento_ch[ch] > MARGENES_UMBRAL[ch]
    
    umbrales_sup[ch] = baseline_ch[ch] + MARGENES_UMBRAL[ch]
    umbrales_inf[ch] = baseline_ch[ch] - MARGENES_UMBRAL[ch]

# REGLA LÓGICA ESTRICTA MUESTRA A MUESTRA (Sin expansiones ni desfasajes)
# Se exige que al menos un frontal Y al menos un temporal superen el umbral en la misma muestra exacta.
frontal_ok = supera_ch['FP1'] | supera_ch['FP2']
temporal_ok = supera_ch['TP9'] | supera_ch['TP10']
deteccion_cruda = frontal_ok & temporal_ok

# Aplicar Período Refractario (Anti-rebote)
deteccion_global = np.zeros_like(deteccion_cruda, dtype=bool)
ultimo_disparo = -9999

for idx_m in range(len(deteccion_cruda)):
    if deteccion_cruda.iloc[idx_m]:
        if (idx_m - ultimo_disparo) > PERIODO_REFRACTARIO:
            deteccion_global[idx_m] = True
            ultimo_disparo = idx_m

# ==========================================
# 11. EVALUACIÓN ESTRICTA (TP DENTRO DEL FLAG, FP FUERA)
# ==========================================
trigger_array = (df_franja['Trigger'] == 1).values
trigger_blocks, num_gestos_reales = label(trigger_array)

tp_count = 0
fn_count = 0

for i in range(1, num_gestos_reales + 1):
    block_mask = (trigger_blocks == i)
    if deteccion_global[block_mask].any():
        tp_count += 1
    else:
        fn_count += 1

fuera_de_trigger = ~trigger_array
fp_mascara = deteccion_global & fuera_de_trigger
_, num_fps = label(fp_mascara.astype(int))

print("\n==========================================")
print("📊 RESULTADOS CON LÓGICA ESTRICTA MUESTRA A MUESTRA")
print(f"==========================================")
print(f"Gestos reales (Triggers):          {num_gestos_reales}")
print(f"Verdaderos Positivos (DENTRO):     {tp_count}")
print(f"Falsos Negativos (No detectados):  {fn_count}")
print(f"Falsos Positivos (Flasas alarmas): {num_fps}")
print("==========================================")


# ==========================================
# 12. PLOT 6: VISUALIZACIÓN CLARA
# ==========================================
fig, axes = plt.subplots(len(CANALES), 1, figsize=(12, 10), sharex=True)

for i, ch in enumerate(CANALES):
    ax = axes[i]
    
    sig_c = df_filtrado[ch + '_fil'] - df_filtrado[ch + '_fil'].mean()
    ax.plot(df_franja['Tiempo_s'], sig_c, label=f'{ch} Señal', color='royalblue', alpha=0.6, linewidth=1)
    ax.plot(df_franja['Tiempo_s'], baseline_ch[ch], label=f'{ch} Base Móvil', color='magenta', linestyle=':', linewidth=1)
    
    ax.plot(df_franja['Tiempo_s'], umbrales_sup[ch], color='darkorange', linestyle='--', linewidth=1.2, label=f'Umbral (±{MARGENES_UMBRAL[ch]}µV)' if i == 0 else "")
    ax.plot(df_franja['Tiempo_s'], umbrales_inf[ch], color='darkorange', linestyle='--', linewidth=1.2)
    
    if trigger_array.any():
        ylim_bottom, ylim_top = ax.get_ylim()
        ax.fill_between(df_franja['Tiempo_s'], ylim_bottom, ylim_top, 
                         where=trigger_array, color='red', alpha=0.1, label='Trigger Real (Gesto)' if i == 0 else "")
        
    # Puntos amarillos: cuando ESTE canal supera su umbral de forma individual
    ch_sup = supera_ch[ch]
    if ch_sup.any():
        ax.scatter(df_franja['Tiempo_s'][ch_sup], sig_c[ch_sup], color='gold', s=20, zorder=4, label='Supera Umbral' if i == 0 else "")

    # Detección Global Válida (Se dibuja arriba como triángulo verde cuando la condición estricta se cumple)
    if deteccion_global.any():
        tiempos_validados = df_franja['Tiempo_s'][deteccion_global]
        y_top_pos = ylim_top * 0.8 if 'ylim_top' in locals() else 50
        ax.scatter(tiempos_validados, [y_top_pos] * len(tiempos_validados), color='limegreen', marker='v', s=60, zorder=6, label='Detección Global Válida' if i == 0 else "")

    ax.set_title(f"Canal {ch}")
    ax.set_ylabel("Amplitud (µV)")
    ax.grid(True, linestyle='--', alpha=0.5)
    
    if i == 0:
        ax.legend(loc='upper right', fontsize='small')

plt.xlim(TIEMPO_INICIO, TIEMPO_FIN)
plt.xlabel("Tiempo (segundos)")
plt.tight_layout()
plt.show()