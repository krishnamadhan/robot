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

---

## Stack Evaluation ADRs — 2026-05-21 (post external tech review)

> Context: Full external stack review run 2026-05-21 against Pi 5 aarch64 Python 3.13 constraints.
> Each ADR below locks in the verdict so future sessions don't re-litigate it.

## ADR-012: Upgrade YOLOv8n → YOLO11n for person detection
- **Date:** 2026-05-21
- **Decision:** Upgrade ultralytics to 11.x and switch model path from `yolov8n.pt` to `yolo11n.pt`
- **Rationale:** YOLO11n is 22% faster and 37% less complex than YOLOv8n at equivalent accuracy. Drop-in replacement — no architecture changes, same ultralytics API, same event bus output. If it regresses, revert model path in `config/models.yaml`.
- **Tradeoff:** Requires re-validating FPS and detection confidence at 320×240 on Pi 5. Low risk — isolated to `config/models.yaml` and `perception/vision/person.py`.
- **Implementation order:** Do this FIRST before any other stack upgrades — cleanest isolated change.
- **Status:** 🔧 In progress

## ADR-013: Keep SFace ONNX for face recognition
- **Date:** 2026-05-21
- **Decision:** No change — SFace via opencv-contrib stays
- **Rationale:** Evaluated FaceNet, ArcFace, dlib. None have better aarch64 Python 3.13 wheels at this model size. SFace at 85MB gives 85–97% accuracy on enrolled faces with low CPU. Alternatives require dlib compilation or >200MB models.
- **Status:** ✅ Locked — do not revisit until Google Coral USB Accelerator is fitted (Phase 5)

## ADR-014: Defer FER library swap for emotion detection
- **Date:** 2026-05-21
- **Decision:** Keep DeepFace for now; re-evaluate FER library when real sensor testing begins
- **Rationale:** DeepFace gives 7 emotion classes (needed for sad/fearful/disgusted nuance in personality). FER library is 3–5× faster but only 5 classes. Swap not worth it until we have face-in-frame test data to compare accuracy.
- **When to revisit:** After OLED eyes wired + face recognition confirmed stable in Phase 1.5 tests. If emotion detection is the CPU bottleneck, swap to FER then.
- **Status:** 📅 Deferred — do not swap without accuracy comparison on real Cosmo phrases/faces

## ADR-015: Keep OpenWakeWord; defer custom wake word to Phase 4
- **Date:** 2026-05-21
- **Decision:** Stay on OpenWakeWord `hey_jarvis`. Custom `hey_cosmo` training is Phase 4.
- **Rationale:** OWW has aarch64 Python 3.13 wheels. ~80ms detection latency. Picovoice Porcupine is more accurate for custom words but costs $/month and requires company account. Phase 4 plan: collect 500× "Hey Cosmo" clips → OWW custom training pipeline.
- **Status:** ✅ Locked until Phase 4

## ADR-016: Test Kitten Micro TTS in isolation before swapping from Piper
- **Date:** 2026-05-21
- **Decision:** Piper en_US-lessac-medium stays until Kitten Micro passes quality gate
- **Rationale:** Piper lessac-medium has specific voice character that Cosmo's personality phrases are tuned around. Kitten Micro is 25MB vs 61MB (same ONNX, streaming-capable) but prosody and cadence may differ. A robotic-sounding TTS would undermine the pet-feel that the whole personality system is built for.
- **Quality gate before swapping:** Run both models on 10 real Cosmo phrases from `config/personality.yaml`. Listen side-by-side. If Kitten prosody matches or beats Piper for casual/playful speech, swap.
- **Status:** 📅 Candidate — do quality test before any code change

## ADR-017: Quantize Ollama fallback LLM to Q4_K_M
- **Date:** 2026-05-21
- **Decision:** Replace `llama3.2:1b` Ollama model with `llama3.2:1b-instruct-q4_K_M`
- **Rationale:** Q4_K_M quantization reduces model size from ~1.3GB to ~700MB with negligible quality loss. Same model family, same Ollama API, no code changes. Frees ~600MB RAM headroom for future Phase 2/3 components.
- **How:** `ollama pull llama3.2:1b-instruct-q4_K_M` then update model name in `config/models.yaml`
- **Status:** 📅 Queued — do after YOLO11n validated

## ADR-018: Enable Anthropic prompt caching for conversation context
- **Date:** 2026-05-21
- **Decision:** Add `cache_control` to system prompt in `cognition/conversation.py` and `cognition/mind.py` Claude API calls
- **Rationale:** Per-person conversation context and the Cosmo system prompt are sent on every Claude call. Anthropic prompt caching (available May 2026) can reduce input token cost by ~90% for repeated context blocks. The per-person thread cache in `conversation.py` is already structured for this.
- **Implementation:** Add `{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}` to messages. No behaviour change.
- **Status:** 📅 Queued — do after YOLO11n + Ollama Q4_K_M
