#!/usr/bin/env python3
"""HC-SR501 PIR motion sensor test — GPIO4 (Pin 7), 3 wires (VCC→5V, GND, OUT→GPIO4)
   Note: PIR output is 3.3V-safe. No LLC needed. Warm-up ~30s after power-on.
"""

from gpiozero import MotionSensor
import time

PIR_PIN = 4

pir = MotionSensor(PIR_PIN)

print("PIR warming up (30 seconds)...")
pir.wait_for_no_motion()
print("PIR ready. Watching for motion — press Ctrl+C to stop.\n")

try:
    while True:
        if pir.motion_detected:
            print(f"[{time.strftime('%H:%M:%S')}] MOTION DETECTED")
            pir.wait_for_no_motion()
            print(f"[{time.strftime('%H:%M:%S')}] motion cleared")
        time.sleep(0.05)
except KeyboardInterrupt:
    print("\nDone.")
