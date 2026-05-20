"""
Cosmo — Reactive Companion Demo (Phase A).
Run: python3 tools/cosmo_demo.py

Systems active:
  Vision     → person detect → face recog → emotion
  Sounds     → numpy-generated reactive audio expressions
  Sensors    → touch/PIR/IMU/light/battery (mocked until hardware arrives)
  Eyes       → 12 expressions in terminal (OLED when hardware arrives)
  Behavior   → idle behaviors, personality-driven reactions
  Navigation → movement with safety (mocked until motors wired)

Ctrl+C to exit.
"""

import asyncio
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env from robot root (PICOVOICE_KEY, ANTHROPIC_API_KEY, etc.)
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from utils.logger import get_logger
log = get_logger(__name__)

from behavior.engine import behavior_engine
from behavior.navigation import navigation
from cognition.conversation import conversation
from cognition.intent import intent_parser
from cognition.mind import cosmo_mind
from core.event_bus import bus, Event, EventType
from core.memory.episodic import episodic
from core.personality import personality
from core.state_machine import sm, RobotState
from expression.eyes import EyeExpression, eye_engine
from core.behavior_tree import behavior_tree, bb as cosmo_bb
from perception.vision.gesture import gesture_loop
from expression.sounds import sounds
from hardware.motors import motor_controller
from hardware.sensor_manager import sensor_manager
from hardware.servos import servo_controller
from perception.audio.pipeline import audio_pipeline
from perception.video.stream_server import stream_server
from perception.vision.camera import camera
from perception.vision.person import person_detector
from perception.vision.vision_loop import vision_loop

console = Console()

_state = {
    "person_name": "no one",
    "emotion": "—",
    "last_response": "...",
    "backend": "—",
    "latency_ms": 0,
    "face_conf": 0.0,
    "persons_visible": 0,
    "listen_state": "passive",
    "events": [],
    "mood": 0.5,
    "energy": 0.7,
    "greeted": set(),
    "eye_expr": "neutral",
    "last_intent": "—",
    "distance_cm": 100.0,
    "battery_pct": 85.0,
    "servo_pan": 90.0,
    "servo_tilt": 90.0,
    "nav_state": "idle",
    "sensor_mock": True,
    "last_person_time": 0.0,    # monotonic time when person was last seen
    "last_emotion": "",         # track emotion changes
}


def _bar(v: float, w: int = 12, center: bool = False) -> str:
    if center:
        n = int((v + 1) / 2 * w)
    else:
        n = int(v * w)
    return "█" * n + "░" * (w - n)


def _build_panel() -> Panel:
    s = _state
    ls = s["listen_state"]
    ls_color = {"passive": "dim", "listening": "green",
                "thinking": "yellow", "speaking": "cyan"}.get(ls, "white")

    pcolor = "green" if s["person_name"] not in ("no one", "stranger") else "yellow"
    mood   = s["mood"]
    mc     = "green" if mood > 0.3 else ("yellow" if mood > -0.1 else "red")
    mock_tag = "[dim](MOCK)[/dim]" if s["sensor_mock"] else "[green](REAL)[/green]"

    lines = [
        f"[bold]Sees:[/bold]    [{pcolor}]{s['person_name']}[/{pcolor}]  [dim](conf {s['face_conf']:.0%})[/dim]",
        f"[bold]Emotion:[/bold] {s['emotion']}   [bold]Persons:[/bold] {s['persons_visible']}",
        f"[bold]Listen:[/bold]  [{ls_color}]{ls.upper()}[/{ls_color}]   [bold]Intent:[/bold] [dim]{s['last_intent']}[/dim]",
        "",
        f"[bold]Mood:[/bold]   [[{mc}]{_bar(mood, center=True)}[/{mc}]] {mood:+.2f}",
        f"[bold]Energy:[/bold] [[blue]{_bar(s['energy'])}[/blue]] {s['energy']:.2f}",
        "",
        f"[bold]Eyes:[/bold]   [cyan]{s['eye_expr'].upper()}[/cyan]   "
        f"[bold]Nav:[/bold] [yellow]{s['nav_state']}[/yellow]",
        f"[bold]Dist:[/bold]   {s['distance_cm']:.0f}cm  "
        f"[bold]Battery:[/bold] {s['battery_pct']:.0f}%  "
        f"[bold]Pan/Tilt:[/bold] {s['servo_pan']:.0f}°/{s['servo_tilt']:.0f}°  {mock_tag}",
        "",
        "[bold]Cosmo:[/bold]",
        f'  [italic cyan]"{s["last_response"][:100]}"[/italic cyan]',
        f"  [dim]{s['backend']} | {s['latency_ms']}ms[/dim]",
        "",
        "[bold dim]Events:[/bold dim]",
    ]
    for ev in s["events"][-6:]:
        lines.append(f"  [dim]{ev}[/dim]")

    return Panel(
        "\n".join(lines),
        title="[bold cyan]COSMO[/bold cyan]",
        subtitle='[dim]Ctrl+C to quit[/dim]',
        border_style="cyan",
    )


