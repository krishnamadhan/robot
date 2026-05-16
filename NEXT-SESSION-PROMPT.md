Continue Cosmo robot pet development.
PURE SOFTWARE SESSION — no hardware wiring needed.

Pi SSH: pi@192.168.1.30 | pi@100.101.250.126
All code in ~/robot/
PM2: `cosmo` running — DO NOT break it
DO NOT touch: banteragent, pi-monitor, .wwebjs_auth

---

## Current State (as of 2026-05-17)

### All 10 Upgrades — DONE ✅

| Upgrade | Status | Notes |
|---------|--------|-------|
| U1 Rich episodic memory | ✅ | `get_context_for_person()` + rich system prompt every call |
| U2 Streaming TTS | ✅ | `speak_streaming()` pipelines Piper+play in parallel |
| U3 Parallel vision | ✅ | 15/5/2 FPS independent loops, no blocking |
| U4 Curiosity engine | ✅ | 30s ambient loop, questions by emotion+time |
| U5 Personality trait drift | ✅ | 0.005/interaction, persisted to personality_traits.json |
| U6 Context-aware proactive | ✅ | Rich prompt + emotion in every trigger |
| U7 Conversation continuity | ✅ | 10-min per-person thread cache |
| U8 Ambient awareness | ✅ | Drives curiosity, memory refs, wonder-aloud |
| U9 Mood-aware sounds | ✅ | whimper_sad / chirp_happy by mood |
| Latency telemetry | ✅ | GET /health, GET /latency, 4 pipeline stages |

### Hardware active
- Logitech C920 webcam (320×240 @ 30fps) ✅
- INMP441 I2S mic (hw:2,0) ✅
- JBL Flip 5 Bluetooth speaker ✅
- UPS HAT (battery monitoring) ✅
- Motors (TB6612FNG) ✅

---

## What's Left

### Physical wiring (biggest visual impact, zero software needed)

**OLED eyes** — driver fully implemented, just needs wires:
```
Both OLEDs:  VCC→Pin1, GND→Pin6, SDA→Pin3, SCL→Pin5
Right eye:   bridge A0 pad on back → changes 0x3C → 0x3D
Verify: sudo i2cdetect -y 1   (must show 0x3C AND 0x3D)
Enable: eye_engine.set_render_target("oled")
```

**HC-SR04 ultrasonic** — enable in config/hardware.yaml:
```yaml
sensors.ultrasonic.available: true
```

**Real sensors** (PIR, touch, IMU) — enable one by one in hardware.yaml, test each with pm2 logs.

### Cross-session memory test (5 min, just need to run it)
```bash
pm2 restart cosmo
# Have a conversation, mention something specific
# Restart again
pm2 restart cosmo
# Ask "what did we talk about?" — should reference it
```

### Latency baseline measurement (diagnostic)
```bash
curl http://localhost:8000/latency
# After a few conversations:
# stt avg should be ~800-1200ms
# llm_to_first_audio avg should be ~500-900ms (streaming)
# wake_to_speech total should be ~2-3s
```

### Indhu face re-enrollment (low confidence ~73%)
```bash
rm -rf ~/.robot/memory/faces/Indhu/
python3 tools/enroll_face.py
# 20+ frames, good lighting, multiple angles
```

### Wake word (low priority)
- Currently "hey_jarvis" (OpenWakeWord)
- Custom "hey_cosmo" needs recording at console.picovoice.ai

---

## Session Goal Options

### Option A — Physical Wiring (biggest wow factor)
1. Wire OLED eyes — see actual eye expressions on hardware
2. Wire HC-SR04 — real obstacle avoidance
3. Verify latency baseline from /latency endpoint

### Option B — Memory + Latency Audit
1. Cross-session memory test (conversation → restart → recall)
2. Check latency numbers from /latency endpoint
3. Re-enroll Indhu face

### Option C — New Features
- Add "good morning" / "good night" time-aware greeting with personality memory
- Add follow-person motor behavior (track bbox_center_x → turn)
- Add scheduled daily summary via personality_learning.report()

---

## Verification Test Sequence
```bash
pm2 restart cosmo && sleep 20
curl http://localhost:8000/health   # check mood, energy, latency snapshot
pm2 logs cosmo -f
```
Then live:
1. Walk in → detected <2s, greeted immediately (pre-baked)
2. Say "Hey Jarvis, how are you" → streaming response starts in <2s
3. Check logs for: `latency stage=wake_to_speech ms=XXXX`
4. Say something, leave, come back in 5 min → thread resumes
5. Leave for 10 min → curiosity engine fires
6. Check /latency endpoint for stage breakdown

---

## Key Files
- `perception/vision/vision_loop.py` — parallel 3-loop pipeline
- `core/personality.py` — PersonalityEngine + PersonalityLearning
- `utils/telemetry.py` — LatencyTracker (start/end/report)
- `perception/audio/pipeline.py` — latency markers wired in
- `cognition/conversation.py` — streaming TTS + thread continuity
- `behavior/engine.py` — ambient loop + curiosity engine
- `cognition/mind.py` — rich system prompt for all Claude calls

## Rules
* Mock everything not physically available
* DO NOT touch: banteragent, pi-monitor, .wwebjs_auth
* DO NOT restart cosmo mid-session unnecessarily
* If cosmo breaks → fix before continuing
* Commit after every working change
