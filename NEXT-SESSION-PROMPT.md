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
- STT: Whisper base.en, beam_size=5, Indian English prompt ✅
- LLM: Claude Haiku 4.5 primary (streaming), Ollama fallback ✅
- TTS: Piper → espeak-ng fallback → PipeWire → JBL BT ✅
- Streaming TTS pipeline: speaks sentence 1 while synthesizing sentence 2 ✅
- Pre-baked greeting voice lines for zero-latency greetings ✅
- Episodic memory: writes per-turn AND on session end ✅
- Rich memory context: familiarity level + mood + memory block in every Claude call ✅
- Conversation continuity: 10-min thread resume per person ✅
- Curiosity engine: asks questions, brings up memories, wonders aloud ✅
- Ambient awareness loop: 30s tick, drives proactive engagement ✅
- Mood-aware sound selection (chirp_happy vs chirp_curious, whimper_sad vs purr) ✅
- Behavior engine: idle sounds gated by personality state + 8s cooldown ✅
- Conversation lock: no duplicate responses ✅
- Claude cooldowns: 30-180s per trigger ±20% ✅
- Camera: 320×240, 10 FPS tiered ✅

### 6 Autonomous Brain Upgrades Applied (U1-U2, U4, U6-U9)
| Upgrade | What Changed |
|---------|-------------|
| U1: Rich episodic memory | `get_context_for_person()` + `_build_rich_system_prompt()` in every Claude call |
| U2: Streaming TTS | `llm.generate_streaming()` + `tts.speak_streaming()` — sentence pipeline |
| U4: Curiosity engine | `_ambient_loop()` — questions by emotion/time, memory references |
| U6: Context-aware proactive | Rich system prompt + emotion appended to all trigger prompts |
| U7: Conversation continuity | Per-person thread cache, 10-min resume window |
| U8: Ambient awareness | 30s autonomous situation assessment → drives curiosity/wonder |
| U9: Mood-aware sounds | Sad→whimper_sad, happy curious→chirp_happy |

### Remaining Upgrades
| # | Upgrade | Status |
|---|---------|--------|
| U3 | Parallel vision pipeline | Skip — tiered approach adequate |
| U5 | Personality learning (trait drift) | Pending |
| U10 | Wire curiosity + ambient into cosmo_demo startup | Pending |

---

## Top Issues Remaining

### 1. Indhu face confidence low (~73-75%)
Re-enroll with better lighting:
```bash
rm -rf ~/.robot/memory/faces/Indhu/
python3 tools/enroll_face.py
```

### 2. Wake word is "hey_jarvis" not "hey_cosmo"
- OpenWakeWord `hey_jarvis` model active
- Custom `hey_cosmo` at `console.picovoice.ai → Wake Word → New`

### 3. OLED eyes not wired
- Driver fully implemented in `expression/eyes.py` — needs physical wiring
- SSD1306 x2 at 0x3C (left) and 0x3D (right)

### 4. HC-SR04 distance sensor mocked
- Enable: `sensors.ultrasonic.available: true` in config/hardware.yaml

### 5. Cross-session memory not tested yet
- Per-turn episodes stored. Have a conversation → restart → verify Cosmo references it.

### 6. Response latency not measured
- Target: < 3s total (wake → first spoken word)
- With streaming TTS: first sentence should start ~1.5-2s after wake word
- Add latency telemetry to audio/pipeline.py

### 7. U5 Personality learning not implemented
- Traits should drift based on interaction outcomes (good_interaction → +mood, +energy)
- Lives in `core/personality.py`

---

## Session Goal Options (pick one)

### Option A — Test Streaming Latency
- Measure end-to-end latency with streaming vs non-streaming
- Add latency telemetry to audio/pipeline.py
- Target: first spoken word < 2s from wake word

### Option B — U5 Personality Learning
- Implement trait drift in `core/personality.py`
- Traits: curiosity, warmth, humor, energy_base drift ±0.01 per interaction
- Store in personality_notes in episodic persons table

### Option C — Wiring
- Wire HC-SR04 ultrasonic sensor (real distance sensing)
- Wire OLED eyes (biggest personality impact)
- Re-enroll Indhu face

---

## Verification Test Sequence
```
pm2 restart cosmo && sleep 20
pm2 logs cosmo -f
```
1. Stand in front — detected within 2s?
2. Face recognized in 3-5s?
3. Cosmo greets with pre-baked line immediately?
4. Say "Hey Jarvis, what did we talk about last time?" — does it recall?
5. Wait 5 min — does curiosity engine ask a question?
6. Walk out, wait 3 min, walk back — does Cosmo reference you were gone?
7. Say something happy — does emotion trigger fire with playful response?
8. Say "Hey Jarvis, tell me about yourself" — does streaming TTS feel faster?

---

## Rules
* Mock everything not physically available
* Test every module before moving to next
* Commit after every working module
* DO NOT touch banteragent, pi-monitor, .wwebjs_auth
* DO NOT restart cosmo mid-session unnecessarily
* If cosmo breaks → fix before continuing
