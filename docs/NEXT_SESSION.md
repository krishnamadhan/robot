# NEXT_SESSION.md — Task Handoff

> This file contains the focused task for the next Claude Code session.
> Replace entirely at end of each session with the new top priority.
> Last updated: 2026-05-28 (Codex collaboration session)

---

## Current Hardware State (as of 2026-05-22)

**Connected and working:**
- Camera: Logitech C920 (USB, /dev/video0) — YOLO11n @ 10.1 FPS confirmed
- Speaker: JBL Flip 5 Bluetooth (28:FA:19:C1:73:F8) — paired and working via PipeWire
- Mic: C920 USB mic — `HD Pro Webcam C920: USB Audio (hw:3,0)` confirmed in logs
- UPS HAT: battery ~94%, I2C 0x36 active
- Wake word: OpenWakeWord "hey_jarvis" — confirmed working
- Full audio pipeline: STT → Claude Haiku → Piper TTS → JBL — confirmed working
- Ollama fallback: llama3.2:1b-instruct-q4_K_M (807MB, smoke-tested)

**Not yet wired:**
- OLED eyes (0x3C + 0x3D) — hardware on desk, highest priority
- PIR, touch ×3, MPU-6050, BH1750 — wired but not enabled in hardware.yaml
- XT60 pigtail for LiPo → motors — on order

**Pending reboot (KI-024):**
- `/boot/firmware/config.txt` i2c-gpio overlay already commented out (2026-05-22)
- Reboot will: (1) apply KI-024 fix, (2) clear stale GPIO27 pin claim → motors.real_4wd will persist
- Safe to reboot — banteragent will auto-restart via PM2

---

## Session start: Test Pet Brain + Wire OLED Eyes

### First: Test PetBrain (5 min, no hardware needed)
```bash
cd ~/robot && python3 tools/pet_brain_test.py
```
Test all state transitions: toggle person (p), change energy (+/-), toggle dark (l), close obstacle (o).
Verify FLEE immediately overrides other states.
Verify wander weight increases when curiosity boosted (c key).

### Then: Wire OLED Eyes (biggest visual impact — hardware on desk)

**Before starting:** `sudo i2cdetect -y 1` — verify baseline, should see 0x36 (UPS)

**Wiring (both SSD1306s to I2C bus 1):**
```
VCC → Pi 3.3V (Pin 1)
GND → Pi GND (Pin 6)
SDA → GPIO2 / Pin 3
SCL → GPIO3 / Pin 5
Left eye  addr 0x3C — A0 pad floating (default)
Right eye addr 0x3D — bridge A0 pad to VCC (solder blob or 10kΩ to 3.3V)
```

**After wiring:**
```bash
sudo i2cdetect -y 1   # Must show 0x3C AND 0x3D
```

**Software (fix KI-019 I2C mutex FIRST, then switch render target):**

KI-019 is the I2C bus contention issue — multiple drivers hitting the bus simultaneously
without locking. Read `hardware/sensor_manager.py` and `expression/eyes.py` to understand
current I2C access patterns, then add an asyncio.Lock() at the hardware layer before enabling
both OLED + sensors simultaneously.

After KI-019 fix:
```python
# In expression/eyes.py — change:
eye_engine.set_render_target("terminal")
# to:
eye_engine.set_render_target("oled")
```

```bash
pm2 restart cosmo && pm2 logs cosmo --lines 30 --nostream | grep eyes
```

Expected: eyes render on hardware, expressions visible (happy/sleepy/alert etc.)

---

## After OLED: Enable sensors (one at a time)

**Order:** BH1750 → touch × 3 → MPU-6050 → PIR (PIR requires reboot first for KI-024)

For each sensor:
1. Set `available: true` in `config/hardware.yaml`
2. `pm2 restart cosmo`
3. Watch logs: `pm2 logs cosmo --lines 20 --nostream | grep sensor`
4. Verify event fires on hardware trigger
5. Only then move to next sensor

---

## Stack status (all upgrades complete or deferred)

| Component | Status |
|---|---|
| YOLO11n person detection | ✅ Complete (ADR-012) |
| SFace face recognition | ✅ Locked (ADR-013) |
| Ollama Q4_K_M | ✅ Complete (ADR-017, 2026-05-22) |
| Prompt caching | ❌ Deferred — 281 tokens < 2048 minimum (ADR-018) |
| Custom wake word | 📅 Phase 4 (ADR-015) |
| TTS swap (Kitten) | 📅 Quality test first (ADR-016) |

---

## Disk warning

Check before starting:
```bash
df -h / | tail -1
ls -lh /home/pi/.robot/logs/
```
Was at 90% on 2026-05-20. If >93%, check cosmo-err.log size.
