import numpy as np
from scipy import signal
from pythonosc import dispatcher, osc_server
import threading
from collections import deque
import time

FS = 256.0  
b_notch, a_notch = signal.iirnotch(50.0, 30.0, FS)
b_band, a_band = signal.butter(4, [20.0/(0.5*FS), 45.0/(0.5*FS)], btype='band')

BUFFER_SIZE = 64 
# Buffers para los 4 canales posibles
ch0 = deque([0.0] * BUFFER_SIZE, maxlen=BUFFER_SIZE)
ch1 = deque([0.0] * BUFFER_SIZE, maxlen=BUFFER_SIZE)
ch2 = deque([0.0] * BUFFER_SIZE, maxlen=BUFFER_SIZE)
ch3 = deque([0.0] * BUFFER_SIZE, maxlen=BUFFER_SIZE)

def eeg_handler(address, *args):
    ch0.append(args[0])
    ch1.append(args[1])
    ch2.append(args[2])
    ch3.append(args[3])

def procesar_canal(buffer_datos):
    if len(buffer_datos) < BUFFER_SIZE: return 0.0
    y_notch = signal.lfilter(b_notch, a_notch, np.array(buffer_datos))
    y_band = signal.lfilter(b_band, a_band, y_notch)
    return np.sqrt(np.mean(np.abs(y_band)**2))

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", eeg_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=iniciar_osc, daemon=True).start()
    
    print("\n[*] ESCÁNER DE 4 CANALES EMG (RMS)")
    print("[*] 1. Muerde fuerte y mira qué canal sube.")
    print("[*] 2. Levanta las cejas y mira qué canal sube.\n")
    print("-" * 65)
    print("   CH_0 (args[0]) | CH_1 (args[1]) | CH_2 (args[2]) | CH_3 (args[3])")
    print("-" * 65)
    
    try:
        while True:
            time.sleep(0.1) # 10 FPS para poder leer la consola
            
            rms0 = procesar_canal(ch0)
            rms1 = procesar_canal(ch1)
            rms2 = procesar_canal(ch2)
            rms3 = procesar_canal(ch3)
            
            print(f"    {rms0:7.1f}       |    {rms1:7.1f}     |    {rms2:7.1f}     |    {rms3:7.1f}")

    except KeyboardInterrupt:
        print("\n[*] Sistema detenido.")