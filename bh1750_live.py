#!/usr/bin/env python3
"""BH1750 Live Light Meter — cover it, shine torch at it, put near window"""
import smbus2
from smbus2 import i2c_msg
import time

bus = smbus2.SMBus(1)
bus.write_byte(0x23, 0x01)   # Power on
time.sleep(0.01)
bus.write_byte(0x23, 0x10)   # Continuous high-res mode
time.sleep(0.18)

def read_lux():
    msg = i2c_msg.read(0x23, 2)
    bus.i2c_rdwr(msg)
    data = list(msg)
    return (data[0] << 8 | data[1]) / 1.2

def label(lux):
    if lux < 1:     return "PITCH DARK       ", "████████░░░░░░░░░░░░"
    if lux < 10:    return "VERY DARK        ", "███████░░░░░░░░░░░░░"
    if lux < 50:    return "DIM (indoor)     ", "████░░░░░░░░░░░░░░░░"
    if lux < 200:   return "NORMAL indoor    ", "██████░░░░░░░░░░░░░░"
    if lux < 1000:  return "BRIGHT indoor    ", "████████████░░░░░░░░"
    if lux < 10000: return "VERY BRIGHT      ", "████████████████░░░░"
    return              "SUNLIGHT / TORCH ", "████████████████████"

def bar(lux):
    import math
    pct = min(1.0, math.log10(max(lux, 0.1) + 1) / 5)
    filled = int(pct * 30)
    return '█' * filled + '░' * (30 - filled)

history = []
print("\033[2J", flush=True)

try:
    while True:
        lux = read_lux()
        history.append(lux)
        if len(history) > 20: history.pop(0)
        avg = sum(history) / len(history)
        trend = "↑ rising " if len(history) > 3 and history[-1] > history[-4] else \
                "↓ falling" if len(history) > 3 and history[-1] < history[-4] else \
                "→ steady "
        desc, _ = label(lux)

        print("\033[H", end="", flush=True)
        print("=" * 46)
        print("   BH1750 LIGHT SENSOR LIVE  —  Ctrl+C stop")
        print("=" * 46)
        print(f"\n  {lux:>8.1f} lux   {trend}")
        print(f"\n  {bar(lux)}")
        print(f"\n  Scene:   {desc}")
        print(f"  Avg:     {avg:.1f} lux (last {len(history)} reads)")
        print(f"\n  Try: cover sensor / shine torch / near window")
        print("=" * 46, flush=True)
        time.sleep(0.2)

except KeyboardInterrupt:
    print("\nStopped.")
