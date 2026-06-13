"""
Cosmo Behavior Tree — Phase C.

Ticks every 100ms via asyncio task.
Reads from CosmoBlackboard (updated by event handlers), fires actions via
sounds/eye_engine/navigation.

Tree priority order (high → low):
  SAFETY → SLEEP_ACTIVE → WAKE_FROM_SLEEP → SOCIAL → AUTONOMOUS
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import py_trees
from py_trees.behaviour import Behaviour
from py_trees.common import Status

from utils.logger import get_logger

log = get_logger(__name__)

TICK_HZ             = 10       # 100ms per tick
GESTURE_COOLDOWN_S  = 3.0      # BT gesture reaction window
GREET_COOLDOWN_S    = 300.0    # 5 min no-spam
EMOTION_COOLDOWN_S  = 30.0     # same-emotion re-react cooldown
ALONE_HIGH_S        = 180.0    # 3 min → wander
ALONE_MED_S         = 60.0     # 1 min → idle sound
SLEEP_ENERGY_THRESH = 0.2      # energy below this → sleep
WANDER_COOLDOWN_S   = 45.0     # min gap between wander calls
IDLE_SOUND_COOLDOWN = 60.0     # min gap between bored idle sounds
BREATH_INTERVAL_S   = 8.0      # sleep breath sound interval
ENGAGE_INTERVAL_S   = 30.0     # presence engagement interval


# ── Shared blackboard ─────────────────────────────────────────────────────────

@dataclass
class CosmoBlackboard:
    """Shared state: written by event handlers, read by BT nodes."""
    person_visible: bool = False
    person_name:    str  = ""
    person_id:      str  = ""
    emotion:        str  = ""
    mood:           float = 0.5
    energy:         float = 0.7
    sleeping:       bool  = False
    alone_since:    float = field(default_factory=time.monotonic)
    distance_cm:    float = 100.0

    # Gesture blackboard (written by event handlers in cosmo_demo.py)
    last_gesture:           str   = ""
    last_gesture_time:      float = 0.0

    # Audio state (written by state_watcher in cosmo_demo.py)
    audio_speaking:         bool  = False  # True while TTS is outputting — gate ambient sounds

    # Ambient activity (written by cognition/activity.py ActivityMonitor)
    activity:               str   = "none"   # watching_tv | quiet_company | hangout | none
    activity_since:         float = 0.0
    settled:                bool  = False    # True once co-presence settle ran this session
    tv_moment:              float = 0.0      # monotonic ts of last loud TV spike

    # Sensor readings (updated by sensor_manager event handlers)
    light_lux:              float = -1.0   # BH1750; -1 = unavailable

    # Discovery (set by navigation wander loop when new obstacle confirmed)
    discovery_pending:      object = None  # behavior.exploration.DiscoveryEvent | None

    # Anti-spam state (written by action nodes)
    last_greeted:           Dict[str, float] = field(default_factory=dict)
    last_emotion_reacted:   str   = ""
    last_emotion_react_time: float = 0.0
    last_bored_sound_time:  float = 0.0
    last_gesture_bt_reacted: Dict[str, float] = field(default_factory=dict)


bb = CosmoBlackboard()


# ── Condition behaviours ──────────────────────────────────────────────────────

class IsSleeping(Behaviour):
    def update(self) -> Status:
        return Status.SUCCESS if bb.sleeping else Status.FAILURE


class PersonVisible(Behaviour):
    def update(self) -> Status:
        return Status.SUCCESS if bb.person_visible else Status.FAILURE


class NotGreetedRecently(Behaviour):
    """Succeeds if person hasn't been greeted in GREET_COOLDOWN_S seconds."""
    def update(self) -> Status:
        if not bb.person_name:
            return Status.FAILURE
        last = bb.last_greeted.get(bb.person_name, 0.0)
        return Status.SUCCESS if (time.monotonic() - last) > GREET_COOLDOWN_S else Status.FAILURE


