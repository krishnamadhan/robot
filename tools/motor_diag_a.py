#!/usr/bin/env python3
"""
Left Front (Channel A) raw pin diagnostic.

Holds each pin HIGH for 3s so you can probe with multimeter.
Then drives Channel A at 100% duty (no PWM threading) to isolate faults.

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
    print(f"GPIO import failed: {e}")
    sys.exit(1)

STBY  = 27
IN1   = 17   # AIN1 — Left Front direction 1
IN2   = 22   # AIN2 — Left Front direction 2
PWM_A = 11   # PWMA — Left Front speed (remapped from GPIO12 — Pin 32 faulty)

HOLD_S = 3   # seconds to hold each pin HIGH

def claim(pin: int, name: str):
    try:
        d = DigitalOutputDevice(pin)
        d.off()
        print(f"  GPIO{pin:2d}  ({name})  claimed OK")
        return d
    except Exception as e:
        print(f"  GPIO{pin:2d}  ({name})  FAILED: {e}")
        return None

print("\n=== Channel A Pin Diagnostic ===\n")
print("Claiming all Channel A + STBY pins...")

stby = claim(STBY,  "STBY")
in1  = claim(IN1,   "AIN1")
in2  = claim(IN2,   "AIN2")
pwm  = claim(PWM_A, "PWMA")

failed = [n for d, n in [(stby,"STBY"),(in1,"AIN1"),(in2,"AIN2"),(pwm,"PWMA")] if d is None]
if failed:
    print(f"\nFailed to claim: {failed}")
    print("These pins are busy — check pm2 stop cosmo was run, or there's a wiring short.")
    sys.exit(1)

print("\nAll pins claimed. Starting individual tests...\n")
input("Press Enter to begin (watch multimeter / LED on each pin)...")

# ── Step 1: Test each pin individually ────────────────────────────────────────
for pin_dev, num, name in [(stby, STBY, "STBY"), (in1, IN1, "AIN1"),
                            (in2, IN2, "AIN2"), (pwm, PWM_A, "PWMA")]:
    input(f"\n  GPIO{num} ({name}) → press Enter to go HIGH (probe now)...")
    pin_dev.on()
    input(f"  GPIO{num} ({name})   HIGH *** probe now *** — press Enter to go LOW")
    pin_dev.off()
    print(f"  GPIO{num} ({name})   LOW")

# ── Step 2: Full forward at 100% duty (no SoftPWM) ────────────────────────────
print("\n--- TEST: Full forward 100% (STBY↑ AIN1↑ AIN2↓ PWMA↑) ---")
input("Press Enter to start motor FORWARD (press Enter again to stop)...")

stby.on()
time.sleep(0.01)
in2.off()
in1.on()
pwm.on()   # 100% duty — no PWM, just full ON
input("  *** MOTOR RUNNING *** — press Enter to stop")
pwm.off(); in1.off(); in2.off()
stby.off()
print("  stopped")

result = input("\nDid the motor spin? (y/n): ").strip().lower()

if result == "y":
    print("\n✓ Channel A GPIO pins are working at 100% duty.")
    print("  Issue is likely the SoftPWM at low duty (20%) not enough to overcome stiction.")
    print("  Fix: increase default speed step or minimum speed in motor_test_left.py.")
elif result == "n":
    print("\n✗ Motor did not spin at 100% duty.")
    print("\nChecklist:")
    print("  1. Is STBY wired to GPIO27 (Pin 13)?")
    print("     → Probe Pin 13 — should go HIGH during test")
    print("  2. Is AIN1 wired to GPIO17 (Pin 11)?")
    print("     → Was HIGH for 3s in step 1 — did multimeter show 3.3V?")
    print("  3. Is PWMA wired to GPIO12 (Pin 32)?")
    print("     → Was HIGH for 3s in step 1 — did multimeter show 3.3V?")
    print("  4. Is VM connected to motor battery? (7.4V LiPo or 4×AA)")
    print("     → TB6612FNG needs motor power on VM, not just VCC 3.3V")
    print("  5. Is motor wired to AO1/AO2 on the chip (not BO1/BO2)?")

# ── Step 3: Full reverse at 100% ──────────────────────────────────────────────
print("\n--- TEST: Full reverse 100% (AIN1↓ AIN2↑ PWMA↑) ---")
do_rev = input("Test reverse too? (y/n): ").strip().lower()
if do_rev == "y":
    stby.on()
    time.sleep(0.01)
    in1.off()
    in2.on()
    pwm.on()
    input("  *** MOTOR RUNNING REVERSE *** — press Enter to stop")
    pwm.off(); in1.off(); in2.off(); stby.off()
    print("  stopped")

# ── Cleanup ────────────────────────────────────────────────────────────────────
for d in [stby, in1, in2, pwm]:
    d.off()
    d.close()

print("\nDone. All pins released. Run: pm2 start cosmo")
