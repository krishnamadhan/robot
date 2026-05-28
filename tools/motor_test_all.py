#!/usr/bin/env python3
"""
2-motor test — single TB6612FNG (right board), front + rear motors.

Wiring (Board 2 only):
    STBY  → GPIO27  Pin 13
    FRONT AIN1 → GPIO20  Pin 38
    FRONT AIN2 → GPIO24  Pin 18
    FRONT PWMA → GPIO18  Pin 12
    REAR  BIN1 → GPIO25  Pin 22
    REAR  BIN2 → GPIO26  Pin 37
    REAR  PWMB → GPIO9   Pin 21

Usage:
    pm2 stop cosmo
    python3 tools/motor_test_all.py
    pm2 start cosmo

Keys:
    1-9 / 0   — Set speed 10%-100%
    w / s     — Both forward / backward
    a / d     — Turn left / right (front stops, rear drives)
    u / j     — Front fwd / bwd
    i / k     — Rear  fwd / bwd
    [ / ]     — Front trim −/+
    , / .     — Rear  trim −/+
    Space     — Brake both
    t         — Timed test
    q         — Quit
"""

import select
import sys
import termios
import threading
import time
import tty

STBY      = 27
FT_IN1    = 20;  FT_IN2 = 24;  FT_PWM = 18
RR_IN1    = 25;  RR_IN2 = 26;  RR_PWM =  9

TRIM      = {"FT": 1.00, "RR": 1.00}
_DEADZONE = 0.18

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


def _apply_trim(speed: float, trim: float) -> float:
    if speed == 0.0:
        return 0.0
    sign = 1 if speed > 0 else -1
    s = _DEADZONE + abs(speed) * (1.0 - _DEADZONE)
    return sign * min(s * trim, 1.0)


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
    def __init__(self, in1, in2, pwm_pin, name):
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
        effective = _apply_trim(speed, TRIM[self.name])
        duty = abs(effective)
        if effective > 0:
            self._in2.off(); self._in1.on();  self._pwm.set(duty)
        elif effective < 0:
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


_log:       list[str] = []
_set_speed: float     = 0.50


def _log_event(msg: str):
    ts = time.strftime("%H:%M:%S")
    _log.insert(0, f"[{ts}] {msg}")
    if len(_log) > 12:
        _log.pop()


def _bar(v: float, w: int = 14) -> str:
    n   = int(abs(v) * w)
    bar = "█" * n + "░" * (w - n)
    if v > 0:   return f"[green]▶ FWD {bar} {v:+.0%}[/green]"
    elif v < 0: return f"[red]◀ BWD {bar} {v:+.0%}[/red]"
    return f"[dim]  STP {'░'*w}   0%[/dim]"


def _build_panel(ft, rr) -> Panel:
    hw = "[green]REAL GPIO[/green]" if _GPIO_OK else "[red]MOCK[/red]"

    status = Table(show_header=False, box=None, padding=(0, 1))
    status.add_column("label", style="bold cyan", width=10)
    status.add_column("value", width=36)
    status.add_row("Hardware",  hw)
    status.add_row("Speed",     f"[bold yellow]{_set_speed:.0%}[/bold yellow]  (1-9, 0=100%)")
    status.add_row("", "")
    status.add_row("FRONT", _bar(ft.speed) + f"  [dim]trim={TRIM['FT']:.2f}[/dim]")
    status.add_row("REAR",  _bar(rr.speed) + f"  [dim]trim={TRIM['RR']:.2f}[/dim]")

    keys = Table(show_header=False, box=None, padding=(0, 1))
    keys.add_column("k", style="bold", width=8)
    keys.add_column("desc", style="dim", width=22)
    for row in [
        ("1-9 / 0", "set speed"),
        ("w / s",   "both fwd / bwd"),
        ("a / d",   "turn left / right"),
        ("u / j",   "Front fwd / bwd"),
        ("i / k",   "Rear  fwd / bwd"),
        ("[ / ]",   "Front trim −/+"),
        (", / .",   "Rear  trim −/+"),
        ("Space",   "brake both"),
        ("t",       "timed test"),
        ("q",       "quit"),
    ]:
        keys.add_row(*row)

    log_t = Table(show_header=False, box=None, padding=(0, 1))
    log_t.add_column("entry", style="dim", width=36)
    for entry in _log[:10]:
        log_t.add_row(entry)

    return Panel(
        Columns([status, keys, log_t]),
        title="[bold cyan]2-Motor Test (Right Board)[/bold cyan]",
        border_style="cyan",
    )


