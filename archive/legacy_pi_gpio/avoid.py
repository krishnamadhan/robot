#!/usr/bin/env python3
"""Obstacle avoidance for 2WD robot using HC-SR04 ultrasonic sensor.

TODO: HC-SR04 not connected yet. Solder pin headers on logic level converter first.
Wiring plan (via logic level converter — Pi GPIO is 3.3V, HC-SR04 is 5V):
  HC-SR04 VCC  → 5V (Pin 2)
  HC-SR04 GND  → GND (Pin 6)
  HC-SR04 TRIG → LLC HV side → LLC LV side → GPIO 23 (Pin 16)
  HC-SR04 ECHO → LLC HV side → LLC LV side → GPIO 24 (Pin 18)
  LLC HV ref   → 5V
  LLC LV ref   → 3.3V (Pin 1)

Logic:
  - Drive forward continuously
  - Measure distance every 100ms
  - If distance < 20cm: stop, reverse 0.3s, turn right 0.4s, resume forward
"""

STOP_DISTANCE_CM = 20
TRIG_PIN = 23
ECHO_PIN = 24

# TODO: uncomment when HC-SR04 is connected
# from gpiozero import DistanceSensor
# from robot_move import forward, backward, right, stop
# import time
#
# sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=4)
#
# print('Obstacle avoidance started. Ctrl+C to stop.')
# forward()
# try:
#     while True:
#         dist_cm = sensor.distance * 100
#         if dist_cm < STOP_DISTANCE_CM:
#             print(f'Obstacle at {dist_cm:.1f}cm — avoiding')
#             stop()
#             backward(0.3)
#             right(0.4)
#             forward()
#         time.sleep(0.1)
# except KeyboardInterrupt:
#     stop()
#     print('Stopped.')

print('HC-SR04 not connected yet. See TODO comments in this file.')
