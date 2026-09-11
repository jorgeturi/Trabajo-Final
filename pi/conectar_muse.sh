#!/bin/bash
MAC="00:06:66:70:5E:54"

echo "Liberando el puerto por si quedó abierto..."
sudo rfcomm release 0 2>/dev/null

echo "Vinculando la Muse ($MAC) al puerto virtual..."
sudo rfcomm bind 0 $MAC 1

echo "¡Listo! El puerto /dev/rfcomm0 debería estar disponible:"
ls -l /dev/rfcomm0