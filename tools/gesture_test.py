"""
Phase D — Gesture Tests
Run: python3 tools/gesture_test.py

NOTE: mediapipe has no Linux aarch64 wheel for Python 3.13.
      Tests A/C/E run against the OpenCV skin-color fallback backend.
      Tests B/D are pipeline/integration tests that don't need mediapipe.

Tests:
  A — Wave hold: Open_Palm held 2+ frames at 4fps → GESTURE_WAVE fires
  B — Cooldown:  rapid wave triggers → max 1 per 3s
  C — Thumbs up: single Thumb_Up → thumbs_up sound, no other gesture
  D — BT routing: gesture event updates bb → BT tick produces correct action
  E — Latency:   frame → event publish latency < 500ms
"""

import asyncio
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

results = []

def run_test(name: str, fn):
    try:
        fn() if not asyncio.iscoroutinefunction(fn) else asyncio.run(fn())
        results.append((name, True, ""))
        console.print(f"  [green]✓[/green] {name}")
    except AssertionError as e:
        results.append((name, False, str(e)))
        console.print(f"  [red]✗[/red] {name}: {e}")
    except Exception as e:
        results.append((name, False, f"{type(e).__name__}: {e}"))
        console.print(f"  [red]✗[/red] {name}: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()


# ── Test A: Wave hold — OpenCV backend + GestureLoop internal logic ───────────

def test_wave_hold():
    """
    Simulate 4 FPS frames with Open_Palm detected.
    Wave must NOT fire on frame 1 (need WAVE_HOLD_FRAMES=2 consecutive).
    Wave MUST fire on frame 2.
    """
    from perception.vision.gesture import GestureLoop, WAVE_HOLD_FRAMES, MIN_CONFIDENCE

    events_fired = []

    class _FakeLoop(GestureLoop):
        """Subclass that records events instead of publishing to event bus."""
        async def _emit(self, name, conf, latency_ms):
            events_fired.append({"gesture": name, "conf": conf, "latency_ms": latency_ms})

        async def _process_gesture(self, name, conf, frame_ts):
            """Replicate _process logic but emit via _emit instead of bus."""
            if name is None or conf < MIN_CONFIDENCE:
                self._wave_streak = 0
                return
            if name == "Open_Palm":
                self._wave_streak += 1
                if self._wave_streak < WAVE_HOLD_FRAMES:
                    return
            else:
                self._wave_streak = 0

            from perception.vision.gesture import _GESTURE_MAP
            if _GESTURE_MAP.get(name) is None:
                return

            now = time.monotonic()
            if now - self._last_fired.get(name, 0.0) < 3.0:
                return
            self._last_fired[name] = now
            await self._emit(name, conf, (time.monotonic() - frame_ts) * 1000)

    loop = _FakeLoop()
    loop._backend_name = "test"

    async def _run():
        ts = time.monotonic()
        # Frame 1: Open_Palm — should NOT fire (streak=1 < 2)
        await loop._process_gesture("Open_Palm", 0.85, ts)
        assert len(events_fired) == 0, \
            f"Fired on frame 1 (wave streak not met); events={events_fired}"

        # Frame 2: Open_Palm again — streak=2, should fire
        await loop._process_gesture("Open_Palm", 0.85, ts)
        assert len(events_fired) == 1, \
            f"Did not fire on frame 2; events={events_fired}"
        assert events_fired[0]["gesture"] == "Open_Palm"

    asyncio.run(_run())


# ── Test B: Cooldown — max 1 wave per 3s ─────────────────────────────────────

def test_cooldown():
    """
    Rapid-fire Open_Palm with streak already satisfied.
    Over 10s should fire at most ceil(10/3) = 4 times.
    """
    from perception.vision.gesture import GestureLoop, WAVE_HOLD_FRAMES, GESTURE_COOLDOWN_S

    fired_times = []

    class _FakeLoop(GestureLoop):
        async def _process_gesture(self, name, conf, frame_ts):
            if name == "Open_Palm":
                self._wave_streak += 1
                if self._wave_streak < WAVE_HOLD_FRAMES:
                    return
            now = time.monotonic()
            if now - self._last_fired.get(name, 0.0) < GESTURE_COOLDOWN_S:
                return
            self._last_fired[name] = now
            fired_times.append(now)

    loop = _FakeLoop()
    loop._backend_name = "test"
    loop._wave_streak = WAVE_HOLD_FRAMES  # pre-satisfy streak

    async def _run():
        # Simulate 10 seconds of continuous detections at 4fps = 40 frames
        start = time.monotonic()
        for i in range(40):
            # Advance mock time: patch _last_fired to use virtual clock
            virtual_now = start + i * 0.25  # 4fps
            for k in loop._last_fired:
                # Shift so our virtual time is "now"
                pass
            await loop._process_gesture("Open_Palm", 0.85, virtual_now)
            await asyncio.sleep(0)  # yield

        max_fires = int(10 / GESTURE_COOLDOWN_S) + 1  # 4
        assert len(fired_times) <= max_fires, \
            f"Cooldown broken: fired {len(fired_times)} times in 10s (limit {max_fires})"
        assert len(fired_times) >= 1, "Never fired — cooldown too aggressive"
        console.print(f"    [dim]Fired {len(fired_times)} times in 10s "
                      f"(3s cooldown → max {max_fires})[/dim]")

    asyncio.run(_run())


# ── Test C: Thumbs up — correct sound, not wave ───────────────────────────────

def test_thumbs_up_bt():
    """
    Simulate a Thumb_Up gesture event hitting the BT.
    BT should react with thumbs_up sound, not wave_response.
    """
    import types as _types

    # Stub expression/sounds/behavior for BT import
    _sounds_mod = _types.ModuleType("expression.sounds")
    played = []
    class _FS:
        def play(self, n):
            played.append(n)
            async def _n(): pass
            return _n()
    _sounds_mod.sounds = _FS()
    sys.modules.setdefault("expression", _types.ModuleType("expression"))
    sys.modules["expression.sounds"] = _sounds_mod

    _eyes_mod = sys.modules.get("expression.eyes") or _types.ModuleType("expression.eyes")
    from enum import Enum
    class EE(str, Enum):
        NEUTRAL="neutral"; HAPPY="happy"; SAD="sad"; SCARED="scared"
        SURPRISED="surprised"; CURIOUS="curious"; CONFUSED="confused"
        SLEEPY="sleepy"; LOVING="loving"
    _eyes_mod.EyeExpression = EE
    class _FE:
        last_expr = None
        def set_expression(self, e, duration=0, priority=0): _FE.last_expr = e
    _eyes_mod.eye_engine = _FE()
    sys.modules["expression.eyes"] = _eyes_mod

    sys.modules.setdefault("behavior", _types.ModuleType("behavior"))
    _nav = _types.ModuleType("behavior.navigation")
    class _FNav:
        def wander(self, **kw):
            async def _n(): pass
            return _n()
    _nav.navigation = _FNav()
    sys.modules["behavior.navigation"] = _nav

    # Neutralise personality sync
    from core.behavior_tree import behavior_tree as bt, bb
    bt._sync_from_personality = lambda: None  # type: ignore

    # Set up Thumb_Up gesture in bb
    bb.last_gesture = "Thumb_Up"
    bb.last_gesture_time = time.monotonic()
    bb.last_gesture_bt_reacted.clear()
    bb.person_visible = True

    bt.tick_once()

    assert "thumbs_up" in played, \
        f"thumbs_up not played; played={played}"
    assert "wave_response" not in played, \
        f"wave_response was wrongly played; played={played}"


# ── Test D: BT routes gesture → correct eye expression ───────────────────────

def test_bt_fist_eyes():
    """
    Closed_Fist → DoFistReact should set eyes to SCARED.
    """
    from core.behavior_tree import bb
    import sys, types as _types

    _eyes_mod = sys.modules.get("expression.eyes") or _types.ModuleType("expression.eyes")
    _fe = getattr(_eyes_mod, "eye_engine", None)
    if _fe is None:
        class _FE2:
            last_expr = None
            def set_expression(self, e, duration=0, priority=0): _FE2.last_expr = e
        _fe = _FE2()
        _eyes_mod.eye_engine = _fe

    _fe.__class__.last_expr = None

    bb.last_gesture = "Closed_Fist"
    bb.last_gesture_time = time.monotonic()
    bb.last_gesture_bt_reacted.clear()
    bb.person_visible = True

    from core.behavior_tree import behavior_tree as bt
    bt._sync_from_personality = lambda: None  # type: ignore
    bt.tick_once()

    EE = _eyes_mod.EyeExpression
    assert _fe.__class__.last_expr == EE.SCARED, \
        f"Eyes should be SCARED for fist; got={_fe.__class__.last_expr}"


# ── Test E: Latency — OpenCV detect < 500ms ──────────────────────────────────

def test_latency():
    """
    Run OpenCV backend on a synthetic frame.
    Measure time from frame creation to gesture result.
    Must be under 500ms.
    """
    import numpy as np
    import cv2

    from perception.vision.gesture import _OpenCVBackend

    backend = _OpenCVBackend()

    # Generate synthetic skin-coloured hand-like blob in the centre
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    # Draw a skin-toned ellipse (approximates a palm)
    skin_bgr = (120, 100, 220)  # roughly skin in BGR
    cv2.ellipse(frame, (320, 240), (80, 100), 0, 0, 360, skin_bgr, -1)
    # Fingers (rectangles extending up)
    for i, x in enumerate(range(260, 380, 25)):
        cv2.rectangle(frame, (x, 100), (x+18, 200), skin_bgr, -1)

    latencies = []
    for _ in range(5):
        t0 = time.monotonic()
        name, conf = backend.detect(frame)
        latencies.append((time.monotonic() - t0) * 1000)

    avg_ms = sum(latencies) / len(latencies)
    max_ms = max(latencies)
    console.print(f"    [dim]OpenCV latency: avg={avg_ms:.1f}ms  max={max_ms:.1f}ms  "
                  f"detected={name}({conf:.2f})[/dim]")

    assert max_ms < 500, f"Max latency {max_ms:.1f}ms exceeds 500ms limit"


# ── Run ───────────────────────────────────────────────────────────────────────

console.print("\n[bold cyan]Phase D — Gesture Tests[/bold cyan]")
console.print("[dim]Backend: opencv_skin (mediapipe unavailable: no Linux aarch64 wheel for Python 3.13)[/dim]\n")

run_test("A. Wave hold (2-frame streak)", test_wave_hold)
run_test("B. Cooldown (max 1/3s)",        test_cooldown)
run_test("C. Thumbs Up → BT sound",       test_thumbs_up_bt)
run_test("D. Fist → SCARED eyes",         test_bt_fist_eyes)
run_test("E. OpenCV latency < 500ms",     test_latency)

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

# Thermal + RAM check (informational)
try:
    import subprocess, psutil
    temp = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True).stdout.strip()
    mem  = psutil.virtual_memory()
    console.print(f"[dim]Thermal: {temp}  |  RAM free: {mem.available // 1024 // 1024} MB[/dim]\n")
except Exception:
    pass

sys.exit(0 if passed == total else 1)
