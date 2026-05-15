Continue Cosmo robot pet development.
PURE SOFTWARE SESSION — no hardware wiring.

Pi SSH: pi@192.168.1.30 | pi@100.101.250.126
All code in ~/robot/
PM2: cosmo_demo running — DO NOT break it
DO NOT touch: banteragent, pi-monitor, .wwebjs_auth

## What's Physically Available
- Logitech C920 webcam (camera + mic) ✅
- JBL Flip 5 Bluetooth speaker ✅
- Everything else: USE MOCKS

## Current State
- Event bus, state machine, personality engine ✅
- Person detection (YOLOv8n 32FPS) ✅
- Face recognition (SFace — Madhan + Indhu enrolled) ✅
- Emotion detection (DeepFace/FER) ✅
- Voice pipeline (OWW → Whisper → Ollama → Piper) ✅
- 31 unit tests passing ✅
- cosmo_demo.py running via PM2 ✅

## Session Goal
Build ALL remaining software systems with mocks.
When hardware arrives → swap mock for real driver.
Zero wiring required today.

---

## BUILD LIST — Complete Everything Below

### 1. SENSOR MANAGER (hardware/sensor_manager.py)

Single unified manager for ALL sensors.
Every sensor has a mock that generates realistic data.
Real driver slot ready — swap mock when hardware arrives.

Mock behaviors (must feel realistic):
- BH1750: lux follows time-of-day curve (bright noon, dark night)
- PIR: random motion triggers every 2-5 min in simulation
- Touch: never triggers in mock (needs physical touch)
- APDS9960: random gesture every 10 min in simulation
- MPU6050: slight random drift, spikes when "shaken"
- UPS HAT: battery drains 0.1% per minute in simulation

Each sensor publishes to event_bus at correct priority:

```
TOUCH_DETECTED → SAFETY (interrupts everything)
PICKUP_DETECTED → SAFETY
CLIFF_DETECTED → SAFETY (mock always returns False)
BATTERY_CRITICAL → SAFETY
GESTURE_DETECTED → HIGH
MOTION_DETECTED → HIGH
FACE_RECOGNIZED → HIGH
LIGHT_CHANGED → NORMAL
BATTERY_LOW → NORMAL
```

Real driver slots (stub these, implement when hardware arrives):
```python
class BH1750Sensor(HardwareInterface):
    # Real: smbus2 read from 0x23
    # Mock: time-of-day lux curve
    pass

class PIRSensor(HardwareInterface):
    # Real: gpiozero DigitalInputDevice(16)
    # Mock: random trigger simulation
    pass

class TouchSensorArray(HardwareInterface):
    # Real: gpiozero on GPIO5, GPIO25, GPIO6, GPIO12
    # Mock: never triggers
    pass

class APDS9960Sensor(HardwareInterface):
    # Real: smbus2 read from 0x39
    # Mock: random gesture simulation
    pass

class MPU6050Sensor(HardwareInterface):
    # Real: smbus2 read from 0x68
    # Mock: slight drift + random shake
    pass

class CliffSensorArray(HardwareInterface):
    # Real: gpiozero on GPIO20, GPIO21 via LLC
    # Mock: always returns safe (no cliff)
    pass

class UltrasonicSensor(HardwareInterface):
    # Real: gpiozero DistanceSensor(echo=24, trigger=16)
    # Mock: returns 100cm (open space)
    pass

class ToFSensorArray(HardwareInterface):
    # Real: VL53L0X on I2C (front, left, right)
    # Mock: returns 100cm all directions
    pass
```

Wire ALL sensor events to personality:

```python
SENSOR_PERSONALITY_EFFECTS = {
    EventType.TOUCH_DETECTED:    {"mood": +0.15, "arousal": +0.10},
    EventType.PICKUP_DETECTED:   {"arousal": +0.30},
    EventType.GESTURE_DETECTED:  {"arousal": +0.10, "mood": +0.05},
    EventType.MOTION_DETECTED:   {"arousal": +0.05},
    EventType.LIGHT_DARK:        {"energy": -0.10},
    EventType.BATTERY_LOW:       {"mood": -0.05},
    EventType.BATTERY_CRITICAL:  {"mood": -0.20, "arousal": +0.30},
}
```

