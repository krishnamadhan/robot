# Brain Build State
_Updated: 2026-05-29_

Current phase: B6 — Hardening + Dashboard (COMPLETE)

Invariants: I1✅ I2✅ I3✅ I4✅ I5✅ I6✅ I7✅

Last replay result: 11/11 pass, all 7 invariants green
Last pytest result: 89/89 pass (tests/brain/)

## What was built this session

### B0 — Feedback harness
- `tests/brain/__init__.py` created
- `tests/brain/conftest.py` — FakeLLM, BudgetSpy, mock HAL (sensor, motors, tts, sounds, eye)
- `tests/brain/fixtures/` — 11 JSON scenario files covering all triggers + full_day soak
- `tools/brain_replay.py` — loads fixtures, drives real cognition code against mock HAL, asserts I1-I7, supports --all / --scenario / --live-smoke

### B1 — Tier-1 rule engine correctness
- `tests/brain/test_tier1.py` — 17 tests covering dark_room, obstacle, idle_wander, nonverbal ordering, I1/I2
- Verified: every rule fires with correct conditions, zero LLM calls, nonverbal before speech

### B2 — LLM routing + budget guard
- Added to `cognition/llm.py`:
  - `TokenBudget` class: daily limit, per-call tracking, claude_allowed(), reset
  - `OllamaProvider`: health_check(), generate() with timeout
  - `ClaudeProvider`: generate() with budget gate, token recording
  - `LLMRouter`: Ollama-first, Claude fallback, exception-safe, both-down → empty
  - Fixed `LLMInterface.generate()`: was Claude-first, now Ollama-first (I4)
- `tests/brain/test_llm_routing.py` — 21 tests covering all routing scenarios

### B3 — Personality prompt integration
- Created `cognition/personality_prompt.py` — PersonalityPromptBuilder
  - `build(state)`: compact deterministic preamble (~100 chars)
  - `build_full(state)`: numerical values + tone guidance
  - Tone shifts: happy→enthusiasm/chatty; sad→subdued/quiet; high arousal→excited; family attachment
- `tests/brain/test_personality_prompt.py` — 14 tests covering determinism, tone shifts, compactness

### B4 — Episodic memory recall
- Added to `core/memory/episodic.py`:
  - `recall_for_prompt(person_id, keywords, limit, max_chars)`: recency+importance weighted, keyword LIKE search, hard 800-char cap
  - `store_fact(person_id, fact, importance, valence)`: convenience write-back
- Updated `cognition/conversation.py` `_build_context()` to use `recall_for_prompt()`
- `tests/brain/test_memory_recall.py` — 12 tests covering I5 end-to-end

### B5 — Conversation loop
- `tests/brain/test_conversation_loop.py` — 8 tests: basic respond, I5 memory injection, budget gate, full_day soak, graceful degradation

### B6 — Hardening + dashboard
- `tests/brain/test_hardening.py` — 17 tests: DB failures, malformed LLM output, API timeout, budget exhaustion, personality prompt edge cases, dashboard smoke tests
- `services/api/service.py` — full dashboard replacement + new endpoints:
  - POST /motor/forward, /motor/back, /motor/left, /motor/right, /motor/stop
  - GET /budget — token budget status with used/limit/remaining
  - GET /logs/tail — last 20 log lines
  - POST /trigger/face_seen, /trigger/touched, /trigger/emotion_happy
  - GET /health — extended with total_ram_mb, disk_pct
- Dashboard features (540px mobile dark-theme):
  - Live MJPEG camera stream at :8080
  - Animated personality bars (mood/energy/arousal/attachment)
  - Attention section (person, emotion, distance, gesture)
  - System health (uptime, CPU temp, RAM bar, disk%, battery)
  - Token budget bar with colored indicator
  - Hardware component badges (real/mock/error)
  - Motor directional pad (▲▼◀▶ + stop)
  - Brain controls (Mind ON/OFF, mute, test triggers)
  - Recent episodic memories (last 10 with valence color)
  - Live log tail (last 20 lines with color coding)
  - Auto-refresh every 3 seconds with manual refresh button

## Next concrete step
- B6 DoD: run --live-smoke (use real API once to verify end-to-end)
- Then: integrate PersonalityPromptBuilder into mind._build_rich_system_prompt()
- Then: wire llm_router into conversation.py in place of llm singleton

## BLOCKED: none

## Token cost notes
- All tests use FakeLLM — zero real API calls
- PersonalityPromptBuilder preamble: ~100-150 chars (~25-40 tokens)
- recall_for_prompt cap: 800 chars (~200 tokens)
- Total overhead per Tier-2 call: ~225-240 tokens above base system prompt
