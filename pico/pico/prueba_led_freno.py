# prueba_led_freno.py - Prueba sola (correr desde Thonny con el boton Run, NO guardarla como main.py).
# Hace titilar el LED de la placa y el pin del freno a la vez, cada 0,5 s, para ubicar el pin con un tester o un LED.
# Los motores no se tocan. Se detiene con Stop (Ctrl+C) y deja el freno aplicado (1).
from machine import Pin
import time

PIN_FRENO = 26                       # el mismo valor que en main.py

try:
    led = Pin("LED", Pin.OUT)        # funciona en Pico y en Pico W
    nombre_led = '"LED"'
except Exception:
    led = Pin(25, Pin.OUT)
    nombre_led = "GP25"
freno = Pin(PIN_FRENO, Pin.OUT, value=1)

print("Titilando LED", nombre_led, "y GP%d (freno). Medi entre ese pin y GND: tiene que alternar 0 V / 3,3 V." % PIN_FRENO)
try:
    estado = 0
    while True:
        estado ^= 1
        led.value(estado)
        freno.value(estado)
        print("GP%d =" % PIN_FRENO, estado)
        time.sleep(0.5)
finally:
    freno.value(1)
    led.value(0)