### 2. MOTOR CONTROL (hardware/motors.py)
Full production motor driver — real code, mock mode now. When TB6612FNG wires confirmed: set mock=False in config.
GPIO assignments (LOCKED — do not change):

```
GPIO17 → AIN1  (left dir 1)
GPIO18 → PWMA  (left speed — HW PWM0)
GPIO27 → STBY  (enable)
GPIO22 → AIN2  (left dir 2)
GPIO23 → BIN1  (right dir 1)
GPIO6  → BIN2  (right dir 2)
GPIO13 → PWMB  (right speed — HW PWM1)
```

Safety rules (hardcoded — never override):
* STBY = LOW on class init
* STBY = HIGH only after self_test passes
* AIN1 + AIN2 NEVER both HIGH (raises MotorSafetyError)
* Dead-man stop: watchdog kills motors if heartbeat > 500ms old
* Cliff detected → immediate stop, no ramp
* Pickup detected → motors off immediately

Motor API:

```python
class MotorController:
    async def forward(self, speed=0.55, ramp=True)
    async def backward(self, speed=0.55, ramp=True)
    async def turn_left(self, speed=0.5, duration=None)
    async def turn_right(self, speed=0.5, duration=None)
    async def stop(self, emergency=False)
    async def ramp_to(self, left: float, right: float,
                      emergency: bool = False)
    # emergency=True → instant, no ramp
    # emergency=False → smooth 150ms ramp
    
    # Trim for drift correction
    LEFT_TRIM: float = 1.0   # Reduce if drifts right
    RIGHT_TRIM: float = 1.0  # Reduce if drifts left
```

Mock motor: prints intended action + speed to log. Real motor: direct gpiozero PWMOutputDevice control.

### 3. OLED EYE ANIMATION ENGINE (expression/eyes.py)
Full implementation. Renders to terminal now. When SSD1306 arrives: change render_target to "oled".
12 expressions required:

```python
class EyeExpression(Enum):
    NEUTRAL   # Normal open eyes — default
    HAPPY     # Squinted up, curved like ^‿^
    EXCITED   # Very wide, pupils small, sparkling
    SAD       # Drooping, curved down
    ANGRY     # Narrowed, inner corners down
    SURPRISED # Huge open, tiny pupils
    SLEEPY    # Half-closed, slow blink
    LOVING    # Heart shape ♥
    CURIOUS   # One raised, slight tilt
    SCARED    # Wide + trembling effect
    CONFUSED  # One squinting, one normal
    PLAYFUL   # Wink
```

Animation requirements:
* 30 FPS loop
* Smooth interpolation between expressions (300ms transition)
* Procedural blinking every 3-7 seconds randomly
* Pupil tracking (moves toward attention target)
* Micro-expressions (50ms flash of different emotion)
* Breathing animation (subtle iris scale pulse in NEUTRAL)
* Catchlight dot in pupil (makes eyes look alive)

Render modes:
* "terminal" → Unicode block characters (use now)
* "png" → save frames for visual testing
* "oled" → luma.oled SSD1306 (when hardware arrives)

Build tools/eye_simulator.py:

```bash
python3 tools/eye_simulator.py
# Cycles all 12 expressions with transitions
# Shows blinking and pupil movement
# Runs until Ctrl+C
```

Wire expressions to events:

```python
EVENT_TO_EXPRESSION = {
    EventType.FACE_RECOGNIZED:         EyeExpression.HAPPY,
    EventType.TOUCH_DETECTED:          EyeExpression.LOVING,
    EventType.PICKUP_DETECTED:         EyeExpression.SURPRISED,
    EventType.GESTURE_DETECTED:        EyeExpression.CURIOUS,
    EventType.BATTERY_CRITICAL:        EyeExpression.SCARED,
    EventType.OBSTACLE_CRITICAL:       EyeExpression.SCARED,
    "emotion.happy":                   EyeExpression.HAPPY,
    "emotion.sad":                     EyeExpression.SAD,
    "emotion.angry":                   EyeExpression.ANGRY,
    "emotion.surprised":               EyeExpression.SURPRISED,
    "emotion.fearful":                 EyeExpression.SCARED,
    "state.IDLE_BORED":               EyeExpression.SAD,
    "state.SLEEPING":                  EyeExpression.SLEEPY,
    "state.INTERACTIVE":               EyeExpression.CURIOUS,
}
```

