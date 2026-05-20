# Cosmo — Redesign Plan v2

> Last updated: 2026-05-20  
> Reviewed by: independent architecture agent (see REVIEW_NOTES.md)

---

## Core Philosophy Change

**The robot was talking too much. It should mostly be silent.**

Old model: Event → LLM → speak  
New model: Event → sound + eyes + movement (→ speech ONLY if wake word fired)

Cosmo understands everything happening around it. It just doesn't narrate its life.
Think of a dog — it reads the room, reacts physically, but only "speaks" (barks) when something actually warrants it.

---

## 1. LLM Backend (Corrected from v1)

**Switch primary to Claude Haiku 4.5. Ollama as offline-only fallback.**

```python
# cognition/llm.py — new priority
1. Claude Haiku 4.5 (API, ~1-2s, always consistent personality)
2. Ollama llama3.2:1b (offline fallback only, 15-30s, acceptable degradation)
```

**Cost**: ~$0.07/day at 100k tokens. Negligible.  
**Why not Ollama primary**: 15-30s response kills the "alive" feeling. 2 seconds feels responsive.

**Context injected every call** (compact, ~200 tokens max):
```
Cosmo is a small robot pet in Madhan and Indhu's home. Tamil/English household.
Personality: {mood_word}, {energy_word}. Currently: {time_of_day}.
Person: {person_name or "no one"}, looks {emotion}.
Memory: {last_3_relevant_episodes_one_line_each}
Respond in 1-2 sentences. No disclaimers. Never say "As an AI". Be warm, slightly cheeky.
```

---

## 2. Wake Word (Corrected from v1)

**OWW cannot train custom models. Porcupine is the correct path.**

| Option | Latency | Accuracy | Effort | Verdict |
|--------|---------|----------|--------|---------|
| OWW "Hey Jarvis" | 80ms | Good | Zero | Use now (wrong name but works) |
| Porcupine "Hey Cosmo" | 50ms | Better | 1 account + 5min | **Recommended** |
| STT fallback | 1500ms | Perfect | Zero | Last resort |

**Action**: Get free Picovoice account → train "Hey Cosmo" model → download .ppn → set PICOVOICE_KEY env var. The code already supports this (PorcupineDetector in wake_word.py). Takes 5 minutes.

---

## 3. Reaction System (Core redesign)

### Non-verbal reaction table (no LLM called for any of these)

| Trigger | Sound | Eyes | Movement | Notes |
|---------|-------|------|----------|-------|
| Person arrives (known) | warm_chime | happy_wide | turn toward | No speech |
| Person arrives (unknown) | curious_bip | curious_squint | slight retreat | No speech |
| Person leaves | sad_pip | droopy | watch them go | No speech |
| Wave detected | wave_response | happy | servo wave | No speech |
| Thumbs up | excited_trill | star_eyes | small spin | No speech |
| Peace | curious_pip | wink | head tilt | No speech |
| Fist / angry | worried_whimper | worried | back away | No speech |
| Touch head gentle | purr | eyes_close | lean into | No speech |
| Touch head rough | yelp | hurt | pull back | No speech |
| Picked up (IMU) | worried_whimper | worried | freeze motors | No speech |
| Obstacle < 15cm | soft_bump | blink | slow to 30% | No speech |
| Obstacle < 5cm | alarm_pip | scared | stop + back | No speech |
| Cliff detected | alarm_pip | scared | back + turn 90° | No speech |
| Battery < 20% | battery_beep | sleepy | return to dock | No speech |
| Dark room (lux < 10) | yawn_sound | sleepy | slow movements | No speech |
| Alone > 3min | bored_hum | bored | wander | No speech |
| Alone > 10min | lonely_whimper | sad | return home spot | No speech |
| Wake word | wake_chime | alert | face user | → STT → LLM → TTS |

### When Cosmo speaks:
1. Wake word triggered → LLM response (always)
2. Direct question detected in ambient audio → LLM response (intent parser catches)
3. First morning greeting (once per day) → one pre-baked line, no LLM
4. Battery critical → one pre-baked line ("I need to charge")

**That's it. Everything else is non-verbal.**

---

## 4. Voice Commands (Intent Parser)

Commands bypass LLM entirely — pattern matched, execute instantly.

```python
# cognition/intent.py — extend existing patterns
COMMANDS = {
    "come_here":   ["come here", "inga vaa", "vaa da", "come to me"],
    "follow_me":   ["follow me", "follow pannu", "enna follow panni"],
    "stop":        ["stop", "stay", "nillu", "freeze", "dont move"],
    "go_away":     ["go away", "poda", "move away", "leave"],
    "go_home":     ["go home", "un idam poo", "go to your place", "go back"],
    "set_home":    ["this is your place", "stay here", "this is home"],
    "what_is_that":["what is that", "what's that", "enna itu", "what is this"],
    "sleep":       ["go to sleep", "thungu", "sleep", "nap time"],
    "wake":        ["wake up", "ezhuntu", "good morning"],
    "dance":       ["dance", "spin", "aadu", "kuthu"],
    "charge":      ["charge", "go charge", "charge panni"],
}
```

**"What's that"** flow:
1. Capture camera frame
2. Send to Claude vision API with: "Describe what's on the floor/in front of this robot in 1 sentence. Focus on objects, not people."
3. TTS speaks the response
4. Does NOT save to memory (ephemeral query)

**"Go home"** flow:
1. Check if home_position is saved in `~/.robot/home_position.json`
2. If saved: navigate using dead reckoning (IMU + motor odometry) toward coordinate
3. If not saved: play confused_bip sound, look around
4. Limitation: works only in same room, will drift over distance

