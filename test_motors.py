from gpiozero import PWMOutputDevice, DigitalOutputDevice
import time

# Setup — STBY starts LOW (motors disabled)
stby  = DigitalOutputDevice(27, initial_value=False)
ain1  = DigitalOutputDevice(17)
ain2  = DigitalOutputDevice(22)
bin1  = DigitalOutputDevice(23)
bin2  = DigitalOutputDevice(6)
pwm_a = PWMOutputDevice(18, frequency=1000)
pwm_b = PWMOutputDevice(13, frequency=1000)

def stop_all():
    ain1.off(); ain2.off()
    bin1.off(); bin2.off()
    pwm_a.value = 0
    pwm_b.value = 0

def left_forward(speed=0.5):
    ain2.off()   # OFF first — safety
    ain1.on()
    pwm_a.value = speed

def left_backward(speed=0.5):
    ain1.off()   # OFF first — safety
    ain2.on()
    pwm_a.value = speed

def right_forward(speed=0.5):
    bin2.off()   # OFF first — safety
    bin1.on()
    pwm_b.value = speed

def right_backward(speed=0.5):
    bin1.off()   # OFF first — safety
    bin2.on()
    pwm_b.value = speed

print("=== MOTOR TEST STARTING ===")
print("Enabling STBY...")
stby.on()
time.sleep(0.5)

print("\nTest 1: LEFT motor forward (2 sec)...")
left_forward(0.6)
time.sleep(2)
stop_all()
time.sleep(0.5)

print("Test 2: LEFT motor backward (2 sec)...")
left_backward(0.6)
time.sleep(2)
stop_all()
time.sleep(0.5)

print("Test 3: RIGHT motor forward (2 sec)...")
right_forward(0.6)
time.sleep(2)
stop_all()
time.sleep(0.5)

print("Test 4: RIGHT motor backward (2 sec)...")
right_backward(0.6)
time.sleep(2)
stop_all()
time.sleep(0.5)

print("Test 5: BOTH forward (2 sec)...")
left_forward(0.6)
right_forward(0.6)
time.sleep(2)
stop_all()
time.sleep(0.5)

print("Test 6: BOTH backward (2 sec)...")
left_backward(0.6)
right_backward(0.6)
time.sleep(2)
stop_all()
time.sleep(0.5)

print("Test 7: TURN LEFT (left back, right forward 1.5 sec)...")
left_backward(0.5)
right_forward(0.5)
time.sleep(1.5)
stop_all()
time.sleep(0.5)

print("Test 8: TURN RIGHT (left forward, right back 1.5 sec)...")
left_forward(0.5)
right_backward(0.5)
time.sleep(1.5)
stop_all()

stby.off()
print("\n=== ALL TESTS COMPLETE ===")
print("If any motor spun wrong direction:")
print("Swap that motor's two wires at TB6612FNG terminal")

