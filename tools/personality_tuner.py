#!/usr/bin/env python3
"""
Personality tuner — live adjust Cosmo's emotional state + inject events.

Usage:
  python3 tools/personality_tuner.py

Commands:
  show                  Show current emotional state
  mood <-1.0 to 1.0>   Set mood directly
  energy <0.0 to 1.0>  Set energy
  arousal <0.0 to 1.0> Set arousal
  inject touch          Simulate a touch event
  inject pickup         Simulate a pickup event
  inject face <name>    Simulate face recognition
  inject gesture <g>    Simulate gesture (up/down/left/right/wave)
  inject battery_low    Simulate low battery
  inject motion         Simulate PIR motion
  history               Show mood history (last 10)
  reset                 Reset to personality defaults
  persons               List known persons
  watch                 Live-watch personality state (Ctrl+C to stop)
  q / quit              Exit
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.event_bus import Event, EventPriority, EventType, bus
from core.personality import personality


def _bar(val: float, lo: float = -1.0, hi: float = 1.0,
         width: int = 20) -> str:
    pct = (val - lo) / (hi - lo)
    filled = int(pct * width)
    return "[" + "█" * filled + "░" * (width - filled) + f"] {val:+.2f}"


def _bar_pos(val: float, width: int = 20) -> str:
    return _bar(val, 0.0, 1.0, width)


def cmd_show(args):
    s = personality.state
    print("\n  ── Cosmo Emotional State ──────────────────")
    print(f"  Mood      {_bar(s.mood)}")
    print(f"  Energy    {_bar_pos(s.energy)}")
    print(f"  Arousal   {_bar_pos(s.arousal)}")
    print(f"  Attachment{_bar_pos(s.attachment)}")
    reasons = personality.introspect()
    if isinstance(reasons, list):
        print(f"\n  Feels: {', '.join(reasons[:3])}" if reasons else "")
    elif isinstance(reasons, str):
        print(f"\n  Feels: {reasons[:80]}")
    print()


def cmd_mood(args):
    if not args:
        print("  Usage: mood <-1.0 to 1.0>")
        return
    val = float(args[0])
    personality.state.mood = max(-1.0, min(1.0, val))
    personality.state.clamp()
    print(f"  Mood set to {personality.state.mood:+.2f}")


def cmd_energy(args):
    if not args:
        print("  Usage: energy <0.0 to 1.0>")
        return
    val = float(args[0])
    personality.state.energy = max(0.0, min(1.0, val))
    print(f"  Energy set to {personality.state.energy:.2f}")


def cmd_arousal(args):
    if not args:
        print("  Usage: arousal <0.0 to 1.0>")
        return
    val = float(args[0])
    personality.state.arousal = max(0.0, min(1.0, val))
    print(f"  Arousal set to {personality.state.arousal:.2f}")


async def cmd_inject(args):
    if not args:
        print("  Usage: inject <touch|pickup|face <name>|gesture <g>|battery_low|motion>")
        return
    kind = args[0].lower()
    if kind == "touch":
        await bus.publish(Event(type=EventType.TOUCH_DETECTED,
                                data={"zone": "head"}, priority=EventPriority.HIGH))
        print("  Injected: TOUCH_DETECTED (head)")
    elif kind == "pickup":
        await bus.publish(Event(type=EventType.PICKUP_DETECTED,
                                data={"accel_g": 3.0}, priority=EventPriority.SAFETY))
        print("  Injected: PICKUP_DETECTED")
    elif kind == "face":
        name = args[1] if len(args) > 1 else "Madhan"
        pid  = f"person_{name.lower()}"
        await bus.publish(Event(type=EventType.FACE_RECOGNIZED,
                                data={"person_id": pid, "name": name, "confidence": 0.9},
                                priority=EventPriority.HIGH))
        print(f"  Injected: FACE_RECOGNIZED ({name})")
    elif kind == "gesture":
        gesture = args[1] if len(args) > 1 else "wave"
        await bus.publish(Event(type=EventType.GESTURE_DETECTED,
                                data={"gesture": gesture}, priority=EventPriority.HIGH))
        print(f"  Injected: GESTURE_DETECTED ({gesture})")
    elif kind == "battery_low":
        await bus.publish(Event(type=EventType.BATTERY_LOW,
                                data={"percent": 18.0}, priority=EventPriority.NORMAL))
        print("  Injected: BATTERY_LOW (18%)")
    elif kind == "motion":
        await bus.publish(Event(type=EventType.MOTION_DETECTED,
                                data={"source": "pir_sim"}, priority=EventPriority.HIGH))
        print("  Injected: MOTION_DETECTED")
    else:
        print(f"  Unknown inject type: {kind!r}")


def cmd_history(args):
    hist = getattr(personality, "_mood_history", [])
    if not hist:
        print("  No mood history recorded")
        return
    print("\n  Mood History (last 10)")
    print("  ─────────────────────")
    for t, mood in hist[-10:]:
        ago = int(time.monotonic() - t)
        bar = _bar(mood)
        print(f"  {ago:>4}s ago  {bar}")
    print()


def cmd_reset(args):
    personality.state.mood      = 0.6
    personality.state.energy    = 0.7
    personality.state.arousal   = 0.5
    personality.state.attachment = 0.6
    print("  Personality reset to defaults")


def cmd_persons(args):
    persons = getattr(personality, "_persons", {})
    if not persons:
        print("  No persons in memory")
        return
    print(f"\n  {'ID':<20}  {'Name':<12}  {'Quality':>7}  {'Interactions':>12}  Last seen")
    print("  " + "─" * 70)
    for pid, p in persons.items():
        ago = int(time.monotonic() - p.last_seen)
        print(f"  {pid:<20}  {p.name or '?':<12}  {p.relationship_quality:>7.2f}  "
              f"{p.interaction_count:>12}  {ago}s ago")
    print()


async def cmd_watch(args):
    print("  Watching personality state... (Ctrl+C to stop)\n")
    try:
        while True:
            s = personality.state
            line = (f"\r  M:{s.mood:+.2f}  E:{s.energy:.2f}  "
                    f"A:{s.arousal:.2f}  Att:{s.attachment:.2f}  ")
            print(line, end="", flush=True)
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        print()


async def run() -> None:
    print("\n  Cosmo Personality Tuner")
    print("  Type 'help' for commands, 'q' to quit\n")

    commands = {
        "show":    (cmd_show,    False),
        "mood":    (cmd_mood,    False),
        "energy":  (cmd_energy,  False),
        "arousal": (cmd_arousal, False),
        "inject":  (cmd_inject,  True),
        "history": (cmd_history, False),
        "reset":   (cmd_reset,   False),
        "persons": (cmd_persons, False),
        "watch":   (cmd_watch,   True),
    }

    while True:
        try:
            line = input("  tuner> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line.lower() in ("q", "quit", "exit"):
            break
        if line.lower() == "help":
            print(__doc__)
            continue

        parts = line.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in commands:
            fn, is_async = commands[cmd]
            try:
                if is_async:
                    await fn(args)
                else:
                    fn(args)
            except Exception as e:
                print(f"  Error: {e}")
        else:
            print(f"  Unknown command: {cmd!r}  (type 'help')")


if __name__ == "__main__":
    asyncio.run(run())