### 4. SOUND ENGINE (expression/sounds.py)
Non-speech audio expressions. Generate all sounds programmatically (no audio files). Output via PipeWire to JBL BT speaker.

Sound library:

```python
SOUNDS = {
    # Happy/positive
    "chirp_happy":    rising_tone(400→800Hz, 0.2s),
    "trill_excited":  rapid_oscillation(600Hz, 0.4s),
    "purr_content":   low_rumble(80Hz, 2.0s),
    "chime_greeting": chord([523,659,784]Hz, 0.5s),  # C-E-G
    
    # Curious/neutral
    "chirp_curious":  question_tone(500→700→600Hz, 0.3s),
    "beep_ack":       short_beep(800Hz, 0.1s),
    "beep_thinking":  slow_pulse(400Hz, 0.5s),
    
    # Negative/concerned
    "whimper_sad":    falling_tone(400→200Hz, 0.5s),
    "whimper_lonely": wavering_tone(300Hz, 0.8s),
    "alert_beep":     urgent_beep(1000Hz, 0.2s, repeat=3),
    
    # Special
    "boot_chime":     ascending_chord(0.8s),
    "sleep_exhale":   soft_exhale(0.6s),
    "yawn":           rising_falling(200→400→200Hz, 1.2s),
    "purr_petted":    content_rumble(100Hz, 3.0s),
    
    # Battery
    "battery_low":    descending_beeps(3),
    "battery_ok":     ascending_beep(1),
}
```

Generate with numpy + scipy, output via:

```python
import subprocess
# Route through PipeWire (already set up for JBL)
subprocess.run(['paplay', '--raw', '--rate=22050',
                '--channels=1', '--format=s16le'],
               input=audio_bytes)
```

Wire sounds to events:

```python
EVENT_TO_SOUND = {
    EventType.TOUCH_DETECTED:    "purr_petted",
    EventType.PICKUP_DETECTED:   "chirp_happy",
    EventType.WAKE_WORD:         "beep_ack",
    EventType.FACE_RECOGNIZED:   "chime_greeting",
    EventType.BATTERY_CRITICAL:  "battery_low",
    "state.SLEEPING":            "sleep_exhale",
    "state.IDLE_BORED":         "whimper_lonely",
    "system.ready":              "boot_chime",
}
```

### 5. NAVIGATION ENGINE (behavior/navigation.py)
Full navigation system — mock mode now, real when motors arrive.

Movement primitives:

```python
class NavigationEngine:
    async def forward(self, speed=None, duration=None)
    async def backward(self, speed=None, duration=None)
    async def turn_left(self, speed=0.5, degrees=None, duration=None)
    async def turn_right(self, speed=0.5, degrees=None, duration=None)
    async def stop(self, emergency=False)
    async def wander(self, duration=30)
    async def approach_target(self, target_x: float, distance_cm: float)
    async def retreat(self, duration=2.0)
    async def face_person(self, person_x: float)
```

Safety stack (priority order):

```python
SAFETY_PRIORITY = [
    ("cliff_detected",    "emergency_stop"),
    ("obstacle < 5cm",    "emergency_stop"),
    ("pickup_detected",   "motors_off"),
    ("battery_critical",  "stop_and_stay"),
    ("obstacle < 15cm",   "slow_and_avoid"),
    ("user_command",      "execute_if_safe"),
    ("autonomous",        "execute"),
]
```

Mock mode: logs intended movement to console. Real mode: calls MotorController methods.

Wander algorithm:

