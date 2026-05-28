#!/usr/bin/env python3
"""
Right-side motor test — standalone, no cosmo stack needed.

Tests Right Front (GPIO20/21/18) and Right Rear (GPIO25/26/19) independently.
STBY on GPIO27 — raised HIGH before any movement, lowered on exit.

Usage (SSH):
    pm2 stop cosmo
    python3 tools/motor_test_right.py
    pm2 start cosmo

Keys:
    w / s   — Right Front forward / backward
    i / k   — Right Rear forward / backward
    + / -   — Increase / decrease speed step (5%)
    Space   — Stop both motors (brake)
    t       — 2-second timed test: RF then RR then both
    q       — Quit (STBY LOW, all pins off)

Speed capped at 60% for bench safety.
"""

import sys
import termios
import threading
import time
import tty
from pathlib import Path

# ── Pin definitions (must match config/hardware.yaml) ─────────────────────────
STBY     = 27

RF_IN1   = 20   # Right Front AIN1
RF_IN2   = 24   # Right Front AIN2 (remapped: GPIO21 dead → GPIO14 UART → GPIO5 dead → GPIO24 Pin 18)
RF_PWM   = 18   # Right Front PWMA

RR_IN1   = 25   # Right Rear BIN1
RR_IN2   = 26   # Right Rear BIN2
RR_PWM   =  9   # Right Rear PWMB (remapped from GPIO19 — Pin 35 faulty)

MAX_TEST_SPEED = 0.60

try:
    from gpiozero import DigitalOutputDevice
    from gpiozero.pins.lgpio import LGPIOFactory
    from gpiozero import Device
    Device.pin_factory = LGPIOFactory()
    _GPIO_OK = True
except Exception as e:
    _GPIO_OK = False
    _GPIO_ERR = str(e)

try:
    from rich.console import Console
    from rich.columns import Columns
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    _RICH = True
except ImportError:
    _RICH = False

console = Console() if _RICH else None


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
                self._pin.off(); time.sleep(period)
            elif d >= 1.0:
                self._pin.on(); time.sleep(period)
            else:
                self._pin.on(); time.sleep(period * d)
                self._pin.off(); time.sleep(period * (1.0 - d))

    def close(self) -> None:
        self._stop = True
        self._t.join(timeout=0.5)
        self._pin.off()


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
            self._in2.off(); self._in1.on()
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


_log: list[str] = []
_speed_step = 0.20


