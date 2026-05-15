# Cosmo — Home Robot

A small wheeled robot that lives with Madhan and Indhu in their Bangalore apartment. Cosmo has a personality — naughty Tamil kid, speaks Tanglish, reacts to faces, emotions, voice, and environment.

Built on Raspberry Pi 5. All code runs on-device.

---

## Quick Start

```bash
pm2 start cosmo       # start
pm2 stop cosmo        # stop
pm2 restart cosmo     # restart after code changes
pm2 logs cosmo        # live logs
```

Logs: `~/.robot/logs/cosmo-out.log`, `cosmo-error.log`
Config: `config/hardware.yaml`, `config/models.yaml`, `config/personality.yaml`
Env: `.env` (ANTHROPIC_API_KEY, PICOVOICE_KEY)

---

## Project Structure

```
robot/
├── main.py              ← entry point
├── config/              ← all tunable params (YAML, no code changes needed)
│   ├── hardware.yaml    ← GPIO pins, I2C addresses, mock/real per sensor
│   ├── models.yaml      ← AI model configs (YOLO, LLM, STT, TTS)
│   ├── personality.yaml ← Cosmo's traits, mood decay rates
│   └── thresholds.yaml  ← sensor thresholds
├── core/
│   ├── event_bus.py     ← async pub/sub (all inter-module comms go here)
│   ├── state_machine.py ← behavioral states (idle/active/sleeping/exploring)
│   ├── personality.py   ← mood/energy/arousal state, decays over time
│   └── memory/          ← episodic (SQLite) + working (RAM)
├── hardware/
│   ├── motors.py        ← TB6612FNG + software PWM
│   └── sensor_manager.py ← aggregates all sensors, mock fallback
├── perception/
│   ├── vision/          ← camera, YOLO person detection, DeepFace recognition+emotion
│   └── audio/           ← mic, OpenWakeWord, Whisper STT, pipeline
├── cognition/
│   ├── mind.py          ← two-tier brain (rule engine + Claude speech)
│   ├── conversation.py  ← active voice conversation manager
│   ├── llm.py           ← Ollama-first, Claude Haiku fallback
│   └── intent.py        ← offline Tanglish command matching (no LLM)
├── behavior/
│   ├── engine.py        ← dance, happy/love reactions, proactive speech
│   └── navigation.py    ← wander, follow, approach, retreat, spin
└── expression/
    ├── eyes.py          ← terminal + SSD1306 OLED eye rendering
    ├── speech.py        ← Piper TTS → espeak-ng → pw-play (BT audio)
    └── sounds.py        ← synthesized robot sounds (chirps, purrs)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| OS | Raspberry Pi OS 64-bit (bookworm) |
| Language | Python 3.13 |
| Process manager | PM2 |
| Vision | OpenCV + YOLOv8n + DeepFace |
| Wake word | OpenWakeWord (`hey_jarvis`) |
| STT | faster-whisper tiny.en |
| TTS | Piper (en_US-lessac-medium, neural, 22050→44100Hz upsampled) → espeak-ng |
| Audio output | PipeWire + pw-play → JBL Flip 5 (Bluetooth) |
| LLM primary | Ollama llama3.2:1b (local, offline, free) |
| LLM cloud | Claude Haiku 4.5 (Anthropic API, fallback + spontaneous speech) |
| Event system | Custom async event bus |
| Memory | SQLite episodic + RAM working memory |

---

## Hardware

| Component | Part | GPIO / Interface | Status |
|---|---|---|---|
| Brain | Raspberry Pi 5 8GB | — | ✅ |
| Motors | TB6612FNG + 2× DC | AIN1=17, AIN2=22, PWMA=18, BIN1=23, BIN2=6, PWMB=13, STBY=27 | ✅ wired |
| Camera | Logitech C920 | USB | ✅ active |
| Mic | C920 built-in | USB | ✅ active |
| Speaker | JBL Flip 5 | Bluetooth | ✅ active |
| Light | BH1750 | I2C 0x23 | ✅ tested |
| Battery/UPS | UPS HAT | I2C 0x36 | ✅ active |
| Touch (×4) | Capacitive pads | GPIO5/25/4/7 | ✅ wired |
| Distance | HC-SR04 | TRIG=GPIO16, ECHO=GPIO24 (via LLC) | ⏳ needs enable |
| IMU | MPU6050 | I2C 0x68 | ⏳ needs enable |
| PIR motion | — | GPIO8 | ⏳ needs enable |
| Gesture/Prox | APDS9960 | I2C 0x39, INT=GPIO12 | ⏳ needs enable |
| Cliff sensors | TCRT5000 ×2 | GPIO20/21 (via LLC) | ⏳ needs enable |
| Sound sensor | KY-038 | GPIO19 | ⏳ needs enable |
| Vibration | SW-420 | GPIO26 | ⏳ needs enable |
| Eyes | SSD1306 OLED ×2 | I2C 0x3C/0x3D | ⏳ not wired yet |
| Head servos | Pan/tilt | — | ⏳ not wired yet |

To enable a sensor once wired: set `available: true` in `config/hardware.yaml`.

---

## Brain Logic — Two-Tier Design

The brain is split to minimize API costs.

### Tier 1: Rule Engine (FREE — every 5 seconds, zero API)
Pure sensor-based decisions:
- `dist < 25cm` → stop motors + surprised expression
- `lux < 50` → scared expression, back away, speak (2min cooldown)
- `idle > 120s` → start wandering
- `dist > 80cm + idle > 60s` → 15% chance random explore

### Tier 2: Claude Speech (PAID — event-triggered, rate-limited)
Claude called ONLY to generate personality speech. Never for movement decisions.

| Trigger | Condition | Cooldown |
|---|---|---|
| `face_seen` | Recognizes Madhan or Indhu | 3 min |
| `emotion_happy/sad/angry` | Emotion detected changes | 3 min |
| `touched` | Touch sensor fires | 3 min |
| `alone_long` | No person for 5+ min | 5 min |
| `obstacle` | Near-collision | 1 min |
| `dark_room` | Enters dark area | 2 min |

Voice conversations ("Hey Cosmo" → talk) go to **Ollama first**, Claude only if Ollama is down.

### Daily Budget
Hard limit: **100,000 tokens/day** → mind goes silent if exceeded.
Logs: `cosmo_mind.tokens` per call, `cosmo_mind.daily_summary` at midnight.

Toggle via voice: `"mind off"` / `"mind on"`

---

## API Credit Usage Map

| Source | Frequency | Ollama first? | Notes |
|---|---|---|---|
| Spontaneous speech | event-triggered, rate-limited | No — always Claude | Main spender |
| Voice replies | per utterance | Yes | Claude only if Ollama down |
| Face greetings | once per session per person | Yes | Claude only if Ollama down |
| Emotion reactions | on emotion change, random % | Yes | Claude only if Ollama down |

**History**: original design called Claude every 4s for movement decisions → ~630k tokens/hour.
Current design: Claude only generates speech, never decides movement.

---

## Open Questions (for review)

### Brain / Intelligence
- Should the rule engine cover more personality states, or keep it minimal and let Ollama handle nuance?
- Should spontaneous speech be triggered by the rule engine OR by events? Currently events — is that right?
- Ollama 3.2:1b is fast but weak at Tanglish — upgrade to 3b? (tradeoff: ~4s vs 1.5s latency)
- Should Cosmo have a persistent inner monologue, or is stateless per-trigger fine?

### Voice
- Wake word is `hey_jarvis` — needs custom `hey_cosmo` model (free at console.picovoice.ai)
- Whisper tiny.en misses Tamil words — consider `whisper small` or fine-tuned model
- TTS is `en_US-lessac-medium` American accent — a warmer voice would suit Cosmo better

### Hardware
- GPIO6 conflict: battery_monitor uses GPIO6 as AC-detect pin = BIN2 (right motor). Needs remap.
- OLED eyes fully coded, just needs wiring — high visual impact, should prioritize
- Servo head tracking coded (stubbed), needs wiring + calibration

### Memory
- Episodic memory DB writes happen but **retrieval into prompts is not implemented yet**
- Cosmo doesn't actually remember past conversations yet — biggest missing personality feature
- Face re-enrollment: Indhu ~75% confidence, needs fresh photos

---

## Future Plans

### Near term
- Wire OLED eyes (big personality impact)
- Fix GPIO6 conflict (battery monitor vs motor BIN2)
- Re-enroll Indhu's face
- Enable HC-SR04 ultrasonic (currently mocked)
- Train `hey_cosmo` wake word

### Medium term
- Episodic memory retrieval in prompts (Cosmo actually remembers things said)
- Servo head tracking (follow face)
- Upgrade Ollama to 3b for better Tanglish quality
- Web dashboard (stream + status + mind toggle)

### Long term
- Custom Tanglish TTS voice (train Piper on a Tamil-English speaker)
- Face enrollment UI (web-based, easy to add new people)
- Proactive behaviour scheduling (greet when returning home via door sensor)
- Multi-sensor fusion for richer context (IMU + distance + lux together)
