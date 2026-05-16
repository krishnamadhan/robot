Continue Cosmo robot pet development.
PURE SOFTWARE SESSION — no hardware wiring needed.

Pi SSH: pi@192.168.1.30 | pi@100.101.250.126
All code in ~/robot/
PM2: `cosmo` running — DO NOT break it
DO NOT touch: banteragent, pi-monitor, .wwebjs_auth

---

## Current State (as of 2026-05-16)

### Hardware active
- Logitech C920 webcam (camera + C920 mic on USB) ✅
- INMP441 I2S mic — ACTIVE (hw:2,0) ✅ (better than C920)
- JBL Flip 5 Bluetooth speaker ✅
- UPS HAT (battery monitoring real) ✅
- Motors (TB6612FNG, real) ✅

### Software stack — all working
- Event bus, state machine, personality engine ✅
- Person detection: YOLOv8n ✅
- Face recognition: SFace — Madhan + Indhu enrolled ✅
- Emotion detection: DeepFace/FER ✅
- Vision loop: 10 FPS with tiered detection (face every 5th, emotion every 10th) ✅
- STT: Whisper base.en, beam_size=5, Indian English prompt ✅ (upgraded from tiny.en)
- LLM: Claude Haiku 4.5 primary (fast, ~1-2s), Ollama fallback ✅
- TTS: Piper → espeak-ng fallback → PipeWire → JBL BT ✅
- Pre-baked greeting voice lines for zero-latency greetings ✅
- Episodic memory: writes per-turn AND on session end ✅
- Memory retrieved into Claude prompts (conversation + mind tier 2) ✅
- Behavior engine: idle sounds gated by personality state + 8s cooldown ✅
- Conversation lock: no duplicate responses ✅
- Claude cooldowns: 30-180s per trigger (was flat 180s) with ±20% variation ✅
- Camera: 320×240 (was 640×480) — much faster pipeline ✅
- PM2 processes: cosmo (1.2GB), banteragent, battery-monitor, pi-monitor, pi-scheduler

### 8 Critical Fixes Applied This Session
| Fix | Before | After |
|-----|--------|-------|
| Camera resolution | 640×480 → slow | 320×240 + 10FPS tiered |
| STT | tiny.en, beam=1 | base.en, beam=5, Indian English |
| LLM primary | verified Claude Haiku | MAX_TOKENS 200→150 |
| Claude cooldowns | flat 180s | 30-180s per trigger ±20% |
| Episodic memory | write on session end only | write per-turn + session end |
| Sound engine | random bloops | gated by mood/energy/cooldown |
| GPIO6 conflict | pre-fixed (GPIO9, hardcoded True) | confirmed ✅ |
| SFace | pre-implemented | confirmed ✅ |

---

## Top Issues Remaining

### 1. Indhu face confidence low (~73-75%)
Re-enroll with better lighting:
```bash
rm -rf ~/.robot/memory/faces/Indhu/
python3 tools/enroll_face.py
# Capture 20+ frames, multiple angles, good light
```

### 2. Wake word is "hey_jarvis" not "hey_cosmo"
- Currently using OpenWakeWord `hey_jarvis` model
- Custom `hey_cosmo` at `console.picovoice.ai → Wake Word → New`
- Or try training with OpenWakeWord (needs ~500 samples)

### 3. OLED eyes not wired
- Driver fully implemented in `expression/eyes.py` — just needs physical wiring
- SSD1306 x2 at 0x3C (left) and 0x3D (right)
- Config: `eye_engine.set_render_target("oled")` to activate

### 4. HC-SR04 distance sensor mocked
- Need to wire and enable in config/hardware.yaml
- `sensors.ultrasonic.available: true`

### 5. Episodic memory not tested cross-session yet
- Per-turn episodes now stored during conversation
- Next session: have a conversation, restart cosmo, talk again — verify Cosmo references it

### 6. Response latency not measured yet
- Target: < 3s total (wake → first spoken word)
- Measure: add latency telemetry logging in audio/pipeline.py

---

## Session Goal Options (pick one)

### Option A — Polish + Test
- Walk through full verification test sequence (see below)
- Re-enroll Indhu face
- Measure end-to-end latency
- Add latency telemetry to audio pipeline

### Option B — Wiring
- Wire HC-SR04 ultrasonic sensor (enable real distance)
- Wire OLED eyes (biggest personality impact)
- Test each with tools/sensor_monitor.py

### Option C — Memory + Personality Deep Dive
- Test cross-session memory recall (conversation → restart → reference)
- Add time-aware greetings (morning/evening phrases)
- Tune proactive speech triggers (currently fires too rarely or randomly)

---

## Verification Test Sequence
```
pm2 restart cosmo && sleep 20
pm2 logs cosmo -f
```
Walk through:
1. Stand in front of camera — detected within 2s? ✅
2. Face recognized in 3-5s? ✅
3. Cosmo speaks within 3s of face seen? (pre-baked line, no LLM) ✅
4. Say "Hey Jarvis how are you" — response in < 4s? (target)
5. Say "what's my name" — does Cosmo know? (memory test)
6. Walk out, wait 3 min, walk back — does Cosmo reference you were gone?
7. Make happy expression — Cosmo reacts? (emotion detection)
8. Touch head pad — Cosmo reacts instantly? (touch sensor)
9. Stand still 5 min — no random bloops? (sound gates working)
10. Say "mind off" — Cosmo confirms and stops LLM calls?

---

## Rules
* Mock everything not physically available
* Test every module before moving to next
* Commit after every working module
* DO NOT touch banteragent, pi-monitor, .wwebjs_auth
* DO NOT restart cosmo mid-session unnecessarily
* If cosmo breaks → fix before continuing
* Every new file needs a unit test
