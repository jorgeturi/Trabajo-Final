#!/bin/bash

# Validar que se ejecute con permisos de administrador (root)
if [ "$EUID" -ne 0 ]; then
  echo "Error: Por favor, ejecutá este script con sudo."
  exit 1
fi

echo "=== Configurando Arquitectura BCI Muse ==="

# 1. Definir rutas dinámicamente y capturar usuario real
# Obtiene la ruta absoluta de la carpeta donde está este script
PROJECT_DIR=$(dirname $(realpath "$0"))
# Captura el usuario que ejecutó el sudo (ej: tujo)
REAL_USER=$SUDO_USER

ENV_FILE="$PROJECT_DIR/variables/muse_config.env"
SERVICE_FILE="/etc/systemd/system/muse.service"

# 2. Validación estricta del entorno
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR CRÍTICO: No se encontró el archivo de configuración."
  echo "Por favor, creá el archivo en: $ENV_FILE"
  echo ""
  echo "El archivo debe contener exactamente estas tres variables:"
  echo "MUSE_MAC=00:06:66:XX:XX:XX"
  echo "TARGET_IP=192.168.X.X"
  echo "TARGET_PORT=5000"
  exit 1
fi
echo "Archivo de variables detectado en $ENV_FILE"

# 3. Crear el servicio de systemd
echo "Creando servicio del sistema en $SERVICE_FILE..."
# Usamos cat << EOF para inyectar el texto.
cat << EOF > $SERVICE_FILE
[Unit]
Description=Decodificador EEG Muse-IO
After=bluetooth.target

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$PROJECT_DIR/sdk
EnvironmentFile=$ENV_FILE
ExecStart=/usr/local/bin/box86 ./muse-io --device \${MUSE_MAC} --osc osc.udp://\${TARGET_IP}:\${TARGET_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 4. Recargar y activar
echo "Activando el servicio para que arranque con la placa..."
systemctl daemon-reload
systemctl enable muse.service
systemctl restart muse.service

echo "================================================="
echo "¡Listo! El servicio está configurado y corriendo."
echo "Se guardó configurado bajo el usuario: $REAL_USER"
echo "Si querés editar este servicio en el futuro, entrá con:"
echo "   sudo nano $SERVICE_FILE"
echo ""
echo "Podés monitorear los logs en tiempo real con:"
echo "sudo journalctl -u muse.service -f"
echo "================================================="