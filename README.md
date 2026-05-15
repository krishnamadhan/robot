# Cosmo — Home Robot

A small wheeled robot that lives with Madhan and Indhu in their Bangalore apartment. Cosmo has a personality — naughty Tamil kid, speaks Tanglish, reacts to faces, emotions, voice, and environment.

Built on Raspberry Pi 5. All code runs on-device. No cloud dependency for core function — Claude API is optional (personality speech only).

---

## Quick Start

```bash
pm2 start cosmo       # start
pm2 stop cosmo        # stop
pm2 restart cosmo     # restart after code changes
pm2 logs cosmo        # live logs
pm2 logs cosmo --lines 50 --nostream   # last 50 lines
```

Logs: `~/.robot/logs/cosmo-out.log`, `~/.robot/logs/cosmo-error.log`
Config: `config/hardware.yaml`, `config/models.yaml`, `config/personality.yaml`
Env vars: `.env` — ANTHROPIC_API_KEY, PICOVOICE_KEY (optional)

Voice toggle mind: say `"mind off"` to disable Claude / `"mind on"` to re-enable.

---

## Hardware — Full Inventory

### Raspberry Pi 5 (8GB)
- Running Raspberry Pi OS 64-bit (bookworm)
- **BCM GPIO numbering used throughout** (not physical pin numbers)
- I2C bus: `/dev/i2c-1` (SDA=GPIO2/Pin3, SCL=GPIO3/Pin5)

### GPIO Pin Map (BCM)

| BCM GPIO | Physical Pin | Function | Component | Notes |
|---|---|---|---|---|
| GPIO2 | Pin 3 | SDA | I2C bus shared | BH1750, MPU6050, APDS9960, UPS HAT, OLED ×2 |
| GPIO3 | Pin 5 | SCL | I2C bus shared | — |
| GPIO4 | Pin 7 | Touch LEFT | Capacitive pad | |
| GPIO5 | Pin 29 | Touch HEAD | Capacitive pad | |
| GPIO6 | Pin 31 | BIN2 (Right motor backward) | TB6612FNG | ⚠️ CONFLICT with battery_monitor.py AC-detect — needs remap |
| GPIO7 | Pin 26 | Touch RIGHT | Capacitive pad | |
| GPIO8 | Pin 24 | PIR motion | HC-SR501 | Moved from GPIO16 (was conflicting with HC-SR04 TRIG) |
| GPIO12 | Pin 32 | APDS9960 INT | Interrupt pin | Optional, floating if not used |
| GPIO13 | Pin 33 | PWMB (Right motor PWM) | TB6612FNG | Hardware PWM1 |
| GPIO16 | Pin 36 | HC-SR04 TRIG | Ultrasonic | 3.3V direct (no divider needed) |
| GPIO17 | Pin 11 | AIN1 (Left motor forward) | TB6612FNG | |
| GPIO18 | Pin 12 | PWMA (Left motor PWM) | TB6612FNG | Hardware PWM0 |
| GPIO19 | Pin 35 | Sound sensor | KY-038 | Via 10kΩ+10kΩ voltage divider (5V→3.3V) |
| GPIO20 | Pin 38 | Cliff LEFT | TCRT5000 | Via LLC channel |
| GPIO21 | Pin 40 | Cliff RIGHT | TCRT5000 | Via LLC channel |
| GPIO22 | Pin 15 | AIN2 (Left motor backward) | TB6612FNG | |
| GPIO23 | Pin 16 | BIN1 (Right motor forward) | TB6612FNG | |
| GPIO24 | Pin 18 | HC-SR04 ECHO | Ultrasonic | Via LLC (5V→3.3V) |
| GPIO25 | Pin 22 | Touch BELLY | Capacitive pad | |
| GPIO26 | Pin 37 | Vibration | SW-420 | Via 10kΩ+10kΩ voltage divider (5V→3.3V) |
| GPIO27 | Pin 13 | STBY (Motor standby) | TB6612FNG | Active HIGH = motors enabled |

