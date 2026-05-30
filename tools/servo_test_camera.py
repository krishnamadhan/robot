#!/usr/bin/env python3
"""
Camera pan/tilt servo test — PCA9685 ch0 (pan) + ch1 (tilt).

    python3 tools/servo_test_camera.py

Keys:
    a / d     — Pan left / right
    w / s     — Tilt up / down
    c         — Center both (90° pan, 90° tilt)
    t         — Auto sweep test
    [ / ]     — Decrease / increase step size
    Ctrl-C    — Quit (centers first)
"""

import select
import sys
import termios
import threading
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PAN_CH   = 0;   PAN_MIN,  PAN_MAX,  PAN_CENTER  = 30, 150, 90
TILT_CH  = 1;   TILT_MIN, TILT_MAX, TILT_CENTER = 60, 120, 90
I2C_ADDR = 0x40

try:
    from adafruit_servokit import ServoKit
    kit = ServoKit(channels=16, address=I2C_ADDR)
    _HW_OK = True
except Exception as e:
    print(f"PCA9685 not found: {e}")
    print("Check: sudo i2cdetect -y 1  — should show 0x40")
    sys.exit(1)

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

_pan  = float(PAN_CENTER)
_tilt = float(TILT_CENTER)
_step = 5.0
_log: list[str] = []


def _set_pan(angle: float):
    global _pan
    _pan = max(PAN_MIN, min(PAN_MAX, angle))
    kit.servo[PAN_CH].angle = _pan


def _set_tilt(angle: float):
    global _tilt
    _tilt = max(TILT_MIN, min(TILT_MAX, angle))
    kit.servo[TILT_CH].angle = _tilt


def _log_event(msg: str):
    _log.insert(0, f"[{time.strftime('%H:%M:%S')}] {msg}")
    del _log[10:]


def _angle_bar(v: float, lo: float, hi: float, w: int = 16) -> str:
    pct = (v - lo) / (hi - lo)
    n   = int(pct * w)
    bar = "█" * n + "░" * (w - n)
    return f"[cyan]{bar}[/cyan] [bold]{v:.0f}°[/bold]"


def _build_ui() -> Panel:
    status = Table(show_header=False, box=None, padding=(0, 1))
    status.add_column("lbl", style="bold cyan", width=6)
    status.add_column("val", width=36)
    status.add_row("PAN",  _angle_bar(_pan,  PAN_MIN,  PAN_MAX)  + f"  [dim]({PAN_MIN}-{PAN_MAX}°)[/dim]")
    status.add_row("TILT", _angle_bar(_tilt, TILT_MIN, TILT_MAX) + f"  [dim]({TILT_MIN}-{TILT_MAX}°)[/dim]")
    status.add_row("",     "")
    status.add_row("Step", f"[yellow]{_step:.0f}°[/yellow]  ([ / ] to change)")

    keys = Table(show_header=False, box=None, padding=(0, 1))
    keys.add_column("k", style="bold", width=8)
    keys.add_column("desc", style="dim", width=22)
    for row in [
        ("a / d",   "pan left / right"),
        ("w / s",   "tilt up / down"),
        ("c",       "center both"),
        ("t",       "auto sweep test"),
        ("[ / ]",   "step −/+"),
        ("Ctrl-C",  "quit (centers)"),
    ]:
        keys.add_row(*row)

    log_t = Table(show_header=False, box=None, padding=(0, 1))
    log_t.add_column("entry", style="dim", width=30)
    for entry in _log[:8]:
        log_t.add_row(entry)

    return Panel(
        Columns([status, keys, log_t]),
        title="[bold cyan]Camera Pan/Tilt Test  ch0=pan  ch1=tilt  I2C 0x40[/bold cyan]",
        border_style="cyan",
    )


def _auto_sweep():
    _log_event("Sweep start")
    for angle in [PAN_MIN, PAN_MAX, PAN_CENTER]:
        _set_pan(angle)
        time.sleep(0.8)
    for angle in [TILT_MIN, TILT_MAX, TILT_CENTER]:
        _set_tilt(angle)
        time.sleep(0.8)
    _set_pan(PAN_CENTER)
    _set_tilt(TILT_CENTER)
    _log_event("Sweep done — centered")


def _handle_key(ch: str):
    global _step
    if ch == "a":
        _set_pan(_pan - _step);  _log_event(f"Pan  → {_pan:.0f}°")
    elif ch == "d":
        _set_pan(_pan + _step);  _log_event(f"Pan  → {_pan:.0f}°")
    elif ch == "w":
        _set_tilt(_tilt - _step); _log_event(f"Tilt → {_tilt:.0f}°")
    elif ch == "s":
        _set_tilt(_tilt + _step); _log_event(f"Tilt → {_tilt:.0f}°")
    elif ch == "c":
        _set_pan(PAN_CENTER); _set_tilt(TILT_CENTER)
        _log_event("Centered")
    elif ch == "t":
        threading.Thread(target=_auto_sweep, daemon=True).start()
    elif ch == "[":
        _step = max(1.0, _step - 1.0); _log_event(f"Step → {_step:.0f}°")
    elif ch == "]":
        _step = min(20.0, _step + 1.0); _log_event(f"Step → {_step:.0f}°")


def main():
    # Center on startup
    _set_pan(PAN_CENTER)
    _set_tilt(TILT_CENTER)
    _log_event("Init — centered")
    _log_event(f"PCA9685 at 0x{I2C_ADDR:02X}")

    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    def cleanup():
        _set_pan(PAN_CENTER)
        _set_tilt(TILT_CENTER)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print(f"\nFinal angles — pan: {_pan:.0f}°  tilt: {_tilt:.0f}°")
        print(f"Update hardware.yaml limits if range needs adjusting.")

    tty.setraw(fd)
    try:
        if _RICH:
            with Live(console=console, refresh_per_second=15, screen=True) as live:
                while True:
                    live.update(_build_ui())
                    if select.select([sys.stdin], [], [], 0.07)[0]:
                        ch = sys.stdin.read(1)
                        if ch in ("\x03", "\x04"):
                            break
                        _handle_key(ch)
        else:
            while True:
                ch = sys.stdin.read(1)
                if ch in ("\x03", "\x04"):
                    break
                _handle_key(ch)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
