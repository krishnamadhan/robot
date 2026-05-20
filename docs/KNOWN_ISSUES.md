# KNOWN_ISSUES.md — Bug Log & Failed Approaches

> Read this before touching any existing code.  
> If marked FAILED — do NOT attempt again without a new strategy.  
> Append new issues. Never delete.

---

## Open Issues

### KI-001: Wake word is "Hey Jarvis" not "Hey Cosmo"
- **Status:** Open — low priority
- **Service:** perception/audio/wake_word.py
- **Symptom:** Wake word model is OpenWakeWord's built-in `hey_jarvis`. Cosmo doesn't respond to "Hey Cosmo".
- **Root cause:** OpenWakeWord ships no `hey_cosmo` model. Custom model requires either Picovoice (company email needed) or OWW custom training (~500 audio samples).
- **Workaround:** System internally maps "hey_jarvis" trigger → Cosmo responds. Users just say "Hey Jarvis" for now.
- **Fix path:** Train via https://console.picovoice.ai OR collect 500× "Hey Cosmo" clips + openWakeWord custom training pipeline
- **Failed approaches:** None tried yet — deferred
- **Priority:** Phase 4

### KI-002: Indhu face recognition confidence ~75% (Madhan 85-97%)
- **Status:** Open — medium priority
- **Service:** perception/vision/face.py (SFace ONNX)
- **Symptom:** SFace gives Indhu lower confidence. Occasionally misidentifies or flags as unknown.
- **Root cause:** Enrollment samples likely had variable lighting, angle, or glasses.
- **Fix path:**
  ```bash
  rm -rf ~/.robot/memory/faces/indhu/
  python3 tools/enroll_face.py --name "Indhu" --samples 20
  # Good lighting, 5 distinct angles, no glasses, ~80cm distance
  ```
- **Failed approaches:** None — just hasn't been done properly
- **Priority:** Phase 1.5 task

### KI-003: LiPo XT60 pigtail not yet arrived
- **Status:** Pending delivery (Robocraze order)
- **Service:** hardware/motors.py
- **Symptom:** Motors running in mock mode. Real motor testing blocked.
- **Workaround:** 4× AA battery holder (6V) for temporary motor testing before LiPo
- **Action when pigtail arrives:**
  1. Confirm 470µF cap across VM+GND before connecting LiPo
  2. Confirm 220µF caps on motor terminal pairs
  3. Connect XT60 pigtail: LiPo (+) → XT60 female pigtail → TB6612FNG VM
  4. Switch motors.py from mock mode to real driver
  5. Test at 30% speed first

### KI-004: APDS-9960 original unit faulty
- **Status:** Pending replacement delivery (Robocraze order)
- **Service:** hardware/sensors/apds9960.py
- **Symptom:** Original APDS-9960 from Robu.in did not appear on I2C bus at expected 0x39
- **Root cause:** Faulty unit confirmed
- **Action:** Keep `available: false` in hardware.yaml until replacement arrives and confirmed at i2cdetect 0x39
- **Do NOT:** Waste time debugging the original unit — it's dead

### KI-005: GPIO6 conflict — battery_monitor vs TB6612FNG BIN2
- **Status:** Fixed in code — VERIFY IT'S COMMITTED
- **Affected files:** hardware/motors.py, battery-monitor process
- **Symptom:** battery_monitor.py originally used GPIO6 for AC-power detect pin. GPIO6 = TB6612FNG BIN2 (right motor backward direction). Simultaneous use would cause motor direction corruption.
- **Fix:** battery_monitor.py remapped to different GPIO
- **Verification:**
  ```bash
  grep -r "GPIO6\|gpio6\|pin=6" ~/robot/hardware/
  grep -r "GPIO6\|gpio6\|pin=6" ~/battery-monitor/
  # Neither should claim GPIO6 for non-motor use
  ```
- **⚠️ Risk:** If this fix is NOT committed, running both processes simultaneously could corrupt motor direction

### KI-006: STT picks up background noise / false transcriptions
- **Status:** Partially mitigated
- **Service:** perception/audio/stt.py, vad.py
- **Symptom:** faster-whisper occasionally transcribes background noise, fan noise, TV audio
- **Mitigations applied:**
  - Switched from tiny.en to base.en (much better quality, acceptable speed)
  - beam=5 (better accuracy, ~1.5s latency acceptable)
  - webrtcvad energy gating in `_has_speech_energy()` in stt.py
- **Next fix if still a problem:**
  ```bash
  python3 tools/audio_calibrate.py   # tune energy threshold
  ```
