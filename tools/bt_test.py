"""
Phase C — Behavior Tree Tests
Run: python3 tools/bt_test.py

Tests (5 required):
  1. Greet      — person appears → DoGreet fires, last_greeted updated
  2. Emotion    — happy face → DoEmotionReact fires, state recorded
  3. Boredom    — alone 200s + energy 0.6 → DoWanderExplore fires
  4. No-spam    — same person re-appears < 5min → DoGreet does NOT fire
  5. RAM        — 30 ticks stay < 50 MB above baseline
"""

import os
import sys
import time
import types
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=RuntimeWarning, message="coroutine.*never awaited")

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

# ── Step 1: stub leaf deps BEFORE importing behavior_tree ─────────────────────
# These are lazy imports inside node .update() — must be in sys.modules first.

# expression.eyes
_eyes_mod = types.ModuleType("expression.eyes")
from enum import Enum
class EyeExpression(str, Enum):
    NEUTRAL="neutral"; HAPPY="happy"; SAD="sad"; SCARED="scared"
    SURPRISED="surprised"; CURIOUS="curious"; CONFUSED="confused"
    SLEEPY="sleepy"; LOVING="loving"
_eyes_mod.EyeExpression = EyeExpression

class _FakeEyeEngine:
    last_expr = None
    def set_expression(self, expr, duration=0.0, priority=0):
        _FakeEyeEngine.last_expr = expr
    @property
    def current_expression(self):
        return EyeExpression.NEUTRAL
_fake_eye_engine = _FakeEyeEngine()
_eyes_mod.eye_engine = _fake_eye_engine
_eyes_mod.PRIORITY_TOUCH = 10
if "expression" not in sys.modules:
    sys.modules["expression"] = types.ModuleType("expression")
sys.modules["expression.eyes"] = _eyes_mod

# expression.sounds
_sounds_mod = types.ModuleType("expression.sounds")
class _FakeSounds:
    last_played: list = []
    def play(self, name: str):
        # Record synchronously at call-time (before _fire awaits)
        _FakeSounds.last_played.append(name)
        async def _noop(): pass
        return _noop()
    async def start(self): pass
_fake_sounds = _FakeSounds()
_sounds_mod.sounds = _fake_sounds
sys.modules["expression.sounds"] = _sounds_mod

# behavior.navigation
_nav_mod = types.ModuleType("behavior.navigation")
class _FakeNav:
    last_wander = False
    def wander(self, duration=20):
        _FakeNav.last_wander = True
        async def _noop(): pass
        return _noop()
_fake_nav = _FakeNav()
_nav_mod.navigation = _fake_nav
if "behavior" not in sys.modules:
    sys.modules["behavior"] = types.ModuleType("behavior")
sys.modules["behavior.navigation"] = _nav_mod

# ── Step 2: import behavior_tree (uses real core package + utils) ─────────────

from core.behavior_tree import (
    behavior_tree, bb,
    DoGreet, DoWanderExplore,
    GREET_COOLDOWN_S,
)

# ── Step 3: neutralise personality sync (avoids DB / full robot env) ──────────
behavior_tree._sync_from_personality = lambda: None  # type: ignore

# ── Test helpers ──────────────────────────────────────────────────────────────

def reset_bb():
    bb.person_visible = False
    bb.person_name    = ""
    bb.person_id      = ""
    bb.emotion        = ""
    bb.mood           = 0.5
    bb.energy         = 0.7
    bb.sleeping       = False
    bb.alone_since    = time.monotonic()
    bb.distance_cm    = 100.0
    bb.last_greeted.clear()
    bb.last_emotion_reacted     = ""
    bb.last_emotion_react_time  = 0.0
    bb.last_bored_sound_time    = 0.0
    _FakeSounds.last_played.clear()
    _FakeEyeEngine.last_expr = None
    _FakeNav.last_wander = False
    DoWanderExplore._last_wander = 0.0  # reset wander cooldown


results = []

