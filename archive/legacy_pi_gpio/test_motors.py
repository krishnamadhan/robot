import os
import signal
import sys
os.environ['GPIOZERO_PIN_FACTORY'] = 'lgpio'
from gpiozero import DigitalOutputDevice
import time

# PWM NOTE: GPIO12/18 share HW PWM0; GPIO13/19 share HW PWM1 on Pi 5 RP1.
# PWMOutputDevice at 1000Hz causes conflict when both are open simultaneously.
# Use software PWM (DigitalOutputDevice + manual toggle) so each pin is independent.

# Shared STBY — both boards
stby = DigitalOutputDevice(27, initial_value=False)

# Board 1 — LEFT side
lf_ain1 = DigitalOutputDevice(17)
lf_ain2 = DigitalOutputDevice(22)
lf_pwm  = DigitalOutputDevice(12)   # Left Front  — SW PWM below

lr_bin1 = DigitalOutputDevice(23)
lr_bin2 = DigitalOutputDevice(10)   # GPIO10 Pin 19 — MOVED from GPIO6 (HAT uses GPIO6 for adapter-fail detect)
lr_pwm  = DigitalOutputDevice(13)   # Left Rear

# Board 2 — RIGHT side
rf_ain1 = DigitalOutputDevice(20)
rf_ain2 = DigitalOutputDevice(21)
rf_pwm  = DigitalOutputDevice(18)   # Right Front

rr_bin1 = DigitalOutputDevice(25)
rr_bin2 = DigitalOutputDevice(26)
rr_pwm  = DigitalOutputDevice(19)   # Right Rear

MAX_SPEED  = 0.50   # 50% ceiling — safe for bench test
PWM_FREQ   = 100    # Hz — low frequency, minimal switching loss


def pwm_run(pin, duty, duration):
    """Bit-bang PWM on a DigitalOutputDevice for `duration` seconds."""
    duty = max(0.0, min(MAX_SPEED, duty))
    period = 1.0 / PWM_FREQ
    t_end = time.monotonic() + duration
    while time.monotonic() < t_end:
        pin.on()
        time.sleep(period * duty)
        pin.off()
        time.sleep(period * (1.0 - duty))


def stop_all():
    for d in (lf_ain1, lf_ain2, lr_bin1, lr_bin2,
              rf_ain1, rf_ain2, rr_bin1, rr_bin2):
        d.off()
    for p in (lf_pwm, lr_pwm, rf_pwm, rr_pwm):
        p.off()


def _safe_exit(*_):
    print("\n[INTERRUPT] stopping all motors and disabling STBY")
    stop_all()
    stby.off()
    sys.exit(0)

signal.signal(signal.SIGINT,  _safe_exit)
signal.signal(signal.SIGTERM, _safe_exit)


# ── Single-motor tests ────────────────────────────────────────────────────────

def test_left_front(speed=0.4, fwd=True, duration=1.0):
    if fwd: lf_ain2.off(); lf_ain1.on()
    else:   lf_ain1.off(); lf_ain2.on()
    pwm_run(lf_pwm, speed, duration)
    lf_ain1.off(); lf_ain2.off(); lf_pwm.off()

def test_left_rear(speed=0.4, fwd=True, duration=1.0):
    if fwd: lr_bin2.off(); lr_bin1.on()
    else:   lr_bin1.off(); lr_bin2.on()
    pwm_run(lr_pwm, speed, duration)
    lr_bin1.off(); lr_bin2.off(); lr_pwm.off()

def test_right_front(speed=0.4, fwd=True, duration=1.0):
    if fwd: rf_ain2.off(); rf_ain1.on()
    else:   rf_ain1.off(); rf_ain2.on()
    pwm_run(rf_pwm, speed, duration)
    rf_ain1.off(); rf_ain2.off(); rf_pwm.off()

def test_right_rear(speed=0.4, fwd=True, duration=1.0):
    if fwd: rr_bin2.off(); rr_bin1.on()
    else:   rr_bin1.off(); rr_bin2.on()
    pwm_run(rr_pwm, speed, duration)
    rr_bin1.off(); rr_bin2.off(); rr_pwm.off()


# ── Combined tests (WHEELS OFF GROUND ONLY) ──────────────────────────────────

def test_all_forward(speed=0.4, duration=1.5):
    lf_ain2.off(); lf_ain1.on()
    lr_bin2.off(); lr_bin1.on()
    rf_ain2.off(); rf_ain1.on()
    rr_bin2.off(); rr_bin1.on()
    t_end = time.monotonic() + duration
    period = 1.0 / PWM_FREQ
    d = max(0.0, min(MAX_SPEED, speed))
    while time.monotonic() < t_end:
        for p in (lf_pwm, lr_pwm, rf_pwm, rr_pwm): p.on()
        time.sleep(period * d)
        for p in (lf_pwm, lr_pwm, rf_pwm, rr_pwm): p.off()
        time.sleep(period * (1.0 - d))
    stop_all()

