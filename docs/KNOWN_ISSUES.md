# KNOWN_ISSUES.md — Bug Log & Failed Approaches

> Read this before touching any existing code.  
> If marked FAILED — do NOT attempt again without a new strategy.  
> Append new issues. Never delete.

---

## Fixed This Session (2026-05-28)

### KI-CRASH-01: Cosmo crash loop — 34 restarts
- **Status:** FIXED
- **Symptom:** C920 not plugged in → camera.start() returns False → cosmo_demo.py hard-exits → PM2 restarts → repeat
- **Fix:** camera.py auto-scans /dev/video0–18 for USB camera (skips Pi ISP video19+). cosmo_demo.py continues in degraded no-vision mode instead of return-on-failure.

### KI-ROBOT-CTRL-DEPRECATED: robot_control.py dangerous pinout
- **Status:** FIXED — sys.exit(1) guard added
- **Symptom:** Old 2WD script uses GPIO6 (UPS HAT adapter-fail pin) for BIN2. Running it would burn HAT converter.
- **Fix:** robot_control.py now prints error and exits immediately. Use tools/motor_test.py instead.

### KI-MIND-RACE-01: mind.py speech race + cooldown burn
- **Status:** FIXED
- **Symptom:** (1) Two concurrent trigger events could both pass tts.is_speaking gate before TTS state flipped. (2) Cooldowns burned even when API call timed out or errored.
- **Fix:** `_speech_in_flight` asyncio.Event blocks concurrent callers. Cooldowns committed only after successful TTS handoff.

### KI-MIND-NONVERBAL-01: Speech before non-verbal reaction
- **Status:** FIXED
- **Symptom:** Triggers went straight to Claude API with no eye/sound reaction first. Robot felt like a voice assistant not a pet.
- **Fix:** `_NONVERBAL` dict fires eye expression + sound cue BEFORE Claude call, with 0.2–0.5s organic delay.

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
- **Status:** ✅ FIXED (2026-05-20) — moved to Fixed Issues below

### KI-011: Face recognition not triggering — person must be within ~80cm
- **Status:** Open — low priority (by design, but should be documented)
- **Service:** perception/vision/face.py, perception/vision/person.py
- **Symptom:** Face recognition never logs during the 2026-05-20 test. State shows `person: no one` even when person is present but far away.
- **Root cause:** Recognition loop runs at 4 FPS and only processes the largest detected person bbox. At 320×240 resolution, face bbox is too small for SFace to match if person is >100cm away.
- **Expected behaviour:** Known limitation — face recognition works at ≤80cm. Document this clearly.
- **Fix path:** At further distances, trigger person greeting as "unknown person" rather than ignoring. Do not increase resolution (CPU cost).
- **Priority:** Low — document and accept

### KI-013: Motor direction pin ordering — brief both-HIGH glitch on direction change (CRITICAL)
- **Status:** ✅ Fixed in commit 3d00059 (2026-05-21)
- **Service:** hardware/motors.py line 125
- **Fix applied:** `self._in2.off(); self._in1.on()` — clear OFF pin before setting ON pin. Same pattern applied to all direction changes in `_MotorChannel.set()`.
- **Note:** Also refactored motors.py (2026-05-21) to support 4WD nested hardware.yaml structure (left_front/left_rear/right_front/right_rear). Flat `mc.ain1`, `mc.pwm_a` attributes replaced with nested `mc.left_front.ain1`, `mc.left_front.pwm`, etc.

### KI-014: Claude API call in mind.py has no timeout — can hang thread pool forever
- **Status:** ✅ Fixed (2026-05-21)
- **Service:** cognition/mind.py line ~423
- **Symptom:** `run_in_executor(None, lambda: self._client.messages.create(...))` submits the synchronous Anthropic client call to asyncio's default thread pool executor with no `asyncio.wait_for()` wrapper. If the Claude API hangs (network drop, upstream issue), the executor thread blocks indefinitely. Default executor has 8 threads on Pi 5 — 8 concurrent hangs = entire asyncio loop blocked.
- **Root cause:** Missing timeout on synchronous API call wrapped in executor.
- **Fix:**
  ```python
  response = await asyncio.wait_for(
      loop.run_in_executor(None, lambda: self._client.messages.create(...)),
      timeout=15.0
  )
  ```
