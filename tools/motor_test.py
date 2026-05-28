"""
Safe incremental motor test — Board 1 (LEFT) first, then Board 2 (RIGHT).

Runs one motor at a time at low speed for a short burst.
Never exceeds 50% duty. Never runs more than 1.5s per burst.

BEFORE RUNNING:
  1. Solder 4700µF cap across VM+GND on each TB6612FNG board
  2. Solder 220µF cap across AO1-AO2 and BO1-BO2 on each board
  3. Use 4× AA battery holder (6V) — NOT LiPo for first test
  4. Confirm STBY wire is on GPIO27

Usage:
  python3 tools/motor_test.py           # tests Board 1 only (safe default)
  python3 tools/motor_test.py --board 2 # tests Board 2 only
  python3 tools/motor_test.py --both    # tests both boards sequentially
  python3 tools/motor_test.py --channel left_front  # single channel only
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.live import Live
from rich.text import Text

from utils.config import cfg

console = Console()

TEST_DUTY   = 0.30   # 30% speed — safe for no-load test
BURST_S     = 1.2    # seconds per burst
REST_S      = 0.8    # seconds between bursts (let motor/driver cool)

BOARD1_CHANNELS = ["left_front", "left_rear"]
BOARD2_CHANNELS = ["right_front", "right_rear"]


def _require_caps_ack() -> bool:
    console.print(Panel(
        "[bold red]SAFETY CHECK — read before proceeding[/]\n\n"
        "[yellow]1.[/] 4700µF 25V cap soldered across [bold]VM + GND[/] on each TB6612FNG board?\n"
        "[yellow]2.[/] 220µF 25V caps soldered across [bold]AO1-AO2[/] and [bold]BO1-BO2[/] on each board?\n"
        "[yellow]3.[/] Using [bold]4× AA battery (6V)[/] — NOT LiPo for this test?\n"
        "[yellow]4.[/] STBY wire on [bold]GPIO27[/] (Pin 13)?\n\n"
        "[bold]Type YES to continue, anything else aborts:[/]",
        title="[bold red]⚡ Hardware Safety[/]",
        border_style="red",
    ))
    resp = input("  → ").strip().upper()
    return resp == "YES"


async def test_channel(
    name: str,
    ain1: int, ain2: int, pwm_pin: int, stby_pin: int,
    duty: float = TEST_DUTY,
    burst_s: float = BURST_S,
) -> dict:
    """Run a single TB6612FNG channel: forward burst, brake, backward burst, brake."""
    try:
        from gpiozero import DigitalOutputDevice
        from gpiozero.pins.lgpio import LGPIOFactory
        from gpiozero import Device as _GpioDevice
        _GpioDevice.pin_factory = LGPIOFactory()
    except ImportError:
        return {"name": name, "ok": False, "error": "gpiozero not available"}

    result = {"name": name, "ok": True, "forward_s": 0.0, "backward_s": 0.0, "error": None}

    try:
        stby = DigitalOutputDevice(stby_pin)
        in1  = DigitalOutputDevice(ain1)
        in2  = DigitalOutputDevice(ain2)
        pwm  = DigitalOutputDevice(pwm_pin)

        stby.off()
        in1.off(); in2.off(); pwm.off()

        # STBY HIGH to enable
        stby.on()
        await asyncio.sleep(0.05)

        # Forward burst
        console.print(f"  [cyan]{name}[/] → [green]FORWARD {duty:.0%}[/]")
        in2.off(); in1.on()

        # Crude software PWM for test (single thread, acceptable for short burst)
        t0 = time.monotonic()
        period = 0.01
        while (time.monotonic() - t0) < burst_s:
            pwm.on()
            await asyncio.sleep(period * duty)
            pwm.off()
            await asyncio.sleep(period * (1.0 - duty))
        result["forward_s"] = round(time.monotonic() - t0, 2)

        # Brake
        in1.off(); in2.off(); pwm.off()
        await asyncio.sleep(REST_S)

        # Backward burst
        console.print(f"  [cyan]{name}[/] → [yellow]BACKWARD {duty:.0%}[/]")
        in1.off(); in2.on()
        t0 = time.monotonic()
        while (time.monotonic() - t0) < burst_s:
            pwm.on()
            await asyncio.sleep(period * duty)
            pwm.off()
            await asyncio.sleep(period * (1.0 - duty))
        result["backward_s"] = round(time.monotonic() - t0, 2)

        # Stop
        in1.off(); in2.off(); pwm.off()
        stby.off()  # power down STBY after test
        await asyncio.sleep(REST_S)

        in1.close(); in2.close(); pwm.close(); stby.close()

    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)

    return result


async def run_tests(channels: list[str]) -> None:
    mc = cfg.hardware.motors

    channel_map = {
        "left_front":  (mc.left_front.ain1,  mc.left_front.ain2,  mc.left_front.pwm,  mc.stby),
        "left_rear":   (mc.left_rear.bin1,   mc.left_rear.bin2,   mc.left_rear.pwm,   mc.stby),
        "right_front": (mc.right_front.ain1, mc.right_front.ain2, mc.right_front.pwm, mc.stby),
        "right_rear":  (mc.right_rear.bin1,  mc.right_rear.bin2,  mc.right_rear.pwm,  mc.stby),
    }

    results = []
    for ch_name in channels:
        if ch_name not in channel_map:
            console.print(f"[red]Unknown channel: {ch_name}[/]")
            continue
        ain1, ain2, pwm_pin, stby_pin = channel_map[ch_name]
        console.print(f"\n[bold]Testing [cyan]{ch_name}[/] — AIN1=GPIO{ain1} AIN2=GPIO{ain2} PWM=GPIO{pwm_pin}[/]")
        res = await test_channel(ch_name, ain1, ain2, pwm_pin, stby_pin)
        results.append(res)

    console.print()
    t = Table(title="Motor Test Results", box=box.ROUNDED)
    t.add_column("Channel",  style="cyan")
    t.add_column("Forward",  style="green")
    t.add_column("Backward", style="yellow")
    t.add_column("Status",   style="bold")

    for r in results:
        status = "[green]✓ OK[/]" if r["ok"] else f"[red]✗ {r.get('error','?')}[/]"
        t.add_row(
            r["name"],
            f"{r.get('forward_s', 0):.2f}s" if r["ok"] else "—",
            f"{r.get('backward_s', 0):.2f}s" if r["ok"] else "—",
            status,
        )
    console.print(t)

    all_ok = all(r["ok"] for r in results)
    if all_ok:
        console.print("\n[bold green]All channels passed.[/] Check that wheels actually turned — if any didn't move, check wiring.")
    else:
        console.print("\n[bold red]Some channels failed.[/] Check GPIO wiring and power connections.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe TB6612FNG motor test")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--board", type=int, choices=[1, 2], default=1,
                       help="Which board to test (default: 1 = LEFT)")
    group.add_argument("--both", action="store_true", help="Test both boards")
    group.add_argument("--channel", type=str, help="Test a single channel by name")
    args = parser.parse_args()

    console.print(Panel(
        f"[bold]TB6612FNG Safe Motor Test[/]\n"
        f"Speed: {TEST_DUTY:.0%} · Burst: {BURST_S}s · Rest: {REST_S}s between bursts\n"
        f"Max duty hard limit: {TEST_DUTY:.0%} (motors.py MAX_DUTY=75%)",
        title="[bold cyan]Cosmo Motor Test[/]",
        border_style="cyan",
    ))

    if not _require_caps_ack():
        console.print("[red]Aborted.[/]")
        sys.exit(0)

    if args.channel:
        channels = [args.channel]
    elif args.both:
        channels = BOARD1_CHANNELS + BOARD2_CHANNELS
    elif args.board == 2:
        channels = BOARD2_CHANNELS
    else:
        channels = BOARD1_CHANNELS

    console.print(f"\n[bold]Channels to test:[/] {', '.join(channels)}\n")
    asyncio.run(run_tests(channels))


if __name__ == "__main__":
    main()
