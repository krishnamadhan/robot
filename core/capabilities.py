"""
core/capabilities.py

Single source of truth for "what can Cosmo currently do".

A capability is an ability that depends on hardware + software being present
AND healthy. Behaviors declare which capabilities they require; the action
router maps intents to whichever outputs are actually usable right now.

This is what makes Cosmo's behavior degrade gracefully:
  - only camera/mic/speaker present -> it watches, listens, talks, emotes
  - motors attached            -> approach/flee/wander become physical
  - more sensors attached      -> richer reactions, no code rewrite

Design notes:
  - asyncio-friendly: no threads, no blocking. emit callback may be sync or a
    coroutine; the registry never awaits it itself (fire via create_task in the
    glue layer if you need async handlers).
  - dependency-aware: FACE_ID is unusable unless VISION is usable, etc.
  - staleness-aware: streaming sensors drop to DEGRADED then FAILED if their
    data stops arriving, so a dead wire doesn't silently look "ready".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Optional


# --------------------------------------------------------------------------- #
# States
# --------------------------------------------------------------------------- #
class CapState(Enum):
    ABSENT = "absent"        # hardware not present / not wired / flag off
    SIMULATED = "simulated"  # mock mode for dev (behaviors run, no real hw)
    READY = "ready"          # present + healthy + fresh
    DEGRADED = "degraded"    # present but unreliable (stale, low rate, flaky)
    FAILED = "failed"        # was usable, now erroring / gone silent


# A capability a behavior can rely on if it is in one of these states.
# SIMULATED counts as usable on purpose: that is how you develop behavior
# against mock hardware and have it "just work" when the real part arrives.
USABLE = {CapState.READY, CapState.SIMULATED, CapState.DEGRADED}


# --------------------------------------------------------------------------- #
# Capabilities
# --------------------------------------------------------------------------- #
class Capability(Enum):
    # --- perception (inputs) ---
    VISION = "vision"                  # camera + detector loaded
    FACE_ID = "face_id"                # recognise known people
    EMOTION_READ = "emotion_read"      # read facial emotion
    HEARING = "hearing"                # mic + STT
    AMBIENT_LIGHT = "ambient_light"    # BH1750
    PROXIMITY = "proximity"            # HC-SR04
    CLIFF_SENSE = "cliff_sense"        # TCRT5000 x2
    TOUCH = "touch"                    # TTP223 x4
    MOTION_SENSE = "motion_sense"      # PIR
    ORIENTATION = "orientation"        # MPU-6050 (tilt / pickup)
    SOUND_SENSE = "sound_sense"        # KY-038 (loudness, not words)
    VIBRATION_SENSE = "vibration_sense"  # SW-420

    # --- actuation (outputs) ---
    SPEECH = "speech"                  # TTS -> speaker
    EXPRESSION = "expression"          # OLED eyes
    LOCOMOTION = "locomotion"          # wheels
    HEAD_MOVEMENT = "head_movement"    # pan-tilt servos


# Composite capabilities: a capability is only usable if its deps are too.
_DEPENDS_ON: dict[Capability, tuple[Capability, ...]] = {
    Capability.FACE_ID: (Capability.VISION,),
    Capability.EMOTION_READ: (Capability.VISION,),
}

# Freshness windows (seconds) for streaming sensors only. If no healthy signal
# arrives within the window, the cap is downgraded. Actuators and the camera
# are health-checked by their owners, not by this timer, so they're omitted.
_FRESHNESS: dict[Capability, float] = {
    Capability.PROXIMITY: 2.0,
    Capability.CLIFF_SENSE: 2.0,
    Capability.MOTION_SENSE: 5.0,
    Capability.ORIENTATION: 2.0,
    Capability.SOUND_SENSE: 5.0,
    Capability.VIBRATION_SENSE: 5.0,
    Capability.AMBIENT_LIGHT: 90.0,   # polled slowly on purpose
    Capability.TOUCH: 30.0,           # change-only; long window
}
# After this multiple of the freshness window with no signal -> FAILED.
_FAIL_AFTER = 3.0


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
@dataclass
class _Entry:
    state: CapState = CapState.ABSENT
    detail: str = ""
    changed_at: float = field(default_factory=time.monotonic)
    last_seen: float = 0.0  # last healthy signal (monotonic)


# emit(cap, old_state, new_state, detail) -> None | coroutine
EmitFn = Callable[[Capability, CapState, CapState, str], object]


class CapabilityRegistry:
    def __init__(self, emit: Optional[EmitFn] = None) -> None:
        self._caps: dict[Capability, _Entry] = {c: _Entry() for c in Capability}
        self._emit = emit

    # -- mutation -------------------------------------------------------- #
    def set_state(self, cap: Capability, state: CapState, detail: str = "") -> None:
        """Set a capability's state. Cascades to dependents and emits on change."""
        e = self._caps[cap]
        if state in USABLE:
            e.last_seen = time.monotonic()
        if e.state == state:
            e.detail = detail or e.detail
            return
        old = e.state
        e.state, e.detail, e.changed_at = state, detail, time.monotonic()
        self._fire(cap, old, state, detail)
        # If a dependency dropped, re-evaluate dependents' effective usability.
        for dep_cap, deps in _DEPENDS_ON.items():
            if cap in deps:
                # Emit a synthetic change so listeners re-check has(dep_cap).
                self._fire(dep_cap, self._caps[dep_cap].state,
                           self._caps[dep_cap].state, f"dep {cap.value} -> {state.value}")

    def mark_seen(self, cap: Capability, detail: str = "") -> None:
        """A healthy data point arrived. Promotes ABSENT/FAILED/DEGRADED -> READY."""
        e = self._caps[cap]
        e.last_seen = time.monotonic()
        if e.state in (CapState.ABSENT, CapState.FAILED, CapState.DEGRADED):
            self.set_state(cap, CapState.READY, detail)

    def simulate(self, cap: Capability, detail: str = "mock") -> None:
        """Force a capability into SIMULATED so behaviors run without real hw."""
        self.set_state(cap, CapState.SIMULATED, detail)

    def sweep(self, now: Optional[float] = None) -> None:
        """Call on a timer (~1 Hz). Downgrades streaming sensors that went quiet."""
        now = now or time.monotonic()
        for cap, window in _FRESHNESS.items():
            e = self._caps[cap]
            if e.state not in (CapState.READY, CapState.DEGRADED):
                continue  # ABSENT/SIMULATED/FAILED are not driven by freshness
            age = now - e.last_seen
            if age > window * _FAIL_AFTER:
                self.set_state(cap, CapState.FAILED, f"no data {age:.0f}s")
            elif age > window and e.state == CapState.READY:
                self.set_state(cap, CapState.DEGRADED, f"stale {age:.0f}s")

    # -- queries --------------------------------------------------------- #
    def has(self, cap: Capability) -> bool:
        """Usable now, including all dependencies."""
        if self._caps[cap].state not in USABLE:
            return False
        return all(self.has(dep) for dep in _DEPENDS_ON.get(cap, ()))

    def state(self, cap: Capability) -> CapState:
        return self._caps[cap].state

    def has_all(self, caps: Iterable[Capability]) -> bool:
        return all(self.has(c) for c in caps)

    def has_any(self, caps: Iterable[Capability]) -> bool:
        return any(self.has(c) for c in caps)

    def snapshot(self) -> dict[str, str]:
        """Human-readable view, e.g. for !pi cosmo or a status line."""
        return {c.value: e.state.value for c, e in self._caps.items()}

    # -- internal -------------------------------------------------------- #
    def _fire(self, cap, old, new, detail) -> None:
        if self._emit:
            self._emit(cap, old, new, detail)


