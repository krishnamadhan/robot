"""
Camera capture pipeline for the Logitech USB webcam.

Runs as an async task that continuously reads frames into a thread-safe
buffer. Downstream consumers (person detector, motion detector) pull from
the buffer — they don't drive capture timing.

Frame dropping under high CPU is intentional: it's better to process
fewer frames than to build up a backlog that causes detection lag.
"""

import asyncio
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

    BUFFER_SIZE = 5          # drop oldest frames when full
    MAX_CONSECUTIVE_ERRORS = 10

    def __init__(self) -> None:
        self._cfg = cfg.hardware.camera
        self._cap: Optional[cv2.VideoCapture] = None
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

    async def start(self) -> bool:
        if self._running:
            return True

        success = await asyncio.get_event_loop().run_in_executor(None, self._open_camera)
        if not success:
            log.error("camera.failed_to_open", device=self._cfg.device)
            hw_registry.report_error("camera", reason=f"failed to open /dev/video{self._cfg.device}")
            return False

        self._running = True
        self._capture_task = asyncio.create_task(self._capture_loop(), name="camera")
        log.info("camera.started", device=self._cfg.device,
                  resolution=f"{self._cfg.width}x{self._cfg.height}")
        hw_registry.report_real("camera",
                                reason=f"/dev/video{self._cfg.device} {self._cfg.width}x{self._cfg.height}@{self._cfg.fps}fps")
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

    def _open_camera(self) -> bool:
        self._cap = cv2.VideoCapture(self._cfg.device)
        if not self._cap.isOpened():
            return False
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._cfg.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._cfg.height)
        self._cap.set(cv2.CAP_PROP_FPS, self._cfg.fps)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # minimize capture latency
        return True

    def _release_camera(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None

    async def _capture_loop(self) -> None:
        consecutive_errors = 0
        target_interval = 1.0 / self._cfg.fps

        while self._running:
            t_start = time.monotonic()

            success, image = await asyncio.get_event_loop().run_in_executor(
                None, self._read_frame
            )

            if not success:
                consecutive_errors += 1
                self._stats["errors"] += 1
                if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                    log.error("camera.too_many_errors", count=consecutive_errors)
                    break
                await asyncio.sleep(0.5)
                continue

            consecutive_errors = 0
            self._frame_id += 1
            frame = Frame(image=image, frame_id=self._frame_id)

            with self._lock:
                self._frame_buffer.append(frame)
            self._stats["frames_captured"] += 1

            # FPS tracking
            now = time.monotonic()
            self._fps_window.append(now)
            if len(self._fps_window) >= 2:
                window_s = self._fps_window[-1] - self._fps_window[0]
                if window_s > 0:
                    self._stats["fps"] = round(len(self._fps_window) / window_s, 1)
            telemetry.gauge("camera.fps", self._stats["fps"])
            telemetry.increment("camera.frames")

            # Sleep to hit target FPS without busy-looping
            elapsed = time.monotonic() - t_start
            sleep_time = target_interval - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    def _read_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self._cap:
            return False, None
        ret, frame = self._cap.read()
        return ret, frame if ret else None

    @property
    def latest_frame(self) -> Optional[Frame]:
        with self._lock:
            return self._frame_buffer[-1] if self._frame_buffer else None

    def frames_since(self, frame_id: int) -> list:
        """Get all frames newer than frame_id."""
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
        }


camera = CameraPipeline()
