# PROJECT_STATE.md — Cosmo Session Continuity

> Updated at end of every session. Read at start of every session.  
> Source of truth for sprint state.
> Last updated: 2026-05-21 (4th session — Phase 2 complete)

---

## Current Sprint: Phase 1.5 — Sensor Integration

**Goal:** Get all wired sensors alive in software. Wire OLED eyes. Enable real motors.  
**Blocker:** XT60 pigtail (Robocraze delivery pending) + APDS-9960 replacement pending.

---

## Session: 2026-05-20 (2nd) — Codebase Audit + Brain Fixes

### What happened this session
- Full autonomous code review against all MD files
- **KI-010 fixed**: Victory gesture false positives eliminated (per-gesture thresholds, VICTORY_HOLD_FRAMES=4, person-gating)
- **mind.py rewritten**: Fixed `tts` NameError, added `_subscribe_events()` — Cosmo now has event-driven proactive speech (face_seen, emotion, touch, light, obstacle)
- **cosmo_demo.py**: Fixed race condition in face event handler (`_approaching` flag), wired `sm.transition_to()` calls
- **behavior_tree.py**: Added `audio_speaking` blackboard flag — gesture/idle sounds now gate when TTS is speaking
- **motors.py + hardware.yaml**: Motor LEFT_TRIM/RIGHT_TRIM moved from hardcode to config
- **API service**: Added `/sound/mute` and `/sound/unmute` debug endpoints

## Session: 2026-05-20 (1st) — System Test + Disk Cleanup

### What happened this session
- Full system test run: gesture detection, face recognition, person detection, audio
- Muted sounds for 2h to test cleanly without constant bloop spam
- **Disk alert: 92% full** → nuked 3.4GB of useless GPU packages (nvidia CUDA + triton) — Cosmo uses onnxruntime CPU, not CUDA
- Cleared old rotated cosmo error logs (~70MB)
- Disk now: 90%, 5.8GB free

### Test results (2026-05-20 1st session)
| Test | Result | Notes |
|---|---|---|
| Gesture: Open_Palm (wave) | ✅ Working | conf 0.82, latency 13–130ms |
| Gesture: Victory (peace) | ✅ Fixed (KI-010) | Per-gesture threshold 0.92 + 4-frame hold |
| Person detection | ✅ Working | Tracked person for 125s, correctly lost when stepped away |
| Face recognition | ⏳ Not triggered this session | No face rec log — person needs to be in frame at ≤80cm |
| Emotion detection | ⏳ Not tested | Needs person in frame first |
| Wake word | ⏳ Not tested | |
| TTS / speech | ⏳ Not tested | |
| Episodic memory | ⏳ Not tested | |

---

## Stack Upgrade Queue (from 2026-05-21 tech review — see DECISIONS.md ADR-012 to ADR-018)

| Component | Current | Target | Status | ADR |
|---|---|---|---|---|
| Person detection | YOLOv8n (was broken — HOG fallback) | YOLO11n @ 10.1 FPS | ✅ Complete | ADR-012 |
| Face recognition | SFace ONNX | — (keep) | ✅ Locked | ADR-013 |
| Emotion detection | DeepFace 7-class | FER 5-class candidate | 📅 Deferred — test after OLED + face tests | ADR-014 |
| Wake word | OWW hey_jarvis | OWW hey_cosmo (custom) | 📅 Phase 4 | ADR-015 |
| TTS | Piper lessac-medium 61MB | Kitten Micro 25MB | 📅 Quality test first | ADR-016 |
| Local LLM | Ollama llama3.2:1b 1.3GB | Q4_K_M ~700MB | 📅 After YOLO11n | ADR-017 |
| Claude context | No caching | Prompt caching (ephemeral) | 📅 After YOLO11n | ADR-018 |

---

## Component Status Table

