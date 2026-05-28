# CLAUDE.md — COSMO ROBOT · CLAUDE CODE MASTER DIRECTIVE
**Project:** Cosmo Autonomous Pet Robot  
**Owner:** Madhan Krishnamadhan  
**Platform:** Raspberry Pi 5 8GB · Bangalore, India  
**Repo:** https://github.com/krishnamadhan/robot  
**SSH (LAN):** `pi@192.168.1.200` · **SSH (Tailscale):** `pi@100.101.250.126`  

> Before doing anything, read `CLAUDE_SESSION_PROTOCOL.md` and `docs/PROJECT_STATE.md`.

---

## ⚡ BOOT PROTOCOL — MANDATORY SEQUENCE EVERY SESSION

Run these steps in order. Do not skip. Do not start coding before step 8.

```bash
# Step 1 — Read all state files first
cat ~/robot/docs/PROJECT_STATE.md
cat ~/robot/docs/KNOWN_ISSUES.md
cat ~/robot/docs/NEXT_SESSION.md

# Step 2 — Check what's running
pm2 status

# Step 3 — Check recent logs for crashes
pm2 logs cosmo_demo --lines 30 --nostream
pm2 logs battery-monitor --lines 10 --nostream

# Step 4 — Verify hardware is up
sudo i2cdetect -y 1
vcgencmd measure_temp
vcgencmd get_throttled
free -h

# Step 5 — Check git status
cd ~/robot && git log --oneline -5 && git status

# Step 6 — Read KNOWN_ISSUES.md again carefully
# Cross-check what you're about to work on against known failures

# Step 7 — Check for sensor drift or new hardware
pinctrl get | grep -E "GPIO(5|6|8|13|16|17|18|19|20|21|22|23|24|25|26|27)"

# Step 8 — Only now begin work, resuming from NEXT_SESSION.md
```

**Rule:** If PM2 is running, never restart `banteragent` — it will lose the WhatsApp session auth.

---

## 🏠 PROJECT ROOT LAYOUT

```
~/robot/
├── CLAUDE.md                    ← THIS FILE — master boot directive
├── docs/
│   ├── PROJECT_STATE.md         ← Session continuity: what's done, in progress, next
│   ├── NEXT_SESSION.md          ← Single-focus task handoff for next session
│   ├── KNOWN_ISSUES.md          ← Bug log + failed approaches (read before touching code)
│   ├── DECISIONS.md             ← Architecture decision log
│   ├── PERFORMANCE_LOG.md       ← CPU/RAM measurements per component
│   └── HARDWARE_NOTES.md        ← Wiring notes, I2C map, voltage rules
├── main.py                      ← Entry point
├── tools/
│   └── cosmo_demo.py            ← Main PM2 entry point (pm2 start ecosystem.config.js)
├── core/
│   ├── event_bus.py             ← Async pub/sub, 4 priority levels
│   ├── personality.py           ← mood/energy/arousal/attachment decay engine
│   ├── state_machine.py         ← 30-state hierarchical HSM
│   └── memory/
│       ├── episodic.py          ← SQLite long-term (IMPLEMENTED + WORKING)
│       ├── working.py           ← RAM short-term, 5min TTL (WORKING)
│       └── spatial.py           ← Room fingerprints, JSON (WORKING)
├── cognition/
│   ├── mind.py                  ← Two-tier brain, Claude calls, rate limiting
│   ├── conversation.py          ← Per-person thread cache, 10min expiry
│   ├── llm.py                   ← Ollama-first, Claude Haiku fallback
│   └── intent.py                ← Offline Tanglish command patterns (15+)
├── perception/
│   ├── vision/
│   │   ├── vision_loop.py       ← Parallel 4-loop pipeline (WORKING)
│   │   ├── person.py            ← YOLOv8n person detection (WORKING)
│   │   ├── face.py              ← SFace recognition (WORKING — Indhu confidence low)
│   │   └── emotion.py           ← DeepFace 7-emotion (WORKING)
│   └── audio/
│       ├── mic.py               ← INMP441 I2S capture
│       ├── wake_word.py         ← OpenWakeWord ("hey_jarvis" — see KNOWN ISSUES)
│       ├── vad.py               ← webrtcvad 30ms frames
│       ├── stt.py               ← faster-whisper base.en, beam=5
│       └── pipeline.py          ← Full audio loop (WORKING)
├── behavior/
│   ├── engine.py                ← Curiosity + 30s ambient loop (WORKING)
│   ├── navigation.py            ← Wander/approach/retreat (MOCK — Phase 2)
│   └── idle.py                  ← Idle behaviour generator
├── expression/
│   ├── eyes.py                  ← SSD1306 OLED driver + 12 expressions
│   │                              (terminal render mode — OLED not yet wired)
│   ├── speech.py                ← Piper streaming TTS → JBL Flip 5
│   └── sounds.py                ← Numpy mood-gated tones
├── hardware/
│   ├── motors.py                ← TB6612FNG driver (MOCK mode — XT60 pigtail pending)
│   ├── sensor_manager.py        ← Unified sensor polling
│   └── sensors/                 ← Individual sensor drivers
├── config/
│   ├── hardware.yaml            ← GPIO pins, I2C addresses, available: true/false flags
│   ├── personality.yaml         ← Trait values, phrase banks
│   └── models.yaml              ← AI model paths + config
└── ecosystem.config.js          ← PM2 config (single source of truth for startup)
```

