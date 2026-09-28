import zmq
import threading
import logging
from flask import Flask, jsonify, render_template_string

# Apagar los logs molestos de Flask en la consola
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# ==========================================
# CONFIGURACIÓN
# ==========================================
IP_RASPBERRY = "192.168.1.50"  # IP de tu Raspberry Pi
PUERTO_ZMQ = 5555
PUERTO_WEB = 8080

app = Flask(__name__)

# Memoria global donde guardamos los últimos datos para la web
DATOS_ACTUALES = {
    "acc": {"x": 0, "y": 0, "z": 0},
    "gyro": {"x": 0, "y": 0, "z": 0},
    "horseshoe": {"TP9": 4, "FP1": 4, "FP2": 4, "TP10": 4},
    "frente": 0,
    "bateria": "Calculando...",
    "parpadeos_total": 0,
    "mandibula_total": 0,
    "evento_reciente": "Ninguno",
    "alpha": [0,0,0,0],
    "beta": [0,0,0,0]
}

# ==========================================
# HILO RECEPTOR DE ZMQ
# ==========================================
def lector_zmq():
    context = zmq.Context()
    sock = context.socket(zmq.SUB)
    sock.connect(f"tcp://{IP_RASPBERRY}:{PUERTO_ZMQ}")
    sock.setsockopt_string(zmq.SUBSCRIBE, "")

    print(f"[*] Hilo ZMQ conectado a {IP_RASPBERRY}:{PUERTO_ZMQ}")

    while True:
        try:
            msg = sock.recv_json()
            tipo = msg.get("tipo", "OTRO")

            if tipo == "ACC":
                DATOS_ACTUALES["acc"] = {"x": msg.get("x"), "y": msg.get("y"), "z": msg.get("z")}
            
            elif tipo == "GYRO":
                DATOS_ACTUALES["gyro"] = {"x": msg.get("x"), "y": msg.get("y"), "z": msg.get("z")}
            
            elif tipo == "HORSESHOE":
                DATOS_ACTUALES["horseshoe"] = {
                    "TP9": msg.get("TP9"), "FP1": msg.get("FP1"), 
                    "FP2": msg.get("FP2"), "TP10": msg.get("TP10")
                }
            
            elif tipo == "BLINK":
                if msg.get("valor") == 1:
                    DATOS_ACTUALES["parpadeos_total"] += 1
                    DATOS_ACTUALES["evento_reciente"] = "👁️ PARPADEO DETECTADO"
            
            elif tipo == "JAW_CLENCH":
                if msg.get("valor") == 1:
                    DATOS_ACTUALES["mandibula_total"] += 1
                    DATOS_ACTUALES["evento_reciente"] = "🦷 MANDÍBULA APRETADA"

            elif tipo == "OTRO":
                address = msg.get("address", "")
                args = msg.get("args", [])
                
                if "/muse/batt" in address and len(args) >= 4:
                    DATOS_ACTUALES["bateria"] = f"{args[0]/100}% ({args[3]}mV)"
                elif "/muse/elements/touching_forehead" in address and len(args) > 0:
                    DATOS_ACTUALES["frente"] = args[0]
                elif "/muse/elements/alpha_absolute" in address and len(args) == 4:
                    DATOS_ACTUALES["alpha"] = [round(x, 3) if isinstance(x, float) else x for x in args]
                elif "/muse/elements/beta_absolute" in address and len(args) == 4:
                    DATOS_ACTUALES["beta"] = [round(x, 3) if isinstance(x, float) else x for x in args]

        except Exception:
            pass

# Iniciamos el hilo de ZMQ en segundo plano
hilo = threading.Thread(target=lector_zmq, daemon=True)
hilo.start()

# ==========================================
# RUTAS DEL SERVIDOR WEB (FLASK)
# ==========================================
@app.route('/datos')
def obtener_datos():
    return jsonify(DATOS_ACTUALES)