def test_all_backward(speed=0.4, duration=1.5):
    lf_ain1.off(); lf_ain2.on()
    lr_bin1.off(); lr_bin2.on()
    rf_ain1.off(); rf_ain2.on()
    rr_bin1.off(); rr_bin2.on()
    t_end = time.monotonic() + duration
    period = 1.0 / PWM_FREQ
    d = max(0.0, min(MAX_SPEED, speed))
    while time.monotonic() < t_end:
        for p in (lf_pwm, lr_pwm, rf_pwm, rr_pwm): p.on()
        time.sleep(period * d)
        for p in (lf_pwm, lr_pwm, rf_pwm, rr_pwm): p.off()
        time.sleep(period * (1.0 - d))
    stop_all()

def test_pivot(left_fwd=False, speed=0.35, duration=1.0):
    """Pivot turn — ONLY run with wheels off the ground."""
    lf_ain2.off() if left_fwd else lf_ain1.off()
    lf_ain1.on()  if left_fwd else lf_ain2.on()
    lr_bin2.off() if left_fwd else lr_bin1.off()
    lr_bin1.on()  if left_fwd else lr_bin2.on()
    rf_ain2.off() if not left_fwd else rf_ain1.off()
    rf_ain1.on()  if not left_fwd else rf_ain2.on()
    rr_bin2.off() if not left_fwd else rr_bin1.off()
    rr_bin1.on()  if not left_fwd else rr_bin2.on()
    t_end = time.monotonic() + duration
    period = 1.0 / PWM_FREQ
    d = max(0.0, min(MAX_SPEED, speed))
    while time.monotonic() < t_end:
        for p in (lf_pwm, lr_pwm, rf_pwm, rr_pwm): p.on()
        time.sleep(period * d)
        for p in (lf_pwm, lr_pwm, rf_pwm, rr_pwm): p.off()
        time.sleep(period * (1.0 - d))
    stop_all()


# ── Test runner ───────────────────────────────────────────────────────────────

print("=== 4WD MOTOR TEST ===")
print(f"Max speed: {MAX_SPEED:.0%} | PWM: {PWM_FREQ}Hz | WHEELS MUST BE OFF GROUND for pivot tests")
print()
print("Enabling STBY...")
stby.on()
time.sleep(0.3)

# Phase 1: single motor, forward only
single_tests = [
    ("Left Front  — forward 1s",  lambda: test_left_front(0.4,  fwd=True,  duration=1.0)),
    ("Left Rear   — forward 1s",  lambda: test_left_rear(0.4,   fwd=True,  duration=1.0)),
    ("Right Front — forward 1s",  lambda: test_right_front(0.4, fwd=True,  duration=1.0)),
    ("Right Rear  — forward 1s",  lambda: test_right_rear(0.4,  fwd=True,  duration=1.0)),
    ("Left Front  — backward 1s", lambda: test_left_front(0.4,  fwd=False, duration=1.0)),
    ("Left Rear   — backward 1s", lambda: test_left_rear(0.4,   fwd=False, duration=1.0)),
    ("Right Front — backward 1s", lambda: test_right_front(0.4, fwd=False, duration=1.0)),
    ("Right Rear  — backward 1s", lambda: test_right_rear(0.4,  fwd=False, duration=1.0)),
]

print("--- Phase 1: Single motor tests ---")
for label, fn in single_tests:
    print(f"  {label}...", end=" ", flush=True)
    fn()
    time.sleep(0.4)
    print("done")

print()
input("--- Phase 2: Combined tests. FLIP ROBOT UPSIDE DOWN now, then press Enter ---")
print()

combined_tests = [
    ("ALL FORWARD  1.5s", lambda: test_all_forward(0.4, 1.5)),
    ("ALL BACKWARD 1.5s", lambda: test_all_backward(0.4, 1.5)),
    ("PIVOT LEFT   1.0s", lambda: test_pivot(left_fwd=False, speed=0.35, duration=1.0)),
    ("PIVOT RIGHT  1.0s", lambda: test_pivot(left_fwd=True,  speed=0.35, duration=1.0)),
]

for label, fn in combined_tests:
    print(f"  {label}...", end=" ", flush=True)
    fn()
    time.sleep(0.5)
    print("done")

stby.off()
print()
print("=== ALL TESTS COMPLETE ===")
print("Wrong direction? Swap that motor's two output wires at the TB6612FNG terminal.")
print("Left/right mirrored wrong? Swap AIN1↔AIN2 (or BIN1↔BIN2) for that channel.")
