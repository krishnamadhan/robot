"""
TV ambilight — sample the camera, drive the LEDDMX strip to match the screen.

v2 algorithm (2026-07-02), addressing three failure modes seen live:
  1. Washed-out colour: a plain RGB average blends the vivid TV with the white
     wall → pale pink. Fixed by taking the DOMINANT HUE (saturation-weighted
     circular mean of only the vivid pixels) and re-saturating the output, so a
     red screen drives a vivid red strip.
  2. Black screen still glowing (TV reflections): strip brightness now tracks
     screen brightness, and a black/near-black screen dims the strip to off
     (hysteresis avoids flicker at the threshold).
  3. Flashes / strobes jolting the strip: brightness is smoothed and rate-limited
     on the way UP, so a single bright frame can't spike the strip.

Colour and brightness are separate LEDDMX registers, so we drive them
independently: colour = dominant hue, brightness = how lit the screen is.

Toggle via API: POST /led/tv {"on": true|false}. While ON the camera is a colour
sensor. Standalone tuning: tools/ambilight_test.py.
"""

import asyncio
import math
from typing import Optional, Tuple

import cv2
import numpy as np

from utils.logger import get_logger

log = get_logger(__name__)

SAMPLE_HZ = 6.0
COLOR_ALPHA = 0.35        # EMA weight for colour (higher = snappier)
BRIGHT_ALPHA = 0.28       # EMA weight for brightness (lower = calmer)
BRIGHT_MAX_RISE = 9       # max brightness increase per tick → tames flashes/strobes
MIN_ON_BRIGHT = 15        # dimmest the strip goes while the screen is lit
SAT_FLOOR = 0.60          # vibrancy floor — output never duller than this
SAT_BOOST = 1.8           # multiply measured saturation before clamping

# Content gate — the key discriminator. "vivid fraction" = share of pixels that are
# both saturated AND bright. Measured live: TV OFF ≈ 0.02-0.08 (wall + strip's own
# glow), TV showing content ≈ 0.5. Whole-frame brightness FAILS because the lit wall
# stays bright; saturation-fraction doesn't, because a wall isn't saturated.
CONTENT_ON = 0.16         # vivid_frac above this → real content on screen
CONTENT_OFF = 0.09        # below this → no content (TV off / reflections) → dim off
STATE_DEBOUNCE = 2        # a new on/off state must persist this many frames to apply

# Anti-flicker deadband — hold the current output unless the change is meaningful.
COLOR_DEADBAND = 22       # sum |ΔR|+|ΔG|+|ΔB| must exceed this to repaint
BRIGHT_DEADBAND = 4       # brightness must move at least this many % to resend


def _resaturate(r: int, g: int, b: int, floor: float = SAT_FLOOR) -> Tuple[int, int, int]:
    """Push a colour's saturation up to at least `floor`, keeping hue + value."""
    px = np.uint8([[[b, g, r]]])
    h, s, v = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0].astype(np.float32)
    s = max(s, floor * 255)
    out = cv2.cvtColor(np.uint8([[[h, min(255, s), v]]]), cv2.COLOR_HSV2BGR)[0, 0]
    return int(out[2]), int(out[1]), int(out[0])


def analyze(bgr: np.ndarray) -> Tuple[Optional[Tuple[int, int, int, int]], float]:
    """Return ((r,g,b,brightness_pct) | None, vivid_frac).

    The colour tuple is None when there's no real content (caller decides on/off
    with hysteresis). vivid_frac is the content-gate signal.
    """
    small = cv2.resize(bgr, (80, 60))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h = hsv[:, :, 0].astype(np.float32)          # 0..179
    s = hsv[:, :, 1].astype(np.float32) / 255.0  # 0..1
    v = hsv[:, :, 2].astype(np.float32) / 255.0  # 0..1

    # Content gate: share of pixels that are BOTH saturated and bright.
    vivid = (s > 0.35) & (v > 0.35)
    vivid_frac = float(vivid.mean())

    # Colour + brightness from the vivid (content) pixels only — never the wall.
    n = int(vivid.sum())
    if n < 8:
        return None, vivid_frac
    content_v = float(v[vivid].mean())
    bright = int(np.clip((content_v ** 0.7) * 100.0, MIN_ON_BRIGHT, 100))

    w = ((s ** 1.2) * v) * vivid
    wsum = float(w.sum())
    ang = h * (math.pi / 90.0)
    x = float((w * np.cos(ang)).sum())
    y = float((w * np.sin(ang)).sum())
    dom = (math.atan2(y, x) % (2 * math.pi)) * (90.0 / math.pi)  # → 0..179
    mean_sat = (w * s).sum() / wsum
    out_s = float(np.clip(mean_sat * SAT_BOOST, SAT_FLOOR, 1.0))
    bgr_out = cv2.cvtColor(
        np.uint8([[[int(dom), int(out_s * 255), 255]]]), cv2.COLOR_HSV2BGR
    )[0, 0]
    return (int(bgr_out[2]), int(bgr_out[1]), int(bgr_out[0]), bright), vivid_frac


