# Cosmo — System Architecture

> Last updated: 2026-05-20  
> Auto-update: run `tools/update_docs.sh` to regenerate status tables from live system

---

## Design Philosophy

**Cosmo is a presence, not an assistant.**

- Non-verbal reactions are ALWAYS first: sounds + eye expressions + movement
- Speech only happens when explicitly triggered via wake word
- Personality drives all behavior — events modulate emotional state, emotional state drives behavior
- The robot should feel alive through *timing and restraint*, not constant chatter

**Reaction priority:**
```
Event → [ Sound + Eyes ] → [ Movement ] → [ Speech only if wake word ]
```

Never call LLM for routine events (person arrives, touch, gesture). LLM only on explicit voice query.

---

## System Map

```
┌─────────────────────────────────────────────────────────────────────┐
│                        RASPBERRY PI 5                               │
│                                                                     │
│  ┌──────────────┐    ┌──────────────────────────────────────────┐  │
│  │   SENSORS    │    │              EVENT BUS                   │  │
│  │              │───▶│  (asyncio pub/sub, priority queues)      │  │
│  │ C920 camera  │    │  SAFETY > HIGH > NORMAL > LOW            │  │
│  │ C920 mic     │    └──────────────┬───────────────────────────┘  │
│  │ HC-SR04      │                   │                              │
│  │ BH1750 light │    ┌──────────────▼───────────────────────────┐  │
│  │ MAX17043 bat │    │           PERSONALITY ENGINE             │  │
│  │ PIR          │    │  mood / energy / arousal / attachment    │  │
│  │ Touch x3     │    │  7-dim state, decays, persisted          │  │
│  │ Cliff x2     │    └──────────────┬───────────────────────────┘  │
│  │ MPU6050 IMU  │                   │                              │
│  └──────────────┘    ┌──────────────▼───────────────────────────┐  │
│                      │          BEHAVIOR TREE (100ms)            │  │
│  ┌──────────────┐    │  SAFETY → SLEEP → SOCIAL → AUTONOMOUS    │  │
│  │  ACTUATORS   │◀───│                                           │  │
│  │              │    └──────────────────────────────────────────┘  │
│  │ JBL speaker  │                                                  │
│  │ SSD1306 x2   │    ┌─────────────────────────────────────────┐   │
│  │ 4x TT motors │    │          COGNITION (on-demand only)     │   │
│  │ Pan/tilt     │    │  Wake word → STT → Claude API → TTS     │   │
│  │   servos     │    │  Intent parser → instant command        │   │
│  └──────────────┘    └─────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Module Status

| Module | File | Status | Notes |
|--------|------|--------|-------|
| Event Bus | `core/event_bus.py` | ✅ Working | 26 EventTypes, priority queues |
| Personality | `core/personality.py` | ✅ Working | 7-dim state, time-of-day, persisted |
| Behavior Tree | `core/behavior_tree.py` | ⚠️ Partial | SafetyTriggered is stub |
| State Machine | `core/state_machine.py` | ✅ Working | Hierarchical, 15+ states |
| Episodic Memory | `core/memory/episodic.py` | ⚠️ Partial | DB works, not injected into LLM yet |
| Working Memory | `core/memory/working.py` | ✅ Working | In-session context |
| Camera | `perception/vision/camera.py` | ✅ Working | C920, 320×240 @ 30fps |
| Face Recognition | `perception/vision/person.py` | ✅ Working | SFace ONNX (Madhan 97%, Indhu 75%) |
| Gesture | `perception/vision/gesture.py` | ✅ Working | opencv_skin (mediapipe pending aarch64) |
| Emotion (vision) | `perception/vision/vision_loop.py` | ✅ Working | HSEmotionCNN @ 2fps |
| Mic capture | `perception/audio/mic.py` | ✅ Working | C920 USB, 16kHz mono |
| Wake word | `perception/audio/wake_word.py` | ✅ Working | OWW "Hey Jarvis" @ 80ms |
| VAD | `perception/audio/vad.py` | ✅ Working | webrtcvad 30ms frames |
| STT | `perception/audio/stt.py` | ✅ Working | faster-whisper base.en ~1.5s |
| Audio pipeline | `perception/audio/pipeline.py` | ✅ Working | Full PASSIVE→THINKING FSM |
| TTS | `expression/speech.py` | ✅ Working | Piper offline → paplay → JBL |
| Sounds | `expression/sounds.py` | ✅ Working | 22 pre-baked sounds |
| Eyes | `expression/eyes.py` | ⚠️ Terminal | OLED pending A0 solder |
| LLM | `cognition/llm.py` | ⚠️ Suboptimal | Ollama 1b primary (slow), Claude fallback |
| Conversation | `cognition/conversation.py` | ✅ Working | Thread resume, context assembly |
| Intent Parser | `cognition/intent.py` | ✅ Working | Tanglish pattern matching |
| Motors | `hardware/motors.py` | ❌ Mocked | TB6612FNG burned — awaiting replacement |
| Servos | `hardware/servos.py` | ❌ Mocked | PCA9685 not wired |
| Sensors | `hardware/sensor_manager.py` | ⚠️ Partial | BH1750+battery real; PIR/touch/cliff mocked |
| Navigation | `behavior/navigation.py` | ⚠️ Mocked | Code ready, motors blocked |
| HW Registry | `hardware/registry.py` | ✅ Working | Probe-at-startup, /hardware API |
| Debug API | `services/api/service.py` | ✅ Working | Port 8000, /health /state /hardware |
| Camera stream | `perception/video/stream_server.py` | ✅ Working | Port 8080 |

---

## Event Types

```
SAFETY (block everything):
  CLIFF_DETECTED, OBSTACLE_CRITICAL, OBSTACLE_WARNING
  PICKUP_DETECTED, BATTERY_CRITICAL, MOTOR_STALL, THERMAL_WARNING

