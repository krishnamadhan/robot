"""
TV ambilight: sample the camera and drive the LEDDMX strip to match the TV.

The runtime loop keeps the v2 behavior:
  1. Pick a dominant hue from saturated, bright content pixels.
  2. Re-saturate output so the strip stays vivid instead of wall-washed.
  3. Gate content with hysteresis so black/off screens dim the strip off.
  4. Smooth color/brightness and rate-limit brightness rises to tame flashes.

If config/ambilight_roi.json exists, analysis samples only the calibrated TV
screen quadrilateral, perspective-corrected and lightly edge-trimmed. Without
that file, it falls back to whole-frame sampling.
"""

import asyncio
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

from utils.logger import get_logger

log = get_logger(__name__)

SAMPLE_HZ = 6.0
ANALYZE_SIZE = (80, 60)   # small HSV working image (w, h)
COLOR_ALPHA = 0.35        # EMA weight for color (higher = snappier)
BRIGHT_ALPHA = 0.28       # EMA weight for brightness (lower = calmer)
BRIGHT_MAX_RISE = 9       # max brightness increase per tick; tames flashes/strobes
MIN_ON_BRIGHT = 15        # dimmest the strip goes while the screen is lit
SAT_FLOOR = 0.90          # output never duller than this (cameras mute TV colour → push hard)
SAT_BOOST = 2.4           # multiply measured saturation before clamping

# Camera colour correction (derived from a paused-orange reference 2026-07-02):
# 1. Auto-WB cools warm scenes (orange gained a blue cast, B 153→1 when locked).
#    Lock WB warm so the camera output is predictable.
# 2. The sensor over-reads GREEN on warm content (orange showed R≈G, hue 52°
#    instead of ~33°). Attenuate green in software → warm colours land right.
#    Pure green/blue hues are unaffected (R or B ≈ 0 there).
WB_LOCK = True
WB_LOCK_R = 2.4           # ISP red ColourGain when ambilight owns the camera
WB_LOCK_B = 0.8           # ISP blue ColourGain (low = kill the cool cast)

# Colour-correction matrix (CCM), auto-calibrated 2026-07-02 by casting red/green/
# blue/white/cyan/magenta/yellow test cards to the TV and measuring the camera's
# rendering of each. Maps camera RGB → true RGB as `true = rgb @ CCM`. ONLY valid
# with WB locked at (WB_LOCK_R, WB_LOCK_B) — the calibration was done there.
# Re-run tools/ambilight_calibrate_cast.py if the camera/TV/setup changes.
CCM_ENABLED = True
CCM = np.array([
    [1.062, -0.069, 0.059],
    [0.018, 1.189, 0.212],
    [-0.017, -0.184, 1.239],
], dtype=np.float32)
VIVID_S_MIN = 0.35        # saturated enough to count as TV content
VIVID_V_MIN = 0.35        # bright enough to count as TV content

# Spatial bias — weight sampling toward the BOTTOM-RIGHT of the ROI. The camera
# sits low-left, so the calibrated box catches the white wall pillar in its
# top-left; the bottom-right is clean TV screen. Floor is the weight at the
# top-left corner (0 = ignore it entirely, 1 = uniform).
SPATIAL_BR_FLOOR = 0.20


def _spatial_weight(h: int, w: int) -> np.ndarray:
    ys = np.linspace(0.0, 1.0, h)[:, None]
    xs = np.linspace(0.0, 1.0, w)[None, :]
    fx = SPATIAL_BR_FLOOR + (1.0 - SPATIAL_BR_FLOOR) * xs
    fy = SPATIAL_BR_FLOOR + (1.0 - SPATIAL_BR_FLOOR) * ys
    return (fx * fy).astype(np.float32)


_SPATIAL = _spatial_weight(ANALYZE_SIZE[1], ANALYZE_SIZE[0])

# Optional TV-screen ROI. Save with tools/ambilight_calibrate.py.
USE_ROI = True
ROI_CONFIG = Path(__file__).resolve().parents[1] / "config" / "ambilight_roi.json"
LEGACY_ROI_CONFIG = Path.home() / ".robot" / "ambilight_roi.json"
ROI_WARP_SIZE = (160, 90)  # 16:9 screen sample before downsample
ROI_EDGE_TRIM = 0.03       # trim bezel/edge glare from calibrated screen sample

