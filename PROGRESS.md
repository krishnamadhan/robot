# Cosmo — Build Progress Tracker

> Last updated: 2026-05-14
> Test suite: **78/78 passing** (47 new)
> Demo: `python3 tools/cosmo_demo.py` — PM2 as `cosmo` — ALL systems running

---

## ORIGINAL VISION

AI companion robot for Madhan & Indhu, Bangalore. Feel ALIVE — ambient, reactive, emotionally aware. Mix of pet + therapist + playful companion + ambient home presence. Never feel like a voice assistant.

Hardware: Raspberry Pi 5 8GB, Logitech C920 webcam, JBL Flip 5 BT speaker, DFRobot UPS HAT. Many sensors arriving.

---

## WHAT HAS BEEN BUILT

### PHASE 0 — ARCHITECTURE + FOUNDATION ✅ COMPLETE

| Item | Status | File | Notes |
|------|--------|------|-------|
| ARCHITECTURE.md | ✅ | `ARCHITECTURE.md` | Full system diagram, data flow, design decisions |
| ROADMAP.md | ✅ | `ROADMAP.md` | All phases + hardware arrival checklist |
| Directory structure | ✅ | all dirs | `__init__.py` + READMEs in every folder |
| Config system | ✅ | `utils/config.py` | YAML + Pydantic validation |
| Structured logging | ✅ | `utils/logger.py` | structlog, rotating files, console |
| Telemetry | ✅ | `utils/telemetry.py` | Prometheus-style counters |
| Async event bus | ✅ | `core/event_bus.py` | Priority queues, history, dead letter queue |
| Hardware abstraction | ✅ | `hardware/base.py` | ABC interface + HardwareStatus enum |
| Mock hardware system | ✅ | `hardware/mock.py` | Gaussian noise, latency simulation, failure injection |
| State machine | ✅ | `core/state_machine.py` | 30 states, hierarchical, timeout transitions, full audit log |
| Personality engine | ✅ | `core/personality.py` | Continuous emotional state, decay, person tracking, quirks |
| Working memory | ✅ | `core/memory/working.py` | Deque-based, last 5 minutes of events |
| Episodic memory | ✅ | `core/memory/episodic.py` | SQLite at `~/.robot/memory/episodic.db`, retrieval by person/emotion/time |
| Spatial memory | ✅ | `core/memory/spatial.py` | Room fingerprints JSON at `~/.robot/memory/spatial.json` |
| Camera pipeline | ✅ | `perception/vision/camera.py` | Async frame buffer, Logitech C920 USB, health monitoring |
| Person detector | ✅ | `perception/vision/person.py` | YOLOv8n primary, HOG fallback, multi-person tracking |
| Sensor monitor tool | ✅ | `tools/sensor_monitor.py` | Rich live dashboard, all state visible |
| main.py | ✅ | `main.py` | Full startup/shutdown lifecycle, PM2 ready |
| Config files | ✅ | `config/*.yaml` | `hardware.yaml`, `personality.yaml`, `thresholds.yaml`, `models.yaml` |
| requirements.txt | ✅ | `requirements.txt` | All dependencies pinned |
| Unit tests | ✅ | `tests/unit/` | 31 tests passing — event bus, personality, state machine |

---

### PHASE 1 — CAMERA + PERCEPTION ✅ COMPLETE (beyond original scope)

| Item | Status | File | Notes |
|------|--------|------|-------|
| Face detection | ✅ | `perception/vision/face.py` | OpenCV DNN cascade + SFace ONNX embeddings |
| Face enrollment | ✅ | `tools/enroll_face.py` | `--headless` SSH-friendly, 10 samples, quality checks |
| Face recognition | ✅ | `perception/vision/face.py` | SFace 128-dim cosine similarity, threshold 0.363 |
| Enrolled persons | ✅ | `~/.robot/faces/` | **Madhan** (82–97% conf), **Indhu** (75% conf) |
| Emotion detection | ✅ | `perception/vision/emotion.py` | DeepFace/FER, 7 emotions, smoothed output |
| Vision loop | ✅ | `perception/vision/vision_loop.py` | 3 FPS pipeline: detect → recognize → emotion |
| Emotion→personality | ✅ | `perception/vision/vision_loop.py` | Mood contagion, comfort triggers, arousal updates |
| Room fingerprinting | ✅ | `tools/room_fingerprint.py`, `tools/detect_room.py` | Light+camera-based room ID |