```python
async def wander(self, duration=30):
    end_time = time.time() + duration
    while time.time() < end_time:
        if not await self._is_safe_to_move():
            await self.stop()
            await asyncio.sleep(1)
            continue
        
        action = random.choices(
            ["forward", "slight_left", "slight_right", "pause"],
            weights=[0.50, 0.20, 0.20, 0.10]
        )[0]
        
        if action == "forward":
            await self.forward(speed=0.25, duration=random.uniform(1, 3))
        elif action == "slight_left":
            await self.turn_left(speed=0.3, duration=random.uniform(0.3, 0.8))
        elif action == "slight_right":
            await self.turn_right(speed=0.3, duration=random.uniform(0.3, 0.8))
        elif action == "pause":
            await asyncio.sleep(random.uniform(1, 3))
```

### 6. BEHAVIOR ENGINE (behavior/engine.py)
Weighted random idle + event-driven active behaviors.

Idle behaviors:

```python
IDLE_BEHAVIORS = [
    Behavior(name="look_left_right", weight=0.30, cooldown=15, energy_min=0.2,
             execute=look_around),
    Behavior(name="slow_blink", weight=0.25, cooldown=8,
             execute=slow_blink),
    Behavior(name="curious_sound", weight=0.15, cooldown=30, energy_min=0.4,
             execute=lambda: sounds.play("chirp_curious")),
    Behavior(name="wander", weight=0.10, cooldown=60, energy_min=0.5,
             execute=navigator.wander),
    Behavior(name="seek_attention", weight=0.20, cooldown=120, energy_max=0.3,
             execute=seek_attention),
    Behavior(name="breathe", weight=0.35, cooldown=5,
             execute=breathing_animation),
]
```

Proactive speech triggers:

```python
PROACTIVE_TRIGGERS = [
    {
        "name": "greet_person",
        "condition": "person_enters AND mood > 0.3",
        "cooldown_min": 30,
        "phrases": [
            "Ayyo {name}! Vandhuttiya?",
            "Hey {name}! Enna da nee?",
            "{name}! Miss panni irundhein!",
        ]
    },
    {
        "name": "comfort_sad_person",
        "condition": "detected_emotion == SAD",
        "cooldown_min": 60,
        "phrases": [
            "Enna achu da {name}? Sad-a irukkiya?",
            "Hey, {name}... Pesalam if you want.",
            "Romba seri illa da {name}? I'm here.",
        ]
    },
    {
        "name": "lonely",
        "condition": "no_person_for > 20min",
        "cooldown_min": 45,
        "phrases": [
            "Romba bore aagudhu da...",
            "Yaarum illaya? Sooo quiet.",
            "*makes sad beeping noise*",
        ]
    },
    {
        "name": "morning",
        "condition": "first_person AND time 06:00-10:00",
        "once_per_day": True,
        "phrases": [
            "Good morning da! Coffee kudichiya?",
            "Vanakam! Nalla thoongina?",
        ]
    },
    {
        "name": "noticed_emotion_change",
        "condition": "emotion changed significantly",
        "cooldown_min": 15,
        "phrases": [
            "Suddenly happy-a? Enna nalla news?",
            "Looks like something happened!",
        ]
    }
]
```

### 7. SERVO + PAN-TILT (hardware/servos.py)
Full servo controller — mock now, real when PCA9685 arrives.

```python
class ServoController:
    CAMERA_PAN  = 0   # MG90S — rotates camera left/right
    CAMERA_TILT = 1   # MG90S — tilts camera up/down
    ULTRASONIC  = 2   # MG90S — rotates HC-SR04 for sweep
    
    PAN_MIN, PAN_MAX   = 30, 150   # 120° range
    TILT_MIN, TILT_MAX = 60, 120   # 60° range
    
    async def pan_to(self, angle: float, smooth: bool = True)
    async def tilt_to(self, angle: float, smooth: bool = True)
    async def center(self)
    async def track_person(self, person_x: float, person_y: float)
    async def sweep_ultrasonic(self) -> Dict[int, float]
```

Person tracking algorithm:

```python
async def track_person(self, person_x: float, person_y: float):
    DEAD_ZONE = 0.15
    
    if abs(person_x) > DEAD_ZONE:
        pan_delta = person_x * 15
        new_pan = self.current_pan + pan_delta
        await self.pan_to(max(self.PAN_MIN, min(self.PAN_MAX, new_pan)), smooth=True)
    
    if abs(person_y) > DEAD_ZONE:
        tilt_delta = -person_y * 10
        new_tilt = self.current_tilt + tilt_delta
        await self.tilt_to(max(self.TILT_MIN, min(self.TILT_MAX, new_tilt)), smooth=True)
```