PERCEPTION (drive reactions):
  PERSON_DETECTED, PERSON_LOST, FACE_RECOGNIZED, FACE_UNKNOWN
  EMOTION_DETECTED, GESTURE_WAVE/THUMBS_UP/PEACE/FIST/LOVE/POINT
  TOUCH_DETECTED, TOUCH_LONG
  WAKE_WORD, SPEECH_DETECTED, SOUND_DETECTED
  MOTION_DETECTED, LIGHT_CHANGED, DISTANCE_UPDATED

STATE (internal):
  MOOD_CHANGED, ENERGY_CHANGED, BEHAVIOR_CHANGED, STATE_CHANGED

INTERACTION (LLM path only):
  CONVERSATION_START, CONVERSATION_END, RESPONSE_READY, USER_INTENT
```

---

## Reaction Table (Non-verbal first)

| Trigger | Sound | Eyes | Movement | Speech |
|---------|-------|------|----------|--------|
| Person arrives | warm_chime | happy_wide | small turn toward | ❌ (wave reaction instead) |
| Face recognized (Madhan) | recognition_pip | excited | wag/wiggle | ❌ |
| Face recognized (Indhu) | recognition_pip | gentle_happy | small nod | ❌ |
| Unknown face | curious_bip | curious_squint | slight back | ❌ |
| Wave gesture | wave_response | happy | wave back (servo) | ❌ |
| Thumbs up | excited_trill | star_eyes | happy spin | ❌ |
| Peace gesture | curious_pip | wink | small tilt | ❌ |
| Fist | worried_whimper | worried | back away | ❌ |
| Touch head (gentle) | purr | happy_close | lean in | ❌ |
| Touch head (rough) | yelp | hurt | pull back | ❌ |
| Picked up | worried_whimper | worried | freeze motors | only if wake word also said |
| Person leaving | sad_pip | sad | turn to watch | ❌ |
| Alone > 3min | bored_hum | bored | wander | ❌ |
| Alone > 10min | lonely_whimper | sad | return to home spot | ❌ |
| Low battery | battery_beep | sleepy | dock-seek | ❌ |
| Dark room | yawn_sound | sleepy | slow down | ❌ |
| Wake word heard | wake_chime | alert | face user | → STT → LLM → TTS |
| Obstacle | bump_sound | surprised | stop + back | ❌ |
| Cliff | alarm_pip | scared | stop + back + turn | ❌ |

**Rule:** LLM is called ONLY when wake word fires. Everything else is sound + eyes + movement lookup table.

---

## Voice Commands (Intent Parser — no LLM needed)

These are matched BEFORE LLM and execute instantly:

| Phrase (EN + Tamil) | Intent | Action |
|---------------------|--------|--------|
| "come here" / "inga vaa" | come_here | approach detected person |
| "follow me" / "enna follow panni" | follow_me | FOLLOW_MODE: track + move |
| "stop" / "nillu" / "stay" | stop | motors.stop(), WAIT state |
| "go away" / "poda" | go_away | retreat 50cm |
| "go to sleep" / "thungu" | sleep | SLEEP state |
| "wake up" | wake | IDLE state |
| "spin" / "dance" | dance | motor sequence |
| "go home" / "un idam poo" | go_home | navigate to home coordinate |
| "this is your place" | set_home | save current position as home |
| "what's that" / "enna itu" | vision_query | capture → Claude vision → speak |
| "follow me" | follow | enter follow mode |
| "charge" / "charge panni" | seek_dock | navigate to charging dock |

---

## LLM Architecture (Corrected)

**Current problem:** Ollama llama3.2:1b is primary (15-30s, poor personality). Claude is fallback.

**Target:**
```
Wake word → STT → Intent parser
                      │
              Intent matched? ──▶ YES → execute command instantly
                      │
                     NO → Claude Haiku 4.5 (primary, ~1s)
                               │
                         Offline? → Ollama (fallback, 15-30s)
