#!/usr/bin/env python3
"""Interactive test for the ONNX person detector (perception/vision/person.py).

Usage:
  PYTHONPATH=/home/pi/robot python3 tools/person_onnx_test.py [image.jpg]
  - With an image: runs detection on it.
  - Without: grabs a frame from the CSI camera (needs cosmo STOPPED) or,
    if the camera is busy, fetches a snapshot from the stream server :8080.

Shows backend load status, per-frame latency, and detections.
"""
import sys
import time
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, "/home/pi/robot")
from perception.vision.person import _YOLOOnnxDetector  # noqa: E402
from utils.config import cfg  # noqa: E402


def get_test_frame() -> np.ndarray:
    if len(sys.argv) > 1:
        img = cv2.imread(sys.argv[1])
        if img is None:
            sys.exit(f"Could not read image: {sys.argv[1]}")
        print(f"frame: {sys.argv[1]} {img.shape}")
        return img
    # Try stream server snapshot (works while cosmo is running)
    try:
        req = urllib.request.urlopen("http://127.0.0.1:8080/snap.jpg", timeout=5)
        arr = np.frombuffer(req.read(), np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None:
            print(f"frame: stream server snapshot {img.shape}")
            return img
    except Exception as e:
        print(f"stream snapshot unavailable ({e}); using synthetic frame")
    return np.random.randint(0, 255, (240, 320, 3), dtype=np.uint8)


def main() -> None:
    det = _YOLOOnnxDetector(cfg.models.person_detection)
    ok = det.initialize()
    print(f"onnx detector loaded: {ok}")
    if not ok:
        sys.exit(1)

    frame = get_test_frame()
    # Warmup + timed runs
    det.detect(frame, 0.5)
    t0 = time.perf_counter()
    n_runs = 10
    for _ in range(n_runs):
        results = det.detect(frame, 0.5)
    dt = (time.perf_counter() - t0) / n_runs * 1000
    print(f"latency: {dt:.1f} ms/frame ({n_runs} runs)")
    print(f"detections: {len(results)}")
    for d in results:
        print(f"  bbox={d.bbox} conf={d.confidence:.2f} dist={d.distance_estimate} pos={d.position_h}")


if __name__ == "__main__":
    main()
