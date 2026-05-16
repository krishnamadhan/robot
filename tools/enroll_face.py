"""
Face enrollment tool.
Usage: python3 tools/enroll_face.py --name "Madhan"
       python3 tools/enroll_face.py --name "Madhan" --headless
       python3 tools/enroll_face.py --list
       python3 tools/enroll_face.py --forget "Madhan"

Captures 10 face samples from webcam with quality checks.
--headless: no display required (SSH-friendly), auto-captures only.
Shows live preview when display is available — SPACE to capture, Q to quit.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np

from perception.vision.face import FaceEngine, FACE_INPUT_SIZE, FACES_DIR

TARGET_SAMPLES = 20


def _brightness_ok(img: np.ndarray, min_lux: int = 40, max_lux: int = 220) -> bool:
    mean = img.mean()
    return min_lux < mean < max_lux


def _sharpness_ok(img: np.ndarray, threshold: float = 60.0) -> bool:
    laplacian = cv2.Laplacian(img, cv2.CV_64F)
    return laplacian.var() > threshold


def _face_centered(bbox, frame_w: int, frame_h: int, margin: float = 0.25) -> bool:
    x, y, w, h = bbox
    cx = x + w / 2
    cy = y + h / 2
    return (
        frame_w * margin < cx < frame_w * (1 - margin)
        and frame_h * margin < cy < frame_h * (1 - margin)
    )


def _draw_overlay(frame: np.ndarray, faces, collected: int, total: int,
                  status: str, status_color) -> None:
    for (x, y, w, h) in faces:
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

    progress = f"Collected: {collected}/{total}"
    cv2.putText(frame, progress, (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.putText(frame, status, (10, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)

    # Guide box (center third of frame)
    h, w = frame.shape[:2]
    gx1, gy1 = w // 4, h // 4
    gx2, gy2 = 3 * w // 4, 3 * h // 4
    cv2.rectangle(frame, (gx1, gy1), (gx2, gy2), (0, 200, 255), 1)
    cv2.putText(frame, "Position face here", (gx1, gy1 - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)


def enroll(name: str, device: int = 0, headless: bool = False, samples: int = TARGET_SAMPLES) -> bool:
    engine = FaceEngine()
    engine.load()   # load existing model if any

    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        print(f"ERROR: Cannot open camera {device}")
        return False

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    print(f"\nEnrolling: {name}")
    print(f"Target: {samples} samples")
    if headless:
        print("Mode: headless (no display) — auto-capture only")
        print("Look at the camera. Keep still between captures.\n")
    else:
        print("Instructions:")
        print("  • Keep face inside the guide box")
        print("  • Vary angle slightly between captures")
        print("  • SPACE = capture manually | Q = quit\n")
    print("Auto-capture will trigger when face is detected and stable...")
    print()

    collected: list = []
    last_capture = 0.0
    auto_delay = 0.8   # seconds between auto-captures

    while len(collected) < samples:
        ret, frame = cap.read()
        if not ret:
            continue

        display = frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_eq = cv2.equalizeHist(gray)

        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml"
        )
        faces_raw = cascade.detectMultiScale(
            gray_eq, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80)
        )
        faces = faces_raw if len(faces_raw) > 0 else []

        now = time.monotonic()
        status = "No face detected"
        status_color = (0, 0, 255)
        do_capture = False

        if len(faces) == 1:
            x, y, w, h = faces[0]
            face_crop_gray = gray[y:y+h, x:x+w]   # grayscale for quality checks
            face_crop_bgr = frame[y:y+h, x:x+w]   # BGR for SFace embedding

            bright = _brightness_ok(face_crop_gray)
            sharp = _sharpness_ok(face_crop_gray)
            centered = _face_centered((x, y, w, h), frame.shape[1], frame.shape[0])

            if not bright:
                status = "Too dark/bright — adjust lighting"
                status_color = (0, 165, 255)
            elif not sharp:
                status = "Move closer or hold still"
                status_color = (0, 165, 255)
            elif not centered:
                status = "Center your face in the guide box"
                status_color = (0, 165, 255)
            else:
                status = "Hold still — auto capturing..."
                status_color = (0, 255, 0)
                if now - last_capture >= auto_delay:
                    do_capture = True
                    face_crop = face_crop_bgr   # pass BGR crop for embedding

        elif len(faces) > 1:
            status = "Multiple faces detected — only one person please"
            status_color = (0, 0, 255)

        if not headless:
            _draw_overlay(display, faces, len(collected), samples, status, status_color)
            cv2.imshow(f"Enroll: {name}", display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            if key == ord(' ') and len(faces) == 1:
                do_capture = True
        else:
            # Print status to terminal instead
            print(f"\r  [{len(collected)}/{samples}] {status}    ", end="", flush=True)

        if do_capture and len(faces) == 1:
            x, y, w, h = faces[0]
            face_resized = cv2.resize(face_crop_bgr, FACE_INPUT_SIZE)  # BGR for SFace
            collected.append(face_resized)
            last_capture = now
            count = len(collected)
            print(f"\n  Captured {count}/{samples} ✓", flush=True)

            if not headless:
                # Flash effect
                flash = display.copy()
                flash[:] = (255, 255, 255)
                cv2.addWeighted(flash, 0.3, display, 0.7, 0, display)
                cv2.imshow(f"Enroll: {name}", display)
                cv2.waitKey(100)

    cap.release()
    if not headless:
        cv2.destroyAllWindows()

    if not collected:
        print("\nNo samples collected — aborting.")
        return False

    print(f"\n\nTraining model with {len(collected)} samples for '{name}'...")
    ok = engine.enroll_person(name, collected)

    if ok:
        enrolled = engine.list_enrolled()
        print(f"Enrolled '{name}' successfully!")
        print(f"All enrolled persons: {', '.join(enrolled)}")
        return True
    else:
        print("Training failed — check logs.")
        return False


def main():
    parser = argparse.ArgumentParser(description="Cosmo face enrollment tool")
    parser.add_argument("--name", type=str, help="Name of person to enroll")
    parser.add_argument("--list", action="store_true", help="List enrolled persons")
    parser.add_argument("--forget", type=str, help="Remove a person's enrollment")
    parser.add_argument("--device", type=int, default=0, help="Camera device index")
    parser.add_argument("--samples", type=int, default=TARGET_SAMPLES, help="Number of face samples to capture")
    parser.add_argument("--headless", action="store_true", help="No display — SSH friendly")
    args = parser.parse_args()

    if args.list:
        engine = FaceEngine()
        engine.load()
        enrolled = engine.list_enrolled()
        if enrolled:
            print("Enrolled persons:")
            for name in enrolled:
                samples = list((FACES_DIR / "samples" / name).glob("*.png"))
                print(f"  {name}: {len(samples)} samples")
        else:
            print("No persons enrolled yet.")
        return

    if args.forget:
        engine = FaceEngine()
        engine.load()
        ok = engine.forget_person(args.forget)
        print(f"Forgot '{args.forget}'" if ok else f"'{args.forget}' not found")
        return

    if not args.name:
        parser.print_help()
        return

    enroll(args.name, device=args.device, headless=args.headless, samples=args.samples)


if __name__ == "__main__":
    main()
