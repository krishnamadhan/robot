"""
Ambient activity inference — what is the household doing right now?

Cosmo should join in, not just react: when Madhan is watching TV, come sit
and watch too; when he's working quietly, settle nearby without interrupting.

Signals (all already available, no new hardware):
  - person presence (BT blackboard, fed by vision)
  - ambient mic loudness (rolling RMS from the audio pipeline)
  - direct interaction recency (touch / wake word / gestures / conversation)
  - light level + time of day

Activities:
  - watching_tv:   person around + sustained varied audio + no direct interaction
  - quiet_company: person around + low audio + no interaction (working/reading)
  - hangout:       recent direct interaction — normal social BT branches own this
  - none

Pure cognition: writes the BT blackboard + working memory and publishes
ACTIVITY_CHANGED. The behavior tree decides what to *do* about it (D6).
"""

import asyncio
import time
from typing import Optional

from core.event_bus import Event, EventPriority, EventType, bus
from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)

# Tunables (mic levels are normalized 0–1; see pipeline.ambient_stats)
TICK_S            = 5.0     # classify cadence
TV_LEVEL          = 0.06    # sustained avg above this looks like TV/music
QUIET_LEVEL       = 0.025   # below this is a quiet room
SUSTAIN_S         = 90.0    # audio must persist this long to call it TV
INTERACTION_GAP_S = 180.0   # no direct interaction for this long → ambient mode
PERSON_MEMORY_S   = 300.0   # person counts as "around" this long after last seen
SPIKE_RATIO       = 3.0     # 5s peak vs 60s avg → "tv moment" (explosion/goal)
SPIKE_MIN_LEVEL   = 0.12    # absolute floor so silence→whisper isn't a spike
BOND_PERIOD_S     = 60.0    # passive bonding cadence while co-present


class ActivityMonitor:
    """Infers ambient household activity and keeps the blackboard current."""

    def __init__(self) -> None:
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_interaction = 0.0
        self._person_last_seen = 0.0
        self._loud_since: Optional[float] = None
        self._pending: str = ""          # hysteresis: candidate must repeat
        self._activity: str = "none"
        self._activity_since = 0.0
        self._last_bond = 0.0
        self._unsubs = []

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        async def _interaction(event: Event) -> None:
            self._last_interaction = time.monotonic()

        bus.subscribe(_interaction, event_types={
            EventType.TOUCH_DETECTED,
            EventType.WAKE_WORD,
            EventType.CONVERSATION_START,
            EventType.GESTURE_DETECTED,
        })
        self._unsubs = [_interaction]
        self._task = asyncio.create_task(self._loop(), name="activity_monitor")
        log.info("activity.started")

    async def stop(self) -> None:
        self._running = False
        for h in self._unsubs:
            bus.unsubscribe(h)
        self._unsubs = []
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    @property
    def activity(self) -> str:
        return self._activity

    # ── Classification ────────────────────────────────────────────────────────

    def _gather(self) -> dict:
        """Collect signals. Isolated for testability."""
        from core.behavior_tree import bb
        now = time.monotonic()
        if bb.person_visible:
            self._person_last_seen = now

        avg60 = peak5 = 0.0
        try:
            from perception.audio.pipeline import audio_pipeline
            stats = audio_pipeline.ambient_stats(60.0)
            avg60 = stats["avg"]
            peak5 = audio_pipeline.ambient_stats(5.0)["peak"]
        except Exception:
            pass

        return {
            "now": now,
            "person_around": (now - self._person_last_seen) < PERSON_MEMORY_S,
            "interacting": (now - self._last_interaction) < INTERACTION_GAP_S,
            "avg60": avg60,
            "peak5": peak5,
        }

    def _classify(self, s: dict) -> str:
        if not s["person_around"]:
            return "none"
        if s["interacting"]:
            return "hangout"

        # Track how long the room has been TV-loud
        if s["avg60"] >= TV_LEVEL:
            if self._loud_since is None:
                self._loud_since = s["now"]
        else:
            self._loud_since = None

        if self._loud_since is not None and (s["now"] - self._loud_since) >= SUSTAIN_S:
            return "watching_tv"
        if s["avg60"] <= QUIET_LEVEL:
            return "quiet_company"
        return self._activity if self._activity in ("watching_tv", "quiet_company") else "none"

    def _step(self) -> None:
        """One classification step — sync so tests can drive it directly."""
        from core.behavior_tree import bb
        s = self._gather()
        candidate = self._classify(s)

        # Hysteresis: a new activity must win two consecutive steps
        if candidate != self._activity:
            if candidate == self._pending:
                self._set_activity(candidate, s["now"])
            else:
                self._pending = candidate
        else:
            self._pending = ""

        # TV-moment spike (explosion / goal / laugh track surge)
        if (self._activity == "watching_tv"
                and s["peak5"] >= SPIKE_MIN_LEVEL
                and s["avg60"] > 0
                and s["peak5"] / max(s["avg60"], 1e-6) >= SPIKE_RATIO):
            bb.tv_moment = s["now"]
            telemetry.increment("activity.tv_moment")

        # Passive bonding: just being together slowly builds attachment
        if (self._activity in ("watching_tv", "quiet_company")
                and s["now"] - self._last_bond >= BOND_PERIOD_S):
            self._last_bond = s["now"]
            try:
                from core.personality import personality
                personality.process_event("co_presence", person_id=bb.person_id or None)
            except Exception:
                pass

    def _set_activity(self, activity: str, now: float) -> None:
        from core.behavior_tree import bb
        prev = self._activity
        self._activity = activity
        self._activity_since = now
        self._pending = ""
        bb.activity = activity
        bb.activity_since = now
        bb.settled = False   # BT re-settles per session

        try:
            from core.memory.working import wm
            wm.set("ambient_activity", activity, ttl_s=600)
        except Exception:
            pass
        try:
            from core.personality import personality
            if activity in ("watching_tv", "quiet_company"):
                personality.process_event("co_settle")
        except Exception:
            pass

        try:
            asyncio.create_task(bus.publish(Event(
                type=EventType.ACTIVITY_CHANGED,
                data={"activity": activity, "previous": prev},
                priority=EventPriority.NORMAL,
            )))
        except RuntimeError:
            pass   # no running loop (sync unit tests drive _step directly)
        log.info("activity.changed", activity=activity, previous=prev)
        telemetry.increment(f"activity.{activity}")

    async def _loop(self) -> None:
        while self._running:
            try:
                self._step()
            except Exception as e:
                log.warning("activity.step_error", error=str(e)[:80])
            await asyncio.sleep(TICK_S)


activity_monitor = ActivityMonitor()