### I2C Devices

| Address | Device | Purpose | Status |
|---|---|---|---|
| 0x23 | BH1750 | Ambient light (lux) | ✅ wired, working |
| 0x36 | UPS HAT (MAX17043) | Battery level + voltage | ✅ wired, working |
| 0x39 | APDS9960 | Gesture + proximity + color | ⏳ needs header soldering |
| 0x3C | SSD1306 OLED | Left eye display (128×64) | ⏳ not wired yet |
| 0x3D | SSD1306 OLED | Right eye display (128×64) | ⏳ not wired yet (A0 pad must be bridged for 0x3D) |
| 0x40 | PCA9685 | Servo driver (pan/tilt head) | ⏳ not purchased yet |
| 0x68 | MPU6050 | IMU (accel + gyro) | ⏳ not wired yet |

### Sensors — Wiring Detail

#### HC-SR04 Ultrasonic (distance)
- VCC → 5V, GND → GND
- TRIG → GPIO16 directly (output, 3.3V signal is fine)
- ECHO → via LLC (Level Logic Converter): 5V side → HC-SR04 ECHO, 3.3V side → GPIO24
- Config: `sensors.ultrasonic.available: true` to enable
- Currently: `available: false` (not yet enabled)

#### PIR Motion (HC-SR501)
- VCC → 5V, GND → GND
- OUT → GPIO8 (3.3V tolerant, HC-SR501 outputs 3.3V)
- Config: `sensors.pir.available: true` to enable
- Note: Was GPIO16, moved due to conflict with HC-SR04 TRIG

#### KY-038 Sound Sensor
- VCC → 5V, GND → GND
- DO (digital out) → 10kΩ → GPIO19 → 10kΩ → GND (voltage divider)
- Config: `sensors.sound.available: true` to enable

#### SW-420 Vibration Sensor
- VCC → 5V, GND → GND
- DO → 10kΩ → GPIO26 → 10kΩ → GND (voltage divider)
- Config: `sensors.vibration.available: true` to enable

#### TCRT5000 Cliff Sensors (×2)
- VCC → 5V, GND → GND
- DO → via LLC → GPIO20 (left), GPIO21 (right)
- Config: `sensors.cliff.available: true` to enable

#### Touch Capacitive Pads (×4)
- Each pad → GPIO via pull-down resistor (internal pull-down enabled in software)
- HEAD → GPIO5, BELLY → GPIO25, LEFT → GPIO4, RIGHT → GPIO7
- Config: `sensors.touch.available: true` to enable

#### APDS9960 Gesture/Proximity
- VCC → 3.3V (NOT 5V — 3.3V only device)
- SDA → GPIO2, SCL → GPIO3
- INT → GPIO12 (optional interrupt pin)
- Config: `sensors.apds9960.available: true` to enable

#### SSD1306 OLED Eyes (×2)
- VCC → 3.3V, GND → GND
- SDA → GPIO2, SCL → GPIO3
- Left eye address 0x3C (default), Right eye 0x3D (A0 pad bridged)
- Driver fully implemented in `expression/eyes.py`, just needs wiring
- To switch to OLED: `eye_engine.set_render_target("oled")` in code

### Motor Driver — TB6612FNG

```
Motor A (LEFT):
  AIN1 = GPIO17  → forward
  AIN2 = GPIO22  → backward
  PWMA = GPIO18  → speed (hardware PWM0)

Motor B (RIGHT):
  BIN1 = GPIO23  → forward
  BIN2 = GPIO6   → backward  ⚠️ see GPIO6 conflict note
  PWMB = GPIO13  → speed (hardware PWM1)

STBY = GPIO27   → HIGH = enabled, LOW = coast stop
```

Software PWM fallback is implemented (`hardware/motors.py` `_SoftPWM` class) for when hardware PWM conflicts with other uses.

