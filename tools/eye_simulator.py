#!/usr/bin/env python3
"""
Eye simulator — live terminal preview of all 12 expressions.

Cycles through expressions automatically, or use keyboard shortcuts.

Controls:
  N  neutral    H  happy     E  excited   S  sad
  A  angry      V  surprised L  sleepy    O  loving
  C  curious    F  scared    X  confused  P  playful
  B  force blink               Q  quit
  1-9  move pupil              0  center pupil
"""

import asyncio
import os
import sys
import time
import tty
import termios
import select
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from expression.eyes import EyeExpression, EyeState, eye_engine
from core.personality import personality

EXPR_KEYS = {
    "n": EyeExpression.NEUTRAL,
    "h": EyeExpression.HAPPY,
    "e": EyeExpression.EXCITED,
    "s": EyeExpression.SAD,
    "a": EyeExpression.ANGRY,
    "v": EyeExpression.SURPRISED,
    "l": EyeExpression.SLEEPY,
    "o": EyeExpression.LOVING,
    "c": EyeExpression.CURIOUS,
    "f": EyeExpression.SCARED,
    "x": EyeExpression.CONFUSED,
    "p": EyeExpression.PLAYFUL,
}

AUTO_CYCLE = list(EyeExpression)
CYCLE_INTERVAL = 3.0

_MOOD_COLORS = {
    range(-100, -50): "\033[31m",
    range(-50, 0):    "\033[33m",
    range(0, 50):     "\033[37m",
    range(50, 101):   "\033[32m",
}


def _mood_color(mood: float) -> str:
    m = int(mood * 100)
    for r, c in _MOOD_COLORS.items():
        if m in r:
            return c
    return "\033[37m"


def _mood_bar(mood: float, energy: float, arousal: float, width: int = 20) -> str:
    def bar(val, lo, hi):
        pct = (val - lo) / (hi - lo)
        n = int(pct * width)
        return "\u2588" * n + "\u2591" * (width - n)

    m_bar = bar(mood, -1.0, 1.0)
    e_bar = bar(energy, 0.0, 1.0)
    a_bar = bar(arousal, 0.0, 1.0)
    return (f"  Mood   [{m_bar}] {mood:+.2f}\n"
            f"  Energy [{e_bar}]  {energy:.2f}\n"
            f"  Arousal[{a_bar}]  {arousal:.2f}")


def _get_key(fd) -> str:
    if select.select([sys.stdin], [], [], 0)[0]:
        ch = sys.stdin.read(1)
        return ch.lower()
    return ""


async def run_simulator() -> None:
    await eye_engine.start()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    cycle_idx  = 0
    last_cycle = time.monotonic()
    pupil_positions = [
        (0.0, 0.0),    # 0 center
        (-0.8, -0.8),  # 1 top-left
        (0.0, -0.8),   # 2 top
        (0.8, -0.8),   # 3 top-right
        (-0.8, 0.0),   # 4 left
        (0.0, 0.0),    # 5 center
        (0.8, 0.0),    # 6 right
        (-0.8, 0.8),   # 7 bottom-left
        (0.0, 0.8),    # 8 bottom
        (0.8, 0.8),    # 9 bottom-right
    ]

    try:
        while True:
            now = time.monotonic()

            key = _get_key(fd)
            if key == "q":
                break
            elif key in EXPR_KEYS:
                eye_engine.set_expression(EXPR_KEYS[key])
                last_cycle = now + 10
            elif key == "b":
                eye_engine._blinking = True
                eye_engine._blink_start = now
            elif key.isdigit():
                idx = int(key)
                if idx < len(pupil_positions):
                    px, py = pupil_positions[idx]
                    eye_engine.set_pupil(px, py)
                    last_cycle = now + 5

            if now - last_cycle >= CYCLE_INTERVAL:
                last_cycle = now
                cycle_idx = (cycle_idx + 1) % len(AUTO_CYCLE)
                eye_engine.set_expression(AUTO_CYCLE[cycle_idx])

            state  = eye_engine.get_state()
            art    = eye_engine.render_terminal()
            s      = personality.state
            mood_c = _mood_color(s.mood)
            reset  = "\033[0m"

            lines = [
                "\033[H\033[2J",
                "\033[1m  \u2554\u2550\u2550\u2550 Cosmo Eye Simulator \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557\033[0m",
                "",
            ]

            for line in art.split("\n"):
                lines.append(f"  {line}")

            lines += [
                "",
                (f"  Expression: \033[1;36m{state.expression.value.upper()}\033[0m"
                 + (f"  -> \033[33m{state.target_expression.value}\033[0m"
                    if state.target_expression != state.expression else "")),
                (f"  Blink: {'|||' if state.blink_progress > 0.3 else '...'}"
                 f"  Pupil: ({state.pupil_x:+.1f}, {state.pupil_y:+.1f})"),
                "",
                f"{mood_c}{_mood_bar(s.mood, s.energy, s.arousal)}{reset}",
                "",
                "\033[90m  [N]eutral [H]appy [E]xcited [S]ad [A]ngry [V]surprised\033[0m",
                "\033[90m  [L]sleepy [O]loving [C]curious [F]scared [X]confused [P]layful\033[0m",
                "\033[90m  [B]link  [1-9]pupil  [Q]uit  (auto-cycles every 3s)\033[0m",
            ]

            print("\n".join(lines), end="", flush=True)
            await asyncio.sleep(1.0 / 15)

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        print("\033[?25h\033[0m\n  Eye simulator stopped.")
        await eye_engine.stop()


if __name__ == "__main__":
    print("\033[?25l", end="")
    asyncio.run(run_simulator())
