from gpiozero import PWMOutputDevice, DigitalOutputDevice
import sys
import tty
import termios

# =========================
# TB6612FNG SETUP
# =========================

stby  = DigitalOutputDevice(27, initial_value=False)

ain1  = DigitalOutputDevice(17)
ain2  = DigitalOutputDevice(22)

bin1  = DigitalOutputDevice(23)
bin2  = DigitalOutputDevice(6)

pwm_a = PWMOutputDevice(18, frequency=1000)
pwm_b = PWMOutputDevice(13, frequency=1000)

SPEED = 0.7

# =========================
# MOTOR CONTROL
# =========================

def stop_all():
    ain1.off()
    ain2.off()

    bin1.off()
    bin2.off()

    pwm_a.value = 0
    pwm_b.value = 0

def left_forward(speed=SPEED):
    ain2.off()
    ain1.on()
    pwm_a.value = speed

def left_backward(speed=SPEED):
    ain1.off()
    ain2.on()
    pwm_a.value = speed

def right_forward(speed=SPEED):
    bin2.off()
    bin1.on()
    pwm_b.value = speed

def right_backward(speed=SPEED):
    bin1.off()
    bin2.on()
    pwm_b.value = speed

# =========================
# MOVEMENTS
# =========================

def forward():
    left_forward()
    right_forward()

def backward():
    left_backward()
    right_backward()

def left():
    left_backward(0.5)
    right_forward(0.5)

def right():
    left_forward(0.5)
    right_backward(0.5)

# =========================
# KEYBOARD INPUT
# =========================

def getch():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setraw(sys.stdin.fileno())
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return ch

# =========================
# MAIN
# =========================

print("\n=== SSH MANUAL DRIVE MODE ===")
print("Controls:")
print("  W = Forward")
print("  S = Backward")
print("  A = Left")
print("  D = Right")
print("  SPACE = Stop")
print("  Q = Quit")
print("\nRobot control active...\n")

stby.on()
stop_all()

try:
    while True:
        key = getch().lower()

        if key == 'w':
            stop_all()
            forward()
            print("FORWARD")

        elif key == 's':
            stop_all()
            backward()
            print("BACKWARD")

        elif key == 'a':
            stop_all()
            left()
            print("LEFT")

        elif key == 'd':
            stop_all()
            right()
            print("RIGHT")

        elif key == ' ':
            stop_all()
            print("STOP")

        elif key == 'q':
            print("EXITING...")
            break

finally:
    stop_all()
    stby.off()

    print("Motors stopped.")
    print("STBY disabled.")
