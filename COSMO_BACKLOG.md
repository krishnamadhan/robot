# Cosmo — Backlog

> Ordered by priority. Tick items as done. Add new items at the correct priority level.
> Session START: read this and pick the top unchecked item.
> Session END: update checkboxes, add newly discovered tasks.

---

## P0 — Blocked / Must do before anything else

- [ ] Reboot Pi — KI-024 config.txt overlay was commented out 2026-05-22; GPIO8/I2C bus 4 conflict + GPIO27 stale pin claim both clear on reboot

---

## P1 — Wire hardware (waiting on parcels or reboot)

- [ ] Wire OLED eyes (0x3C + 0x3D) → verify with `i2cdetect` → fix KI-019 I2C mutex → switch eyes.py to oled mode
- [ ] Enable BH1750 light sensor: `available: false → true` in hardware.yaml (already wired, just needs config flip)
- [ ] Enable TTP223 touch × 3 (belly removed): `available: false → true` in hardware.yaml
- [ ] Enable MPU-6050 gyro: `available: false → true` in hardware.yaml
- [ ] Enable PIR HC-SR501: `available: false → true` — ONLY AFTER reboot (KI-024)
- [ ] Enable HC-SR04 ultrasonic — BLOCKED on XT60 pigtail + capacitors arriving
- [ ] Enable motors (TB6612FNG real mode) — BLOCKED on XT60 pigtail + capacitors arriving
- [ ] Enable TCRT5000 cliff sensors × 2 — BLOCKED on parcel arriving

---

## P2 — Code improvements

- [ ] KI-016: migrate episodic memory to aiosqlite (currently blocking async loop)
- [ ] Re-enroll Indhu face: 20 samples, good light (currently 75% — target 90%+)
- [ ] Fix person.py docstring: still says "YOLOv8n" — model is yolo11n.pt
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

- [ ] Custom OpenWakeWord "hey_cosmo" model (Picovoice or OWW training)
- [ ] Switch wake word from hey_jarvis → hey_cosmo

---

## Done (recent)

- [x] Pet brain (cognition/pet_brain.py) — movement decision states wired
- [x] Camera auto-detect + degraded mode (no more crash loop)
- [x] mind.py: sound-first before Claude API call
- [x] mind.py: _speech_in_flight race condition fixed
- [x] behavior/engine.py: personality-aware wander
- [x] robot_control.py: deprecated with sys.exit(1)
- [x] Upgrade person detection to YOLO11n (ADR-012)
- [x] Local LLM: Ollama llama3.2:1b Q4_K_M 807MB (ADR-017)
- [x] Full P0-P4 code audit + safety fixes (2026-05-21)
- [x] Disk cleanup: removed CUDA/nvidia packages, freed 3.4GB
