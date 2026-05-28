#!/usr/bin/env python3
"""
Right-side (Channel B) raw pin diagnostic. Enter-gated — holds each pin
until you press Enter so you can probe or swap wires at your own pace.

Run with cosmo stopped: pm2 stop cosmo
"""

import sys
import time

try:
    from gpiozero import DigitalOutputDevice
    from gpiozero.pins.lgpio import LGPIOFactory
    from gpiozero import Device
    Device.pin_factory = LGPIOFactory()
except Exception as e:
    print(f"GPIO import failed: {e}"); sys.exit(1)

STBY  = 27
IN1   = 20   # AIN1 — Right Front
IN2   = 24   # AIN2 — Right Front (remapped: GPIO21 dead → GPIO14 UART → GPIO5 dead → GPIO24 Pin 18)
PWM_A = 18   # PWMA — Right Front

IN3   = 25   # BIN1 — Right Rear
IN4   = 26   # BIN2 — Right Rear
PWM_B =  9   # PWMB — Right Rear  (remapped from GPIO19 — Pin 35 faulty)

def claim(pin, name):
    try:
        d = DigitalOutputDevice(pin); d.off()
        print(f"  GPIO{pin:2d}  ({name})  OK")
        return d
    except Exception as e:
        print(f"  GPIO{pin:2d}  ({name})  FAILED: {e}")
        return None

print("\n=== Channel B (Right Side) Pin Diagnostic ===\n")
print("Claiming all right-side + STBY pins...")

stby = claim(STBY, "STBY")
in1  = claim(IN1,  "RF_AIN1")
in2  = claim(IN2,  "RF_AIN2")
pwma = claim(PWM_A,"RF_PWMA")
in3  = claim(IN3,  "RR_BIN1")
in4  = claim(IN4,  "RR_BIN2")
pwmb = claim(PWM_B,"RR_PWMB")

failed = [n for d,n in [(stby,"STBY"),(in1,"RF_AIN1"),(in2,"RF_AIN2"),
                         (pwma,"RF_PWMA"),(in3,"RR_BIN1"),(in4,"RR_BIN2"),
                         (pwmb,"RR_PWMB")] if d is None]
if failed:
    print(f"\nFailed: {failed} — check pm2 stop cosmo, or wiring short")
    sys.exit(1)

print("\nAll pins claimed.\n")
input("Press Enter to begin individual pin tests...")

# ── Step 1: Each pin HIGH until Enter ─────────────────────────────────────────
for dev, num, name in [
    (stby, STBY,  "STBY"),
    (in1,  IN1,   "RF_AIN1 (Right Front dir1)"),
    (in2,  IN2,   "RF_AIN2 (Right Front dir2)"),
    (pwma, PWM_A, "RF_PWMA (Right Front speed)"),
    (in3,  IN3,   "RR_BIN1 (Right Rear dir1)"),
    (in4,  IN4,   "RR_BIN2 (Right Rear dir2)"),
    (pwmb, PWM_B, "RR_PWMB (Right Rear speed)"),
]:
    input(f"\n  GPIO{num:2d} ({name}) → Enter to go HIGH...")
    dev.on()
    input(f"  GPIO{num:2d}  *** HIGH — probe now *** → Enter to go LOW")
    dev.off()
    print(f"  GPIO{num:2d}  LOW")

# ── Step 2: Right Front full forward ──────────────────────────────────────────
print("\n--- TEST: Right Front full forward (AIN1↑ AIN2↓ PWMA↑) ---")
input("Press Enter to start (Enter again to stop)...")
stby.on(); time.sleep(0.01)
in2.off(); in1.on(); pwma.on()
input("  *** RIGHT FRONT RUNNING *** → Enter to stop")
pwma.off(); in1.off(); in2.off(); stby.off()
print("  stopped")

r1 = input("Did Right Front spin? (y/n): ").strip().lower()

# ── Step 3: Right Rear full forward ───────────────────────────────────────────
print("\n--- TEST: Right Rear full forward (BIN1↑ BIN2↓ PWMB↑) ---")
input("Press Enter to start (Enter again to stop)...")
stby.on(); time.sleep(0.01)
in4.off(); in3.on(); pwmb.on()
input("  *** RIGHT REAR RUNNING *** → Enter to stop")
pwmb.off(); in3.off(); in4.off(); stby.off()
print("  stopped")

r2 = input("Did Right Rear spin? (y/n): ").strip().lower()

# ── Step 4: Both reverse ──────────────────────────────────────────────────────
do_rev = input("\nTest both reverse? (y/n): ").strip().lower()
if do_rev == "y":
    stby.on(); time.sleep(0.01)
    in1.off(); in2.on(); pwma.on()
    in3.off(); in4.on(); pwmb.on()
    input("  *** BOTH RUNNING REVERSE *** → Enter to stop")
    pwma.off(); pwmb.off()
    in1.off(); in2.off(); in3.off(); in4.off()
    stby.off()
    print("  stopped")

# ── Summary ────────────────────────────────────────────────────────────────────
print(f"\nRight Front: {'✓' if r1=='y' else '✗ — check GPIO20/21/18 wiring and VM power'}")
print(f"Right Rear:  {'✓' if r2=='y' else '✗ — check GPIO25/26/19 wiring and VM power'}")

for d in [stby, in1, in2, pwma, in3, in4, pwmb]:
    d.off(); d.close()

print("\nAll pins released. Run: pm2 start cosmo")
