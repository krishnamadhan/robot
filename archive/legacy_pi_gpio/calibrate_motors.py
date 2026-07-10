#!/usr/bin/env python3
"""Interactive motor calibration — separate forward and backward trim.
   Q/A = left up/down  |  W/S = right up/down  (adjusts whichever direction is running)
   F = run both forward  |  B = run both backward
   L = left only  |  R = right only
   SPACE = stop  |  E = save & exit
"""
import curses, time, json, os
from gpiozero import PWMOutputDevice, DigitalOutputDevice

CALIB_FILE = "/home/pi/robot/calibration.json"

stby  = DigitalOutputDevice(27, initial_value=False)
ain1  = DigitalOutputDevice(17)
ain2  = DigitalOutputDevice(22)
bin1  = DigitalOutputDevice(23)
bin2  = DigitalOutputDevice(6)
pwm_a = PWMOutputDevice(18, frequency=1000)
pwm_b = PWMOutputDevice(13, frequency=1000)

def stop():
    ain1.off(); ain2.off(); bin1.off(); bin2.off()
    pwm_a.value = 0; pwm_b.value = 0
    stby.off()

def both(l, r, fwd):
    stby.on()
    if fwd: ain2.off(); ain1.on(); bin2.off(); bin1.on()
    else:   ain1.off(); ain2.on(); bin1.off(); bin2.on()
    pwm_a.value = l; pwm_b.value = r

def left_only(speed, fwd):
    stby.on()
    bin1.off(); bin2.off(); pwm_b.value = 0
    if fwd: ain2.off(); ain1.on()
    else:   ain1.off(); ain2.on()
    pwm_a.value = speed

def right_only(speed, fwd):
    stby.on()
    ain1.off(); ain2.off(); pwm_a.value = 0
    if fwd: bin2.off(); bin1.on()
    else:   bin1.off(); bin2.on()
    pwm_b.value = speed

def bar(val, width=18):
    n = int(round(val * width))
    return "█" * n + "░" * (width - n)

def trim_label(l, r):
    d = l - r
    if abs(d) < 0.005: return "balanced ✓"
    return f"L faster +{d:.2f}" if d > 0 else f"R faster +{-d:.2f}"