---

## 🤖 CURRENT STATUS — PHASE 1.5 IN PROGRESS

### Phase completion map
| Phase | Name | Status |
|---|---|---|
| 0 | Foundation (event bus, state machine, memory, personality) | ✅ **COMPLETE** |
| 1 | Perception + Voice (camera, face, emotion, wake→TTS pipeline) | ✅ **COMPLETE** |
| 1.5 | Sensor Integration + OLED eyes | 🔧 **IN PROGRESS — CURRENT SPRINT** |
| 2 | Real Movement + Navigation | ⏳ Blocked on XT60 pigtail arrival |
| 3 | Servo + Camera Pan-Tilt | ⏳ Hardware on order |
| 4 | Room Mapping + Wake Word | 📅 Planned |
| 5 | Polish + Advanced AI | 📅 Planned |

### What is working right now
- ✅ Cosmo runs as PM2 `cosmo_demo` — event bus, personality, state machine live
- ✅ Face recognition — Madhan 85-97%, Indhu 75% (needs re-enroll)
- ✅ Emotion detection → personality engine
- ✅ Wake word "Hey Jarvis" → voice conversation → JBL Flip 5
- ✅ Episodic memory — Cosmo remembers and references past conversations
- ✅ Streaming TTS — first word ~1.5s, full reply ~2-3s
- ✅ Parallel vision pipeline — 15 FPS detection, 4 FPS recognition, 2 FPS emotion
- ✅ Context-aware proactive speech + curiosity ambient loop
- ✅ Conversation thread continuity per person (10min window)
- ✅ Battery monitoring via UPS HAT at I2C 0x36
- ✅ BH1750 tested standalone (not yet in event bus)
- ✅ MPU-6050, PIR, touch tested standalone (not yet enabled)

### What is assembled but not yet enabled in software
- OLED left eye 0x3C + right eye 0x3D (hardware arrived — wire now)
- BH1750 light sensor (I2C 0x23 — enable in hardware.yaml)
- PIR motion sensor GPIO8 (enable in hardware.yaml)
- TTP223 touch × 4: GPIO5/25/4/7 (enable in hardware.yaml)
- MPU-6050 gyro I2C 0x68 (enable in hardware.yaml)
- HC-SR04 ultrasonic GPIO16/24 (mock in software — enable when confident)
- TB6612FNG motors (mock mode — waiting for XT60 pigtail)

---

## 🔩 HARDWARE REFERENCE

### Compute
| Component | Detail |
|---|---|
| SBC | Raspberry Pi 5 8GB |
| OS | Raspberry Pi OS 64-bit Debian Trixie |
| Cooling | Official Pi 5 Active Cooler (on top) |
| Pi Power | DFRobot FIT0992 UPS HAT (pogo pins, 4× 18650 cells, I2C 0x36) |
| UPS Charger | FEDUS 12V 3A DC adapter (5.5×2.1mm barrel) |