_EMOTION_REACTIONS = {
    "happy":     {"expr": EyeExpression.HAPPY,     "sound": "trill_excited", "proactive": 0.4},
    "sad":       {"expr": EyeExpression.SAD,        "sound": "whimper_sad",   "proactive": 0.7},
    "angry":     {"expr": EyeExpression.SCARED,     "sound": "whimper_sad",   "proactive": 0.15},
    "surprised": {"expr": EyeExpression.SURPRISED,  "sound": "chirp_happy",   "proactive": 0.5},
    "fearful":   {"expr": EyeExpression.SCARED,     "sound": "whimper_sad",   "proactive": 0.5},
    "disgusted": {"expr": EyeExpression.CONFUSED,   "sound": None,            "proactive": 0.2},
    "neutral":   {"expr": EyeExpression.NEUTRAL,    "sound": None,            "proactive": 0.05},
}

def _load_person_config() -> dict:
    """Load known_persons from personality.yaml — prompts + greeting lines."""
    try:
        import yaml
        cfg_path = Path(__file__).parent.parent / "config" / "personality.yaml"
        data = yaml.safe_load(cfg_path.read_text())
        return data.get("personality", {}).get("known_persons", {})
    except Exception:
        return {}

_KNOWN_PERSONS = _load_person_config()

_approaching = False   # guard against concurrent face-and-approach tasks


async def _face_and_approach(person_x: float) -> None:
    """Turn to face person then nudge forward — feels alive."""
    global _approaching
    if _approaching:
        return
    _approaching = True
    try:
        DEAD = 0.2
        tasks = []
        if person_x < -DEAD:
            tasks.append(navigation.turn_left(speed=0.35, duration=abs(person_x) * 0.5))
        elif person_x > DEAD:
            tasks.append(navigation.turn_right(speed=0.35, duration=person_x * 0.5))
        eye_engine.set_expression(EyeExpression.CURIOUS, duration=2.0)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0.3)
        if _state["distance_cm"] > 80:
            await navigation.forward(speed=0.25, duration=0.6)
    finally:
        _approaching = False