**Face recognition architecture:**
- Input: BGR frame from C920
- Detection: OpenCV Haar cascade (haarcascade_frontalface_alt2.xml)
- Embedding: SFace ONNX model (112×112 BGR input → 128-dim vector)
- Matching: cosine similarity, threshold 0.363 for known/unknown
- Storage: `~/.robot/faces/model.yml` (LBPH legacy) + `~/.robot/faces/embeddings.pkl`

---

### PHASE 2 — PERSONALITY + BRAIN ✅ COMPLETE

| Item | Status | File | Notes |
|------|--------|------|-------|
| LLM interface | ✅ | `cognition/llm.py` | Ollama primary (llama3.2:1b, 90s timeout, keep_alive=1h), Claude fallback |
| Conversation manager | ✅ | `cognition/conversation.py` | Multi-turn, person-aware, emotion-aware |
| Cosmo system prompt | ✅ | `cognition/llm.py` | Tanglish style, mood/energy injection, memory retrieval |

**LLM chain:**
1. Ollama + llama3.2:1b (offline, ~5s warm, ~51s cold start)
2. Claude Haiku (cloud fallback — needs `ANTHROPIC_API_KEY`)
3. Hardcoded fallback: "Hmm... my brain's a bit fuzzy right now da."

**Known issue:** Cold start is 51s. Fix: `keep_alive: 1h` keeps model warm after first load. The warmup task runs in background at startup — first greeting will fire ~65s after launch.

---

### PHASE 4 — VOICE + AUDIO ✅ COMPLETE (built before Phase 3)

Built before Phase 3 because speaker hardware arrived (JBL Flip 5 BT).

| Item | Status | File | Notes |
|------|--------|------|-------|
| Microphone input | ✅ | `perception/audio/mic.py` | PyAudio, Logitech C920 (stereo→mono downmix), 16kHz |
| Voice activity detection | ✅ | `perception/audio/vad.py` | webrtcvad aggressiveness=2, 30ms frames, 960 bytes |
| Wake word detection | ✅ | `perception/audio/wake_word.py` | STT-based ("hey cosmo", "cosmo", "hey robot") — OpenWakeWord API incompatible |
| Speech to text | ✅ | `perception/audio/stt.py` | faster-whisper tiny.en, CPU int8, ~1.5s latency |
| Full listen pipeline | ✅ | `perception/audio/pipeline.py` | passive→wake→VAD→STT→LLM→TTS loop |
| TTS — espeak | ✅ | `expression/speech.py` | Fallback, robotic but always available |
| TTS — Piper | ✅ | `expression/speech.py` | **Primary**, neural voice, en_US-lessac-medium (61MB) |
| Sound expressions | ✅ | `expression/speech.py` | boot_chime, beep_ack, chirp_curious/happy, whimper_sad, purr_content, happy_trill — all numpy generated |
| BT speaker | ✅ | System config | JBL Flip 5, `bluez_output.28_FA_19_C1_73_F8.1`, routed via `pw-play --target @DEFAULT_SINK@` |

**Audio stack:**
- PipeWire + WirePlumber (not PulseAudio, not bluealsa — bluealsa disabled)
- `pw-play --target "@DEFAULT_SINK@"` correctly acquires A2DP transport
- Piper: 1.8s generation, `en_US-lessac-medium.onnx` at `~/.robot/models/piper/`
- Mic: C920 USB, channels=2, downmixed to mono for VAD/STT

**Wake word issue:** OpenWakeWord binary API is incompatible (`AudioFeatures.__init__()` error). STT-based keyword detection works as substitute. Needs proper OWW fix or Porcupine later.

---

### INTEGRATION DEMO ✅ RUNNING

| Item | Status | File | Notes |
|------|--------|------|-------|
| Full demo | ✅ | `tools/cosmo_demo.py` | All subsystems wired, Rich live dashboard |
| Face greeting | ✅ | `tools/cosmo_demo.py` | Face recognized → LLM greeting → Piper TTS on JBL |
| Voice conversation | ✅ | `tools/cosmo_demo.py` | "Hey Cosmo" → STT → LLM → TTS |
| Emotion display | ✅ | `tools/cosmo_demo.py` | Detected emotion shown in dashboard |
| Audio test tool | ✅ | `tools/cosmo_audio_test.py` | Standalone audio pipeline test |

**Verified end-to-end:**
- Madhan recognized at 82–97% confidence
- Greeting: *"Anna me! Aiyoh, big smile on face today! How's it goin' da?"*
- Piper voice confirmed playing on JBL Flip 5 via BT
- Ollama llama3.2:1b generating Tanglish responses

---

---

### SESSION 3 — ALL REMAINING SOFTWARE (2026-05-14) ✅ COMPLETE

