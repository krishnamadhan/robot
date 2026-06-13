# Cosmo — Backlog

> Ordered by priority. Tick items as done. Add new items at the correct priority level.
> Session START: read this and pick the top unchecked item.
> Session END: update checkboxes, add newly discovered tasks.

---

## P0 — Architecture gaps (fix before Phase 2 motors go live)

- [x] **ESP32 local cliff reflex**: add `Pin.irq` on cliff GPIO13/14 in `esp32/main.py` — local motor stop without Pi round-trip. Currently cliff→Pi→motor has unbounded latency if Pi is busy. *(done 2026-06-12, OQ-1)*
- [x] **Bounded `_outq`**: replace plain list in `esp32/main.py` with a bounded deque (max 20 items, drop oldest) — prevents unbounded memory growth at 10 Hz polling. *(done 2026-06-12, OQ-2 — bounded list 100, critical never dropped)*
- [x] **Event bus `create_task` dispatch**: `core/event_bus.py` currently awaits handlers inline. Switch to `asyncio.create_task` so a slow CLIFF_DETECTED handler cannot block the loop. *(done 2026-06-11, OQ-3)*
- [x] **Kill HSM or define ownership**: both `state_machine.py` (HSM) and py_trees behavior tree run simultaneously in `tools/cosmo_demo.py` with no boundary. Either remove the HSM and route everything through py_trees, or document the split clearly in `docs/DECISIONS.md`. *(done 2026-06-11 — HSM archived, BT sole authority, OQ-4)*
- [x] **Unify LLM call paths**: `cognition/llm.py:LLMInterface` (Ollama→Claude) and `cognition/mind.py` (direct `anthropic.Anthropic` client) are two separate paths. Route everything through `LLMInterface`. *(done 2026-06-11, OQ-6)*
- [x] **Unify + persist token budget**: `cognition/llm.py:TokenBudget` and `cognition/mind.py:_DailyBudget` are separate, neither persists to disk. Merge into one class; persist to `~/.robot/memory_meta/budget.json` so a crash doesn't reset the counter. *(done 2026-06-11, OQ-5 — persisted to memory_meta SQLite, not JSON)*

---

## P1 — Wire hardware (waiting on parcels or XT60)

- [ ] **Fix Pi camera**: seat CSI ribbon cable firmly (blue tab toward USB ports) → `rpicam-hello --list-cameras` to verify → update cosmo_demo.py camera startup (replace cv2.VideoCapture with picamera2)
- [ ] Wire OLED eyes (0x3C + 0x3D) → verify with `i2cdetect` → switch eyes.py to oled mode (KI-019 mutex done 2026-06-12 — software ready)
- [ ] Wire motors: rewire TB6612FNG from Pi GPIO → ESP32 GPIO 15–21 — **BLOCKED on XT60 pigtail + capacitors arriving**
- [ ] Wire PIR HC-SR501 → ESP32 GPIO12, set `SENSORS["pir"] = True` in esp32/main.py
- [ ] Wire TTP223 touch ×4 → ESP32 GPIO1–4 (head/back/belly/tail), set `SENSORS["touch"] = True`
- [ ] Wire MPU-6050 → ESP32 I2C (GPIO8/9), set `SENSORS["imu"] = True`
- [ ] Wire HC-SR04 ultrasonic → ESP32 GPIO10 (TRIG) / GPIO11 (ECHO via 2kΩ/1kΩ divider) — **BLOCKED on XT60 pigtail**
- [ ] Wire TCRT5000 cliff sensors ×2 → ESP32 GPIO13/14 — **BLOCKED on parcel arriving**
- [ ] Wire KY-038 sound → ESP32 GPIO5 ADC1 — **BLOCKED on parcel arriving**

---

## P2 — Code improvements

