"""
Camera capture pipeline — supports Pi CSI camera (picamera2) and USB webcams (OpenCV).

Auto-detects the backend: tries picamera2 first (IMX708 CSI), falls back to
OpenCV V4L2 (USB webcam). Runs capture in a thread, feeds an async frame buffer.
Downstream consumers pull from latest_frame — they don't drive timing.
"""

import asyncio
import concurrent.futures
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional, Tuple

import cv2
import numpy as np

from hardware.registry import hw_registry
from utils.config import cfg
from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)

# Live-tunable colour config — updated via stream_server /color endpoint.
# Hardware gains set on the ISP; software multipliers applied per-frame.
color_config: dict = {
    # ColourGains are ordinary (red_gain, blue_gain) multipliers. The old
    # "gains behave inversely" note was an artefact of the R/B channel swap
    # fixed 2026-07-02 (see _Picamera2Backend.read). Boot default is auto-AWB;
    # hw_* only apply when pushed manually from the stream dashboard.
    # config/color.toml overrides these.
    "hw_r": 1.8,       # ColourGains red (ISP, manual override only)
    "hw_b": 1.6,       # ColourGains blue (ISP, manual override only)
    "sw_r": 1.0,       # software R multiplier
    "sw_g": 1.0,       # software G multiplier
    "sw_b": 1.0,       # software B multiplier
    "saturation": 1.0,  # 1.0 = neutral, >1 = more vivid, <1 = desaturate
    "shadow": 0.0,     # shadow blue subtraction (0 = off; was compensating the swap)
    "ev": 0.6,         # AE exposure-value compensation (stops); >0 = brighter
}


@dataclass
class Frame:
    image: np.ndarray
    timestamp: float = field(default_factory=time.monotonic)
    frame_id: int = 0

    def age_ms(self) -> float:
        return (time.monotonic() - self.timestamp) * 1000

    def thumbnail(self, max_dim: int = 160) -> np.ndarray:
        h, w = self.image.shape[:2]
        scale = max_dim / max(h, w)
        return cv2.resize(self.image, (int(w * scale), int(h * scale)))

    def is_stale(self, max_age_ms: float = 200.0) -> bool:
        return self.age_ms() > max_age_ms


class _Picamera2Backend:
    """Wraps picamera2 for the CSI Pi camera module."""

    def __init__(self, width: int, height: int, fps: int) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._cam = None

    def open(self) -> bool:
        try:
            from picamera2 import Picamera2
            import time as _time
            # Use imx708_wide.json — the correct CCM for this wide lens.
            # imx708.json (standard lens) has wrong CCM cross-talk that suppresses
            # red even when gains are boosted. Wide tuning + auto-AWB = accurate
            # colour since the R/B swap fix (see read()).
            try:
                tuning = Picamera2.load_tuning_file("imx708_wide.json")
                cam = Picamera2(tuning=tuning)
                log.info("camera.tuning", file="imx708_wide.json")
            except Exception:
                cam = Picamera2()
                log.warning("camera.tuning_fallback", reason="imx708_wide.json not found")
            config = cam.create_video_configuration(
                main={"size": (self._width, self._height), "format": "RGB888"},
                controls={
                    "FrameRate": float(self._fps),
                    "AwbEnable": True,
                    "AeEnable": True,
                },
            )
            cam.configure(config)
            cam.start()
            # v2 2026-07-02: AWB left in AUTO. The old manual gain lock
            # (hw_r=1.8, hw_b=2.0) was calibrated against R/B-swapped output
            # (see read() note) — those values are meaningless now that the
            # swap is fixed, and the wide tuning's auto-AWB + correct CCM
            # produce accurate colour on their own. Manual gains can still be
            # pushed live from the stream dashboard via apply_hw_gains().
            _time.sleep(3.0)
            try:
                cam.set_controls({"ExposureValue": color_config["ev"]})
                log.info("camera.awb_auto", ev=color_config["ev"])
            except Exception as e:
                log.warning("camera.ev_set_failed", error=str(e))
            self._cam = cam
            return True
        except Exception as e:
            log.warning("camera.picamera2_open_failed", error=str(e))
            return False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        cam = self._cam
        if not cam:
            return False, None
        try:
            # picamera2 "RGB888" is BGR in memory (libcamera quirk) — the array
            # is ALREADY OpenCV-native BGR. The old code assumed RGB and then
            # swapped again at the end, which shipped R/B-swapped frames
            # downstream — the source of the persistent "blue cast".
            bgr = cam.capture_array()
            cfg = color_config
            # Fast path: with neutral config (the post-AWB-fix default) skip the
            # float pipeline entirely — it costs real CPU at 30fps on the Pi.
            if (cfg["sw_r"] == 1.0 and cfg["sw_g"] == 1.0 and cfg["sw_b"] == 1.0
                    and cfg["saturation"] == 1.0 and cfg["shadow"] <= 0):
                return True, bgr
            f = bgr.astype("float32")
            f[:, :, 2] = np.clip(f[:, :, 2] * cfg["sw_r"], 0, 255)   # ch2 = R
            f[:, :, 1] = np.clip(f[:, :, 1] * cfg["sw_g"], 0, 255)   # ch1 = G
            f[:, :, 0] = np.clip(f[:, :, 0] * cfg["sw_b"], 0, 255)   # ch0 = B
            sat = cfg["saturation"]
            if sat != 1.0:
                luma = f[:, :, 2] * 0.299 + f[:, :, 1] * 0.587 + f[:, :, 0] * 0.114
                luma3 = luma[:, :, np.newaxis]
                f = np.clip(luma3 + sat * (f - luma3), 0, 255)
            if cfg["shadow"] > 0:
                luma = f[:, :, 2] * 0.299 + f[:, :, 1] * 0.587 + f[:, :, 0] * 0.114
                shadow_w = np.clip((100.0 - luma) / 100.0, 0.0, 1.0) ** 1.5
                f[:, :, 0] = np.clip(f[:, :, 0] - shadow_w * cfg["shadow"], 0, 255)
            return True, f.astype("uint8")
        except Exception:
            return False, None

    def apply_hw_gains(self) -> None:
        # Manual override from the stream dashboard — disables auto-AWB.
        cam = self._cam
        if cam:
            try:
                cam.set_controls({
                    "AwbEnable": False,
                    "ColourGains": (color_config["hw_r"], color_config["hw_b"]),
                    "ExposureValue": color_config["ev"],
                })
            except Exception:
                pass

    def release(self) -> None:
        cam = self._cam
        self._cam = None  # null out first — concurrent read() calls return False immediately
        if cam:
            def _close():
                try:
                    cam.stop()
                except Exception:
                    pass
                try:
                    cam.close()
                except Exception:
                    pass
            t = threading.Thread(target=_close, daemon=True)
            t.start()
            t.join(timeout=3.0)  # don't block reconnect if capture_array() is still stuck

    @property
    def name(self) -> str:
        return "picamera2(CSI)"