class Ambilight:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._rgb = None            # smoothed colour (float array)
        self._bright = 0.0          # smoothed brightness
        self._has_content = False   # content-gate state (TV showing something)
        self._pending = 0           # debounce counter for state flips
        self._last_sent_rgb = None
        self._last_sent_bright = -1

    @property
    def active(self) -> bool:
        return self._running

    async def start(self) -> bool:
        if self._running:
            return True
        self._running = True
        self._rgb = None
        self._bright = 0.0
        self._has_content = False
        self._pending = 0
        self._task = asyncio.create_task(self._loop(), name="ambilight")
        log.info("ambilight.start")
        return True

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        log.info("ambilight.stop")

    async def _loop(self) -> None:
        from perception.vision.camera import camera
        from hardware.led_strip import strip
        period = 1.0 / SAMPLE_HZ
        misses = 0
        try:
            while self._running:
                await asyncio.sleep(period)
                frame_obj = camera.latest_frame
                if frame_obj is None or frame_obj.is_stale(2000):
                    misses += 1
                    if misses == 30:
                        log.warning("ambilight.no_frames")
                    continue
                misses = 0

                res, vivid_frac = analyze(frame_obj.image)

                # ── Content gate: hysteresis + debounce (no flicker at boundary) ──
                want = (vivid_frac > CONTENT_ON) if not self._has_content \
                    else (vivid_frac > CONTENT_OFF)
                if want != self._has_content:
                    self._pending += 1
                    if self._pending >= STATE_DEBOUNCE:
                        self._has_content = want
                        self._pending = 0
                else:
                    self._pending = 0

                # ── Targets ──
                if not self._has_content or res is None:
                    target_bright = 0.0
                else:
                    r, g, b, bpct = res
                    target_bright = float(bpct)
                    sample = np.array([r, g, b], dtype=np.float32)
                    self._rgb = sample if self._rgb is None else \
                        COLOR_ALPHA * sample + (1 - COLOR_ALPHA) * self._rgb

                # brightness EMA with rate-limited rise (flash/strobe damping)
                nb = BRIGHT_ALPHA * target_bright + (1 - BRIGHT_ALPHA) * self._bright
                if nb - self._bright > BRIGHT_MAX_RISE:
                    nb = self._bright + BRIGHT_MAX_RISE
                self._bright = nb

                await self._push(strip)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("ambilight.loop_error", error=str(e)[:100])
            self._running = False

    async def _push(self, strip) -> None:
        bright = int(round(self._bright))
        # Brightness: off when black/near-zero, else track scene.
        if bright <= 1:
            if self._last_sent_bright != 0:
                await strip.power(False)
                self._last_sent_bright = 0
            return
        # Colour: only when it moved enough (avoid spamming BLE).
        if self._rgb is not None:
            r, g, b = _resaturate(*(int(x) for x in self._rgb))
            if self._last_sent_rgb is None or \
                    sum(abs(a - c) for a, c in zip((r, g, b), self._last_sent_rgb)) > COLOR_DEADBAND:
                await strip.set_color(r, g, b)
                self._last_sent_rgb = (r, g, b)
        # Brightness: only on meaningful change.
        if abs(bright - self._last_sent_bright) >= BRIGHT_DEADBAND:
            await strip.set_brightness(bright)
            self._last_sent_bright = bright


ambilight = Ambilight()