def _log_event(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    _log.insert(0, f"[{ts}] {msg}")
    if len(_log) > 12:
        _log.pop()


def _bar(v: float, w: int = 12) -> str:
    frac = abs(v)
    n = int(frac * w)
    bar = "█" * n + "░" * (w - n)
    if v > 0:   return f"[green]FWD {bar} {v:+.0%}[/green]"
    elif v < 0: return f"[red]BWD {bar} {v:+.0%}[/red]"
    return f"[dim]STP {'░'*w}   0%[/dim]"


def _build_panel(rf: Motor, rr: Motor) -> Panel:
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column("label", style="bold", width=14)
    t.add_column("value", width=30)
    hw = "[green]REAL GPIO[/green]" if _GPIO_OK else "[red]MOCK[/red]"
    t.add_row("Hardware",    hw)
    t.add_row("Speed step",  f"{_speed_step:.0%}")
    t.add_row("", "")
    t.add_row("Right Front", _bar(rf.speed))
    t.add_row("Right Rear",  _bar(rr.speed))
    t.add_row("", "")
    t.add_row("[dim]w/s[/dim]",   "[dim]Right Front fwd/bwd[/dim]")
    t.add_row("[dim]i/k[/dim]",   "[dim]Right Rear fwd/bwd[/dim]")
    t.add_row("[dim]+/-[/dim]",   "[dim]speed step[/dim]")
    t.add_row("[dim]Space[/dim]", "[dim]brake both[/dim]")
    t.add_row("[dim]t[/dim]",     "[dim]timed test[/dim]")
    t.add_row("[dim]q[/dim]",     "[dim]quit[/dim]")

    log_t = Table(show_header=False, box=None, padding=(0, 1))
    log_t.add_column("entry", style="dim", width=40)
    for entry in _log[:8]:
        log_t.add_row(entry)

    return Panel(Columns([t, log_t]),
                 title="[bold cyan]Right Motor Test[/bold cyan]",
                 border_style="cyan")


def _handle_key(ch, rf, rr, enable_stby, timed_test):
    global _speed_step
    if ch == "w":
        enable_stby()
        rf.drive(rf.speed + _speed_step if rf.speed >= 0 else _speed_step)
        _log_event(f"RF fwd → {rf.speed:+.0%}")
    elif ch == "s":
        enable_stby()
        rf.drive(rf.speed - _speed_step if rf.speed <= 0 else -_speed_step)
        _log_event(f"RF bwd → {rf.speed:+.0%}")
    elif ch == "i":
        enable_stby()
        rr.drive(rr.speed + _speed_step if rr.speed >= 0 else _speed_step)
        _log_event(f"RR fwd → {rr.speed:+.0%}")
    elif ch == "k":
        enable_stby()
        rr.drive(rr.speed - _speed_step if rr.speed <= 0 else -_speed_step)
        _log_event(f"RR bwd → {rr.speed:+.0%}")
    elif ch == "+":
        _speed_step = min(0.5, _speed_step + 0.05)
        _log_event(f"Step → {_speed_step:.0%}")
    elif ch == "-":
        _speed_step = max(0.05, _speed_step - 0.05)
        _log_event(f"Step → {_speed_step:.0%}")
    elif ch == " ":
        rf.brake(); rr.brake()
        _log_event("BRAKE both")
    elif ch in ("t", "T"):
        threading.Thread(target=timed_test, daemon=True).start()


def main():
    global _speed_step

    stby = None
    if _GPIO_OK:
        try:
            stby = DigitalOutputDevice(STBY)
        except Exception as e:
            if "busy" in str(e).lower():
                print("\n╔══════════════════════════════════════════════════════╗")
                print("║  GPIO27 (STBY) is busy — stop cosmo first:          ║")
                print("║    pm2 stop cosmo                                   ║")
                print("╚══════════════════════════════════════════════════════╝\n")
                sys.exit(1)

    rf = Motor(RF_IN1, RF_IN2, RF_PWM, "right_front")
    rr = Motor(RR_IN1, RR_IN2, RR_PWM, "right_rear")

    if stby:
        stby.off()

    _log_event("Init — STBY LOW, all off")
    _log_event(f"GPIO: {'REAL' if _GPIO_OK else 'MOCK'}")

    def enable_stby():
        if stby and not stby.value:
            stby.on()
            _log_event("STBY HIGH")

    def timed_test():
        enable_stby()
        for motor, name in [(rf, "RF"), (rr, "RR")]:
            _log_event(f"Timed: {name} fwd 40%")
            motor.drive(0.4); time.sleep(2.0); motor.brake()
        _log_event("Timed: BOTH fwd 40%")
        rf.drive(0.4); rr.drive(0.4)
        time.sleep(2.0)
        rf.brake(); rr.brake()
        _log_event("Timed test done")

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    def cleanup():
        rf.close(); rr.close()
        if stby: stby.off(); stby.close()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    tty.setraw(fd)
    try:
        if _RICH:
            import select
            with Live(console=console, refresh_per_second=8, screen=True) as live:
                while True:
                    live.update(_build_panel(rf, rr))
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        ch = sys.stdin.read(1)
                        if ch in ("q", "Q", "\x03"):
                            break
                        _handle_key(ch, rf, rr, enable_stby, timed_test)
        else:
            while True:
                ch = sys.stdin.read(1)
                if ch in ("q", "Q", "\x03"):
                    break
                _handle_key(ch, rf, rr, enable_stby, timed_test)
    finally:
        cleanup()

    print("\nRight motor test exited. STBY LOW, all pins off.")


if __name__ == "__main__":
    main()
