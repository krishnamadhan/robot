# Cosmo — STATE.md
> Single source of truth for session continuity. Updated at end of every session; read at start.
> Build history (June 2026 phase sprints, resolved OQs) → docs/HISTORY.md. Work queue → AgentBoard (`board list`) + COSMO_BACKLOG.md.

## Current State (2026-07-07)

**Service:** `cosmo` PM2 = MINIMAL `tools/led_service.py` — camera + LED API (:8000)
+ live stream (:8080) + TV ambilight only. Personality/audio/vision-AI stack OFF
(no robot hardware wired). ~320MB RSS.

**TV ambilight — WORKING (reflected-light mode).** Camera samples the scene → LEDDMX
BLE strip + Wipro bulb follow the dominant colour.
- `behavior/ambilight.py`: ROI sampling, CCM colour correction (valid only at WB lock
  hw_r 2.4 / hw_b 0.8 — self-heals if something resets camera colour), content gate,
  white/pastel desaturation path, idle dim-off, ACK-only state tracking.
- **⚠ Camera is NOT aimed at the TV** (drifted since 2026-07-02) — sync runs off wall
  glow; hues land 4–12°, orange 23°. Re-aim → `!led calibrate` (rejects wall glow)
  → re-solve CCM with 8 cards (`tools/ambilight_cast_verify.py --calibrate`).
- **Wipro bulb** (Tuya v3.3, 192.168.1.3, key in .env): synced via music-mode
  streaming, serialized coalescing worker ~3 Hz. `hardware/wipro_light.py`.
  **Manual control (AB-014, 2026-07-10, LIVE):** `set_color_manual` (persistent
  DP24 colour mode) + `POST/GET /led/bulb` (color/bright/on/off), bulb block in
  `/led` + `/led/health`, static scenes fan out to the bulb. curl-verified on
  the running service. Remaining: `!led bulb` banteragent half (codex, staged).
- **Strip controller modes** (2026-07-07, driver-level, API/!led wiring pending):
  0x03 patterns 0-210, 0x0B built-in-mic sound sync, 0x09 colour temp, custom
  patterns. `hardware/led_strip.py` PATTERNS/MUSIC_MODES. 0x04 power stays BANNED.
  Robot API `/led` and `tools/led_test.py` now expose `pattern`, `music`, and
  `temp` controls alongside the existing colour/brightness commands.
- **Routines (cron):** 18:00 sync on · 00:00 all off (`tools/lights_routine.sh`);
  TV-sync desired state persists across cosmo restarts (evening window).
- Govee T2 behind the TV runs its own camera sync (independent, intentional).

**Toggle:** `POST /led/tv {on}` · status `GET /led` · `!led tv on|off` (WhatsApp).
Tests: `tools/led_test.py`, `tools/wipro_test.py`, `tools/ambilight_cast_verify.py`.

**banteragent staged (dormant until its next natural restart):** !led/!pi help
updates, !cosmo proxy suite, /cosmo-notify, !refreshgames all (AB-007 merged).

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
| Outbound WhatsApp | ✅ | N/A | cognition/notifications.py; 10/day limit; /cosmo-notify on banteragent:3099 (activates on next banteragent restart) |
| Exploration memory | ✅ | N/A | behavior/exploration.py; room snapshots every 5min; anti-revisit wander bias; DiscoveryEvent log |
| Discovery behavior | ✅ | ⚠️ Needs motors | wander detects obstacle <25cm sustained → DoDiscovery BT node; speak+eyes if person; WhatsApp if alone |
| API auth | ✅ | N/A | Bearer token via ROBOT_API_TOKEN env var; motor/mind/sound/trigger endpoints gated; /cosmo/say open for banteragent compat |
| Smart home | ✅ Stub | N/A | EventType.SMARTHOME_* (5 types); POST /smarthome/event ingestion; mind.py reacts to tv_on, lights_off, presence_home |
| Vision pipeline | ✅ | ✅ Working | IMX708 Wide CSI; **R/B channel swap fixed 2026-07-02** (picamera2 RGB888 is BGR — old code double-swapped → blue cast). AWB auto at boot; color.toml reset neutral. Person detect: **yolo11n.onnx via onnxruntime** (torch uninstalled, ~160MB+ RSS saved; ultralytics path broken since torchvision loss, had silently fallen back to HOG) |
| Face recognition | ✅ | ⚠️ Re-enroll both | SFace — embeddings were enrolled on R/B-swapped frames (pre-2026-07-02 fix); confidence may drop. Re-enroll Madhan + Indhu with tools/enroll_face.py |
| Emotion detection | ✅ | ✅ Working | DeepFace 7-emotion |
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