### GPIO Map (BCM numbering — always BCM, never physical pin)
```
GPIO4  → DEAD PIN — do not use (confirmed bad on this unit, 2026-05-28)
GPIO5  → TTP223 Touch HEAD
GPIO6  → TB6612FNG BIN2 (right motor backward) ← CONFLICT FIXED (see KNOWN_ISSUES)
GPIO7  → DEAD PIN — do not use (confirmed bad on this unit, 2026-05-28)
GPIO8  → HC-SR501 PIR OUT (3.3V direct)
GPIO10 → TB6612FNG left_rear BIN2 (direction 2)
GPIO11 → TB6612FNG left_front PWM (SW PWM — remapped from GPIO12, Pin 32 confirmed faulty on this unit)
GPIO12 → DEAD PIN — do not use (Pin 32 faulty on this Pi 5 unit, confirmed 2026-05-28)
GPIO13 → TB6612FNG left_rear PWM (SW PWM — shares HW PWM1 with GPIO19, never use dtoverlay)
GPIO16 → HC-SR04 TRIG — DISABLED: FIT0992 HAT uses GPIO16 for charge control
GPIO17 → TB6612FNG left_front AIN1 (direction 1)
GPIO5  → DEAD PIN — do not use (confirmed bad on this unit, 2026-05-28)
GPIO24 → TB6612FNG right_front AIN2 (direction 2) — remapped: GPIO21 dead→GPIO14 UART→GPIO5 dead→GPIO24
GPIO18 → TB6612FNG right_front PWM (SW PWM)
GPIO19 → DEAD PIN — do not use (Pin 35 faulty on this Pi 5 unit, confirmed 2026-05-28)
GPIO20 → TB6612FNG right_front AIN1 (direction 1)
GPIO21 → DEAD PIN — do not use (Pin 40 faulty on this Pi 5 unit, confirmed 2026-05-28)
GPIO22 → TB6612FNG left_front AIN2 (direction 2)
GPIO23 → TB6612FNG left_rear BIN1 (direction 1)
GPIO24 → HC-SR04 ECHO (via LLC ch1, 5V→3.3V)
GPIO25 → TB6612FNG right_rear BIN1 (direction 1)
GPIO26 → TB6612FNG right_rear BIN2 (direction 2)
GPIO27 → TB6612FNG STBY (LOW at boot always — HIGH only after self-test)
```

### I2C Bus (GPIO2 SDA / GPIO3 SCL — shared)
```
0x10 → DFRobot UPS HAT battery IC
0x23 → BH1750 Light Sensor (ADDR pin → GND)
0x36 → UPS HAT main IC
0x39 → APDS-9960 Gesture (replacement unit on order — keep available: false)
0x3C → OLED Left Eye SSD1306 (hardware arrived — wire now)
0x3D → OLED Right Eye SSD1306 (A0 pad must be bridged — wire now)
0x40 → PCA9685 Servo Driver (hardware on order)
0x68 → MPU-6050 Gyro/Accel (AD0 pin → GND)
```

### Motor Driver Truth Table (CRITICAL — do not violate)
```
AIN1=1, AIN2=0, PWMA=X%  → Left Forward
AIN1=0, AIN2=1, PWMA=X%  → Left Backward
AIN1=0, AIN2=0, PWMA=0   → Left Brake
AIN1=1, AIN2=1, ANY       → ❌ PROHIBITED — destroys TB6612FNG
STBY must be LOW at boot. Raise HIGH only after sensor self-test.
Always set OFF pin LOW before ON pin HIGH — prevents both-HIGH glitch.
```

### ⚡ Power Safety Rules (violating these destroys hardware)
1. LiPo 7.4V → TB6612FNG VM terminal ONLY — never to Pi 5V rail
2. Pi 3.3V → TB6612FNG VCC (logic) — NOT 5V
3. Pi GND + LiPo GND share common ground at TB6612FNG GND — intentional
4. All Pi 5 GPIO pins are 3.3V max — no exceptions
5. 470µF cap across TB6612FNG VM+GND (motor surge)
6. 220µF caps per motor terminal pair (back-EMF)

### ⛔ FIT0992 UPS HAT — RESERVED GPIO (never assign to anything else)
These pins are used by the HAT internally. Driving them from robot code destroys the HAT converter IC.
| GPIO | Pin | HAT function | What happens if driven |
|------|-----|--------------|----------------------|
| GPIO6 | Pin 31 | Adapter-fail detect | LOW = HAT thinks 12V adapter gone → battery boost → converter overheats and burns |
| GPIO16 | Pin 36 | Charging control | HIGH = disables charging circuit |