- **Failed approaches:** tiny.en model — too many errors, not worth the speed gain

### KI-007: Ollama llama3.2:1b cold start 51 seconds
- **Status:** Mitigated — not critical
- **Service:** cognition/llm.py
- **Symptom:** After 2h idle, Ollama unloads model. Next invocation takes 51s.
- **Mitigation:** `keep_alive: 2h` in Ollama config. Claude Haiku is now primary LLM for all responses — Ollama only used if Claude API fails.
- **Acceptable:** Yes — Claude Haiku latency is 1-2s. Ollama is offline fallback.
- **Do NOT:** Try to keep Ollama always loaded — uses 1.3GB RAM permanently on Pi

### KI-008: Personality trait drift not implemented
- **Status:** Deferred to Phase 5
- **Service:** core/personality.py
- **Context:** Session U5 from an earlier sprint was skipped. Cosmo doesn't yet gradually evolve its personality traits over weeks of interaction (e.g., becoming more adventurous if exploration is rewarded, more attached if touch interactions are frequent).
- **Impact:** Low right now — other physical hardware tasks are higher priority
- **Implementation plan when ready:** Add drift vectors to PersonalityState, updated weekly by memory consolidation job

### KI-009: OLED eyes in terminal render mode (not on physical hardware)
- **Status:** In Progress — top priority this session
- **Service:** expression/eyes.py
- **Context:** SSD1306 OLED hardware has arrived (0x3C left, 0x3D right). Eyes are rendering to terminal for debugging. Code supports OLED mode — just needs wiring + one-line switch.
- **Fix:** Wire OLEDs → verify i2cdetect → change render target to "oled"
- **See:** docs/NEXT_SESSION.md for full step-by-step

### KI-010: Victory gesture fires as false positive every ~3 seconds
- **Status:** Open — HIGH priority (causes constant bloop spam)
- **Service:** perception/vision/gesture.py, core/behavior_tree.py
- **Symptom:** `gesture.fired gesture=Victory conf=0.78` fires every 3s even when no hand is visible. This triggers `excited_trill` sound repeatedly — the "annoying bloops" reported by Madhan.
- **Root cause:** opencv_skin backend is detecting skin-coloured blobs in the frame background and classifying them as Victory gesture at conf 0.78 threshold. Threshold too low — Victory requires higher confidence than wave.
- **Workaround:** Mute sounds via `POST /sound/mute` during testing
- **Fix path:**
  1. Raise Victory confidence threshold from 0.78 → 0.88 in gesture.py
  2. Add consecutive-frame debounce: gesture must appear in 2/3 consecutive frames before firing
  3. OR: switch from opencv_skin to MediaPipe Hands for gesture backend (much more accurate)
- **Priority:** Fix before next test session

### KI-011: Face recognition not triggering — person must be within ~80cm
- **Status:** Open — low priority (by design, but should be documented)
- **Service:** perception/vision/face.py, perception/vision/person.py
- **Symptom:** Face recognition never logs during the 2026-05-20 test. State shows `person: no one` even when person is present but far away.
- **Root cause:** Recognition loop runs at 4 FPS and only processes the largest detected person bbox. At 320×240 resolution, face bbox is too small for SFace to match if person is >100cm away.
- **Expected behaviour:** Known limitation — face recognition works at ≤80cm. Document this clearly.
- **Fix path:** At further distances, trigger person greeting as "unknown person" rather than ignoring. Do not increase resolution (CPU cost).
- **Priority:** Low — document and accept

---

## Fixed Issues

*(move from Open to here when resolved, with fix date)*

---

## Architectural Dead Ends

**Do not repeat these approaches:**

- **tiny.en Whisper model for STT** — KI-006 — too many transcription errors on Indian English accents. Stay on base.en beam=5.
- **Polling camera in a tight loop for face recognition** — too expensive. Parallel 4-loop pipeline with different FPS per loop is the correct approach (vision_loop.py).
- **HTTP calls between internal robot services** — use event_bus.py (async pub/sub). HTTP adds latency and coupling.
- **Continuous Claude API calls without gating** — 100K daily budget burns in minutes. All Claude calls must go through mind.py's cooldown system.

---

## Sacred File Warning

```
~/banteragent/.wwebjs_auth/   ← WhatsApp session auth — delete = lose bot permanently
~/banteragent/.env            ← API keys — never log, never commit
```

These files must NEVER be deleted, moved, or modified by Claude Code.