Mock mode: logs pan/tilt angles to dashboard.

### 8. INTENT PARSER (cognition/intent.py)
Parse spoken commands without LLM (fast, offline):

```python
INTENTS = {
    "come_here": {
        "patterns": ["come here", "come to me", "inga vaa", "vaa vaa", "this way"],
        "action": "navigator.approach_person"
    },
    "follow_me": {
        "patterns": ["follow me", "follow", "enna follow panni", "come with me"],
        "action": "navigator.follow_mode"
    },
    "stop": {
        "patterns": ["stop", "stay", "dont move", "nilllu", "pause"],
        "action": "navigator.stop"
    },
    "go_away": {
        "patterns": ["go away", "leave me", "poda", "po po"],
        "action": "navigator.retreat"
    },
    "how_are_you": {
        "patterns": ["how are you", "eppadi irukka", "how feeling", "whats up"],
        "action": "respond_mood_report"
    },
    "dance": {
        "patterns": ["dance", "aadunga", "move", "shake"],
        "action": "behavior.dance_move"
    },
    "sleep": {
        "patterns": ["sleep", "go to sleep", "thoonga pO", "rest"],
        "action": "state_machine.transition('SLEEPING')"
    },
    "wake_up": {
        "patterns": ["wake up", "ezhu", "hey", "cosmo"],
        "action": "state_machine.transition('IDLE')"
    }
}

class IntentParser:
    def parse(self, text: str) -> Optional[Intent]:
        text_lower = text.lower().strip()
        for intent_name, config in INTENTS.items():
            for pattern in config["patterns"]:
                if pattern in text_lower:
                    return Intent(name=intent_name, action=config["action"],
                                  confidence=1.0, raw_text=text)
        return None  # Let LLM handle it
```

In conversation.py:

```python
async def respond(self, user_text: str) -> str:
    intent = self.intent_parser.parse(user_text)
    if intent:
        await self.execute_intent(intent)
        return await self._get_intent_response(intent)
    return await self.llm.chat(...)
```

### 9. MEMORY TOOLS

tools/memory_browser.py:
```bash
python3 tools/memory_browser.py
# list / show <id> / person <name> / emotion <happy|sad>
# delete <id> / stats / export
```

tools/personality_tuner.py:
```bash
python3 tools/personality_tuner.py
# mood 0.8 / energy 0.3
# inject touch / inject pickup / inject face Madhan
# show / history
```

tools/cosmo_dashboard.py — upgrade sensor_monitor, add panels for:
* Current expression (ASCII eye preview)
* Active behavior name + timer
* Last 5 events in event bus
* Conversation last 3 turns
* Servo positions (mock)
* Motor state (mock)
* All sensor values (real or mock)
* Personality state (mood/energy/arousal bars)

### 10. FIX KNOWN ISSUES

Fix 1 — Re-enroll Indhu face (75% → 90%+):
```bash
rm -rf ~/.robot/memory/faces/indhu/
python3 tools/enroll_face.py --name "Indhu" --samples 20 --angles 5
```

Fix 2 — STT background noise:
```python
# Add energy threshold in stt.py
# Calibrate: python3 tools/audio_calibrate.py
```

Fix 3 — Wake word rename:
```python
# In wake_word.py: rename "hey_jarvis" → "hey_cosmo" in all event data/logs
# User still says "Hey Jarvis" physically
# Document as temporary until custom model
```

Fix 4 — ANTHROPIC_API_KEY:
```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." >> ~/robot/.env
```

Fix 5 — Verify Ollama keep_alive:
```bash
curl http://localhost:11434/api/generate \
  -d '{"model":"llama3.2:1b","keep_alive":"2h"}'
ollama ps
```

### 11. INTEGRATION WIRING
Wire everything together in main.py / cosmo_demo.py:

