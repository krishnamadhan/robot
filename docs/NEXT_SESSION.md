# NEXT_SESSION.md — Task Handoff

> This file contains the focused task for the next Claude Code session.
> Replace entirely at end of each session with the new top priority.
> Last updated: 2026-05-21 (4th session — Phase 2 complete)

---

## Current Hardware State (as of 2026-05-21)

**Connected and working:**
- Camera: Logitech C920 (USB, /dev/video0) — YOLO11n @ 10.1 FPS confirmed
- Speaker: JBL Flip 5 Bluetooth (28:FA:19:C1:73:F8) — paired and working via PipeWire
- Mic: C920 USB mic — `HD Pro Webcam C920: USB Audio (hw:3,0)` confirmed in logs
- UPS HAT: battery at ~94%, I2C 0x36 active
- Wake word: OpenWakeWord "hey_jarvis" — confirmed `oww.loaded` in logs
- Full audio pipeline: STT → Claude Haiku → Piper TTS → JBL — confirmed working

**Not yet wired:**
- OLED eyes (0x3C + 0x3D) — hardware on desk
- PIR, touch ×4, MPU-6050, BH1750 — wired but not enabled in hardware.yaml
- XT60 pigtail for LiPo → motors — on order

**Required before next PIR enable:**
- Comment out `dtoverlay=i2c-gpio,bus=4,i2c_gpio_sda=8,i2c_gpio_scl=9` in `/boot/firmware/config.txt`
- Reboot (KI-024 — GPIO8/I2C bus 4 conflict)

---

## Session start task: Ollama Q4_K_M + first live test

```bash
ollama pull llama3.2:1b-instruct-q4_K_M
# Then update config/models.yaml: llm.backends.ollama.model → llama3.2:1b-instruct-q4_K_M
# Verify: ollama run llama3.2:1b-instruct-q4_K_M "hello"
```

Then do a live test session with someone in frame. Watch:
- Proactive speech trigger rate (face_seen, emotion triggers firing)
- Face recognition handoffs (should be clean after Phase 2 fixes)
- Check `pm2 logs cosmo --lines 30` for any new errors from Phase 2 code

---

## Next priorities after live test

### 1. Wire OLED Eyes (biggest visual impact — hardware on desk)
1. Connect both SSD1306s to I2C (SDA=GPIO2/Pin3, SCL=GPIO3/Pin5, VCC=3.3V, GND)
2. Bridge A0 pad on right eye board (solder blob → VCC) for address 0x3D
3. `sudo i2cdetect -y 1` → verify 0x3C and 0x3D appear
4. In `expression/eyes.py` change render target `"terminal"` → `"oled"`
5. `pm2 restart cosmo` → verify eyes render on hardware
6. Fix KI-019 (I2C mutex) BEFORE enabling OLED + sensors simultaneously

### 2. Prompt caching (ADR-018) — API cost cut ~40%
Implement ephemeral cache prefix in `cognition/mind.py`:
```python
# In _build_rich_system_prompt() — return tuple (static_prefix, dynamic_suffix)
# In _maybe_speak() — pass system as list with cache_control block
system=[
    {"type": "text", "text": static_prefix,
     "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": dynamic_suffix},
]
```

### 3. KI-016 — aiosqlite migration (episodic memory)
Use `aiosqlite` — not `asyncio.Lock()` bolted onto sync calls.
Migrate `core/memory/episodic.py` fully to async aiosqlite.

### 4. Enable sensors (one at a time, verify after each)
Enable order: BH1750 → touch × 3 → MPU-6050 → PIR (only after KI-024 config.txt fix)

---

## Disk warning
cosmo-err.log now receives ALSA/Jack noise. Cap it:
```bash
# Check size after 24h:
ls -lh /home/pi/.robot/logs/cosmo-err.log
# If growing fast, add logrotate rule or grep filter in ecosystem.config.js
```
