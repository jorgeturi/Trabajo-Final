import zmq
import threading
import logging
from flask import Flask, jsonify, render_template_string

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

IP_RASPBERRY = "192.168.1.50"
PUERTO_ZMQ = 5555
PUERTO_WEB = 8080

app = Flask(__name__)

DATOS_ACTUALES = {
    "acc": {"x": 0, "y": 0, "z": 0},
    "gyro": {"x": 0, "y": 0, "z": 0},
    "horseshoe": {"TP9": 4, "FP1": 4, "FP2": 4, "TP10": 4},
    "frente": 0,
    "bateria": "Calculando...",
    "parpadeos_total": 0,
    "mandibula_total": 0,
    "evento_reciente": "ESPERANDO SEÑAL",
    "alpha": [0,0,0,0],
    "beta": [0,0,0,0]
}

def lector_zmq():
    context = zmq.Context()
    sock = context.socket(zmq.SUB)
    sock.connect(f"tcp://{IP_RASPBERRY}:{PUERTO_ZMQ}")
    sock.setsockopt_string(zmq.SUBSCRIBE, "")

    print(f"[INFO] Hilo ZMQ escuchando en {IP_RASPBERRY}:{PUERTO_ZMQ}")

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
                    DATOS_ACTUALES["evento_reciente"] = "PARPADEO"
            
            elif tipo == "JAW_CLENCH":
                if msg.get("valor") == 1:
                    DATOS_ACTUALES["mandibula_total"] += 1
                    DATOS_ACTUALES["evento_reciente"] = "MANDIBULA"

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

