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
archivos = glob.glob("dataset_cerrar ambos ojos_1789840274.csv")
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
lowcut = 2.0   # Sube de 1Hz a 2Hz para cortar latidos (~1.2Hz) y parpadeos/derivas lentas
highcut = 10.0 # Baja de 30Hz a 20Hz para eliminar espasmos musculares muy abruptos
nyq = FS / 2.0

# Crear filtro Butterworth pasa-bandas de orden 4
b_bp, a_bp = signal.butter(4, [lowcut / nyq, highcut / nyq], btype='band')

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