```

**Context injected into every Claude call:**
- Current mood + energy + arousal (2-3 words, not numbers)
- Who is present + their detected emotion
- Time of day + light level
- Last 3 relevant episodic memories (importance-weighted)
- Semantic facts about person ("Madhan likes cricket", "Indhu dislikes loud noise")
- 1-2 sentences max response enforced in system prompt

---

## Sensor Capability Matrix (Movement Safety)

| Active Sensors | Max Speed | Allowed Behaviors |
|----------------|-----------|-------------------|
| None | 0% | No movement |
| Ultrasonic only | 40% | Forward with stop at 15cm, no cliff detection |
| Cliff sensors only | 30% | Cliff-safe turning, no distance guarantee |
| Ultrasonic + Cliff | 80% | Normal wander + avoid |
| + IMU | 80% | + pickup detection, tilt awareness |
| + Camera | 80% | + face tracking, pan/tilt servo |
| Full (all sensors) | 100% | All behaviors enabled |

Movement capability is published as MOVEMENT_CAPABILITY event at startup. Navigation checks this before executing any move.

---

## Known Issues / TODOs

1. SafetyTriggered BT node is STUB → cliff/obstacle/pickup don't block behavior tree
2. LLM memory injection not wired (episodic.retrieve() exists, not called in conversation.py)
3. "Hey Jarvis" wake word is wrong for a robot named Cosmo
4. LLM primary should be Claude Haiku 4.5, not Ollama 1b
5. Servo quirks fire probabilistically but don't move servos (code calls mock)
6. Face recognition for Indhu at 75% — needs re-enrollment with 15+ samples
7. Gesture detection on opencv_skin is noisy (false positives in some lighting)
8. No "go home" position stored yet — command exists, coordinate not saved
9. No routine learner — doesn't know that Madhan comes home at 7pm
10. Camera stream stays on even when idle — wastes ~15% CPU
