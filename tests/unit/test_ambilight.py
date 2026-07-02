import json

import cv2
import numpy as np

import behavior.ambilight as ambilight
from behavior.ambilight import analyze, analyze_debug


TV_QUAD = [(150, 110), (500, 100), (520, 355), (130, 365)]


def _room_frame(tv_bgr=(20, 20, 220)) -> np.ndarray:
    frame = np.full((480, 640, 3), (145, 140, 132), dtype=np.uint8)
    cv2.fillConvexPoly(frame, np.array(TV_QUAD, dtype=np.int32), tv_bgr)
    # A saturated non-TV distraction that should not affect ROI analysis.
    cv2.rectangle(frame, (20, 40), (130, 430), (255, 0, 0), -1)
    return frame


def test_roi_tracks_tv_color_not_room_distraction():
    result = analyze_debug(_room_frame(tv_bgr=(20, 20, 230)), roi_points=TV_QUAD)

    assert result.roi_active is True
    assert result.color is not None
    r, g, b, bright = result.color
    assert r > 220
    assert g < 80
    assert b < 80
    assert bright >= 70


def test_dark_roi_turns_off_despite_colored_reflection_elsewhere():
    result = analyze_debug(_room_frame(tv_bgr=(6, 6, 6)), roi_points=TV_QUAD)

    assert result.roi_active is True
    assert result.color is None
    assert result.vivid_frac < ambilight.CONTENT_OFF


def test_analyze_uses_normalized_roi_config(tmp_path, monkeypatch):
    frame = _room_frame(tv_bgr=(0, 220, 0))
    roi_path = tmp_path / "ambilight_roi.json"
    roi_path.write_text(json.dumps({
        "version": 1,
        "normalized": True,
        "points": [[x / 640, y / 480] for x, y in TV_QUAD],
    }))
    monkeypatch.setattr(ambilight, "ROI_CONFIG", roi_path)
    monkeypatch.setattr(ambilight, "_roi_cache_mtime", None)
    monkeypatch.setattr(ambilight, "_roi_cache_points", None)

    color, vivid_frac = analyze(frame)

    assert color is not None
    r, g, b, _ = color
    assert g > 220
    assert r < 80
    assert b < 80
    assert vivid_frac > ambilight.CONTENT_ON


def test_analyze_uses_legacy_rect_roi_config(tmp_path, monkeypatch):
    frame = _room_frame(tv_bgr=(0, 220, 0))
    missing_new_path = tmp_path / "missing_new_roi.json"
    legacy_path = tmp_path / "legacy_roi.json"
    legacy_path.write_text(json.dumps({
        "roi": [
            TV_QUAD[0][0] / 640,
            TV_QUAD[1][1] / 480,
            TV_QUAD[2][0] / 640,
            TV_QUAD[3][1] / 480,
        ],
    }))
    monkeypatch.setattr(ambilight, "ROI_CONFIG", missing_new_path)
    monkeypatch.setattr(ambilight, "LEGACY_ROI_CONFIG", legacy_path)
    monkeypatch.setattr(ambilight, "_roi_cache_mtime", None)
    monkeypatch.setattr(ambilight, "_roi_cache_points", None)

    color, vivid_frac = analyze(frame)

    assert color is not None
    r, g, b, _ = color
    assert g > 220
    assert r < 80
    assert b < 80
    assert vivid_frac > ambilight.CONTENT_ON