| Item | Status | File | Notes |
|------|--------|------|-------|
| Sensor manager | ✅ | `hardware/sensor_manager.py` | All 8 sensors, mock fallback, event bus wiring |
| Motor controller | ✅ | `hardware/motors.py` | TB6612FNG, mock mode, watchdog, safety events |
| Servo controller | ✅ | `hardware/servos.py` | PCA9685, mock mode, person tracking algo |
| Eye engine | ✅ | `expression/eyes.py` | 12 expressions, 30FPS, blinking, terminal renderer |
| Eye simulator | ✅ | `tools/eye_simulator.py` | Live terminal, keyboard control, auto-cycle |
| Sound engine | ✅ | `expression/sounds.py` | 17 sounds, all numpy-generated, paplay output |
| Intent parser | ✅ | `cognition/intent.py` | 14 intents, Tamil+English, event bus publish |
| Navigation engine | ✅ | `behavior/navigation.py` | Safety stack, wander, approach, follow mode |
| Behavior engine | ✅ | `behavior/engine.py` | 6 idle behaviors + 5 proactive Tanglish triggers |
| Memory browser | ✅ | `tools/memory_browser.py` | Browse/query episodic memory SQLite |
| Personality tuner | ✅ | `tools/personality_tuner.py` | Live mood adjust + event injection |
| Integration wired | ✅ | `tools/cosmo_demo.py` | All systems wired into single demo |
| Intent in pipeline | ✅ | `perception/audio/pipeline.py` | Intent checked before LLM on every utterance |
| 47 new tests | ✅ | `tests/unit/test_new_systems.py` | 78 total passing |

**Sensors ON real hardware (no code needed):**
- TouchSensorArray: GPIO 5/25/6/12 — REAL ✅
- UPSHATSensor: I2C 0x36 — REAL ✅

**Sensors mocked (wire when hardware arrives):**
- BH1750 light, PIR motion, APDS9960 gesture, MPU6050 IMU
- Cliff sensors, HC-SR04 ultrasonic

**When hardware arrives:** Set `available: true` in `config/hardware.yaml` — zero code changes.

---

## WHAT IS NOT BUILT YET

### PHASE 3 — MOVEMENT + NAVIGATION ⚠️ SOFTWARE READY, HARDWARE PENDING

Waiting on hardware: HC-SR04, VL53L0X ToF, TCRT5000 cliff sensors, MPU-6050, PCA9685 servos.

| Item | Status | File | Notes |
|------|--------|------|-------|
| Motor real driver | ❌ | `hardware/motors.py` | TB6612FNG wired, test scripts exist but no production driver |
| Ultrasonic driver | ❌ | `hardware/sensors/ultrasonic.py` | Hardware arriving |
| ToF distance driver | ❌ | `hardware/sensors/tof.py` | Hardware arriving |
| Cliff sensor driver | ❌ | `hardware/sensors/cliff.py` | Hardware arriving |
| IMU driver | ❌ | `hardware/sensors/imu.py` | Hardware arriving |
| Servo controller | ❌ | `hardware/servos.py` | PCA9685 arriving |
| Safety constraints | ⚠️ | `core/safety.py` | Defined in architecture but not enforced in real-time |
| Navigation engine | ❌ | `behavior/navigation.py` | Empty dir |
| Behavior engine | ❌ | `behavior/engine.py` | Empty dir — only `__init__.py` |
| Attention system | ❌ | `behavior/attention.py` | Not started |
| Idle behavior gen | ❌ | `behavior/idle.py` | Not started |
| Occupancy grid | ❌ | `mapping/grid.py` | Empty dir |

Test scripts that exist (pre-production, not wired to main system):
- `test_motors.py`, `calibrate_motors.py`, `avoid.py`, `manual_drive.py`, `keyboard_control.py`
- `mpu6050_live.py`, `hcsr04_live.py`, `hcsr04_test.py`

---

### PHASE 1 — PARTIALLY MISSING

| Item | Status | File | Notes |
|------|--------|------|-------|
| OLED eye animations | ❌ | `hardware/display/oled.py` | Hardware arriving — OLEDs not yet present |
| Eye expression engine | ❌ | `expression/eyes.py` | Empty, pending OLED arrival |
| BH1750 real driver | ⚠️ | — | Tested standalone (`bh1750_live.py`), not wired to main system |
| PIR real driver | ⚠️ | — | Tested standalone (`pir_live.py`), not wired to main system |
| Touch sensor driver | ⚠️ | — | Tested standalone (`test_touch.py`), not wired |
| APDS-9960 gesture | ⚠️ | — | Tested standalone (`apds9960_live.py`), not wired |
| MPU-6050 | ⚠️ | — | Tested standalone (`mpu6050_live.py`), not wired |

