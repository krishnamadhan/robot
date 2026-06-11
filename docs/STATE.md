# Cosmo — STATE.md
> Single source of truth for session continuity. Updated at end of every session; read at start.
> Last updated: 2026-06-11

---

## Component Status

| Component | Code | Hardware | Notes |
|-----------|------|----------|-------|
| Event bus | ✅ | N/A | 4 priority levels; concurrent `create_task` dispatch (OQ-3 ✅); sync handlers tolerated |
| Personality engine | ✅ | N/A | mood/energy/arousal/attachment decay |
| Capability registry | ✅ | N/A | core/capabilities.py; BT gates on `has_all`; snapshot in sensor_monitor |
| Action router | ✅ | N/A | core/action_router.py — sole actuator authority; all 16 Intents have executors |
| State machine (HSM) | 📦 Archived | N/A | archive/state_machine.py — BT is sole decision authority (OQ-4 ✅) |
| Behavior tree | ✅ | N/A | py_trees, 56 nodes; capability-gated social branches; owns wander/idle |
| Episodic memory | ✅ | N/A | SQLite; blocking reads — KI-016 aiosqlite migration pending |
| Working memory | ✅ | N/A | 5-min TTL RAM |
| Spatial memory | ✅ | N/A | Room fingerprints JSON |
| Vision pipeline | ✅ | ✅ Active | C920 USB; **yolo11n.pt** (not YOLOv8n) @ 8 FPS |
| Face recognition | ✅ | ✅ Active | SFace — Madhan ~95%, Indhu ~75% (re-enroll needed) |
| Emotion detection | ✅ | ✅ Active | DeepFace 7-emotion |
| Audio pipeline | ✅ | ✅ Active | hey_jarvis → STT → Claude → TTS → JBL Flip 5 |
| Token budget | ✅ Unified | N/A | Single TokenBudget (cognition/llm.py); persists to memory_meta SQLite, survives restarts (OQ-5 ✅) |
| LLM routing | ✅ Unified | N/A | LLMInterface only; D4 two-tier (ambient→Ollama-first, person→Claude direct); LLMRouter + mind direct client deleted (OQ-6 ✅) |
| OLED eyes | ⚠️ Terminal | ⚠️ Not wired | Hardware arrived — wire now (0x3C left, 0x3D right with A0 bridged) |
| BH1750 light | ✅ Enabled | ✅ Wired | available: true in hardware.yaml; BH1750Sensor in sensor_manager.py |
| ESP32-S3 bridge | ✅ Code done | ⚠️ Not connected | hardware/esp32_bridge.py; all SENSORS flags False (mock mode) |
| Motors (TB6612FNG) | ✅ Code done | ⚠️ Not rewired | motors.py → bridge → ESP32 GPIO 15–21; **physically still on Pi GPIO** — rewire before enabling |
| PIR | ✅ Code done | ⚠️ Not wired | ESP32 GPIO12; set SENSORS["pir"]=True after wiring |
| Touch ×4 | ✅ Code done | ⚠️ Not wired | ESP32 GPIO1–4 (head/back/belly/tail) |
| MPU-6050 IMU | ✅ Code done | ⚠️ Not wired | ESP32 I2C GPIO8/9 @ 0x68 |
| Cliff ×2 | ✅ Code done | ⚠️ Not wired | ESP32 GPIO13/14; parcels pending |
| HC-SR04 ultrasonic | ✅ Code done | ⚠️ Not wired | ESP32 GPIO10/11 via 2kΩ/1kΩ divider; blocked on XT60 pigtail |
| KY-038 sound | ✅ Code done | ⚠️ Not wired | ESP32 GPIO5 ADC1; parcel pending |
| SW-420 vibration | ✅ Code done | ⚠️ Not wired | ESP32 GPIO6 |
| APDS-9960 gesture | ❌ | ⚠️ Replacement ordered | Keep available: false |
| PCA9685 servo | ❌ Not started | ⚠️ On order | Phase 3 |

---

## Next Priority

