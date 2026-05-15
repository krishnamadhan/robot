"""
Real-time room detection.
Usage: python3 tools/detect_room.py

Shows current room estimate from camera + light sensor.
Updates every 2 seconds.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np

from core.memory.spatial import spatial


def _estimate_lux_from_frame(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(gray.mean())


def detect_room_loop(device: int = 0) -> None:
    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        print("ERROR: Cannot open camera")
        return

    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    rooms = spatial.list_rooms()

    if not rooms:
        print("No rooms fingerprinted yet.")
        print("Run: python3 tools/room_fingerprint.py --name living_room")
        cap.release()
        return

    print(f"Detecting room... ({len(rooms)} rooms in memory)")
    print("Press Q to quit\n")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            lux = _estimate_lux_from_frame(frame)
            room_id, confidence = spatial.identify_room(lux=lux)
            room_name = spatial.get_room(room_id).name if room_id else "Unknown"

            # Display overlay
            display = frame.copy()
            color = (0, 255, 0) if confidence > 0.6 else ((0, 165, 255) if confidence > 0.3 else (0, 0, 255))
            cv2.putText(display, f"Room: {room_name} ({confidence:.0%})",
                        (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
            cv2.putText(display, f"Lux: {lux:.0f}",
                        (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)

            for i, r in enumerate(rooms):
                cv2.putText(display, f"  {r.name}: {r.avg_lux:.0f} lux",
                            (10, 120 + i * 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

            cv2.imshow("Cosmo Room Detection", display)
            print(f"\r  Room: {room_name:20s} | Confidence: {confidence:.0%} | Lux: {lux:.0f}",
                  end="", flush=True)

            if cv2.waitKey(500) & 0xFF == ord('q'):
                break

    finally:
        print()
        cap.release()
        cv2.destroyAllWindows()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=int, default=0)
    args = parser.parse_args()
    detect_room_loop(args.device)


if __name__ == "__main__":
    main()