| Component | Code Status | Hardware Status | Notes |
|---|---|---|---|
| Event bus | ✅ Working | N/A | 4 priority levels, async pub/sub |
| Personality engine | ✅ Working | N/A | mood/energy/arousal/attachment decay |
| State machine | ✅ Working | N/A | 30-state HSM |
| Episodic memory | ✅ Working | N/A | SQLite, remembers conversations |
| Working memory | ✅ Working | N/A | 5min TTL RAM store |
| Spatial memory | ✅ Working | N/A | Room fingerprints JSON |
| Vision pipeline | ✅ Working | ✅ Active | Parallel 4-loop, Logitech C920 |
| Person detection | ✅ Working | ✅ Active | YOLOv8n, 32 FPS |
| Face recognition | ✅ Working | ✅ Active | SFace — Madhan 97%, Indhu 75% |
| Emotion detection | ✅ Working | ✅ Active | DeepFace 7-emotion |
| Audio pipeline | ✅ Working | ✅ Active | hey_jarvis → STT → Claude → TTS |
| Wake word | ✅ Working | ✅ Active | OpenWakeWord hey_jarvis (not hey_cosmo) |
| STT | ✅ Working | ✅ Active | faster-whisper base.en beam=5 |
| TTS | ✅ Working | ✅ Active | Piper streaming → JBL Flip 5 |
| Claude API calls | ✅ Working | ✅ Active | claude-haiku-4-5, 100K daily limit |
| Behaviour engine | ✅ Working | ✅ Active | 30s ambient loop, proactive speech |
| OLED eyes | ⚠️ Terminal mode | ⚠️ Not wired | Hardware arrived — wire NOW |
| BH1750 light | ⚠️ Not enabled | ✅ Wired | Change available: false → true in hardware.yaml |
| PIR motion | ⚠️ Not enabled | ✅ Wired | Change available: false → true |
| TTP223 touch × 4 | ⚠️ Not enabled | ✅ Wired | Change available: false → true |
| MPU-6050 gyro | ⚠️ Not enabled | ✅ Wired | Change available: false → true |
| HC-SR04 ultrasonic | ⚠️ Mock only | ✅ Wired | Enable after XT60 pigtail + caps |
| TCRT5000 cliff × 2 | ⚠️ Not enabled | ⚠️ In parcel | Enable after arriving |
| KY-038 sound | ⚠️ Not enabled | ⚠️ In parcel | Enable after arriving |
| SW-420 vibration | ⚠️ Not enabled | ✅ Have it | Enable when ready |
| APDS-9960 gesture | ❌ Faulty | ⚠️ Replacement ordered | Keep available: false |
| Motors (TB6612FNG) | ⚠️ Mock mode | ✅ Wired | Enable after XT60 pigtail + caps |
| Motor LiPo power | ❌ Not connected | ⚠️ XT60 pigtail ordered | Connect when pigtail arrives |
| PCA9685 servo | ❌ Not yet | ⚠️ On order | Phase 3 |
| Camera pan-tilt | ❌ Not yet | ⚠️ On order | Phase 3 |
| BanterAgent | ✅ Running | N/A | WhatsApp bot — NEVER TOUCH |

---

## Session: 2026-05-21 (4th) — Phase 2: Code Audit + Safety Fixes

### What happened this session
**Phase 2 complete** — all P0-P4 issues from the full code audit fixed and committed (e5cc5bf).

**P0 — Hardware safety:**
- `motors.py` refactored for 4WD nested config — reads `mc.left_front.ain1/ain2/pwm` etc. instead of flat `mc.ain1`. 4 independent `_MotorChannel` objects, driven in sync pairs. Would have crashed on first real motor enable.
- `sensor_manager.py` — SoundSensor reads pin from config (GPIO11, was hardcoded 19); VibrationSensor handles null pin (GPIO26 taken by motor); TouchSensorArray reads from config (belly removed — GPIO25 is right_rear BIN1)

**P1 — Silent failures:**
- `ecosystem.config.js` — stderr now goes to real log file, max_restarts 20→5, backoff 100→1000ms
- New KI-024 added: GPIO8/I2C bus 4 conflict (needs config.txt fix + reboot — user to do)

**P2 — Timeouts fixed (KI-014, KI-015):**
- `mind.py` — `asyncio.wait_for(timeout=15s)` around Claude executor call
- `speech.py` — `piper.communicate(timeout=10s)` + paplay cleanup on exception

**P3 — Data integrity:**
- `spatial.py` — atomic write via `tmp.replace()`, division-by-zero and empty-dict guards

**P4 — Medium fixes:**
- `working.py` — auto-purge loop every 60s
- `llm.py` — OLLAMA_TIMEOUT_S 90→60, streaming path logs token usage
- `state_machine.py` — deny-by-default for unregistered transitions (KI-018)
- `intent.py` — word-boundary regex matching (no more "nil" matching "pencil")
- `behavior/engine.py` — morning greeting uses `datetime.now().hour` not UTC time.time()
- `cosmo_mind.start()` now wired

---

## In Progress

- [ ] Rebooted Pi (KI-024 — GPIO8/I2C conflict, config.txt change required)
- [ ] Ollama Q4_K_M pull (next session task)

## Next Up (priority order)

1. Pull Ollama Q4_K_M + live test with someone in frame
2. Wire OLEDs (0x3C + 0x3D) → verify with i2cdetect → switch eyes.py to oled mode + fix KI-019 I2C mutex first
3. Prompt caching ADR-018 (ephemeral cache prefix in mind.py — ~40% API cost cut)
4. KI-016 — aiosqlite migration for episodic memory
5. Enable sensors one at a time: BH1750 → touch × 3 → MPU-6050 → PIR (only after KI-024 config.txt fix)
6. Re-enroll Indhu face (20 samples, good light)

## Blocked On

- XT60 female pigtail (Robocraze) — no motor testing with LiPo until this arrives
- APDS-9960 replacement (Robocraze) — keep available: false until confirmed
- PCA9685 + MG90S + pan-tilt bracket (Robocraze) — Phase 3 starts when these arrive
- KI-024 config.txt edit (done during reboot on 2026-05-21)

## Performance Snapshot (2026-05-20)

| Component | RAM | CPU | Notes |
|---|---|---|---|
| Full cosmo (all loops) | 480MB | ~0% idle | PM2 measured |
| CPU temp | — | 55.4°C | Active cooler running |
| Free RAM | 6044MB | — | Plenty of headroom |
| Gesture latency | — | 13–130ms | opencv_skin backend |
- Do NOT touch motor driver until XT60 pigtail confirmed arrived
- Run i2cdetect first thing to confirm current I2C bus state
- **Disk is at 90%** — if it grows further, check `.robot/logs/` and `/home/pi/downloads/`
- Test proactive speech: stand at ≤80cm from camera, verify `cosmo_mind.spoke` in logs
