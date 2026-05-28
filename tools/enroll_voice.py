#!/usr/bin/env python3
"""
Voice enrollment tool for Cosmo.

Records a reference audio clip of a person reading a paragraph,
then benchmarks XTTS v2 synthesis speed with that clip.
Enrolled voice activates automatically when that person's face is recognized.

Usage:
    python3 tools/enroll_voice.py --name "Madhan"
    python3 tools/enroll_voice.py --name "Indhu"
    python3 tools/enroll_voice.py --name "Madhan" --play   # play test output
"""

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sounddevice as sd
import soundfile as sf
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text

console = Console()

_VOICES_DIR   = Path.home() / ".robot/memory/voices"
_RATE         = 22050
_DURATION     = 30       # seconds to record
_CHANNELS     = 1
_TEST_SENTENCE = "Hey, it's me! I'm Cosmo. I'm really happy to see you today."

# Phonetically balanced passage — covers all English phonemes
_PARAGRAPH = """\
Please call Stella. Ask her to bring these things with her from the store:
six spoons of fresh snow peas, five thick slabs of blue cheese, and maybe
a snack for her brother Bob. We also need a small plastic snake and a big
toy frog for the babies. Look at her, she has a way with people that just
makes everyone around her smile. She walks quickly and speaks clearly,
and laughs at the right moments — a truly wonderful person to know.\
"""

_PW_ENV = {
    **os.environ,
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
}


def _check_xtts() -> bool:
    try:
        from TTS.api import TTS  # noqa: F401
        return True
    except ImportError:
        return False


def _record(duration: int, rate: int) -> "np.ndarray":
    import numpy as np
    console.print(f"\n[bold red]● RECORDING[/bold red] — {duration}s")
    audio = sd.rec(int(duration * rate), samplerate=rate, channels=_CHANNELS, dtype="int16")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as prog:
        task = prog.add_task(f"Recording... speak clearly into the mic", total=duration)
        start = time.monotonic()
        while time.monotonic() - start < duration:
            elapsed = time.monotonic() - start
            prog.update(task, completed=elapsed)
            time.sleep(0.1)
    sd.wait()
    return audio


def _countdown(n: int) -> None:
    for i in range(n, 0, -1):
        console.print(f"[bold yellow]{i}...[/bold yellow]", end="\r")
        time.sleep(1)
    console.print("[bold green]GO![/bold green]   ")


def _benchmark_xtts(ref_wav: Path, play: bool) -> float:
    console.print("\n[cyan]Loading XTTS v2 model (first run downloads ~1.8 GB)...[/cyan]")
    from TTS.api import TTS

    t0 = time.monotonic()
    xtts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False)
    load_time = time.monotonic() - t0
    console.print(f"[dim]Model loaded in {load_time:.1f}s[/dim]")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        out_path = f.name

    console.print(f'[cyan]Synthesising test sentence:[/cyan] "[italic]{_TEST_SENTENCE}[/italic]"')
    t0 = time.monotonic()
    xtts.tts_to_file(
        text=_TEST_SENTENCE,
        speaker_wav=str(ref_wav),
        language="en",
        file_path=out_path,
    )
    synth_time = time.monotonic() - t0

    if play:
        console.print("[cyan]Playing test output...[/cyan]")
        subprocess.run(["paplay", out_path], env=_PW_ENV, check=False)

    os.unlink(out_path)
    return synth_time


def main() -> None:
    parser = argparse.ArgumentParser(description="Enroll a voice profile for Cosmo")
    parser.add_argument("--name", required=True, help="Person's name (must match face enrolment)")
    parser.add_argument("--play", action="store_true", help="Play the synthesised test sentence after benchmark")
    args = parser.parse_args()

    name = args.name.strip().title()
    voice_dir = _VOICES_DIR / name.lower()
    ref_wav   = voice_dir / "reference.wav"

    xtts_ok = _check_xtts()

    console.print(Panel(
        f"[bold]Cosmo Voice Enrolment — {name}[/bold]\n\n"
        f"XTTS v2 available: [{'green]✓' if xtts_ok else 'red]✗ — install coqui-tts first'}[/{'green' if xtts_ok else 'red'}]\n"
        f"Output: [dim]{ref_wav}[/dim]",
        title="Voice Enrolment",
        border_style="cyan",
    ))

    if not xtts_ok:
        console.print("[red]Install TTS first: pip install --break-system-packages coqui-tts[all][/red]")
        sys.exit(1)

    # Show the passage
    console.print(Panel(
        Text(_PARAGRAPH, style="bold white"),
        title="[yellow]Read this aloud — naturally, at a normal pace[/yellow]",
        border_style="yellow",
        padding=(1, 2),
    ))
    console.print("[dim]Tip: speak clearly, no background music, mic ~30–50 cm away[/dim]")
    console.print("\nPress [bold]Enter[/bold] when ready...")
    input()

    _countdown(3)
    audio = _record(_DURATION, _RATE)

    voice_dir.mkdir(parents=True, exist_ok=True)
    sf.write(str(ref_wav), audio, _RATE)
    duration_s = len(audio) / _RATE
    console.print(f"[green]✓ Saved {duration_s:.1f}s reference clip → {ref_wav}[/green]")

    # Benchmark synthesis
    synth_time = _benchmark_xtts(ref_wav, play=args.play)

    console.print(f"\n[bold]Results:[/bold]")
    console.print(f"  Synthesis time: [{'green' if synth_time < 15 else 'yellow' if synth_time < 30 else 'red'}]{synth_time:.1f}s[/{'green' if synth_time < 15 else 'yellow' if synth_time < 30 else 'red'}]")
    console.print(f"  Characters:     {len(_TEST_SENTENCE)}")
    console.print(f"  Chars/sec:      {len(_TEST_SENTENCE) / synth_time:.1f}")

    if synth_time < 10:
        verdict = "[green]Excellent — fast enough for conversation[/green]"
    elif synth_time < 20:
        verdict = "[yellow]Usable — noticeable delay but acceptable for Cosmo[/yellow]"
    elif synth_time < 35:
        verdict = "[yellow]Slow — works as novelty, not for fluid conversation[/yellow]"
    else:
        verdict = "[red]Too slow for real-time use on this Pi[/red]"

    console.print(f"\n  Verdict: {verdict}")
    console.print(f"\n[bold green]Enrolment complete![/bold green]")
    console.print(f"[dim]Cosmo will speak in {name}'s voice when their face is recognized.[/dim]")
    console.print(f"[dim]Run with --play to hear the test output.[/dim]")


if __name__ == "__main__":
    main()
