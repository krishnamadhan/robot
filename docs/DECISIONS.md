# DECISIONS.md — Architecture Decision Log

> Record every significant architectural decision here.  
> Future sessions must not reverse a decision without reading the rationale first.

---

## ADR-001: PM2 over systemd for process management
- **Date:** Early project
- **Decision:** Use PM2 (Node.js process manager) instead of systemd units
- **Rationale:** PM2 gives easy log aggregation, restart policies, and a single `pm2 status` for all processes. The Pi already runs banteragent (Node.js) under PM2 — unified process management is simpler.
- **Tradeoff:** PM2 must itself be started on boot. Mitigation: `pm2 startup systemd` and `pm2 save`.
- **Status:** ✅ Committed

## ADR-002: Claude Haiku as primary LLM, Ollama as fallback
- **Date:** Phase 1
- **Decision:** Use `claude-haiku-4-5-20251001` as primary brain. Ollama `llama3.2:1b` only activates if Claude API fails.
- **Rationale:** Claude Haiku: 1-2s latency, much better personality and context. Ollama: 51s cold start, 5-8s warm, inferior personality output.
- **Tradeoff:** Ongoing API cost. Mitigated by 100K token daily hard limit in mind.py + aggressive cooldowns.
- **Status:** ✅ Committed

## ADR-003: Two-tier brain architecture
- **Date:** Phase 1
- **Decision:** Rule engine (free, every 5s) handles reflexive sensor reactions. Claude API (paid, event-triggered) handles personality and conversation.
- **Rationale:** Sensor reactions (cliff → stop, low battery → slow down) must be instant and free. Claude calls are expensive and slow — only for social/emotional output.
- **Tradeoff:** Rule engine logic requires maintaining sensor → behaviour mappings manually.
- **Status:** ✅ Committed

## ADR-004: Parallel vision pipeline (4 separate loops)
- **Date:** Phase 1
- **Decision:** Separate asyncio loops for capture (30fps), detection (15fps), recognition (4fps), emotion (2fps)
- **Rationale:** Sequential pipeline caused face recognition to block person detection. Parallel loops let each run at its natural speed.
- **Tradeoff:** More complex code. Worth it — reduced vision latency significantly.
- **Status:** ✅ Committed

## ADR-005: faster-whisper base.en with beam=5 for STT
- **Date:** Phase 1
- **Decision:** Use base.en model with beam=5, not tiny.en or large
- **Rationale:** tiny.en: too many errors on Indian English. large: too slow on Pi 5. base.en beam=5: ~1.5s, good accuracy, handles Indian accent well.
- **Tradeoff:** ~1.5s STT latency. Acceptable — full pipeline is 2-3s which feels natural.
- **Status:** ✅ Committed

## ADR-006: Stay 2WD (not 4WD)
- **Date:** Phase 1 hardware planning
- **Decision:** Keep 2WD round acrylic chassis
- **Rationale:** 4WD: more complex, heavier, faster battery drain, harder drift calibration. 2WD is sufficient for apartment navigation.
- **Revisit:** Only if apartment navigation proves inadequate (tight corners, thick carpet)
- **Status:** ✅ Decided

## ADR-007: Logitech C920 USB over Pi Camera Module 3
- **Date:** Phase 1 hardware
- **Decision:** Use Logitech C920 (already owned) for Phase 1 and 2. Pi Camera Module 3 optional for Phase 3.
- **Rationale:** C920 fully capable, no additional cost. Pi Camera Module 3 would add 120° FOV and better autofocus but not needed yet.
- **Status:** ✅ Decided

## ADR-008: JBL Flip 5 via Bluetooth for audio output
- **Date:** Phase 1
- **Decision:** Use JBL Flip 5 (already owned) via PipeWire + A2DP as primary speaker.
- **Rationale:** Excellent sound quality, zero cost. Tradeoff: Bluetooth dependency, 1-2s Bluetooth latency before first audio.
- **Future:** PAM8403 + 3W 4Ω speaker (wired) ordered — will replace JBL dependency in Phase 5.
- **Status:** ✅ In use

## ADR-009: INMP441 I2S mic over USB mic
- **Date:** Hardware selection
- **Decision:** INMP441 I2S MEMS microphone
- **Rationale:** No USB overhead, direct I2S audio, good sensitivity for wake word detection. Compact.
- **Status:** ✅ Active

## ADR-010: Separate power rails for Pi logic and motor system
- **Date:** Hardware design
- **Decision:** Pi/sensors on DFRobot UPS HAT (18650 cells). Motors on dedicated 7.4V 2S LiPo. Shared GND only.
- **Rationale:** Motor current spikes corrupt Pi power. Brown-out at Pi = data loss + hardware damage. Isolation is mandatory.
- **Status:** ✅ Committed — DO NOT change this architecture

## ADR-011: gpiozero over RPi.GPIO as primary GPIO library
- **Date:** Phase 1
- **Decision:** Use gpiozero with RPi.GPIO fallback
- **Rationale:** gpiozero is higher-level, built for Pi 5, cleaner API. RPi.GPIO retained as fallback for any edge cases.
- **Status:** ✅ Committed
