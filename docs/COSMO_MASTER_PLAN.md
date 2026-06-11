# COSMO MASTER PLAN — living roadmap

> Capability-driven, alive-first, body-grows-later.
> Maintained per-phase. Each phase ends at a STOP gate: report, review, go-ahead.
> Last updated: 2026-06-10

## North Star

Separate INTENT from ACTION. Personality + world model generate intents
("greet Madhan", "that startled me"). A capability-aware router maps each
intent to the best output that physically exists right now. Same inner life,
different body completeness. Cosmo is fully alive with camera/mic/speaker
only; each new wire adds capability with **zero new behavior code**.

---

## Decisions (agreed 2026-06-10)

| # | Decision |
|---|----------|
| D1 | Archive **HSM (`core/state_machine.py`) + `cognition/pet_brain.py` + `behavior/engine.py`** to `archive/`. Behavior tree is the sole decision authority. Carve-out: port behavior_engine's proactive-trigger phrases/timing AND its live actuator routines (`dance`, `happy_reaction`, `love_reaction`) into the router before archiving. |
| D2 | New intent model lives in `core/intents.py` (`Intent` enum). Existing `cognition/intent.py` parser class renamed `VoiceCommand`. End state: VoiceCommand **emits** Intents through the same router — not a parallel world. |
| D3 | `LLMInterface` (cognition/llm.py) is the single LLM call path. Delete `LLMRouter` (dead in prod; update its tests). Refactor mind.py's direct anthropic client through LLMInterface. |
| D4 | Two-tier speech routing: **ambient** (lonely/startle/dark) → Ollama-first, Claude fallback. **Greet-by-name + conversation** → Claude Haiku direct (rare by construction, carries episodic recall — the path a 1B model fumbles). Tanglish deferred to Phase 2, decided per-path there. |
| D5 | Token budget persists to the **memory_meta SQLite table** (atomic read-modify-write; no JSON races). STATE.md OQ-5 updated to match. |
| D6 | Greeting consolidation is the template: event handlers become pure sensor→blackboard writers; BT decides; router acts. |
| D7 | Router policies: GREET/EXPRESS_* = ALL_AVAILABLE; APPROACH/FLEE/FOLLOW/COME = FIRST_AVAILABLE requiring LOCOMOTION. |

## Drift guards (design principles, not edge cases)

1. **Port-before-archive.** Nothing moves to `archive/` until every live entry
   point into it (USER_INTENT handlers, event subscriptions, direct calls from
   cosmo_demo) is enumerated and mapped to its new intent/router home.
2. **VoiceCommand emits Intents.** Voice command parsing is an intent *source*;
   "come here" → `Intent.COME` through the router, same as any internal drive.
3. **Expressive fallback is the default.** A movement intent with no LOCOMOTION
   falls back to an expressive substitute (leaning eyes + vocalization), never a
   silent no-op. A robot that *shows the want* reads alive; one that silently
   can't reads broken. Only intents with no meaningful expression (pure nav
   corrections) may no-op.

## Phase 1 exit criterion — migration-completeness check

Before Phase 1 reports done:
- [ ] grep: no USER_INTENT action string or emotion event has a dangling handler
- [ ] every actuator routine that existed pre-refactor is reachable through the router
- [ ] no prod import of archived modules remains (`state_machine`, `pet_brain`, `behavior.engine`)

---

## PHASE 0 — Memory system (Claude Code harness)

- [x] 0.1 Session-start rule in `~/.claude/CLAUDE.md`: open by stating STATE.md Next Priority + first action; never ask what to work on
- [x] 0.2 `~/bin/rebuild-memory-index.sh` auto-generates MEMORY.md; called from SessionEnd hook
- [x] 0.3 Freshness convention `<!-- verified: YYYY-MM-DD -->`; SessionStart flags files >21 days unverified
- [x] 0.4 Deleted `project_robot_snapshot.md` (project_robot.md / project_cosmo_inventory.md were already gone)
- [x] 0.5 SessionEnd: `git pull --rebase` before push; on conflict abort push + log to `~/.claude/logs`
- [x] **STOP — report** (2026-06-10)

> Stale-memory policy: **verify-on-touch**. When a flagged Cosmo-related memory file is actually used in a session, verify and re-stamp it then. Never bulk re-stamp; non-Cosmo stale files are ignored or deleted, not refreshed.

## PHASE 1 — Brain foundation (software only)