@app.route('/')
def index():
    html_dashboard = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Dashboard BCI Muse</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { background-color: #121212; color: #ffffff; padding: 20px; }
            .card { background-color: #1e1e1e; border: 1px solid #333; margin-bottom: 20px; }
            .card-header { font-weight: bold; font-size: 1.1rem; border-bottom: 1px solid #333; }
            .bueno { color: #00ff00; font-weight: bold; }
            .malo { color: #ff3333; font-weight: bold; }
            .evento-destacado { font-size: 1.3rem; color: #00d2ff; }
            .dato-imu { display: inline-block; width: 30%; text-align: center; font-family: monospace; font-size: 1.1rem;}
        </style>
    </head>
    <body>
        <div class="container-fluid">
            <h2 class="mb-4 text-center">🧠 TuJo BCI Dashboard</h2>
            
            <div class="row">
                <!-- Tarjeta de Estado Físico -->
                <div class="col-md-4">
                    <div class="card h-100">
                        <div class="card-header text-info">📡 Estado del Hardware</div>
                        <div class="card-body">
                            <p>Batería: <span id="bateria" class="text-warning fw-bold">Cargando...</span></p>
                            <p>Contacto Frente: <span id="frente">Buscando...</span></p>
                            <hr>
                            <p class="mb-1 text-muted">Calidad de Señal por Sensor:</p>
                            <p class="mb-0">TP9 (Oreja Izq): <span id="tp9"></span></p>
                            <p class="mb-0">FP1 (Frente Izq): <span id="fp1"></span></p>
                            <p class="mb-0">FP2 (Frente Der): <span id="fp2"></span></p>
                            <p class="mb-0">TP10 (Oreja Der): <span id="tp10"></span></p>
                        </div>
                    </div>
                </div>

                <!-- Tarjeta de IMU (Movimiento) -->
                <div class="col-md-4">
                    <div class="card h-100">
                        <div class="card-header text-success">🧭 Unidad de Movimiento (IMU)</div>
                        <div class="card-body">
                            <h6 class="text-secondary">Acelerómetro</h6>
                            <div class="w-100 mb-3 bg-dark p-2 rounded border border-secondary">
                                <span class="dato-imu text-danger">X: <span id="acc_x">0</span></span>
                                <span class="dato-imu text-success">Y: <span id="acc_y">0</span></span>
                                <span class="dato-imu text-primary">Z: <span id="acc_z">0</span></span>
                            </div>
                            
                            <h6 class="text-secondary">Giroscopio</h6>
                            <div class="w-100 bg-dark p-2 rounded border border-secondary">
                                <span class="dato-imu text-danger">X: <span id="gyro_x">0</span></span>
                                <span class="dato-imu text-success">Y: <span id="gyro_y">0</span></span>
                                <span class="dato-imu text-primary">Z: <span id="gyro_z">0</span></span>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Tarjeta de Eventos y Ondas -->
                <div class="col-md-4">
                    <div class="card mb-3">
                        <div class="card-header text-warning">⚡ Eventos Detectados</div>
                        <div class="card-body text-center">
                            <div class="d-flex justify-content-around mb-2">
                                <div>Parpadeos<br><span id="cont_parpadeos" class="fs-3 fw-bold">0</span></div>
                                <div>Mandíbula<br><span id="cont_mandibula" class="fs-3 fw-bold">0</span></div>
                            </div>
                            <div id="evento_reciente" class="evento-destacado bg-dark rounded py-2">Ninguno</div>
                        </div>
                    </div>
                    
                    <div class="card">
                        <div class="card-header text-primary">🌊 Bandas de Frecuencia</div>
                        <div class="card-body">
                            <p class="mb-1 small text-muted">Alpha (Relajación)</p>
                            <p class="mb-2 text-monospace" id="alpha">[0, 0, 0, 0]</p>
                            <p class="mb-1 small text-muted">Beta (Concentración)</p>
                            <p class="mb-0 text-monospace" id="beta">[0, 0, 0, 0]</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            function formatoSensor(valor) {
                if (valor === 1) return "<span class='bueno'>1 (Óptimo)</span>";
                if (valor === 2) return "<span class='text-warning'>2 (Regular)</span>";
                return "<span class='malo'>4 (Malo/Sin contacto)</span>";
            }

            setInterval(() => {
                fetch('/datos')
                    .then(response => response.json())
                    .then(data => {
                        document.getElementById('bateria').innerText = data.bateria;
                        document.getElementById('frente').innerHTML = data.frente === 1 ? "<span class='bueno'>Sí</span>" : "<span class='malo'>No</span>";
                        
                        document.getElementById('tp9').innerHTML = formatoSensor(data.horseshoe.TP9);
                        document.getElementById('fp1').innerHTML = formatoSensor(data.horseshoe.FP1);
                        document.getElementById('fp2').innerHTML = formatoSensor(data.horseshoe.FP2);
                        document.getElementById('tp10').innerHTML = formatoSensor(data.horseshoe.TP10);
                        
                        document.getElementById('cont_parpadeos').innerText = data.parpadeos_total;
                        document.getElementById('cont_mandibula').innerText = data.mandibula_total;
                        document.getElementById('evento_reciente').innerText = data.evento_reciente;
                        
                        document.getElementById('acc_x').innerText = data.acc.x.toFixed(1);
                        document.getElementById('acc_y').innerText = data.acc.y.toFixed(1);
                        document.getElementById('acc_z').innerText = data.acc.z.toFixed(1);

                        document.getElementById('gyro_x').innerText = data.gyro.x.toFixed(1);
                        document.getElementById('gyro_y').innerText = data.gyro.y.toFixed(1);
                        document.getElementById('gyro_z').innerText = data.gyro.z.toFixed(1);
                        
                        document.getElementById('alpha').innerText = JSON.stringify(data.alpha);
                        document.getElementById('beta').innerText = JSON.stringify(data.beta);
                    });
            }, 100); 
        </script>
    </body>
    </html>
    """
    return render_template_string(html_dashboard)

if __name__ == '__main__':
    print("\n" + "="*50)
    print(f"🚀 DASHBOARD WEB INICIADO")
    print(f"👉 Abre tu navegador en la PC en: http://127.0.0.1:{PUERTO_WEB}")
    print("="*50 + "\n")
    
    app.run(host="0.0.0.0", port=PUERTO_WEB, debug=False)