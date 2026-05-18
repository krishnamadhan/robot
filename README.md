# Cosmo — Home Robot

A small wheeled robot that lives with Madhan and Indhu. Cosmo has a personality — playful, affectionate, curious — reacts to faces, emotions, voice, and environment.

Built on Raspberry Pi 5. All code runs on-device. Claude API is optional (personality speech only). Ollama handles voice conversations locally.

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
Env vars: `.env` — `ANTHROPIC_API_KEY`, `PICOVOICE_KEY` (optional)

Voice commands: say `"mind off"` to disable Claude / `"mind on"` to re-enable.

---

## Hardware — Full Inventory

### Raspberry Pi 5 (8GB)
- Raspberry Pi OS 64-bit (bookworm)
- **BCM GPIO numbering throughout** (not physical pin numbers)
- I2C bus: `/dev/i2c-1` (SDA=GPIO2/Pin3, SCL=GPIO3/Pin5)

### GPIO Pin Map (BCM)

| BCM GPIO | Physical Pin | Function | Component | Notes |
|---|---|---|---|---|
| GPIO2 | Pin 3 | SDA | I2C bus shared | BH1750, MPU6050, APDS9960, UPS HAT, OLED ×2 |
| GPIO3 | Pin 5 | SCL | I2C bus shared | — |
| GPIO4 | Pin 7 | Touch LEFT | Capacitive pad | |
| GPIO5 | Pin 29 | Touch HEAD | Capacitive pad | |
| GPIO6 | Pin 31 | BIN2 (Right motor backward) | TB6612FNG | ⚠️ CONFLICT with battery_monitor.py AC-detect |
| GPIO7 | Pin 26 | Touch RIGHT | Capacitive pad | |
| GPIO8 | Pin 24 | PIR motion | HC-SR501 | Moved from GPIO16 (was conflicting with HC-SR04 TRIG) |
| GPIO9 | Pin 21 | APDS9960 INT | Interrupt pin | Moved from GPIO12 to free hardware PWM0 |
| GPIO11 | Pin 23 | Sound sensor | KY-038 | Via 10kΩ+10kΩ divider (moved from GPIO19 to free I2S) |
| GPIO12 | Pin 32 | PWMA (Left motor PWM) | TB6612FNG | Hardware PWM0 (moved from GPIO18 to free I2S bus) |
| GPIO13 | Pin 33 | PWMB (Right motor PWM) | TB6612FNG | Hardware PWM1 |
| GPIO14 | Pin 8 | Cliff LEFT | TCRT5000 | Via LLC (moved from GPIO20 to free I2S) |
| GPIO15 | Pin 10 | Cliff RIGHT | TCRT5000 | Via LLC (moved from GPIO21 to free I2S) |
| GPIO16 | Pin 36 | HC-SR04 TRIG | Ultrasonic | 3.3V direct |
| GPIO17 | Pin 11 | AIN1 (Left motor forward) | TB6612FNG | |
| GPIO18 | Pin 12 | I2S BCLK | INMP441 mic | Free for I2S; previously PWMA |
| GPIO19 | Pin 35 | I2S LRCLK | INMP441 mic | Free for I2S; previously sound sensor |
| GPIO20 | Pin 38 | I2S DIN | INMP441 mic | Free for I2S; previously cliff LEFT |
| GPIO21 | Pin 40 | I2S DOUT | INMP441 mic | Free for I2S; previously cliff RIGHT |
| GPIO22 | Pin 15 | AIN2 (Left motor backward) | TB6612FNG | |
| GPIO23 | Pin 16 | BIN1 (Right motor forward) | TB6612FNG | |
| GPIO24 | Pin 18 | HC-SR04 ECHO | Ultrasonic | Via LLC (5V→3.3V) |
| GPIO25 | Pin 22 | Touch BELLY | Capacitive pad | |
| GPIO26 | Pin 37 | Vibration | SW-420 | Via 10kΩ+10kΩ divider |
| GPIO27 | Pin 13 | STBY (Motor standby) | TB6612FNG | HIGH = motors enabled |

