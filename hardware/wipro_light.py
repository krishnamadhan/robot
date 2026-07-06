"""
Wipro Next Smart Home RGB bulb — Tuya local protocol (LAN, no cloud).

Device:  192.168.1.3  DeviceID: 01731060d8f15be1dd7a  Protocol: v3.3
Key:     WIPRO_LOCAL_KEY env var (robot/.env)

Sync architecture (Govee-style):
  - MUSIC MODE (DP28) for colour streaming: hardware fade-blends between
    updates, accepts rapid changes, and never writes to flash (DP24 colour
    writes persist to flash — wears it out at 6 Hz).
  - Single worker task + latest-value mailbox: Tuya devices accept ONE TCP
    connection; concurrent calls fail or land out of order (the "strip green,
    bulb blue" bug). The worker serializes sends and coalesces — if frames
    arrive faster than the bulb can take, intermediates are dropped, so the
    bulb always chases the *latest* colour, never a backlog.
  - Persistent socket + reconnect-and-re-enter-music-mode on failure.

All tinytuya I/O runs on a dedicated single-thread executor so it can never
block the event loop or interleave.
"""

import asyncio
import colorsys
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple

from utils.logger import get_logger

log = get_logger(__name__)

WIPRO_IP        = "192.168.1.3"
WIPRO_DEVICE_ID = "01731060d8f15be1dd7a"
WIPRO_VERSION   = 3.3

SEND_MIN_INTERVAL = 0.30   # seconds between bulb sends (~3.3 Hz max)
MUSIC_TRANSITION  = 1      # 0 = jump, 1 = hardware fade between colours
BRIGHT_FLOOR      = 0.10   # never send an all-black colour frame
FAIL_RECONNECT    = 3      # consecutive failures before socket reset

# Mailbox sentinel for "power off"
_OFF = ("off",)


def _make_bulb():
    key = os.environ.get("WIPRO_LOCAL_KEY", "").strip()
    if not key:
        return None
    try:
        import tinytuya
        b = tinytuya.BulbDevice(WIPRO_DEVICE_ID, WIPRO_IP, key)
        b.set_version(WIPRO_VERSION)
        b.set_socketPersistent(True)
        b.set_socketTimeout(2)
        b.set_socketRetryLimit(1)
        return b
    except ImportError:
        log.error("wipro.tinytuya_missing", hint="pip install tinytuya")
        return None


class WiproLight:
    """Async Wipro bulb driver with a serialized, coalescing sync worker."""

    def __init__(self) -> None:
        self._bulb = None
        self._enabled = False
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wipro")
        # Mailbox: holds only the LATEST desired state.
        self._target: Optional[Tuple] = None
        self._dirty: Optional[asyncio.Event] = None
        self._worker: Optional[asyncio.Task] = None
        self._in_music = False
        self._is_on = False
        self._consec_fail = 0
        self._sends_ok = 0
        self._sends_fail = 0

    # ── lifecycle ────────────────────────────────────────────────────────────

    def init(self) -> bool:
        """Initialise — call once at startup. Returns True if key configured."""
        self._bulb = _make_bulb()
        self._enabled = self._bulb is not None
        if self._enabled:
            log.info("wipro.ready", ip=WIPRO_IP, id=WIPRO_DEVICE_ID)
        else:
            log.warning("wipro.disabled", reason="WIPRO_LOCAL_KEY not set")
        return self._enabled

    def _ensure_worker(self) -> None:
        if self._dirty is None:
            self._dirty = asyncio.Event()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker(), name="wipro-sync")

    # ── public API (all non-blocking: they just update the mailbox) ─────────

    def set_color(self, r: int, g: int, b: int, bright_pct: int) -> None:
        """Queue a colour update (RGB 0-255, brightness 0-100). Coalescing."""
        if not self._enabled:
            return
        self._target = (int(r), int(g), int(b), int(bright_pct))
        self._ensure_worker()
        self._dirty.set()

    def power_off(self) -> None:
        """Queue a power-off. Coalescing — cancels any pending colour."""
        if not self._enabled:
            return
        self._target = _OFF
        self._ensure_worker()
        self._dirty.set()

    async def power(self, on: bool) -> None:
        """Compat shim for old call sites."""
        if on:
            self.set_color(255, 240, 220, 80)
        else:
            self.power_off()

    async def stop(self) -> None:
        """Stop the worker (leaves the bulb in its last state)."""
        if self._worker:
            self._worker.cancel()
            self._worker = None
        self._in_music = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def stats(self) -> dict:
        return {"ok": self._sends_ok, "fail": self._sends_fail,
                "music": self._in_music, "on": self._is_on}

    # ── worker: single consumer, serialized sends ────────────────────────────

    async def _run_worker(self) -> None:
        loop = asyncio.get_event_loop()
        last_send = 0.0
        try:
            while True:
                await self._dirty.wait()
                self._dirty.clear()
                # Pace: bulb can't keep up beyond ~3 Hz; coalesce in between.
                wait = SEND_MIN_INTERVAL - (time.monotonic() - last_send)
                if wait > 0:
                    await asyncio.sleep(wait)
                    # Grab whatever is newest AFTER the pacing sleep.
                    self._dirty.clear()
                target = self._target
                if target is None:
                    continue
                last_send = time.monotonic()
                ok = await loop.run_in_executor(self._exec, self._send, target)
                if ok:
                    self._sends_ok += 1
                    self._consec_fail = 0
                else:
                    self._sends_fail += 1
                    self._consec_fail += 1
                    if self._consec_fail >= FAIL_RECONNECT:
                        await loop.run_in_executor(self._exec, self._reset_socket)
                        self._consec_fail = 0
                    # Retry latest state on next wake; mark dirty if unchanged.
                    if self._target is target:
                        self._dirty.set()
                        await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    # ── blocking Tuya calls (single executor thread only) ────────────────────

    def _send(self, target: Tuple) -> bool:
        try:
            if target is _OFF:
                self._bulb.turn_off(nowait=True)
                self._is_on = False
                self._in_music = False   # mode resets on next on
                return True

            r, g, b, bright = target
            if not self._is_on:
                self._bulb.turn_on()
                self._is_on = True
            if not self._in_music:
                self._bulb.set_mode("music")
                self._in_music = True
                log.info("wipro.music_mode")

            # Brightness → scale V in HSV (keeps hue/sat; floors at 10%).
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            v = max(BRIGHT_FLOOR, v * bright / 100.0)
            rr, gg, bb = [int(x * 255) for x in colorsys.hsv_to_rgb(h, s, v)]
            res = self._bulb.set_music_colour(
                MUSIC_TRANSITION, rr, gg, bb, nowait=True
            )
            if isinstance(res, dict) and res.get("Error"):
                log.warning("wipro.send_error", error=str(res)[:100])
                return False
            return True
        except Exception as e:
            log.warning("wipro.send_failed", error=str(e)[:100])
            return False

    def _reset_socket(self) -> None:
        try:
            self._bulb.close()
        except Exception:
            pass
        self._in_music = False
        log.info("wipro.socket_reset")


wipro = WiproLight()
