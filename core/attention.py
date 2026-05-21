"""
Attention system — Cosmo's conscious spotlight.

Every incoming stimulus competes to become the current attention target.
Whether it wins depends on its salience vs the current target's salience,
modulated by interruptibility (how easily the current focus can be stolen).

AttentionManager routes events through attention before behavior sees them,
so the robot reacts coherently to what it's actually focused on rather than
firing handlers in arbitrary arrival order.

Publishes:
  ATTENTION_SHIFTED — new target acquired (with full AttentionState in data)
  ATTENTION_LOST    — attention faded below threshold, no current target
"""

import asyncio
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from core.event_bus import bus, Event, EventType
from utils.logger import get_logger

log = get_logger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────

# Minimum confidence to hold attention — below this, target is dropped
_CONFIDENCE_FLOOR = 0.08

# Decay tick interval (seconds)
_DECAY_TICK_S = 1.0

# How much stronger a new stimulus must be to steal attention:
#   required_salience = current_salience * (1 + interruptibility_resistance)
#   interruptibility=1.0 (idle)       → resistance=0.0 → equal salience wins
#   interruptibility=0.5 (engaged)    → resistance=0.5 → need 50% more salience
#   interruptibility=0.0 (locked)     → resistance=1.5 → need 2.5× salience
def _shift_threshold(current_salience: float, interruptibility: float) -> float:
    resistance = (1.0 - interruptibility) * 1.5
    return current_salience * (1.0 + resistance)


# ── Salience table — base importance of each stimulus type ───────────────────

_SALIENCE: dict[str, float] = {
    # Social stimuli (highest — Cosmo is a social creature)
    "wake_word":       1.00,  # direct address — always wins
    "touch":           0.90,
    "touch_long":      0.95,
    "face_known":      0.80,
    "face_unknown":    0.65,
    "conversation":    0.85,

    # Emotional signals
    "emotion_happy":   0.70,
    "emotion_sad":     0.75,
    "emotion_angry":   0.80,
    "emotion_neutral": 0.40,

    # Perceptual
    "person":          0.60,
    "gesture":         0.72,
    "sound":           0.45,
    "motion":          0.40,

    # Safety (handled separately by safety layer, but include for completeness)
    "obstacle":        0.95,
    "cliff":           1.00,
}

# Default interruptibility per modality when attention is first acquired
_DEFAULT_INTERRUPTIBILITY: dict[str, float] = {
    "wake_word":    0.05,   # almost locked — always finish responding
    "touch":        0.20,   # engaged but can be overridden by safety
    "conversation": 0.10,   # deep engagement
    "face_known":   0.50,   # watching a known person — can be redirected
    "face_unknown": 0.65,
    "person":       0.70,
    "gesture":      0.55,
    "sound":        0.80,
    "motion":       0.85,
    "idle":         1.00,   # freely stolen
}


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class AttentionState:
    target:            Optional[str]  = None   # person_id, stimulus key, or None
    target_name:       Optional[str]  = None   # human-readable label
    modality:          str            = "idle" # "face_known" / "touch" / "sound" etc.
    reason:            str            = ""     # one-line human-readable reason
    confidence:        float          = 0.0    # 0.0–1.0, decays over time
    salience:          float          = 0.0    # importance at acquisition
    decay_rate:        float          = 0.05   # confidence lost per second
    interruptibility:  float          = 1.0    # 0=locked, 1=freely stolen
    emotional_weight:  float          = 1.0    # multiplier on personality impacts
    acquired_at:       float          = field(default_factory=time.monotonic)

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.acquired_at

    @property
    def focused(self) -> bool:
        return self.confidence >= _CONFIDENCE_FLOOR

    def decay(self) -> None:
        self.confidence = max(0.0, self.confidence - self.decay_rate)


# ── Manager ───────────────────────────────────────────────────────────────────

