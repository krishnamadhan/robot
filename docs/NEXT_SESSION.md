# NEXT_SESSION.md — Task Handoff

> This file contains the focused task for the next Claude Code session.
> Replace entirely at end of each session with the new top priority.
> Last updated: 2026-05-21 (3rd session)

---

## Current Hardware State (as of 2026-05-21)

**Connected and working:**
- Camera: Logitech C920 (USB, /dev/video0) — YOLO11n @ 10.1 FPS confirmed
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

## Code State (after 2026-05-21 3rd session)

| Fix | File | Status |
|-----|------|--------|
| YOLO11n + CPU torch (KI-012) | person.py, models.yaml | ✅ Done |
| Motor pin glitch (KI-013) | hardware/motors.py | ✅ Done — in2.off() before in1.on() |
| ADR-012 to ADR-018 locked | docs/DECISIONS.md | ✅ Done |
| KI-013 through KI-018 documented | docs/KNOWN_ISSUES.md | ✅ Done |

---

## Session start task
Ollama Q4_K_M quantization swap — one command, 10 min.
```bash
ollama pull llama3.2:1b-instruct-q4_K_M
# Then update config/models.yaml: llm.backends.ollama.model → llama3.2:1b-instruct-q4_K_M
# Verify: ollama run llama3.2:1b-instruct-q4_K_M "hello" — confirm it responds
```

Then fix KI-014 and KI-015 in the same session (both timeout patterns,
30 min combined):
- cognition/mind.py ~line 423: asyncio.wait_for(..., timeout=15.0)
- expression/speech.py ~line 80: timeout=10.0 + kill + finally reset

KI-016 and KI-017 are next session after that.

Observe Cosmo with someone in frame after fixes — first real YOLO session.
Watch: gesture gate, proactive speech trigger rate, face recognition handoffs.

---

## KI-016 Fix Note (when you get to it)

Use `aiosqlite` — not `asyncio.Lock()` bolted onto sync calls. Python 3.13,
async-first throughout, no executor overhead. Migrate `core/memory/episodic.py`
to aiosqlite properly rather than patching around the sync sqlite3 connection.

---

## Top Priority After Above: Wire OLED Eyes

1. Connect both SSD1306s to I2C (SDA=GPIO2/Pin3, SCL=GPIO3/Pin5, VCC=3.3V, GND)
2. Bridge A0 pad on right eye board (solder blob → VCC) to change address to 0x3D
3. Run `sudo i2cdetect -y 1` → verify both 0x3C and 0x3D appear
4. In `expression/eyes.py` change render target from `"terminal"` to `"oled"`
5. `pm2 restart cosmo` → verify eyes render on hardware

Then enable sensors one at a time:
1. BH1750 (I2C 0x23): change `available: false → true` in hardware.yaml, restart, verify `sensor.bh1750` log
2. PIR (GPIO8): change `available: false → true`, restart, wave hand, verify `MOTION_DETECTED` event
3. Touch × 4: change `available: false → true`, restart, touch each pad, verify `TOUCH_DETECTED` event
4. MPU-6050: enable last — test pickup detection → motor off + surprised eyes

---

## Disk Warning
Disk is at 90% (5.8GB free). Next time it hits 93%+, check:
- `/home/pi/.robot/logs/cosmo-error.log` (ALSA noise fills this fast)
- `/home/pi/downloads/` (414MB of unknown files)