**This was the root cause of 5 burned chips.** GPIO6 was assigned to left_rear BIN2, which went LOW on every forward command.

---

## 🧠 SOFTWARE STACK

| Layer | Technology |
|---|---|
| Language | Python 3.13 |
| Process manager | **PM2** (not systemd) |
| GPIO | gpiozero (primary), RPi.GPIO fallback |
| I2C | smbus2 |
| Async | asyncio throughout |
| Config | YAML via config/ directory |

### PM2 Processes
```
cosmo_demo      — Robot brain — MAIN PROCESS (~/robot/)
banteragent     — WhatsApp bot (Node.js, ~/banteragent/) — ⚠️ NEVER RESTART
pi-monitor      — Health daemon (~/pi-monitor/)
battery-monitor — UPS HAT monitoring
```

### AI Models on Pi
| Model | Purpose | Size | Status |
|---|---|---|---|
| YOLOv8n | Person detection | 6MB | ✅ 32 FPS at 320×240 |
| faster-whisper base.en | STT | 74MB | ✅ ~1.5s, beam=5, Indian English |
| SFace ONNX | Face recognition | 85MB | ✅ Madhan 85-97%, Indhu 75% |
| Piper en_US-lessac-medium | Neural TTS | 61MB | ✅ 1.8s generation |
| Ollama llama3.2:1b | Local LLM fallback | 1.3GB | ✅ 51s cold, 5-8s warm |
| DeepFace/FER | Emotion detection | 35MB | ✅ 7 emotions |
| OpenWakeWord hey_jarvis | Wake word | — | ✅ ~80ms (not hey_cosmo — see KNOWN_ISSUES) |

### Two-Tier Brain (cost-critical — respect rate limits)
```
TIER 1 — Rule Engine (free, every 5s, no API calls)
  Sensor readings → direct behaviour (stop if cliff, wander if alone, etc.)

TIER 2 — Claude API (paid, event-triggered, rate-limited)
  Model:          claude-haiku-4-5-20251001
  Daily budget:   100,000 tokens HARD LIMIT — enforce via mind.py
  Cooldowns:      face_seen=45s, emotion=60s, alone=180s
  Voice toggle:   "mind off" / "mind on"
```

### Audio Pipeline
```
INMP441 I2S mic
  → OpenWakeWord (hey_jarvis, ~80ms detection)
  → webrtcvad (30ms frames, VAD gating)
  → faster-whisper base.en (STT, ~1.5s, beam=5)
  → intent.py (offline Tanglish patterns, 15+) OR claude-haiku-4-5
  → Piper TTS (neural, sentence-by-sentence streaming)
  → ffmpeg (22050→44100Hz stereo)
  → pw-play → PipeWire → JBL Flip 5
End-to-end latency: ~2-3s (was 7-9s before streaming TTS)
```

### Vision Pipeline (Parallel — do not break this)
```
Logitech C920 USB /dev/video0
  capture_loop: 30 FPS
  detection_loop: 15 FPS  → YOLOv8n person bbox
  recognition_loop: 4 FPS → SFace face ID
  emotion_loop: 2 FPS     → DeepFace 7 emotions
  → event_bus → personality → behavior → expression → speech
```

---

## 🔧 CURRENT SPRINT: PHASE 1.5 — SENSOR INTEGRATION

### Tasks by priority (work top-down)

**P0 — Wire OLED eyes (biggest visual impact, hardware arrived)**
```bash
# After wiring both OLEDs:
sudo i2cdetect -y 1   # Must show 0x3C AND 0x3D
# Then in expression/eyes.py:
#   change: eye_engine.set_render_target("terminal")
#   to:     eye_engine.set_render_target("oled")
```

**P1 — Enable real sensors in config/hardware.yaml (one by one)**
```bash
# Enable order: BH1750 → PIR → touch × 4 → MPU-6050 → sound/vibration
# After each: pm2 restart cosmo_demo && pm2 logs cosmo_demo -f | grep sensor
# Watch for: GPIO conflicts, I2C errors, crash loops
```