class AttentionManager:
    """
    Central nervous system for Cosmo's selective awareness.

    Usage:
        from core.attention import attention
        if attention.state.focused:
            target_name = attention.state.target_name
    """

    def __init__(self) -> None:
        self._state    = AttentionState()
        self._lock     = asyncio.Lock()
        self._running  = False
        self._task: Optional[asyncio.Task] = None

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def state(self) -> AttentionState:
        return self._state

    @property
    def focused(self) -> bool:
        return self._state.focused

    @property
    def target(self) -> Optional[str]:
        return self._state.target if self._state.focused else None

    @property
    def target_name(self) -> Optional[str]:
        return self._state.target_name if self._state.focused else None

    def lock(self) -> None:
        """Call when entering deep conversation — resist all interruptions."""
        self._state.interruptibility = 0.05

    def unlock(self) -> None:
        """Call when conversation ends — restore normal interruptibility."""
        self._state.interruptibility = _DEFAULT_INTERRUPTIBILITY.get(
            self._state.modality, 0.7
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._subscribe()
        self._task = asyncio.create_task(self._decay_loop(), name="attention.decay")
        log.info("attention.started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        log.info("attention.stopped")

    # ── Competition logic ─────────────────────────────────────────────────────

    async def compete(
        self,
        target:    Optional[str],
        modality:  str,
        reason:    str,
        *,
        target_name:      Optional[str] = None,
        salience_override: Optional[float] = None,
        confidence:        float = 0.9,
        decay_rate:        Optional[float] = None,
        emotional_weight:  float = 1.0,
    ) -> bool:
        """
        Attempt to capture attention.
        Returns True if this stimulus won and attention shifted.
        """
        salience = salience_override if salience_override is not None else \
                   _SALIENCE.get(modality, 0.5)

        async with self._lock:
            current = self._state

            # Always yield to wake word and safety
            is_priority = modality in ("wake_word", "cliff", "obstacle")

            if current.focused and not is_priority:
                threshold = _shift_threshold(current.salience, current.interruptibility)
                if salience < threshold:
                    log.debug(
                        "attention.rejected",
                        challenger=modality,
                        challenger_salience=round(salience, 2),
                        threshold=round(threshold, 2),
                        current_target=current.target_name or current.modality,
                    )
                    return False

            prev_target = current.target_name or current.modality
            shifted = current.target != target or current.modality != modality

            self._state = AttentionState(
                target           = target,
                target_name      = target_name,
                modality         = modality,
                reason           = reason,
                confidence       = confidence,
                salience         = salience,
                decay_rate       = decay_rate if decay_rate is not None else
                                   self._default_decay(modality),
                interruptibility = _DEFAULT_INTERRUPTIBILITY.get(modality, 0.7),
                emotional_weight = emotional_weight,
                acquired_at      = time.monotonic(),
            )

            if shifted:
                log.info(
                    "attention.shifted",
                    from_target=prev_target,
                    to_target=target_name or modality,
                    modality=modality,
                    salience=round(salience, 2),
                    reason=reason,
                )
                await bus.publish(Event(
                    type   = EventType.ATTENTION_SHIFTED,
                    source = "attention",
                    data   = {
                        **asdict(self._state),
                        "previous_target": prev_target,
                    },
                ))

        return True

    # ── Decay loop ────────────────────────────────────────────────────────────

    async def _decay_loop(self) -> None:
        while self._running:
            await asyncio.sleep(_DECAY_TICK_S)
            async with self._lock:
                if not self._state.focused:
                    continue
                self._state.decay()
                if not self._state.focused:
                    lost_target = self._state.target_name or self._state.modality
                    self._state = AttentionState()  # reset to unfocused idle
                    log.info("attention.lost", previous_target=lost_target)
                    await bus.publish(Event(
                        type   = EventType.ATTENTION_LOST,
                        source = "attention",
                        data   = {"previous_target": lost_target},
                    ))

    # ── Event subscriptions ───────────────────────────────────────────────────

    def _subscribe(self) -> None:

        @bus.on(EventType.WAKE_WORD)
        async def on_wake_word(event: Event) -> None:
            await self.compete(
                target      = "voice",
                modality    = "wake_word",
                reason      = "direct address",
                target_name = "voice",
                confidence  = 1.0,
                decay_rate  = 0.02,  # decays slowly — hold through conversation
            )

        @bus.on(EventType.FACE_RECOGNIZED)
        async def on_face_known(event: Event) -> None:
            person_id   = event.data.get("person_id")
            person_name = event.data.get("name", "someone")
            await self.compete(
                target      = person_id,
                modality    = "face_known",
                reason      = f"recognised {person_name}",
                target_name = person_name,
                confidence  = 0.95,
                decay_rate  = 0.03,
                emotional_weight = 1.3,  # known face has stronger emotional impact
            )

        @bus.on(EventType.FACE_UNKNOWN)
        async def on_face_unknown(event: Event) -> None:
            await self.compete(
                target      = "unknown_face",
                modality    = "face_unknown",
                reason      = "unrecognised face",
                target_name = "stranger",
                confidence  = 0.80,
                decay_rate  = 0.05,
            )

        @bus.on(EventType.PERSON_DETECTED)
        async def on_person(event: Event) -> None:
            # Lower salience than face_known — detection without recognition
            # Only competes if there's no stronger current focus
            person_id = event.data.get("person_id", "person")
            await self.compete(
                target      = person_id,
                modality    = "person",
                reason      = "person in frame",
                target_name = event.data.get("name") or "someone",
                confidence  = 0.70,
                decay_rate  = 0.04,
            )

        @bus.on(EventType.PERSON_LOST)
        async def on_person_lost(event: Event) -> None:
            # Accelerate decay if the person we were watching just left
            async with self._lock:
                lost_id = event.data.get("person_id")
                if self._state.target == lost_id or \
                   self._state.modality in ("face_known", "face_unknown", "person"):
                    self._state.decay_rate = 0.20  # rapid fade
                    log.debug("attention.person_left", accelerating_decay=True)

        @bus.on(EventType.TOUCH_DETECTED)
        async def on_touch(event: Event) -> None:
            await self.compete(
                target      = "touch",
                modality    = "touch",
                reason      = f"touched at {event.data.get('location', 'unknown')}",
                target_name = "touch",
                confidence  = 0.90,
                decay_rate  = 0.08,  # touch is brief, fades quickly
            )

        @bus.on(EventType.TOUCH_LONG)
        async def on_touch_long(event: Event) -> None:
            await self.compete(
                target      = "touch",
                modality    = "touch",
                reason      = "sustained touch",
                target_name = "touch",
                confidence  = 0.95,
                decay_rate  = 0.05,
                emotional_weight = 1.5,
            )

        @bus.on(EventType.GESTURE_DETECTED)
        async def on_gesture(event: Event) -> None:
            gesture = event.data.get("gesture", "unknown")
            await self.compete(
                target      = "gesture",
                modality    = "gesture",
                reason      = f"gesture: {gesture}",
                target_name = gesture,
                confidence  = 0.85,
                decay_rate  = 0.10,
            )

        @bus.on(EventType.EMOTION_DETECTED)
        async def on_emotion(event: Event) -> None:
            emotion  = event.data.get("emotion", "neutral")
            modality = f"emotion_{emotion}"
            # Emotional signals don't steal attention on their own —
            # they only win if salience is competitive and we're idle.
            # They do boost emotional_weight on existing attention.
            async with self._lock:
                if self._state.focused and \
                   self._state.target == event.data.get("person_id"):
                    # Same person — boost emotional weight, don't shift
                    weight_delta = 0.2 if emotion in ("sad", "angry", "fear") else 0.1
                    self._state.emotional_weight = min(
                        2.0, self._state.emotional_weight + weight_delta
                    )
                    return
            # Different or no person — compete normally
            await self.compete(
                target      = event.data.get("person_id"),
                modality    = modality,
                reason      = f"detected emotion: {emotion}",
                target_name = event.data.get("name") or "someone",
                confidence  = 0.75,
                decay_rate  = 0.06,
            )

        @bus.on(EventType.SOUND_DETECTED)
        async def on_sound(event: Event) -> None:
            # Sound only captures idle attention or very weak focus
            await self.compete(
                target      = "sound",
                modality    = "sound",
                reason      = "sound detected",
                target_name = "sound",
                confidence  = 0.55,
                decay_rate  = 0.15,  # sound is transient
            )

        @bus.on(EventType.CONVERSATION_START)
        async def on_conv_start(event: Event) -> None:
            self.lock()

        @bus.on(EventType.CONVERSATION_END)
        async def on_conv_end(event: Event) -> None:
            self.unlock()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _default_decay(modality: str) -> float:
        defaults = {
            "wake_word":    0.02,
            "touch":        0.08,
            "touch_long":   0.05,
            "conversation": 0.02,
            "face_known":   0.03,
            "face_unknown": 0.05,
            "person":       0.04,
            "gesture":      0.10,
            "sound":        0.15,
            "motion":       0.12,
            "idle":         0.00,
        }
        return defaults.get(modality, 0.06)

    def describe(self) -> str:
        """One-line description for logging and Claude context."""
        s = self._state
        if not s.focused:
            return "attention: idle (no focus)"
        age = f"{s.age_s:.0f}s ago"
        return (
            f"attention: {s.target_name or s.modality} "
            f"via {s.modality} (conf={s.confidence:.2f}, "
            f"salience={s.salience:.2f}, acquired {age})"
        )


# ── Module singleton ──────────────────────────────────────────────────────────

attention = AttentionManager()