- **Priority:** Medium — only fires if Claude API goes down, but recovery is full process restart
- **Addendum (AB-010, 2026-07-08):** two residual unbounded paths found and fixed:
  `generate_streaming` had no timeout on `messages.stream` (stalled connection hung
  the speech loop — now passes `timeout=CLAUDE_TIMEOUT_S`, an httpx read timeout
  bounding inter-chunk gaps), and `/trigger/describe` used a **sync** Anthropic
  client with no timeout inside the async API — blocked the whole :8000 loop
  (now AsyncAnthropic + 30s). Tests: tests/unit/test_timeouts_ab010.py.

### KI-015: Piper TTS subprocess has no timeout — _speaking flag can lock forever
- **Status:** ✅ Fixed (2026-05-21)
- **Service:** expression/speech.py line ~80
- **Symptom:** `piper.communicate(text.encode("utf-8"))` blocks until Piper process exits. No timeout. If Piper hangs (OOM, corrupt model file, I/O stall), the `_speaking` flag stays `True` permanently. All future TTS calls are silently dropped. Cosmo goes mute with no recovery path except full restart.
- **Root cause:** Missing timeout on subprocess.communicate().
- **Fix:**
  ```python
  try:
      stdout, stderr = piper.communicate(text.encode("utf-8"), timeout=10.0)
  except subprocess.TimeoutExpired:
      piper.kill()
      log.error("speech.piper_timeout", text_len=len(text))
  finally:
      self._speaking = False
  ```
- **Priority:** Medium — mutes Cosmo silently, hard to diagnose
- **Addendum (AB-010, 2026-07-08):** residual gap fixed — `paplay.wait()` after a
  successful piper synth was unbounded (wedged audio sink froze the speech thread;
  `_speaking` itself was safe via finally, but the thread never returned). Now
  bounded by audio duration + 5s, killed on expiry. Test in
  tests/unit/test_timeouts_ab010.py::test_paplay_wedge_is_killed.

### KI-016: SQLite episodic memory — concurrent writes from thread pool cause "database is locked"
- **Status:** ✅ Resolved 2026-06-12 — episodic.py migrated to aiosqlite (single async connection serializes all access; `initialize()`/`close()` now async, call sites updated in main.py / cosmo_demo.py / memory_browser.py; tests rewritten to async API). TokenBudget keeps its own separate sync conn (WAL, atomic UPSERT) — unaffected.
- **Service:** core/memory/episodic.py line ~63
- **Symptom:** `sqlite3.connect(..., check_same_thread=False)` is used with multiple `run_in_executor()` calls hitting the default ThreadPoolExecutor. Under concurrent emotion + face + conversation writes, SQLite's default 5s busy timeout may expire, raising `OperationalError: database is locked` and silently dropping the memory write.
- **Root cause:** Shared sqlite3 connection across threads without a write serialisation layer.
- **Fix:** Migrate to `aiosqlite` — do not bolt asyncio.Lock() onto the existing sync calls. Project is Python 3.13, async-first throughout; aiosqlite removes the executor overhead entirely and gives a proper async write path. sqlite3.connect timeout bump is a band-aid, not a fix.
- **Priority:** Low — only triggers under simultaneous recognition + conversation writes. Upgrade to aiosqlite when touching episodic.py next.

### KI-017: Claude API budget check in conversation.py not inside async lock — concurrent calls can double-spend
- **Status:** ✅ Resolved 2026-06-12 — `TokenBudget.try_reserve()/release()` reservation counter (cognition/llm.py). No lock needed: asyncio is single-threaded and check+reserve has no await between them, so it is atomic. `_call_claude` and `generate_streaming` reserve EST_CALL_TOKENS=2000 before the awaited API call and release in `finally`; actual spend recorded via `record()`. `claude_allowed()` now accounts for in-flight reservations. Tests: TestBudgetReservation in tests/unit/test_token_budget.py (incl. double-spend scenario).
- **Service:** cognition/conversation.py
- **Symptom:** Budget check (read daily_tokens → compare → allow) and budget update (write daily_tokens + spend) are not inside the same asyncio.Lock. Two concurrent `respond()` calls (e.g., two people speaking at once, or wake word + proactive speech race) can both pass the budget check before either records its spend. This can overdraw the 100K daily token budget.
- **Root cause:** Read-check-write is not atomic.
- **Fix:**
  ```python
  async with self._budget_lock:
      if self._daily_tokens + estimated_tokens > DAILY_BUDGET:
          raise BudgetExceeded()
      self._daily_tokens += estimated_tokens
  ```
