#!/usr/bin/env python3
"""
End-to-end ambilight accuracy verification via TV screencast.

Casts solid-colour cards to the TV (Chromecast), then for each card:
grabs a camera snapshot, runs the real ambilight analysis (ROI + CCM),
and compares the colour that would be sent to the lights against truth.

Optionally recalibrates the TV ROI from the red card first (--calibrate),
which trims non-TV areas (walls, pillar) out of the sample.

Usage:
  PYTHONPATH=/home/pi/robot python3 tools/ambilight_cast_verify.py --calibrate
  PYTHONPATH=/home/pi/robot python3 tools/ambilight_cast_verify.py --colours red,blue
Output: contact sheet at --out (default /tmp/ambilight_cast_verify.jpg) + stdout report.
"""

import argparse
import base64
import colorsys
import functools
import http.server
import json
import socket
import socketserver
import sys
import threading
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TV_IP = "192.168.1.10"          # Samsung Q70D "Maddy TV"
HTTP_PORT = 8901                 # transient card server (freed on exit)
API = "http://127.0.0.1:8000"
SETTLE_S = 7.0                   # cast → TV actually displaying

CARDS = {                        # truth RGB
    "red":     (255, 0, 0),
    "green":   (0, 255, 0),
    "blue":    (0, 0, 255),
    "white":   (255, 255, 255),
    "cyan":    (0, 255, 255),
    "magenta": (255, 0, 255),
    "yellow":  (255, 255, 0),
    "orange":  (255, 140, 0),
}


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((TV_IP, 80))
    ip = s.getsockname()[0]
    s.close()
    return ip


