# Cosmo — STATE.md
> Single source of truth for session continuity. Updated at end of every session; read at start.
> Last updated: 2026-06-12

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
| Episodic memory | ✅ | N/A | aiosqlite (KI-016 ✅ 2026-06-12); lifecycle is `await episodic.initialize()/close()` |
| Working memory | ✅ | N/A | 5-min TTL RAM |
| Spatial memory | ✅ | N/A | Room fingerprints JSON |
| Vision pipeline | ✅ | ✅ Active | C920 USB; **yolo11n.pt** (not YOLOv8n) @ 8 FPS |
| Face recognition | ✅ | ✅ Active | SFace — Madhan ~95%, Indhu ~75% (re-enroll needed) |
| Emotion detection | ✅ | ✅ Active | DeepFace 7-emotion |
| Audio pipeline | ✅ | ✅ Active | hey_jarvis → STT → Claude → TTS → JBL Flip 5 |
| Token budget | ✅ Unified | N/A | Single TokenBudget (cognition/llm.py); persists to memory_meta SQLite, survives restarts (OQ-5 ✅) |
| LLM routing | ✅ Unified | N/A | LLMInterface only; D4 two-tier (ambient→Ollama-first, person→Claude direct); LLMRouter + mind direct client deleted (OQ-6 ✅) |
| OLED eyes | ⚠️ Terminal | ⚠️ Not wired | Hardware arrived — wire now (0x3C left, 0x3D right with A0 bridged). Eye engine personality-baseline drift live (2.1) |
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