async def setup_event_handlers() -> None:

    @bus.on(EventType.PERSON_DETECTED)
    async def on_person(event: Event) -> None:
        _state["persons_visible"] = 1
        _state["last_person_time"] = time.monotonic()
        cosmo_bb.person_visible = True
        person_x = event.data.get("bbox_center_x", 0.0)

        # Stop any wandering immediately
        if navigation.state.value == "wandering":
            await navigation.stop()
            await sounds.play("chirp_curious")

        # Turn to face + approach (_approaching flag prevents concurrent tasks)
        asyncio.create_task(_face_and_approach(person_x))

        await sm.transition_to(RobotState.ALERT_PERSON, trigger="person_detected")
        _state["events"].append(f"Person @ x={person_x:.2f}")

    @bus.on(EventType.FACE_RECOGNIZED)
    async def on_face(event: Event) -> None:
        name = event.data.get("name", "?")
        conf = event.data.get("confidence", 0)
        person_id = event.data.get("person_id", f"person_{name.lower()}")
        person_x = event.data.get("bbox_center_x", 0.0)
        _state["person_name"] = name
        _state["face_conf"] = conf
        _state["last_person_time"] = time.monotonic()
        cosmo_bb.person_visible = True
        cosmo_bb.person_name = name
        cosmo_bb.person_id = person_id
        _state["events"].append(f"Face: {name} ({conf:.0%})")

        audio_pipeline.update_person(person_id, _state.get("last_emotion") or None)
        await sm.transition_to(RobotState.INTERACTIVE, trigger="face_recognized")

        if name not in _state["greeted"]:
            _state["greeted"].add(name)
            if not conversation.in_conversation:
                await conversation.start_session(person_id, name)
            conversation.set_person(person_id, name)

            # Face them + play greeting chime
            await asyncio.gather(
                _face_and_approach(person_x),
                sounds.play("chime_greeting"),
                return_exceptions=True,
            )
            _state["events"].append(f"Greeted {name}")

    @bus.on(EventType.FACE_UNKNOWN)
    async def on_unknown(event: Event) -> None:
        _state["person_name"] = "stranger"
        _state["face_conf"] = 0.0
        _state["last_person_time"] = time.monotonic()
        _state["events"].append("Unknown face")
        # Curious but cautious
        eye_engine.set_expression(EyeExpression.CURIOUS, duration=3.0)
        await sounds.play("chirp_curious")

    @bus.on(EventType.PERSON_LOST)
    async def on_lost(event: Event) -> None:
        prev = _state["person_name"]
        _state["person_name"] = "no one"
        _state["face_conf"] = 0.0
        _state["persons_visible"] = 0
        _state["greeted"].discard(prev)
        _state["events"].append("Person left")
        cosmo_bb.person_visible = False
        cosmo_bb.person_name = ""
        cosmo_bb.person_id = ""
        cosmo_bb.alone_since = time.monotonic()
        audio_pipeline.update_person(None)
        if conversation.in_conversation:
            await conversation.end_session()
        await sm.transition_to(RobotState.IDLE_CURIOUS, trigger="person_lost")
        # Look around for them
        eye_engine.set_expression(EyeExpression.CURIOUS, duration=3.0)
        eye_engine.set_pupil(-0.8, 0)

    @bus.on(EventType.EMOTION_DETECTED)
    async def on_emotion(event: Event) -> None:
        emotion = event.data.get("emotion", "?")
        conf = event.data.get("confidence", 0)
        _state["emotion"] = f"{emotion} ({conf:.0%})"
        conversation.set_emotion(emotion)
        cosmo_bb.emotion = emotion

        # Only react if emotion actually changed
        if emotion == _state["last_emotion"]:
            return
        _state["last_emotion"] = emotion
        _state["events"].append(f"Emotion: {emotion}")

        reaction = _EMOTION_REACTIONS.get(emotion.lower(), _EMOTION_REACTIONS["neutral"])
        eye_engine.set_expression(reaction["expr"], duration=4.0)
        tasks = []
        if reaction["sound"]:
            tasks.append(sounds.play(reaction["sound"]))

        # Physical reaction to emotion
        if emotion == "happy":
            tasks.append(behavior_engine.happy_reaction())
        elif emotion == "sad" and _state["distance_cm"] > 60:
            tasks.append(navigation.forward(speed=0.2, duration=1.0))
        elif emotion == "angry":
            tasks.append(navigation.backward(speed=0.2, duration=0.6))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @bus.on(EventType.WAKE_WORD)
    async def on_wake(event: Event) -> None:
        _state["events"].append(f"Wake: '{event.data.get('word', '?')}'")
        eye_engine.set_expression(EyeExpression.CURIOUS, duration=3.0)
        await sounds.play("beep_ack")
        await sm.transition_to(RobotState.LISTENING, trigger="wake_word")

    @bus.on(EventType.USER_INTENT)
    async def on_intent(event: Event) -> None:
        intent = event.data.get("intent", "?")
        action = event.data.get("action", "?")
        _state["last_intent"] = f"{intent} → {action}"
        _state["events"].append(f"Intent: {intent}")
        if action == "navigation.approach_person":
            asyncio.create_task(navigation.approach_person())
        elif action == "navigation.retreat":
            asyncio.create_task(navigation.retreat())
        elif action == "navigation.stop":
            asyncio.create_task(navigation.stop())
        elif action == "navigation.spin_360":
            asyncio.create_task(navigation.spin_360())
        elif action == "behavior.dance":
            asyncio.create_task(behavior_engine.dance())
        elif action == "behavior.happy_reaction":
            asyncio.create_task(behavior_engine.happy_reaction())
        elif action == "behavior.love_reaction":
            asyncio.create_task(behavior_engine.love_reaction())
        elif action == "state.transition.SLEEPING":
            eye_engine.set_expression(EyeExpression.SLEEPY)
            await sounds.play("sleep_exhale")
        elif action == "audio.mute_30s":
            sounds.mute(30)
        elif action == "behavior.look_around":
            asyncio.create_task(behavior_engine._look_around())
        elif action == "cosmo_mind.disable":
            cosmo_mind.disable()
        elif action == "cosmo_mind.enable":
            cosmo_mind.enable()

    @bus.on(EventType.GESTURE_WAVE)
    async def on_gesture_wave(event: Event) -> None:
        cosmo_bb.last_gesture = "Open_Palm"
        cosmo_bb.last_gesture_time = time.monotonic()
        _state["events"].append(f"Gesture: wave ({event.data.get('backend','?')})")

    @bus.on(EventType.GESTURE_THUMBS_UP)
    async def on_gesture_thumbs(event: Event) -> None:
        cosmo_bb.last_gesture = "Thumb_Up"
        cosmo_bb.last_gesture_time = time.monotonic()
        _state["events"].append("Gesture: thumbs_up")

    @bus.on(EventType.GESTURE_PEACE)
    async def on_gesture_peace(event: Event) -> None:
        cosmo_bb.last_gesture = "Victory"
        cosmo_bb.last_gesture_time = time.monotonic()
        _state["events"].append("Gesture: peace")

    @bus.on(EventType.GESTURE_FIST)
    async def on_gesture_fist(event: Event) -> None:
        cosmo_bb.last_gesture = "Closed_Fist"
        cosmo_bb.last_gesture_time = time.monotonic()
        _state["events"].append("Gesture: fist")

    @bus.on(EventType.GESTURE_LOVE)
    async def on_gesture_love(event: Event) -> None:
        cosmo_bb.last_gesture = "ILoveYou"
        cosmo_bb.last_gesture_time = time.monotonic()
        _state["events"].append("Gesture: love")

    @bus.on(EventType.GESTURE_POINT)
    async def on_gesture_point(event: Event) -> None:
        cosmo_bb.last_gesture = "Pointing_Up"
        cosmo_bb.last_gesture_time = time.monotonic()
        _state["events"].append("Gesture: point")

    @bus.on(EventType.TOUCH_DETECTED)
    async def on_touch(event: Event) -> None:
        from expression.eyes import PRIORITY_TOUCH
        zone = event.data.get("zone", "?")
        _state["events"].append(f"Touch: {zone}")
        eye_engine.set_expression(EyeExpression.LOVING, duration=3.0, priority=PRIORITY_TOUCH)
        await sounds.play("purr_petted")

    @bus.on(EventType.MOTION_DETECTED)
    async def on_motion(event: Event) -> None:
        _state["events"].append("Motion (PIR)")
        if _state["person_name"] == "no one":
            eye_engine.set_expression(EyeExpression.CURIOUS, duration=2.0)
            await sounds.play("chirp_curious")

    @bus.on(EventType.BATTERY_CRITICAL)
    async def on_bat_crit(event: Event) -> None:
        pct = event.data.get("percent", 0)
        _state["events"].append(f"Battery CRITICAL: {pct:.0f}%")
        eye_engine.set_expression(EyeExpression.SCARED, duration=10.0)
        await sounds.play("battery_low")

    @bus.on(EventType.RESPONSE_READY)
    async def on_response(event: Event) -> None:
        text = event.data.get("text", "")
        backend = event.data.get("backend", "?")
        latency = event.data.get("latency_ms", 0)
        if text:
            _state["last_response"] = text
            _state["backend"] = backend
            _state["latency_ms"] = latency