#### ⚠️ Known Conflict: GPIO6
`battery_monitor.py` uses GPIO6 to detect AC power (charger plugged in). This is the same pin as BIN2 (right motor backward). Current workaround: `battery_monitor.py` is a separate PM2 process — conflict only manifests if both drive GPIO6 simultaneously. **Needs proper remap before heavy motor testing.**

### Audio

| Component | Connection | Notes |
|---|---|---|
| Microphone | Logitech C920 (USB) — built-in mic | Device auto-detected |
| Speaker | JBL Flip 5 (Bluetooth) | Via PipeWire (`pw-play`) |

Bluetooth audio pipeline: Piper TTS generates WAV at 22050 Hz mono → ffmpeg upsamples to 44100 Hz stereo → `pw-play` → JBL Flip 5. The upsample step is critical — without it PipeWire resamples on the fly and produces grainy audio.

### Camera

- Logitech C920 HD Pro Webcam, USB
- `/dev/video0`, 640×480 @ 30fps
- Also provides the microphone

---

## Software Stack

| Layer | Technology | Notes |
|---|---|---|
| OS | Raspberry Pi OS 64-bit (bookworm) | |
| Language | Python 3.13 | |
| Process manager | PM2 | `cosmo`, `banteragent`, `battery-monitor`, `pi-monitor`, `pi-scheduler` |
| Vision | OpenCV + YOLOv8n | Person detection |
| Face recognition | DeepFace (VGG-Face backend) | Enrolled: Madhan, Indhu |
| Emotion detection | DeepFace | 7 emotions |
| Wake word | OpenWakeWord | `hey_jarvis` model (custom `hey_cosmo` not yet trained) |
| STT | faster-whisper tiny.en | ~75MB, ~2s latency on Pi 5 |
| TTS | Piper (en_US-lessac-medium, neural) | Falls back to espeak-ng |
| Audio output | PipeWire + pw-play | Native Bluetooth support |
| LLM (local) | Ollama llama3.2:1b | Runs fully offline, free |
| LLM (cloud) | Claude Haiku 4.5 | Anthropic API — personality speech only |
| Event bus | Custom async pub/sub | `core/event_bus.py` |
| Memory | SQLite (episodic) + RAM (working) | Episodic retrieval not yet wired to prompts |

---

## Architecture

```
perception/
  vision/
    camera.py          — OpenCV camera capture
    person.py          — YOLOv8n person detection
    face.py            — DeepFace recognition (enrolled: Madhan, Indhu)
    emotion.py         — DeepFace emotion detection (7 classes)
    vision_loop.py     — orchestrates camera → detect → recognize → emit events
  audio/
    mic.py             — microphone capture
    wake_word.py       — OpenWakeWord (hey_jarvis)
    vad.py             — voice activity detection
    stt.py             — faster-whisper STT
    pipeline.py        — full audio pipeline: wake → listen → transcribe → respond

cognition/
  mind.py              — two-tier brain (see Brain Logic section)
  conversation.py      — manages active voice conversations
  llm.py               — Ollama-first LLM, Claude Haiku fallback
  intent.py            — offline Tanglish command pattern matching

core/
  event_bus.py         — async pub/sub, all inter-module communication
  personality.py       — mood/energy/arousal/attachment, decays over time
  state_machine.py     — behavioral states: idle.calm → active → sleeping → exploring
  memory/
    working.py         — short-term conversation history (RAM)
    episodic.py        — long-term memory (SQLite, not yet retrieved into prompts)
    spatial.py         — room mapping (stubbed)

behavior/
  engine.py            — dance, happy/love reactions, proactive speech behaviors
  navigation.py        — wander, follow, approach_person, retreat, spin_360

expression/
  eyes.py              — terminal rendering + SSD1306 OLED driver (luma.oled)
  speech.py            — Piper TTS → espeak-ng fallback → ffmpeg upsample → pw-play
  sounds.py            — synthesized robot sounds (chirps, purrs, chimes)

hardware/
  motors.py            — TB6612FNG driver + software PWM fallback
  sensor_manager.py    — aggregates all sensors, returns mock values if unavailable
  sensors/             — individual sensor drivers (BH1750, MPU6050, HC-SR04, etc.)
  servos.py            — PCA9685 servo driver (stubbed, awaiting hardware)

tools/
  cosmo_demo.py        — main demo/personality layer (event handlers, face greet, etc.)
  sensor_monitor.py    — real-time developer dashboard
  enroll_face.py       — face enrollment tool
```

