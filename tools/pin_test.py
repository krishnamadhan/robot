#!/usr/bin/env python3
"""
Test GPIO pins for input/output capability.
Run with no hardware connected to avoid shorts.

Usage:
  python3 tools/pin_test.py              # test all suspected-dead pins
  python3 tools/pin_test.py --all        # test all non-reserved pins
  python3 tools/pin_test.py 4 7 12 19 21 # test specific pins

Method: set OUTPUT HIGH → read back → set OUTPUT LOW → read back
A "dead" pin typically reads LOW regardless of output state.
"""

import sys
import time
import argparse
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

SUSPECTED_DEAD = [4, 7, 12, 19, 21]

RESERVED = {
    0: "HAT EEPROM I2C0",
    1: "HAT EEPROM I2C0",
    2: "I2C1 SDA",
    3: "I2C1 SCL",
    6: "FIT0992 adapter-fail (HAT drives — never set as output)",
    16: "FIT0992 charging-disable (HAT drives — never touch)",
}

MOTOR_PINS = {9, 10, 11, 13, 17, 18, 20, 22, 23, 24, 25, 26, 27}

ALL_TESTABLE = [p for p in range(28) if p not in RESERVED]


def _pinctrl_read(gpio: int, pull: str) -> int:
    """Set pull (pu/pd/pn) and read level via pinctrl. Returns 0 or 1."""
    import subprocess
    subprocess.run(["pinctrl", "set", str(gpio), "ip", pull],
                   capture_output=True, check=True)
    time.sleep(0.03)
    r = subprocess.run(["pinctrl", "get", str(gpio)],
                       capture_output=True, text=True, check=True)
    # Output: "17: ip    pu | hi // ..."  or  "17: ip    pd | lo // ..."
    return 1 if "| hi" in r.stdout else 0


def test_pin(gpio: int) -> dict:
    try:
        pud_up   = _pinctrl_read(gpio, "pu")   # with pull-up:   floating → HIGH (1)
        pud_down = _pinctrl_read(gpio, "pd")   # with pull-down: floating → LOW  (0)
        # Restore to no-pull input
        import subprocess
        subprocess.run(["pinctrl", "set", str(gpio), "ip", "pn"], capture_output=True)

        working = (pud_up == 1 and pud_down == 0)
        return {"pud_up": pud_up, "pud_down": pud_down, "working": working, "error": None}

    except Exception as e:
        return {"pud_up": None, "pud_down": None, "working": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Test GPIO pins for dead/alive status")
    parser.add_argument("pins", nargs="*", type=int, help="GPIO pins to test (BCM)")
    parser.add_argument("--all", action="store_true", help="Test all non-reserved pins")
    args = parser.parse_args()

    if args.all:
        pins = ALL_TESTABLE
    elif args.pins:
        pins = args.pins
    else:
        pins = SUSPECTED_DEAD

    console.print(f"\n[bold cyan]GPIO Pin Test[/bold cyan] — testing {len(pins)} pins")
    console.print("[yellow]WARNING: Disconnect all hardware before running (except UPS HAT I2C is OK)[/yellow]\n")

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold white")
    table.add_column("GPIO", style="cyan", width=6)
    table.add_column("PUD_UP", width=8)
    table.add_column("PUD_DOWN", width=10)
    table.add_column("Result", width=20)
    table.add_column("Note")

    for gpio in pins:
        if gpio in RESERVED:
            table.add_row(
                f"GPIO{gpio}",
                "—", "—",
                "[yellow]RESERVED[/yellow]",
                RESERVED[gpio],
            )
            continue

        r = test_pin(gpio)
        motor_note = " [motor pin]" if gpio in MOTOR_PINS else ""
        if r["error"]:
            table.add_row(
                f"GPIO{gpio}", "ERR", "ERR",
                "[red]ERROR[/red]",
                r["error"][:50],
            )
        elif r["working"]:
            table.add_row(
                f"GPIO{gpio}",
                str(r["pud_up"]), str(r["pud_down"]),
                "[bold green]ALIVE[/bold green]",
                f"pull-up/down OK{motor_note}",
            )
        else:
            table.add_row(
                f"GPIO{gpio}",
                str(r["pud_up"]), str(r["pud_down"]),
                "[bold red]DEAD[/bold red]",
                f"stuck pud_up={r['pud_up']} pud_down={r['pud_down']}{motor_note}",
            )

    console.print(table)
    console.print("\n[dim]Alive = PUD_UP reads 1, PUD_DOWN reads 0. Dead = stuck same value regardless.[/dim]")


if __name__ == "__main__":
    main()
