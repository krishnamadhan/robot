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
import time
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

CHAR_FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
NAME_PREFIX = "LEDDMX"
# Known address — once connected, the strip STOPS advertising, so scan-by-name
# can't rediscover it. Connecting by address works regardless of advertising.
KNOWN_ADDR = "41:42:CD:95:A7:15"


def _color_frame(r: int, g: int, b: int) -> bytes:
    return bytes([0x7B, 0xFF, 0x07, r & 0xFF, g & 0xFF, b & 0xFF, 0x00, 0xFF, 0xBF])


def _bright_frame(pct: int) -> bytes:
    pct = max(0, min(100, int(pct)))
    return bytes([0x7B, 0xFF, 0x01, (pct * 32) // 100, pct, 0x00, 0xFF, 0xFF, 0xBF])


# ── Extended opcodes (github user154lt/LEDDMX-00, verified against Dmx00Data.kt
#    2026-07-06). NOTE: opcode 0x04 (power) stays BANNED — latches this unit. ──

def _pattern_frame(index: int) -> bytes:
    """Built-in animated pattern, 0-210 (0 = pattern off). See PATTERNS."""
    index = max(0, min(210, int(index)))
    return bytes([0x7B, 0xFF, 0x03, index, 0xFF, 0xFF, 0xFF, 0xFF, 0xBF])


def _mic_frame(eq: int) -> bytes:
    """Controller's built-in mic sound-sync. eq 0 = off, 1..255 = EQ modes.
    (8-byte frame — matches the reference implementation exactly.)"""
    eq = max(0, min(255, int(eq)))
    return bytes([0x7B, 0xFF, 0x0B, eq, 0x00, 0xFF, 0xFF, 0xBF])


def _temp_frame(pct: int) -> bytes:
    """Colour temperature as percentage (0 = warmest, 100 = coolest)."""
    pct = max(0, min(100, int(pct)))
    return bytes([0x7B, 0xFF, 0x09, (pct * 32) // 100, pct, 0xFF, 0xFF, 0xFF, 0xBF])


def _custom_color_frame(pos: int, r: int, g: int, b: int, size: int) -> bytes:
    """One colour of a custom pattern list. pos starts at 1."""
    return bytes([0x7B, pos & 0xFF, 0x0E, 0xFD, r & 0xFF, g & 0xFF, b & 0xFF,
                  size & 0xFF, 0xBF])


def _custom_mode_frame(mode: int) -> bytes:
    """Custom pattern animation mode 0-7 (see CUSTOM_MODES; 4 = off)."""
    return bytes([0x7B, 0xFF, 0x13, mode & 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xBF])


def _custom_direction_frame(forward: bool) -> bytes:
    return bytes([0x7B, 0xFF, 0x0D, 0x00 if forward else 0x01,
                  0xFF, 0xFF, 0xFF, 0xFF, 0xBF])


# Curated shortcuts into the 211-entry pattern table (full list in
# docs of the LEDDMX-00 repo; index = position in that table).
PATTERNS = {
    "off":       0,
    "dreaming":  1,    # slow colour dream
    "rainbow":   3,    # forward 7 colours
    "rgb":       5,    # forward red/green/blue
    "trail":     23,   # forward trailing 7 colours
    "stream":    39,   # forward streaming 7 colours
    "curtain":   57,   # open curtain 7 colours
    "spot":      63,   # forward follow-spot 7 colours
    "flutter":   69,   # forward flutter 7 colours
    "hop":       75,   # hop 7 colours
    "strobe":    78,   # strobe 7 colours
    "gradual":   81,   # gradual 7 colours
    "race":      101,  # horse race red
    "run":       122,  # forward run 7 colours
    "swab":      199,  # forward swab 7 colours
}

# Custom-pattern animation modes (enum order from CustomPatternMode.kt).
CUSTOM_MODES = {"gradual": 0, "fade": 1, "flow": 2, "flash": 3,
                "off": 4, "pulse": 5, "flutter": 6, "hop": 7}

# Music EQ modes for the built-in mic. The LED LAMP app exposes a handful;
# indices are passed straight through (0 = off). Names are convenience labels.
MUSIC_MODES = {"off": 0, "classic": 1, "soft": 2, "dynamic": 3, "disco": 4}


# Named colours for the WhatsApp/voice command surface
COLORS = {
    "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
    "white": (255, 255, 255), "warm": (255, 160, 60), "yellow": (255, 255, 0),
    "orange": (255, 90, 0), "purple": (150, 0, 255), "pink": (255, 40, 120),
    "cyan": (0, 255, 255), "amber": (255, 120, 0), "off": None,
}

# Hands-free scene presets: colour + brightness (or an animation).
SCENES = {
    "movie":   {"rgb": (255, 130, 40),  "bright": 22, "desc": "dim warm bias light"},
    "chill":   {"rgb": (255, 160, 70),  "bright": 55, "desc": "warm relaxed"},
    "night":   {"rgb": (255, 70, 0),    "bright": 8,  "desc": "deep amber nightlight"},
    "focus":   {"rgb": (235, 242, 255), "bright": 100, "desc": "cool bright"},
    "reading": {"rgb": (255, 200, 120), "bright": 85, "desc": "warm white"},
    "romance": {"rgb": (255, 20, 90),   "bright": 35, "desc": "soft rose"},
    "party":   {"animate": "cycle",     "desc": "rotating colour cycle"},
}


class LedStrip:
    """Stateful LEDDMX strip. Reconnects on demand; remembers colour + brightness."""

    def __init__(self, name_prefix: str = NAME_PREFIX) -> None:
        self._prefix = name_prefix.upper()
        self._client = None
        self._addr: Optional[str] = KNOWN_ADDR
        self._last_rgb = (255, 255, 255)
        self._brightness = 100
        self._is_on = True
        self._lock = asyncio.Lock()
        # Health telemetry
        self._writes_ok = 0
        self._writes_fail = 0
        self._consec_fail = 0
        self._last_ok_ts = 0.0
        self._last_error = ""
        self._scene: Optional[str] = None
        self._mode: Optional[str] = None   # controller-side mode (pattern/music/temp)
        self._anim_task: Optional[asyncio.Task] = None

    async def _ensure(self) -> bool:
        """Connect if not already; return True if usable."""
        if self._client is not None and self._client.is_connected:
            return True
        from bleak import BleakClient, BleakScanner

        # 1. Connect by known address directly — works even when the strip is not
        #    advertising (a connected BLE peripheral goes silent, so scanning fails).
        if self._addr:
            try:
                self._client = BleakClient(self._addr, timeout=15.0)
                await self._client.connect()
                log.info("led.connected", addr=self._addr, via="address")
                return True
            except Exception as e:
                log.info("led.addr_connect_failed", error=str(e)[:60])
                self._client = None

        # 2. Fall back to scan-by-name (first-ever connect / address changed).
        dev = await BleakScanner.find_device_by_filter(
            lambda d, adv: (d.name or "").upper().startswith(self._prefix), timeout=12.0
        )
        if not dev:
            log.warning("led.not_found", prefix=self._prefix, addr=self._addr)
            return False
        try:
            self._client = BleakClient(dev.address, timeout=20.0)
            await self._client.connect()
            self._addr = dev.address
            log.info("led.connected", name=dev.name, addr=dev.address, via="scan")
            return True
        except Exception as e:
            log.warning("led.connect_failed", error=str(e)[:80])
            self._client = None
            return False

    async def _write(self, frame: bytes) -> bool:
        async with self._lock:
            if not await self._ensure():
                self._writes_fail += 1
                self._consec_fail += 1
                self._last_error = "not connected"
                return False
            try:
                await self._client.write_gatt_char(CHAR_FFE1, frame, response=False)
                self._writes_ok += 1
                self._consec_fail = 0
                self._last_ok_ts = time.time()
                return True
            except Exception as e:
                log.warning("led.write_failed", error=str(e)[:80])
                self._client = None  # force reconnect next call
                self._writes_fail += 1
                self._consec_fail += 1
                self._last_error = str(e)[:80]
                return False

    async def set_color(self, r: int, g: int, b: int) -> bool:
        self._last_rgb = (r, g, b)
        self._is_on = True
        self._mode = None   # a colour frame exits any controller-side mode
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
        """Soft power — native 0x04 opcode is broken/latches this unit.
        Off = zero the RGB channels *and* brightness. Brightness-0 alone
        leaves the last colour latched, which some units show as a faint
        glow; a black colour frame kills that residual. (The controller's
        own status LED is physical and can't be reached over BLE.)"""
        self._is_on = on
        if not on:
            self._mode = None
            await self._write(_color_frame(0, 0, 0))
            return await self._write(_bright_frame(0))
        return await self._write(_bright_frame(self._brightness))

    async def _stop_anim(self) -> None:
        if self._anim_task:
            self._anim_task.cancel()
            self._anim_task = None
        self._scene = None

    async def stop_animation(self) -> None:
        """Public: stop any running scene animation (e.g. before TV sync takes over)."""
        await self._stop_anim()

    # ── Controller-side modes (pattern / music / colour temperature) ────────
    # These run ON the strip's controller — no BLE traffic while active.
    # Any set_color/set_scene naturally overrides them.

    async def set_pattern(self, pattern) -> bool:
        """Built-in animated pattern by name (PATTERNS) or raw index 0-210."""
        await self._stop_anim()
        if isinstance(pattern, str):
            if pattern.lower() not in PATTERNS:
                return False
            index = PATTERNS[pattern.lower()]
        else:
            index = int(pattern)
        ok = await self._write(_pattern_frame(index))
        if ok:
            self._mode = None if index == 0 else f"pattern:{pattern}"
            self._is_on = index != 0 or self._is_on
        return ok

    async def set_music(self, eq) -> bool:
        """Sound sync via the controller's BUILT-IN mic. eq: name (MUSIC_MODES)
        or raw index 0-255; 0/'off' stops it."""
        await self._stop_anim()
        if isinstance(eq, str):
            if eq.lower() not in MUSIC_MODES:
                return False
            index = MUSIC_MODES[eq.lower()]
        else:
            index = int(eq)
        ok = await self._write(_mic_frame(index))
        if ok:
            self._mode = None if index == 0 else f"music:{eq}"
        return ok

    async def set_color_temp(self, pct: int) -> bool:
        """White colour temperature, 0 (warmest) - 100 (coolest)."""
        ok = await self._write(_temp_frame(pct))
        if ok:
            self._mode = f"temp:{max(0, min(100, int(pct)))}"
            self._is_on = True
        return ok

    async def set_custom_pattern(self, colors, mode="flow", forward=True) -> bool:
        """Custom animated pattern: 2+ RGB tuples + animation mode + direction."""
        if not colors or len(colors) < 2:
            return False
        mode_idx = CUSTOM_MODES.get(str(mode).lower())
        if mode_idx is None:
            return False
        await self._stop_anim()
        ok = True
        size = min(len(colors), 16)
        for i, (r, g, b) in enumerate(colors[:size], start=1):
            ok = await self._write(_custom_color_frame(i, r, g, b, size)) and ok
        ok = await self._write(_custom_mode_frame(mode_idx)) and ok
        ok = await self._write(_custom_direction_frame(forward)) and ok
        if ok:
            self._mode = f"custom:{mode}"
            self._is_on = True
        return ok

    async def set_scene(self, name: str) -> bool:
        """Apply a hands-free scene preset (colour+brightness, or an animation)."""
        preset = SCENES.get(name.lower())
        if preset is None:
            return False
        await self._stop_anim()
        if preset.get("animate") == "cycle":
            self._scene = name.lower()
            self._anim_task = asyncio.create_task(self._party_loop(), name="led_party")
            return True
        r, g, b = preset["rgb"]
        ok = await self.set_color(r, g, b)
        ok = await self.set_brightness(preset["bright"]) and ok
        self._scene = name.lower()
        return ok

    async def _party_loop(self) -> None:
        """Slowly rotate hue through the spectrum — a calm party glow."""
        import colorsys
        try:
            await self.set_brightness(100)
            h = 0.0
            while True:
                r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(h, 1.0, 1.0))
                await self.set_color(r, g, b)
                h = (h + 0.04) % 1.0
                await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            pass

    async def disconnect(self) -> None:
        await self._stop_anim()
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    @property
    def state(self) -> dict:
        return {"on": self._is_on, "rgb": self._last_rgb,
                "brightness": self._brightness, "addr": self._addr,
                "scene": self._scene, "mode": self._mode}

    @property
    def health(self) -> dict:
        connected = self._client is not None and self._client.is_connected
        return {
            "connected": connected,
            "writes_ok": self._writes_ok,
            "writes_fail": self._writes_fail,
            "consec_fail": self._consec_fail,
            "last_ok_age_s": round(time.time() - self._last_ok_ts, 1) if self._last_ok_ts else None,
            "last_error": self._last_error or None,
            "healthy": connected and self._consec_fail < 3,
        }


# Module-level singleton for the robot process / API to share
strip = LedStrip()
