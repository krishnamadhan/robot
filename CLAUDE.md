# Robot Project — Claude Code Context

## What This Project Is
An embodied AI companion/pet running locally on this Pi.
Design inspiration: EMO by LivingAI, Vector, Loona — fully local, self-hostable.
Goal is PRESENCE, not intelligence. The robot feels alive through timing, reactions,
memory continuity, emotional state, and attention — not constant talking.

**Cosmo is now a reactive companion. No LLM in real-time path.**
All reactions are: sensor/vision event → behavior tree → sound + eyes.
Do not add any Claude API or Ollama calls to event handlers.
Do not add TTS. Sound responses only.

## Session Start Checklist — DO THIS FIRST, EVERY SESSION

1. `cd /home/pi/claude-memory && git pull && cat STATUS.md && cat ACTIVE_TASK.md`
2. `cat /home/pi/robot/ARCHITECTURE.md` (if it exists)
3. `ls /home/pi/robot/services/`
4. `systemctl --user list-units 'robot-*'`
5. Read the subsystem README for whatever you're about to work on

Do NOT start coding until steps 1–3 are done.

## Project Structure

```
/home/pi/robot/
├── CLAUDE.md
├── ARCHITECTURE.md        ← always-current subsystem map, keep updated
├── main.py                ← entry point, starts event bus + core services
├── venv/                  ← always activate before running anything
├── services/
│   ├── event_bus/         ← core async pub/sub (ALWAYS ON)
│   ├── perception/        ← camera + OpenCV + MediaPipe (event-triggered)
│   ├── emotion_engine/    ← persistent emotional state (ALWAYS ON)
│   ├── attention/         ← dynamic focus system (ALWAYS ON)
│   ├── memory/            ← short/long/episodic memory layers (ALWAYS ON)
│   ├── behavior/          ← behavior engine, utility AI + FSM (ALWAYS ON)
│   ├── voice/             ← STT/TTS/wake-word (FUTURE — stub only)
│   ├── hardware/          ← motor/servo/OLED abstraction (FUTURE — mock layer)
│   └── api/               ← HTTP + WebSocket for debug dashboards
├── config/
│   ├── personality.yaml   ← all personality params, never hardcode these
│   ├── services.yaml      ← which services enabled + resource limits
│   └── hardware.yaml      ← hardware present/absent flags
├── data/
│   ├── robot.db           ← aiosqlite (episodic + long-term memory)
│   └── emotional_state.json ← persisted across reboots
├── tests/
└── docs/
    ├── subsystems/        ← one .md per subsystem
    └── decisions/         ← ADRs
```

Every `services/<name>/` folder MUST contain:
- `__init__.py`
- `service.py` — must expose `async def start()` and `async def stop()`
- `README.md` — purpose, events emitted/consumed, known issues

## Event Bus Contract
Everything communicates through the event bus. No direct service-to-service calls.
Event shape: `{"type": "PERSON_DETECTED", "payload": {...}, "ts": float}`

## Emotional State
7 dimensions: happiness, curiosity, loneliness, excitement, stress, fatigue, attachment
All floats 0.0–1.0. Persisted to `data/emotional_state.json` across reboots.
Updated by events. Decay over time. Never reset to defaults on reboot.

## Hardware Abstraction Rule
All hardware access goes through `services/hardware/`.
When hardware absent, mock implementations auto-used (via `config/hardware.yaml` flags).
Current flags: camera=true, everything else=false.

## Active Phase — PHASE 1: Embodied Presence Without Movement

Hardware available now: Pi 5 + camera + UPS only.

Tasks:
- [ ] Event bus (asyncio pub/sub core)
- [ ] Camera perception pipeline (OpenCV + MediaPipe face detection)
- [ ] Emotional state engine (persistent, decays, event-driven updates)
- [ ] Attention system (who/what robot is focused on)
- [ ] Memory logging (episodic, aiosqlite)
- [ ] Idle behavior engine (what robot does when nothing is happening)
- [ ] Debug API (HTTP endpoint to inspect internal state)

Phase 2 (FUTURE): Voice
Phase 3 (FUTURE): OLED display/eyes
Phase 4 (FUTURE): Mobility

## Locked-In Decisions (do not re-debate without strong reason)

| Decision | Reason |
|---|---|
| asyncio over threading | entire stack is async |
| aiosqlite over PostgreSQL | local-only, no network dependency |
| MediaPipe over heavy CV models | runs on Pi CPU without GPU |
| Utility AI for behavior engine | more extensible than pure FSM |
| structlog for all logging | JSON, easy to grep |
| No local LLM models | Anthropic API only, on-demand, never resident in RAM |
| personality.yaml for all personality params | zero hardcoding |

## Memory Repo Rule
At the end of every session, before exiting:
```bash
cd /home/pi/claude-memory
git add -A
git commit -m "session YYYY-MM-DD: <one line summary>"
git push
```
Always update STATUS.md and ACTIVE_TASK.md before committing.
