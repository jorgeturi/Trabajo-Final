import numpy as np
from scipy import signal
from pythonosc import dispatcher, osc_server
import threading
from collections import deque
import time

FS = 256.0  

# Filtros
b_notch, a_notch = signal.iirnotch(50.0, 30.0, FS)
b_band_ojos, a_band_ojos = signal.butter(4, [1.0/(0.5*FS), 10.0/(0.5*FS)], btype='band')
b_band_mandibula, a_band_mandibula = signal.butter(4, [15.0/(0.5*FS), 45.0/(0.5*FS)], btype='band')

BUFFER_SIZE = 256
raw_tp9 = deque([0.0] * BUFFER_SIZE, maxlen=BUFFER_SIZE)   
raw_fp1 = deque([0.0] * BUFFER_SIZE, maxlen=BUFFER_SIZE)   

# Variable global de seguridad (Kill Switch)
vincha_conectada = False

def eeg_handler(address, *args):
    raw_tp9.append(args[0])
    raw_fp1.append(args[1])

def forehead_handler(address, *args):
    global vincha_conectada
    # args[0] es 1 si toca la piel, 0 si está al aire
    vincha_conectada = (args[0] == 1 or args[0] == 1.0)

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    disp.map("/muse/elements/touching_forehead", forehead_handler) # Escuchamos el sensor de piel
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=iniciar_osc, daemon=True).start()

    print("\n[*] INICIANDO BCI (CON KILL-SWITCH DE HARDWARE)...")
    print("[!] Esperando a que te pongas la diadema...")
    
    # Esperar hasta que el DRL toque la piel
    while not vincha_conectada:
        time.sleep(0.5)

    print("[*] Contacto detectado. 5 SEGUNDOS DE CALIBRACIÓN (Quédate inmóvil)...\n")
    tiempo_inicio = time.time()
    base_ojos, base_mandibula = [], []
    
    while time.time() - tiempo_inicio < 5.0:
        time.sleep(0.1)
        if len(raw_tp9) < BUFFER_SIZE or not vincha_conectada: continue

        y_tp9 = signal.lfilter(b_band_mandibula, a_band_mandibula, signal.lfilter(b_notch, a_notch, np.array(raw_tp9)))
        y_fp1 = signal.lfilter(b_band_ojos, a_band_ojos, signal.lfilter(b_notch, a_notch, np.array(raw_fp1)))
        
        base_mandibula.append(np.std(y_tp9))
        base_ojos.append(np.std(y_fp1))

    # Umbrales ajustados para que dispare más fácil
    UMB_MANDIBULA = np.mean(base_mandibula) * 1.3
    UMB_OJOS = np.mean(base_ojos) * 1.3

    print("=" * 50)
    print(f"[*] UMBRAL MANDÍBULA: {UMB_MANDIBULA:.1f}")
    print(f"[*] UMBRAL PARPADEO:  {UMB_OJOS:.1f}")
    print("=" * 50 + "\n")

    cooldown = 0
    try:
        while True:
            time.sleep(0.1) 
            
            if not vincha_conectada:
                print("[!] ERROR: VINCHA DESCONECTADA - Bloqueando falsos positivos...")
                time.sleep(0.5)
                continue

            if cooldown > 0:
                cooldown -= 1
                continue

            y_tp9 = signal.lfilter(b_band_mandibula, a_band_mandibula, signal.lfilter(b_notch, a_notch, np.array(raw_tp9)))
            y_fp1 = signal.lfilter(b_band_ojos, a_band_ojos, signal.lfilter(b_notch, a_notch, np.array(raw_fp1)))
            
            energia_mandibula = np.std(y_tp9)
            energia_ojos = np.std(y_fp1)

            if energia_ojos > UMB_OJOS:
                print(f"[👁️] >>> PARPADEO <<< (E: {energia_ojos:.1f})")
                cooldown = 10
            elif energia_mandibula > UMB_MANDIBULA:
                print(f"[🦷] >>> MANDÍBULA <<< (E: {energia_mandibula:.1f})")
                cooldown = 10
            else:
                # Modificado para que veas los números claros al morder
                print(f"Ojos: {energia_ojos:.1f} (U:{UMB_OJOS:.1f}) | Mandíbula: {energia_mandibula:.1f} (U:{UMB_MANDIBULA:.1f})")

    except KeyboardInterrupt:
        print("\n[*] Sistema detenido.")