**P2 — Wire XT60 pigtail → LiPo → TB6612FNG VM (when pigtail arrives)**
```bash
# Before first LiPo test:
# 1. Confirm 470µF cap on VM+GND
# 2. Confirm 220µF caps on motor terminals
# 3. Set STBY LOW in motors.py before powering
# 4. Test at 30% speed first
# 5. Log result in docs/PERFORMANCE_LOG.md
```

**P3 — Re-enroll Indhu face**
```bash
rm -rf ~/.robot/memory/faces/indhu/
python3 tools/enroll_face.py --name "Indhu" --samples 20
# Good lighting, 5 angles, no glasses, distance ~80cm
```

**P4 — Health endpoint port 8081**
```python
# Add FastAPI endpoint to pi-monitor or new service
# GET /health → JSON: {pm2_status, temps, ram, battery_pct, face_recognition_active}
# Access from phone: http://100.101.250.126:8081/health
```

---

## 🤖 HOW TO USE CLAUDE CODE FEATURES — OPERATING RULES

### Sub-agents — use aggressively for parallel work

Claude Code sub-agents are the most powerful feature for this project. Use them whenever tasks are independent.

**When to spawn sub-agents:**
```
Implementing two sensor drivers at once:
  → Agent 1: implement BH1750 integration + personality hooks
  → Agent 2: implement PIR + touch sensor integration
  → Parent: merge, test both, update PROJECT_STATE.md

Debugging while building:
  → Agent 1: reproduce and isolate the crash
  → Agent 2: write the new feature assuming fix will land
  → Parent: integrate when fix is confirmed

Writing tests while writing code:
  → Agent 1: implement the feature
  → Agent 2: write unit tests for it in parallel
  → Parent: run tests, iterate
```

**Sub-agent prompt template:**
```
Sub-agent task: [specific task]
Context files to read first: [list]
Constraints: [list any hardware safety rules relevant]
Output: [what to produce — file, patch, test, etc.]
Do NOT touch: [banteragent/, any working vision pipeline]
Report back: what you changed, what still needs doing
```

### Memory files — how continuity works across sessions

At the end of every session, Claude Code MUST update these files:

| File | What to write |
|---|---|
| `docs/PROJECT_STATE.md` | Full current sprint state, component status table |
| `docs/NEXT_SESSION.md` | Single focused task for the next session to start with |
| `docs/KNOWN_ISSUES.md` | Any new bugs found or failed approaches |
| `docs/DECISIONS.md` | Any architecture choices made this session with rationale |
| `docs/PERFORMANCE_LOG.md` | Any new CPU/RAM measurements |

Then commit:
```bash
cd ~/robot
git add docs/ config/ && git commit -m "session: [brief description of what changed]"
git push origin master
```

### Todo tracking — use within every session

Use Claude Code's built-in todo list to track the session's tasks. Map them to the sprint defined in `docs/PROJECT_STATE.md`. Mark done before session end.

### Bash tool — preferred diagnostic patterns
```bash
# Health snapshot
pm2 status && free -h && vcgencmd measure_temp && vcgencmd get_throttled

# I2C scan
sudo i2cdetect -y 1

# Live cosmo logs
pm2 logs cosmo_demo -f

# Last N log lines (no stream)
pm2 logs cosmo_demo --lines 50 --nostream

# Sensor monitor (rich dashboard)
python3 tools/sensor_monitor.py

# Audio calibration
python3 tools/audio_calibrate.py

# Restart safely (never restart banteragent)
pm2 restart cosmo_demo

# GPIO state
pinctrl get
```

---

## 🧬 BEHAVIOUR MODEL — ENGINEERING REFERENCE

### Personality state vector (not a simple mood)
```python
@dataclass
class PersonalityState:
    mood:       float  # -1.0 (miserable) → +1.0 (joyful)
    energy:     float  # 0.0 (exhausted)  → 1.0 (hyper)
    arousal:    float  # 0.0 (calm)       → 1.0 (stimulated)
    attachment: float  # 0.0 (distant)    → 1.0 (bonded)
```
These decay over time via `personality.py`. Inputs from sensors, faces, touch, voice.

