# Cosmo — System Architecture

**Version:** Phase 0.1  
**Platform:** Raspberry Pi 5, 8GB RAM, Debian Trixie (64-bit)  
**Last Updated:** 2026-05-13

---

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         COSMO ROBOT SYSTEM                          │
│                                                                     │
│  ┌──────────────────┐     ┌──────────────────┐                      │
│  │   PERCEPTION     │     │    HARDWARE       │                      │
│  │                  │     │                   │                      │
│  │  ┌────────────┐  │     │  ┌─────────────┐ │                      │
│  │  │  Camera    │──┼────▶│  │  Webcam     │ │                      │
│  │  │  Pipeline  │  │     │  │  (USB)      │ │                      │
│  │  └────────────┘  │     │  └─────────────┘ │                      │
│  │  ┌────────────┐  │     │  ┌─────────────┐ │                      │
│  │  │  Person    │  │     │  │  BH1750     │ │                      │
│  │  │  Detector  │  │     │  │  Light      │ │                      │
│  │  └────────────┘  │     │  └─────────────┘ │                      │
│  │  ┌────────────┐  │     │  ┌─────────────┐ │                      │
│  │  │  Audio     │  │     │  │  TB6612FNG  │ │                      │
│  │  │  Pipeline  │  │     │  │  Motors     │ │                      │
│  │  └────────────┘  │     │  └─────────────┘ │                      │
│  └────────┬─────────┘     │  [+ 15 sensors   │                      │
│           │               │   arriving soon] │                      │
│           │               └──────────────────┘                      │
│           ▼                                                          │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │                     EVENT BUS (async)                       │     │
│  │   Priority: SAFETY > HIGH > NORMAL > LOW                   │     │
│  │   Safety events always dispatched first                     │     │
│  └──────────────────┬─────────────────────────────────────────┘     │
│                     │                                                │
│         ┌───────────┼────────────┐                                  │
│         ▼           ▼            ▼                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐                         │
│  │  STATE   │ │PERSONALITY│ │   BEHAVIOR   │                        │
│  │ MACHINE  │ │  ENGINE  │ │    ENGINE    │                         │
│  │          │ │          │ │              │                         │
│  │ Hierarchi│ │ Mood/    │ │ Behavior     │                         │
│  │ cal HSM  │ │ Energy/  │ │ Tree         │                         │
│  │ 30 states│ │ Arousal  │ │ Idle/Social/ │                         │
│  └──────────┘ └────┬─────┘ │ Navigation  │                         │
│                    │       └──────┬───────┘                         │
│                    ▼             ▼                                  │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │                      MEMORY SYSTEM                          │     │
│  │                                                             │     │
│  │  Working Memory    Episodic Memory    Spatial Memory        │     │
│  │  (in-RAM, 5min)   (SQLite, forever)  (JSON, room maps)     │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                     │
│                    ▼                                                │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │                    EXPRESSION SYSTEM                        │     │
│  │                                                             │     │
│  │   OLED Eyes    Body Movement    Sound    Speech (TTS)       │     │
│  │   (SSD1306)    (Motors+Servo)   (Tones)  (Piper)           │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                     │
│                    ▼                                                │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │                    COGNITION SYSTEM                         │     │
│  │                                                             │     │
│  │   LLM Interface    Conversation    Intent    Context        │     │
│  │   (Ollama local/   Manager         Parser    Tracker        │     │
│  │    Claude fallback)                                         │     │
│  └────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Subsystems and Relationships

| Subsystem | Depends On | Publishes | Subscribes To |
|-----------|-----------|-----------|---------------|
| Camera Pipeline | Hardware (webcam) | CAMERA_FRAME | — |
| Person Detector | Camera Pipeline | PERSON_DETECTED, PERSON_LOST | — |
| Face Recognizer | Person Detector | FACE_RECOGNIZED | PERSON_DETECTED |
| Audio Pipeline | Hardware (INMP441) | SOUND_DETECTED, SPEECH | — |
| Sensor Loop | Hardware (all) | LIGHT_CHANGED, TOUCH, CLIFF, etc. | — |
| State Machine | Event Bus | STATE_CHANGED | All state-changing events |
| Personality Engine | Event Bus, Memory | MOOD_CHANGED | All perception events |
| Behavior Engine | SM + Personality | Motor/Servo/Display commands | STATE_CHANGED, MOOD_CHANGED |
| Expression System | Behavior Engine | — | BEHAVIOR_CHANGED |
| Cognition/LLM | Memory, Personality | RESPONSE_READY | SPEECH_DETECTED, WAKE_WORD |
| Memory System | — | — | Significant events (stored) |

