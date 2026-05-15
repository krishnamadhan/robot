"""
Audio pipeline integration test.
Tests: mic → VAD → STT → LLM → TTS (JBL speaker)

Run: python3 tools/cosmo_audio_test.py
Say "Hey Cosmo" then speak — Cosmo will respond.
Ctrl+C to quit.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from core.event_bus import bus, Event, EventType
from core.personality import personality
from cognition.conversation import conversation
from cognition.llm import llm
from expression.speech import tts
from perception.audio.mic import mic
from perception.audio.stt import stt
from perception.audio.pipeline import audio_pipeline, ListenState

console = Console()

_state = {
    "pipeline_state": "passive",
    "last_heard": "—",
    "last_response": "—",
    "backend": "—",
    "latency_ms": 0,
    "wake_count": 0,
    "events": [],
}


def _panel() -> Panel:
    s = _state
    sc = {"passive": "dim", "listening": "green", "thinking": "yellow", "speaking": "cyan"}
    state_color = sc.get(s["pipeline_state"], "white")
    lines = [
        f"[bold]State:[/bold]  [{state_color}]{s['pipeline_state'].upper()}[/]",
        f"[bold]Wakes:[/bold]  {s['wake_count']}",
        "",
        f"[bold]Heard:[/bold]  [green]{s['last_heard']}[/]",
        f"[bold]Cosmo:[/bold]  [cyan]{s['last_response'][:80]}[/]",
        f"[dim]backend={s['backend']} | {s['latency_ms']}ms[/dim]",
        "",
        "[bold dim]Events:[/bold dim]",
    ]
    for e in s["events"][-6:]:
        lines.append(f"  [dim]{e}[/]")
    return Panel("\n".join(lines),
                 title="[bold cyan]Cosmo Audio Test[/bold cyan]",
                 subtitle="[dim]Say 'Hey Cosmo' | Ctrl+C to quit[/dim]",
                 border_style="cyan")


async def main() -> None:
    console.print("\n[bold cyan]Cosmo Audio Pipeline Test[/bold cyan]")
    console.print("[dim]Starting...[/dim]\n")

    await bus.start()
    await personality.start()

    # Warm up Ollama
    console.print("[dim]Warming up LLM...[/dim]")
    ready = await llm.is_ollama_ready()
    if ready:
        warmup = await llm.generate("Hi", conversation_history=[])
        if str(warmup.get("backend", "")).startswith("ollama"):
            console.print(f"[green]✓ Ollama ready ({warmup['latency_ms']}ms)[/green]")
        else:
            console.print("[yellow]⚠ Ollama slow — using Claude Haiku fallback[/yellow]")
    else:
        console.print("[yellow]⚠ No Ollama — using Claude Haiku[/yellow]")

    # Subscribe to pipeline events
    @bus.on(EventType.WAKE_WORD)
    async def on_wake(event: Event) -> None:
        _state["wake_count"] += 1
        _state["events"].append(f"Wake: '{event.data.get('word')}'")

    # Patch pipeline respond to capture output
    original_handle = audio_pipeline._handle_wake_word

    async def patched_handle(word: str) -> None:
        _state["events"].append(f"Listening after '{word}'...")
        await original_handle(word)
        _state["last_response"] = conversation._history[-1][1] if conversation._history else "—"

    audio_pipeline._handle_wake_word = patched_handle

    # Start pipeline
    ok = await audio_pipeline.start()
    if not ok:
        console.print("[red]✗ Mic not available[/red]")
        return

    console.print(f"[green]✓ Mic: {mic.device_name}[/green]")
    console.print(f"[green]✓ STT: Whisper {stt._model and 'tiny.en' or 'unavailable'}[/green]")
    console.print(f"[green]✓ Speaker: JBL Flip 5 (PipeWire)[/green]")
    console.print("\n[bold green]Say 'Hey Cosmo' to start![/bold green]\n")

    try:
        with Live(console=console, refresh_per_second=4, transient=True) as live:
            while True:
                _state["pipeline_state"] = audio_pipeline.state.value
                live.update(_panel())
                await asyncio.sleep(0.25)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        console.print("\n[dim]Shutting down...[/dim]")
        await audio_pipeline.stop()
        await personality.stop()
        await bus.stop()
        console.print("[dim]Done.[/dim]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