- [x] KI-016: migrate episodic memory to aiosqlite *(done 2026-06-12 — async lifecycle: `await episodic.initialize()/close()`)*
- [x] Outbound WhatsApp notifications (cognition/notifications.py + banteragent /cosmo-notify) *(done 2026-06-13)*
- [x] Exploration memory + anti-revisit wander bias (behavior/exploration.py) *(done 2026-06-13)*
- [x] Discovery behavior: wander detects new obstacles → DoDiscovery BT node → speak/notify *(done 2026-06-13)*
- [x] "Missing you" WhatsApp nudge when alone >20min + attachment >0.6 *(done 2026-06-13)*
- [x] **Port 8000 API auth** — Bearer token via ROBOT_API_TOKEN env var; motor/mind/sound/trigger endpoints gated *(done 2026-06-13)*
- [x] **Personalized curiosity questions** — _build_curiosity_prompt() pulls episodic memories and asks specific questions *(done 2026-06-13)*
- [x] **Touch → attachment boost** — TOUCH_DETECTED → personality.process_event("touch_gentle") + episodic.upsert_person(relationship_delta=0.04) *(done 2026-06-13)*
- [x] **Activity → movement response** — co_presence settle: stop wander + async approach; hangout: follow_mode(60s) every 2min *(done 2026-06-13)*
- [x] **Smart home integration stub** — EventType.SMARTHOME_* (5 types); POST /smarthome/event; mind.py reacts to tv_on/lights_off/presence *(done 2026-06-13)*
- [x] **Dashboard motor controls need token** — dashboard injects token at serve time; postCmd/motorCmd send Bearer header; motor duration fix via _timed_move wrapper *(done 2026-06-13)*
- [ ] **Home Assistant webhook** — configure HA webhook → POST http://pi-tailscale-ip:8000/smarthome/event for TV on/off, lights, presence automations
- [ ] Re-enroll Indhu face: 20 samples, good light (currently ~75% — target 90%+) → `python3 tools/enroll_face.py`
- [ ] Prompt caching (ADR-018): add ephemeral cache headers to Claude calls — after OLED + face tests
- [ ] Test Piper Kitten Micro 25MB vs current lessac-medium 61MB (ADR-016)
- [ ] Test FER 5-class vs current DeepFace 7-class emotion detection (ADR-014)

---

## P3 — Phase 3 (waiting on hardware)

- [ ] PCA9685 + MG90S + pan-tilt bracket: wire when Robocraze parcel arrives
- [ ] Camera pan-tilt servo following person
- [ ] Ultrasonic rotate for 180° sweep

---

## P4 — Phase 4

- [ ] Train custom "hey_cosmo" OWW model → save to `~/.robot/models/hey_cosmo.tflite` (COSMO_WAKE_LABEL env var controls active label — no code change needed once model is there)

---

## Done

- [x] ESP32-S3 architecture rework (2026-06-09) — sensors+motors offloaded to ESP32; Pi owns brain/camera/audio
- [x] ESP32 bridge + motors.py + sensor_manager.py rewritten for ESP32 architecture
- [x] 21 ESP32 bridge mock tests passing
- [x] tools/esp32_test.py — Rich UI live dashboard
- [x] Ollama llama3.2:1b-q4_K_M benchmarked — warm TTFT ~1s streaming; use for ambient reactions
- [x] Memory system restructured (2026-06-09) — STATE.md, reduced CLAUDE.md files, secrets migrated to ~/secrets/
- [x] BH1750 light sensor: available: true in hardware.yaml, BH1750Sensor in sensor_manager.py ✅ (was listed as P1 but already done)
- [x] CLAUDE_SESSION_PROTOCOL.md created (2026-05-29 — now folded into CLAUDE.md and deleted)
- [x] COSMO_BACKLOG.md + docs/CHANGELOG.md created (2026-05-29)
- [x] tools/cosmo_doctor.sh — one-shot health snapshot
- [x] hardware/pin_registry.py — boot-time GPIO conflict detection
- [x] tests/unit/test_token_budget.py — 9 tests
- [x] tests/unit/test_safety_paths.py — 10 tests
- [x] Brain test harness (tests/brain/) — 89 tests, all 7 invariants green
- [x] LLM routing (cognition/llm.py) — Ollama-first, Claude fallback, TokenBudget
- [x] PersonalityPromptBuilder (cognition/personality_prompt.py)
- [x] recall_for_prompt / store_fact (core/memory/episodic.py)
- [x] Dashboard rebuilt (services/api/service.py — B6 spec)
- [x] Upgrade person detection to YOLO11n (ADR-012)
- [x] Local LLM: Ollama llama3.2:1b Q4_K_M (ADR-017)
- [x] Full P0–P4 code audit + safety fixes (2026-05-21)
- [x] Pet brain (cognition/pet_brain.py)
- [x] Camera auto-detect + degraded mode
- [x] robot_control.py deprecated with sys.exit(1)
