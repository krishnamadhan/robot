"""
LEDDMX BLE RGB strip driver (One94Store 5M smart strip / "LED LAMP" app).

Protocol (reverse-engineered 2026-07-02 via PacketLogger on the LED LAMP iOS app,
cross-checked against github user154lt/LEDDMX-00). Frames are written
Write-Without-Response to characteristic 0xFFE1:

    [0x7B][0xFF][cmd][data...][0xBF]
      cmd 0x07 = colour     → 7B FF 07 RR GG BB 00 FF BF
      cmd 0x01 = brightness → 7B FF 01 AA PP 00 FF FF BF   (PP 0-100, AA = PP*32//100)
      cmd 0x04 = native power → BROKEN on this unit: no response and can latch the
                 controller into a blackout that only a physical power-cycle clears.
                 We NEVER send 0x04. "Power" is soft: brightness→0 / restore.

Single-connection device: only one BLE central at a time. Disconnect the phone's
LED LAMP app before the Pi connects (and vice versa).

Test tool: tools/led_test.py.
"""

import asyncio
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

CHAR_FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
NAME_PREFIX = "LEDDMX"


def _color_frame(r: int, g: int, b: int) -> bytes:
    return bytes([0x7B, 0xFF, 0x07, r & 0xFF, g & 0xFF, b & 0xFF, 0x00, 0xFF, 0xBF])


def _bright_frame(pct: int) -> bytes:
    pct = max(0, min(100, int(pct)))
    return bytes([0x7B, 0xFF, 0x01, (pct * 32) // 100, pct, 0x00, 0xFF, 0xFF, 0xBF])


# Named colours for the WhatsApp/voice command surface
COLORS = {
    "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
    "white": (255, 255, 255), "warm": (255, 160, 60), "yellow": (255, 255, 0),
    "orange": (255, 90, 0), "purple": (150, 0, 255), "pink": (255, 40, 120),
    "cyan": (0, 255, 255), "amber": (255, 120, 0), "off": None,
}


class LedStrip:
    """Stateful LEDDMX strip. Reconnects on demand; remembers colour + brightness."""

    def __init__(self, name_prefix: str = NAME_PREFIX) -> None:
        self._prefix = name_prefix.upper()
        self._client = None
        self._addr: Optional[str] = None
        self._last_rgb = (255, 255, 255)
        self._brightness = 100
        self._is_on = True
        self._lock = asyncio.Lock()

    async def _ensure(self) -> bool:
        """Connect if not already; return True if usable."""
        if self._client is not None and self._client.is_connected:
            return True
        from bleak import BleakClient, BleakScanner
        dev = await BleakScanner.find_device_by_filter(
            lambda d, adv: (d.name or "").upper().startswith(self._prefix), timeout=12.0
        )
        if not dev:
            log.warning("led.not_found", prefix=self._prefix)
            return False
        try:
            self._client = BleakClient(dev.address, timeout=20.0)
            await self._client.connect()
            self._addr = dev.address
            log.info("led.connected", name=dev.name, addr=dev.address)
            return True
        except Exception as e:
            log.warning("led.connect_failed", error=str(e)[:80])
            self._client = None
            return False

    async def _write(self, frame: bytes) -> bool:
        async with self._lock:
            if not await self._ensure():
                return False
            try:
                await self._client.write_gatt_char(CHAR_FFE1, frame, response=False)
                return True
            except Exception as e:
                log.warning("led.write_failed", error=str(e)[:80])
                self._client = None  # force reconnect next call
                return False

    async def set_color(self, r: int, g: int, b: int) -> bool:
        self._last_rgb = (r, g, b)
        self._is_on = True
        return await self._write(_color_frame(r, g, b))

    async def set_named(self, name: str) -> bool:
        rgb = COLORS.get(name.lower())
        if rgb is None:
            return await self.power(False)
        return await self.set_color(*rgb)

    async def set_brightness(self, pct: int) -> bool:
        self._brightness = max(0, min(100, int(pct)))
        self._is_on = self._brightness > 0
        return await self._write(_bright_frame(self._brightness))

    async def power(self, on: bool) -> bool:
        """Soft power — native 0x04 opcode is broken/latches this unit."""
        self._is_on = on
        return await self._write(_bright_frame(self._brightness if on else 0))

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    @property
    def state(self) -> dict:
        return {"on": self._is_on, "rgb": self._last_rgb,
                "brightness": self._brightness, "addr": self._addr}


# Module-level singleton for the robot process / API to share
strip = LedStrip()
