#!/usr/bin/env python3
"""W/A/S/D keyboard control for 2WD robot over SSH. Press Q to quit."""
import sys
import tty
import termios
from move import forward, backward, left, right, stop, cleanup

HELP = """
Robot Keyboard Control
======================
  W  — Forward
  S  — Backward
  A  — Turn left
  D  — Turn right
  Space / X — Stop
  Q  — Quit
"""

def get_key():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

print(HELP)
current_action = None

try:
    while True:
        key = get_key().lower()

        if key == 'w':
            if current_action != 'forward':
                forward()
                current_action = 'forward'
                print('\r[FORWARD]  ', end='', flush=True)
        elif key == 's':
            if current_action != 'backward':
                backward()
                current_action = 'backward'
                print('\r[BACKWARD] ', end='', flush=True)
        elif key == 'a':
            if current_action != 'left':
                left()
                current_action = 'left'
                print('\r[LEFT]     ', end='', flush=True)
        elif key == 'd':
            if current_action != 'right':
                right()
                current_action = 'right'
                print('\r[RIGHT]    ', end='', flush=True)
        elif key in (' ', 'x'):
            stop()
            current_action = None
            print('\r[STOP]     ', end='', flush=True)
        elif key == 'q':
            print('\nQuitting...')
            break

except KeyboardInterrupt:
    print('\nInterrupted.')
finally:
    stop()
    cleanup()
    print('Motors stopped.')