def _handle_key(ch, ft, rr, enable_stby, timed_test):
    global _set_speed, TRIM

    if ch.isdigit():
        _set_speed = 1.0 if ch == "0" else int(ch) * 0.10
        _log_event(f"Speed → {_set_speed:.0%}")
        return

    if ch == "[":
        TRIM["FT"] = max(0.5, round(TRIM["FT"] - 0.02, 2))
        _log_event(f"FT trim → {TRIM['FT']:.2f}"); return
    if ch == "]":
        TRIM["FT"] = min(1.5, round(TRIM["FT"] + 0.02, 2))
        _log_event(f"FT trim → {TRIM['FT']:.2f}"); return
    if ch == ",":
        TRIM["RR"] = max(0.5, round(TRIM["RR"] - 0.02, 2))
        _log_event(f"RR trim → {TRIM['RR']:.2f}"); return
    if ch == ".":
        TRIM["RR"] = min(1.5, round(TRIM["RR"] + 0.02, 2))
        _log_event(f"RR trim → {TRIM['RR']:.2f}"); return

    spd = _set_speed

    if ch == "w":
        enable_stby()
        ft.drive(spd); rr.drive(spd)
        _log_event(f"FWD {spd:.0%}")
    elif ch == "s":
        enable_stby()
        ft.drive(-spd); rr.drive(-spd)
        _log_event(f"BWD {spd:.0%}")
    elif ch == "a":
        enable_stby()
        ft.drive(spd); rr.drive(0)
        _log_event(f"TURN left {spd:.0%}")
    elif ch == "d":
        enable_stby()
        ft.drive(0); rr.drive(spd)
        _log_event(f"TURN right {spd:.0%}")
    elif ch == "u": enable_stby(); ft.drive(spd);  _log_event(f"FT fwd {spd:.0%}")
    elif ch == "j": enable_stby(); ft.drive(-spd); _log_event(f"FT bwd {spd:.0%}")
    elif ch == "i": enable_stby(); rr.drive(spd);  _log_event(f"RR fwd {spd:.0%}")
    elif ch == "k": enable_stby(); rr.drive(-spd); _log_event(f"RR bwd {spd:.0%}")
    elif ch == " ":
        ft.brake(); rr.brake()
        _log_event("BRAKE")
    elif ch in ("t", "T"):
        threading.Thread(target=timed_test, daemon=True).start()


def main():
    stby = None
    if _GPIO_OK:
        try:
            stby = DigitalOutputDevice(STBY)
        except Exception as e:
            if "busy" in str(e).lower():
                print("\nGPIO27 busy — run: pm2 stop cosmo\n")
                sys.exit(1)
            raise

    ft = Motor(FT_IN1, FT_IN2, FT_PWM, "FT")
    rr = Motor(RR_IN1, RR_IN2, RR_PWM, "RR")

    if stby:
        stby.off()

    _log_event("Init — STBY LOW")
    _log_event(f"GPIO: {'REAL' if _GPIO_OK else 'MOCK'}")

    def enable_stby():
        if stby and not stby.value:
            stby.on(); _log_event("STBY HIGH")

    def timed_test():
        enable_stby(); spd = _set_speed
        for motor, name in [(ft, "FRONT"), (rr, "REAR")]:
            _log_event(f"Timed: {name} fwd {spd:.0%}")
            motor.drive(spd); time.sleep(2.0); motor.brake()
            time.sleep(0.4)
        _log_event(f"Timed: BOTH fwd {spd:.0%}")
        ft.drive(spd); rr.drive(spd)
        time.sleep(2.0)
        ft.brake(); rr.brake()
        _log_event("Done — tune with [ ] , .")

    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    def cleanup():
        ft.close(); rr.close()
        if stby: stby.off(); stby.close()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print(f"\nTrim values → hardware.yaml:")
        print(f"  FT (right_front): {TRIM['FT']:.2f}")
        print(f"  RR (right_rear):  {TRIM['RR']:.2f}")

    tty.setraw(fd)
    try:
        if _RICH:
            with Live(console=console, refresh_per_second=10, screen=True) as live:
                while True:
                    live.update(_build_panel(ft, rr))
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        ch = sys.stdin.read(1)
                        if ch in ("q", "Q", "\x03"):
                            break
                        _handle_key(ch, ft, rr, enable_stby, timed_test)
        else:
            while True:
                ch = sys.stdin.read(1)
                if ch in ("q", "Q", "\x03"):
                    break
                _handle_key(ch, ft, rr, enable_stby, timed_test)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