- [x] 1.1 Enumerate inbound calls to HSM / pet_brain / behavior_engine → docs/PHASE1_MIGRATION.md (boxes there check off as replacements go live)
- [x] 1.2 Land `core/capabilities.py`; CapabilityRegistry in cosmo_demo; CAPABILITY_CHANGED bus event (emit via create_task); bootstrap_current_hardware(); 1 Hz sweep task
- [x] 1.3 Feed registry: esp32_bridge `mark_seen` per reading (simulate in mock mode); vision_loop marks VISION/FACE_ID/EMOTION_READ; ESP32 hb → LOCOMOTION; bridge disconnect → FAILED; BH1750 poll → AMBIENT_LIGHT
- [x] 1.4 `core/intents.py` Intent enum + `core/action_router.py` (sole actuator authority; D7 policies; drift guard 3 verified: APPROACH w/o LOCOMOTION → leaning eyes + chirp)
- [x] 1.5 Port behavior_engine routines + phrases into router (dance/happy/love/look_around + all 5 trigger phrase banks + sound gate + approach steering + obstacle gate + learning outcomes); cosmo_demo USER_INTENT handler + on_emotion physical reactions now emit Intents. Trigger *conditions* (morning/curiosity/memory/wind-down) re-home in mind rule engine (1.8b); idle variety re-homes in BT (1.9)
- [x] 1.6 Archive HSM + pet_brain + behavior_engine; strip imports (cosmo_demo, sensor_manager, behavior/engine refs)
- [x] 1.7 Rename `cognition/intent.py` Intent → VoiceCommand; wire to emit Intents (D2)
- [x] 1.8a Fix cost leak FIRST (OQ-7a): `generate_streaming` (cognition/llm.py) never calls `token_budget.record()` — main conversation path is uncounted. **Prerequisite for 1.8b and 1.11** — record before unifying, unify before persisting, or we persist a lie. Add prompt caching (`cache_control` on system prompt, OQ-7b) while in there.
- [x] 1.8b mind.py: rule engine + LLM path emit Intents only; route LLM through LLMInterface; two-tier speech routing (D4); delete LLMRouter
- [x] 1.9 BT nodes gate on `reg.has_all(...)`; unusable behaviors pruned, not errored
- [x] 1.10 event_bus dispatch via `asyncio.create_task`; in the same pass fix the subscription leak: `navigation._follow_loop` registers `@bus.on(PERSON_DETECTED)` per call, never unsubscribes (behavior/navigation.py:254, OQ-8)
- [x] 1.11 Unify TokenBudget (one class), persist to memory_meta SQLite (D5) — **after 1.8a**; update STATE.md OQ-5
- [x] 1.12 Episodic memory READ: recall step in conversation path
- [x] 1.13 Migration-completeness check (exit criterion above)
- [x] **STOP — report** (2026-06-11, gate approved; committed fe52382)

## PHASE 2 — Aliveness on current hardware

**Entry prerequisite:** perception/audio deep-dive review (vision pipeline, wake-word/STT/VAD internals) — only spot-checked during the 2026-06-10 stability review; do this before any Phase 2 work so eye-engine/idle-loop changes build on verified foundations.

- [x] 2.1 Eye engine: expression state machine driven by personality vector (extend existing EyeEngine; reg.simulate(EXPRESSION))
- [ ] 2.2 Idle personality loop: curiosity glances, boredom fidgets, settling, micro-reactions
- [ ] 2.3 Circadian energy modulation
- [ ] 2.4 Reactive expressions off existing events
- [ ] 2.5 Greet-by-name: SFace + persons table + episodic recall; Tanglish via Claude Haiku (per-path language decision here)
- [ ] **STOP — report**

## PHASE 3 — Movement, safely (firmware now, wiring later)

- [ ] 3.1 ESP32 firmware: local cliff reflex (Pin.irq, immediate stop, no Pi round-trip); irq for touch/vibration/PIR; bound _outq (deque ~100, drop-oldest, never drop heartbeats)
- [ ] 3.2 Pi-side: coalesce stale move commands; heartbeat watchdog (3s → bridge offline, eyes "body offline")
- [ ] 3.3 Behaviors gated on LOCOMOTION: APPROACH/FLEE/WANDER/FOLLOW/COME against reg.simulate
- [ ] **STOP — report**

## PHASE 4 — docs/HARDWARE_PLAN.md (doc only)

- [ ] Wiring order steps 1–7 with parts/pins/test command/capability flip; GPIO6/16 conflict, MAX17040 failure, KI-024, maintenance checklist, SD-card risk lines
- [ ] **STOP — report**

## PHASE 5 — WhatsApp control surface

- [ ] `!cosmo status|caps|sim|start|stop|say|mood|last|log` in banteragent (owner path; NO auth changes, NO restart of banteragent)
- [ ] **STOP — report**
