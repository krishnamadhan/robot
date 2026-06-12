"""
Shared Pi I2C bus mutex (KI-019).

One physical bus (i2c-1) is touched by two in-process writers:
  - sensor_manager: BH1750 + UPS HAT reads (sync calls on the event loop thread)
  - expression/eyes.py: SSD1306 OLED frame renders (run_in_executor thread)

A threading.Lock — not asyncio.Lock — because the OLED render runs on an
executor thread while sensor reads run synchronously on the loop thread;
an asyncio.Lock cannot serialize across threads or sync callers.

Hold time is small (single smbus transaction or one OLED frame ≈30ms),
so briefly blocking the loop thread on contention is acceptable.

battery_monitor.py is a separate process — it reads via the cosmo HTTP API
and is not covered by this lock.
"""

import threading

i2c_lock = threading.Lock()
