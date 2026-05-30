#!/usr/bin/env python3
"""
2WD WASD motor test — LEFT (GPIO17/22/11) + RIGHT (GPIO20/24/18).

    pm2 stop cosmo
    python3 tools/motor_test_2wd.py
    pm2 start cosmo

Keys:
    w / s     — Forward / Backward
    a / d     — Tank turn left / right (opposite wheels)
    q / e     — Gentle curve left / right (one wheel slower)
    Space     — Brake
    1-9 / 0   — Speed 10%-100%
    [ / ]     — Left motor trim −/+
    , / .     — Right motor trim −/+
    t         — Auto timed test (fwd, bwd, turn left, turn right)
    Ctrl-C    — Quit
"""

import select
import sys
import termios
import threading
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

STBY    = 27
L_IN1   = 17;  L_IN2  = 22;  L_PWM  = 11   # left motor  (left_front config)
R_IN1   = 20;  R_IN2  = 24;  R_PWM  = 18   # right motor (right_front config)

TRIM       = {"L": 1.00, "R": 1.00}
_DEADZONE  = 0.18
_MAX_DUTY  = 0.75

try:
    from gpiozero import DigitalOutputDevice
    from gpiozero.pins.lgpio import LGPIOFactory
    from gpiozero import Device
    Device.pin_factory = LGPIOFactory()
    _GPIO_OK = True
except Exception:
    _GPIO_OK = False

try:
    from rich.columns import Columns
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    _RICH = True
except ImportError:
    _RICH = False

console = Console() if _RICH else None


def _effective(speed: float, trim: float) -> float:
    if abs(speed) < 0.001:
        return 0.0
    sign = 1 if speed > 0 else -1
    s = _DEADZONE + abs(speed) * (1.0 - _DEADZONE)
    return sign * min(s * trim, _MAX_DUTY)


class _SoftPWM:
    _FREQ = 160

    def __init__(self, pin_dev):
        self._pin  = pin_dev
        self._duty = 0.0
        self._lock = threading.Lock()
        self._stop = False
        threading.Thread(target=self._run, daemon=True).start()

    def set(self, duty: float):
        with self._lock:
            self._duty = max(0.0, min(1.0, duty))

    def off(self):
        with self._lock:
            self._duty = 0.0
        self._pin.off()

    def _run(self):
        period = 1.0 / self._FREQ
        while not self._stop:
            with self._lock:
                d = self._duty
            if d <= 0.0:
                self._pin.off();  time.sleep(period)
            elif d >= 1.0:
                self._pin.on();   time.sleep(period)
            else:
                self._pin.on();   time.sleep(period * d)
                self._pin.off();  time.sleep(period * (1.0 - d))

    def close(self):
        self._stop = True
        self._pin.off()


class Motor:
    def __init__(self, in1: int, in2: int, pwm_pin: int, name: str):
        self.name  = name
        self.speed = 0.0
        if _GPIO_OK:
            self._in1 = DigitalOutputDevice(in1)
            self._in2 = DigitalOutputDevice(in2)
            self._pwm = _SoftPWM(DigitalOutputDevice(pwm_pin))
        else:
            self._in1 = self._in2 = self._pwm = None

    def drive(self, speed: float):
        speed      = max(-1.0, min(1.0, speed))
        self.speed = speed
        if not _GPIO_OK:
            return
        eff  = _effective(speed, TRIM[self.name])
        duty = abs(eff)
        if eff > 0:
            self._in2.off(); self._in1.on();  self._pwm.set(duty)
        elif eff < 0:
            self._in1.off(); self._in2.on();  self._pwm.set(duty)
        else:
            self._in1.off(); self._in2.off(); self._pwm.off()

    def brake(self):
        self.speed = 0.0
        if _GPIO_OK:
            self._in1.off(); self._in2.off(); self._pwm.off()

    def close(self):
        self.brake()
        if _GPIO_OK and self._pwm:
            self._pwm.close()
            self._in1.close(); self._in2.close()


_log:       list[str] = []
_set_speed: float     = 0.50


def _log_event(msg: str):
    _log.insert(0, f"[{time.strftime('%H:%M:%S')}] {msg}")
    del _log[12:]


def _bar(v: float, w: int = 12) -> str:
    n   = int(abs(v) * w)
    bar = "█" * n + "░" * (w - n)
    if v > 0:   return f"[green]▶ {bar} {v:+.0%}[/green]"
    elif v < 0: return f"[red]◀ {bar} {v:+.0%}[/red]"
    return f"[dim]  {'░'*w}   0%[/dim]"


def _build_ui(left: Motor, right: Motor) -> Panel:
    hw = "[green]REAL GPIO[/green]" if _GPIO_OK else "[red]MOCK[/red]"

    status = Table(show_header=False, box=None, padding=(0, 1))
    status.add_column("lbl", style="bold cyan", width=8)
    status.add_column("val", width=32)
    status.add_row("HW",    hw)
    status.add_row("Speed", f"[bold yellow]{_set_speed:.0%}[/bold yellow]  (1-9, 0=100%)")
    status.add_row("", "")
    status.add_row("LEFT",  _bar(left.speed)  + f"  [dim]trim={TRIM['L']:.2f}[/dim]")
    status.add_row("RIGHT", _bar(right.speed) + f"  [dim]trim={TRIM['R']:.2f}[/dim]")

    keys = Table(show_header=False, box=None, padding=(0, 1))
    keys.add_column("k", style="bold", width=8)
    keys.add_column("desc", style="dim", width=24)
    for row in [
        ("w / s",   "forward / backward"),
        ("a / d",   "tank turn left / right"),
        ("q / e",   "curve left / right"),
        ("Space",   "brake"),
        ("1-9 / 0", "set speed"),
        ("[ / ]",   "left trim −/+"),
        (", / .",   "right trim −/+"),
        ("t",       "auto timed test"),
        ("Ctrl-C",  "quit"),
    ]:
        keys.add_row(*row)

    log_t = Table(show_header=False, box=None, padding=(0, 1))
    log_t.add_column("entry", style="dim", width=32)
    for entry in _log[:10]:
        log_t.add_row(entry)

    return Panel(
        Columns([status, keys, log_t]),
        title="[bold cyan]2WD Motor Test  LEFT=GPIO17/22/11  RIGHT=GPIO20/24/18[/bold cyan]",
        border_style="cyan",
    )


