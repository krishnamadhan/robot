# Phase 1 — Port-Before-Archive Checklist (item 1.1)

> Drift guard 1: nothing moves to `archive/` until every live entry point below
> is mapped to its new intent/router home AND the mapping is implemented.
> Check the box only when the replacement is live.

## A. core/state_machine.py (HSM, singleton `sm`)

| Entry point | What it does today | New home |
|---|---|---|
| [x] cosmo_demo.py:51 import, :509 `sm.start()` | boots HSM | delete — BT is sole authority |
| [x] cosmo_demo.py:211 `ALERT_PERSON` on PERSON_DETECTED | state mirror | blackboard `cosmo_bb` already holds person state; delete |
| [x] cosmo_demo.py:231 `INTERACTIVE` on FACE_RECOGNIZED | state mirror | delete (bb has it) |
| [x] cosmo_demo.py:273 `IDLE_CURIOUS` on PERSON_LOST | state mirror | delete (bb has it) |
| [x] cosmo_demo.py:317 `LISTENING` on WAKE_WORD | state mirror | delete (conversation owns listening state) |
| [x] main.py:72–132 (start + person/touch/cliff/battery transitions) | legacy launcher (NOT the PM2 entry) | strip HSM usage; cliff/battery safety = SAFETY-priority bus events + Intent.FLEE/ALERT |
| [x] tools/sensor_monitor.py:195,204,236 `sm.stats()` | diagnostics display | replace with CapabilityRegistry `snapshot()` |
| [x] behavior/engine.py:20 import | unused-by-then | dies with engine archive |

## B. cognition/pet_brain.py (singleton `pet_brain`)

| Entry point | What it does today | New home |
|---|---|---|
| [x] cosmo_demo.py:46 import, :576 start, :645 stop | boots tick loop | delete |
| [x] Subscriptions: PERSON_DETECTED / FACE_RECOGNIZED / PERSON_LOST | pure context writes | blackboard already tracks (D6: handlers = sensor→bb writers) |
| [x] `_decide()` priorities: FLEE / PLAY / APPROACH_PERSON / SEEK_LIGHT / CURIOUS_WANDER / RESTING | 1 Hz drive loop, direct nav+eyes+sounds | BT drive nodes emitting `Intent.FLEE / EXPRESS_JOY / APPROACH / EXPRESS_CURIOSITY / IDLE_FIDGET`; personality-modulated speed/duration moves into router executor params |
| [x] `_approach_dir()` person-direction steering | turn toward person_x | router APPROACH executor (keep logic) |
| [x] obstacle gate `dist_cm < 20` movement block | safety check before move | router LOCOMOTION executor pre-check (keep) |
| [x] personality_learning.record_outcome on move | learning feedback | router executor post-hook (keep) |
| [x] tools/pet_brain_test.py | test dashboard | archive alongside module |

## C. behavior/engine.py (singleton `behavior_engine`)

| Entry point | What it does today | New home |
|---|---|---|
| [x] cosmo_demo.py:41 import, :570 start | boots 3 loops | delete |
| [x] USER_INTENT → `dance()` (cosmo_demo:334) | spin sequence + sounds | router executor for `Intent.EXPRESS_JOY` (variant "dance"); VoiceCommand emits it |
| [x] USER_INTENT → `happy_reaction()` (:301, :336) | eyes+chirp+phrase | router executor `Intent.EXPRESS_JOY` |
| [x] USER_INTENT → `love_reaction()` (:338) | eyes+purr+phrase | router executor `Intent.EXPRESS_AFFECTION` |
| [x] USER_INTENT → `_look_around()` (:345) | eye/servo scan | router executor `Intent.EXPRESS_CURIOSITY` |
| [x] on_emotion physical reactions (cosmo_demo:300–305: happy→happy_reaction, sad→approach, angry→backward) | direct actuation in handler | handler becomes bb writer; BT emits `EXPRESS_JOY / COMFORT / FLEE` (item 1.5) |
| [x] Subscriptions: FACE_RECOGNIZED / PERSON_LOST / EMOTION_DETECTED | context writes | blackboard (already tracked) |
| [x] Idle behaviors: look_around, slow_blink, curious_sound, purr_idle, wander, seek_attention, breathe (+ `_apply_personality_weights`) | weighted idle loop | BT idle node emitting `IDLE_FIDGET` / `EXPRESS_CURIOSITY`; weight logic + routines port to router |
| [x] Proactive triggers: greet_person, comfort_sad, lonely, morning, emotion_changed (+ Tanglish phrase lists, cooldowns, `_proactive_lock`, 60 s global gap) | trigger loop → tts.speak | D1 carve-out: phrases + timing port into router speech executors; conditions become BT nodes; ambient tier (D4 Ollama-first) |
| [x] Ambient loop: curiosity questions, `_bring_up_memory` (episodic), `_wonder_aloud` | 30 s tick → tts.speak | `Intent.ASK_QUESTION` path, ambient tier; episodic bring-up feeds item 1.12 |
| [x] Sleep-hours / wind-down / wake-chirp logic | time gating in loops | port as router/BT gating now; superseded by Phase 2 circadian (2.3) |
| [x] `_play_sound_gated` (mood/energy/cooldown sound gate) | shared gate | port into router as shared expression gate |

## D. Cross-cutting

- [x] grep after archive: no prod import of `state_machine`, `pet_brain`, `behavior.engine`
- [x] every USER_INTENT action string in cosmo_demo:319–349 has a router-backed equivalent (nav.* strings stay, behavior.* strings re-route)
- [x] tools that import archived modules (pet_brain_test, sensor_monitor) updated or archived
