# External Architecture Review Notes

> Reviewed: 2026-05-20  
> Reviewer: Independent architecture agent (no codebase context prior to review)

---

## Critical Issues (Must Fix)

### 1. OpenWakeWord Cannot Train Custom Models
**Finding**: OWW only ships fixed bundled models (hey_jarvis, alexa, etc.). "Train custom OWW model" is not possible without forking the library.  
**Fix**: Use Porcupine personal tier (free). 5 minutes, way better accuracy. Code already supports it via PICOVOICE_KEY.

### 2. Motor Watchdog Missing
**Finding**: If behavior engine crashes, motors run indefinitely.  
**Fix**: Add 10s watchdog timer that forces STBY low if no command received.

### 3. Cliff Sensors Need Calibration Routine
**Finding**: No baseline calibration exists. Without it, sensor will either false-trigger or miss real cliffs.  
**Fix**: `tools/calibrate_cliff.py` — run once after wiring, saves thresholds to yaml.

### 4. Motor Speed Doesn't Reduce on Missing Sensors
**Finding**: MovementCapability concept exists in plan but not in code. Sensor failure = full speed ahead.  
**Fix**: Add MovementCapability enum to navigation.py, check before every move.

### 5. Voice Command Intent Parser is Dead Code
**Finding**: `perception/audio/commands.py` is 49 lines of comments. No intent parsing wired to event bus.  
**Fix**: Extend existing `cognition/intent.py` patterns (which DO work) to handle navigation commands.

### 6. Motor Speed Asymmetry on Low Battery
**Finding**: At low LiPo voltage, left/right motors spin at different actual speeds (even with same PWM), causing the bot to veer.  
**Fix**: Voltage-compensated speed formula using real-time battery reading from MAX17043.

### 7. Bluetooth TTS Hangs
**Finding**: `paplay` blocks up to 5s if JBL speaker disconnects. Async pipeline stalls.  
**Fix**: Check BT sink exists before every speak(), reconnect if missing, 3s max timeout on paplay.

---

## Good Findings (Keep These)

- Event bus architecture is clean and correct
- Personality engine (7-dim, decay, persisted) is well designed
- Hardware registry (real/mock/error tracking) is excellent pattern
- Episodic memory foundation is solid
- Mock system (hardware.yaml gating) is good for development

---

## Over-engineered (Simplify)

- State machine: 30 states → collapse to 8 core states
- HAL mock layer: 2000+ LOC for unmounted sensors — simplify to timer-based fake events
- Event bus priority levels: for single-process robot, asyncio task priority is sufficient

---

## Under-engineered (Add This)

- Motor watchdog (safety critical)
- Cliff calibration routine (safety critical)
- Battery voltage compensation for motor speed
- BT speaker reconnection logic
- Capability-gated movement (sensor → max speed matrix)

---

## Deferred (Good Calls)

- Routine learner: needs 3 weeks real data first. Defer.
- Navigation dead reckoning: needs encoders first. Motors without encoders = drift.
- Custom OWW: not possible. Use Porcupine instead.
- LIDAR: overkill for apartment pet robot.