# Content gate. "vivid fraction" = share of pixels that are both saturated and
# bright. It works better than whole-frame brightness because room walls stay
# bright even when the TV is black/off.
CONTENT_ON = 0.16
CONTENT_OFF = 0.09
STATE_DEBOUNCE = 2

# Anti-flicker deadband: hold output unless the change is meaningful.
COLOR_DEADBAND = 22       # sum abs(delta RGB) must exceed this to repaint
BRIGHT_DEADBAND = 4       # brightness must move at least this many percent

# Idle timeout: if the screen sample barely changes for this long (paused frame,
# menu, screensaver, static logo) treat it as "not being watched" → dim to min.
# Any meaningful change wakes it back up immediately.
IDLE_TIMEOUT_S = 300.0    # 5 minutes
IDLE_CHANGE_RGB = 30      # sum abs(delta RGB) that counts as real activity
IDLE_CHANGE_BRIGHT = 8    # brightness delta that counts as real activity
IDLE_MIN_BRIGHT = 0       # brightness while idle (0 = off; raise for a faint glow)

QuadPoints = Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int], Tuple[int, int]]


@dataclass(frozen=True)
class Analysis:
    color: Optional[Tuple[int, int, int, int]]
    vivid_frac: float
    roi_active: bool
    roi_points: Optional[QuadPoints]
    vivid_pixels: int
    mean_sat: float
    content_value: float
    sample_shape: Tuple[int, int]


_roi_cache_mtime: Optional[float] = None
_roi_cache_points: Optional[Tuple[Tuple[float, float], ...]] = None
_roi_cache_warned = False


def _resaturate(r: int, g: int, b: int, floor: float = SAT_FLOOR) -> Tuple[int, int, int]:
    """Push a color's saturation up to at least `floor`, keeping hue + value."""
    px = np.uint8([[[b, g, r]]])
    h, s, v = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0].astype(np.float32)
    s = max(s, floor * 255)
    out = cv2.cvtColor(np.uint8([[[h, min(255, s), v]]]), cv2.COLOR_HSV2BGR)[0, 0]
    return int(out[2]), int(out[1]), int(out[0])


