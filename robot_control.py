#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  ⚠️  DEPRECATED — DO NOT RUN  ⚠️                                         ║
# ║                                                                          ║
# ║  This script uses the OLD 2WD pinout:                                    ║
# ║    bin2  = GPIO6   ← BURNS FIT0992 UPS HAT (adapter-fail pin)           ║
# ║    pwm_a = GPIO18  ← Now right_front PWM in 4WD config                  ║
# ║    pwm_b = GPIO13  ← Now left_rear PWM in 4WD config                    ║
# ║                                                                          ║
# ║  Use hardware/motors.py via tools/motor_test.py instead.                 ║
# ║  See config/hardware.yaml for the current 4WD GPIO map.                  ║
# ╚══════════════════════════════════════════════════════════════════════════╝
"""Keyboard robot control — DEPRECATED, see warning above."""
import sys
print("ERROR: robot_control.py is deprecated and uses a dangerously wrong pinout.")
print("       Run tools/motor_test.py instead.")
sys.exit(1)

import curses, json, os, time
from gpiozero import PWMOutputDevice, DigitalOutputDevice

CALIB_FILE = "/home/pi/robot/calibration.json"

stby  = DigitalOutputDevice(27, initial_value=False)
ain1  = DigitalOutputDevice(17)
ain2  = DigitalOutputDevice(22)
bin1  = DigitalOutputDevice(23)
bin2  = DigitalOutputDevice(6)   # WRONG: GPIO6 = UPS HAT adapter-fail pin
pwm_a = PWMOutputDevice(18, frequency=1000)  # WRONG: now right_front PWM
pwm_b = PWMOutputDevice(13, frequency=1000)  # WRONG: now left_rear PWM

def stop():
    ain1.off(); ain2.off(); bin1.off(); bin2.off()
    pwm_a.value = 0; pwm_b.value = 0
    stby.off()

def set_motors(l_pwm, r_pwm, l_fwd, r_fwd):
    stby.on()
    if l_fwd: ain2.off(); ain1.on()
    else:     ain1.off(); ain2.on()
    if r_fwd: bin2.off(); bin1.on()
    else:     bin1.off(); bin2.on()
    pwm_a.value = min(1.0, l_pwm)
    pwm_b.value = min(1.0, r_pwm)

def normalize(l, r):
    sc = 1.0 / max(l, r, 0.01)
    return round(l * sc, 3), round(r * sc, 3)

def load_calib():
    if os.path.exists(CALIB_FILE):
        try:
            d = json.load(open(CALIB_FILE))
            fl = d.get("fwd_left",  d.get("left_speed",  0.6))
            fr = d.get("fwd_right", d.get("right_speed", 0.6))
            bl = d.get("bck_left",  fl)
            br = d.get("bck_right", fr)
            return normalize(fl, fr), normalize(bl, br), True
        except Exception:
            pass
    return (0.6, 0.6), (0.6, 0.6), False

def main(scr):
    curses.curs_set(0); scr.nodelay(True)
    curses.start_color(); curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN,  -1)
    curses.init_pair(2, curses.COLOR_RED,    -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_CYAN,   -1)

    (fl, fr), (bl, br), calib_ok = load_calib()
    calib_note = f"Calibration OK  fwd L={fl} R={fr}  bck L={bl} R={br}" \
                 if calib_ok else "No calibration file — equal speeds"

    spd   = 0.8
    t_spd = 0.5
    mode  = "STOPPED"

    while True:
        scr.erase()
        scr.addstr(0, 2, "━" * 46, curses.color_pair(4))
        scr.addstr(1, 2, "   ROBOT CONTROL  —  Q to quit", curses.color_pair(4)|curses.A_BOLD)
        scr.addstr(2, 2, "━" * 46, curses.color_pair(4))

        scr.addstr(4, 2, "         [W] Forward")
        scr.addstr(5, 2, "  [A] Left   [S] Back   [D] Right")
        scr.addstr(6, 2, "         [SPACE] Stop")
        scr.addstr(7, 2, "  [ = slower              ] = faster")

        col = curses.color_pair(1) if mode != "STOPPED" else curses.color_pair(2)
        scr.addstr(9,  2, f"  Direction : {mode:<14}", col | curses.A_BOLD)
        scr.addstr(10, 2, f"  Speed     : {int(spd*100):3d}%")
        scr.addstr(11, 2, f"  {calib_note}", curses.color_pair(3))
        scr.refresh()

        key = scr.getch()
        changed = False

        if key in (ord('w'), ord('W')):
            if mode != "FORWARD":  mode = "FORWARD";  changed = True
        elif key in (ord('s'), ord('S')):
            if mode != "BACKWARD": mode = "BACKWARD"; changed = True
        elif key in (ord('a'), ord('A')):
            if mode != "LEFT":     mode = "LEFT";     changed = True
        elif key in (ord('d'), ord('D')):
            if mode != "RIGHT":    mode = "RIGHT";    changed = True
        elif key == ord(' '):
            mode = "STOPPED"; stop()
        elif key == ord(']'):
            spd = min(1.0, round(spd + 0.05, 2)); changed = True
        elif key == ord('['):
            spd = max(0.1, round(spd - 0.05, 2)); changed = True
        elif key in (ord('q'), ord('Q')):
            stop(); break

        if changed:
            if mode == "FORWARD":
                set_motors(fl * spd, fr * spd, True,  True)
            elif mode == "BACKWARD":
                set_motors(bl * spd, br * spd, False, False)
            elif mode == "LEFT":
                set_motors(t_spd, t_spd, False, True)
            elif mode == "RIGHT":
                set_motors(t_spd, t_spd, True,  False)

        time.sleep(0.03)

try:
    curses.wrapper(main)
except KeyboardInterrupt:
    pass
finally:
    stop()
    print("Robot stopped.")
