# REVIEW_AND_OPTIMIZE.md — Self-Driving Audit, Fix & LLM Bake-off

> Paste "Read prompts/REVIEW_AND_OPTIMIZE.md and begin." into a Claude Code session.
> You (Claude Code) work autonomously: **audit everything before touching anything**,
> rank by severity, fix highest-down, prove each fix with before/after evidence,
> then run the LLM bake-off. Stop cleanly with a summary when all phases are done.

---

## 0. MISSION

1. **Whole-repo bug and performance audit** → ranked register → fix Critical and High
   in severity order, with measured before/after evidence for every change.
2. **LLM bake-off**: run `tools/llm_bakeoff.py` across Ollama (1b, 3b-if-RAM-OK) and
   Claude Haiku on a fixed Tanglish personality prompt suite; produce a comparison table
   and raw outputs so the team can decide the runtime LLM stack with real data.

You succeed when:
- The ranked register is written (every file touched by any previous session reviewed).
- Every Critical and High item is fixed with a passing before/after test.
- The bake-off report is committed to `docs/LLM_BAKEOFF_REPORT.md`.
- `docs/AUDIT_STATE.md` reflects final state and any BLOCKED items.

---

## 1. GUARDRAILS — HARD, NEVER VIOLATE

1. **A0 is read-only.** Zero code changes until the full ranked register exists in
   `docs/AUDIT_STATE.md`. Violating this causes thrashing — fix it first.
2. **Fix in severity order.** Critical → High → Medium → Low. Never touch a Low while
   a Critical is open.
3. **No fix without before/after evidence.** Every fix must show:
   - The failing/slow behaviour *before* (test output, profiler number, or log line).
   - The same behaviour *after* (test pass, new profiler number, or absence of log line).
   If you cannot measure it, write `BLOCKED: unmeasurable` and move on.
4. **Perf changes need a number.** "Reduced latency" with no measurement is a regression
   risk disguised as an improvement. Measure with cProfile or `time.perf_counter()` before
   claiming any perf win.
5. **Tests must stay green throughout.** Run `python3 -m pytest tests/ -q` before and
   after every fix. A fix that breaks another test is not done.
6. **asyncio only, no threading, no global pip.** System Python, PYTHONPATH=/home/pi/robot.
7. **No real API calls except the bake-off phase.** Use FakeLLM (from tests/brain/) for
   all unit/replay tests. The bake-off uses real models — cap each model to 20 calls.
8. **Never stream video frames to any external API.**
9. **Commit after each severity tier.** Message format:
   `fix(audit): Critical — <short description>` / `fix(audit): High — ...` / etc.

---

## 2. AUDIT SCOPE

Read every file in these paths. Look for bugs, perf issues, and correctness problems.
Also check for drift between code and config.

```
cognition/          core/               hardware/
behavior/           perception/         expression/
services/           utils/              tools/cosmo_demo.py
config/hardware.yaml  config/models.yaml  config/personality.yaml
tests/              docs/PROJECT_STATE.md
```

**Known categories to look for** (not exhaustive — audit is open-ended):

| Category | What to look for |
|---|---|
| Hardware safety | GPIO double-claims, wrong pin directions, missing safety interlocks |
| Race conditions | asyncio tasks that can run concurrently when they should be serialized |
| Memory leaks | event handlers registered multiple times, DB connections never closed |
| Error swallowing | bare `except: pass` hiding real failures |
| Config drift | yaml keys referenced in code that don't exist; dead yaml keys |
| Perf hot paths | sync I/O on the event loop, polling loops that should be event-driven |
| Token waste | system prompts rebuilt every call (should be cached), over-large context |
| Test gaps | code paths with zero test coverage that could cause silent regressions |
| Dead code | imported but unused modules loaded at boot (wastes RAM + boot time) |

---

## 3. THE RANKED REGISTER FORMAT

Write to `docs/AUDIT_STATE.md`. Each entry:

```
| ID | Sev | File:line | Description | Root cause | Proposed fix | Evidence needed |
```

Severity levels:
- **Critical** — hardware damage, data corruption, silent crash, safety regression
- **High** — logic error that causes wrong behavior, resource leak, hard crash path
- **Medium** — suboptimal behavior, unhandled edge case, test gap on important path
- **Low** — style, dead code, minor inefficiency, doc drift

---

## 4. YOUR WORKING LOOP

