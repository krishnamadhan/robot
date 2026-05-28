#!/usr/bin/env python3
"""
Voice enrollment tool for Cosmo.

Records a reference audio clip of a person reading a paragraph,
then benchmarks XTTS v2 synthesis speed using the 3.11 venv subprocess.
Enrolled voice activates automatically when that person's face is recognized.

Usage:
    python3 tools/enroll_voice.py --name "Madhan"
    python3 tools/enroll_voice.py --name "Indhu"
    python3 tools/enroll_voice.py --name "Madhan" --play   # play test output
    python3 tools/enroll_voice.py --name "Madhan" --record-only  # skip benchmark
"""

import argparse
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

_VOICES_DIR  = Path.home() / ".robot/memory/voices"
_VENV_PYTHON = Path.home() / ".robot/venvs/xtts311/bin/python3"
_WORKER      = Path(__file__).parent / "xtts_worker.py"
_RATE        = 22050
_DURATION    = 30
_CHANNELS    = 1
_TEST_SENT   = "Hey, it's me! I'm Cosmo. I'm really happy to see you today."

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


def _xtts_ready() -> bool:
    return _VENV_PYTHON.exists() and _WORKER.exists()


def _countdown(n: int) -> None:
    for i in range(n, 0, -1):
        console.print(f"[bold yellow]{i}...[/bold yellow]", end="\r")
        time.sleep(1)
    console.print("[bold green]GO![/bold green]   ")


def _record(duration: int, rate: int):
    console.print(f"\n[bold red]● RECORDING[/bold red] — {duration}s — speak clearly")
    audio = sd.rec(int(duration * rate), samplerate=rate, channels=_CHANNELS, dtype="int16")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as prog:
        task = prog.add_task("Recording...", total=duration)
        start = time.monotonic()
        while time.monotonic() - start < duration:
            prog.update(task, completed=time.monotonic() - start)
            time.sleep(0.1)
    sd.wait()
    return audio


def _benchmark(ref_wav: Path, play: bool) -> float:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp = f.name

    console.print(f'\n[cyan]Synthesising:[/cyan] "[italic]{_TEST_SENT}[/italic]"')
    console.print("[dim](first run downloads ~1.8 GB XTTS model — be patient)[/dim]")

    try:
        result = subprocess.run(
            [str(_VENV_PYTHON), str(_WORKER), str(ref_wav), _TEST_SENT, tmp],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            console.print(f"[red]XTTS worker failed:[/red] {result.stderr[:200]}")
            return -1.0

        elapsed = float(result.stdout.strip().split(":")[-1])

        if play:
            console.print("[cyan]Playing test output...[/cyan]")
            subprocess.run(["paplay", tmp], env=_PW_ENV, check=False)

        return elapsed
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Enroll a voice profile for Cosmo")
    parser.add_argument("--name", required=True, help="Person's name (must match face enrolment)")
    parser.add_argument("--play", action="store_true", help="Play the synthesised test sentence")
    parser.add_argument("--record-only", action="store_true",
                        help="Just record — skip XTTS benchmark")
    args = parser.parse_args()

    name      = args.name.strip().title()
    voice_dir = _VOICES_DIR / name.lower()
    ref_wav   = voice_dir / "reference.wav"
    xtts_ok   = _xtts_ready()

    console.print(Panel(
        f"[bold]Cosmo Voice Enrolment — {name}[/bold]\n\n"
        f"XTTS venv: [{'green]✓ ready' if xtts_ok else 'red]✗ missing — run tools/setup_xtts_venv.sh'}[/{'green' if xtts_ok else 'red'}]\n"
        f"Output:    [dim]{ref_wav}[/dim]",
        title="Voice Enrolment",
        border_style="cyan",
    ))

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
    console.print(f"[green]✓ Saved {_DURATION}s reference clip → {ref_wav}[/green]")

    if args.record_only or not xtts_ok:
        if not xtts_ok:
            console.print("\n[yellow]Run [bold]bash tools/setup_xtts_venv.sh[/bold] to enable synthesis benchmark.[/yellow]")
        console.print("\n[bold green]Recording saved.[/bold green]")
        return

    synth_time = _benchmark(ref_wav, play=args.play)
    if synth_time < 0:
        return

    console.print(f"\n[bold]Results:[/bold]")
    if synth_time < 10:
        verdict, color = "Excellent — fast enough for conversation", "green"
    elif synth_time < 20:
        verdict, color = "Usable — noticeable pause, acceptable for Cosmo", "yellow"
    elif synth_time < 40:
        verdict, color = "Slow — novelty use, not fluid conversation", "yellow"
    else:
        verdict, color = "Too slow for real-time — consider Hailo accelerator", "red"

    console.print(f"  Synthesis time:  [{color}]{synth_time:.1f}s[/{color}]")
    console.print(f"  Chars/sec:       {len(_TEST_SENT) / synth_time:.1f}")
    console.print(f"  Verdict:         [{color}]{verdict}[/{color}]")
    console.print(f"\n[bold green]Enrolment complete![/bold green]")
    console.print(f"[dim]Cosmo will speak in {name}'s voice when their face is recognized.[/dim]")


if __name__ == "__main__":
    main()
