#!/usr/bin/env python3
"""
ESP32 bridge live test — Rich dashboard.
Shows real-time sensor data, motor command round-trips, and protocol stats.

Usage:
  python3 tools/esp32_test.py
  python3 tools/esp32_test.py --mock      # force mock mode (no ESP32 needed)
  python3 tools/esp32_test.py --motors    # include motor test sequence
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

# ── State ─────────────────────────────────────────────────────────────────────

_state = {
    "connected": False,
    "mock": False,
    "esp_uptime": 0,
    "last_hb_age": 0.0,
    "sensors": {},
    "sensor_data": {},
    "events": [],         # last 20 events
    "motor_l": 0.0,
    "motor_r": 0.0,
    "cmd_sent": 0,
    "cmd_acked": 0,
    "errors": [],
    "start_time": time.monotonic(),
}

MAX_EVENTS = 20


def _log_event(label: str, data: dict = None, color: str = "white"):
    ts = time.monotonic() - _state["start_time"]
    entry = {"ts": round(ts, 2), "label": label, "data": data or {}, "color": color}
    _state["events"].insert(0, entry)
    _state["events"] = _state["events"][:MAX_EVENTS]


# ── Bridge event hooks ────────────────────────────────────────────────────────

def _install_event_hooks():
    from core.event_bus import EventType, bus

    @bus.on(EventType.DISTANCE_UPDATED)
    async def on_dist(event):
        d = event.data.get("distance_cm")
        if d is not None:
            _state["sensor_data"]["ultrasonic"] = f"{d:.1f} cm"
        imu = event.data.get("imu")
        if imu:
            _state["sensor_data"]["imu"] = (
                f"ax={imu['ax']:.2f} ay={imu['ay']:.2f} az={imu['az']:.2f}"
            )

    @bus.on(EventType.OBSTACLE_CRITICAL)
    async def on_crit(event):
        _log_event("OBSTACLE CRITICAL", event.data, "bold red")

    @bus.on(EventType.OBSTACLE_WARNING)
    async def on_warn(event):
        _log_event("obstacle warning", event.data, "yellow")

    @bus.on(EventType.MOTION_DETECTED)
    async def on_motion(event):
        _state["sensor_data"]["pir"] = f"MOTION ({event.data.get('source','')})"
        _log_event("motion detected", event.data, "cyan")

    @bus.on(EventType.CLIFF_DETECTED)
    async def on_cliff(event):
        _state["sensor_data"]["cliff"] = f"CLIFF side={event.data.get('side','?')}"
        _log_event("CLIFF DETECTED", event.data, "bold red")

    @bus.on(EventType.TOUCH_DETECTED)
    async def on_touch(event):
        _state["sensor_data"]["touch"] = f"TOUCH zone={event.data.get('zone','?')}"
        _log_event("touch", event.data, "green")

    @bus.on(EventType.PICKUP_DETECTED)
    async def on_pickup(event):
        _log_event("PICKUP DETECTED", event.data, "bold magenta")

    @bus.on(EventType.SOUND_DETECTED)
    async def on_sound(event):
        _state["sensor_data"]["sound"] = f"level={event.data.get('level','?')}"
        _log_event("sound", event.data, "blue")

    @bus.on(EventType.LIGHT_CHANGED)
    async def on_light(event):
        _state["sensor_data"]["light"] = f"{event.data.get('lux', 0):.1f} lux"
        _log_event("light changed", event.data, "yellow")


# ── Layout builder ────────────────────────────────────────────────────────────

def _build_layout() -> Table:
    now = time.monotonic()

    # Connection status
    if _state["connected"]:
        status_text = Text("● CONNECTED", style="bold green")
    elif _state["mock"]:
        status_text = Text("◌ MOCK MODE", style="bold yellow")
    else:
        status_text = Text("✗ DISCONNECTED", style="bold red")

    # Header row
    header = Table.grid(expand=True)
    header.add_column()
    header.add_column(justify="right")
    header.add_row(
        f"[bold cyan]ESP32-S3 Bridge Test[/]  {status_text}",
        f"uptime: {_state['esp_uptime']}s  hb: {_state['last_hb_age']:.1f}s ago",
    )

    # Sensor table
    sensor_table = Table(
        title="Sensor Data", box=box.SIMPLE, show_header=True,
        header_style="bold blue", min_width=40,
    )
    sensor_table.add_column("Sensor", style="cyan", width=14)
    sensor_table.add_column("Value", width=30)
    sensor_table.add_column("Wired", justify="center", width=6)

    sensor_map = {
        "ultrasonic": "HC-SR04",
        "pir":        "PIR",
        "cliff":      "Cliff",
        "touch":      "Touch",
        "sound":      "KY-038",
        "imu":        "MPU-6050",
        "light":      "BH1750",
    }
    esp32_sensors = _state.get("sensors", {})
    for key, label in sensor_map.items():
        val = _state["sensor_data"].get(key, "—")
        wired = esp32_sensors.get(key, False)
        wired_str = "[green]YES[/]" if wired else "[dim]no[/]"
        sensor_table.add_row(label, str(val), wired_str)

    # Motor table
    motor_table = Table(
        title="Motors", box=box.SIMPLE, show_header=False, min_width=28,
    )
    motor_table.add_column("Label", style="cyan", width=8)
    motor_table.add_column("Value", width=18)
    motor_table.add_row("Left",   f"{_state['motor_l']:+.2f}")
    motor_table.add_row("Right",  f"{_state['motor_r']:+.2f}")
    motor_table.add_row("Sent",   str(_state["cmd_sent"]))
    motor_table.add_row("Acked",  str(_state["cmd_acked"]))

    # Event log
    event_table = Table(
        title="Event Log (last 20)", box=box.SIMPLE, show_header=True,
        header_style="bold", min_width=60,
    )
    event_table.add_column("t+s", width=6, style="dim")
    event_table.add_column("Event", width=24)
    event_table.add_column("Data", width=30)
    for ev in _state["events"]:
        event_table.add_row(
            str(ev["ts"]),
            Text(ev["label"], style=ev["color"]),
            str(ev["data"])[:40],
        )

    # Errors
    error_lines = "\n".join(_state["errors"][-5:]) if _state["errors"] else "[dim]none[/]"

    root = Table.grid(expand=True, padding=(0, 1))
    root.add_column()
    root.add_row(Panel(header, box=box.MINIMAL))
    root.add_row(Columns([
        Panel(sensor_table, box=box.ROUNDED),
        Panel(motor_table, box=box.ROUNDED),
    ]))
    root.add_row(Panel(event_table, box=box.ROUNDED))
    root.add_row(Panel(error_lines, title="Errors", box=box.MINIMAL))
    return root


# ── Motor test sequence ───────────────────────────────────────────────────────

async def run_motor_test(bridge):
    steps = [
        ("forward",    0.4,  0.4,  0.8),
        ("stop",       0.0,  0.0,  0.3),
        ("backward",  -0.4, -0.4,  0.8),
        ("stop",       0.0,  0.0,  0.3),
        ("turn left", -0.3,  0.3,  0.6),
        ("stop",       0.0,  0.0,  0.2),
        ("turn right", 0.3, -0.3,  0.6),
        ("stop",       0.0,  0.0,  0.2),
    ]
    for label, l, r, dur in steps:
        _log_event(f"motor: {label}", {"l": l, "r": r}, "magenta")
        if l == 0 and r == 0:
            await bridge.send_stop()
        else:
            await bridge.send_motor(l, r)
        _state["motor_l"] = l
        _state["motor_r"] = r
        _state["cmd_sent"] += 1
        await asyncio.sleep(dur)
    _log_event("motor test complete", color="bold green")


# ── Heartbeat poller ──────────────────────────────────────────────────────────

async def status_poll(bridge):
    while True:
        status = bridge.get_status()
        _state["connected"]   = status["connected"]
        _state["mock"]        = status["mock"]
        _state["esp_uptime"]  = status["esp_uptime_s"]
        _state["last_hb_age"] = status["last_hb_age_s"]
        _state["sensors"]     = status["sensors"]
        await asyncio.sleep(0.5)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(run_motors: bool, force_mock: bool):
    from core.event_bus import bus
    from hardware.esp32_bridge import bridge

    await bus.start()

    if force_mock:
        bridge._mock = True
        bridge._connected = False

    _install_event_hooks()
    await bridge.start()

    asyncio.create_task(status_poll(bridge))

    _log_event("bridge started", {"mock": bridge.is_mock}, "green")

    await asyncio.sleep(1.0)  # let first heartbeat arrive

    if run_motors and not bridge.is_mock:
        asyncio.create_task(run_motor_test(bridge))

    with Live(_build_layout(), refresh_per_second=4, console=console) as live:
        try:
            while True:
                live.update(_build_layout())
                await asyncio.sleep(0.25)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

    await bridge.stop()
    await bus.stop()
    console.print("[bold green]Test complete.[/]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ESP32 bridge test dashboard")
    parser.add_argument("--mock",   action="store_true", help="Force mock mode")
    parser.add_argument("--motors", action="store_true", help="Run motor test sequence")
    args = parser.parse_args()

    try:
        asyncio.run(main(run_motors=args.motors, force_mock=args.mock))
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/]")
