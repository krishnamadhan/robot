"""
Real-time sensor dashboard using Rich.
Run: python3 tools/sensor_monitor.py

Shows: all sensor values, event bus activity, robot state/mood, system health.
Updates every 500ms. Press Ctrl+C to exit.
"""

import asyncio
import sys
import time
from pathlib import Path

# Add robot root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def _mood_bar(value: float, width: int = 20) -> str:
    """Render -1..1 value as colored ASCII bar."""
    normalized = (value + 1) / 2  # -1..1 → 0..1
    filled = int(normalized * width)
    bar = "█" * filled + "░" * (width - filled)
    return bar


def _energy_bar(value: float, width: int = 20) -> str:
    filled = int(value * width)
    bar = "█" * filled + "░" * (width - filled)
    return bar


def build_dashboard(robot_state: dict) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="center"),
        Layout(name="right"),
    )

    # ── Header ─────────────────────────────────────────────────────────────
    header_text = Text("🤖 COSMO SENSOR MONITOR", style="bold cyan", justify="center")
    ts = time.strftime("%H:%M:%S")
    layout["header"].update(Panel(header_text, subtitle=ts))

    # ── Left: Personality + State ───────────────────────────────────────────
    pers = robot_state.get("personality", {})
    state = pers.get("state", {})
    mood = state.get("mood", 0.0)
    energy = state.get("energy", 0.0)
    arousal = state.get("arousal", 0.5)
    attachment = state.get("attachment", 0.5)

    pers_table = Table(show_header=False, box=None, padding=(0, 1))
    pers_table.add_column("Key", style="dim")
    pers_table.add_column("Bar")
    pers_table.add_column("Val", style="bold")

    mood_color = "green" if mood > 0.3 else ("yellow" if mood > -0.1 else "red")
    pers_table.add_row("Mood", f"[{mood_color}]{_mood_bar(mood)}[/]", f"{mood:+.2f}")
    pers_table.add_row("Energy", f"[blue]{_energy_bar(energy)}[/]", f"{energy:.2f}")
    pers_table.add_row("Arousal", f"[magenta]{_energy_bar(arousal)}[/]", f"{arousal:.2f}")
    pers_table.add_row("Bond", f"[cyan]{_energy_bar(attachment)}[/]", f"{attachment:.2f}")

    thresholds = robot_state.get("thresholds", {})
    flags = [k for k, v in thresholds.items() if v]
    flags_str = ", ".join(flags) if flags else "none"

    sm_state = robot_state.get("state_machine", {}).get("current", "unknown")
    sm_time = robot_state.get("state_machine", {}).get("time_in_state_s", 0)

    left_content = f"""[bold]Behavioral State[/bold]
{sm_state}
In state: {sm_time:.0f}s

[bold]Emotional State[/bold]
"""
    layout["left"].update(Panel(
        str(left_content) + "\n",
        title="[cyan]Personality",
        renderable=Panel(pers_table, title=f"State: [bold yellow]{sm_state}[/]",
                          subtitle=f"Flags: {flags_str}"),
    ))

    # ── Center: Sensors ─────────────────────────────────────────────────────
    sensors = robot_state.get("sensors", {})
    sens_table = Table(title="Sensors", show_header=True, header_style="bold")
    sens_table.add_column("Sensor", style="dim")
    sens_table.add_column("Value")
    sens_table.add_column("Status")

    for sensor_name, sensor_data in sensors.items():
        if isinstance(sensor_data, dict):
            avail = sensor_data.get("available", False)
            error = sensor_data.get("error")
            if error:
                val = f"[red]ERROR: {error}[/]"
                status = "[red]FAIL[/]"
            elif not avail:
                val = "[dim]SIM[/dim]"
                status = "[yellow]MOCK[/]"
            else:
                # Format value — show first numeric field
                val_parts = []
                for k, v in sensor_data.items():
                    if k not in ("available", "error") and isinstance(v, (int, float)):
                        val_parts.append(f"{k}={v:.1f}" if isinstance(v, float) else f"{k}={v}")
                val = " ".join(val_parts[:2]) or str(sensor_data)
                status = "[green]OK[/]"
            sens_table.add_row(sensor_name, val, status)
        else:
            sens_table.add_row(sensor_name, str(sensor_data), "[dim]?[/]")

    layout["center"].update(Panel(sens_table, title="[cyan]Sensors"))

    # ── Right: Events + System ──────────────────────────────────────────────
    events = robot_state.get("recent_events", [])
    event_table = Table(show_header=False, box=None, padding=(0, 1))
    event_table.add_column("Age", style="dim", width=6)
    event_table.add_column("Type", width=30)

    for ev in events[:12]:
        age = ev.get("age_s", 0)
        etype = ev.get("type", "?")
        age_str = f"{age:.0f}s"
        color = "red" if "safety" in etype else ("yellow" if "perception" in etype else "white")
        event_table.add_row(age_str, f"[{color}]{etype}[/]")

    sys_info = robot_state.get("system", {})
    cpu = sys_info.get("cpu_percent", 0)
    temp = sys_info.get("cpu_temp_c")
    mem = sys_info.get("memory", {})
    mem_pct = mem.get("percent", 0) if mem else 0
    cam_fps = robot_state.get("camera", {}).get("fps", 0)
    persons = robot_state.get("persons", 0)

    cpu_color = "red" if cpu > 80 else ("yellow" if cpu > 60 else "green")
    temp_str = f"{temp:.1f}°C" if temp else "N/A"
    temp_color = "red" if (temp and temp > 75) else "green"

    sys_str = (
        f"CPU: [{cpu_color}]{cpu:.0f}%[/]  Temp: [{temp_color}]{temp_str}[/]\n"
        f"RAM: {mem_pct:.0f}%   Camera: {cam_fps:.0f} FPS\n"
        f"Persons visible: [bold]{persons}[/]"
    )

    layout["right"].update(Panel(
        f"{sys_str}\n\n[bold]Recent Events[/bold]\n",
        title="[cyan]System",
        renderable=Table.grid(padding=0),
    ))
    # Simpler right panel
    from rich.console import Group
    layout["right"].update(Panel(
        Group(
            Text(sys_str, justify="left"),
            Text("\nRecent Events:", style="bold"),
            event_table,
        ),
        title="[cyan]System + Events",
    ))

    # ── Footer ─────────────────────────────────────────────────────────────
    bus_stats = robot_state.get("event_bus", {})
    footer_text = (
        f"Events: {bus_stats.get('dispatched', 0)} dispatched  |  "
        f"Queue: {bus_stats.get('queue_depth', 0)}  |  "
        f"Dead letter: {bus_stats.get('dead_letter', 0)}  |  "
        f"Press Ctrl+C to exit"
    )
    layout["footer"].update(Panel(Text(footer_text, justify="center", style="dim")))

    return layout


