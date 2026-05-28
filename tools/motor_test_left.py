#!/usr/bin/env python3
"""
Left-side motor test — standalone, no cosmo stack needed.

Tests Left Front (GPIO17/22/12) and Left Rear (GPIO23/10/13) independently.
STBY on GPIO27 — raised HIGH before any movement, lowered on exit.

Usage (SSH):
    python3 tools/motor_test_left.py

Keys:
    w / s   — Left Front forward / backward
    i / k   — Left Rear forward / backward
    + / -   — Increase / decrease speed step (5%)
    Space   — Stop both motors (brake)
    r       — Release (coast, PWM=0 but STBY stays HIGH)
    t       — 2-second timed test: both fwd, then stop
    q       — Quit (STBY LOW, all pins off)

Speed is capped at 60% for safety during bench testing.
Remove cap in code when chassis is on the ground.
"""

import sys
import termios
import threading
import time
import tty
from pathlib import Path

# ── Pin definitions (must match config/hardware.yaml) ─────────────────────────
STBY     = 27

LF_IN1   = 17   # Left Front direction 1
LF_IN2   = 22   # Left Front direction 2
LF_PWM   = 11   # Left Front speed (remapped from GPIO12 — Pin 32 faulty)

LR_IN1   = 23   # Left Rear direction 1
LR_IN2   = 10   # Left Rear direction 2
LR_PWM   = 13   # Left Rear speed

MAX_TEST_SPEED = 0.60   # safety cap for bench testing

# ── Attempt GPIO import ────────────────────────────────────────────────────────
try:
    from gpiozero import DigitalOutputDevice
    from gpiozero.pins.lgpio import LGPIOFactory
    from gpiozero import Device
    Device.pin_factory = LGPIOFactory()
    _GPIO_OK = True
except Exception as e:
    _GPIO_OK = False
    _GPIO_ERR = str(e)

# ── Rich UI ────────────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    _RICH = True
except ImportError:
    _RICH = False

console = Console() if _RICH else None


# ── Software PWM ───────────────────────────────────────────────────────────────
class _SoftPWM:
    _FREQ = 100

    def __init__(self, pin_dev) -> None:
        self._pin = pin_dev
        self._duty = 0.0
        self._lock = threading.Lock()
        self._stop = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def set(self, duty: float) -> None:
        with self._lock:
            self._duty = max(0.0, min(1.0, duty))

    def off(self) -> None:
        with self._lock:
            self._duty = 0.0
        self._pin.off()

    def _run(self) -> None:
        period = 1.0 / self._FREQ
        while not self._stop:
            with self._lock:
                d = self._duty
            if d <= 0.0:
                self._pin.off()
                time.sleep(period)
            elif d >= 1.0:
                self._pin.on()
                time.sleep(period)
            else:
                self._pin.on()
                time.sleep(period * d)
                self._pin.off()
                time.sleep(period * (1.0 - d))

    def close(self) -> None:
        self._stop = True
        self._t.join(timeout=0.5)
        self._pin.off()


# ── Single motor channel ───────────────────────────────────────────────────────
class Motor:
    def __init__(self, in1: int, in2: int, pwm_pin: int, name: str) -> None:
        self.name = name
        self.speed = 0.0
        if _GPIO_OK:
            self._in1 = DigitalOutputDevice(in1)
            self._in2 = DigitalOutputDevice(in2)
            self._pwm = _SoftPWM(DigitalOutputDevice(pwm_pin))
        else:
            self._in1 = self._in2 = self._pwm = None

    def drive(self, speed: float) -> None:
        speed = max(-MAX_TEST_SPEED, min(MAX_TEST_SPEED, speed))
        self.speed = speed
        if not _GPIO_OK:
            return
        duty = abs(speed)
        if speed > 0:
            self._in2.off(); self._in1.on()   # clear before set
            self._pwm.set(duty)
        elif speed < 0:
            self._in1.off(); self._in2.on()
            self._pwm.set(duty)
        else:
            self._in1.off(); self._in2.off(); self._pwm.off()

    def brake(self) -> None:
        self.speed = 0.0
        if _GPIO_OK:
            self._in1.off(); self._in2.off(); self._pwm.off()

    def close(self) -> None:
        self.brake()
        if _GPIO_OK and self._pwm:
            self._pwm.close()


# ── State ──────────────────────────────────────────────────────────────────────
_log: list[str] = []
_speed_step = 0.20   # 20% steps by default