def _order_quad(points: Sequence[Sequence[float]]) -> np.ndarray:
    """Return points ordered top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(points, dtype=np.float32)
    if pts.shape != (4, 2):
        raise ValueError("ROI needs exactly four 2D points")
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).reshape(4)
    ordered[0] = pts[np.argmin(sums)]
    ordered[2] = pts[np.argmax(sums)]
    ordered[1] = pts[np.argmin(diffs)]
    ordered[3] = pts[np.argmax(diffs)]
    return ordered


def _load_roi_points(shape: Tuple[int, int, int]) -> Optional[np.ndarray]:
    """Load cached ROI config and scale normalized points to this frame."""
    global _roi_cache_mtime, _roi_cache_points, _roi_cache_warned
    if not USE_ROI:
        return None

    path = ROI_CONFIG if ROI_CONFIG.exists() else LEGACY_ROI_CONFIG
    if not path.exists():
        return None

    try:
        mtime = path.stat().st_mtime
        if mtime != _roi_cache_mtime:
            data = json.loads(path.read_text())
            points = data.get("points")
            if points is None and isinstance(data.get("roi"), list) and len(data["roi"]) == 4:
                x0, y0, x1, y1 = (float(v) for v in data["roi"])
                points = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            if not isinstance(points, list) or len(points) != 4:
                raise ValueError("points must contain four [x, y] pairs")
            _roi_cache_points = tuple((float(p[0]), float(p[1])) for p in points)
            _roi_cache_mtime = mtime
            _roi_cache_warned = False
    except Exception as e:
        if not _roi_cache_warned:
            log.warning("ambilight.roi_config_invalid", path=str(path), error=str(e)[:100])
            _roi_cache_warned = True
        return None

    if _roi_cache_points is None:
        return None
    h, w = shape[:2]
    pts = np.asarray(_roi_cache_points, dtype=np.float32)
    if float(np.nanmax(pts)) <= 1.5:
        pts[:, 0] *= w
        pts[:, 1] *= h
    pts[:, 0] = np.clip(pts[:, 0], 0, w - 1)
    pts[:, 1] = np.clip(pts[:, 1], 0, h - 1)
    return _order_quad(pts)


def _extract_roi(
    bgr: np.ndarray,
    roi_points: Optional[Sequence[Sequence[float]]] = None,
) -> Tuple[np.ndarray, bool, Optional[QuadPoints]]:
    points = _order_quad(roi_points) if roi_points is not None else _load_roi_points(bgr.shape)
    if points is None:
        return bgr, False, None

    dst_w, dst_h = ROI_WARP_SIZE
    dst = np.float32([[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]])
    matrix = cv2.getPerspectiveTransform(points.astype(np.float32), dst)
    warped = cv2.warpPerspective(bgr, matrix, (dst_w, dst_h))

    trim_x = int(dst_w * ROI_EDGE_TRIM)
    trim_y = int(dst_h * ROI_EDGE_TRIM)
    if trim_x > 0 and trim_y > 0 and dst_w > trim_x * 2 and dst_h > trim_y * 2:
        warped = warped[trim_y:dst_h - trim_y, trim_x:dst_w - trim_x]

    int_points = tuple((int(round(x)), int(round(y))) for x, y in points)
    return warped, True, int_points  # type: ignore[return-value]


def analyze_debug(
    bgr: np.ndarray,
    roi_points: Optional[Sequence[Sequence[float]]] = None,
) -> Analysis:
    """Return a rich analysis record for tools/tests and the runtime loop."""
    sample_bgr, roi_active, used_points = _extract_roi(bgr, roi_points)
    small = cv2.resize(sample_bgr, ANALYZE_SIZE)
    if CCM_ENABLED:
        # Apply the calibrated colour-correction matrix (works in RGB).
        rgb = small[:, :, ::-1].astype(np.float32).reshape(-1, 3)
        corr = np.clip(rgb @ CCM, 0, 255).reshape(small.shape)
        small = corr[:, :, ::-1].astype(np.uint8)   # back to BGR
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h = hsv[:, :, 0].astype(np.float32)          # 0..179
    s = hsv[:, :, 1].astype(np.float32) / 255.0  # 0..1
    v = hsv[:, :, 2].astype(np.float32) / 255.0  # 0..1

    vivid = (s > VIVID_S_MIN) & (v > VIVID_V_MIN)
    # Bottom-right-biased content fraction: the wall pillar (top-left) is weighted
    # down so it neither inflates nor deflates the gate — the clean TV screen drives it.
    sw = _SPATIAL
    vivid_frac = float((vivid * sw).sum() / sw.sum())
    n = int(vivid.sum())
    if n < 8:
        return Analysis(
            color=None,
            vivid_frac=vivid_frac,
            roi_active=roi_active,
            roi_points=used_points,
            vivid_pixels=n,
            mean_sat=0.0,
            content_value=0.0,
            sample_shape=sample_bgr.shape[:2],
        )

    # Brightness + colour from vivid pixels, biased toward the bottom-right screen.
    content_v = float((v * vivid * sw).sum() / max((vivid * sw).sum(), 1e-6))
    bright = int(np.clip((content_v ** 0.7) * 100.0, MIN_ON_BRIGHT, 100))

    weights = ((s ** 1.2) * v) * vivid * sw
    wsum = float(weights.sum())
    ang = h * (math.pi / 90.0)
    x = float((weights * np.cos(ang)).sum())
    y = float((weights * np.sin(ang)).sum())
    dom = (math.atan2(y, x) % (2 * math.pi)) * (90.0 / math.pi)
    mean_sat = float((weights * s).sum() / wsum)
    out_s = float(np.clip(mean_sat * SAT_BOOST, SAT_FLOOR, 1.0))
    bgr_out = cv2.cvtColor(
        np.uint8([[[int(dom), int(out_s * 255), 255]]]), cv2.COLOR_HSV2BGR
    )[0, 0]

    return Analysis(
        color=(int(bgr_out[2]), int(bgr_out[1]), int(bgr_out[0]), bright),
        vivid_frac=vivid_frac,
        roi_active=roi_active,
        roi_points=used_points,
        vivid_pixels=n,
        mean_sat=mean_sat,
        content_value=content_v,
        sample_shape=sample_bgr.shape[:2],
    )


def analyze(bgr: np.ndarray) -> Tuple[Optional[Tuple[int, int, int, int]], float]:
    """Return ((r, g, b, brightness_pct) | None, vivid_frac)."""
    result = analyze_debug(bgr)
    return result.color, result.vivid_frac


class Ambilight:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._rgb = None
        self._bright = 0.0
        self._has_content = False
        self._pending = 0
        self._last_sent_rgb = None
        self._last_sent_bright = -1
        self._idle = False
        self._last_activity_ts = 0.0
        self._activity_rgb = None
        self._activity_bright = 0.0

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
        self._idle = False
        self._last_activity_ts = time.monotonic()
        self._activity_rgb = None
        self._activity_bright = 0.0
        self._apply_wb_lock()
        self._task = asyncio.create_task(self._loop(), name="ambilight")
        roi_path = ROI_CONFIG if ROI_CONFIG.exists() else LEGACY_ROI_CONFIG
        log.info("ambilight.start", roi=str(roi_path) if roi_path.exists() else None)
        return True

    def _apply_wb_lock(self) -> None:
        """Lock camera white balance warm while ambilight owns the camera —
        auto-WB otherwise cools warm TV scenes toward blue/magenta."""
        if not WB_LOCK:
            return
        try:
            from perception.vision.camera import color_config, camera
            color_config["hw_r"] = WB_LOCK_R
            color_config["hw_b"] = WB_LOCK_B
            camera._backend.apply_hw_gains()
            log.info("ambilight.wb_locked", r=WB_LOCK_R, b=WB_LOCK_B)
        except Exception as e:
            log.warning("ambilight.wb_lock_failed", error=str(e)[:80])

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        # Turn the strip OFF on stop — otherwise it holds the last colour at full
        # brightness, which reads as "bright even after stop".
        try:
            from hardware.led_strip import strip
            await strip.power(False)
        except Exception as e:
            log.warning("ambilight.stop_poweroff_failed", error=str(e)[:80])
        self._rgb = None
        self._bright = 0.0
        self._has_content = False
        self._last_sent_rgb = None
        self._last_sent_bright = -1
        log.info("ambilight.stop")

    async def _loop(self) -> None:
        from hardware.led_strip import strip
        from perception.vision.camera import camera

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

                want = (vivid_frac > CONTENT_ON) if not self._has_content else (vivid_frac > CONTENT_OFF)
                if want != self._has_content:
                    self._pending += 1
                    if self._pending >= STATE_DEBOUNCE:
                        self._has_content = want
                        self._pending = 0
                else:
                    self._pending = 0

                if not self._has_content or res is None:
                    target_bright = 0.0
                else:
                    r, g, b, bpct = res
                    # ── Idle detection: has the raw sample actually changed? ──
                    now = time.monotonic()
                    if self._activity_rgb is None:
                        self._activity_rgb = (r, g, b)
                        self._activity_bright = bpct
                        self._last_activity_ts = now
                    moved = (sum(abs(a - c) for a, c in zip((r, g, b), self._activity_rgb)) > IDLE_CHANGE_RGB
                             or abs(bpct - self._activity_bright) > IDLE_CHANGE_BRIGHT)
                    if moved:
                        self._activity_rgb = (r, g, b)
                        self._activity_bright = bpct
                        self._last_activity_ts = now
                        self._idle = False
                    elif now - self._last_activity_ts > IDLE_TIMEOUT_S:
                        self._idle = True

                    if self._idle:
                        target_bright = float(IDLE_MIN_BRIGHT)
                    else:
                        target_bright = float(bpct)
                        sample = np.array([r, g, b], dtype=np.float32)
                        self._rgb = sample if self._rgb is None else (
                            COLOR_ALPHA * sample + (1 - COLOR_ALPHA) * self._rgb
                        )

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
        if bright <= 1:
            if self._last_sent_bright != 0:
                await strip.power(False)
                self._last_sent_bright = 0
            return

        if self._rgb is not None:
            r, g, b = _resaturate(*(int(x) for x in self._rgb))
            if (
                self._last_sent_rgb is None
                or sum(abs(a - c) for a, c in zip((r, g, b), self._last_sent_rgb)) > COLOR_DEADBAND
            ):
                await strip.set_color(r, g, b)
                self._last_sent_rgb = (r, g, b)

        if abs(bright - self._last_sent_bright) >= BRIGHT_DEADBAND:
            await strip.set_brightness(bright)
            self._last_sent_bright = bright


ambilight = Ambilight()
