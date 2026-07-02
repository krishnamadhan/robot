#!/usr/bin/env python3
"""Interactive test tool for the LEDDMX BLE strip (hardware/led_strip.py).

  PYTHONPATH=/home/pi/robot python3 tools/led_test.py          # run demo cycle
  PYTHONPATH=/home/pi/robot python3 tools/led_test.py red      # set a named colour
  PYTHONPATH=/home/pi/robot python3 tools/led_test.py 255 0 128 # set raw RGB
  PYTHONPATH=/home/pi/robot python3 tools/led_test.py bright 40 # brightness %
  PYTHONPATH=/home/pi/robot python3 tools/led_test.py off / on

Strip is single-connection: disconnect the phone's LED LAMP app first.
If it won't wake, physically power-cycle the controller (~5s off).
"""
import asyncio
import sys

sys.path.insert(0, "/home/pi/robot")
from hardware.led_strip import LedStrip, COLORS  # noqa: E402


async def main() -> None:
    args = sys.argv[1:]
    s = LedStrip()

    if not args:
        print("demo: cycling colours + brightness + soft off/on...")
        for name in ("red", "green", "blue", "white", "warm"):
            ok = await s.set_named(name)
            print(f"  {name}: {'ok' if ok else 'FAILED — strip powered? phone disconnected?'}")
            if not ok:
                return
            await asyncio.sleep(2)
        await s.set_brightness(30); print("  brightness 30%"); await asyncio.sleep(2)
        await s.power(False); print("  soft off"); await asyncio.sleep(2)
        await s.power(True); print("  soft on"); await asyncio.sleep(2)
        await s.disconnect()
        return

    cmd = args[0].lower()
    if cmd in COLORS:
        ok = await s.set_named(cmd)
    elif cmd in ("on",):
        ok = await s.power(True)
    elif cmd in ("off",):
        ok = await s.power(False)
    elif cmd in ("bright", "brightness") and len(args) > 1:
        ok = await s.set_brightness(int(args[1]))
    elif len(args) == 3 and all(a.isdigit() for a in args):
        ok = await s.set_color(int(args[0]), int(args[1]), int(args[2]))
    else:
        print(f"unknown: {args}\ncolours: {', '.join(COLORS)}")
        return
    print("ok" if ok else "FAILED — strip powered? phone disconnected?")
    print("state:", s.state)
    await s.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