async def alone_watcher() -> None:
    """Start wandering when no person for 2+ min. Play sad sound when lonely 10+ min."""
    WANDER_AFTER_S = 120
    LONELY_AFTER_S = 600
    _last_lonely_speak = 0.0

    while True:
        await asyncio.sleep(10)
        if _state["persons_visible"]:
            continue

        alone_s = time.monotonic() - _state["last_person_time"]

        if alone_s > WANDER_AFTER_S and navigation.state.value == "idle":
            _state["events"].append("Alone → wandering")
            eye_engine.set_expression(EyeExpression.SLEEPY, duration=5.0)
            asyncio.create_task(navigation.wander(duration=30))

        if alone_s > LONELY_AFTER_S:
            now = time.monotonic()
            if now - _last_lonely_speak > 300:
                _last_lonely_speak = now
                eye_engine.set_expression(EyeExpression.SAD, duration=5.0)
                asyncio.create_task(sounds.play("whimper_lonely"))


async def memory_maintenance_loop() -> None:
    """Run episodic forgetting curve once per day at 03:00."""
    import datetime as _dt
    _last_run_day = -1
    while True:
        await asyncio.sleep(300)  # check every 5 min
        now = _dt.datetime.now()
        today = now.date().toordinal()
        if now.hour == 3 and _last_run_day != today:
            _last_run_day = today
            try:
                count = await episodic.apply_forgetting_curve()
                log.info("episodic.forgetting_curve_applied", decayed=count)
            except Exception as e:
                log.warning("episodic.forgetting_curve_error", error=str(e)[:80])


