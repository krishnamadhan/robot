"""
Hierarchical state machine for Cosmo's behavioral states.

Design: python-statemachine would be cleaner but adds a dependency.
This implementation encodes the hierarchy explicitly — child states
inherit parent transitions, and history states remember last substate.

State transitions emit STATE_CHANGED events to the bus so all subsystems
can react to behavioral context shifts.
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple

from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)


# ── State definitions ────────────────────────────────────────────────────────

class RobotState(str, Enum):
    # Top-level states
    SAFE_MODE = "safe_mode"
    SLEEPING = "sleeping"

    # IDLE branch
    IDLE = "idle"
    IDLE_CURIOUS = "idle.curious"
    IDLE_CALM = "idle.calm"
    IDLE_BORED = "idle.bored"

    # ALERT branch
    ALERT = "alert"
    ALERT_PERSON = "alert.person"
    ALERT_SOUND = "alert.sound"
    ALERT_MOTION = "alert.motion"

    # INTERACTIVE branch
    INTERACTIVE = "interactive"
    LISTENING = "interactive.listening"
    PROCESSING = "interactive.processing"
    RESPONDING = "interactive.responding"
    PLAYING = "interactive.playing"

    # NAVIGATING branch
    NAVIGATING = "navigating"
    WANDERING = "navigating.wandering"
    APPROACHING = "navigating.approaching"
    RETREATING = "navigating.retreating"
    AVOIDING = "navigating.avoiding"

    # EXPRESSING branch
    EXPRESSING = "expressing"
    EXPRESSING_HAPPY = "expressing.happy"
    EXPRESSING_EXCITED = "expressing.excited"
    EXPRESSING_CONFUSED = "expressing.confused"
    EXPRESSING_SAD = "expressing.sad"
    EXPRESSING_SCARED = "expressing.scared"
    EXPRESSING_PLAYFUL = "expressing.playful"


# Parent relationships — child inherits parent's allowed transitions
_PARENTS: Dict[RobotState, RobotState] = {
    RobotState.IDLE_CURIOUS: RobotState.IDLE,
    RobotState.IDLE_CALM: RobotState.IDLE,
    RobotState.IDLE_BORED: RobotState.IDLE,
    RobotState.ALERT_PERSON: RobotState.ALERT,
    RobotState.ALERT_SOUND: RobotState.ALERT,
    RobotState.ALERT_MOTION: RobotState.ALERT,
    RobotState.LISTENING: RobotState.INTERACTIVE,
    RobotState.PROCESSING: RobotState.INTERACTIVE,
    RobotState.RESPONDING: RobotState.INTERACTIVE,
    RobotState.PLAYING: RobotState.INTERACTIVE,
    RobotState.WANDERING: RobotState.NAVIGATING,
    RobotState.APPROACHING: RobotState.NAVIGATING,
    RobotState.RETREATING: RobotState.NAVIGATING,
    RobotState.AVOIDING: RobotState.NAVIGATING,
    RobotState.EXPRESSING_HAPPY: RobotState.EXPRESSING,
    RobotState.EXPRESSING_EXCITED: RobotState.EXPRESSING,
    RobotState.EXPRESSING_CONFUSED: RobotState.EXPRESSING,
    RobotState.EXPRESSING_SAD: RobotState.EXPRESSING,
    RobotState.EXPRESSING_SCARED: RobotState.EXPRESSING,
    RobotState.EXPRESSING_PLAYFUL: RobotState.EXPRESSING,
}

Guard = Callable[[], bool]
Action = Callable[[], Coroutine[Any, Any, None]]


@dataclass
class Transition:
    target: RobotState
    guard: Optional[Guard] = None       # returns True if transition is allowed
    action: Optional[Action] = None     # runs on transition
    timeout_s: Optional[float] = None   # auto-transition if in state this long


@dataclass
class StateConfig:
    state: RobotState
    entry: Optional[Action] = None      # called when entering state
    exit: Optional[Action] = None       # called when leaving state
    transitions: List[Transition] = field(default_factory=list)
    timeout_transition: Optional[Transition] = None
    # Which child state to restore when re-entering a parent (history state)
    default_child: Optional[RobotState] = None


@dataclass
class StateRecord:
    """Audit log entry for one state."""
    state: RobotState
    entered_at: float
    exited_at: Optional[float] = None
    trigger: str = "unknown"

    def duration_s(self) -> float:
        end = self.exited_at or time.monotonic()
        return end - self.entered_at


class StateMachine:
    """
    Hierarchical state machine for Cosmo.

    Usage:
        sm = StateMachine()
        sm.add_state(StateConfig(RobotState.IDLE, ...))
        await sm.start(RobotState.IDLE_CALM)
        await sm.transition_to(RobotState.ALERT_PERSON, trigger="person_detected")
    """

    MAX_HISTORY = 200

    def __init__(self) -> None:
        self._states: Dict[RobotState, StateConfig] = {}
        self._current: Optional[RobotState] = None
        self._history: List[StateRecord] = []
        self._history_states: Dict[RobotState, RobotState] = {}  # parent → last child
        self._current_record: Optional[StateRecord] = None
        self._timeout_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._listeners: List[Callable] = []

        self._build_default_states()

    # ── State registration ───────────────────────────────────────────────────

    def add_state(self, config: StateConfig) -> None:
        self._states[config.state] = config

    def add_transition(self, from_state: RobotState, transition: Transition) -> None:
        if from_state not in self._states:
            self._states[from_state] = StateConfig(state=from_state)
        self._states[from_state].transitions.append(transition)

    def on_transition(self, callback: Callable) -> None:
        """Register callback called after every state transition."""
        self._listeners.append(callback)

    # ── Runtime API ──────────────────────────────────────────────────────────

    @property
    def current(self) -> Optional[RobotState]:
        return self._current

    @property
    def parent(self) -> Optional[RobotState]:
        return _PARENTS.get(self._current) if self._current else None

    def in_state(self, *states: RobotState) -> bool:
        """True if current state IS any of states, or a child of any."""
        if self._current in states:
            return True
        p = self.parent
        return p in states if p else False

    def time_in_state_s(self) -> float:
        if self._current_record:
            return self._current_record.duration_s()
        return 0.0

    async def start(self, initial: RobotState = RobotState.IDLE_CALM) -> None:
        async with self._lock:
            self._current = initial
            self._current_record = StateRecord(state=initial, entered_at=time.monotonic())
            cfg_entry = self._states.get(initial)
            if cfg_entry and cfg_entry.entry:
                try:
                    await cfg_entry.entry()
                except Exception as e:
                    log.error("state_machine.entry_error", state=initial, error=str(e))
            self._schedule_timeout(initial)
            log.info("state_machine.started", state=initial)

    async def transition_to(
        self,
        target: RobotState,
        trigger: str = "manual",
        force: bool = False,
    ) -> bool:
        """
        Transition to target state.
        Returns True if transition happened, False if blocked by guard.
        force=True skips guard checks (used by safety system).
        """
        if target == self._current:
            return True

        async with self._lock:
            if not force:
                if not self._is_transition_allowed(target):
                    log.debug("state_machine.transition_blocked",
                               from_state=self._current, to=target, trigger=trigger)
                    return False

            await self._do_transition(target, trigger)
            return True

    # ── Internal ─────────────────────────────────────────────────────────────

    def _is_transition_allowed(self, target: RobotState) -> bool:
        current_cfg = self._states.get(self._current)
        if not current_cfg:
            return True   # unknown states allow all transitions

        allowed_targets = {t.target for t in current_cfg.transitions}

        # Check parent's transitions too (hierarchy)
        parent = _PARENTS.get(self._current)
        if parent and parent in self._states:
            parent_cfg = self._states[parent]
            allowed_targets |= {t.target for t in parent_cfg.transitions}

        # SAFE_MODE transitions are always allowed (safety override)
        if target == RobotState.SAFE_MODE:
            return True

        # If no transitions defined, allow anything (permissive default)
        if not allowed_targets:
            return True

        return target in allowed_targets

    async def _do_transition(self, target: RobotState, trigger: str) -> None:
        old_state = self._current

        # Cancel timeout task for old state
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None

        # Exit old state
        if old_state and old_state in self._states:
            old_cfg = self._states[old_state]
            if old_cfg.exit:
                try:
                    await old_cfg.exit()
                except Exception as e:
                    log.error("state_machine.exit_error", state=old_state, error=str(e))

        # Record history: remember child states per parent
        if old_state:
            parent = _PARENTS.get(old_state)
            if parent:
                self._history_states[parent] = old_state

            if self._current_record:
                self._current_record.exited_at = time.monotonic()
                self._history.append(self._current_record)
                if len(self._history) > self.MAX_HISTORY:
                    self._history.pop(0)

        # Enter new state (or restore history child if entering parent)
        resolved = self._resolve_state(target)
        self._current = resolved
        self._current_record = StateRecord(state=resolved, entered_at=time.monotonic(), trigger=trigger)

        if resolved in self._states:
            entry_cfg = self._states[resolved]
            if entry_cfg.entry:
                try:
                    await entry_cfg.entry()
                except Exception as e:
                    log.error("state_machine.entry_error", state=resolved, error=str(e))

        self._schedule_timeout(resolved)

        telemetry.increment("state_machine.transitions")
        log.info("state_machine.transition",
                  from_state=old_state, to=resolved, trigger=trigger)

        for cb in self._listeners:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(old_state, resolved, trigger)
                else:
                    cb(old_state, resolved, trigger)
            except Exception as e:
                log.error("state_machine.listener_error", error=str(e))

    def _resolve_state(self, target: RobotState) -> RobotState:
        """If entering a parent state, restore history child if available."""
        cfg = self._states.get(target)
        if cfg and cfg.default_child:
            return self._history_states.get(target, cfg.default_child)
        return target

    def _schedule_timeout(self, state: RobotState) -> None:
        cfg = self._states.get(state)
        if not cfg or not cfg.timeout_transition:
            return
        timeout_s = cfg.timeout_transition.timeout_s
        if not timeout_s:
            return

        async def _timeout_coro() -> None:
            await asyncio.sleep(timeout_s)
            t = cfg.timeout_transition
            if t.guard and not t.guard():
                return
            log.debug("state_machine.timeout", state=state, timeout_s=timeout_s)
            await self.transition_to(t.target, trigger=f"timeout:{state}")

        self._timeout_task = asyncio.create_task(_timeout_coro(), name=f"sm_timeout_{state}")

    def recent_history(self, n: int = 10) -> List[StateRecord]:
        return list(reversed(self._history[-n:]))

    def stats(self) -> Dict[str, Any]:
        return {
            "current": self._current,
            "parent": self.parent,
            "time_in_state_s": round(self.time_in_state_s(), 1),
            "total_transitions": len(self._history),
        }

    def _build_default_states(self) -> None:
        """Register all states with sensible defaults."""
        # SAFE_MODE — motors off, minimal processing
        self.add_state(StateConfig(
            state=RobotState.SAFE_MODE,
            transitions=[
                Transition(target=RobotState.IDLE, guard=lambda: True),
            ],
        ))

        # SLEEPING — low power
        self.add_state(StateConfig(
            state=RobotState.SLEEPING,
            transitions=[
                Transition(target=RobotState.IDLE_CALM),
                Transition(target=RobotState.ALERT_PERSON),
                Transition(target=RobotState.SAFE_MODE),
            ],
        ))

        # IDLE — awake, no interaction
        self.add_state(StateConfig(
            state=RobotState.IDLE,
            default_child=RobotState.IDLE_CALM,
            transitions=[
                Transition(target=RobotState.ALERT),
                Transition(target=RobotState.ALERT_PERSON),
                Transition(target=RobotState.ALERT_SOUND),
                Transition(target=RobotState.ALERT_MOTION),
                Transition(target=RobotState.INTERACTIVE),
                Transition(target=RobotState.LISTENING),
                Transition(target=RobotState.NAVIGATING),
                Transition(target=RobotState.WANDERING),
                Transition(target=RobotState.SLEEPING),
                Transition(target=RobotState.EXPRESSING),
                Transition(target=RobotState.SAFE_MODE),
            ],
        ))
        for child in [RobotState.IDLE_CURIOUS, RobotState.IDLE_CALM, RobotState.IDLE_BORED]:
            self.add_state(StateConfig(
                state=child,
                timeout_transition=Transition(
                    target=RobotState.IDLE_CALM, timeout_s=120.0
                ) if child != RobotState.IDLE_CALM else None,
            ))

        # ALERT
        self.add_state(StateConfig(
            state=RobotState.ALERT,
            default_child=RobotState.ALERT_PERSON,
            transitions=[
                Transition(target=RobotState.INTERACTIVE),
                Transition(target=RobotState.LISTENING),
                Transition(target=RobotState.IDLE),
                Transition(target=RobotState.NAVIGATING),
                Transition(target=RobotState.SAFE_MODE),
            ],
        ))
        for child, timeout in [
            (RobotState.ALERT_PERSON, 30.0),
            (RobotState.ALERT_SOUND, 10.0),
            (RobotState.ALERT_MOTION, 15.0),
        ]:
            self.add_state(StateConfig(
                state=child,
                timeout_transition=Transition(target=RobotState.IDLE_CURIOUS, timeout_s=timeout),
            ))

        # INTERACTIVE
        self.add_state(StateConfig(
            state=RobotState.INTERACTIVE,
            default_child=RobotState.LISTENING,
            transitions=[
                Transition(target=RobotState.IDLE),
                Transition(target=RobotState.ALERT),
                Transition(target=RobotState.EXPRESSING),
                Transition(target=RobotState.SAFE_MODE),
            ],
        ))
        self.add_state(StateConfig(state=RobotState.LISTENING,
            timeout_transition=Transition(target=RobotState.IDLE_CALM, timeout_s=15.0)))
        self.add_state(StateConfig(state=RobotState.PROCESSING,
            timeout_transition=Transition(target=RobotState.IDLE_CALM, timeout_s=20.0)))
        self.add_state(StateConfig(state=RobotState.RESPONDING,
            timeout_transition=Transition(target=RobotState.LISTENING, timeout_s=30.0)))
        self.add_state(StateConfig(state=RobotState.PLAYING))

        # NAVIGATING
        self.add_state(StateConfig(
            state=RobotState.NAVIGATING,
            default_child=RobotState.WANDERING,
            transitions=[
                Transition(target=RobotState.IDLE),
                Transition(target=RobotState.ALERT),
                Transition(target=RobotState.SAFE_MODE),
            ],
        ))
        for child in [RobotState.WANDERING, RobotState.APPROACHING,
                      RobotState.RETREATING, RobotState.AVOIDING]:
            self.add_state(StateConfig(state=child))

        # EXPRESSING
        self.add_state(StateConfig(
            state=RobotState.EXPRESSING,
            default_child=RobotState.EXPRESSING_HAPPY,
            transitions=[
                Transition(target=RobotState.IDLE),
                Transition(target=RobotState.INTERACTIVE),
                Transition(target=RobotState.SAFE_MODE),
            ],
        ))
        for child, timeout in [
            (RobotState.EXPRESSING_HAPPY, 5.0),
            (RobotState.EXPRESSING_EXCITED, 8.0),
            (RobotState.EXPRESSING_CONFUSED, 6.0),
            (RobotState.EXPRESSING_SAD, 10.0),
            (RobotState.EXPRESSING_SCARED, 4.0),
            (RobotState.EXPRESSING_PLAYFUL, 10.0),
        ]:
            self.add_state(StateConfig(
                state=child,
                timeout_transition=Transition(target=RobotState.IDLE_CALM, timeout_s=timeout),
            ))


# Module-level singleton
sm = StateMachine()
