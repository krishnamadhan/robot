# Cosmo — STATE.md
> Single source of truth for session continuity. Updated at end of every session; read at start.
> Last updated: 2026-06-09

---

## Component Status

| Component | Code | Hardware | Notes |
|-----------|------|----------|-------|
| Event bus | ✅ | N/A | 4 priority levels; handlers awaited inline — see OQ-3 |
| Personality engine | ✅ | N/A | mood/energy/arousal/attachment decay |
| State machine (HSM) | ✅ | N/A | 30-state; ownership boundary with behavior tree undefined — see OQ-4 |
| Behavior tree | ✅ | N/A | py_trees; both HSM and BT running simultaneously — see OQ-4 |
| Episodic memory | ✅ | N/A | SQLite; blocking reads — KI-016 aiosqlite migration pending |
| Working memory | ✅ | N/A | 5-min TTL RAM |
| Spatial memory | ✅ | N/A | Room fingerprints JSON |
| Vision pipeline | ✅ | ✅ Active | C920 USB; **yolo11n.pt** (not YOLOv8n) @ 8 FPS |
| Face recognition | ✅ | ✅ Active | SFace — Madhan ~95%, Indhu ~75% (re-enroll needed) |
| Emotion detection | ✅ | ✅ Active | DeepFace 7-emotion |
| Audio pipeline | ✅ | ✅ Active | hey_jarvis → STT → Claude → TTS → JBL Flip 5 |
| Token budget | ⚠️ Duplicate | N/A | Two separate trackers (cognition/llm.py + cognition/mind.py); neither persists to disk — see OQ-5 |
| LLM routing | ⚠️ Duplicate | N/A | Two call paths (LLMInterface + direct anthropic client in mind.py) — see OQ-6 |
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

**Phase 1 of docs/COSMO_MASTER_PLAN.md — brain foundation (software only).** Archive HSM + pet_brain + behavior_engine via port-before-archive checklist; land capability registry + intent model + action router; unify LLM paths and token budget. Wiring is deferred — the soldering plan becomes a Phase 4 doc (docs/HARDWARE_PLAN.md).

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
| OQ-3 | event_bus.py awaits handlers inline (not `create_task`). A slow CLIFF_DETECTED handler blocks the entire event loop. Switch to `asyncio.create_task` dispatch. | Latency |
| OQ-4 | Both py_trees behavior tree and 30-state HSM run simultaneously in cosmo_demo.py with no documented ownership boundary. Define split or kill one. | Architecture |
| OQ-5 | TokenBudget in cognition/llm.py and _DailyBudget in cognition/mind.py are separate and neither persists to disk. Unify into one class; persist to the **memory_meta SQLite table** (decided 2026-06-10 — atomic RMW, no JSON races). | Reliability |
| OQ-6 | THREE LLM call paths, not two: LLMInterface (prod, conversation.py), LLMRouter (tests only — dead in prod, delete), mind.py direct client. Unify on LLMInterface (decided 2026-06-10). | Maintainability |
| OQ-7 | Two sub-items in cognition/llm.py: **(a)** `generate_streaming` logs usage but never calls `token_budget.record()` — the main conversation path is UNCOUNTED against the 100K daily limit. **Phase-1 prerequisite for budget persistence (OQ-5)** — fix recording before unifying/persisting, otherwise we persist a lie. **(b)** No prompt caching (`cache_control`) on the repeated system prompt — independent savings, can land any time. | Cost |
| OQ-8 | `navigation._follow_loop` registers `@bus.on(PERSON_DETECTED)` inside the loop body (navigation.py:254) — one leaked subscription per follow command, dispatched forever. | Reliability |