async def state_watcher() -> None:
    while True:
        _state["mood"]        = personality.state.mood
        _state["energy"]      = personality.state.energy
        _state["listen_state"] = audio_pipeline.state.value
        _state["eye_expr"]    = eye_engine.current_expression.value
        _state["nav_state"]   = navigation.state.value
        _state["servo_pan"]   = servo_controller.current_pan
        _state["servo_tilt"]  = servo_controller.current_tilt
        _state["sensor_mock"] = sensor_manager.is_mock
        cosmo_bb.audio_speaking = (audio_pipeline.state.value == "speaking")
        try:
            bat = sensor_manager.get_battery()
            _state["battery_pct"] = bat.get("percent", 85.0)
            _state["distance_cm"] = sensor_manager.get_distance_cm()
        except Exception:
            pass
        await asyncio.sleep(0.3)


async def main() -> None:
    console.print("\n[bold cyan]COSMO — Starting up[/bold cyan]")

    await bus.start()
    episodic.initialize()
    await personality.start()
    await sm.start(RobotState.IDLE_CALM)

    import os as _os
    _llm_key = _os.environ.get("ANTHROPIC_API_KEY", "")
    if _llm_key:
        console.print("[green]✓ LLM — Claude Haiku (primary)[/green]")
    else:
        console.print("[yellow]⚠ LLM — ANTHROPIC_API_KEY not set, set it in robot/.env[/yellow]")

    # Camera + vision
    if not await camera.start():
        console.print("[red]✗ Camera failed[/red]"); return
    console.print("[green]✓ Camera[/green]")

    await person_detector.start()
    console.print(f"[green]✓ Person detector ({person_detector.stats()['backend']})[/green]")

    await vision_loop.start()
    enrolled = vision_loop.get_face_engine().list_enrolled()
    em_ok = vision_loop.get_emotion_detector().is_available
    console.print(f"[green]✓ Face recognition — enrolled: {enrolled or ['none']}[/green]")
    console.print(f"[green]✓ Emotion detection ({'ON' if em_ok else 'OFF'})[/green]")

    # MJPEG stream server
    if await stream_server.start():
        console.print(f"[green]✓ Stream server — {stream_server.best_url()}[/green]")
    else:
        console.print("[yellow]⚠ Stream server failed to start[/yellow]")

    # Sensors (mocked until hardware arrives)
    await sensor_manager.start()
    console.print(f"[{'green' if not sensor_manager.is_mock else 'yellow'}]"
                  f"{'✓' if not sensor_manager.is_mock else '⚠'} Sensors "
                  f"({'real' if not sensor_manager.is_mock else 'mock'})[/{'green' if not sensor_manager.is_mock else 'yellow'}]")

    # Motors
    await motor_controller.initialize()
    await motor_controller.self_test()
    motor_label = "mock" if motor_controller.is_mock else "real"
    color = "yellow" if motor_controller.is_mock else "green"
    console.print(f"[{color}]{'⚠' if motor_controller.is_mock else '✓'} Motors ({motor_label})[/{color}]")

    # Navigation + Servos (mocked)
    await navigation.initialize()
    await servo_controller.initialize()
    console.print(f"[yellow]⚠ Navigation (mock)[/yellow]")
    console.print(f"[yellow]⚠ Servos (mock)[/yellow]")

    # Eye engine (terminal renderer — OLED when hardware arrives)
    await eye_engine.start()
    console.print("[green]✓ Eye engine (terminal)[/green]")

    # Sound engine
    await sounds.start()
    console.print("[green]✓ Sound engine[/green]")

    # Behavior engine (idle behaviors + proactive speech)
    await behavior_engine.start()
    console.print("[green]✓ Behavior engine[/green]")

    await cosmo_mind.start()
    console.print("[green]✓ Cosmo Mind (rule engine)[/green]")

    behavior_tree.setup()
    await behavior_tree.start()
    console.print("[green]✓ Behavior Tree (100ms tick)[/green]")

    gesture_loop.setup()
    await gesture_loop.start()
    console.print(f"[green]✓ Gesture loop ({gesture_loop.backend} @ {4}fps)[/green]")

    if await audio_pipeline.start():
        console.print(f"[green]✓ Audio pipeline — wake={audio_pipeline._wake_backend}[/green]")
    else:
        console.print("[yellow]⚠ Audio pipeline — no mic (C920 not found?)[/yellow]")

    await setup_event_handlers()
    asyncio.create_task(alone_watcher())

    # WhatsApp notifier — hardware events + decision logs → BanterAgent group
    try:
        from services.notifier import start as notifier_start
        await notifier_start()
        console.print("[green]✓ WhatsApp notifier (hardware events + decisions)[/green]")
    except Exception as e:
        console.print(f"[yellow]⚠ WhatsApp notifier failed: {e}[/yellow]")

    # Debug HTTP API — GET /state, /memory/recent, /health, POST /trigger/describe
    try:
        from services.api import service as api_service
        api_service.wire_state(_state, _state["events"])
        await api_service.start()
        console.print("[green]✓ Debug API — http://localhost:8000/docs[/green]")
    except Exception as e:
        console.print(f"[yellow]⚠ Debug API failed: {e}[/yellow]")

    console.print(f"\n[bold green]Cosmo is alive! {personality.describe()}[/bold green]")
    if enrolled:
        console.print(f'[dim]Walk in — Cosmo knows {", ".join(enrolled)}[/dim]')
    console.print(f'[dim]📹 Live stream: {stream_server.best_url()}[/dim]\n')

    asyncio.create_task(state_watcher())
    asyncio.create_task(memory_maintenance_loop())

    _shutdown = asyncio.Event()
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, _shutdown.set)

    try:
        with Live(console=console, refresh_per_second=3, transient=True) as live:
            while not _shutdown.is_set():
                live.update(_build_panel())
                await asyncio.sleep(0.33)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        console.print("\n[dim]Shutting down...[/dim]")
        try:
            from services.api import service as api_service
            await api_service.stop()
        except Exception:
            pass
        if conversation.in_conversation:
            await conversation.end_session()
        await gesture_loop.stop()
        await behavior_tree.stop()
        await cosmo_mind.stop()
        await audio_pipeline.stop()
        await motor_controller.stop(emergency=True)
        await stream_server.stop()
        await vision_loop.stop()
        await person_detector.stop()
        await camera.stop()
        await personality.stop()
        await bus.stop()
        console.print("[dim]Goodbye.[/dim]")


if __name__ == "__main__":
    import os as _os
    import subprocess as _sub
    try:
        # Boost process priority so vision/audio loops aren't starved
        _sub.run(["sudo", "renice", "-n", "-10", "-p", str(_os.getpid())],
                 capture_output=True, timeout=3)
    except Exception:
        pass
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
