"""
Room fingerprinting tool.
Usage: python3 tools/room_fingerprint.py --name "living_room"

Captures 30 frames from camera, extracts visual fingerprint
(color histogram + brightness), saves to ~/.robot/memory/spatial.json.
Walk the camera to each room and run once per room.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np

from core.memory.spatial import spatial


def _extract_fingerprint(frames: list) -> dict:
    """
    Extract visual fingerprint from a list of BGR frames.
    Returns dict with lux, color histograms, dominant_hue.
    """
    lux_values = []
    h_hists = []

    for frame in frames:
        # Lux: mean of grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        lux_values.append(float(gray.mean()))

        # Color: hue histogram in HSV
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h_hist = cv2.calcHist([hsv], [0], None, [36], [0, 180])
        h_hist = h_hist.flatten() / h_hist.sum()
        h_hists.append(h_hist.tolist())

    avg_lux = float(np.mean(lux_values))
    lux_std = float(np.std(lux_values))
    avg_h_hist = np.mean(h_hists, axis=0).tolist()

    # Dominant hue bin (0-36 → 0-360° in steps of 10)
    dominant_bin = int(np.argmax(avg_h_hist))

    return {
        "avg_lux": round(avg_lux, 1),
        "lux_std": round(lux_std, 2),
        "color_histogram": [round(v, 4) for v in avg_h_hist],
        "dominant_hue_bin": dominant_bin,
    }


def fingerprint_room(name: str, device: int = 0, n_frames: int = 30) -> bool:
    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        print(f"ERROR: Cannot open camera {device}")
        return False

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    print(f"\nFingerprinting room: '{name}'")
    print(f"Capturing {n_frames} frames...")
    print("Point camera at the room normally. Press any key to start.\n")

    # Show preview until key press
    while True:
        ret, frame = cap.read()
        if ret:
            cv2.putText(frame, f"Room: {name} | Press SPACE to start | Q to quit",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.imshow("Room Fingerprint", frame)
        key = cv2.waitKey(30) & 0xFF
        if key == ord(' '):
            break
        if key == ord('q'):
            cap.release()
            cv2.destroyAllWindows()
            return False

    frames = []
    print("Capturing", end="", flush=True)
    while len(frames) < n_frames:
        ret, frame = cap.read()
        if ret:
            frames.append(frame.copy())
            display = frame.copy()
            cv2.putText(display, f"Capturing {len(frames)}/{n_frames}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.imshow("Room Fingerprint", display)
            cv2.waitKey(1)
            print(".", end="", flush=True)
            time.sleep(0.05)

    cap.release()
    cv2.destroyAllWindows()
    print(" done!\n")

    fp = _extract_fingerprint(frames)
    print(f"Fingerprint for '{name}':")
    print(f"  Avg brightness: {fp['avg_lux']:.1f} lux-equivalent")
    print(f"  Brightness std: {fp['lux_std']:.2f}")
    print(f"  Dominant hue bin: {fp['dominant_hue_bin']} ({fp['dominant_hue_bin'] * 10}°)")

    # Update spatial memory
    # Check if room exists, create or update
    existing = spatial.get_room(name)
    if existing:
        spatial.update_room(name, fp["avg_lux"])
        print(f"\nUpdated existing room '{name}'")
    else:
        spatial.add_room(name, name.replace("_", " ").title(), fp["avg_lux"])
        print(f"\nAdded new room '{name}'")

    # Store color histogram in room notes
    room = spatial.get_room(name)
    if room:
        room.color_histogram = fp["color_histogram"]
        room.notes["dominant_hue_bin"] = fp["dominant_hue_bin"]
        room.notes["lux_std"] = fp["lux_std"]
        spatial._save()

    print(f"Rooms in memory: {[r.name for r in spatial.list_rooms()]}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Room fingerprinting tool for Cosmo")
    parser.add_argument("--name", type=str, required=True, help="Room name (e.g. living_room)")
    parser.add_argument("--device", type=int, default=0, help="Camera device index")
    parser.add_argument("--frames", type=int, default=30, help="Number of frames to capture")
    parser.add_argument("--list", action="store_true", help="List all fingerprinted rooms")
    args = parser.parse_args()

    if args.list:
        rooms = spatial.list_rooms()
        if rooms:
            print("Fingerprinted rooms:")
            for r in rooms:
                print(f"  {r.room_id}: {r.name} — avg_lux={r.avg_lux:.0f}, visits={r.visit_count}")
        else:
            print("No rooms fingerprinted yet.")
        return

    fingerprint_room(args.name, device=args.device, n_frames=args.frames)


if __name__ == "__main__":
    main()
