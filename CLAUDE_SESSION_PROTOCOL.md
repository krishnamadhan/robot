# Cosmo — Claude Code Session Protocol

> Purpose: make every new Claude Code session self-orienting, and stop documentation
> from drifting. Referenced from the top of `CLAUDE.md`.

---

## Rule 0 — One source of truth

`docs/PROJECT_STATE.md` is the ONLY authoritative description of current reality.

- README.md = public-facing overview (may lag; never trust it for "what works now").
- CONTEXT_Part*.md = historical/aspirational; archive them, do not treat as current.
- ARCHITECTURE.md / ROADMAP.md = stable design intent, updated rarely.

If any doc disagrees with `PROJECT_STATE.md`, `PROJECT_STATE.md` wins, and fixing the
disagreement is part of the task that surfaced it.

**Verified environment (last checked 2026-05-29):**
- OS: Debian GNU/Linux 13 (trixie)
- Python: 3.13.5
- Vision model on disk: `models/yolo11n.pt` — person.py docstring still says YOLOv8n (stale, fix when touching that file)

---

## Session START ritual (run every time, before coding)

```bash
cat docs/PROJECT_STATE.md
cat COSMO_BACKLOG.md
git log --oneline -10
git status
pm2 status && pm2 logs cosmo --lines 30 --nostream
```

State out loud which backlog task you are taking and why, then proceed.

---

## Session END ritual (run every time, before stopping)

1. Update `docs/PROJECT_STATE.md`:
   - what changed this session
   - what is now working / newly broken
   - "Next up" pointer for the following session
   - any new gotcha or hardware-state change (e.g. "wired PIR to GPIO8")
2. Tick/update checkboxes in `COSMO_BACKLOG.md`; add any new tasks discovered.
3. Append a one-line entry to `docs/CHANGELOG.md` (date + summary).
4. Commit with a conventional message: `feat(memory): wire episodic recall into prompt`.
5. If hardware physically changed, say so explicitly in the commit body.

---

## Guardrails — design rules that must never be violated

- Non-verbal reaction (chirp/purr/eyes) ALWAYS fires before any LLM speech.
- No model >500MB resident in RAM; always keep >1GB headroom free on the Pi.
- asyncio only — never introduce threading.
- No global `pip install`; system Python with `PYTHONPATH=/home/pi/robot`.
- Never stream video frames to any external API.
- Claude is for personality SPEECH only — never for movement/expression decisions.
- Respect the daily token budget; if a change could increase token use, say so and
  estimate the cost before merging.
