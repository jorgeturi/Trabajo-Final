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
archivos = glob.glob("dataset_*.csv")
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
TIEMPO_INICIO = 1.0
TIEMPO_FIN = 25.0

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