def _handle_key(ch: str, left: Motor, right: Motor, enable_stby, timed_test):
    global _set_speed, TRIM

    if ch.isdigit():
        _set_speed = 1.0 if ch == "0" else int(ch) * 0.10
        _log_event(f"Speed → {_set_speed:.0%}")
        return
    if ch == "[":
        TRIM["L"] = max(0.5, round(TRIM["L"] - 0.02, 2))
        _log_event(f"L trim → {TRIM['L']:.2f}"); return
    if ch == "]":
        TRIM["L"] = min(1.5, round(TRIM["L"] + 0.02, 2))
        _log_event(f"L trim → {TRIM['L']:.2f}"); return
    if ch == ",":
        TRIM["R"] = max(0.5, round(TRIM["R"] - 0.02, 2))
        _log_event(f"R trim → {TRIM['R']:.2f}"); return
    if ch == ".":
        TRIM["R"] = min(1.5, round(TRIM["R"] + 0.02, 2))
        _log_event(f"R trim → {TRIM['R']:.2f}"); return

    spd = _set_speed
    if ch == "w":
        enable_stby()
        left.drive(spd);   right.drive(spd)
        _log_event(f"FWD {spd:.0%}")
    elif ch == "s":
        enable_stby()
        left.drive(-spd);  right.drive(-spd)
        _log_event(f"BWD {spd:.0%}")
    elif ch == "a":                                # tank left: left bwd, right fwd
        enable_stby()
        left.drive(-spd);  right.drive(spd)
        _log_event(f"TANK LEFT {spd:.0%}")
    elif ch == "d":                                # tank right: left fwd, right bwd
        enable_stby()
        left.drive(spd);   right.drive(-spd)
        _log_event(f"TANK RIGHT {spd:.0%}")
    elif ch == "q":                                # gentle left: right only
        enable_stby()
        left.drive(0);     right.drive(spd)
        _log_event(f"CURVE LEFT {spd:.0%}")
    elif ch == "e":                                # gentle right: left only
        enable_stby()
        left.drive(spd);   right.drive(0)
        _log_event(f"CURVE RIGHT {spd:.0%}")
    elif ch == " ":
        left.brake();      right.brake()
        _log_event("BRAKE")
    elif ch in ("t", "T"):
        threading.Thread(target=timed_test, daemon=True).start()


def main():
    stby = None
    if _GPIO_OK:
        try:
            stby = DigitalOutputDevice(STBY)
            stby.off()
        except Exception as e:
            if "busy" in str(e).lower():
                print("\nGPIO27 busy — run:  pm2 stop cosmo\n")
                sys.exit(1)
            raise

    left  = Motor(L_IN1, L_IN2, L_PWM,  "L")
    right = Motor(R_IN1, R_IN2, R_PWM,  "R")

    _log_event("Init — STBY LOW")
    _log_event(f"GPIO: {'REAL' if _GPIO_OK else 'MOCK'}")

    def enable_stby():
        if stby and not stby.value:
            stby.on()
            _log_event("STBY HIGH — motors live")

    def timed_test():
        enable_stby()
        spd = _set_speed
        steps = [
            ("FWD",        spd,  spd,  2.0),
            ("BWD",       -spd, -spd,  2.0),
            ("TANK LEFT", -spd,  spd,  1.5),
            ("TANK RIGHT", spd, -spd,  1.5),
            ("FWD",        spd,  spd,  1.0),
        ]
        for label, l, r, dur in steps:
            _log_event(f"Test: {label} {spd:.0%}")
            left.drive(l); right.drive(r)
            time.sleep(dur)
            left.brake();  right.brake()
            time.sleep(0.4)
        _log_event("Timed test done — tune with [ ] , .")

    def cleanup():
        left.close(); right.close()
        if stby:
            stby.off(); stby.close()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print(f"\nFinal trim values (copy to config/hardware.yaml):")
        print(f"  left_trim:  {TRIM['L']:.3f}")
        print(f"  right_trim: {TRIM['R']:.3f}")

    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)

    try:
        if _RICH:
            with Live(console=console, refresh_per_second=15, screen=True) as live:
                while True:
                    live.update(_build_ui(left, right))
                    if select.select([sys.stdin], [], [], 0.07)[0]:
                        ch = sys.stdin.read(1)
                        if ch in ("\x03", "\x04"):   # Ctrl-C / Ctrl-D
                            break
                        _handle_key(ch, left, right, enable_stby, timed_test)
        else:
            print("rich not installed — keyboard only, no display")
            while True:
                ch = sys.stdin.read(1)
                if ch in ("\x03", "\x04"):
                    break
                _handle_key(ch, left, right, enable_stby, timed_test)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