def make_cards(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    for name, (r, g, b) in CARDS.items():
        img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        img[:] = (b, g, r)
        cv2.imwrite(str(outdir / f"{name}.png"), img)


def serve_cards(outdir: Path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(outdir))
    handler.log_message = lambda *a, **k: None
    httpd = socketserver.TCPServer(("", HTTP_PORT), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def get_cast():
    import pychromecast
    casts, browser = pychromecast.get_chromecasts(timeout=8)
    for cc in casts:
        if cc.cast_info.host == TV_IP or "maddy" in (cc.name or "").lower():
            cc.wait(timeout=10)
            return cc, browser
    raise RuntimeError(
        f"TV not found at {TV_IP}; seen: {[(c.name, c.cast_info.host) for c in casts]}")


def cast_card(cc, url: str) -> None:
    mc = cc.media_controller
    mc.play_media(url, "image/png")
    mc.block_until_active(timeout=10)


def api_json(path: str, method: str = "GET", body: dict = None, token: str = ""):
    req = urllib.request.Request(f"{API}{path}", method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=15) as resp:
        return json.loads(resp.read().decode())


def snapshot() -> np.ndarray:
    payload = api_json("/camera/snapshot")
    raw = base64.b64decode(payload["jpeg_b64"])
    bgr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise RuntimeError("snapshot decode failed")
    return bgr


def hue_err(rgb_a, rgb_b) -> float:
    ha = colorsys.rgb_to_hsv(*[x / 255 for x in rgb_a])[0] * 360
    hb = colorsys.rgb_to_hsv(*[x / 255 for x in rgb_b])[0] * 360
    d = abs(ha - hb) % 360
    return min(d, 360 - d)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true",
                    help="recalibrate TV ROI from the red card first")
    ap.add_argument("--colours", default=",".join(CARDS),
                    help="comma list of cards to test")
    ap.add_argument("--out", default="/tmp/ambilight_cast_verify.jpg")
    ap.add_argument("--token", default="")
    args = ap.parse_args()

    token = args.token
    if not token:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("ROBOT_API_TOKEN="):
                token = line.split("=", 1)[1].strip()

    cards_dir = Path("/tmp/ambilight_cards")
    make_cards(cards_dir)
    httpd = serve_cards(cards_dir)
    ip = local_ip()
    print(f"Card server: http://{ip}:{HTTP_PORT}/")

    cc, browser = get_cast()
    print(f"Cast target: {cc.name} ({cc.cast_info.host})")

    # TV sync ON → locks camera WB (CCM validity) and drives strip+bulb live.
    print("TV sync ON (locks WB; strip + bulb follow live)...")
    api_json("/led/tv", "POST", {"on": True}, token)
    time.sleep(2)

    if args.calibrate:
        print("Casting RED for ROI calibration...")
        cast_card(cc, f"http://{ip}:{HTTP_PORT}/red.png")
        time.sleep(SETTLE_S)
        res = api_json("/led/calibrate", "POST", None, token)
        print(f"ROI calibrated: {res.get('points')}")
        if res.get("preview_b64"):
            Path("/tmp/ambilight_roi_preview.jpg").write_bytes(
                base64.b64decode(res["preview_b64"]))
            print("ROI preview: /tmp/ambilight_roi_preview.jpg")

    # ROI file is mtime-cached inside ambilight — a recalibration above is
    # picked up automatically here AND by the running cosmo service.
    from behavior.ambilight import analyze_debug

    rows = []
    report = []
    names = [c.strip() for c in args.colours.split(",") if c.strip() in CARDS]
    for name in names:
        truth = CARDS[name]
        print(f"Casting {name}...", end=" ", flush=True)
        cast_card(cc, f"http://{ip}:{HTTP_PORT}/{name}.png")
        time.sleep(SETTLE_S)
        frame = snapshot()
        a = analyze_debug(frame)
        if a.color is None:
            print("NO CONTENT DETECTED")
            report.append((name, truth, None, None, a.vivid_frac))
            continue
        out = a.color[:3]
        err = hue_err(truth, out) if name != "white" else None
        print(f"out=rgb{out} bright={a.color[3]}% "
              f"hue_err={f'{err:.1f}°' if err is not None else 'n/a'} "
              f"vivid={a.vivid_frac:.2f}")
        report.append((name, truth, out, err, a.vivid_frac))
        rows.append((name, frame, truth, out, a))

    # ── contact sheet ──
    if rows:
        tile_h, tile_w = 180, 320
        sheet = []
        for name, frame, truth, out, a in rows:
            f = cv2.resize(frame, (tile_w, tile_h))
            if a.roi_points:
                pts = np.array(a.roi_points, dtype=np.int32)
                scale = np.array([tile_w / frame.shape[1], tile_h / frame.shape[0]])
                pts = (pts * scale).astype(np.int32)
                cv2.polylines(f, [pts.reshape(-1, 1, 2)], True, (0, 255, 0), 1)
            tr = np.full((tile_h, 120, 3), truth[::-1], dtype=np.uint8)
            ot = np.full((tile_h, 120, 3), out[::-1], dtype=np.uint8)
            for img, label in ((f, name), (tr, "truth"), (ot, "output")):
                cv2.putText(img, label, (6, 20), cv2.FONT_HERSHEY_SIMPLEX,
                            0.55, (255, 255, 255), 2)
            sheet.append(np.hstack([f, tr, ot]))
        cv2.imwrite(args.out, np.vstack(sheet), [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"\nContact sheet: {args.out}")

    print("\n── REPORT ──")
    worst = 0.0
    for name, truth, out, err, vivid in report:
        if out is None:
            print(f"  {name:8s} FAILED — no content (vivid={vivid:.2f})")
            continue
        e = f"{err:5.1f}°" if err is not None else "  n/a"
        print(f"  {name:8s} truth=rgb{truth}  out=rgb{out}  hue_err={e}")
        if err:
            worst = max(worst, err)
    print(f"Worst hue error: {worst:.1f}° "
          f"({'PASS ✅' if worst < 20 else 'NEEDS CCM RECAL ⚠️'} — <20° reads as same colour)")

    browser.stop_discovery()
    httpd.shutdown()


if __name__ == "__main__":
    main()
