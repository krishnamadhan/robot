# Cosmo — Development Roadmap

---

## Phase 0: Architecture + Foundation ✅ (Current)

**Goal:** Solid, testable foundation before any hardware-specific code.

- [x] System architecture document
- [x] Complete directory structure with `__init__.py` and READMEs
- [x] Config system (YAML + Pydantic validation)
- [x] Structured logging (structlog, rotating files + console)
- [x] Async event bus (priority queues, history, dead letter)
- [x] Hardware abstraction layer (ABC + mock system)
- [x] Hierarchical state machine (30 states, timeout transitions)
- [x] Personality engine (continuous emotional state dynamics)
- [x] Three-tier memory system (working + episodic SQLite + spatial JSON)
- [x] Camera pipeline (Logitech USB webcam, async frame buffer)
- [x] Person detector (YOLOv8n primary, HOG fallback)
- [x] Developer tools (sensor_monitor.py dashboard)
- [x] main.py (full startup/shutdown lifecycle)

**Next:** Phase 1

---

## Phase 1: Camera + Eyes — Perception Foundation

**Goal:** Cosmo can see people, react with eyes, understand light.

**Prerequisites:** OLED displays and PIR arriving.

- [ ] Face detector (OpenCV DNN, `perception/vision/face.py`)
- [ ] Face recognizer (OpenCV LBPH, `perception/vision/face.py`)
  - Enrollment: "Hey Cosmo, meet Madhan" → stores face embedding
  - Recognition: identify Madhan vs Indhu vs strangers
- [ ] Emotion detector (FER lightweight, `perception/vision/emotion.py`)
- [ ] Motion detector (frame differencing, `perception/vision/motion.py`)
- [ ] OLED eye animations (`hardware/display/oled.py`, `expression/eyes.py`)
  - 12 expressions: neutral, happy, excited, sad, angry, surprised, confused,
    sleepy, loving, curious, scared, playful
  - Smooth interpolation between expressions
  - Procedural blinking (random 2-8 second intervals)
- [ ] PIR integration (`hardware/sensors/pir.py`)
- [ ] BH1750 real driver (already tested hardware, `hardware/sensors/light.py`)
- [ ] Scene classifier (`perception/vision/scene.py`) — room fingerprinting
- [ ] Attention system (`behavior/attention.py`) — saliency-based focus
- [ ] Sensor monitor real data (connect BH1750 live reading to dashboard)

**Success criteria:** Cosmo's eyes change expression when someone enters the room.

---

## Phase 2: Personality + Memory — The Brain

**Goal:** Cosmo has a genuine emotional life that persists across days.

- [ ] Conversation manager (`cognition/conversation.py`)
- [ ] LLM interface (`cognition/llm.py`)
  - Ollama with llama3.2:1b (primary, offline)
  - Claude Haiku fallback (if Ollama fails)
  - System prompt with mood/energy/context injection
- [ ] Intent parser (`cognition/intent.py`)
- [ ] Idle behavior generator (`behavior/idle.py`)
  - 10 weighted idle behaviors
  - Frequency based on energy level
  - Quirk system integration
- [ ] Routine system (`behavior/routines.py`)
  - Learn "Madhan comes home around 7pm"
  - "Indhu usually works at the table in the morning"
- [ ] Memory browser tool (`tools/memory_browser.py`)
- [ ] Personality tuner tool (`tools/personality_tuner.py`)

**Success criteria:** Cosmo references past interactions naturally in conversation.

---

## Phase 3: Movement + Navigation — Embodiment

**Goal:** Cosmo moves purposefully and safely around the home.

**Prerequisites:** HC-SR04, VL53L0X ToF sensors, TCRT5000 cliff sensors, MPU-6050, PCA9685.

- [ ] Motor control real driver (`hardware/motors.py`)
  - TB6612FNG real GPIO control (replacing test scripts)
  - PWM speed control, ramp, stall detection