---

## Brain Logic — Two-Tier Design

The brain is split to avoid calling Claude for simple decisions.

### Tier 1: Rule Engine (FREE — every 5 seconds, zero API calls)

| Sensor reading | Action |
|---|---|
| `dist < 25cm` | Stop motors, show SURPRISED expression |
| `lux < 50` | Show SCARED expression, back away, speak (2min cooldown) |
| `idle > 120s, nav=idle` | Start 20s wander routine, show CURIOUS expression |
| `dist > 80cm, idle > 60s` | 15% chance: short forward explore burst |
| `idle > 300s, no person` | Speak "alone" phrase (5min cooldown) |

### Tier 2: Claude Speech (PAID — event-triggered, rate-limited)

Claude is called **only to generate personality speech**. Never for movement or expression decisions.

| Trigger | Condition | Default Cooldown |
|---|---|---|
| `face_seen` | Face recognized | 3 min |
| `emotion_happy` | Person looks happy | 3 min |
| `emotion_sad` | Person looks sad | 3 min |
| `emotion_angry` | Person looks angry | 3 min |
| `touched` | Touch sensor fires | 3 min |
| `alone_long` | No person > 5 min | 5 min |
| `obstacle` | Near-collision avoided | 1 min |
| `dark_room` | lux drops below 50 | 2 min |

Voice conversations ("Hey Cosmo" → speak) → **Ollama first**, Claude only if Ollama is down.

### Daily Token Budget
Hard limit: `100,000 tokens/day` — mind silences itself if exceeded.
Logged: `cosmo_mind.tokens` per call, `cosmo_mind.daily_summary` at midnight.
Toggle: say `"mind off"` / `"mind on"` (also wired to intent parser).

---

## API Credit Usage — Full Map

**Old design (burned ~1.2M tokens in one session):** Claude called every 4 seconds with full sensor state to decide movement — completely unnecessary.

**Current design:** Claude only generates speech, never makes movement decisions.

| Source | Frequency | Ollama? | Claude? |
|---|---|---|---|
| Spontaneous speech (mind tier 2) | Event-triggered, rate-limited | No | Always |
| Voice conversation replies | Per utterance | Yes (primary) | Fallback only |
| Face greeting | Once per session per person | Yes (primary) | Fallback only |
| Emotion proactive speech | On emotion change, random % | Yes (primary) | Fallback only |

---

## Open Issues

### ⚠️ GPIO6 Conflict
`battery_monitor.py` (PM2 process) uses GPIO6 as AC-power detect pin. GPIO6 is also BIN2 (right motor backward). If both processes drive the pin simultaneously, the motor driver will behave incorrectly. Fix: remap battery monitor to an unused GPIO.

### Sensors Not Yet Enabled
All sensors except BH1750 and UPS HAT are `available: false` in `config/hardware.yaml`. To enable once wired, change to `available: true`. Each sensor has a mock fallback so Cosmo runs fine without them.

### OLED Eyes Not Wired
The SSD1306 driver is fully implemented (`expression/eyes.py`). Currently renders to terminal. Once wired: `eye_engine.set_render_target("oled")`. This is a high-impact visual upgrade.

### Episodic Memory Not Retrieving
The episodic memory DB writes happen on every conversation turn. But the retrieval path (querying memories and injecting them into the system prompt) is not implemented yet. Cosmo doesn't actually remember past conversations. This is the biggest missing personality feature.

### Face Confidence — Indhu
Indhu's face recognition confidence is ~73-75%. Needs fresh enrollment with better lighting and multiple angles. Use: `python3 tools/enroll_face.py`

