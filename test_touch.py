#!/usr/bin/env python3
"""TTP223 capacitive touch sensor test — GPIO5 (left), GPIO25 (right)
   3 wires each: VCC→3.3V, GND, OUT→GPIO pin.
   TTP223 output is 3.3V — no LLC needed.
"""

from gpiozero import Button
import time

TOUCH_L_PIN = 5
TOUCH_R_PIN = 25

# TTP223 default: output HIGH on touch → use pull_down so idle is clean
touch_l = Button(TOUCH_L_PIN, pull_up=False)
touch_r = Button(TOUCH_R_PIN, pull_up=False)

print("TTP223 touch test ready. Touch the sensors — press Ctrl+C to stop.\n")

def on_touch_l():
    print(f"[{time.strftime('%H:%M:%S')}] LEFT touch")

def on_release_l():
    print(f"[{time.strftime('%H:%M:%S')}] LEFT released")

def on_touch_r():
    print(f"[{time.strftime('%H:%M:%S')}] RIGHT touch")

def on_release_r():
    print(f"[{time.strftime('%H:%M:%S')}] RIGHT released")

touch_l.when_pressed  = on_touch_l
touch_l.when_released = on_release_l
touch_r.when_pressed  = on_touch_r
touch_r.when_released = on_release_r

try:
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\nDone.")