---

## Process Architecture

```
main.py (single asyncio process)
│
├── asyncio event loop
│   ├── Task: event_bus._dispatch_loop
│   ├── Task: camera._capture_loop  (reads frames via executor)
│   ├── Task: person_detector._detect_loop
│   ├── Task: personality._tick_loop
│   ├── Task: sensor_loop (polls hardware sensors)
│   ├── Task: behavior_engine._behavior_loop
│   └── Task: main._run_loop (heartbeat + housekeeping)
│
└── ThreadPoolExecutor (for blocking operations)
    ├── camera._read_frame()       (OpenCV is not async)
    ├── person_detector._run_detection()  (YOLO inference)
    ├── episodic_memory._*_sync()  (SQLite I/O)
    └── llm._call_ollama()         (HTTP to Ollama)
```

**Why single process?** The Pi 5 has 4 cores. Multiprocessing would require
serializing numpy arrays between processes (expensive). asyncio + executor
threads give us concurrent I/O + parallel CPU tasks in one process with
shared memory.

---

## Message Bus Design

The event bus (`core/event_bus.py`) is the central nervous system.

**Priority levels:**
1. `SAFETY (0)` — cliff, obstacle, pickup, battery critical → dispatched immediately
2. `HIGH (1)` — touch, wake word, face recognized → fast response needed
3. `NORMAL (2)` — motion, sound, state changes
4. `LOW (3)` — camera frames, idle ticks, ambient updates

**Key design decisions:**
- All handlers are `async def` — no blocking in event handlers
- Dead letter queue captures unhandled events for debugging
- Event history (30s TTL) lets late subscribers catch up
- `publish_sync()` for hardware ISRs that run in threads

---

## State Machine Overview

```
SAFE_MODE ← (always reachable from any state, forced)
SLEEPING ↔ IDLE
IDLE
  ├── IDLE_CURIOUS   (bored, exploring)
  ├── IDLE_CALM      (default, resting)
  └── IDLE_BORED     (needs attention)
ALERT
  ├── ALERT_PERSON   (30s timeout → IDLE_CURIOUS)
  ├── ALERT_SOUND    (10s timeout → IDLE_CURIOUS)
  └── ALERT_MOTION   (15s timeout → IDLE_CURIOUS)
INTERACTIVE
  ├── LISTENING      (15s timeout → IDLE_CALM)
  ├── PROCESSING     (20s timeout → IDLE_CALM)
  ├── RESPONDING
  └── PLAYING
NAVIGATING
  ├── WANDERING
  ├── APPROACHING
  ├── RETREATING
  └── AVOIDING
EXPRESSING
  ├── HAPPY (5s), EXCITED (8s), CONFUSED (6s)
  ├── SAD (10s), SCARED (4s), PLAYFUL (10s)
  └── all timeout → IDLE_CALM
```

**History states:** When returning to IDLE, the system restores the last
active IDLE substate (e.g., if interrupted during IDLE_CURIOUS, returns there).

---

## Memory System Design

```
┌─────────────────────────────────────────────────────┐
│              THREE-TIER MEMORY ARCHITECTURE          │
│                                                     │
│  Tier 1: Working Memory (in-RAM)                    │
│  ┌─────────────────────────────────┐                │
│  │ TTL-based key-value store       │                │
│  │ Current conversation context    │                │
│  │ Last 200 events (deque)         │                │
│  │ Active sensor readings          │                │
│  │ Fast: O(1) read/write           │                │
│  └─────────────────────────────────┘                │
│           ↕ (write significant events)              │
│  Tier 2: Episodic Memory (SQLite)                   │
│  ┌─────────────────────────────────┐                │
│  │ ~/.robot/memory/episodic.db     │                │
│  │ Interactions with persons       │                │
│  │ Emotional valence per episode   │                │
│  │ Importance-weighted retrieval   │                │
│  │ Forgetting curve (0.05/day)     │                │
│  └─────────────────────────────────┘                │
│           ↕ (informs room context)                  │
│  Tier 3: Spatial Memory (JSON)                      │
│  ┌─────────────────────────────────┐                │
│  │ ~/.robot/memory/spatial.json    │                │
│  │ Room fingerprints (lux, color)  │                │
│  │ Landmark locations              │                │
│  │ Obstacle map (temp + persist)   │                │
│  └─────────────────────────────────┘                │
└─────────────────────────────────────────────────────┘
```