> **I2S bus note:** GPIO18–21 are reserved for the INMP441 I2S microphone. All conflicting peripherals have been remapped. The overlay `dtoverlay=inmp441-pi5` is already in `/boot/firmware/config.txt`. Switch from C920 to INMP441: set `audio.inmp441.available: true` in `hardware.yaml` and update the mic device to `hw:1,0`.

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
- TRIG → GPIO16 directly (3.3V output is fine)
- ECHO → via LLC: 5V side → HC-SR04 ECHO, 3.3V side → GPIO24
- Config: `sensors.ultrasonic.available: true` to enable

#### PIR Motion (HC-SR501)
- VCC → 5V, GND → GND
- OUT → GPIO8 (HC-SR501 outputs 3.3V, Pi-safe)
- Config: `sensors.pir.available: true` to enable

#### KY-038 Sound Sensor
- VCC → 5V, GND → GND
- DO → 10kΩ → GPIO11 → 10kΩ → GND (voltage divider; moved from GPIO19)
- Config: `sensors.sound.available: true` to enable

#### SW-420 Vibration Sensor
- VCC → 5V, GND → GND
- DO → 10kΩ → GPIO26 → 10kΩ → GND
- Config: `sensors.vibration.available: true` to enable

#### TCRT5000 Cliff Sensors (×2)
- VCC → 5V, GND → GND
- DO → via LLC → GPIO14 (left), GPIO15 (right) — moved from GPIO20/21
- Config: `sensors.cliff.available: true` to enable

#### Touch Capacitive Pads (×4)
- Internal pull-down enabled in software
- HEAD → GPIO5, BELLY → GPIO25, LEFT → GPIO4, RIGHT → GPIO7
- Config: `sensors.touch.available: true` to enable

#### APDS9960 Gesture/Proximity
- VCC → 3.3V (3.3V only — not 5V tolerant)
- SDA → GPIO2, SCL → GPIO3
- INT → GPIO9 (moved from GPIO12)
- Config: `sensors.apds9960.available: true` to enable

#### SSD1306 OLED Eyes (×2)
- VCC → 3.3V, GND → GND, SDA → GPIO2, SCL → GPIO3
- Left eye: 0x3C (default), Right eye: 0x3D (A0 pad bridged)
- Driver fully implemented in `expression/eyes.py`
- To switch to OLED: `eye_engine.set_render_target("oled")` in code

### Motor Driver — TB6612FNG

```
Motor A (LEFT):
  AIN1 = GPIO17  → forward
  AIN2 = GPIO22  → backward
  PWMA = GPIO12  → speed (hardware PWM0)  ← moved from GPIO18

Motor B (RIGHT):
  BIN1 = GPIO23  → forward
  BIN2 = GPIO6   → backward  ⚠️ see GPIO6 conflict note
  PWMB = GPIO13  → speed (hardware PWM1)

STBY = GPIO27   → HIGH = enabled, LOW = coast stop
```

Software PWM fallback implemented (`hardware/motors.py` `_SoftPWM` class).

#### ⚠️ Known Conflict: GPIO6
`battery_monitor.py` uses GPIO6 to detect AC power. Same pin as BIN2 (right motor backward). Fix: remap battery monitor to an unused GPIO before heavy motor testing.

### Audio

| Component | Connection | Notes |
|---|---|---|
| Microphone (active) | Logitech C920 (USB) | Device auto-detected |
| Microphone (ready) | INMP441 (I2S, GPIO18–21) | Overlay installed; switch via config |
| Speaker | JBL Flip 5 (Bluetooth) | Via PipeWire (`pw-play`) |

Bluetooth audio pipeline: Piper TTS → WAV at 22050 Hz mono → ffmpeg upsample to 44100 Hz stereo → `pw-play` → JBL Flip 5. Upsampling is required — skipping it produces grainy audio via PipeWire.

#### INMP441 Mono Channel
The INMP441 outputs audio on the **left channel only** (right is silent). `mic.py` extracts the left channel directly rather than averaging, which would halve the amplitude.

### Camera