- **Priority:** Low — requires unusual concurrency. Budget overruns are cost exposure, not a crash.

### KI-019: Shared I2C bus has no mutex — concurrent sensor + OLED writes collide
- **Status:** ✅ Resolved 2026-06-12 — `hardware/i2c_bus.py` exposes a process-wide `threading.Lock` (`i2c_lock`). threading.Lock, not asyncio.Lock as originally sketched: the OLED render runs on an executor *thread* (`run_in_executor` in eyes.py) while sensor reads are sync calls on the loop thread — asyncio.Lock cannot serialize across threads. Held around every smbus transaction (BH1750 + UPS HAT) and per OLED frame display (released between the two eyes so sensor reads can interleave). battery_monitor.py is a separate process (reads via cosmo API, lock not applicable). Tests: tests/unit/test_i2c_lock.py.
- **Service:** hardware/sensor_manager.py, expression/eyes.py
- **Symptom:** Multiple async tasks share `smbus.SMBus(1)` with no locking. When OLED rendering (eyes.py) runs concurrently with sensor polling (BH1750, MPU-6050, UPS HAT battery reads), they can collide mid-transaction and raise `OSError: [Errno 121] Remote I/O error`. Silent sensor drop or display glitch.
- **Root cause:** No `asyncio.Lock()` protecting the physical I2C bus. smbus2 is not thread/coroutine-safe.
- **Becomes active when:** OLED eyes are wired and enabled. Until then, sensor_manager is the only I2C writer and collisions are unlikely.
- **Fix:** One shared `asyncio.Lock` in a central location (e.g. `hardware/i2c_bus.py`), wrapping every `smbus` transaction across sensor_manager and eyes.py:
  ```python
  async with i2c_lock:
      self._bus.write_i2c_block_data(...)
  ```
- **Priority:** Fix before wiring OLED eyes — collisions guaranteed once both sensor polling and display rendering are live

