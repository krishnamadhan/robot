#!/usr/bin/env python3
"""
4WD Motor Test — Interactive SSH Controller
Cosmo Robot Pet | Madhan Krishnamadhan
Two TB6612FNG drivers: #1=LEFT side, #2=RIGHT side

Controls:
  W = Forward    S = Backward
  A = Turn Left  D = Turn Right
  Q = Spin Left  E = Spin Right
  Z = Slow       X = Fast
  Space = Stop   K = Kill & quit
"""

import sys
import tty
import termios
import time
from gpiozero import PWMOutputDevice, DigitalOutputDevice

# ── PIN ASSIGNMENTS ────────────────────────────────────────────
# TB6612FNG #1 — LEFT SIDE
AIN1 = 17   # Front-left dir 1
AIN2 = 22   # Front-left dir 2
PWMA = 18   # Front-left speed (HW PWM0)
BIN1 = 23   # Rear-left dir 1
BIN2 = 6    # Rear-left dir 2
PWMB = 13   # Rear-left speed (HW PWM1)

# TB6612FNG #2 — RIGHT SIDE
CIN1 = 19   # Front-right dir 1
CIN2 = 10   # Front-right dir 2
PWMC = 12   # Front-right speed
DIN1 = 14   # Rear-right dir 1
DIN2 = 15   # Rear-right dir 2
PWMD = 11   # Rear-right speed

STBY = 27   # Shared enable — both chips

# ── HARDWARE INIT ──────────────────────────────────────────────
print("Initialising motors...")

stby   = DigitalOutputDevice(STBY, initial_value=False)  # Motors OFF

# Left side (TB1)
ain1   = DigitalOutputDevice(AIN1)
ain2   = DigitalOutputDevice(AIN2)
pwm_a  = PWMOutputDevice(PWMA, frequency=1000)
bin1   = DigitalOutputDevice(BIN1)
bin2   = DigitalOutputDevice(BIN2)
pwm_b  = PWMOutputDevice(PWMB, frequency=1000)

# Right side (TB2)
cin1   = DigitalOutputDevice(CIN1)
cin2   = DigitalOutputDevice(CIN2)
pwm_c  = PWMOutputDevice(PWMC, frequency=1000)
din1   = DigitalOutputDevice(DIN1)
din2   = DigitalOutputDevice(DIN2)
pwm_d  = PWMOutputDevice(PWMD, frequency=1000)

print("Hardware ready. Enabling STBY...")
time.sleep(0.5)
stby.on()
print("Motors ENABLED.\n")

# ── SPEED SETTING ──────────────────────────────────────────────
SPEED = 0.55    # Default speed
SLOW  = 0.35
FAST  = 0.75
TURN  = 0.45

# ── MOTOR PRIMITIVES ───────────────────────────────────────────
def left(speed: float):
    """
    Set left side (both left motors) speed and direction.
    +speed = forward, -speed = backward, 0 = stop.
    Always set OFF pin before ON pin — prevents H-bridge short.
    """
    speed = max(-1.0, min(1.0, speed))
    if speed > 0:
        # Forward
        ain2.off(); ain1.on(); pwm_a.value = speed
        bin2.off(); bin1.on(); pwm_b.value = speed
    elif speed < 0:
        # Backward
        ain1.off(); ain2.on(); pwm_a.value = abs(speed)
        bin1.off(); bin2.on(); pwm_b.value = abs(speed)
    else:
        # Stop
        ain1.off(); ain2.off(); pwm_a.value = 0
        bin1.off(); bin2.off(); pwm_b.value = 0


def right(speed: float):
    """
    Set right side (both right motors) speed and direction.
    +speed = forward, -speed = backward, 0 = stop.
    """
    speed = max(-1.0, min(1.0, speed))
    if speed > 0:
        cin2.off(); cin1.on(); pwm_c.value = speed
        din2.off(); din1.on(); pwm_d.value = speed
    elif speed < 0:
        cin1.off(); cin2.on(); pwm_c.value = abs(speed)
        din1.off(); din2.on(); pwm_d.value = abs(speed)
    else:
        cin1.off(); cin2.off(); pwm_c.value = 0
        din1.off(); din2.off(); pwm_d.value = 0


def stop():
    left(0)
    right(0)


def kill():
    stop()
    stby.off()
    print("\nMotors DISABLED. STBY LOW. Goodbye!")


# ── MOVEMENT COMMANDS ──────────────────────────────────────────
def forward(spd=None):
    s = spd or SPEED
    left(s); right(s)

def backward(spd=None):
    s = spd or SPEED
    left(-s); right(-s)

def turn_left(spd=None):
    """Gentle left turn — left slows, right stays."""
    s = spd or TURN
    left(s * 0.3); right(s)

def turn_right(spd=None):
    """Gentle right turn — right slows, left stays."""
    s = spd or TURN
    left(s); right(s * 0.3)

def spin_left(spd=None):
    """Spin in place left — left back, right forward."""
    s = spd or TURN
    left(-s); right(s)

def spin_right(spd=None):
    """Spin in place right — left forward, right back."""
    s = spd or TURN
    left(s); right(-s)


# ── DISPLAY ────────────────────────────────────────────────────
def show_status(key: str, action: str):
    spd_bar = "█" * int(SPEED * 10) + "░" * (10 - int(SPEED * 10))
    print(f"\r[{key}] {action:<20} Speed: [{spd_bar}] {int(SPEED*100)}%   ",
          end="", flush=True)


def print_controls():
    print("""
╔══════════════════════════════════════╗
║    COSMO 4WD — SSH MOTOR TEST        ║
╠══════════════════════════════════════╣
║  W = Forward     S = Backward        ║
║  A = Turn Left   D = Turn Right      ║
║  Q = Spin Left   E = Spin Right      ║
║  Z = Slow mode   X = Fast mode       ║
║  Space = Stop    K = Kill + Quit     ║
╠══════════════════════════════════════╣
║  If motor spins WRONG direction:     ║
║  Swap its 2 wires at TB terminal     ║
║  No code change needed               ║
╚══════════════════════════════════════╝
""")


# ── MAIN INTERACTIVE LOOP ──────────────────────────────────────
def main():
    global SPEED

    print_controls()
    print("Ready! Press a key...\n")

    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    try:
        tty.setraw(fd)

        while True:
            ch = sys.stdin.read(1).lower()

            if ch == 'w':
                forward()
                show_status('W', 'FORWARD')

            elif ch == 's':
                backward()
                show_status('S', 'BACKWARD')

            elif ch == 'a':
                turn_left()
                show_status('A', 'TURN LEFT')

            elif ch == 'd':
                turn_right()
                show_status('D', 'TURN RIGHT')

            elif ch == 'q':
                spin_left()
                show_status('Q', 'SPIN LEFT')

            elif ch == 'e':
                spin_right()
                show_status('E', 'SPIN RIGHT')

            elif ch == ' ':
                stop()
                show_status('SPC', 'STOP')

            elif ch == 'z':
                SPEED = SLOW
                stop()
                show_status('Z', 'SLOW MODE')

            elif ch == 'x':
                SPEED = FAST
                stop()
                show_status('X', 'FAST MODE')

            elif ch == 'k':
                break

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        kill()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        termios.tcsetattr(sys.stdin.fileno(),
                          termios.TCSADRAIN,
                          termios.tcgetattr(sys.stdin.fileno()))
        kill()
