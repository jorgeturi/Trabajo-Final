#!/bin/bash
echo "Actualizando sistema e instalando dependencias base..."
sudo apt update
sudo apt install python3-venv -y

echo "Creando el entorno virtual 'tfg'..."
python3 -m venv tfg

echo "Activando el entorno e instalando requerimientos..."
source tfg/bin/activate
pip install -r requirements.txt

echo "¡Entorno replicado con éxito!"