```python
# Vision → Personality
vision.on_face_recognized → personality.on_person_seen
vision.on_emotion_detected → personality.on_emotion_contagion
vision.on_person_lost → personality.on_loneliness_tick

# Vision → Behavior
vision.on_person_detected → behavior.approach_or_greet
vision.on_face_recognized → proactive_speech.greet_person
vision.on_emotion_sad → proactive_speech.comfort_person

# Vision → Servo (mock)
vision.person_x_y → servo.track_person

# Sensor → Personality
sensors.on_touch → personality.boost_mood
sensors.on_pickup → personality.spike_arousal
sensors.on_gesture → behavior.react_to_gesture
sensors.on_motion_pir → state_machine.alert

# Sensor → Expression
sensors.on_touch → eyes.set(LOVING, duration=3)
sensors.on_pickup → eyes.set(SURPRISED)
sensors.on_gesture → eyes.set(CURIOUS)

# Sensor → Sound
sensors.on_touch → sounds.play("purr_petted")
sensors.on_pickup → sounds.play("chirp_happy")
sensors.on_motion → sounds.play("chirp_curious")

# Personality → Expression
personality.on_mood_change → eyes.update_expression
personality.on_energy_low → eyes.set(SLEEPY)

# Personality → Behavior
personality.on_boredom → behavior.seek_attention
personality.on_loneliness → proactive_speech.lonely

# Behavior → All outputs
behavior.idle_look_around → eyes.move_pupil
behavior.wander → navigation.wander (mock)
behavior.seek_attention → tts.speak + eyes.SAD

# State Machine → Everything
state.SLEEPING → sounds.sleep_exhale + eyes.SLEEPY
state.ALERT → eyes.CURIOUS + sounds.chirp_curious
state.INTERACTIVE → eyes.set_bright + stop_wandering
state.IDLE → start_idle_behavior_loop
```

### 12. PERFORMANCE TARGETS
```bash
python3 tools/profiler.py --duration 60
```

Targets:
* Idle CPU: < 35% total
* RAM: < 5GB (Ollama loaded)
* Camera pipeline: > 10 FPS
* Event bus latency: < 2ms
* Eye animation: 30 FPS smooth
* Wake word: < 300ms detection
* End-to-end voice: < 8s

---

## SUCCESS CRITERIA
Session complete when:
1. ✅ Sensor manager running with realistic mocks
2. ✅ All sensor events wiring to personality
3. ✅ Motor controller with full safety system (mock)
4. ✅ All 12 eye expressions rendering in terminal
5. ✅ Eye simulator tool works and feels alive
6. ✅ All sounds generating via numpy (no audio files)
7. ✅ Navigation engine with wander algorithm (mock)
8. ✅ Intent parser handles 10+ Tamil+English commands
9. ✅ Behavior engine firing idle behaviors every 3-8s
10. ✅ Proactive speech triggers working
11. ✅ Servo controller with person tracking (mock)
12. ✅ Memory browser tool working
13. ✅ Personality tuner tool working
14. ✅ Full dashboard showing all systems
15. ✅ Everything wired: vision→personality→expression→sound
16. ✅ All tests passing (target: 50+ tests)
17. ✅ Indhu face confidence > 85%
18. ✅ cosmo_demo.py running with all new systems

---

## WHEN HARDWARE ARRIVES
The only changes needed:
```yaml
# In config/hardware.yaml:
simulation:
  enabled: false

# Or per-sensor:
sensors:
  bh1750:  { mock: false }
  pir:     { mock: false }
  motors:  { mock: false }
  oled:    { render_target: "oled" }
  servos:  { mock: false }
```

Zero code changes for most components. Just config flags + physical wiring.

---

## RULES
* Mock everything not physically available
* Test every module before moving to next
* Commit after every working module
* DO NOT touch banteragent, pi-monitor, .wwebjs_auth
* DO NOT restart cosmo_demo mid-session unnecessarily
* If cosmo_demo breaks → fix before continuing
* Tanglish personality in ALL user-facing strings
* Every new file needs a unit test

Start with: check current PM2 status + run existing tests. Then build in order: 1 → 2 → 3 → 4 → 5 → ... Report after each: built ✅, tested ✅, any blockers.