class EmotionChanged(Behaviour):
    """Succeeds if emotion is non-neutral and changed (or cooldown elapsed)."""
    def update(self) -> Status:
        if not bb.emotion or bb.emotion.lower() in ("neutral", ""):
            return Status.FAILURE
        if bb.emotion != bb.last_emotion_reacted:
            return Status.SUCCESS
        if (time.monotonic() - bb.last_emotion_react_time) > EMOTION_COOLDOWN_S:
            return Status.SUCCESS
        return Status.FAILURE


class EnergyLow(Behaviour):
    def update(self) -> Status:
        return Status.SUCCESS if bb.energy < SLEEP_ENERGY_THRESH else Status.FAILURE


class EnergyHigh(Behaviour):
    def update(self) -> Status:
        return Status.SUCCESS if bb.energy >= 0.5 else Status.FAILURE


class AloneLong(Behaviour):
    def __init__(self, threshold_s: float, **kwargs):
        super().__init__(**kwargs)
        self._thresh = threshold_s

    def update(self) -> Status:
        if bb.person_visible:
            return Status.FAILURE
        return Status.SUCCESS if (time.monotonic() - bb.alone_since) > self._thresh else Status.FAILURE


class WasSleepingPersonArrived(Behaviour):
    """Succeeds when sleeping AND person just appeared."""
    def update(self) -> Status:
        return Status.SUCCESS if (bb.sleeping and bb.person_visible) else Status.FAILURE


class GestureDetected(Behaviour):
    """Succeeds if the named gesture fired recently (within GESTURE_COOLDOWN_S)."""
    def __init__(self, gesture_name: str, **kwargs):
        super().__init__(**kwargs)
        self._gesture = gesture_name

    def update(self) -> Status:
        if bb.last_gesture != self._gesture:
            return Status.FAILURE
        if (time.monotonic() - bb.last_gesture_time) > GESTURE_COOLDOWN_S:
            return Status.FAILURE
        # Not reacted yet?
        last_react = bb.last_gesture_bt_reacted.get(self._gesture, 0.0)
        if last_react >= bb.last_gesture_time:
            return Status.FAILURE  # already handled this gesture event
        return Status.SUCCESS


class CapUsable(Behaviour):
    """Prune a subtree when its required capabilities are not usable."""

    def __init__(self, *caps, name: str = "CapUsable"):
        super().__init__(name=name)
        self._caps = caps

    def update(self) -> Status:
        from core.capabilities import registry
        return Status.SUCCESS if registry.has_all(self._caps) else Status.FAILURE


class ActivityIs(Behaviour):
    """Succeeds when the ambient activity matches one of the given names."""

    def __init__(self, *activities: str, name: str = "ActivityIs"):
        super().__init__(name=name)
        self._activities = activities

    def update(self) -> Status:
        return Status.SUCCESS if bb.activity in self._activities else Status.FAILURE


class SafetyTriggered(Behaviour):
    """Placeholder — Phase D+ will wire cliff/obstacle/pickup here."""
    def update(self) -> Status:
        return Status.FAILURE


# ── Action behaviours (fire-and-forget asyncio tasks) ────────────────────────

class _AsyncAction(Behaviour):
    """Base for actions that schedule coroutines and return SUCCESS immediately."""

    def _fire(self, coro) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(coro)
        except RuntimeError:
            pass


class _GestureAction(_AsyncAction):
    """Base for all gesture reaction actions."""
    _gesture_name: str = ""
    _sound_name:   str = ""
    _eye_expr:     str = "CURIOUS"

    def update(self) -> Status:
        if bb.audio_speaking:
            return Status.FAILURE
        from expression.eyes import EyeExpression, eye_engine
        from expression.sounds import sounds

        bb.last_gesture_bt_reacted[self._gesture_name] = time.monotonic()
        from utils.action_log import action_log
        action_log.set_context("gesture", self._gesture_name)
        expr = getattr(EyeExpression, self._eye_expr, EyeExpression.CURIOUS)
        eye_engine.set_expression(expr, duration=3.0)
        self._fire(sounds.play(self._sound_name))
        log.info("bt.gesture_react", gesture=self._gesture_name, sound=self._sound_name)
        return Status.SUCCESS


class DoWaveResponse(_GestureAction):
    _gesture_name = "Open_Palm"
    _sound_name   = "wave_response"
    _eye_expr     = "HAPPY"