- Logitech C920 HD Pro Webcam, USB
- `/dev/video0`, 640×480 @ 30fps
- Also provides the microphone when INMP441 is not active

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
| Wake word | OpenWakeWord | `hey_jarvis` model |
| STT | faster-whisper tiny.en | ~75MB, ~2s latency on Pi 5 |
| TTS | Piper (en_US-lessac-medium) | Falls back to espeak-ng |
| Audio output | PipeWire + pw-play | Native Bluetooth support |
| LLM (local) | Ollama llama3.2:1b | Voice conversations, fully offline |
| LLM (cloud) | Claude Haiku 4.5 | Anthropic API — personality speech only |
| Event bus | Custom async pub/sub | `core/event_bus.py` |
| Memory | SQLite (episodic) + RAM (working) | |

---

## Architecture

```
Camera (30 FPS)
  ├── YOLOv8n         person detection      (15 FPS)
  ├── SFace           face recognition      ( 4 FPS, enrolled: Madhan, Indhu)
  ├── DeepFace        emotion detection     ( 2 FPS, 7 classes)
  └── OpenCV Gesture  hand gesture          ( 4 FPS, MediaPipe-ready)
       ↓ events → event_bus (async pub/sub, priority queues)
Behavior Tree (100ms tick, 52 nodes, py_trees)
  reads:  personality state, blackboard (person/emotion/gesture/energy)
  writes: sounds.play(), eye_engine.set_expression(), navigation.*
  tree:   SAFETY → SLEEP_ACTIVE → WAKE_FROM_SLEEP → SOCIAL → AUTONOMOUS
           SOCIAL: GESTURE → GREET(5min) → EMOTION_REACT(30s) → PERSON_PRESENT
           AUTONOMOUS: ENTER_SLEEP → BORED_HIGH(3min) → BORED_MED(1min) → IDLE
Personality Engine (continuous decay)
  mood / energy / arousal / boredom → feeds BT blackboard

Next to wire: INMP441 mic → Vosk keywords → BT COMMAND branch
Next to wire: OLED eyes (SSD1306 × 2), sensors, motors

perception/
  vision/
    camera.py          — async frame buffer, Logitech C920 USB
    person.py          — YOLOv8n person detection, HOG fallback
    face.py            — SFace face recognition
    emotion.py         — DeepFace emotion detection
    gesture.py         — OpenCV skin+hull (MediaPipe Tasks API ready)
    vision_loop.py     — orchestrates all vision loops
  audio/
    pipeline.py        — STUB (STT removed Phase A)
    wake_word.py       — STUB (wake word removed Phase A)
    stt.py             — STUB (STT removed Phase A)
    commands.py        — STUB (wire INMP441 + Vosk to activate)

cognition/
  mind.py              — rule engine (wander/obstacle/dark), Claude speech disabled
  llm.py               — STUB (LLM calls disabled Phase A)
  conversation.py      — session tracking (no active listening)

core/
  event_bus.py         — async pub/sub, priority queues, dead letter queue
  behavior_tree.py     — py_trees BT, 52 nodes, 100ms tick, CosmoBlackboard
  personality.py       — mood/energy/arousal/attachment, decays over time
  state_machine.py     — state tracking (legacy, demoted)
  memory/
    episodic.py        — long-term memory (SQLite)

behavior/
  engine.py            — idle behaviors, navigation triggers
  navigation.py        — wander, approach, retreat, spin

expression/
  eyes.py              — 12 expressions, terminal renderer (SSD1306 when wired)
  sounds.py            — 22 numpy-generated sounds, priority interruption
  speech.py            — STUB (TTS removed Phase A)

hardware/
  motors.py            — TB6612FNG driver (mock until wired)
  sensor_manager.py    — all sensors, mock values until wired
  servos.py            — PCA9685 driver (mock until wired)

tools/
  cosmo_demo.py        — main entry point, event handlers, BT wiring
  bt_test.py           — Phase C: 5 BT tests
  gesture_test.py      — Phase D: 5 gesture tests
  enroll_face.py       — face enrollment
  sensor_monitor.py    — live sensor dashboard
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

Voice conversations ("Hey Jarvis" → speak) → **Ollama first**, Claude only if Ollama is down.

### Pre-baked Voice Lines
Common greetings (per-person), touch reactions, and alone phrases are **pre-generated via Piper at startup** and stored in `/tmp/cosmo_sounds/`. These play instantly with zero API or TTS latency. Claude is only called for novel/contextual speech.

### Daily Token Budget
Hard limit: `100,000 tokens/day` — mind silences itself if exceeded.
Toggle: say `"mind off"` / `"mind on"`.

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

### Known Persons
Per-person greeting banks are defined in `config/personality.yaml` under `known_persons`. Cosmo picks a random greeting from the bank for recognized faces rather than generating one via LLM.

| Person | Style |
|---|---|
| Madhan | Playful, teasing, like best friends |
| Indhu | Warm, affectionate, like family |
| Stranger | Cautious but curious |

---

## Open Issues

### ⚠️ GPIO6 Conflict
`battery_monitor.py` uses GPIO6 as AC-power detect. GPIO6 is also BIN2 (right motor backward). Conflict only manifests if both processes drive the pin simultaneously. Fix: remap battery monitor to an unused GPIO before motor testing.

### Sensors Not Yet Enabled
All sensors except BH1750 and UPS HAT are `available: false` in `config/hardware.yaml`. Each has a mock fallback so Cosmo runs without them.

### OLED Eyes Not Wired
SSD1306 driver fully implemented. Currently renders to terminal. Once wired: `eye_engine.set_render_target("oled")`.

### Episodic Memory Not Retrieving
Episodic DB writes happen each conversation turn. Retrieval into the system prompt is not yet implemented — Cosmo doesn't actually remember past conversations. Biggest missing personality feature.

### Face Confidence — Indhu
Indhu's face recognition confidence is ~73-75%. Re-enroll with better lighting: `python3 tools/enroll_face.py`

### Wake Word
Currently using `hey_jarvis`. Custom `hey_cosmo` model can be trained free at `console.picovoice.ai`.

---

## Known Working Features (as of 2026-05-16)

- [x] Face recognition: Madhan + Indhu (~85% / ~75% confidence)
- [x] Emotion detection: 7 emotions from camera
- [x] Wake word: "Hey Jarvis" triggers conversation
- [x] Voice conversation: speak → Whisper STT → Ollama → Piper TTS → JBL
- [x] Spontaneous speech when person detected (Claude, rate-limited)
- [x] Pre-baked greeting lines — zero-latency, no API call
- [x] Autonomous wandering when idle
- [x] Obstacle stop (mocked)
- [x] Touch reactions (capacitive pads wired and software enabled)
- [x] Eye expressions (terminal rendering, OLED driver ready)
- [x] Motor drive with software PWM
- [x] Battery monitoring via UPS HAT
- [x] Light sensing via BH1750
- [x] Live camera stream: `http://100.101.250.126:8080`
- [x] Intent parser: 15+ voice commands without LLM
- [x] Bluetooth speaker (JBL Flip 5 via PipeWire, 44100Hz stereo)
- [x] Daily token budget with hard cutoff
- [x] Concurrency-safe conversation lock (no duplicate responses)
- [x] GPIO remapped: I2S bus (GPIO18–21) free for INMP441
- [x] INMP441 I2S overlay installed — ready to activate via config

## Not Yet Working / Pending

- [ ] INMP441 active (C920 still primary — switch via config)
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
- Activate INMP441 mic (already wired in config, just needs physical wiring)

### Phase 2 — Intelligence
- Episodic memory retrieval into prompts (Cosmo remembers conversations)
- Upgrade Ollama to llama3.2:3b for better quality
- Custom `hey_cosmo` wake word

### Phase 3 — Expressiveness
- Servo pan/tilt head (face tracking)
- Sound + vibration reactive behaviors
- Richer proactive behaviors (time-aware greetings, reminders)

### Phase 4 — Polish
- Web dashboard (stream + status + mind toggle + face enrollment)
- Custom TTS voice
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

### Switching mic from C920 to INMP441
1. In `config/hardware.yaml`: set `audio.inmp441.available: true`, `audio.mic.available: false`
2. The I2S pins (GPIO18–21) are already free — all conflicts remapped
3. Overlay `dtoverlay=inmp441-pi5` is already in `/boot/firmware/config.txt`
4. Run `python3 tools/mic_compare.py` to validate SNR before committing

### Sensor mock values
When `available: false`, `sensor_manager` returns realistic mock values so all code paths work without hardware. See `hardware/mock.py`.
