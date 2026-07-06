#!/usr/bin/env python3
"""
Interactive test for the Wipro smart bulb (Tuya local, music-mode sync engine).

Usage:
    cd /home/pi/robot && PYTHONPATH=/home/pi/robot python3 tools/wipro_test.py
    (WIPRO_LOCAL_KEY is loaded from robot/.env automatically)

Controls:
    r / g / b / w   — red / green / blue / white
    1–9             — brightness 10%–90%
    0               — full bright
    o               — off
    t               — smooth sync demo (rainbow via music mode, 20 s)
    s               — show send stats (ok/fail/music-mode)
    q               — quit
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/pi/robot")

# Load robot/.env (WIPRO_LOCAL_KEY)
_env = Path("/home/pi/robot/.env")
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from hardware.wipro_light import wipro


COLOURS = {
    "r": (255, 30,  30),
    "g": (30,  220, 80),
    "b": (30,  80,  255),
    "w": (255, 240, 220),
}


async def main():
    if not wipro.init():
        print("WIPRO_LOCAL_KEY not set in robot/.env")
        return

    print("Wipro test ready. Commands: r g b w  1-9 0  o t s q")

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    bright = 80

    try:
        tty.setraw(fd)
        while True:
            # Non-blocking-ish read: run in executor so the sync worker runs
            ch = await asyncio.get_event_loop().run_in_executor(
                None, sys.stdin.read, 1
            )
            if ch == "q":
                break
            elif ch == "o":
                wipro.power_off()
                print("\r[off]                    ", end="", flush=True)
            elif ch == "s":
                print(f"\r{wipro.stats}                ", end="", flush=True)
            elif ch in COLOURS:
                r, g, b = COLOURS[ch]
                wipro.set_color(r, g, b, bright)
                print(f"\r[{ch} rgb({r},{g},{b}) @{bright}%]   ", end="", flush=True)
            elif ch.isdigit():
                bright = int(ch) * 10 if ch != "0" else 100
                print(f"\r[brightness {bright}%]        ", end="", flush=True)
            elif ch == "t":
                print("\r[smooth rainbow 20 s — watch for jumps]", end="", flush=True)
                import colorsys
                for i in range(120):   # 6 Hz feed, worker coalesces to ~3 Hz
                    h = (i / 40.0) % 1.0
                    r2, g2, b2 = [int(x * 255) for x in colorsys.hsv_to_rgb(h, 0.9, 1.0)]
                    wipro.set_color(r2, g2, b2, bright)
                    await asyncio.sleep(0.167)
                print("\r[demo done]                            ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\nOff.")
        wipro.power_off()
        await asyncio.sleep(0.8)   # let the worker deliver the off
        await wipro.stop()


if __name__ == "__main__":
    asyncio.run(main())