class DoThumbsUp(_GestureAction):
    _gesture_name = "Thumb_Up"
    _sound_name   = "thumbs_up"
    _eye_expr     = "HAPPY"


class DoPeace(_GestureAction):
    _gesture_name = "Victory"
    _sound_name   = "excited_trill"
    _eye_expr     = "HAPPY"


class DoFistReact(_GestureAction):
    _gesture_name = "Closed_Fist"
    _sound_name   = "angry_buzz"
    _eye_expr     = "SCARED"

    def update(self) -> Status:
        # angry_buzz is P1 — override sounds to use priority play
        from expression.eyes import EyeExpression, eye_engine
        from expression.sounds import sounds
        bb.last_gesture_bt_reacted[self._gesture_name] = time.monotonic()
        eye_engine.set_expression(EyeExpression.SCARED, duration=3.0)
        self._fire(sounds.play("angry_buzz"))
        log.info("bt.gesture_react", gesture="Closed_Fist", sound="angry_buzz")
        return Status.SUCCESS


class DoLoveReact(_GestureAction):
    _gesture_name = "ILoveYou"
    _sound_name   = "love_sound"
    _eye_expr     = "LOVING"


class DoPointReact(_GestureAction):
    _gesture_name = "Pointing_Up"
    _sound_name   = "curious_pip"
    _eye_expr     = "CURIOUS"


class DoGreet(_AsyncAction):
    def update(self) -> Status:
        from core.action_router import router
        from core.intents import Intent

        name = bb.person_name
        bb.last_greeted[name] = time.monotonic()

        from utils.action_log import action_log
        action_log.set_context("face_greet", name)
        router.emit(Intent.GREET, source="bt", name=name, person_id=bb.person_id)
        log.info("bt.greet", name=name)
        return Status.SUCCESS


class DoEmotionReact(_AsyncAction):
    def update(self) -> Status:
        from core.action_router import router
        from core.intents import Intent

        emotion = bb.emotion
        bb.last_emotion_reacted   = emotion
        bb.last_emotion_react_time = time.monotonic()

        intent, params = {
            "happy":     (Intent.EXPRESS_JOY,       {"speak": False}),
            "sad":       (Intent.COMFORT,           {"name": bb.person_name}),
            "angry":     (Intent.EXPRESS_FEAR,      {}),
            "surprised": (Intent.ALERT,             {}),
            "fearful":   (Intent.COMFORT,           {"name": bb.person_name}),
            "disgusted": (Intent.EXPRESS_CURIOSITY, {}),
        }.get(emotion.lower(), (Intent.EXPRESS_CURIOSITY, {}))
        router.emit(intent, source="bt_emotion", **params)
        log.info("bt.emotion_react", emotion=emotion, intent=intent.value)
        return Status.SUCCESS


class DoEngagePresence(_AsyncAction):
    """Periodic light engagement while person is present.

    Also starts brief follow-mode when activity is 'hangout' (direct interaction)
    — Cosmo follows the person around while they're actively engaging.
    """
    _last_engage: float = 0.0
    _last_follow: float = 0.0
    _FOLLOW_COOLDOWN_S = 120.0   # 2 min between follow activations

    def update(self) -> Status:
        if bb.audio_speaking:
            return Status.SUCCESS
        now = time.monotonic()
        if now - DoEngagePresence._last_engage < ENGAGE_INTERVAL_S:
            return Status.SUCCESS
        DoEngagePresence._last_engage = now

        from expression.eyes import EyeExpression, eye_engine
        from expression.sounds import sounds
        import random

        # Follow-mode when actively hanging out
        if (bb.activity == "hangout"
                and now - DoEngagePresence._last_follow > self._FOLLOW_COOLDOWN_S):
            DoEngagePresence._last_follow = now
            from behavior.navigation import navigation
            self._fire(navigation.follow_mode(duration=60))
            log.info("bt.follow_mode_started")
            return Status.SUCCESS

        if random.random() < 0.3:
            eye_engine.set_expression(EyeExpression.CURIOUS, duration=2.0)
            self._fire(sounds.play("chirp_curious"))
        return Status.SUCCESS