def run_test(name: str, fn):
    try:
        fn()
        results.append((name, True, ""))
        console.print(f"  [green]✓[/green] {name}")
    except AssertionError as e:
        results.append((name, False, str(e)))
        console.print(f"  [red]✗[/red] {name}: {e}")
    except Exception as e:
        results.append((name, False, f"{type(e).__name__}: {e}"))
        console.print(f"  [red]✗[/red] {name}: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_greet():
    """Person appears → DoGreet fires chime_greeting and updates last_greeted."""
    reset_bb()
    bb.person_visible = True
    bb.person_name = "Madhan"

    behavior_tree.tick_once()

    assert "Madhan" in bb.last_greeted, "last_greeted not set for Madhan"
    assert time.monotonic() - bb.last_greeted["Madhan"] < 2.0, "timestamp looks stale"
    assert "chime_greeting" in _FakeSounds.last_played, \
        f"chime_greeting not played; played={_FakeSounds.last_played}"
    assert _FakeEyeEngine.last_expr == EyeExpression.HAPPY, \
        f"eyes should be HAPPY; got={_FakeEyeEngine.last_expr}"


def test_emotion_react():
    """Happy face → DoEmotionReact fires trill_excited and records emotion."""
    reset_bb()
    bb.person_visible = True
    bb.person_name = "Indhu"
    bb.last_greeted["Indhu"] = time.monotonic()  # skip greet
    bb.emotion = "happy"

    behavior_tree.tick_once()

    assert bb.last_emotion_reacted == "happy", \
        f"last_emotion_reacted={bb.last_emotion_reacted!r}, expected 'happy'"
    assert "trill_excited" in _FakeSounds.last_played, \
        f"trill_excited not played; played={_FakeSounds.last_played}"
    assert _FakeEyeEngine.last_expr == EyeExpression.HAPPY, \
        f"eyes should be HAPPY; got={_FakeEyeEngine.last_expr}"


def test_boredom_wander():
    """Alone 200s, energy 0.6 → BORED_HIGH fires DoWanderExplore."""
    reset_bb()
    bb.person_visible = False
    bb.alone_since    = time.monotonic() - 200.0
    bb.energy         = 0.6

    behavior_tree.tick_once()

    assert _FakeNav.last_wander, "navigation.wander was not called"
    assert "curious_pip" in _FakeSounds.last_played, \
        f"curious_pip not played; played={_FakeSounds.last_played}"


def test_no_spam_greet():
    """Re-appearing within 5 min must NOT trigger a second DoGreet."""
    reset_bb()
    bb.person_visible = True
    bb.person_name    = "Madhan"

    # First tick → should greet
    behavior_tree.tick_once()
    first_ts = bb.last_greeted.get("Madhan", 0.0)
    assert first_ts > 0, "First greet didn't fire — test setup broken"

    _FakeSounds.last_played.clear()

    # Person leaves
    bb.person_visible = False
    bb.person_name    = ""
    bb.alone_since    = time.monotonic()
    behavior_tree.tick_once()

    # Person returns almost immediately (within 5-min cooldown)
    time.sleep(0.05)
    bb.person_visible = True
    bb.person_name    = "Madhan"
    behavior_tree.tick_once()

    assert bb.last_greeted.get("Madhan") == first_ts, \
        "DoGreet fired again within cooldown (timestamp changed)"
    assert "chime_greeting" not in _FakeSounds.last_played, \
        f"chime_greeting played again within cooldown: {_FakeSounds.last_played}"


def test_ram():
    """30 ticks must not grow RSS by more than 50 MB."""
    import psutil
    reset_bb()
    bb.person_visible = False

    proc = psutil.Process(os.getpid())
    baseline = proc.memory_info().rss

    for _ in range(30):
        behavior_tree.tick_once()

    delta_mb = (proc.memory_info().rss - baseline) / 1024 / 1024
    console.print(f"    [dim]RSS delta after 30 ticks: {delta_mb:+.2f} MB[/dim]")
    assert delta_mb < 50, f"RAM grew {delta_mb:.1f} MB (limit 50 MB)"


# ── Run ───────────────────────────────────────────────────────────────────────

console.print("\n[bold cyan]Phase C — Behavior Tree Tests[/bold cyan]\n")

behavior_tree.setup()   # build the tree once before tests

run_test("1. Greet",          test_greet)
run_test("2. Emotion React",  test_emotion_react)
run_test("3. Boredom Wander", test_boredom_wander)
run_test("4. No-spam Greet",  test_no_spam_greet)
run_test("5. RAM (30 ticks)", test_ram)

# ── Summary ───────────────────────────────────────────────────────────────────

passed = sum(1 for _, ok, _ in results if ok)
total  = len(results)
console.print()

table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
table.add_column("Test",   style="white")
table.add_column("Result", justify="center")
table.add_column("Detail", style="dim")
for name, ok, detail in results:
    table.add_row(name, "[green]PASS[/green]" if ok else "[red]FAIL[/red]", detail)

console.print(table)
console.print(
    f"\n[bold]{'[green]ALL PASS' if passed == total else '[red]FAILURES'}[/bold]"
    f"[bold] — {passed}/{total}[/bold]\n"
)
sys.exit(0 if passed == total else 1)
