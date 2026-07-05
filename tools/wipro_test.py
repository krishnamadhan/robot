#!/usr/bin/env python3
"""
Interactive test for the Wipro smart bulb (Tuya local control).

Usage:
    PYTHONPATH=/home/pi/robot WIPRO_LOCAL_KEY=<key> python3 tools/wipro_test.py

Controls:
    r / g / b / w   — red / green / blue / white
    1–9             — brightness 10%–90%
    0               — full bright
    o               — off
    t               — TV-sync demo (cycle rainbow at 1.5 Hz for 20 s)
    q               — quit
"""
import asyncio
import sys
import os

sys.path.insert(0, "/home/pi/robot")

from hardware.wipro_light import wipro


COLOURS = {
    "r": (255, 30,  30),
    "g": (30,  220, 80),
    "b": (30,  80,  255),
    "w": (255, 240, 220),
}


async def main():
    if not wipro.init():
        print("WIPRO_LOCAL_KEY not set — export it first:\n"
              "  export WIPRO_LOCAL_KEY=<your key>")
        return

    print("Wipro test ready. Commands: r g b w  1-9 0  o t q")

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    bright = 80

    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == "q":
                break
            elif ch == "o":
                await wipro.power(False)
                print("\r[off]          ", end="", flush=True)
            elif ch in COLOURS:
                r, g, b = COLOURS[ch]
                await wipro.set_color(r, g, b, bright)
                print(f"\r[{ch} rgb({r},{g},{b}) @{bright}%]  ", end="", flush=True)
            elif ch.isdigit():
                bright = int(ch) * 10 if ch != "0" else 100
                print(f"\r[brightness {bright}%]  ", end="", flush=True)
            elif ch == "t":
                print("\r[TV-sync demo 20 s — Ctrl-C to abort]", end="", flush=True)
                import colorsys
                for i in range(30):
                    h = (i / 30.0)
                    r2, g2, b2 = [int(x * 255) for x in colorsys.hsv_to_rgb(h, 0.9, 1.0)]
                    await wipro.set_color(r2, g2, b2, bright)
                    await asyncio.sleep(0.67)
                print("\r[demo done]          ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\nOff.")
        await wipro.power(False)


if __name__ == "__main__":
    asyncio.run(main())