- [ ] Ultrasonic sensor real driver (`hardware/sensors/ultrasonic.py`)
- [ ] ToF sensor real driver (`hardware/sensors/tof.py`)
- [ ] Cliff sensor real driver (`hardware/sensors/cliff.py`)
- [ ] IMU real driver (`hardware/sensors/imu.py`) — pickup detection
- [ ] Servo controller (`hardware/servos.py`) — PCA9685
  - Camera pan/tilt following person
  - Ultrasonic rotate for 180° sweep
- [ ] Safety constraint integration — all sensor stop conditions live
- [ ] Navigation engine (`behavior/navigation.py`)
  - Wander (random exploration with obstacle avoidance)
  - Approach (move toward person)
  - Retreat (back away from obstacle)
- [ ] Occupancy grid (`mapping/grid.py`) — 5cm resolution

**Success criteria:** Cosmo wanders the home, stops before obstacles and cliffs.

---

## Phase 4: Voice + Audio — Voice

**Goal:** Cosmo wakes to "Hey Cosmo", understands and speaks.

**Prerequisites:** INMP441 microphone, PAM8403 + speaker.

- [ ] Microphone pipeline (`hardware/audio/mic.py`) — I2S INMP441
- [ ] Voice activity detection (`perception/audio/vad.py`) — WebRTC VAD
- [ ] Wake word (`perception/audio/wake_word.py`) — OpenWakeWord "Hey Cosmo"
- [ ] Speech-to-text (`perception/audio/stt.py`) — faster-whisper tiny.en
  - Indian English accent handling
  - Tamil word recognition
- [ ] TTS output (`expression/speech.py`) — Piper offline
- [ ] Sound expression engine (`expression/sound.py`)
  - Chirps, whimpers, purring, beeps — generated with numpy/scipy
  - No audio files — all programmatic
- [ ] Audio environment analysis (`perception/audio/audio_analysis.py`)
  - Detect laughter, music, TV
  - Mood contagion from happy sounds

**Success criteria:** Full voice conversation loop works offline.

---

## Phase 5: Mapping + Autonomy — Spatial Intelligence

**Goal:** Cosmo knows where it is and learns routines.

- [ ] Room identification from sensor fingerprints
- [ ] Room naming ("Madhan called this the bedroom")
- [ ] Landmark detection and storage
- [ ] WiFi RSSI as room fingerprint feature
- [ ] Routine learning (time + person + location patterns)
- [ ] Autonomous exploration behavior
- [ ] Docking behavior (charge station recognition)
- [ ] 5-channel IR line detection (`hardware/sensors/ir_line.py`)
- [ ] Map visualization tool (ASCII art to terminal)

**Success criteria:** Cosmo correctly identifies which room it's in 90% of the time.

---

## Future Phases

- **Phase 6: Cloud Intelligence** — Hailo AI HAT for hardware-accelerated vision
- **Phase 7: Multi-Robot** — Cosmo communicates with other Cosmo instances
- **Phase 8: Learning** — personality evolves from months of interaction data
- **Phase 9: HomeKit/Matter integration** — awareness of smart home state

---

## Hardware Arrival Checklist

When new hardware arrives, enable in `config/hardware.yaml`:

| Component | Config key | Driver to implement |
|-----------|-----------|---------------------|
| OLED displays | `displays.left_eye.available` | `hardware/display/oled.py` |
| MPU-6050 | `sensors.mpu6050.available` | `hardware/sensors/imu.py` |
| HC-SR04 | `sensors.ultrasonic.available` | `hardware/sensors/ultrasonic.py` |
| PIR HC-SR501 | `sensors.pir.available` | `hardware/sensors/pir.py` |
| APDS-9960 | `sensors.apds9960.available` | `hardware/sensors/gesture.py` |
| Touch TTP223 | `sensors.touch.available` | `hardware/sensors/touch.py` |
| VL53L0X ×3 | `sensors.tof.front.available` | `hardware/sensors/tof.py` |
| INMP441 | `audio.mic.available` | `hardware/audio/mic.py` |
| PAM8403 | `audio.speaker.available` | `hardware/audio/speaker.py` |
| PCA9685 | `servos.available` | `hardware/servos.py` |
