#!/usr/bin/env python3
"""Calibrate the TV-screen ROI for behavior.ambilight.

Examples:
  PYTHONPATH=/home/pi/robot python3 tools/ambilight_calibrate.py --save
  PYTHONPATH=/home/pi/robot python3 tools/ambilight_calibrate.py --image frame.jpg --save
  PYTHONPATH=/home/pi/robot python3 tools/ambilight_calibrate.py --points "120,90 520,84 540,360 95,370" --save
"""

import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from behavior.ambilight import ROI_CONFIG, _order_quad  # noqa: E402

Point = Tuple[float, float]


def _fetch_snapshot(url: str) -> np.ndarray:
    with urllib.request.urlopen(url, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    raw = base64.b64decode(payload["jpeg_b64"])
    arr = np.frombuffer(raw, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        raise RuntimeError("snapshot did not decode as an image")
    return bgr


def _load_frame(args: argparse.Namespace) -> np.ndarray:
    if args.image:
        bgr = cv2.imread(args.image)
        if bgr is None:
            raise RuntimeError(f"could not read {args.image}")
        return bgr
    return _fetch_snapshot(args.snapshot_url)


def _parse_points(raw: str) -> List[Point]:
    parts = raw.replace(";", " ").split()
    values: List[Point] = []
    for part in parts:
        x_raw, y_raw = part.split(",", 1)
        values.append((float(x_raw), float(y_raw)))
    if len(values) != 4:
        raise ValueError("--points must contain four x,y pairs")
    return values


def _auto_detect(bgr: np.ndarray) -> List[Point]:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 35, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    frame_area = float(w * h)
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < frame_area * 0.04:
            continue
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.025 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        pts = approx.reshape(4, 2).astype(np.float32)
        ordered = _order_quad(pts)
        top = np.linalg.norm(ordered[1] - ordered[0])
        bottom = np.linalg.norm(ordered[2] - ordered[3])
        left = np.linalg.norm(ordered[3] - ordered[0])
        right = np.linalg.norm(ordered[2] - ordered[1])
        width = max(top, bottom)
        height = max(left, right)
        if height <= 1:
            continue
        aspect = width / height
        if not 1.1 <= aspect <= 2.7:
            continue
        margin = 6
        if (
            np.any(ordered[:, 0] < margin)
            or np.any(ordered[:, 0] > w - margin)
            or np.any(ordered[:, 1] < margin)
            or np.any(ordered[:, 1] > h - margin)
        ):
            continue
        candidates.append((area, ordered))

    if not candidates:
        raise RuntimeError("auto-detect found no plausible TV quadrilateral; use --points")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [(float(x), float(y)) for x, y in candidates[0][1]]


def _normalize(points: Sequence[Point], shape: Tuple[int, int, int]) -> List[List[float]]:
    h, w = shape[:2]
    ordered = _order_quad(points)
    return [[round(float(x) / w, 6), round(float(y) / h, 6)] for x, y in ordered]


def _draw_overlay(bgr: np.ndarray, points: Sequence[Point], out_path: Path) -> None:
    display = bgr.copy()
    ordered = _order_quad(points).astype(int)
    cv2.polylines(display, [ordered], True, (0, 255, 255), 3)
    for idx, (x, y) in enumerate(ordered, start=1):
        cv2.circle(display, (int(x), int(y)), 6, (0, 0, 255), -1)
        cv2.putText(display, str(idx), (int(x) + 8, int(y) - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    cv2.imwrite(str(out_path), display, [cv2.IMWRITE_JPEG_QUALITY, 92])


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="Use a local image instead of the live API snapshot")
    parser.add_argument("--snapshot-url", default="http://127.0.0.1:8000/camera/snapshot")
    parser.add_argument("--points", help='Manual four corners: "x,y x,y x,y x,y"')
    parser.add_argument("--out", default=str(ROI_CONFIG), help="ROI config path")
    parser.add_argument("--overlay", default="/tmp/ambilight_roi_overlay.jpg")
    parser.add_argument("--save", action="store_true", help="Write the ROI config")
    args = parser.parse_args(list(argv) if argv is not None else None)

    bgr = _load_frame(args)
    points = _parse_points(args.points) if args.points else _auto_detect(bgr)
    normalized = _normalize(points, bgr.shape)

    overlay = Path(args.overlay)
    _draw_overlay(bgr, points, overlay)

    doc = {
        "version": 1,
        "normalized": True,
        "points": normalized,
        "source_shape": [int(bgr.shape[1]), int(bgr.shape[0])],
        "note": "Points are ordered top-left, top-right, bottom-right, bottom-left.",
    }

    if args.save:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"saved {out}")
    else:
        print(json.dumps(doc, indent=2))
        print("dry run; add --save to write the config")
    print(f"overlay {overlay}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