class DoCoPresence(_AsyncAction):
    """
    Shared-activity companionship: when the household is watching TV or
    quietly working, come over, settle nearby, and *be together* —
    glance at the person now and then, startle at loud TV moments,
    very occasionally murmur a comment. Pet on the sofa, not a chatbot.
    """
    _last_glance:     float = 0.0
    _last_tv_react:   float = 0.0
    _tv_react_for:    float = 0.0   # which bb.tv_moment we already reacted to
    _last_comment_try: float = 0.0

    GLANCE_GAP_S      = 45.0
    TV_REACT_COOLDOWN = 20.0
    COMMENT_TRY_GAP_S = 300.0   # attempt cadence; mind enforces its own 900s cooldown

    def update(self) -> Status:
        from core.action_router import router
        from core.intents import Intent
        from expression.eyes import EyeExpression, eye_engine
        from expression.sounds import sounds
        import random

        now = time.monotonic()
        watching = bb.activity == "watching_tv"

        # ── Settle once per session: stop wandering, come over, get comfortable ─
        if not bb.settled:
            bb.settled = True
            DoCoPresence._last_glance = now
            # Stop autonomous wander — presence trumps exploration
            router.emit(Intent.STOP, source="bt_copresence")
            # Brief pause then approach
            async def _settle_approach():
                import asyncio as _aio
                await _aio.sleep(0.8)
                from behavior.navigation import navigation
                await navigation.approach_person()
            self._fire(_settle_approach())
            eye_engine.set_expression(EyeExpression.LOVING, duration=4.0)
            if watching and not bb.audio_speaking:
                self._fire(sounds.play("purr"))
            log.info("bt.co_presence_settle", activity=bb.activity,
                     person=bb.person_name)
            return Status.SUCCESS

        # ── React to loud TV moments (explosion / goal / laugh surge) ────────
        if (watching and bb.tv_moment > DoCoPresence._tv_react_for
                and now - DoCoPresence._last_tv_react > self.TV_REACT_COOLDOWN):
            DoCoPresence._tv_react_for = bb.tv_moment
            DoCoPresence._last_tv_react = now
            eye_engine.set_expression(EyeExpression.SURPRISED, duration=2.5)
            if not bb.audio_speaking and random.random() < 0.5:
                self._fire(sounds.play("chirp_curious"))
            try:
                from core.personality import personality
                personality.process_event("tv_moment")
            except Exception:
                pass
            log.info("bt.tv_moment_react")
            return Status.SUCCESS

        # ── Occasional slow glance at the person, then back ──────────────────
        if now - DoCoPresence._last_glance > self.GLANCE_GAP_S * random.uniform(0.7, 1.4):
            DoCoPresence._last_glance = now

            async def _glance():
                eye_engine.set_pupil(random.choice((-0.6, 0.6)), 0.1)
                await asyncio.sleep(random.uniform(1.5, 3.0))
                eye_engine.set_pupil(0.0, 0.0)

            self._fire(_glance())

        # ── Rare murmured comment — TV only, heavily rate-limited ────────────
        if (watching and bb.person_name
                and now - DoCoPresence._last_comment_try > self.COMMENT_TRY_GAP_S):
            DoCoPresence._last_comment_try = now
            if random.random() < 0.35:
                from cognition.mind import cosmo_mind
                self._fire(cosmo_mind._maybe_speak(
                    "co_watch", bb.person_name, person_id=bb.person_id or None))

        return Status.SUCCESS


class DoWakeUp(_AsyncAction):
    def update(self) -> Status:
        from expression.eyes import EyeExpression, eye_engine
        from expression.sounds import sounds

        bb.sleeping = False
        eye_engine.set_expression(EyeExpression.CURIOUS, duration=3.0)
        self._fire(sounds.play("chime_greeting"))
        log.info("bt.wake_up", triggered_by=bb.person_name)
        return Status.SUCCESS