class _OpenCVBackend:
    """Wraps cv2.VideoCapture for USB webcams."""

    def __init__(self, device: int, width: int, height: int, fps: int) -> None:
        self._device = device
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: Optional[cv2.VideoCapture] = None

    def open(self) -> bool:
        cap = cv2.VideoCapture(self._device)
        if not cap.isOpened():
            cap.release()
            found = self._find_usb_camera()
            if found is None:
                return False
            self._device = found
            cap = cv2.VideoCapture(self._device)
            if not cap.isOpened():
                cap.release()
                return False
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(cv2.CAP_PROP_FPS, self._fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap
        return True

    def _find_usb_camera(self) -> Optional[int]:
        import subprocess, os
        for idx in range(19):
            path = f"/dev/video{idx}"
            if not os.path.exists(path):
                continue
            try:
                result = subprocess.run(
                    ["v4l2-ctl", "--device", path, "--list-formats"],
                    capture_output=True, timeout=1,
                )
                if b"MJPEG" in result.stdout or b"YUYV" in result.stdout:
                    return idx
            except Exception:
                continue
        return None

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self._cap:
            return False, None
        ret, frame = self._cap.read()
        return ret, frame if ret else None

    def release(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None

    @property
    def name(self) -> str:
        return f"opencv(USB /dev/video{self._device})"


class CameraPipeline:
    """
    Async camera capture and frame distribution.

    Usage:
        cam = CameraPipeline()
        await cam.start()
        frame = cam.latest_frame
        if frame and not frame.is_stale():
            process(frame.image)
        await cam.stop()
    """

    BUFFER_SIZE = 5
    MAX_CONSECUTIVE_ERRORS = 10
    RECONNECT_DELAY_S = 30.0    # wait before attempting camera reopen after failure

    def __init__(self) -> None:
        self._cfg = cfg.hardware.camera
        self._backend = None
        self._frame_buffer: Deque[Frame] = deque(maxlen=self.BUFFER_SIZE)
        self._frame_id = 0
        self._running = False
        self._capture_task: Optional[asyncio.Task] = None
        self._lock = threading.Lock()
        self._stats = {
            "frames_captured": 0,
            "frames_dropped": 0,
            "errors": 0,
            "fps": 0.0,
        }
        self._fps_window: Deque[float] = deque(maxlen=30)
        # Dedicated single-thread executor for frame reads only.
        # When capture_array() hangs, asyncio.wait_for times out but the thread
        # stays stuck. Using a separate pool prevents stuck read threads from
        # exhausting the default executor and deadlocking reconnect.
        self._read_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="cam_read"
        )

    def _open_camera(self) -> bool:
        w, h, fps = self._cfg.width, self._cfg.height, self._cfg.fps

        # Try picamera2 (CSI) first unless config explicitly says opencv
        backend_pref = getattr(self._cfg, "backend", "auto")
        if backend_pref != "opencv":
            b = _Picamera2Backend(w, h, fps)
            if b.open():
                self._backend = b
                log.info("camera.backend_selected", backend=b.name)
                return True

        # Fall back to OpenCV (USB webcam)
        b = _OpenCVBackend(self._cfg.device, w, h, fps)
        if b.open():
            self._backend = b
            log.info("camera.backend_selected", backend=b.name)
            return True

        return False

    def _release_camera(self) -> None:
        if self._backend:
            self._backend.release()
            self._backend = None

    async def start(self) -> bool:
        if self._running:
            return True

        success = await asyncio.get_event_loop().run_in_executor(None, self._open_camera)
        if not success:
            log.error("camera.failed_to_open")
            hw_registry.report_error("camera", reason="no usable camera found (tried picamera2 + opencv)")
            return False

        self._running = True
        self._capture_task = asyncio.create_task(self._capture_loop(), name="camera")
        log.info("camera.started", backend=self._backend.name,
                 resolution=f"{self._cfg.width}x{self._cfg.height}")
        hw_registry.report_real("camera", reason=f"{self._backend.name} {self._cfg.width}x{self._cfg.height}@{self._cfg.fps}fps")
        return True

    async def stop(self) -> None:
        self._running = False
        if self._capture_task:
            self._capture_task.cancel()
            try:
                await self._capture_task
            except asyncio.CancelledError:
                pass
        await asyncio.get_event_loop().run_in_executor(None, self._release_camera)

    async def _reconnect(self) -> bool:
        """Release and reopen the camera backend. Called after too_many_errors."""
        await asyncio.get_event_loop().run_in_executor(None, self._release_camera)
        log.info("camera.reconnecting", delay_s=self.RECONNECT_DELAY_S)
        await asyncio.sleep(self.RECONNECT_DELAY_S)
        if not self._running:
            return False
        success = await asyncio.get_event_loop().run_in_executor(None, self._open_camera)
        if success:
            log.info("camera.reconnected", backend=self._backend.name)
            hw_registry.report_real("camera", reason=f"{self._backend.name} (reconnected)")
        else:
            log.error("camera.reconnect_failed")
            hw_registry.report_error("camera", reason="reconnect failed")
        return success

    async def _capture_loop(self) -> None:
        consecutive_errors = 0
        target_interval = 1.0 / self._cfg.fps
        read_timeout = max(5.0, 3.0 / self._cfg.fps)  # 5s min; 3 missed frames

        while self._running:
            t_start = time.monotonic()

            try:
                success, image = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(self._read_executor, self._backend.read),
                    timeout=read_timeout,
                )
            except asyncio.TimeoutError:
                log.error("camera.read_timeout", timeout_s=read_timeout)
                success, image = False, None

            if not success:
                consecutive_errors += 1
                self._stats["errors"] += 1
                if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                    log.error("camera.too_many_errors", count=consecutive_errors)
                    if not await self._reconnect():
                        break   # give up only if reconnect also fails
                    consecutive_errors = 0
                    continue
                await asyncio.sleep(0.5)
                continue

            consecutive_errors = 0
            self._frame_id += 1
            frame = Frame(image=image, frame_id=self._frame_id)

            with self._lock:
                self._frame_buffer.append(frame)
            self._stats["frames_captured"] += 1

            now = time.monotonic()
            self._fps_window.append(now)
            if len(self._fps_window) >= 2:
                window_s = self._fps_window[-1] - self._fps_window[0]
                if window_s > 0:
                    self._stats["fps"] = round(len(self._fps_window) / window_s, 1)
            telemetry.gauge("camera.fps", self._stats["fps"])
            telemetry.increment("camera.frames")

            elapsed = time.monotonic() - t_start
            sleep_time = target_interval - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    @property
    def latest_frame(self) -> Optional[Frame]:
        with self._lock:
            return self._frame_buffer[-1] if self._frame_buffer else None

    def frames_since(self, frame_id: int) -> list:
        with self._lock:
            return [f for f in self._frame_buffer if f.frame_id > frame_id]

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def fps(self) -> float:
        return self._stats["fps"]

    def stats(self) -> Dict[str, Any]:
        latest = self.latest_frame
        return {
            **self._stats,
            "buffer_depth": len(self._frame_buffer),
            "latest_frame_age_ms": latest.age_ms() if latest else -1,
            "resolution": f"{self._cfg.width}x{self._cfg.height}",
            "backend": self._backend.name if self._backend else "none",
        }


camera = CameraPipeline()