**Phase 1 APPROVED & COMMITTED (2026-06-11, fe52382). Phase 2 entry prerequisite DONE: perception/audio deep-dive complete (62b62a0).** Critical findings fixed: emotion smoothing history cleared on PERSON_LOST; mic capture-thread shutdown race tolerated; stale VAD `_partial` dropped per session; STT model unloaded on pipeline stop. Non-critical findings: wake_word import-time load fixed (lazy `load_detectors()` on pipeline start) and stale-frame telemetry counter added (`vision.frame_stale_drop`) on 2026-06-12; 2026-06-12 "Don't wait" go-ahead — both deferred findings resolved: PERSON_DETECTED owner = person.py (vision_loop publish removed, personality side-effects kept, `vision.person_arrived` counter); anonymous emotions gated (mind `_on_emotion` early-return on person_id=None; cosmo_demo `set_emotion` only when pid matches active person — eyes/sounds stay ungated, pet-like). **Phase 2 GO received; 2.1 + 2.2 done.** 2.1: eye baseline drift from personality vector (tests/unit/test_eyes.py; eye_simulator j/k u/i g/t personality nudges). 2.2: expression/idle_motion.py — curiosity glances (cadence from curiosity trait + arousal), boredom fidgets, pupil settling, micro-reactions to SOUND_DETECTED/LIGHT_CHANGED; defers to event expressions; started in cosmo_demo + eye_simulator (tests/unit/test_idle_motion.py). 2.3 done: time-of-day now shifts the *baseline targets* mood/energy drift toward (`_circadian_targets`); late_night −0.5 settles energy ≈0.15 → sleepy face/behaviors engage overnight. 2.4 done: eye event map extended to 13 events with per-event duration/priority (now incl. WAKE_WORD→surprised, FACE_UNKNOWN→curious, PERSON_LOST→sad, CONVERSATION_START→happy, CLIFF_DETECTED→scared, BATTERY_LOW→sleepy, OBSTACLE_WARNING→surprised). 2.5 done: FACE_RECOGNIZED `person_id` now threads into `_maybe_speak` → greet uses `_build_rich_system_prompt` (episodic recall + persons-table relationship_quality/last-seen gap) even before `conversation.set_person`; speech language configurable (`personality.yaml speech.language: english|tanglish`) — applies to Claude-direct person paths only, ambient Ollama prompts pinned English; default english pending Tanglish decision at STOP gate (tests/unit/test_mind_greet.py, 13 tests). **Phase 2 STOP gate PASSED 2026-06-12 ("Don't wait" go-ahead): speech.language flipped to tanglish (Claude-direct paths; ambient stays English). Phase 3 (movement firmware) is now active — 3.1 done (ESP32 cliff Pin.irq reflex + bounded _outq, OQ-1/OQ-2 ✅); 3.2 done (bridge move-command coalescing + 300ms staleness drop; 3s heartbeat watchdog → caps FAILED + SENSOR_TIMEOUT event → CONFUSED eyes "body offline"; auto-recovery via mark_seen). 3.3 done (tests/unit/test_router_movement.py — APPROACH/FLEE/WANDER/FOLLOW/COME drive navigation under reg.simulate(LOCOMOTION); without it, expressive fallback fires, never silent; STOP allowed no-op; obstacle gate holds). Suite 260 passed. **Phase 3 STOP gate PASSED 2026-06-12 (suite 260 passed, 0 failed; 3.1/3.2/3.3 committed). Phase 4 (HARDWARE_PLAN.md doc) DONE 2026-06-12 — docs/HARDWARE_PLAN.md rewritten: 8-step wiring guide with parts/pins/test commands/capability flips, GPIO6/16 FIT0992 conflict, MAX17040 fragility notes, KI-024 pre-flight, maintenance checklist, SD-card risk/recovery. Phase 5 ("Continue with the build" go-ahead, 2026-06-12) DONE pending STOP gate: robot API (port 8000, services/api/service.py) gained `/caps`, `POST /cosmo/sim`, `POST /cosmo/say` (300-char cap, tts.speak), `GET /cosmo/last`; banteragent src/router.ts `!cosmo` case extended with status|caps|mood|last|log|say|sim|start|stop (proxies to 8000; start/stop = pm2 via ecosystem.config.js; 5s fetch timeout → "brain offline" hint; DM-only + listener BOT_OWNER_PHONE gate = owner path; tsc clean). **banteragent NOT restarted — proxy is dormant until its next natural restart**; banteragent tree was already dirty with Madhan's in-progress work so router.ts left uncommitted there. Robot tests: tests/unit/test_api_cosmo.py (9 tests); suite 269 passed. Phase 4 + 5 gates approved ("Go", 2026-06-12) — master plan complete; work now comes from COSMO_BACKLOG.md. KI-016 done 2026-06-12: episodic memory migrated to aiosqlite (async initialize/close, call sites in main.py/cosmo_demo.py/memory_browser.py updated; brain tests rewritten to async API; suite 269 passed). Next backlog candidates: Indhu face re-enroll (needs her present), prompt caching ADR-018 (gated on OLED+face tests), Piper Kitten Micro ADR-016 — most remaining items are hardware-blocked (parcels/XT60). A daily remote trigger (trig_01FMp6sVt1KVAe81BahjUgT8, 11:30 IST) resumes work on this STATE; disable at claude.ai/code/scheduled when done.**

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
| ~~OQ-1~~ | ✅ Resolved 2026-06-12 (3.1) — cliff `Pin.irq` brakes motors locally + 500ms move-refusal hold (`_cliff_active`); pir/touch/vibe also IRQ-driven (vibe debounced 50ms). Deploy to ESP32 at wiring time. | Safety |
| ~~OQ-2~~ | ✅ Resolved 2026-06-12 (3.1) — `_outq` bounded at 100, drop-oldest non-critical; heartbeats + cliff events never evicted. | Reliability |
| ~~OQ-3~~ | ✅ Resolved 2026-06-11 — event_bus dispatches via `create_task` (strong refs + done-callback error logging; sync handlers tolerated; drained on stop). | Latency |
| ~~OQ-4~~ | ✅ Resolved 2026-06-11 — HSM archived (archive/state_machine.py); BT is sole decision authority, router sole actuator. | Architecture |
| ~~OQ-5~~ | ✅ Resolved 2026-06-11 — single TokenBudget; persists per-day total to memory_meta via atomic increment UPSERT; resumes on restart. Tests isolated via tests/conftest.py. | Reliability |
| ~~OQ-6~~ | ✅ Resolved 2026-06-11 — LLMRouter deleted; mind.py routes through `LLMInterface.generate_once` (D4 two-tier). | Maintainability |
| ~~OQ-7~~ | ✅ Resolved 2026-06-11 — (a) generate_streaming budget-gated + records usage. (b) cache_control added, but static prefix ≈300 tokens < Haiku's 2048-token cache minimum → currently a no-op; becomes live when the prompt grows. | Cost |
| ~~OQ-8~~ | ✅ Resolved 2026-06-11 — `_follow_loop` unsubscribes its PERSON_DETECTED handler in `finally`. | Reliability |
| ~~OQ-9~~ | ✅ Resolved 2026-06-12 — deleted tests for never-existed Pi-side sensor classes (PIR/MPU/cliff/ultra live on ESP32, covered by bridge tests); rewrote MotorController/SoundEngine/UPSHAT tests to current APIs; MotorSafetyError test → Pi-side e-stop latch test; bridge tests get `_mock` attr + `asyncio.run` (loop isolation). Suite: 248 passed, 0 failed. | Hygiene |
