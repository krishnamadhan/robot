"""
Cosmo — main entry point.

Brings up all subsystems in dependency order:
  1. Config + logging (no deps)
  2. Event bus (no deps)
  3. Hardware layer (deps: config)
  4. Memory system (deps: config, logging)
  5. Personality engine (deps: memory, config)
  6. State machine (deps: event bus, personality)
  7. Perception pipeline (deps: hardware, event bus)
  8. Behavior engine (deps: all above)

Graceful shutdown on SIGINT/SIGTERM.
"""

import asyncio
import os
import signal
import sys
from pathlib import Path

# Ensure robot root is on path regardless of working directory
sys.path.insert(0, str(Path(__file__).parent))

from utils.config import cfg
from utils.logger import get_logger, bind_context
from utils.telemetry import telemetry

log = get_logger("main")


class CosmoRobot:
    """Top-level coordinator — owns all subsystems."""

    def __init__(self) -> None:
        self._running = False
        self._subsystems = []

    async def start(self) -> None:
        bind_context(robot=cfg.personality.name)
        log.info("cosmo.starting", name=cfg.personality.name,
                  simulation=cfg.simulation_enabled())

        # ── Event bus ────────────────────────────────────────────────────────
        from core.event_bus import bus
        await bus.start()
        log.info("cosmo.subsystem_ok", name="event_bus")

        # ── Hardware layer ────────────────────────────────────────────────────
        from hardware.mock import mock_hardware
        hw_results = await mock_hardware.initialize_all()
        for name, ok in hw_results.items():
            if not ok:
                log.warning("cosmo.hardware_init_failed", component=name)
        log.info("cosmo.subsystem_ok", name="hardware",
                  initialized=sum(hw_results.values()), total=len(hw_results))

        # ── Memory system ─────────────────────────────────────────────────────
        from core.memory.episodic import episodic
        from core.memory.spatial import spatial
        from core.memory.working import wm
        await episodic.initialize()
        await wm.start()
        log.info("cosmo.subsystem_ok", name="memory")

        from cognition.activity import activity_monitor
        await activity_monitor.start()
        log.info("cosmo.subsystem_ok", name="activity_monitor")

        # ── Personality engine ────────────────────────────────────────────────
        from core.personality import personality
        await personality.start()
        log.info("cosmo.subsystem_ok", name="personality",
                  state=personality.describe())

        # ── Behavior tree (sole decision authority) ──────────────────────────
        from core.behavior_tree import behavior_tree
        behavior_tree.setup()
        await behavior_tree.start()
        log.info("cosmo.subsystem_ok", name="behavior_tree")

        # ── Camera pipeline ───────────────────────────────────────────────────
        from perception.vision.camera import camera
        cam_ok = await camera.start()
        if cam_ok:
            log.info("cosmo.subsystem_ok", name="camera")
        else:
            log.warning("cosmo.camera_unavailable")

        # ── Person detector ───────────────────────────────────────────────────
        if cam_ok:
            from perception.vision.person import person_detector
            await person_detector.start()
            log.info("cosmo.subsystem_ok", name="person_detector",
                      backend="yolov8n" if person_detector._use_yolo else "hog")

        # ── Wire event handlers ───────────────────────────────────────────────
        self._wire_handlers(bus, personality)

        self._running = True
        log.info("cosmo.ready", mood=personality.describe())
        print(f"\n{'='*50}")
        print(f"  Cosmo is awake! {personality.describe()}")
        print(f"  Simulation: {cfg.simulation_enabled()}")
        print(f"{'='*50}\n")

    def _wire_handlers(self, bus, personality) -> None:
        from core.event_bus import EventType, Event
        from core.action_router import router
        from core.behavior_tree import bb
        from core.intents import Intent
        import time as _time

        @bus.on(EventType.PERSON_DETECTED)
        async def on_person(event: Event) -> None:
            personality.process_event("person_arrived")
            bb.person_visible = True

        @bus.on(EventType.PERSON_LOST)
        async def on_person_lost(event: Event) -> None:
            personality.process_event("person_left")
            bb.person_visible = False
            bb.alone_since = _time.monotonic()

        @bus.on(EventType.TOUCH_DETECTED)
        async def on_touch(event: Event) -> None:
            personality.process_event("touch_gentle")
            router.emit(Intent.EXPRESS_AFFECTION, source="touch", speak=False)

        @bus.on(EventType.CLIFF_DETECTED)
        async def on_cliff(event: Event) -> None:
            log.warning("cosmo.cliff_detected")
            router.emit(Intent.STOP, source="cliff", emergency=True)
            router.emit(Intent.ALERT, source="cliff", reason="cliff")

        @bus.on(EventType.BATTERY_CRITICAL)
        async def on_battery_critical(event: Event) -> None:
            personality.process_event("battery_low")
            router.emit(Intent.STOP, source="battery", emergency=True)
            router.emit(Intent.ALERT, source="battery", reason="battery_critical")

        @bus.on(EventType.LIGHT_CHANGED)
        async def on_light(event: Event) -> None:
            lux = event.data.get("lux", 200)
            if lux < 10:
                personality.process_event("darkness")
            elif lux > 1000:
                personality.process_event("bright_light")

        @bus.on(EventType.STATE_CHANGED)
        async def on_state_changed(event: Event) -> None:
            log.info("cosmo.state", state=event.data.get("to"))

    async def run(self) -> None:
        """Main loop — keeps robot alive until shutdown."""
        tick = 0
        while self._running:
            await asyncio.sleep(5.0)
            tick += 1

            # Periodic housekeeping
            if tick % 12 == 0:   # every minute
                from core.personality import personality
                snap = telemetry.snapshot()
                log.info("cosmo.heartbeat",
                          cpu=snap.get("cpu_percent"),
                          temp=snap.get("cpu_temp_c"),
                          mood=round(personality.state.mood, 2),
                          energy=round(personality.state.energy, 2))

            # Quirk system — pick a random personality quirk
            if tick % 6 == 0:    # every 30 seconds
                from core.personality import personality
                quirk = personality.pick_quirk()
                if quirk:
                    log.debug("cosmo.quirk", id=quirk.get("id"))
                    # Behavior engine would execute this quirk

    async def stop(self) -> None:
        log.info("cosmo.stopping")
        self._running = False

        # Shutdown in reverse order
        try:
            from perception.vision.camera import camera
            await camera.stop()
        except Exception:
            pass

        try:
            from perception.vision.person import person_detector
            await person_detector.stop()
        except Exception:
            pass

        try:
            from core.personality import personality
            await personality.stop()
        except Exception:
            pass

        try:
            from hardware.mock import mock_hardware
            await mock_hardware.shutdown_all()
        except Exception:
            pass

        try:
            from core.event_bus import bus
            await bus.stop()
        except Exception:
            pass

        try:
            from core.memory.episodic import episodic
            await episodic.close()
        except Exception:
            pass

        log.info("cosmo.stopped")


async def main() -> None:
    robot = CosmoRobot()

    loop = asyncio.get_event_loop()

    def _handle_signal() -> None:
        log.info("cosmo.signal_received")
        asyncio.create_task(robot.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    try:
        await robot.start()
        await robot.run()
    except Exception as e:
        log.error("cosmo.fatal_error", error=str(e), exc_info=True)
    finally:
        await robot.stop()


if __name__ == "__main__":
    asyncio.run(main())
