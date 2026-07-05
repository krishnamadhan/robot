"""
Wipro Next Smart Home RGB bulb — Tuya local protocol (LAN, no cloud).

Device:  192.168.1.3  DeviceID: 01731060d8f15be1dd7a  Protocol: v3.3
Key:     WIPRO_LOCAL_KEY env var (get via `python3 -m tinytuya wizard`
         or iot.tuya.com → Cloud Project → Device → Local Key)

All tinytuya calls are blocking TCP; run them in a thread pool so they
never block the asyncio event loop or the ambilight 6-Hz loop.
"""

import asyncio
import colorsys
import os
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

WIPRO_IP        = "192.168.1.3"
WIPRO_DEVICE_ID = "01731060d8f15be1dd7a"
WIPRO_VERSION   = 3.3


def _make_bulb():
    key = os.environ.get("WIPRO_LOCAL_KEY", "").strip()
    if not key:
        return None
    try:
        import tinytuya
        b = tinytuya.BulbDevice(WIPRO_DEVICE_ID, WIPRO_IP, key)
        b.set_version(WIPRO_VERSION)
        b.set_socketTimeout(1.5)    # fast fail — don't hold the thread
        b.set_socketRetryDelay(0)
        b.set_socketRetryLimit(1)
        return b
    except ImportError:
        log.error("wipro.tinytuya_missing", hint="pip install tinytuya")
        return None


class WiproLight:
    """Async RGB bulb driver. Runs Tuya I/O in a thread pool."""

    def __init__(self) -> None:
        self._bulb = None
        self._enabled = False
        self._is_on: bool = False
        self._last_rgb: Optional[tuple] = None
        self._last_bright: int = -1

    def init(self) -> bool:
        """Initialise — call once at startup. Returns True if key is configured."""
        self._bulb = _make_bulb()
        self._enabled = self._bulb is not None
        if self._enabled:
            log.info("wipro.ready", ip=WIPRO_IP, id=WIPRO_DEVICE_ID)
        else:
            log.warning("wipro.disabled", reason="WIPRO_LOCAL_KEY not set")
        return self._enabled

    async def _run(self, fn) -> None:
        if not self._enabled:
            return
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, fn)
        except Exception as e:
            log.warning("wipro.call_failed", error=str(e)[:100])

    async def power(self, on: bool) -> None:
        if not self._enabled:
            return
        self._is_on = on
        bulb = self._bulb
        if on:
            await self._run(lambda: bulb.turn_on())
        else:
            await self._run(lambda: bulb.turn_off())
        log.debug("wipro.power", on=on)

    async def set_color(self, r: int, g: int, b: int, bright_pct: int) -> None:
        """Set colour (RGB 0-255) and brightness (0-100). Turns on if needed."""
        if not self._enabled:
            return
        bulb = self._bulb

        # Scale brightness via HSV (preserves hue/saturation — just dims the value).
        # Floor at 10% so the bulb never sends an all-black frame mid-sync.
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        v_scaled = max(0.10, v * bright_pct / 100.0)
        rr, gg, bb = [int(x * 255) for x in colorsys.hsv_to_rgb(h, s, v_scaled)]

        def _send():
            if not self._is_on:
                bulb.turn_on()
                self._is_on = True
            bulb.set_colour(rr, gg, bb)

        await self._run(_send)
        self._last_rgb = (r, g, b)
        self._last_bright = bright_pct

    @property
    def enabled(self) -> bool:
        return self._enabled


wipro = WiproLight()