### Emotional state → behaviour output
```
HIGH energy + HIGH arousal + person present → approach, excited eyes, fast speech
LOW energy + LOW mood + alone → wander slowly, droopy eyes, ambient sounds
HIGH attachment + known face → rush toward, rapid tail-wag sound, happy eyes
LOW energy (battery<20%) → slow movement, sleepy eyes, minimal speech
HIGH arousal + unexpected noise → ALERT state, quick turn, scan eyes
```

### Relationship profiles (per person in episodic.py)
```python
@dataclass
class PersonProfile:
    person_id:            str         # SFace embedding ID
    name:                 str | None
    familiarity_score:    float       # 0.0 → 1.0 (time-weighted interaction history)
    attachment_level:     float       # 0.0 → 1.0 (positive interaction history)
    interaction_count:    int
    last_seen:            datetime
    tone_history:         list[str]   # rolling window of "positive"/"neutral"/"negative"
    emotional_associations: dict      # {"happy": 12, "surprised": 3}
    trust_score:          float
```
Madhan = highest attachment, Indhu = high attachment, strangers = cautious mode.

### Sleep cycle
```
Active:  07:00 → 00:00 — full behaviour, proactive speech, exploration
Sleep:   00:00 → 07:00 — minimal movement, silent, low power, emergency only
```

---

## ⚠️ KNOWN ISSUES SUMMARY (full detail in docs/KNOWN_ISSUES.md)

| ID | Issue | Status | Workaround |
|---|---|---|---|
| KI-001 | Wake word is "Hey Jarvis" not "Hey Cosmo" | Open | System maps internally — custom training needed |
| KI-002 | Indhu face confidence ~75% (Madhan 85-97%) | Open | Re-enroll with 20 samples, good lighting |
| KI-003 | LiPo XT60 pigtail not arrived | Pending delivery | Using 4× AA 6V for motor testing |
| KI-004 | APDS-9960 original faulty — replacement ordered | Pending delivery | keep `available: false` in hardware.yaml |
| KI-005 | GPIO6 conflict — battery_monitor vs BIN2 | Fixed in code | Verify fix is committed before touching either file |
| KI-006 | STT picks up background noise | Partially fixed | Tune `audio_calibrate.py`, beam=5 helps |
| KI-007 | Ollama cold start 51s | Mitigated | keep_alive: 2h, Claude Haiku is primary |
| KI-008 | Personality trait drift not implemented | Deferred to Phase 5 | Not urgent — implement after hardware complete |
| KI-009 | OLED eyes in terminal render mode (not on hardware) | In progress | Wire OLEDs — this session |

---

## 🚫 DO NOT EVER

- **Never restart `banteragent` PM2 process** — loses WhatsApp auth session permanently
- **Never delete `~/banteragent/.wwebjs_auth/`** — same reason
- **Never set AIN1=1 AND AIN2=1 simultaneously** — destroys TB6612FNG
- **Never connect LiPo to Pi 5V rail** — destroys Pi 5 (₹7,500+)
- **Never apply 5V to any Pi GPIO pin** — no 5V-tolerant pins on Pi 5
- **Never run vision inference in a tight loop** — gate with person-present signal
- **Never make Claude API calls in a tight loop** — 100K token daily budget is hard limit
- **Never hardcode GPIO pin numbers** — always read from `config/hardware.yaml`
- **Never skip updating docs/ state files at session end** — breaks continuity
- **Never repeat an approach already marked FAILED in docs/KNOWN_ISSUES.md**

---

## 📦 HARDWARE ARRIVING SOON (check before implementing)

| Item | Purpose | Action when arrives |
|---|---|---|
| XT60 female pigtail | Connect LiPo to TB6612FNG VM | Enable real motor driver in motors.py |
| APDS-9960 replacement | Gesture + proximity sensor | Set `available: true` in hardware.yaml, test I2C 0x39 |
| PCA9685 16-ch servo driver | Pan-tilt camera + ultrasonic servo | Phase 3 kick-off |
| MG90S × 3 | Camera pan, tilt, ultrasonic rotate | Phase 3 |
| Pan-tilt bracket | Camera mounting | Phase 3 |
| TCRT5000 cliff sensors × 2 | Cliff detection | Enable GPIO20/21 after XT60 pigtail |
| KY-038 sound sensor | Sound detection | Enable GPIO19 after voltage divider check |
| 470µF caps + 220µF caps | Motor noise protection | Install before first LiPo motor test |
| PAM8403 amp + 3W 4Ω speaker | Replace JBL BT dependency | Phase 5 |
| 5-channel IR line sensor | Line following mode | Phase 4 fun feature |