def _log_event(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    _log.insert(0, f"[{ts}] {msg}")
    if len(_log) > 12:
        _log.pop()


# ── Rich display ───────────────────────────────────────────────────────────────
def _bar(v: float, w: int = 12) -> str:
    frac = abs(v)
    n = int(frac * w)
    bar = ("█" * n + "░" * (w - n))
    if v > 0:
        return f"[green]FWD {bar} {v:+.0%}[/green]"
    elif v < 0:
        return f"[red]BWD {bar} {v:+.0%}[/red]"
    return f"[dim]STP {'░'*w}   0%[/dim]"


def _build_panel(lf: Motor, lr: Motor, gpio_ok: bool) -> Panel:
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column("label", style="bold", width=14)
    t.add_column("value", width=30)

    hw = "[green]REAL GPIO[/green]" if gpio_ok else "[red]MOCK (no GPIO)[/red]"
    t.add_row("Hardware", hw)
    t.add_row("Speed step", f"{_speed_step:.0%}")
    t.add_row("", "")
    t.add_row("Left Front", _bar(lf.speed))
    t.add_row("Left Rear",  _bar(lr.speed))
    t.add_row("", "")
    t.add_row("[dim]w/s[/dim]", "[dim]Left Front fwd/bwd[/dim]")
    t.add_row("[dim]i/k[/dim]", "[dim]Left Rear fwd/bwd[/dim]")
    t.add_row("[dim]+/-[/dim]", "[dim]speed step up/down[/dim]")
    t.add_row("[dim]Space[/dim]", "[dim]brake both[/dim]")
    t.add_row("[dim]t[/dim]",     "[dim]2s timed test[/dim]")
    t.add_row("[dim]q[/dim]",     "[dim]quit[/dim]")

    log_t = Table(show_header=False, box=None, padding=(0, 1))
    log_t.add_column("entry", style="dim", width=40)
    for entry in _log[:8]:
        log_t.add_row(entry)

    from rich.columns import Columns
    body = Columns([t, log_t])
    return Panel(body, title="[bold cyan]Left Motor Test[/bold cyan]", border_style="cyan")


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    global _speed_step

    if not _GPIO_OK:
        print(f"WARNING: GPIO not available ({_GPIO_ERR if '_GPIO_ERR' in dir() else 'import failed'})")
        print("Running in mock mode — no real motor movement.\n")

    stby = None
    if _GPIO_OK:
        try:
            stby = DigitalOutputDevice(STBY)
        except Exception as e:
            if "busy" in str(e).lower() or "GPIO busy" in str(e):
                print("\n╔══════════════════════════════════════════════════════╗")
                print("║  GPIO27 (STBY) is claimed by another process.        ║")
                print("║                                                      ║")
                print("║  Stop cosmo first:   pm2 stop cosmo                 ║")
                print("║  Then run again:     python3 tools/motor_test_left.py║")
                print("║  When done:          pm2 start cosmo                ║")
                print("╚══════════════════════════════════════════════════════╝\n")
                sys.exit(1)
            else:
                print(f"WARNING: STBY init failed ({e}) — running without STBY control")

    lf = Motor(LF_IN1, LF_IN2, LF_PWM, "left_front")
    lr = Motor(LR_IN1, LR_IN2, LR_PWM, "left_rear")

    if stby:
        stby.off()   # safety: STBY LOW on init

    _log_event("Init — STBY LOW, all motors off")
    _log_event(f"GPIO: {'REAL' if _GPIO_OK else 'MOCK'}")

    def enable_stby() -> None:
        if stby and not stby.value:
            stby.on()
            _log_event("STBY HIGH — motors enabled")

    def _timed_test() -> None:
        enable_stby()
        _log_event("Timed test: LEFT FRONT fwd 40%")
        lf.drive(0.4)
        time.sleep(2.0)
        lf.brake()
        _log_event("Timed test: LEFT REAR fwd 40%")
        lr.drive(0.4)
        time.sleep(2.0)
        lr.brake()
        _log_event("Timed test: BOTH fwd 40%")
        lf.drive(0.4); lr.drive(0.4)
        time.sleep(2.0)
        lf.brake(); lr.brake()
        _log_event("Timed test: done")

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    def _cleanup() -> None:
        lf.close()
        lr.close()
        if stby:
            stby.off()
            stby.close()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    if not _RICH:
        # Fallback: plain text
        print("Left Motor Test (plain mode)")
        print("w/s=LF fwd/bwd  i/k=LR fwd/bwd  Space=stop  q=quit")
        tty.setraw(fd)
        try:
            while True:
                ch = sys.stdin.read(1)
                _handle_key(ch, lf, lr, enable_stby, _timed_test)
                if ch in ("q", "Q", "\x03"):
                    break
        finally:
            _cleanup()
        return

    tty.setraw(fd)
    try:
        with Live(console=console, refresh_per_second=8, screen=True) as live:
            while True:
                live.update(_build_panel(lf, lr, _GPIO_OK))

                import select
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    ch = sys.stdin.read(1)
                    if ch in ("q", "Q", "\x03"):
                        _log_event("Quit — STBY LOW")
                        break
                    _handle_key(ch, lf, lr, enable_stby, _timed_test)
    finally:
        _cleanup()

    print("\nMotor test exited. STBY LOW, all outputs off.")


def _handle_key(ch: str, lf: Motor, lr: Motor,
                enable_stby, timed_test) -> None:
    global _speed_step

    if ch == "w":
        enable_stby()
        lf.drive(lf.speed + _speed_step if lf.speed >= 0 else _speed_step)
        _log_event(f"LF fwd → {lf.speed:+.0%}")
    elif ch == "s":
        enable_stby()
        lf.drive(lf.speed - _speed_step if lf.speed <= 0 else -_speed_step)
        _log_event(f"LF bwd → {lf.speed:+.0%}")
    elif ch == "i":
        enable_stby()
        lr.drive(lr.speed + _speed_step if lr.speed >= 0 else _speed_step)
        _log_event(f"LR fwd → {lr.speed:+.0%}")
    elif ch == "k":
        enable_stby()
        lr.drive(lr.speed - _speed_step if lr.speed <= 0 else -_speed_step)
        _log_event(f"LR bwd → {lr.speed:+.0%}")
    elif ch == "+":
        _speed_step = min(0.5, _speed_step + 0.05)
        _log_event(f"Step → {_speed_step:.0%}")
    elif ch == "-":
        _speed_step = max(0.05, _speed_step - 0.05)
        _log_event(f"Step → {_speed_step:.0%}")
    elif ch == " ":
        lf.brake(); lr.brake()
        _log_event("BRAKE both")
    elif ch in ("t", "T"):
        threading.Thread(target=timed_test, daemon=True).start()


if __name__ == "__main__":
    main()
