#!/usr/bin/env python3
"""Speak a test phrase through Cosmo's configured voice engine."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from expression.speech import TTSEngine
from rich.console import Console

console = Console()


async def _run() -> None:
    parser = argparse.ArgumentParser(description="Test Cosmo voice synthesis")
    parser.add_argument("--name", help="use a consented cloned voice profile")
    parser.add_argument("--text", default="Hey Madhan, voice engine test is working.",
                        help="text to speak")
    parser.add_argument("--wait", type=float, default=8.0,
                        help="seconds to wait for background playback")
    args = parser.parse_args()

    tts = TTSEngine()
    if args.name:
        tts.set_voice_profile(args.name)
    console.print(f"[cyan]Speaking via active voice:[/cyan] {tts.active_voice}")
    await tts.speak(args.text)
    await asyncio.sleep(max(0.1, args.wait))
    console.print("[green]Done.[/green]")


if __name__ == "__main__":
    asyncio.run(_run())