---

### PHASE 2 — PARTIALLY MISSING

| Item | Status | File | Notes |
|------|--------|------|-------|
| Intent parser | ❌ | `cognition/intent.py` | Not started |
| Idle behavior generator | ❌ | `behavior/idle.py` | Not started |
| Routine learning | ❌ | `behavior/routines.py` | Not started |
| Memory browser tool | ❌ | `tools/memory_browser.py` | Not started |
| Personality tuner tool | ❌ | `tools/personality_tuner.py` | Not started |

---

### PHASE 4 — GAPS

| Item | Status | Notes |
|------|--------|-------|
| OpenWakeWord | ❌ | API incompatible — needs fix or replace with Porcupine |
| Audio environment analysis | ❌ | `perception/audio/audio_analysis.py` — not started |
| INMP441 I2S mic | ❌ | Using C920 USB mic instead — INMP441 arriving |
| PAM8403 + 3W speaker | ❌ | Using JBL BT instead — PAM8403 arriving |

---

### PHASE 5 — NOT STARTED

| Item | Status | Notes |
|------|--------|-------|
| Full room identification | ⚠️ | Prototype exists (`tools/room_fingerprint.py`, `tools/detect_room.py`) but not integrated |
| Landmark detection | ❌ | Not started |
| WiFi RSSI fingerprinting | ❌ | Not started |
| Routine learning | ❌ | Not started |
| Autonomous exploration | ❌ | Blocked on Phase 3 |
| Docking behavior | ❌ | Far future |

---

## KNOWN BUGS / TECH DEBT

| # | Severity | Description | Fix |
|---|----------|-------------|-----|
| 1 | HIGH | OpenWakeWord API incompatible | Replace with Porcupine or fix OWW version |
| 2 | MED | Ollama cold start is 51s — first greeting delayed | `keep_alive: 1h` in all calls; consider PM2 pre-warmup script |
| 3 | MED | Indhu face confidence only 75% | Re-enroll with more samples, varied lighting/angles |
| 4 | MED | Sensors (BH1750, PIR, touch, APDS, MPU6050) tested standalone but not wired to event bus | Build sensor drivers + wire to core |
| 5 | LOW | `expression/eyes.py` stub — no eye animations until OLED arrives | Build simulator first using `tools/eye_simulator.py` |
| 6 | LOW | `behavior/` directory is empty — no behavior tree | Build idle behaviors as first behavior module |
| 7 | LOW | No Anthropic API key on Pi — cloud LLM fallback non-functional | Add to environment or use `.env` file |
| 8 | LOW | STT occasionally transcribes background noise ("I'm right, me", "We'll be in the final spot") | Tune energy threshold or add semantic filter |
| 9 | LOW | cosmo_demo.py must be manually restarted after crash | Add to PM2 ecosystem.config.js |

---

## HARDWARE STATUS

| Component | Status | Notes |
|-----------|--------|-------|
| Raspberry Pi 5 8GB | ✅ Running | Main compute |
| Logitech C920 webcam | ✅ Active | `/dev/video0`, 640×480 |
| JBL Flip 5 BT speaker | ✅ Connected | `28:FA:19:C1:73:F8`, A2DP via PipeWire |
| DFRobot UPS HAT | ✅ Monitored | `battery_monitor.py` via PM2 |
| BH1750 light sensor | ✅ Wired | I2C 0x23, tested, not in main system yet |
| TB6612FNG + TT motors | ✅ Wired | Test scripts work, no production driver |
| 2WD chassis | ✅ Built | Basic movement tested manually |
| MPU-6050 IMU | ✅ Wired | Tested standalone, not in main system |
| APDS-9960 gesture | ✅ Wired | Tested standalone, not in main system |
| TTP223 touch sensors | ✅ Wired | Tested standalone, not in main system |
| HC-SR501 PIR | ✅ Wired | Tested standalone, not in main system |
| OLED displays ×2 | ❌ Arriving | SSD1306 I2C — eye animations blocked on this |
| HC-SR04 ultrasonic | ❌ Arriving | Obstacle avoidance blocked |
| VL53L0X ToF ×3 | ❌ Arriving | Precision distance blocked |
| TCRT5000 cliff ×2 | ❌ Arriving | Cliff detection blocked |
| PCA9685 servo driver | ❌ Arriving | Pan/tilt camera blocked |
| MG90S servos ×3 | ❌ Arriving | Camera pan/tilt + ultrasonic rotate |
| INMP441 I2S mic | ❌ Arriving | Will replace C920 USB mic |
| PAM8403 + 3W speaker | ❌ Arriving | Will replace JBL BT |
| 5-ch IR line module | ❌ Arriving | Line following blocked |

