#!/usr/bin/env python3
"""Create a visual ambilight verification contact sheet.

Each row shows: camera frame with ROI outline, perspective-corrected ROI sample,
and the color/brightness the algorithm would send to the strip.

Examples:
  PYTHONPATH=/home/pi/robot python3 tools/ambilight_verify.py --out /tmp/verify.jpg
  PYTHONPATH=/home/pi/robot python3 tools/ambilight_verify.py frame_*.jpg --out /tmp/verify.jpg
"""

import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path
from typing import Iterable, List, Tuple

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from behavior.ambilight import _extract_roi, analyze_debug  # noqa: E402


def _fetch_snapshot(url: str) -> Tuple[str, np.ndarray]:
    with urllib.request.urlopen(url, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    raw = base64.b64decode(payload["jpeg_b64"])
    arr = np.frombuffer(raw, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        raise RuntimeError("snapshot did not decode as an image")
    return f"snapshot frame_id={payload.get('frame_id', '?')}", bgr


def _load_inputs(paths: List[str], snapshot_url: str) -> List[Tuple[str, np.ndarray]]:
    if not paths:
        return [_fetch_snapshot(snapshot_url)]
    frames: List[Tuple[str, np.ndarray]] = []
    for path in paths:
        bgr = cv2.imread(path)
        if bgr is None:
            raise RuntimeError(f"could not read {path}")
        frames.append((Path(path).name, bgr))
    return frames


def _resize_fit(bgr: np.ndarray, width: int, height: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    scale = min(width / w, height / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    resized = cv2.resize(bgr, (nw, nh))
    canvas = np.full((height, width, 3), 24, dtype=np.uint8)
    x = (width - nw) // 2
    y = (height - nh) // 2
    canvas[y:y + nh, x:x + nw] = resized
    return canvas


def _draw_frame_panel(name: str, bgr: np.ndarray, points) -> np.ndarray:
    marked = bgr.copy()
    if points:
        poly = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(marked, [poly], True, (0, 255, 255), 3)
    panel = _resize_fit(marked, 360, 220)
    cv2.putText(panel, name[:38], (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return panel


def _draw_sample_panel(sample: np.ndarray, roi_active: bool) -> np.ndarray:
    panel = _resize_fit(sample, 260, 220)
    label = "ROI sample" if roi_active else "Whole frame sample"
    cv2.putText(panel, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return panel


def _draw_swatch_panel(result) -> np.ndarray:
    panel = np.full((220, 220, 3), 34, dtype=np.uint8)
    if result.color is None:
        color_bgr = (0, 0, 0)
        label = "OFF"
        detail = f"vivid={result.vivid_frac:.3f}"
    else:
        r, g, b, bright = result.color
        color_bgr = (b, g, r)
        label = f"RGB {r},{g},{b}"
        detail = f"bright={bright}% vivid={result.vivid_frac:.3f}"
    cv2.rectangle(panel, (28, 42), (192, 148), color_bgr, -1)
    cv2.rectangle(panel, (28, 42), (192, 148), (210, 210, 210), 1)
    cv2.putText(panel, label, (18, 178), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)
    cv2.putText(panel, detail, (18, 202), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1)
    return panel


def _row(name: str, bgr: np.ndarray) -> Tuple[np.ndarray, str]:
    result = analyze_debug(bgr)
    sample, _, _ = _extract_roi(bgr)
    panels = [
        _draw_frame_panel(name, bgr, result.roi_points),
        _draw_sample_panel(sample, result.roi_active),
        _draw_swatch_panel(result),
    ]
    row = np.concatenate(panels, axis=1)
    if result.color is None:
        summary = f"{name}: OFF vivid={result.vivid_frac:.3f} roi={result.roi_active}"
    else:
        r, g, b, bright = result.color
        summary = (
            f"{name}: rgb=({r},{g},{b}) brightness={bright} "
            f"vivid={result.vivid_frac:.3f} roi={result.roi_active}"
        )
    return row, summary


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="*", help="Images to analyze; omit for live snapshot")
    parser.add_argument("--snapshot-url", default="http://127.0.0.1:8000/camera/snapshot")
    parser.add_argument("--out", default="/tmp/ambilight_verify.jpg")
    args = parser.parse_args(list(argv) if argv is not None else None)

    rows = []
    for name, bgr in _load_inputs(args.images, args.snapshot_url):
        row, summary = _row(name, bgr)
        rows.append(row)
        print(summary)
    sheet = np.concatenate(rows, axis=0)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