**"Set home"** flow:
1. Save current IMU position + compass heading to `~/.robot/home_position.json`
2. Play confirmation chime
3. Show "home" in eyes briefly

---

## 5. Movement with Partial Sensors

**Sensor capability levels** — checked at startup and on sensor loss:

```python
class MovementCapability(Enum):
    NONE      = 0   # no sensors: DO NOT MOVE
    CLIFF_ONLY = 1  # cliff only: slow, turning OK, no distance
    SONIC_ONLY = 2  # ultrasonic only: forward OK, no cliff detect
    BASIC      = 3  # sonic + cliff: normal wander
    STANDARD   = 4  # + IMU: pickup detect, tilt aware
    FULL       = 5  # + camera: face tracking, pan/tilt

MAX_SPEED = {
    MovementCapability.NONE:       0.0,
    MovementCapability.CLIFF_ONLY: 0.30,
    MovementCapability.SONIC_ONLY: 0.40,
    MovementCapability.BASIC:      0.80,
    MovementCapability.STANDARD:   0.80,
    MovementCapability.FULL:       1.00,
}
```

Navigation must check capability before every move command. If capability degrades mid-run (sensor dies), navigation immediately reduces speed.

**Cliff calibration** (reviewer flagged this is missing):
- On first boot with cliff sensors enabled, run `tools/calibrate_cliff.py`
- Records baseline readings at 30cm above ground (safe) and at actual cliff
- Saves thresholds to `config/cliff_calibration.yaml`
- Never use cliff sensors without running calibration first

---

## 6. Memory System (Phased)

**Phase 2A (implement now — 1 day):**
- Fix: wire episodic.retrieve() into conversation.respond()
- Add: `~/.robot/memory/facts.json` — simple key-value semantic memory
  - "madhan_likes": ["cricket", "Tamil music"]
  - "indhu_dislikes": ["loud noises", "being woken up"]
  - Updated via: "remember that I like X" / "remember Indhu hates Y"

**Phase 2B (defer — needs 3 weeks of data):**
- Routine learner: time-series pattern matching on arrival/departure
- Implement only after you have real usage data

---

## 7. Motor Safety (Prevent chip burnout)

Reviewer confirmed: **No motor watchdog exists. This must be built.**

```python
# hardware/motors.py — add watchdog
class MotorWatchdog:
    TIMEOUT_S = 10  # if no command for 10s, force STBY low

    async def run(self):
        while True:
            if (time.monotonic() - self._last_command_ts) > self.TIMEOUT_S:
                self._force_stby()  # GPIO STBY pin → LOW
            await asyncio.sleep(1.0)
```

Also: battery voltage compensation for motor speed:
```python
def voltage_compensated_speed(base_speed, battery_pct):
    # Motors run slower at low battery — compensate to maintain consistent behavior
    voltage_factor = 0.7 + (battery_pct / 100) * 0.3  # 0.7–1.0 range
    return min(base_speed / voltage_factor, 1.0)
```

---

## 8. Architecture Simplification (reviewer recommendation)

**State machine**: Collapse from 30 states to 8 core states:
```
SAFE_MODE     ← cliff/obstacle/pickup/battery critical
SLEEPING      ← energy < 0.2 or night time
IDLE          ← no person, waiting
ALERT         ← motion/sound detected, looking around
ATTENTIVE     ← person present, watching
INTERACTIVE   ← wake word fired, listening/responding
NAVIGATING    ← following/wandering/going home
EXPRESSING    ← playing out emotion animation
```

Personality dimensions (mood, energy, arousal) modulate behavior WITHIN states, not through state transitions. This simplification cuts debugging surface by 60%.

---

## 9. Bluetooth Reliability (reviewer flagged)

TTS hangs if JBL speaker disconnects mid-speech (paplay blocks up to 5s).

Fix:
```python
# expression/speech.py
async def _ensure_bt_connected(self) -> bool:
    """Check BT sink exists, reconnect if needed."""
    result = subprocess.run(["pactl", "list", "sinks", "short"],
                           capture_output=True, text=True, timeout=2)
    if "bluez" not in result.stdout:
        subprocess.run(["bluetoothctl", "connect", BT_MAC], timeout=5)
        await asyncio.sleep(1.5)
    return "bluez" in result.stdout

# Wrap all paplay calls with timeout
async def speak(self, text, interrupt=True):
    if not await self._ensure_bt_connected():
        log.warning("tts.bt_unavailable")
        return  # silent fallback — don't hang
```

---

## Implementation Priority

| # | Task | Effort | Impact | Blocks |
|---|------|--------|--------|--------|
| 1 | Switch Claude Haiku as LLM primary | 30min | HIGH | personality quality |
| 2 | Wire episodic memory into conversation | 1hr | HIGH | memory recall |
| 3 | Add motor watchdog | 2hr | HIGH | chip safety |
| 4 | Fix BT reconnect + TTS timeout | 1hr | HIGH | reliability |
| 5 | Add MovementCapability check to nav | 2hr | HIGH | movement safety |
| 6 | Get Porcupine "Hey Cosmo" trained | 20min | MEDIUM | wake word |
| 7 | Add cliff calibration tool | 2hr | MEDIUM | cliff safety |
| 8 | Add semantic memory (facts.json) | 1 day | MEDIUM | memory |
| 9 | Collapse state machine to 8 states | 3 days | MEDIUM | debuggability |
| 10 | Routine learner | 2 weeks | LOW | defer |
