import os
os.environ['GPIOZERO_PIN_FACTORY'] = 'lgpio'
from gpiozero import PWMOutputDevice, DigitalOutputDevice
import time

# Shared STBY — both boards
stby = DigitalOutputDevice(27, initial_value=False)

# Board 1 — LEFT side
lf_ain1 = DigitalOutputDevice(17)
lf_ain2 = DigitalOutputDevice(22)
lf_pwm  = PWMOutputDevice(12, frequency=1000)   # Left Front

lr_bin1 = DigitalOutputDevice(23)
lr_bin2 = DigitalOutputDevice(6)
lr_pwm  = PWMOutputDevice(13, frequency=1000)   # Left Rear

# Board 2 — RIGHT side
rf_ain1 = DigitalOutputDevice(20)
rf_ain2 = DigitalOutputDevice(21)
rf_pwm  = PWMOutputDevice(18, frequency=1000)   # Right Front

rr_bin1 = DigitalOutputDevice(25)
rr_bin2 = DigitalOutputDevice(26)
rr_pwm  = PWMOutputDevice(19, frequency=1000)   # Right Rear


def stop_all():
    for d in (lf_ain1, lf_ain2, lr_bin1, lr_bin2,
              rf_ain1, rf_ain2, rr_bin1, rr_bin2):
        d.off()
    for p in (lf_pwm, lr_pwm, rf_pwm, rr_pwm):
        p.value = 0


def left_front(speed=0.5, fwd=True):
    if fwd: lf_ain2.off(); lf_ain1.on()
    else:   lf_ain1.off(); lf_ain2.on()
    lf_pwm.value = speed

def left_rear(speed=0.5, fwd=True):
    if fwd: lr_bin2.off(); lr_bin1.on()
    else:   lr_bin1.off(); lr_bin2.on()
    lr_pwm.value = speed

def right_front(speed=0.5, fwd=True):
    if fwd: rf_ain2.off(); rf_ain1.on()
    else:   rf_ain1.off(); rf_ain2.on()
    rf_pwm.value = speed

def right_rear(speed=0.5, fwd=True):
    if fwd: rr_bin2.off(); rr_bin1.on()
    else:   rr_bin1.off(); rr_bin2.on()
    rr_pwm.value = speed

def all_forward(speed=0.5):
    left_front(speed); left_rear(speed)
    right_front(speed); right_rear(speed)

def all_backward(speed=0.5):
    left_front(speed, fwd=False); left_rear(speed, fwd=False)
    right_front(speed, fwd=False); right_rear(speed, fwd=False)

def turn_left(speed=0.5):
    left_front(speed, fwd=False); left_rear(speed, fwd=False)
    right_front(speed); right_rear(speed)

def turn_right(speed=0.5):
    left_front(speed); left_rear(speed)
    right_front(speed, fwd=False); right_rear(speed, fwd=False)


print("=== 4WD MOTOR TEST ===")
print("Enabling STBY...")
stby.on()
time.sleep(0.5)

tests = [
    ("Left Front only — forward",   lambda: left_front(0.6)),
    ("Left Rear only  — forward",   lambda: left_rear(0.6)),
    ("Right Front only — forward",  lambda: right_front(0.6)),
    ("Right Rear only  — forward",  lambda: right_rear(0.6)),
    ("ALL FORWARD",                 lambda: all_forward(0.6)),
    ("ALL BACKWARD",                lambda: all_backward(0.6)),
    ("TURN LEFT  (pivot)",          lambda: turn_left(0.5)),
    ("TURN RIGHT (pivot)",          lambda: turn_right(0.5)),
]

for label, fn in tests:
    print(f"\n{label}...")
    fn()
    time.sleep(2)
    stop_all()
    time.sleep(0.5)

stby.off()
print("\n=== ALL TESTS COMPLETE ===")
print("Wrong direction on any motor? Swap that motor's two wires at the TB6612FNG terminal.")