class DoSleepIdle(_AsyncAction):
    _last_breath: float = 0.0

    def update(self) -> Status:
        from expression.eyes import EyeExpression, eye_engine
        from expression.sounds import sounds

        eye_engine.set_expression(EyeExpression.SLEEPY, duration=10.0)
        now = time.monotonic()
        if now - DoSleepIdle._last_breath > BREATH_INTERVAL_S:
            DoSleepIdle._last_breath = now
            self._fire(sounds.play("sleep_breath"))
        return Status.SUCCESS


class DoEnterSleep(_AsyncAction):
    def update(self) -> Status:
        from expression.eyes import EyeExpression, eye_engine
        from expression.sounds import sounds

        bb.sleeping = True
        eye_engine.set_expression(EyeExpression.SLEEPY, duration=30.0)
        self._fire(sounds.play("sleep_breath"))
        log.info("bt.enter_sleep", energy=bb.energy)
        return Status.SUCCESS


class DoWanderExplore(_AsyncAction):
    _last_wander: float = 0.0

    def update(self) -> Status:
        now = time.monotonic()
        if now - DoWanderExplore._last_wander < WANDER_COOLDOWN_S:
            return Status.FAILURE  # cooldown — fall through to bored_med

        DoWanderExplore._last_wander = now
        from core.action_router import router
        from core.intents import Intent
        from utils.action_log import action_log

        alone_s = time.monotonic() - bb.alone_since
        action_log.set_context("behavior_wander", f"alone {alone_s:.0f}s")
        router.emit(Intent.WANDER, source="bt", duration=20)
        log.info("bt.wander", alone_s=alone_s)
        return Status.SUCCESS


class DoIdleSound(_AsyncAction):
    def update(self) -> Status:
        if bb.audio_speaking:
            return Status.SUCCESS
        now = time.monotonic()
        if now - bb.last_bored_sound_time < IDLE_SOUND_COOLDOWN:
            return Status.SUCCESS
        bb.last_bored_sound_time = now

        from core.action_router import router
        from core.intents import Intent
        from utils.action_log import action_log
        import random

        if bb.energy < 0.15:
            variant = "seek_attention"
        else:
            variant = random.choices(
                ["bored", "blink", "purr", "curious_sound"],
                weights=[4, 2, 2, 2],
            )[0]
        action_log.set_context("behavior_idle", "no person, bored")
        router.emit(Intent.IDLE_FIDGET, source="bt", variant=variant)
        log.info("bt.bored_idle", variant=variant)
        return Status.SUCCESS


class DiscoveryPending(Behaviour):
    """Succeeds when navigation has flagged an unhandled discovery event."""
    def update(self) -> Status:
        return Status.SUCCESS if bb.discovery_pending is not None else Status.FAILURE


class DoDiscovery(_AsyncAction):
    """React to a newly discovered obstacle: eyes + speech + optional WhatsApp notify."""

    def update(self) -> Status:
        event = bb.discovery_pending
        if event is None:
            return Status.FAILURE

        from core.action_router import router
        from core.intents import Intent
        from expression.eyes import EyeExpression, eye_engine
        from utils.action_log import action_log

        action_log.set_context("behavior_discovery", f"dist={event.distance_cm:.0f}cm")
        eye_engine.set_expression(EyeExpression.CURIOUS, duration=4.0)

        if bb.person_visible:
            # Person can see — speak in-person
            router.emit(Intent.SPEAK_AMBIENT, source="bt",
                        trigger="found_something",
                        hint=f"You found something {event.distance_cm:.0f}cm away. "
                              "Say one short curious sentence about it in Tanglish.")
        else:
            # Alone — schedule WhatsApp notification
            asyncio.get_event_loop().create_task(self._notify_owner(event))

        # Clear pending only after handling
        bb.discovery_pending = None
        from behavior.exploration import exploration_memory
        exploration_memory.mark_reported(event)
        log.info("bt.discovery_handled", dist_cm=event.distance_cm,
                 person=bb.person_visible)
        return Status.SUCCESS

    @staticmethod
    async def _notify_owner(event) -> None:
        import datetime
        from cognition.notifications import notifications
        ts = datetime.datetime.fromtimestamp(event.ts).strftime("%H:%M")
        msg = (
            f"🤖 Cosmo here! I found something interesting at {ts} "
            f"(about {event.distance_cm:.0f}cm away). "
            "Come check it out? 👀"
        )
        await notifications.send("found_something", msg)