---

## Hardware Abstraction Layer Design

Every hardware module inherits from `HardwareInterface` (ABC).
At startup, the system checks `is_available` and automatically
selects real or mock implementation.

```python
# Hardware selection (happens in main.py or subsystem init)
if sensor.is_available and not cfg.simulation_enabled():
    light = BH1750Real()   # real I2C hardware
else:
    light = MockBH1750()   # realistic simulation
```

**Mock system guarantees:**
- Gaussian noise on all values (configurable noise_level in hardware.yaml)
- Simulated I2C/GPIO latency
- Failure injection API for testing error paths
- All mocks implement same interface as real hardware

---

## Local vs Cloud Inference

```
┌──────────────────────────────────────────────────────────┐
│                    INFERENCE BOUNDARY                     │
│                                                          │
│  LOCAL (stays on Pi, always):                            │
│  ├── Person detection (YOLOv8n, CPU)                    │
│  ├── Face recognition (OpenCV LBPH)                     │
│  ├── Motion detection (OpenCV frame diff)               │
│  ├── LLM: Ollama llama3.2:1b or gemma2:2b              │
│  ├── Wake word (OpenWakeWord)                           │
│  ├── STT (faster-whisper tiny.en)                       │
│  ├── TTS (Piper)                                        │
│  └── ALL FACE DATA (never leaves device)                │
│                                                          │
│  CLOUD (optional fallback, requires internet):           │
│  ├── LLM: Claude Haiku (if Ollama unavailable)         │
│  ├── LLM: GPT-4o-mini (second fallback)                │
│  └── Anonymized telemetry (no PII, opt-in)             │
└──────────────────────────────────────────────────────────┘
```

---

## Privacy Architecture

1. **Face data** — stored in `~/.robot/models/face_recognizer.yml` (local only)
2. **Audio** — never recorded/stored raw. VAD detects speech, Whisper transcribes locally.
3. **Conversation logs** — stored locally in episodic memory with emotional valence.
   No cloud logging of conversation content.
4. **Telemetry** — system metrics only (CPU, events/sec). No behavioral data in telemetry.
5. **User control** — `python3 tools/memory_browser.py` to view/delete any memory.

---

## Safety Constraint System

Safety constraints run at the event bus level, **before** any behavioral processing:

```python
# In main.py _wire_handlers():
@bus.on(EventType.CLIFF_DETECTED)
async def on_cliff(event):
    # This fires before any other handler because priority=SAFETY
    await sm.transition_to(RobotState.SAFE_MODE, force=True)
    # force=True bypasses all guard conditions
```

**Non-overridable safety rules:**
1. `CLIFF_STOP` — any cliff sensor → SAFE_MODE (motors off)
2. `OBSTACLE_STOP` — distance < 5cm → SAFE_MODE
3. `PICKUP_STOP` — IMU pickup detected → motors off
4. `BATTERY_STOP` — LiPo < 6.8V → SAFE_MODE
5. `THERMAL_STOP` — Pi temp > 82°C → reduce processing
6. `MOTOR_GUARD` — motors cannot run >30s without sensor update
7. `PRIVACY_GUARD` — face data cannot be exported or transmitted

---

## Logging and Telemetry

- **Structured JSON logs** → `~/.robot/logs/cosmo.log` (rotating, 10MB × 5)
- **Console output** → colored key=value format via structlog
- **Telemetry counters** → in-memory, readable via `telemetry.snapshot()`
- **Grafana/Prometheus** — future integration point (telemetry module prepared)

---

## Testing Strategy

| Layer | Tool | Coverage Target |
|-------|------|-----------------|
| Unit | pytest + pytest-asyncio | 80% of business logic |
| Integration | pytest with mock hardware | All subsystem boundaries |
| Simulation | Full system with MockHardwareRegistry | Regression + performance |
| Hardware-in-loop | Pytest marked @hardware | Sensor validation, motor timing |

**Running tests:**
```bash
# Unit only (no hardware needed)
cd ~/robot && python3 -m pytest tests/unit/ -v

# Integration (mock hardware)
python3 -m pytest tests/integration/ -v

# Full simulation
python3 -m pytest tests/simulation/ -v

# Hardware required
python3 -m pytest tests/hardware/ -v -m hardware
```