---

## 🏪 COMPONENTS — LOCAL EMERGENCY SOURCE

**Rishab Electronics** — Marathahalli Main Rd, ~3.5km from Sobha Dream Acres  
📞 080-5005-7390 · Open 9:30AM–9:30PM daily · 4.5⭐  
Sells: resistors, caps, wires, breadboards — same day

---

## 📋 SESSION END CHECKLIST

Before ending any session:

- [ ] Updated `docs/PROJECT_STATE.md` with current component status table
- [ ] Wrote `docs/NEXT_SESSION.md` with single focused next task
- [ ] Appended any new bugs/failures to `docs/KNOWN_ISSUES.md`
- [ ] Logged any architecture decisions to `docs/DECISIONS.md`
- [ ] Logged any performance measurements to `docs/PERFORMANCE_LOG.md`
- [ ] Ran `pm2 status` — no processes in error/crash state
- [ ] Ran `pm2 logs cosmo_demo --lines 10 --nostream` — no crash loop
- [ ] Committed and pushed: `git add . && git commit -m "session: ..." && git push`
- [ ] Verified `banteragent` is still running (`pm2 status | grep banteragent`)

---

## 🎯 FULL BUILD ROADMAP

### Phase 1.5 — Sensor Integration (CURRENT)
- [ ] Wire OLED eyes (0x3C + 0x3D) — hardware here
- [ ] Enable BH1750 → sleepy when dark, active when bright
- [ ] Enable PIR → ALERT state on motion
- [ ] Enable TTP223 touch × 4 → mood boost, purr sound
- [ ] Enable MPU-6050 → pickup detection → motors off + surprised eyes
- [ ] Re-enroll Indhu face (20 samples, good lighting)
- [ ] Deploy health endpoint on port 8081
- [ ] Enable HC-SR04 ultrasonic (after XT60 pigtail + caps installed)
- [ ] Enable TCRT5000 cliff sensors

### Phase 2 — Real Movement + Navigation
- [ ] XT60 pigtail wired → LiPo to TB6612FNG VM
- [ ] 470µF + 220µF caps installed
- [ ] motors.py production mode (switch from mock)
- [ ] Motor calibration (LEFT_TRIM / RIGHT_TRIM for drift)
- [ ] Wander when alone → stop when person detected
- [ ] Obstacle avoidance (HC-SR04 → movement_controller)
- [ ] Cliff detection guard (TCRT5000 → motor stop)
- [ ] Vision → motor integration (approach tracked person)

### Phase 3 — Servo + Camera Tracking
- [ ] PCA9685 at I2C 0x40 (hardware arrives)
- [ ] Camera pan servo (MG90S ch0)
- [ ] Camera tilt servo (MG90S ch1)
- [ ] Face tracking via pan-tilt (track detected person bbox)
- [ ] Ultrasonic rotate servo (MG90S ch2) — 180° scan

### Phase 4 — Room Mapping + Voice Quality
- [ ] Room identification via visual fingerprinting
- [ ] Spatial memory integration (spatial.py hooks)
- [ ] Custom "hey_cosmo" wake word (Picovoice or OWW training)
- [ ] Upgrade Ollama to llama3.2:3b for better offline LLM
- [ ] Line following mode (5-ch IR sensor)

### Phase 5 — Polish + Advanced
- [ ] Personality trait drift (evolves over weeks of interaction)
- [ ] Web management dashboard (camera stream + status + face enroll)
- [ ] PAM8403 + 3W speaker (remove JBL BT dependency)
- [ ] Google Coral USB Accelerator (faster local face recognition)
- [ ] Second LiPo for hot-swap charging
- [ ] WhatsApp admin commands via BanterAgent integration

---

*This file governs every Claude Code session on the Cosmo project.*  
*Update the version line and roadmap as phases complete.*  
*Never delete sections — evolve them.*  
*Last updated: May 2026 · Phase 1.5 active*