class DoIdle(Behaviour):
    """Always-succeeding background idle — occasional attention blinks."""
    _last_blink: float = 0.0
    _BLINK_INTERVAL_S = 5.0

    def update(self) -> Status:
        from expression.eyes import EyeExpression, eye_engine
        import random

        now = time.monotonic()
        if now - DoIdle._last_blink > self._BLINK_INTERVAL_S:
            DoIdle._last_blink = now
            if random.random() < 0.4:
                eye_engine.set_expression(EyeExpression.NEUTRAL, duration=0.5)
        return Status.SUCCESS


# ── Tree builder ──────────────────────────────────────────────────────────────

def build_tree() -> Behaviour:
    root = py_trees.composites.Selector(name="ROOT", memory=False)

    # ── SAFETY (stub — reserved for cliff/obstacle/pickup) ────────────────────
    safety = py_trees.composites.Sequence(name="SAFETY", memory=False)
    safety.add_child(SafetyTriggered(name="SafetyTriggered"))

    # ── SLEEP_ACTIVE ──────────────────────────────────────────────────────────
    sleep_active = py_trees.composites.Sequence(name="SLEEP_ACTIVE", memory=False)
    sleep_active.add_children([
        IsSleeping(name="IsSleeping"),
        DoSleepIdle(name="SleepIdle"),
    ])

    # ── WAKE_FROM_SLEEP ───────────────────────────────────────────────────────
    wake_from_sleep = py_trees.composites.Sequence(name="WAKE_FROM_SLEEP", memory=False)
    wake_from_sleep.add_children([
        WasSleepingPersonArrived(name="WasSleepingPersonArrived"),
        DoWakeUp(name="DoWakeUp"),
    ])

    # ── SOCIAL ────────────────────────────────────────────────────────────────
    # GESTURE — highest priority inside SOCIAL
    def _gesture_seq(raw_name: str, action: Behaviour, node_name: str):
        seq = py_trees.composites.Sequence(name=node_name, memory=False)
        seq.add_children([GestureDetected(gesture_name=raw_name, name=f"Gest_{node_name}"), action])
        return seq

    from core.capabilities import Capability

    gesture_sel = py_trees.composites.Selector(name="GESTURE_INNER", memory=False)
    gesture_sel.add_children([
        _gesture_seq("Open_Palm",   DoWaveResponse(name="DoWaveResponse"), "WAVE"),
        _gesture_seq("Thumb_Up",    DoThumbsUp(name="DoThumbsUp"),         "THUMBS"),
        _gesture_seq("Victory",     DoPeace(name="DoPeace"),               "PEACE"),
        _gesture_seq("Closed_Fist", DoFistReact(name="DoFistReact"),       "FIST"),
        _gesture_seq("ILoveYou",    DoLoveReact(name="DoLoveReact"),       "LOVE"),
        _gesture_seq("Pointing_Up", DoPointReact(name="DoPointReact"),     "POINT"),
    ])
    gesture_gated = py_trees.composites.Sequence(name="GESTURE", memory=False)
    gesture_gated.add_children([
        CapUsable(Capability.VISION, name="CapVision"),
        gesture_sel,
    ])

    greet_seq = py_trees.composites.Sequence(name="GREET", memory=False)
    greet_seq.add_children([
        CapUsable(Capability.FACE_ID, name="CapFaceID"),
        PersonVisible(name="PersonVisible_G"),
        NotGreetedRecently(name="NotGreetedRecently"),
        DoGreet(name="DoGreet"),
    ])

    emotion_seq = py_trees.composites.Sequence(name="EMOTION_REACT", memory=False)
    emotion_seq.add_children([
        CapUsable(Capability.EMOTION_READ, name="CapEmotionRead"),
        PersonVisible(name="PersonVisible_E"),
        EmotionChanged(name="EmotionChanged"),
        DoEmotionReact(name="DoEmotionReact"),
    ])

    # CO_PRESENCE — shared activity (TV / quiet company). No PersonVisible
    # gate: the person may be on the sofa outside camera view; ActivityMonitor
    # already requires a recently-seen person before setting these activities.
    co_presence_seq = py_trees.composites.Sequence(name="CO_PRESENCE", memory=False)
    co_presence_seq.add_children([
        ActivityIs("watching_tv", "quiet_company", name="ActivityShared"),
        DoCoPresence(name="DoCoPresence"),
    ])

    presence_seq = py_trees.composites.Sequence(name="PERSON_PRESENT", memory=False)
    presence_seq.add_children([
        PersonVisible(name="PersonVisible_P"),
        DoEngagePresence(name="DoEngagePresence"),
    ])

    social = py_trees.composites.Selector(name="SOCIAL", memory=False)
    social.add_children([gesture_gated, greet_seq, emotion_seq, co_presence_seq, presence_seq])

    # ── AUTONOMOUS ────────────────────────────────────────────────────────────
    enter_sleep = py_trees.composites.Sequence(name="ENTER_SLEEP", memory=False)
    enter_sleep.add_children([
        EnergyLow(name="EnergyLow"),
        DoEnterSleep(name="DoEnterSleep"),
    ])

    # DISCOVERY — highest priority in autonomous (gates on DiscoveryPending)
    discovery_seq = py_trees.composites.Sequence(name="DISCOVERY", memory=False)
    discovery_seq.add_children([
        DiscoveryPending(name="DiscoveryPending"),
        DoDiscovery(name="DoDiscovery"),
    ])

    bored_high = py_trees.composites.Sequence(name="BORED_HIGH", memory=False)
    bored_high.add_children([
        AloneLong(threshold_s=ALONE_HIGH_S, name="AloneLong_180"),
        EnergyHigh(name="EnergyHigh"),
        DoWanderExplore(name="DoWanderExplore"),
    ])

    bored_med = py_trees.composites.Sequence(name="BORED_MED", memory=False)
    bored_med.add_children([
        AloneLong(threshold_s=ALONE_MED_S, name="AloneLong_60"),
        DoIdleSound(name="DoIdleSound"),
    ])

    autonomous = py_trees.composites.Selector(name="AUTONOMOUS", memory=False)
    autonomous.add_children([discovery_seq, enter_sleep, bored_high, bored_med, DoIdle(name="IDLE")])

    root.add_children([safety, sleep_active, wake_from_sleep, social, autonomous])
    return root


