"""
Central async event bus — publish/subscribe with priority queues.

Design decisions:
- asyncio-native: all handlers are async coroutines.
- Safety events get their own priority queue and are dispatched before normal events.
- Thread-safe: hardware ISRs call publish_threadsafe() from non-async contexts.
- Event history with TTL lets new subscribers replay recent events.
- Dead letter queue captures unhandled events for diagnostics.
"""

import asyncio
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Deque, Dict, List, Optional, Set

from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)


class EventPriority(int, Enum):
    SAFETY = 0      # cliff, pickup, obstacle — always first
    HIGH = 1        # touch, wake word, face recognized
    NORMAL = 2      # motion, sound, state changes
    LOW = 3         # idle behaviors, ambient updates


class EventType(str, Enum):
    # ── Safety (highest priority) ─────────────────────────────────────────────
    CLIFF_DETECTED = "safety.cliff"
    OBSTACLE_CRITICAL = "safety.obstacle.critical"
    OBSTACLE_WARNING = "safety.obstacle.warning"
    PICKUP_DETECTED = "safety.pickup"
    BATTERY_CRITICAL = "safety.battery.critical"
    BATTERY_LOW = "safety.battery.low"
    MOTOR_STALL = "safety.motor.stall"
    THERMAL_WARNING = "safety.thermal.warning"

    # ── Perception ────────────────────────────────────────────────────────────
    PERSON_DETECTED = "perception.person.detected"
    PERSON_LOST = "perception.person.lost"
    PERSON_APPROACHING = "perception.person.approaching"
    PERSON_LEAVING = "perception.person.leaving"
    FACE_RECOGNIZED = "perception.face.recognized"
    FACE_UNKNOWN = "perception.face.unknown"
    EMOTION_DETECTED = "perception.emotion"
    MOTION_DETECTED = "perception.motion"
    MOTION_STOPPED = "perception.motion.stopped"
    GESTURE_DETECTED = "perception.gesture"
    GESTURE_WAVE      = "perception.gesture.wave"
    GESTURE_THUMBS_UP = "perception.gesture.thumbs_up"
    GESTURE_PEACE     = "perception.gesture.peace"
    GESTURE_FIST      = "perception.gesture.fist"
    GESTURE_LOVE      = "perception.gesture.love"
    GESTURE_POINT     = "perception.gesture.point"
    TOUCH_DETECTED = "perception.touch"
    TOUCH_LONG = "perception.touch.long"
    SOUND_DETECTED = "perception.sound"
    WAKE_WORD = "perception.wake_word"
    SPEECH_DETECTED = "perception.speech"
    SPEECH_END = "perception.speech.end"
    ROOM_CHANGED = "perception.room.changed"
    LIGHT_CHANGED = "perception.light.changed"
    DISTANCE_UPDATED = "perception.distance.updated"

    # ── State ─────────────────────────────────────────────────────────────────
    MOOD_CHANGED = "state.mood.changed"
    ENERGY_CHANGED = "state.energy.changed"
    BEHAVIOR_CHANGED = "state.behavior.changed"
    STATE_CHANGED = "state.changed"
    ACTIVITY_CHANGED = "state.activity.changed"   # ambient household activity (TV, quiet)

    # ── Interaction ───────────────────────────────────────────────────────────
    CONVERSATION_START = "interaction.conversation.start"
    CONVERSATION_END = "interaction.conversation.end"
    RESPONSE_READY = "interaction.response.ready"
    USER_INTENT = "interaction.intent"

    # ── Hardware ──────────────────────────────────────────────────────────────
    I2C_ERROR = "hardware.i2c.error"
    SENSOR_TIMEOUT = "hardware.sensor.timeout"
    CAMERA_FRAME = "hardware.camera.frame"
    CAPABILITY_CHANGED = "hardware.capability.changed"

    # ── Attention ─────────────────────────────────────────────────────────────
    ATTENTION_SHIFTED = "attention.shifted"   # new target acquired
    ATTENTION_LOST    = "attention.lost"      # attention faded, no target

    # ── Smart home ────────────────────────────────────────────────────────────
    SMARTHOME_DEVICE_ON  = "smarthome.device.on"   # e.g. TV turned on
    SMARTHOME_DEVICE_OFF = "smarthome.device.off"  # e.g. lights off
    SMARTHOME_MOTION     = "smarthome.motion"       # motion sensor from HA/MQTT
    SMARTHOME_PRESENCE   = "smarthome.presence"     # someone home / left (phone GPS)
    SMARTHOME_SCENE      = "smarthome.scene"        # scene activated (movie, bedtime…)

    # ── Internal ──────────────────────────────────────────────────────────────
    TICK = "internal.tick"              # periodic heartbeat
    SHUTDOWN = "internal.shutdown"