### KI-020: Spatial memory write is not atomic — power loss mid-write corrupts JSON
- **Status:** ✅ Resolved (verified 2026-06-12) — spatial.py:206-208 already uses the temp-file + `os.replace` pattern from the fix sketch.
- **Service:** core/memory/spatial.py line ~200
- **Symptom:** `SPATIAL_PATH.write_text(json.dumps(data))` truncates the file before writing. If the Pi loses power mid-write (UPS HAT limits this but doesn't eliminate it), `spatial.json` is left as an empty or partial file. Next boot: `json.JSONDecodeError` in spatial memory load, Cosmo loses all room fingerprints permanently.
- **Root cause:** `Path.write_text()` is not atomic across all conditions. No temp-file pattern used.
- **Fix:** Write to a temp file then atomically rename:
  ```python
  tmp = SPATIAL_PATH.with_suffix(".tmp")
  tmp.write_text(json.dumps(data, indent=2))
  tmp.replace(SPATIAL_PATH)   # os.replace() — atomic on Linux
  ```
- **Priority:** Low — UPS HAT makes clean shutdown likely, but still worth fixing

### KI-021: dtoverlay=inmp441-pi5 still active — GPIO20/21 latent conflict with motor direction pins
- **Status:** Open — fix before enabling motors
- **Service:** /boot/firmware/config.txt, hardware/motors.py
- **Symptom:** `/boot/firmware/config.txt` has `dtoverlay=inmp441-pi5` active. The I2S overlay claims GPIO18 (BCLK), GPIO19 (LRCLK), GPIO20 (DIN), GPIO21 (DOUT). GPIO18/19 are used as hardware PWM for motors — PWM is a separate function, no conflict. But GPIO20/21 are motor AIN1/AIN2 direction pins AND I2S DIN/DOUT. Kernel may fight gpiozero for ownership of those pins.
- **Not live yet:** INMP441 is `available: false` and motors are mock mode — conflict is latent, not active.
- **Fix before motor enable:** Comment out `dtoverlay=inmp441-pi5` and `dtparam=i2s=on` in `/boot/firmware/config.txt`, then reboot. Confirm `i2cdetect` and motor init still work. INMP441 will need GPIO reassignment before re-enabling anyway.
- **Priority:** Must fix before connecting XT60 pigtail / enabling real motor driver

### KI-022: Working memory has no automatic expiry loop — _store grows unbounded
- **Status:** ✅ Resolved 2026-06-12 — `wm.start()` cleanup loop existed and was wired in tools/cosmo_demo.py; now also wired in main.py, and the task holds a strong reference (bare create_task could be GC'd).
- **Service:** core/memory/working.py line ~80
- **Symptom:** `purge_expired()` exists and correctly deletes expired entries, but it's only called when `snapshot()` is triggered manually. If nothing calls `snapshot()`, expired entries accumulate in `_store` indefinitely. Slow heap growth over days of runtime.
- **Root cause:** No background cleanup task wired up.
- **Fix:** Add a periodic cleanup coroutine on startup:
  ```python
  async def _cleanup_loop(self) -> None:
      while True:
          await asyncio.sleep(60)
          removed = self.purge_expired()
          if removed:
              log.debug("working_memory.purged", count=removed)
  ```
- **Priority:** Low — entries are small, growth is slow. Fix when next touching working.py.

### KI-023: Ollama timeout set to 90s — longer than worst-case cold start
- **Status:** ✅ Resolved (verified 2026-06-12) — `OLLAMA_TIMEOUT_S` is already 60.0 in cognition/llm.py.
- **Service:** cognition/llm.py — `OLLAMA_TIMEOUT_S = 90.0`
- **Symptom:** Ollama cold start is ~51s. Timeout is 90s — so a hung/unresponsive Ollama blocks the fallback path for up to 90s before giving up. During that window the robot is silent.
- **Fix:** Lower `OLLAMA_TIMEOUT_S` to 60 — covers cold start with 9s margin, halves worst-case hang time.
- **Priority:** Low — Ollama is already the fallback-of-last-resort. Claude Haiku is primary.

### KI-018: State machine silently accepts transitions to unregistered states
- **Status:** ✅ Obsolete 2026-06-12 — core/state_machine.py was archived (OQ-4, now archive/state_machine.py); behavior tree is the sole decision authority, so this code path no longer runs.
- **Service:** core/state_machine.py line ~244
- **Symptom:** When `transition_to()` is called with a state not in the registered transitions map, the state machine falls through to a permissive catch-all that allows the transition anyway and logs nothing. Silent state corruption — HSM ends up in a state with no registered exit transitions, breaking the whole behaviour loop.
- **Root cause:** Permissive fallback in transition guard. Should be an error.
- **Fix:** Replace fallback with explicit error log + reject the transition:
  ```python
  log.error("state_machine.unregistered_transition", from_state=current, to_state=target)
  return False  # reject instead of silently allow
  ```
- **Priority:** Low — only fires on code bugs (typo'd state name). But silent failures are very hard to debug.

### KI-024: GPIO8 double-claimed — PIR sensor AND I2C software bus 4 SDA
- **Status:** Config fixed 2026-05-22 — overlay commented out in /boot/firmware/config.txt; reboot required to take effect
- **Service:** /boot/firmware/config.txt, hardware/sensor_manager.py (PIR)
- **Symptom:** `/boot/firmware/config.txt` has `dtoverlay=i2c-gpio,bus=4,i2c_gpio_sda=8,i2c_gpio_scl=9`. This creates a software I2C bus on GPIO8 (SDA) and GPIO9 (SCL). GPIO8 is simultaneously the PIR motion sensor pin. When the PIR sensor is enabled, both the kernel I2C driver and gpiozero will compete for GPIO8 ownership — kernel wins, PIR reads garbage or raises permission error.
- **Fix (requires reboot):**
  1. Comment out `dtoverlay=i2c-gpio,bus=4,i2c_gpio_sda=8,i2c_gpio_scl=9` in `/boot/firmware/config.txt`
  2. Reboot
  3. Verify no I2C bus 4 devices are connected that need that overlay
- **Priority:** Fix before enabling PIR sensor (`pir.available: true`)

### KI-025: OLLAMA_TIMEOUT_S too long + streaming path token usage not recorded
- **Status:** Partially fixed (2026-05-21) — logging added, budget enforcement still caller-side
- **Service:** cognition/llm.py
- **Symptom (a):** `OLLAMA_TIMEOUT_S = 90.0` but Ollama cold start is ~51s. A hung Ollama blocks the path for up to 90s.
- **Fix (a):** Lower to 60s (applied 2026-05-21 — see KI-023 now folded in here).
- **Symptom (b):** `generate_streaming()` yields sentences but never called `_budget.record()`. Token usage was invisible to the daily budget enforcer in mind.py and conversation.py.
- **Fix (b):** Added `stream.get_final_message()` call after streaming completes — usage is now logged. Callers (conversation.py) still need to wire the usage into their budget. Tracked as KI-017.

---

## Fixed Issues

### KI-012: CUDA torch silently crashing — person detection on HOG fallback (FIXED 2026-05-21)
- **Status:** Fixed
- **Service:** perception/vision/person.py
- **Symptom:** Cosmo's person detection had been running HOG fallback since the NVIDIA packages were removed. YOLO appeared to load but threw `libcublasLt` errors at runtime. No visible crash — silent degradation. Explains "performance felt off" reports from weeks prior.
- **Root cause:** torch installed as a CUDA build. After `nvidia-*` packages were removed (2026-05-20 disk cleanup), `libcublasLt.so.13` disappeared. ultralytics swallowed the import error and fell back to OpenCV HOG detector silently.
- **Fix:** Replaced with `torch==2.11.0+cpu` + `torchvision==0.26.0+cpu` from `https://download.pytorch.org/whl/cpu`. Downloaded `yolo11n.pt` (5.4MB), placed in `models/`, updated `config/models.yaml` to local path.
- **Impact:** First working YOLO on this Pi. 10.1 FPS measured at 320×240 on Pi 5 CPU (pipeline target: 8 FPS). All person-gated systems (gesture detection, proactive speech, face recognition handoff) now fire correctly.
- **Discovered / Fixed:** 2026-05-21

### KI-010: Victory gesture false positives (FIXED 2026-05-20)
- **Root cause:** opencv_skin backend returns conf 0.78–0.80 for Victory on background blobs. Default threshold was too low.
- **Fix applied (gesture.py):**
  1. Added per-gesture `_GESTURE_THRESHOLDS` dict — Victory threshold set to 0.92 (above what opencv_skin ever returns for background blobs)
  2. `VICTORY_HOLD_FRAMES = 4` — Victory now requires 4 consecutive matching frames
  3. `_streaks: Dict[str, int]` replaces the old single `_wave_streak` counter — all gestures tracked independently
  4. Person-gating in `_loop()`: if `cosmo_bb.person_visible == False`, skip all detection entirely (eliminates background false positives AND saves CPU)
- **Fix applied (mind.py):**
  - Added `from expression.speech import tts` import (was NameError crashing proactive speech)
  - Added `_subscribe_events()` method with bus.on() handlers wired for all 5 trigger types
  - Added per-trigger `_trigger_last` dict for independent cooldowns
- **Fix applied (cosmo_demo.py):**
  - Race condition in face event handler: replaced `_state["person_name"] == "no one"` guard with module-level `_approaching` flag + try/finally
  - Wired `sm.transition_to()` calls on person_detected, face_recognized, person_lost, wake_word
- **Fix applied (behavior_tree.py):**
  - Added `audio_speaking: bool` to `CosmoBlackboard` — gated `_GestureAction`, `DoEngagePresence`, `DoIdleSound` to skip sounds while TTS is active
- **Fix applied (motors.py + hardware.yaml):**
  - `LEFT_TRIM` / `RIGHT_TRIM` moved from hardcoded class attributes to `config/hardware.yaml` under `motors.left_trim` / `motors.right_trim`
  - `MotorController.__init__()` loads trim from config with safe fallback

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

## KI-025 — Face embeddings enrolled on R/B-swapped frames (2026-07-02)
The camera shipped R/B-swapped frames until the 2026-07-02 colour fix
(perception/vision/camera.py). SFace embeddings for Madhan (~95%) and Indhu
(~75%) were enrolled against swapped colours — recognition confidence may drop
now that frames are correct. **Fix: re-enroll both** with
`python3 tools/enroll_face.py` (20 samples, good light) when each is present.
Status: OPEN — needs humans present.