def main(scr):
    curses.curs_set(0); scr.nodelay(True)
    curses.start_color(); curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN,  -1)
    curses.init_pair(2, curses.COLOR_RED,    -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_CYAN,   -1)

    # defaults — load existing calibration if present
    fl, fr = 0.60, 0.82   # forward
    bl, br = 0.60, 0.82   # backward
    if os.path.exists(CALIB_FILE):
        try:
            d = json.load(open(CALIB_FILE))
            fl = d.get("fwd_left",  d.get("left_speed",  0.60))
            fr = d.get("fwd_right", d.get("right_speed", 0.82))
            bl = d.get("bck_left",  fl)
            br = d.get("bck_right", fr)
        except Exception: pass

    mode   = "STOPPED"
    is_fwd = True          # which direction is currently active
    note   = "Loaded existing calibration." if os.path.exists(CALIB_FILE) else "Defaults loaded."
    STEP   = 0.01

    while True:
        scr.erase()
        scr.addstr(0, 2, "━" * 48, curses.color_pair(4))
        scr.addstr(1, 2, "    MOTOR CALIBRATION  (fwd + back)", curses.color_pair(4)|curses.A_BOLD)
        scr.addstr(2, 2, "━" * 48, curses.color_pair(4))

        # active direction label
        editing = "FORWARD" if is_fwd else "BACKWARD"
        ec = curses.color_pair(1) if is_fwd else curses.color_pair(2)
        scr.addstr(3, 2, f"  Editing: {editing}  (F/B to switch)", ec | curses.A_BOLD)

        # forward row
        fc = curses.color_pair(1) if is_fwd else curses.color_pair(5)
        scr.addstr(4, 2, f"  FWD  L {bar(fl)} {fl:.2f}   R {bar(fr)} {fr:.2f}   {trim_label(fl,fr)}", fc)
        # backward row
        bc = curses.color_pair(2) if not is_fwd else curses.color_pair(5)
        scr.addstr(5, 2, f"  BCK  L {bar(bl)} {bl:.2f}   R {bar(br)} {br:.2f}   {trim_label(bl,br)}", bc)

        scr.addstr(7,  2, "  Q/A = left ▲▼   W/S = right ▲▼")
        scr.addstr(8,  2, "  F = fwd run   B = back run   L/R = one motor")
        scr.addstr(9,  2, "  SPACE = stop   E = save & exit")
        scr.addstr(10, 2, "─" * 48)

        mc = curses.color_pair(1) if mode != "STOPPED" else curses.color_pair(2)
        scr.addstr(11, 2, f"  {mode:<20}", mc | curses.A_BOLD)
        scr.addstr(12, 2, f"  {note:<46}", curses.color_pair(3))
        scr.refresh()

        key = scr.getch()
        reapply = False

        if key in (ord('q'), ord('Q')):
            if is_fwd: fl = min(1.0, round(fl + STEP, 2))
            else:      bl = min(1.0, round(bl + STEP, 2))
            reapply = True
        elif key in (ord('a'), ord('A')):
            if is_fwd: fl = max(0.0, round(fl - STEP, 2))
            else:      bl = max(0.0, round(bl - STEP, 2))
            reapply = True
        elif key in (ord('w'), ord('W')):
            if is_fwd: fr = min(1.0, round(fr + STEP, 2))
            else:      br = min(1.0, round(br + STEP, 2))
            reapply = True
        elif key in (ord('s'), ord('S')):
            if is_fwd: fr = max(0.0, round(fr - STEP, 2))
            else:      br = max(0.0, round(br - STEP, 2))
            reapply = True
        elif key in (ord('f'), ord('F')):
            mode = "BOTH FORWARD"; is_fwd = True;  both(fl, fr, True)
        elif key in (ord('b'), ord('B')):
            mode = "BOTH BACKWARD"; is_fwd = False; both(bl, br, False)
        elif key in (ord('l'), ord('L')):
            mode = "LEFT ONLY"
            left_only(fl if is_fwd else bl, is_fwd)
        elif key in (ord('r'), ord('R')):
            mode = "RIGHT ONLY"
            right_only(fr if is_fwd else br, is_fwd)
        elif key == ord(' '):
            mode = "STOPPED"; stop()
        elif key in (ord('e'), ord('E')):
            stop()
            data = {
                "fwd_left": fl, "fwd_right": fr,
                "bck_left": bl, "bck_right": br,
                "fwd_trim": round(fl - fr, 3),
                "bck_trim": round(bl - br, 3),
            }
            json.dump(data, open(CALIB_FILE, "w"), indent=2)
            note = f"Saved! fwd L={fl} R={fr}  bck L={bl} R={br}"
            mode = "STOPPED"
            scr.addstr(12, 2, f"  {note:<46}", curses.color_pair(1))
            scr.refresh(); time.sleep(1.5); break

        if reapply:
            l = fl if is_fwd else bl
            r = fr if is_fwd else br
            if   mode == "BOTH FORWARD":  both(l, r, True)
            elif mode == "BOTH BACKWARD": both(l, r, False)
            elif mode == "LEFT ONLY":     left_only(l, is_fwd)
            elif mode == "RIGHT ONLY":    right_only(r, is_fwd)
            note = f"fwd L={fl} R={fr}   bck L={bl} R={br}"

        time.sleep(0.04)

try:
    curses.wrapper(main)
except KeyboardInterrupt:
    pass
finally:
    stop()
    print("Motors stopped.")
    if os.path.exists(CALIB_FILE):
        print("calibration.json:", open(CALIB_FILE).read())