# --------------------------------------------------------------------------- #
# Baseline bootstrap
# --------------------------------------------------------------------------- #
def bootstrap_current_hardware(reg: CapabilityRegistry) -> None:
    """
    Declare what is physically true TODAY: camera + mic + speaker only.
    Everything else is ABSENT until its wire goes in and its owner calls
    mark_seen()/set_state(READY). Flip these as you wire, nothing else changes.
    """
    reg.set_state(Capability.VISION, CapState.READY, "C920")
    reg.set_state(Capability.FACE_ID, CapState.READY, "SFace")
    reg.set_state(Capability.EMOTION_READ, CapState.READY, "DeepFace")
    reg.set_state(Capability.HEARING, CapState.READY, "faster-whisper")
    reg.set_state(Capability.SPEECH, CapState.READY, "Piper")
    reg.set_state(Capability.AMBIENT_LIGHT, CapState.READY, "BH1750 on Pi")
    # Not yet wired -> stay ABSENT (default). Use reg.simulate(...) to dev them:
    #   reg.simulate(Capability.LOCOMOTION)   # build follow/wander against mock
    #   reg.simulate(Capability.EXPRESSION)   # build eye engine before OLEDs


# --------------------------------------------------------------------------- #
# Singleton wired to the event bus
# --------------------------------------------------------------------------- #
def _emit_to_bus(cap: Capability, old: CapState, new: CapState, detail: str) -> None:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # before loop start (e.g. bootstrap at import time) — no listeners yet
    from core.event_bus import Event, EventType, bus

    asyncio.create_task(bus.publish(Event(
        type=EventType.CAPABILITY_CHANGED,
        data={"capability": cap.value, "old": old.value,
              "new": new.value, "detail": detail},
        source="capability_registry",
    )))


registry = CapabilityRegistry(emit=_emit_to_bus)


async def sweep_loop(reg: CapabilityRegistry, interval_s: float = 1.0) -> None:
    """Run as a task from the main app: downgrades quiet streaming sensors."""
    import asyncio

    while True:
        reg.sweep()
        await asyncio.sleep(interval_s)


if __name__ == "__main__":
    # tiny self-test / demo
    def _log(cap, old, new, detail):
        print(f"[cap] {cap.value}: {old.value} -> {new.value} ({detail})")

    reg = CapabilityRegistry(emit=_log)
    bootstrap_current_hardware(reg)
    print("can greet by name?", reg.has(Capability.FACE_ID))   # True
    print("can move?", reg.has(Capability.LOCOMOTION))         # False
    reg.simulate(Capability.LOCOMOTION)
    print("can move (simulated)?", reg.has(Capability.LOCOMOTION))  # True
    # camera dies -> face_id should follow it down
    reg.set_state(Capability.VISION, CapState.FAILED, "camera unplugged")
    print("face_id after camera loss?", reg.has(Capability.FACE_ID))  # False