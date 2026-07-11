#!/usr/bin/env python3
"""
Force the remote Voicebox path to fail and verify Piper fallback speaks.

This does not require the real Voicebox server. It uses VOICEBOX_URL pointed at
a closed local port, then activates the requested consented profile.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from expression.speech import TTSEngine
from rich.console import Console

console = Console()


async def _run() -> None:
    parser = argparse.ArgumentParser(description="Verify Voicebox failure falls back to Piper")
    parser.add_argument("--name", required=True,
                        help="consented profile name to activate before forcing fallback")
    parser.add_argument("--text", default="Piper fallback test. I should still speak.",
                        help="text to speak")
    parser.add_argument("--wait", type=float, default=8.0,
                        help="seconds to wait for background playback")
    args = parser.parse_args()

    os.environ["VOICEBOX_URL"] = "http://127.0.0.1:9"
    os.environ["VOICEBOX_TIMEOUT_S"] = "0.5"

    tts = TTSEngine()
    tts.set_voice_profile(args.name)
    if tts.active_voice == "piper":
        console.print("[yellow]Profile missing or unconsented; still testing Piper directly.[/yellow]")
    else:
        console.print(f"[cyan]Remote path armed for {tts.active_voice}; failure is expected.[/cyan]")

    await tts.speak(args.text)
    await asyncio.sleep(max(0.1, args.wait))
    console.print("[green]If audio played, fallback is working.[/green]")


if __name__ == "__main__":
    asyncio.run(_run())