async def run_monitor() -> None:
    """Run in simulation mode — reads from mock hardware."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from core.event_bus import bus, Event, EventType
    from core.personality import personality
    from core.state_machine import sm, RobotState
    from hardware.mock import mock_hardware
    from utils.telemetry import telemetry
    from perception.vision.camera import camera

    # Initialize subsystems
    await bus.start()
    await mock_hardware.initialize_all()
    await personality.start()
    await sm.start(RobotState.IDLE_CALM)

    # Try starting camera
    cam_available = await camera.start()

    console = Console()
    console.print("[bold green]Cosmo Sensor Monitor starting...[/]")

    with Live(console=console, refresh_per_second=2, screen=True) as live:
        while True:
            await asyncio.sleep(0.5)

            # Collect sensor readings
            sensors = {}
            sensors["light"] = await mock_hardware.light.read_safe()
            sensors["imu"] = await mock_hardware.imu.read_safe()
            sensors["ultrasonic"] = await mock_hardware.ultrasonic.read_safe()
            sensors["pir"] = await mock_hardware.pir.read_safe()

            # Collect system info
            snap = telemetry.snapshot()

            # Recent events
            recent = bus.recent(max_age_s=30, limit=15)
            events_data = [
                {"type": str(e.type), "age_s": e.age_ms() / 1000}
                for e in recent
            ]

            robot_state = {
                "personality": personality.introspect(),
                "thresholds": personality.check_thresholds(),
                "state_machine": sm.stats(),
                "sensors": sensors,
                "recent_events": events_data,
                "event_bus": bus.stats(),
                "system": snap,
                "camera": camera.stats() if cam_available else {"fps": 0},
                "persons": 0,
            }

            try:
                live.update(build_dashboard(robot_state))
            except Exception as e:
                console.print(f"[red]Dashboard error: {e}[/]")


def main() -> None:
    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        print("\n[Cosmo monitor exited]")


if __name__ == "__main__":
    main()
