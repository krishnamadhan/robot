# PROJECT_STATE.md — Cosmo Session Continuity

> Updated at end of every session. Read at start of every session.  
> Source of truth for sprint state.
> Last updated: 2026-05-20

---

## Current Sprint: Phase 1.5 — Sensor Integration

**Goal:** Get all wired sensors alive in software. Wire OLED eyes. Enable real motors.  
**Blocker:** XT60 pigtail (Robocraze delivery pending) + APDS-9960 replacement pending.

---

## Session: 2026-05-20 — System Test + Disk Cleanup

### What happened this session
- Full system test run: gesture detection, face recognition, person detection, audio
- Added `/sound/mute` and `/sound/unmute` endpoints to debug API (`services/api/service.py`)
- Muted sounds for 2h to test cleanly without constant bloop spam
- **Disk alert: 92% full** → nuked 3.4GB of useless GPU packages (nvidia CUDA + triton) — Cosmo uses onnxruntime CPU, not CUDA
- Cleared old rotated cosmo error logs (~70MB)
- Disk now: 90%, 5.8GB free

### Test results (2026-05-20)
| Test | Result | Notes |
|---|---|---|
| Gesture: Open_Palm (wave) | ✅ Working | conf 0.82, latency 13–130ms |
| Gesture: Victory (peace) | ⚠️ False positives | Fires every 3s even with no hand — KI-010 |
| Person detection | ✅ Working | Tracked person for 125s, correctly lost when stepped away |
| Face recognition | ⏳ Not triggered this session | No face rec log — person needs to be in frame at ≤80cm |
| Emotion detection | ⏳ Not tested | Needs person in frame first |
| Wake word | ⏳ Not tested | |
| TTS / speech | ⏳ Not tested | |
| Episodic memory | ⏳ Not tested | |

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

## In Progress

- [ ] Wiring OLED eyes (hardware on desk, just needs wiring + code switch)
- [ ] Planning sensor enable sequence (BH1750 first → safest)

## Next Up (priority order)

1. Wire OLEDs (0x3C + 0x3D) → verify with i2cdetect → switch eyes.py to oled mode
2. Enable BH1750 in hardware.yaml → wire light→mood integration → test
3. Enable PIR GPIO8 → ALERT state → test
4. Enable TTP223 touch × 4 → mood boost + purr → test
5. Enable MPU-6050 → pickup detection → motor off + surprised eyes → test
6. Re-enroll Indhu face (20 samples, good light)
7. Build health endpoint port 8081
8. Wait for XT60 pigtail — then: caps → LiPo → motor real mode

## Blocked On

- XT60 female pigtail (Robocraze) — no motor testing with LiPo until this arrives
- APDS-9960 replacement (Robocraze) — keep available: false until confirmed
- PCA9685 + MG90S + pan-tilt bracket (Robocraze) — Phase 3 starts when these arrive

## Performance Snapshot (2026-05-20)

| Component | RAM | CPU | Notes |
|---|---|---|---|
| Full cosmo (all loops) | 480MB | ~0% idle | PM2 measured |
| CPU temp | — | 55.4°C | Active cooler running |
| Free RAM | 6044MB | — | Plenty of headroom |
| Gesture latency | — | 13–130ms | opencv_skin backend |

## Notes for Next Session

- Start with OLED wiring — biggest visual impact, everything is ready
- Fix Victory false-positive spam (KI-010) before anything else — it's the source of the bloop noise
- Enable sensors one at a time — watch pm2 logs between each
- Do NOT touch motor driver until XT60 pigtail confirmed arrived
- Run i2cdetect first thing to confirm current I2C bus state
- **Disk is at 90%** — if it grows further, check `.robot/logs/` and `/home/pi/downloads/`