**Phase 1 COMPLETE (2026-06-11) — STOPPED at gate, awaiting Madhan's review.** All 14 items done (see docs/COSMO_MASTER_PLAN.md Phase 1 + docs/PHASE1_MIGRATION.md, 30/30 boxes). Migration-completeness check passed: no prod imports of archived modules; all 16 Intents router-reachable; brain + safety + budget suites green (102 brain/budget tests pass). **Phase 2 entry prerequisite: perception/audio deep-dive.** Pre-existing test debt (predates Phase 1, 26 failures): tests/unit/test_new_systems.py + test_phase1.py + test_safety_paths MotorStby reference classes that never existed (PIRSensor, MotorSafetyError); tests/hardware/test_esp32_bridge.py hits `_mock` attr drift. Wiring deferred to Phase 4 doc.

<details><summary>Wiring sequence (deferred — reference for Phase 4 doc)</summary>

1. Rewire TB6612FNG from Pi GPIO to ESP32 GPIO 15–21 (AIN1=15, AIN2=16, PWMA=17, BIN1=18, BIN2=19, PWMB=20, STBY=21). **Do not power motors until XT60 pigtail arrives.**
2. Wire sensors in order (PIR first — safest): PIR→GPIO12, Touch×4→GPIO1–4, IMU I2C→GPIO8/9, Cliff→GPIO13/14, Sound→GPIO5.
3. For each sensor: set `SENSORS["<id>"] = True` in esp32/main.py, copy to ESP32, run `python3 tools/esp32_test.py` to verify before enabling next.
4. Wire OLED eyes (0x3C left, 0x3D right) — run `sudo i2cdetect -y 1` to confirm both visible, then switch eyes.py render target to "oled".

**Blocked on:** XT60 female pigtail (Robocraze) for motor power; APDS-9960 replacement; cliff/sound sensors in parcel.

</details>

---

## Open Questions

| # | Question | Stakes |
|---|----------|--------|
| OQ-1 | ESP32 has no local cliff reflex — all sensor→action round-trips through Pi. If Pi is busy, cliff-stop latency is unbounded. Implement `Pin.irq` on cliff pins in esp32/main.py for immediate local stop. | Safety |
| OQ-2 | `_outq` in esp32/main.py is an unbounded plain list. At 10 Hz polling with Pi busy, it grows without bound. Add bounded deque with drop-oldest policy. | Reliability |
| ~~OQ-3~~ | ✅ Resolved 2026-06-11 — event_bus dispatches via `create_task` (strong refs + done-callback error logging; sync handlers tolerated; drained on stop). | Latency |
| ~~OQ-4~~ | ✅ Resolved 2026-06-11 — HSM archived (archive/state_machine.py); BT is sole decision authority, router sole actuator. | Architecture |
| ~~OQ-5~~ | ✅ Resolved 2026-06-11 — single TokenBudget; persists per-day total to memory_meta via atomic increment UPSERT; resumes on restart. Tests isolated via tests/conftest.py. | Reliability |
| ~~OQ-6~~ | ✅ Resolved 2026-06-11 — LLMRouter deleted; mind.py routes through `LLMInterface.generate_once` (D4 two-tier). | Maintainability |
| ~~OQ-7~~ | ✅ Resolved 2026-06-11 — (a) generate_streaming budget-gated + records usage. (b) cache_control added, but static prefix ≈300 tokens < Haiku's 2048-token cache minimum → currently a no-op; becomes live when the prompt grows. | Cost |
| ~~OQ-8~~ | ✅ Resolved 2026-06-11 — `_follow_loop` unsubscribes its PERSON_DETECTED handler in `finally`. | Reliability |
| OQ-9 | Pre-existing test debt: 26 failures across test_new_systems / test_phase1 / test_esp32_bridge / MotorStby reference APIs that never existed (PIRSensor, MotorSafetyError) or drifted (`_mock` vs `is_mock` in esp32_bridge). Rewrite or delete during Phase 2 perception/audio deep-dive. | Hygiene |