---

## AI MODELS INSTALLED

| Model | Path | Purpose | Size |
|-------|------|---------|------|
| YOLOv8n | `/home/pi/robot/yolov8n.pt` | Person detection | 6MB |
| Whisper tiny.en | `~/.cache/huggingface/` | Speech to text | ~75MB |
| faster-whisper tiny.en | `~/.cache/huggingface/` | STT (quantized) | ~39MB |
| SFace ONNX | Auto-downloaded by OpenCV | Face embedding | ~85MB |
| Piper en_US-lessac-medium | `~/.robot/models/piper/` | TTS neural voice | 61MB |
| Ollama llama3.2:1b | `~/.ollama/models/` | Local LLM | ~1.3GB |
| DeepFace/FER models | `~/.deepface/` | Emotion detection | ~35MB |

---

## SYSTEM ARCHITECTURE (QUICK REF)

```
[C920 Camera] ──→ [camera.py] ──→ [person.py] ──→ [vision_loop.py]
                                        │                  │
                                   [face.py]          [emotion.py]
                                        │                  │
                                   [event_bus] ←──────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    ↓                   ↓                   ↓
              [personality.py]   [conversation.py]   [state_machine.py]
                    │                   │
                    │              [llm.py] ──→ Ollama → Claude → fallback
                    │                   │
                    └───────────────────→ [speech.py] ──→ Piper → espeak
                                                │
                                         [pw-play] ──→ [JBL Flip 5 BT]

[C920 Mic] ──→ [mic.py] ──→ [vad.py] ──→ [wake_word.py]
                                                │
                                          [pipeline.py]
                                                │
                                          [stt.py] (Whisper)
                                                │
                                          [conversation.py]
                                                │
                                          [speech.py] → JBL
```

---

## PM2 SERVICES (DO NOT TOUCH)

| Service | PID owner | Purpose |
|---------|-----------|---------|
| banteragent | PM2 | WhatsApp Tanglish bot — PRODUCTION |
| pi-monitor | PM2 | Pi health monitoring |
| battery_monitor | PM2 | UPS HAT battery tracking |
| cosmo_demo | Manual | Main robot demo — not yet in PM2 |

---

## IMMEDIATE NEXT STEPS (Priority Order)

1. **Fix OpenWakeWord** — replace with Porcupine offline or fix OWW version
2. **Wire sensors to event bus** — BH1750, PIR, touch, MPU-6050, APDS-9960 → fire events
3. **Add cosmo_demo to PM2** — auto-restart on crash
4. **Build `behavior/idle.py`** — 10 idle behaviors (look around, blink, sounds, wander)
5. **Build `behavior/attention.py`** — saliency-based attention + servo follow (when servo arrives)
6. **OLED eye simulator** — build full expression engine in simulator before hardware arrives
7. **Build `cognition/intent.py`** — parse "come here", "go away", "follow me" from STT
8. **Build `tools/memory_browser.py`** — browse Cosmo's episodic memories
9. **Build `tools/personality_tuner.py`** — live personality adjustment
10. **Add Phase 3 real drivers** — when hardware (HC-SR04, ToF, cliff, IMU, servos) arrives
11. **Fix Indhu face enrollment** — re-enroll with better samples

---

## SESSION HISTORY (What was done when)

### Session 1 (Phase 0 + Phase 1 foundation)
- Built entire Phase 0 architecture, config, logging, event bus, state machine, personality, memory
- Built camera pipeline, person detector (YOLOv8n)
- Built sensor monitor tool
- 31 unit tests passing

### Session 2 (Phase 1 completion + Phase 4 audio)
- **Face recognition**: SFace ONNX embeddings replacing LBPH
- **Enrolled faces**: Madhan + Indhu headless (SSH-friendly enroll_face.py)
- **Emotion detection**: DeepFace/FER integrated into vision_loop
- **Connected JBL Flip 5 BT**: PipeWire A2DP, disabled bluealsa conflict
- **Full audio pipeline**: mic → VAD → STT (Whisper) → LLM → TTS
- **Piper TTS**: installed + integrated as primary voice (espeak fallback)
- **cosmo_demo.py**: full integration demo with Rich live dashboard
- **Critical bugfix**: vision_loop crashing on `lbph_distance` attr (removed stale ref)
- **Ollama**: cold start 51s → fixed with 90s timeout + `keep_alive: 1h` + background warmup
- **Verified**: Madhan recognized, greeted in Tanglish, Piper voice on JBL ✅
