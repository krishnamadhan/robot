#!/usr/bin/env python3
import smbus2, time, math, os

bus = smbus2.SMBus(1)
bus.write_byte_data(0x68, 0x6B, 0)
time.sleep(0.1)

def rw(r):
    h = bus.read_byte_data(0x68, r)
    l = bus.read_byte_data(0x68, r + 1)
    v = (h << 8) | l
    return v - 65536 if v > 32767 else v

def read():
    ax = rw(0x3B) / 16384.0
    ay = rw(0x3D) / 16384.0
    az = rw(0x3F) / 16384.0
    gx = rw(0x43) / 131.0
    gy = rw(0x45) / 131.0
    gz = rw(0x47) / 131.0
    return ax, ay, az, gx, gy, gz

def bar(val, lo=-2, hi=2, width=20):
    pct = (val - lo) / (hi - lo)
    pct = max(0, min(1, pct))
    filled = min(int(pct * width), width - 1)
    mid = width // 2
    b = ['-'] * width
    b[mid] = '|'
    if filled <= mid:
        for i in range(filled, mid): b[i] = '='
    else:
        for i in range(mid, filled): b[i] = '='
    b[filled] = '#'
    return ''.join(b)

def orientation(ax, ay, az):
    if az > 0.7:   return "FLAT (face up)     ", "  [===]  "
    if az < -0.7:  return "UPSIDE DOWN        ", "  [uuu]  "
    if ax < -0.5:  return "TILT LEFT  <<<<    ", "  <<<<   "
    if ax > 0.5:   return "TILT RIGHT >>>>    ", "  >>>>   "
    if ay > 0.5:   return "TILT FORWARD /     ", "  ////   "
    if ay < -0.5:  return "TILT BACK  \\      ", "  \\\\\\\\   "
    return         "BALANCED           ", "  [---]  "

prev_mag = 1.0
shake_count = 0
shake_msg = ""
shake_timer = 0

print("\033[2J", flush=True)

try:
    while True:
        ax, ay, az, gx, gy, gz = read()
        mag = math.sqrt(ax**2 + ay**2 + az**2)

        # Shake detection
        if abs(mag - prev_mag) > 0.8:
            shake_count += 1
            shake_msg = f"SHAKE! x{shake_count} 🫨"
            shake_timer = 8
        prev_mag = mag
        if shake_timer > 0:
            shake_timer -= 1
        else:
            shake_msg = ""

        label, icon = orientation(ax, ay, az)

        print("\033[H", end="", flush=True)
        print("=" * 44)
        print("   MPU-6050 LIVE  —  Ctrl+C to stop")
        print("=" * 44)
        print(f"\n  Orientation:  {label}")
        print(f"\n        {icon}\n")
        print(f"  AX {bar(ax):22s} {ax:+.2f}g")
        print(f"  AY {bar(ay):22s} {ay:+.2f}g")
        print(f"  AZ {bar(az, -1, 2):22s} {az:+.2f}g")
        print(f"\n  GX {bar(gx,-200,200):22s} {gx:+6.1f}°/s")
        print(f"  GY {bar(gy,-200,200):22s} {gy:+6.1f}°/s")
        print(f"  GZ {bar(gz,-200,200):22s} {gz:+6.1f}°/s")
        print(f"\n  Magnitude: {mag:.3f}g")
        print(f"  {shake_msg:<30}")
        print("=" * 44, flush=True)

        time.sleep(0.1)

except KeyboardInterrupt:
    print("\n\nStopped.")