# Priority mapping — determines queue insertion order
_EVENT_PRIORITIES: Dict[EventType, EventPriority] = {
    EventType.CLIFF_DETECTED: EventPriority.SAFETY,
    EventType.OBSTACLE_CRITICAL: EventPriority.SAFETY,
    EventType.PICKUP_DETECTED: EventPriority.SAFETY,
    EventType.BATTERY_CRITICAL: EventPriority.SAFETY,
    EventType.MOTOR_STALL: EventPriority.SAFETY,
    EventType.GESTURE_WAVE:      EventPriority.HIGH,
    EventType.GESTURE_THUMBS_UP: EventPriority.HIGH,
    EventType.GESTURE_PEACE:     EventPriority.HIGH,
    EventType.GESTURE_FIST:      EventPriority.HIGH,
    EventType.GESTURE_LOVE:      EventPriority.HIGH,
    EventType.GESTURE_POINT:     EventPriority.HIGH,
    EventType.TOUCH_DETECTED: EventPriority.HIGH,
    EventType.TOUCH_LONG: EventPriority.HIGH,
    EventType.WAKE_WORD: EventPriority.HIGH,
    EventType.FACE_RECOGNIZED: EventPriority.HIGH,
    EventType.SPEECH_DETECTED: EventPriority.HIGH,
    EventType.CAMERA_FRAME: EventPriority.LOW,
    EventType.TICK: EventPriority.LOW,
}


@dataclass
class Event:
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.monotonic)
    priority: EventPriority = EventPriority.NORMAL

    def __post_init__(self) -> None:
        if self.priority == EventPriority.NORMAL:
            self.priority = _EVENT_PRIORITIES.get(self.type, EventPriority.NORMAL)

    def age_ms(self) -> float:
        return (time.monotonic() - self.timestamp) * 1000

    def __lt__(self, other: "Event") -> bool:
        # PriorityQueue: lower number = higher priority
        return (self.priority, self.timestamp) < (other.priority, other.timestamp)


Handler = Callable[[Event], Coroutine[Any, Any, None]]


@dataclass
class _Subscription:
    handler: Handler
    event_types: Optional[Set[EventType]]   # None = all events
    filter_fn: Optional[Callable[[Event], bool]]
    once: bool = False