# ── BehaviorTree engine ───────────────────────────────────────────────────────

class BehaviorTree:
    """Wraps the py_trees tree with an asyncio 100ms tick loop."""

    def __init__(self) -> None:
        self._tree: Optional[py_trees.trees.BehaviourTree] = None
        self._task: Optional[asyncio.Task] = None
        self._running   = False
        self._tick_count = 0

    def setup(self) -> None:
        root = build_tree()
        self._tree = py_trees.trees.BehaviourTree(root=root)
        self._tree.setup(timeout=1)
        node_count = sum(1 for _ in self._tree.root.iterate())
        log.info("bt.setup", nodes=node_count)

    async def start(self) -> None:
        if self._tree is None:
            self.setup()
        self._running = True
        self._task = asyncio.create_task(self._tick_loop())
        log.info("bt.started", tick_hz=TICK_HZ)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("bt.stopped", ticks=self._tick_count)

    async def _tick_loop(self) -> None:
        interval = 1.0 / TICK_HZ
        while self._running:
            try:
                self._sync_from_personality()
                self._tree.tick()
                self._tick_count += 1
            except Exception as e:
                log.error("bt.tick_error", error=str(e)[:200])
            await asyncio.sleep(interval)

    def _sync_from_personality(self) -> None:
        try:
            from core.personality import personality
            bb.mood   = personality.state.mood
            bb.energy = personality.state.energy
        except Exception:
            pass

    def tick_once(self) -> None:
        """Synchronous single tick — for unit tests."""
        if self._tree is None:
            self.setup()
        self._tree.tick()
        self._tick_count += 1

    @property
    def tick_count(self) -> int:
        return self._tick_count


behavior_tree = BehaviorTree()
