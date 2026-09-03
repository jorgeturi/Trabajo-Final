import tkinter as tk
import threading
from pythonosc import dispatcher, osc_server

# Variables de inclinación (Acelerómetro puro)
acc_x = 0.0  # Cabeceo (Adelante/Atrás)
acc_z = 0.0  # Inclinación lateral (Oreja al hombro)

def acc_handler(address, *args):
    global acc_x, acc_z
    acc_x = args[0]
    acc_z = args[2]

def iniciar_osc():
    disp = dispatcher.Dispatcher()
    disp.map("/muse/acc", acc_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 5000), disp)
    server.serve_forever()

def actualizar_puntero():
    sensibilidad = 0.6 
    
    # Eje X de pantalla controlado por Eje Z del acelerómetro (inclinación lateral)
    pantalla_x = 300 + (acc_z * sensibilidad)
    
    # Eje Y de pantalla controlado por Eje X del acelerómetro (cabeceo)
    pantalla_y = 300 - (acc_x * sensibilidad) 
    
    # Límites para que no salga de la ventana
    pantalla_x = max(15, min(585, pantalla_x))
    pantalla_y = max(15, min(585, pantalla_y))
    
    # Movemos el cursor
    canvas.coords(puntero, pantalla_x-15, pantalla_y-15, pantalla_x+15, pantalla_y+15)
    
    # Bucle de actualización (~30 FPS)
    ventana.after(33, actualizar_puntero)

if __name__ == "__main__":
    threading.Thread(target=iniciar_osc, daemon=True).start()

    ventana = tk.Tk()
    ventana.title("Interfaz BCI - Control por Inclinación")
    ventana.geometry("600x600")

    canvas = tk.Canvas(ventana, width=600, height=600, bg="#111111")
    canvas.pack()

    # Mira de referencia
    canvas.create_line(300, 0, 300, 600, fill="#333333")
    canvas.create_line(0, 300, 600, 300, fill="#333333")

    puntero = canvas.create_oval(285, 285, 315, 315, fill="#ff3333", outline="white")

    print("[*] Control por inclinación (Acelerómetro puro) iniciado.")
    print("[*] Arriba/Abajo: Asentir con la cabeza (Adelante/Atrás).")
    print("[*] Izq/Der: Inclinación lateral (Oreja al hombro).\n")
    
    actualizar_puntero()
    ventana.mainloop()