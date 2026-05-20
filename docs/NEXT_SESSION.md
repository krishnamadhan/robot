# NEXT_SESSION.md — Task Handoff

> This file contains the focused task for the next Claude Code session.
> Replace entirely at end of each session with the new top priority.
> Last updated: 2026-05-20

---

## Current Hardware State (as of 2026-05-20)

**Connected and working:**
- Camera: Logitech C920 (USB, /dev/video0) — gesture + person detection confirmed running
- Speaker: JBL Flip 5 Bluetooth (28:FA:19:C1:73:F8) — paired and working via PipeWire
- Mic: C920 USB mic — `HD Pro Webcam C920: USB Audio (hw:3,0)` confirmed in logs
- UPS HAT: battery at ~94%, I2C 0x36 active
- Wake word: OpenWakeWord "hey_jarvis" — confirmed `oww.loaded` in logs
- Full audio pipeline: STT (faster-whisper base.en) → Claude Haiku → Piper TTS → JBL — all confirmed working

**Not yet wired (do NOT touch wiring tasks — software only):**
- OLED eyes (0x3C + 0x3D) — hardware on desk
- PIR, touch ×4, MPU-6050, BH1750 — wired but not enabled in hardware.yaml
- XT60 pigtail for LiPo → motors — on order from Robocraze

---

## Top Priority: Fix Victory Gesture False Positives (KI-010)

### Problem
`gesture.fired gesture=Victory conf=0.78` fires every ~3 seconds even when no hand is visible.
This was the source of the constant `excited_trill` bloop spam Madhan reported.
Root cause: opencv_skin backend detects skin-coloured blobs in background as Victory at low confidence threshold.

### Fix Options (pick ONE)

**Option A — Raise Victory threshold (quick fix, 5 min):**
Find the gesture confidence thresholds in `perception/vision/gesture.py` and raise Victory-specific threshold from 0.78 → 0.88. Wave/Open_Palm can stay at 0.78 (it's accurate).

**Option B — Add consecutive-frame debounce (better fix, 20 min):**
Gesture must appear in 2 out of 3 consecutive frames before firing the event. Single-frame detections are discarded. This prevents transient skin-blob misclassifications.

**Option C — Switch to MediaPipe Hands backend (best fix, 1-2h):**
gesture.py already has a MediaPipe stub. MediaPipe is far more accurate than opencv_skin and won't false-positive on background. Only cost: ~40MB RAM, ~15ms additional latency.

**Recommended: Option B first (quick), then Option C when time allows.**

### After fixing Victory spam

Next tests to complete from the 2026-05-20 session:

1. **Face recognition** — stand 60–80cm from camera, verify `face.recognized name=Madhan` in logs
2. **Emotion detection** — make happy/sad/surprised faces, verify `emotion.detected` logs
3. **Wake word + STT** — say "Hey Jarvis" then speak, verify Cosmo responds via speaker
4. **Eye expressions** — run `python3 tools/eye_simulator.py`, cycle all 12
5. **Episodic memory** — `curl http://localhost:8000/memory/recent`

### After all tests pass

Wire OLED eyes:
1. Connect both SSD1306s to I2C (SDA=GPIO2/Pin3, SCL=GPIO3/Pin5, VCC=3.3V, GND)
2. Bridge A0 pad on right eye board (solder blob → VCC) to change address to 0x3D
3. Run `sudo i2cdetect -y 1` → verify both 0x3C and 0x3D appear
4. In `expression/eyes.py` change render target from `"terminal"` to `"oled"`
5. `pm2 restart cosmo` → verify eyes render on hardware

---

## Disk Warning
Disk is at 90% (5.8GB free). Keep an eye on it. Next time it hits 93%+, check:
- `/home/pi/.robot/logs/cosmo-error.log` (ALSA noise fills this fast)
- `/home/pi/downloads/` (414MB of unknown files)
