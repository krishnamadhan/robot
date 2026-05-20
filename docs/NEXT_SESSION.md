# NEXT_SESSION.md — Task Handoff

> This file contains the focused task for the next Claude Code session.
> Replace entirely at end of each session with the new top priority.
> Last updated: 2026-05-20 (2nd session)

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

## Code State (after 2026-05-20 2nd session)

All critical brain bugs are now fixed. Cosmo should be proactively speaking:

| Bug | Fix | File |
|-----|-----|------|
| Victory gesture spam | threshold 0.92, 4-frame hold, person-gating | gesture.py |
| tts NameError in mind.py | Added `from expression.speech import tts` | mind.py |
| Proactive speech never fired | Added `_subscribe_events()` with bus.on() | mind.py |
| Race condition in face handler | `_approaching` flag + try/finally | cosmo_demo.py |
| BT sounds play over TTS | `audio_speaking` blackboard flag | behavior_tree.py |
| Motor trim hardcoded | Moved to hardware.yaml | motors.py |
| State machine unused | Wired sm.transition_to() calls | cosmo_demo.py |

**First thing to do:** `pm2 restart cosmo_demo` to pick up all changes.

---

## Top Priority: Wire OLED Eyes + Test Proactive Speech

### Step 1 — Test proactive speech (5 minutes)
After `pm2 restart cosmo_demo`, stand at ≤80cm from camera and check logs:
```bash
pm2 logs cosmo_demo -f | grep -E "cosmo_mind|spoke|trigger"
```
Expected: `cosmo_mind.speak_trigger` log then `cosmo_mind.spoke` with 1-sentence text.

### Step 2 — Wire OLED eyes (biggest visual impact, hardware on desk)
1. Connect both SSD1306s to I2C (SDA=GPIO2/Pin3, SCL=GPIO3/Pin5, VCC=3.3V, GND)
2. Bridge A0 pad on right eye board (solder blob → VCC) to change address to 0x3D
3. Run `sudo i2cdetect -y 1` → verify both 0x3C and 0x3D appear
4. In `expression/eyes.py` change render target from `"terminal"` to `"oled"`
5. `pm2 restart cosmo` → verify eyes render on hardware

### Step 3 — Enable sensors one at a time (after eyes working)
1. BH1750 (I2C 0x23): change `available: false → true` in hardware.yaml, restart, verify `sensor.bh1750` log
2. PIR (GPIO8): change `available: false → true`, restart, wave hand, verify `MOTION_DETECTED` event
3. Touch × 4: change `available: false → true`, restart, touch each pad, verify `TOUCH_DETECTED` event
4. MPU-6050: enable last — test pickup detection → motor off + surprised eyes

---

## Disk Warning
Disk is at 90% (5.8GB free). Next time it hits 93%+, check:
- `/home/pi/.robot/logs/cosmo-error.log` (ALSA noise fills this fast)
- `/home/pi/downloads/` (414MB of unknown files)
