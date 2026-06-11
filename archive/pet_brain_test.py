#!/usr/bin/env python3
"""
Pet Brain Test — interactive dashboard to observe and override PetBrain decisions.

Simulates personality state, sensor readings, and social context,
then shows real-time PetBrain state transitions and movement intents.

Usage:
    python3 tools/pet_brain_test.py

Keys:
    p  — toggle person visible
    e  — cycle emotion (neutral → happy → sad → angry)
    +  — boost energy +0.1
    -  — reduce energy -0.1
    m  — boost mood +0.1
    n  — drop mood -0.1
    c  — boost curiosity trait +0.1
    d  — reduce curiosity trait -0.1
    l  — toggle lux (bright 400 / dark 15)
    o  — toggle obstacle (far 150cm / near 12cm)
    r  — reset all to defaults
    q  — quit
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

# ── Simulated world state ──────────────────────────────────────────────────────
sim = {
    "energy":         0.7,
    "mood":           0.55,
    "curiosity":      0.70,
    "person_visible": False,
    "person_x":       0.0,
    "person_dist_cm": 80.0,
    "lux":            350.0,
    "dist_cm":        100.0,
    "hour":           14,
    "emotion":        "neutral",
}

# ── History ────────────────────────────────────────────────────────────────────
history: list = []      # (timestamp, old_state, new_state, reason)
intents: list = []      # (timestamp, MovementIntent)

_EMOTIONS = ["neutral", "happy", "sad", "angry", "fearful", "surprised"]
_emotion_idx = 0


def _bar(v: float, w: int = 10, lo: float = 0.0, hi: float = 1.0) -> str:
    frac = max(0.0, min(1.0, (v - lo) / (hi - lo)))
    n = int(frac * w)
    fill = "█" * n + "░" * (w - n)
    color = "green" if frac > 0.55 else ("yellow" if frac > 0.3 else "red")
    return f"[{color}]{fill}[/{color}] {v:+.2f}" if lo < 0 else f"[{color}]{fill}[/{color}] {v:.2f}"


def _state_color(s: str) -> str:
    return {
        "resting":         "dim",
        "curious_wander":  "cyan",
        "seek_light":      "yellow",
        "approach_person": "green",
        "play":            "bold magenta",
        "flee":            "bold red",
    }.get(s, "white")


def _build_panel(intent) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="top", size=16),
        Layout(name="bottom"),
    )
    layout["top"].split_row(
        Layout(name="sim", ratio=1),
        Layout(name="decision", ratio=1),
    )

    # ── Simulation state ────────────────────────────────────────────────────
    sim_table = Table(show_header=False, box=None, padding=(0, 1))
    sim_table.add_column("key",   style="bold dim", width=18)
    sim_table.add_column("value", width=22)

    sim_table.add_row("Energy",    _bar(sim["energy"]))
    sim_table.add_row("Mood",      _bar(sim["mood"], lo=-1.0, hi=1.0))
    sim_table.add_row("Curiosity", _bar(sim["curiosity"]))
    sim_table.add_row("Lux",       f"{sim['lux']:.0f} lx {'[yellow]DARK[/yellow]' if sim['lux'] < 40 else ''}")
    sim_table.add_row("Dist cm",   f"{sim['dist_cm']:.0f} cm {'[red]CLOSE[/red]' if sim['dist_cm'] < 20 else ''}")
    sim_table.add_row("Person",    f"[green]YES x={sim['person_x']:+.1f}[/green]" if sim["person_visible"] else "[dim]no[/dim]")
    sim_table.add_row("Emotion",   f"[cyan]{sim['emotion']}[/cyan]")
    sim_table.add_row("Hour",      f"{sim['hour']:02d}:00 {'[dim]NIGHT[/dim]' if sim['hour'] >= 23 or sim['hour'] < 7 else ''}")
    sim_table.add_row("", "")
    sim_table.add_row("[dim]p=person e=emotion +/-=energy[/dim]", "")
    sim_table.add_row("[dim]m/n=mood c/d=curiosity l=lux o=obstacle[/dim]", "")

    layout["sim"].update(Panel(sim_table, title="[bold]Simulation[/bold]", border_style="blue"))

    # ── Current decision ─────────────────────────────────────────────────────
    if intent:
        state_c = _state_color(intent.state.value)
        dec_text = Text()
        dec_text.append(f"\n  State:     ", style="bold")
        dec_text.append(f"{intent.state.value.upper()}\n", style=f"bold {state_c}")
        dec_text.append(f"  Direction: {intent.direction}\n")
        dec_text.append(f"  Speed:     {intent.speed:.2f}\n")
        dec_text.append(f"  Duration:  {intent.duration:.1f}s\n")
        dec_text.append(f"  Eyes:      {intent.eye_expr.value}\n")
        dec_text.append(f"  Sound:     {intent.sound or '—'}\n")
    else:
        dec_text = Text("\n  No decision yet...", style="dim")

    layout["decision"].update(Panel(dec_text, title="[bold]Current Intent[/bold]", border_style="cyan"))

    # ── History log ──────────────────────────────────────────────────────────
    hist_table = Table(show_header=True, box=None, padding=(0, 1))
    hist_table.add_column("Time",  width=8,  style="dim")
    hist_table.add_column("State", width=18)
    hist_table.add_column("Dir",   width=12)
    hist_table.add_column("Spd",   width=5)
    hist_table.add_column("Dur",   width=5)

    for ts, i in reversed(intents[-12:]):
        age = f"{time.monotonic() - ts:.0f}s ago"
        sc  = _state_color(i.state.value)
        hist_table.add_row(
            age,
            f"[{sc}]{i.state.value}[/{sc}]",
            i.direction,
            f"{i.speed:.2f}",
            f"{i.duration:.1f}",
        )

    layout["bottom"].update(Panel(hist_table, title="[bold]Decision History[/bold]", border_style="dim"))
    return layout


async def input_loop(brain) -> None:
    """Non-blocking key handler using stdin in raw mode."""
    import termios
    import tty

    global _emotion_idx

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    try:
        while True:
            ch = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.read, 1)
            if ch in ("q", "Q", "\x03"):
                break
            elif ch == "p":
                sim["person_visible"] = not sim["person_visible"]
                brain._person_visible = sim["person_visible"]
            elif ch == "e":
                _emotion_idx = (_emotion_idx + 1) % len(_EMOTIONS)
                sim["emotion"] = _EMOTIONS[_emotion_idx]
            elif ch == "+":
                sim["energy"] = min(1.0, sim["energy"] + 0.1)
            elif ch == "-":
                sim["energy"] = max(0.0, sim["energy"] - 0.1)
            elif ch == "m":
                sim["mood"] = min(1.0, sim["mood"] + 0.1)
            elif ch == "n":
                sim["mood"] = max(-1.0, sim["mood"] - 0.1)
            elif ch == "c":
                sim["curiosity"] = min(1.0, sim["curiosity"] + 0.1)
            elif ch == "d":
                sim["curiosity"] = max(0.0, sim["curiosity"] - 0.1)
            elif ch == "l":
                sim["lux"] = 15.0 if sim["lux"] > 100 else 400.0
            elif ch == "o":
                sim["dist_cm"] = 12.0 if sim["dist_cm"] > 50 else 150.0
            elif ch == "r":
                sim.update({
                    "energy": 0.7, "mood": 0.55, "curiosity": 0.70,
                    "person_visible": False, "lux": 350.0,
                    "dist_cm": 100.0, "emotion": "neutral",
                })
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


async def main() -> None:
    import datetime

    # Minimal bootstrap — no real hardware, no event bus needed for PetBrain logic
    from cognition.pet_brain import PetBrain, PetState

    brain = PetBrain()
    current_intent = None

    async def _decision_loop():
        nonlocal current_intent
        while True:
            personality_dict = {
                "energy":    sim["energy"],
                "mood":      sim["mood"],
                "curiosity": sim["curiosity"],
            }
            intent = brain._decide(
                energy=sim["energy"],
                mood=sim["mood"],
                curiosity=sim["curiosity"],
                lux=sim["lux"],
                dist_cm=sim["dist_cm"],
                person_visible=sim["person_visible"],
                hour=sim["hour"],
            )

            # Simulate hold timer
            now = time.monotonic()
            if intent.state != brain.state:
                from cognition.pet_brain import _STATE_HOLD
                import random
                brain.state = intent.state
                brain._hold_until = now + _STATE_HOLD[brain.state] * random.uniform(0.85, 1.15)
                intents.append((now, intent))

            current_intent = intent
            await asyncio.sleep(0.5)

    console.print("\n[bold cyan]Cosmo Pet Brain Test[/bold cyan]")
    console.print("[dim]Testing PetBrain decision engine — no hardware needed[/dim]\n")

    # Run the key input and decision loops together
    done_event = asyncio.Event()

    async def run_ui():
        with Live(console=console, refresh_per_second=4, screen=True) as live:
            while not done_event.is_set():
                live.update(_build_panel(current_intent))
                await asyncio.sleep(0.25)

    decision_task = asyncio.create_task(_decision_loop())
    ui_task       = asyncio.create_task(run_ui())
    input_task    = asyncio.create_task(input_loop(brain))

    # Wait for input_loop to exit (q pressed)
    try:
        await input_task
    except Exception:
        pass
    finally:
        done_event.set()
        decision_task.cancel()
        ui_task.cancel()

    console.print("\n[dim]Pet Brain test exited.[/dim]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