```
Phase A0 (read-only):
  read all files in scope
  write docs/AUDIT_STATE.md with full ranked register
  do NOT change any code
  commit: "docs(audit): A0 ranked register — N Critical, N High, N Medium, N Low"

Phase A1 (Critical fixes):
  for each Critical item:
    run tests/ before the fix (record result)
    implement the smallest correct fix
    run tests/ after (confirm green + any new test you added)
    write before/after evidence in docs/AUDIT_STATE.md
    mark item FIXED
  commit: "fix(audit): Critical — <summary>"

Phase A2 (High fixes):  [same loop as A1]

Phase A3 (Performance):
  profile the hot paths identified in A0
    use: python3 -m cProfile -s cumtime tools/cosmo_demo.py [short run]
    OR:  tracemalloc snapshots in the relevant module
    OR:  time.perf_counter() micro-benchmarks for specific operations
  fix only if improvement is measurable (>10% on the metric)
  before: record the number; after: record the number
  commit: "perf(audit): <module> — Xms → Yms"

Phase A4 (LLM bake-off):
  ensure tools/llm_bakeoff.py is complete (see §6)
  check RAM budget before loading 3b model:
    run: free -m  (with cosmo + banteragent running if possible)
    3b model needs ~900MB free headroom beyond current usage
    if headroom < 900MB: skip 3b, note SKIPPED_LOW_RAM in report
  run: python3 tools/llm_bakeoff.py --suite tests/bakeoff/prompts/ --output docs/LLM_BAKEOFF_REPORT.md
  verify report was written and contains all models + all prompts
  commit: "docs(bakeoff): Ollama 1b vs 3b vs Claude Haiku — raw results"

Phase A5 (Medium + Low):
  fix Medium items worth doing (judgment call: skip if effort > value)
  for Low: only fix if trivially safe (rename, delete dead import, etc.)
  commit: "fix(audit): Medium/Low cleanup"

Phase A6 (Final):
  run full test suite: python3 -m pytest tests/ -q
  run full replay:     python3 tools/brain_replay.py --all
  update docs/AUDIT_STATE.md: mark all fixed/skipped, write final summary
  update docs/PROJECT_STATE.md: one-line summary of what changed
  update docs/CHANGELOG.md: one entry
  git push
  print DONE summary (counts, open BLOCKED items)
```

---

## 5. STATE FILE — `docs/AUDIT_STATE.md`

Update after every phase:

```markdown
# Audit State
_Updated: <ts>_
Phase: A<n> — <name>
Tests: <N passing> (last run: <ts>)
Register: <N Critical> / <N High> / <N Medium> / <N Low>  (<N fixed> / <N total>)

## Ranked Register
| ID   | Sev      | File:line | Description | Status |
|------|----------|-----------|-------------|--------|
| A001 | Critical | ...       | ...         | OPEN   |
...

## Fix Evidence
### A001 — <description>
Before: <test output / profiler number / log line>
Fix: <what changed>
After: <test output / profiler number>

## BLOCKED
- <ID>: <exact question or hardware requirement>

## Perf Baselines
- event_bus.publish: Xµs (measured <ts>)
- vision_loop frame: Xms
- episodic retrieve(limit=20): Xms
- LLM prompt build: Xms
```

---

## 6. LLM BAKE-OFF TOOL — `tools/llm_bakeoff.py`

The scaffold is already committed. Complete it if needed, then run it.

### What it does
- Loads every `*.json` prompt fixture from `--suite` directory
- Runs each prompt against each configured model
- Records: latency (ms), token count (in+out where available), raw output
- Scores each output on a 1–5 rubric (automated + raw text preserved for human review):
  - 1 = wrong language / irrelevant / empty
  - 2 = English only, no personality
  - 3 = correct language, robotic tone
  - 4 = natural, some Tanglish, feels like Cosmo
  - 5 = perfect Tanglish, personality clear, ≤12 words
- Writes `--output` markdown report with comparison table + all raw outputs

### Models to test (in order)
1. `ollama/llama3.2:1b` — current production model
2. `ollama/llama3.2:3b` — gated: only if free RAM ≥ 900MB while services running
3. `claude-haiku-4-5-20251001` — current Claude fallback

### Prompt fixture format (`tests/bakeoff/prompts/*.json`)
```json
{
  "id": "greeting_madhan",
  "scenario": "Cosmo sees Madhan after a long day",
  "system": "You are Cosmo, a small playful robot companion...",
  "user": "[You just spotted Madhan. Say a warm spontaneous greeting in 1 sentence, English or Tanglish.]",
  "expected_language": "tanglish_ok",
  "max_words": 12,
  "tags": ["greeting", "face_seen"]
}
```

### Fixtures to include (already in `tests/bakeoff/prompts/`)
1. `greeting_madhan.json` — face seen after long gap
2. `greeting_indhu.json` — face seen, Indhu looks tired
3. `emotion_happy.json` — person looks very happy
4. `emotion_sad.json` — person looks sad
5. `touch_surprised.json` — someone touched Cosmo suddenly
6. `alone_dramatic.json` — Cosmo has been alone 10 minutes
7. `obstacle_annoyed.json` — almost bumped into something
8. `dark_room_scared.json` — room suddenly went dark
9. `memory_reference.json` — person seen again, Cosmo recalls last conversation
10. `banter_indhu.json` — Indhu asks how Cosmo is feeling (Tanglish exchange)

---

## 7. FIRST ACTIONS (do these in order, do not skip)

1. Read: `CLAUDE_SESSION_PROTOCOL.md`
2. Read: `docs/PROJECT_STATE.md | head -60`
3. Check tests are currently green: `python3 -m pytest tests/ -q 2>&1 | tail -5`
4. Check replay is green: `python3 tools/brain_replay.py --all 2>&1 | tail -10`
5. Read every file in the audit scope (§2) — this is A0, read-only
6. Write `docs/AUDIT_STATE.md` with the full ranked register
7. Commit the register: `docs(audit): A0 ranked register`
8. Begin A1 (Critical fixes) — work down through A6

---

## 8. COST DISCIPLINE

- All test/replay runs use FakeLLM — zero real API calls.
- Bake-off: cap each model at 20 prompt calls maximum.
- Do not run `--live-smoke` on brain_replay during this session unless A6 explicitly requires it.
- If the bake-off reveals Claude costs would spike in production, note it in the report.

---

## 9. RESUMPTION

Read `docs/AUDIT_STATE.md` → find current phase + next item → re-run `pytest tests/ -q`
to confirm recorded state is still accurate → continue. If tests disagree with the state
file, trust the tests and correct the state file first.