class EventBus:
    """
    Central async event bus.

    Usage:
        bus = EventBus()
        await bus.start()

        @bus.on(EventType.TOUCH_DETECTED)
        async def on_touch(event: Event):
            print(f"Touched! data={event.data}")

        await bus.publish(Event(type=EventType.TOUCH_DETECTED, data={"pin": 5}))
    """

    HISTORY_TTL_S = 30.0        # keep events in history for this long
    HISTORY_MAX = 500            # max events in history
    DEAD_LETTER_MAX = 100        # max dead-letter entries

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._subscriptions: List[_Subscription] = []
        self._history: Deque[Event] = deque(maxlen=self.HISTORY_MAX)
        self._dead_letter: Deque[Event] = deque(maxlen=self.DEAD_LETTER_MAX)
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._handler_tasks: set = set()
        self._stats = {"published": 0, "dispatched": 0, "dead_letter": 0}

    # ── Subscription API ─────────────────────────────────────────────────────

    def on(
        self,
        *event_types: EventType,
        filter_fn: Optional[Callable[[Event], bool]] = None,
    ) -> Callable:
        """Decorator: subscribe handler to one or more event types."""
        def decorator(handler: Handler) -> Handler:
            self.subscribe(handler, event_types=set(event_types), filter_fn=filter_fn)
            return handler
        return decorator

    def subscribe(
        self,
        handler: Handler,
        event_types: Optional[Set[EventType]] = None,
        filter_fn: Optional[Callable[[Event], bool]] = None,
        once: bool = False,
    ) -> None:
        self._subscriptions.append(_Subscription(
            handler=handler,
            event_types=event_types,
            filter_fn=filter_fn,
            once=once,
        ))

    def unsubscribe(self, handler: Handler) -> None:
        self._subscriptions = [s for s in self._subscriptions if s.handler is not handler]

    # ── Publish API ──────────────────────────────────────────────────────────

    async def publish(self, event: Event) -> None:
        """Publish from an async context."""
        self._history.append(event)
        telemetry.increment(f"event.{event.type}")
        telemetry.increment("event.total")
        self._stats["published"] += 1
        await self._queue.put((event.priority, event.timestamp, event))

    def publish_sync(self, event: Event, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Thread-safe publish from hardware ISRs or sync contexts."""
        lp = loop or asyncio.get_event_loop()
        lp.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self.publish(event))
        )

    # ── History API ──────────────────────────────────────────────────────────

    def recent(
        self,
        event_type: Optional[EventType] = None,
        max_age_s: float = HISTORY_TTL_S,
        limit: int = 20,
    ) -> List[Event]:
        """Return recent events, newest first."""
        now = time.monotonic()
        events = [
            e for e in reversed(self._history)
            if (now - e.timestamp) <= max_age_s
            and (event_type is None or e.type == event_type)
        ]
        return events[:limit]

    def last(self, event_type: EventType) -> Optional[Event]:
        """Most recent event of a given type, or None."""
        results = self.recent(event_type, limit=1)
        return results[0] if results else None

    # ── Dispatch loop ────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop(), name="event_bus")
        log.info("event_bus.started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._handler_tasks:
            await asyncio.gather(*self._handler_tasks, return_exceptions=True)
        log.info("event_bus.stopped", **self._stats)

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                _priority, _ts, event = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            await self._dispatch(event)
            self._queue.task_done()

    async def _dispatch(self, event: Event) -> None:
        matched = False
        to_remove = []

        for sub in self._subscriptions:
            if sub.event_types and event.type not in sub.event_types:
                continue
            if sub.filter_fn and not sub.filter_fn(event):
                continue

            matched = True
            # Dispatch concurrently — a slow handler must not block safety
            # events (OQ-3). Errors surface in _handler_done.
            try:
                result = sub.handler(event)
            except Exception as e:
                log.error("event_bus.handler_error",
                           event_type=event.type,
                           handler=getattr(sub.handler, "__name__", "?"),
                           error=str(e), exc_info=True)
                result = None
            if asyncio.iscoroutine(result):
                task = asyncio.create_task(
                    result,
                    name=f"evt:{event.type}:{getattr(sub.handler, '__name__', '?')}",
                )
                self._handler_tasks.add(task)
                task.add_done_callback(self._handler_done)

            if sub.once:
                to_remove.append(sub)

        for sub in to_remove:
            self._subscriptions.remove(sub)

        if not matched:
            self._dead_letter.append(event)
            self._stats["dead_letter"] += 1
            log.debug("event_bus.dead_letter", event_type=event.type, event_id=event.id)

        self._stats["dispatched"] += 1
        telemetry.gauge("event_bus.queue_depth", self._queue.qsize())

    def _handler_done(self, task: "asyncio.Task") -> None:
        self._handler_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("event_bus.handler_error",
                       handler=task.get_name(),
                       error=str(exc),
                       exc_info=exc)

    def stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "queue_depth": self._queue.qsize(),
            "subscriber_count": len(self._subscriptions),
            "history_size": len(self._history),
            "dead_letter_size": len(self._dead_letter),
        }


# Module-level singleton — all subsystems share this bus
bus = EventBus()