### Wake Word
Currently using `hey_jarvis`. A custom `hey_cosmo` model would be better. Train for free at `console.picovoice.ai → Wake Word → New Wake Word`.

---

## Personality System

Cosmo has a continuous internal state managed by `core/personality.py`:

| Variable | Range | Meaning |
|---|---|---|
| `mood` | -1.0 to +1.0 | Negative = grumpy, positive = happy |
| `energy` | 0.0 to 1.0 | 0 = sleepy, 1 = hyperactive |
| `arousal` | 0.0 to 1.0 | Low = calm, high = excited/anxious |
| `attachment` | 0.0 to 1.0 | Comfort level with known people |

These decay toward baseline over time. Good interactions raise mood/energy. Being alone lowers mood. Dark rooms lower mood/energy. Touch raises attachment.

Personality influences TTS speed/pitch and LLM system prompt context.

---

## Known Working Features (as of 2026-05-15)

- [x] Face recognition: Madhan + Indhu, ~85% / ~75% confidence
- [x] Emotion detection: 7 emotions from camera
- [x] Wake word: "Hey Jarvis" triggers conversation
- [x] Voice conversation: speak → Whisper STT → Ollama → Piper TTS → JBL
- [x] Spontaneous speech when person detected (Claude, rate-limited)
- [x] Autonomous wandering when idle
- [x] Obstacle stop (distance sensor, currently mocked)
- [x] Touch reactions (capacitive pads wired, software enabled)
- [x] Eye expressions (terminal rendering, OLED ready)
- [x] Motor drive with software PWM
- [x] Battery monitoring via UPS HAT
- [x] Light sensing via BH1750
- [x] Live camera stream: `http://100.101.250.126:8080`
- [x] Intent parser: 15+ Tanglish voice commands without LLM
- [x] Bluetooth speaker (JBL Flip 5 via PipeWire, 44100Hz stereo)
- [x] Daily token budget with hard cutoff + per-call logging

## Not Yet Working / Pending

- [ ] OLED eye displays (wiring needed)
- [ ] Servo head tracking (hardware not purchased)
- [ ] HC-SR04 real distance (mocked)
- [ ] PIR motion sensor (not wired)
- [ ] Episodic memory retrieval in prompts
- [ ] Custom `hey_cosmo` wake word
- [ ] GPIO6 conflict resolution
- [ ] Indhu face re-enrollment
- [ ] Web management dashboard

---

## Future Roadmap

### Phase 1 — Sensory (next)
- Wire HC-SR04, PIR, cliff, vibration, sound sensors
- Wire OLED eyes (biggest personality impact)
- Fix GPIO6 battery monitor conflict
- Re-enroll Indhu's face

### Phase 2 — Intelligence
- Episodic memory retrieval into prompts (Cosmo remembers conversations)
- Upgrade Ollama to llama3.2:3b for better Tanglish
- Custom `hey_cosmo` wake word

### Phase 3 — Expressiveness
- Servo pan/tilt head (face tracking)
- Sound + vibration reactive behaviors
- Richer proactive behaviors (time-aware greetings, reminders)

### Phase 4 — Polish
- Web dashboard (stream + status + mind toggle + face enrollment)
- Custom Tanglish TTS voice
- Multi-room awareness

---

## Developer Notes

### Adding a new sensor
1. Add driver in `hardware/sensors/`
2. Register in `hardware/sensor_manager.py`
3. Add config entry in `config/hardware.yaml` with `available: false`
4. Set `available: true` once wired

### Adding a new voice command (no LLM cost)
Add pattern to `cognition/intent.py` `_INTENTS` dict, wire action in `tools/cosmo_demo.py` `on_intent()`.

### Adding a new Claude speech trigger
Add entry to `_SPEAK_PROMPTS` in `cognition/mind.py`, call `cosmo_mind._maybe_speak(trigger, name)` from the relevant event handler.

### Sensor mock values
When `available: false`, `sensor_manager` returns realistic mock values so all code paths work without hardware. See `hardware/mock.py`.
