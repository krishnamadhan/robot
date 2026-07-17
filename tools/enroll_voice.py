#!/usr/bin/env python3
"""
Consent-gated voice enrollment for Cosmo.

This stores only local enrollment artifacts:
  ~/.robot/memory/voices/<name>/reference.wav
  ~/.robot/memory/voices/<name>/consent.json

Remote Voicebox synthesis is optional and controlled separately with VOICEBOX_URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sounddevice as sd
import soundfile as sf
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text

from expression.voice_engine import RemoteVoiceboxEngine, voice_profile_path

console = Console()

RATE = 22050
DURATION_S = 30
CHANNELS = 1
CONSENT_PHRASE = "I consent to Cosmo cloning my voice for local lab use"

PARAGRAPH = """\
Please call Stella. Ask her to bring these things with her from the store:
six spoons of fresh snow peas, five thick slabs of blue cheese, and maybe
a snack for her brother Bob. We also need a small plastic snake and a big
toy frog for the babies. Look at her, she has a way with people that just
makes everyone around her smile. She walks quickly and speaks clearly,
and laughs at the right moments.
"""


def _countdown(n: int) -> None:
    for i in range(n, 0, -1):
        console.print(f"[bold yellow]{i}...[/bold yellow]", end="\r")
        time.sleep(1)
    console.print("[bold green]GO![/bold green]   ")


def _record(duration_s: int) -> object:
    console.print(f"\n[bold red]RECORDING[/bold red] — {duration_s}s — speak clearly")
    audio = sd.rec(int(duration_s * RATE), samplerate=RATE, channels=CHANNELS, dtype="int16")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as prog:
        task = prog.add_task("Recording...", total=duration_s)
        start = time.monotonic()
        while time.monotonic() - start < duration_s:
            prog.update(task, completed=time.monotonic() - start)
            time.sleep(0.1)
    sd.wait()
    return audio


def _write_consent(path: Path, *, name: str, spoken_consent: str,
                   transcribed_consent: str | None) -> None:
    data = {
        "name": name,
        "consent_granted": True,
        "consent_phrase": CONSENT_PHRASE,
        "spoken_consent": spoken_consent,
        "transcribed_consent": transcribed_consent,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Cosmo local lab voice cloning only",
        "revocation": "Delete this voice directory to disable cloned synthesis.",
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _optional_transcribe(path: Path, base_url: str | None) -> str | None:
    if not base_url:
        return None
    try:
        engine = RemoteVoiceboxEngine(base_url, timeout_s=10)
        return engine.transcribe(path.read_bytes(), sample_rate=RATE, encoding="wav")
    except Exception as exc:
        console.print(f"[yellow]Voicebox /transcribe check skipped: {exc}[/yellow]")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Enroll a consented voice profile for Cosmo")
    parser.add_argument("--name", required=True, help="Person's name; should match face enrollment")
    parser.add_argument("--duration", type=int, default=DURATION_S,
                        help=f"reference recording duration in seconds (default {DURATION_S})")
    parser.add_argument("--voicebox-url", default=os.getenv("VOICEBOX_URL"),
                        help="optional URL for a /transcribe consent check")
    args = parser.parse_args()

    name = args.name.strip().title()
    if not name:
        raise SystemExit("--name cannot be empty")

    root = voice_profile_path(name)
    ref_wav = root / "reference.wav"
    consent_json = root / "consent.json"

    console.print(Panel(
        f"[bold]Cosmo Voice Enrollment — {name}[/bold]\n\n"
        f"Output: [dim]{root}[/dim]\n"
        f"Remote transcribe: {'enabled' if args.voicebox_url else 'disabled'}",
        title="Voice Enrollment",
        border_style="cyan",
    ))

    console.print(Panel(
        Text(CONSENT_PHRASE, style="bold white"),
        title="[yellow]Type this exact consent phrase to continue[/yellow]",
        border_style="yellow",
        padding=(1, 2),
    ))
    typed = input("> ").strip()
    if typed != CONSENT_PHRASE:
        console.print("[red]Consent phrase did not match. No recording was saved.[/red]")
        raise SystemExit(2)

    console.print(Panel(
        Text(PARAGRAPH, style="bold white"),
        title="[yellow]Read this aloud naturally after the countdown[/yellow]",
        border_style="yellow",
        padding=(1, 2),
    ))
    input("Press Enter when ready...")

    _countdown(3)
    audio = _record(max(5, args.duration))

    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    sf.write(str(ref_wav), audio, RATE)
    transcript = _optional_transcribe(ref_wav, args.voicebox_url)
    _write_consent(consent_json, name=name, spoken_consent=typed,
                   transcribed_consent=transcript)

    console.print(f"[green]Saved reference audio:[/green] {ref_wav}")
    console.print(f"[green]Saved consent metadata:[/green] {consent_json}")
    if transcript:
        console.print(f"[dim]Voicebox transcript: {transcript[:120]}[/dim]")
    console.print("[bold green]Enrollment complete.[/bold green]")


if __name__ == "__main__":
    main()