hilo = threading.Thread(target=lector_zmq, daemon=True)
hilo.start()

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
        <title>Telemetría BCI</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-base: #000000;
                --bg-panel: #111111;
                --border-hard: #333333;
                --text-main: #FFFFFF;
                --text-data: #00E5FF;
                --ok: #00FF00;
                --warn: #FFFF00;
                --err: #FF0000;
            }
            body {
                background-color: var(--bg-base);
                color: var(--text-main);
                font-family: 'Inter', sans-serif;
                padding: 2rem;
            }
            h2 {
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 2px;
                margin-bottom: 2rem;
                font-size: 1.5rem;
            }
            .card {
                background-color: var(--bg-panel);
                border: 1px solid var(--border-hard);
                border-radius: 0;
                margin-bottom: 20px;
            }
            .card-header {
                background-color: transparent;
                border-bottom: 1px solid var(--border-hard);
                font-weight: 600;
                font-size: 0.9rem;
                text-transform: uppercase;
                letter-spacing: 1px;
                color: var(--text-main);
                padding: 1rem;
            }
            .card-body {
                padding: 1.5rem;
            }
            .label-row {
                display: flex;
                justify-content: space-between;
                border-bottom: 1px solid var(--border-hard);
                padding: 0.5rem 0;
            }
            .label-row:last-child {
                border-bottom: none;
            }
            .data-label {
                font-size: 0.9rem;
                font-weight: 600;
                color: var(--text-main);
            }
            .data-value {
                font-family: 'JetBrains Mono', monospace;
                font-size: 1rem;
                color: var(--text-data);
            }
            .imu-grid {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 10px;
                margin-top: 10px;
                margin-bottom: 20px;
            }
            .imu-item {
                font-family: 'JetBrains Mono', monospace;
                background-color: var(--bg-base);
                border: 1px solid var(--border-hard);
                padding: 0.5rem;
                text-align: center;
                font-size: 0.9rem;
                color: var(--text-data);
            }
            .event-box {
                font-family: 'JetBrains Mono', monospace;
                font-size: 1.2rem;
                font-weight: 700;
                color: var(--text-base);
                background-color: var(--text-data);
                color: #000000;
                padding: 1rem;
                text-align: center;
                text-transform: uppercase;
                margin-top: 1rem;
            }
            .status-ok { color: var(--ok); font-family: 'JetBrains Mono', monospace; }
            .status-warn { color: var(--warn); font-family: 'JetBrains Mono', monospace; }
            .status-err { color: var(--err); font-family: 'JetBrains Mono', monospace; }
        </style>
    </head>
    <body>
        <div class="container-fluid max-w-7xl">
            <h2>Panel de Telemetría</h2>
            
            <div class="row g-4">
                <div class="col-md-4">
                    <div class="card h-100">
                        <div class="card-header">Hardware</div>
                        <div class="card-body">
                            <div class="label-row">
                                <span class="data-label">Batería</span>
                                <span class="data-value" id="bateria">--</span>
                            </div>
                            <div class="label-row mb-4">
                                <span class="data-label">Contacto Frontal</span>
                                <span class="data-value" id="frente">--</span>
                            </div>
                            
                            <div class="data-label mt-4 mb-2">Conexión Electrodos</div>
                            <div class="label-row">
                                <span>TP9 (Izq)</span>
                                <span id="tp9"></span>
                            </div>
                            <div class="label-row">
                                <span>FP1 (Frente Izq)</span>
                                <span id="fp1"></span>
                            </div>
                            <div class="label-row">
                                <span>FP2 (Frente Der)</span>
                                <span id="fp2"></span>
                            </div>
                            <div class="label-row">
                                <span>TP10 (Der)</span>
                                <span id="tp10"></span>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="col-md-4">
                    <div class="card h-100">
                        <div class="card-header">Cinemática (IMU)</div>
                        <div class="card-body">
                            <div class="data-label">Acelerómetro</div>
                            <div class="imu-grid">
                                <div class="imu-item">X: <span id="acc_x">0.0</span></div>
                                <div class="imu-item">Y: <span id="acc_y">0.0</span></div>
                                <div class="imu-item">Z: <span id="acc_z">0.0</span></div>
                            </div>
                            
                            <div class="data-label">Giroscopio</div>
                            <div class="imu-grid mb-0">
                                <div class="imu-item">X: <span id="gyro_x">0.0</span></div>
                                <div class="imu-item">Y: <span id="gyro_y">0.0</span></div>
                                <div class="imu-item">Z: <span id="gyro_z">0.0</span></div>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="col-md-4">
                    <div class="card mb-4">
                        <div class="card-header">Eventos</div>
                        <div class="card-body">
                            <div class="label-row">
                                <span class="data-label">Parpadeos Totales</span>
                                <span id="cont_parpadeos" class="data-value">0</span>
                            </div>
                            <div class="label-row">
                                <span class="data-label">Contracciones</span>
                                <span id="cont_mandibula" class="data-value">0</span>
                            </div>
                            <div id="evento_reciente" class="event-box">--</div>
                        </div>
                    </div>
                    
                    <div class="card">
                        <div class="card-header">Bandas de Frecuencia</div>
                        <div class="card-body">
                            <div class="label-row">
                                <span class="data-label">Alpha</span>
                                <span class="data-value" id="alpha">[0, 0, 0, 0]</span>
                            </div>
                            <div class="label-row">
                                <span class="data-label">Beta</span>
                                <span class="data-value" id="beta">[0, 0, 0, 0]</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            function formatoSensor(valor) {
                if (valor === 1) return "<span class='status-ok'>OK</span>";
                if (valor === 2) return "<span class='status-warn'>REGULAR</span>";
                return "<span class='status-err'>FALLA</span>";
            }

            setInterval(() => {
                fetch('/datos')
                    .then(response => response.json())
                    .then(data => {
                        document.getElementById('bateria').innerText = data.bateria;
                        document.getElementById('frente').innerHTML = data.frente === 1 ? "<span class='status-ok'>CONECTADO</span>" : "<span class='status-err'>DESCONECTADO</span>";
                        
                        document.getElementById('tp9').innerHTML = formatoSensor(data.horseshoe.TP9);
                        document.getElementById('fp1').innerHTML = formatoSensor(data.horseshoe.FP1);
                        document.getElementById('fp2').innerHTML = formatoSensor(data.horseshoe.FP2);
                        document.getElementById('tp10').innerHTML = formatoSensor(data.horseshoe.TP10);
                        
                        document.getElementById('cont_parpadeos').innerText = data.parpadeos_total;
                        document.getElementById('cont_mandibula').innerText = data.mandibula_total;
                        document.getElementById('evento_reciente').innerText = data.evento_reciente;
                        
                        document.getElementById('acc_x').innerText = data.acc.x.toFixed(1).padStart(5, ' ');
                        document.getElementById('acc_y').innerText = data.acc.y.toFixed(1).padStart(5, ' ');
                        document.getElementById('acc_z').innerText = data.acc.z.toFixed(1).padStart(5, ' ');

                        document.getElementById('gyro_x').innerText = data.gyro.x.toFixed(1).padStart(5, ' ');
                        document.getElementById('gyro_y').innerText = data.gyro.y.toFixed(1).padStart(5, ' ');
                        document.getElementById('gyro_z').innerText = data.gyro.z.toFixed(1).padStart(5, ' ');
                        
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
    print(f"[INFO] Servidor web de telemetria iniciado en http://127.0.0.1:{PUERTO_WEB}")
    app.run(host="0.0.0.0", port=PUERTO_WEB, debug=False)