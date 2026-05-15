#!/usr/bin/env python3
"""APDS-9960 live — gesture, proximity, RGB. Sensor faces UP.
   Wave hand 5-15cm above. Hold coloured objects close for colour.
"""
import board, busio, time
from adafruit_apds9960.apds9960 import APDS9960

i2c   = busio.I2C(board.SCL, board.SDA)
apds  = APDS9960(i2c)

apds.enable_proximity = True
apds.enable_gesture   = True
apds.enable_color     = True
apds.gesture_sensitivity = 1   # 0=more sensitive, 1=less false triggers

GESTURES = {1: "UP    ↑", 2: "DOWN  ↓", 3: "LEFT  ←", 4: "RIGHT →"}

last_gesture  = "wave hand over sensor..."
gesture_timer = 0

def colour_name(r, g, b):
    m = max(r, g, b, 1)
    if m < 100:   return "DARK        "
    if r/m > 0.6 and g/m < 0.5 and b/m < 0.5: return "RED         "
    if g/m > 0.6 and r/m < 0.5 and b/m < 0.5: return "GREEN       "
    if b/m > 0.6 and r/m < 0.5 and g/m < 0.5: return "BLUE        "
    if r/m > 0.6 and g/m > 0.5 and b/m < 0.4: return "YELLOW      "
    if r/m > 0.5 and b/m > 0.5 and g/m < 0.4: return "PURPLE      "
    return "WHITE/MIXED "

def bar(val, maxv, width=20):
    filled = min(int(val / maxv * width), width)
    return '█' * filled + '░' * (width - filled)

print("\033[2J", flush=True)

try:
    while True:
        g = apds.gesture()
        if g:
            last_gesture  = GESTURES.get(g, f"unknown({g})")
            gesture_timer = 25

        if gesture_timer > 0: gesture_timer -= 1
        else: last_gesture = "wave hand over sensor..."

        prox = apds.proximity
        r, g_c, b, c = apds.color_data

        print("\033[H", end="", flush=True)
        print("=" * 46)
        print("   APDS-9960  Gesture + Prox + Colour")
        print("=" * 46)
        print(f"\n  Gesture:   {last_gesture:<30}")
        print(f"\n  Proximity: {bar(prox, 255)} {prox:3d}/255")
        print(f"\n  Colour:    {colour_name(r, g_c, b)}")
        print(f"  R  {bar(r, 1000):20s} {r:5d}")
        print(f"  G  {bar(g_c, 1000):20s} {g_c:5d}")
        print(f"  B  {bar(b, 1000):20s} {b:5d}")
        print(f"  C  {bar(c, 3000):20s} {c:5d}")
        print(f"\n  Tips:")
        print(f"  - Gesture: wave 5-15cm above sensor (fast)")
        print(f"  - Colour:  hold object 2-5cm away")
        print(f"  - Prox:    bring finger slowly closer")
        print("=" * 46, flush=True)
        time.sleep(0.05)

except KeyboardInterrupt:
    print("\nStopped.")
