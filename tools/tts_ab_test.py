"""
ADR-016 quality gate — Piper lessac-medium vs KittenTTS nano, side by side.

Synthesizes the same 10 real Cosmo phrases (greetings from personality.yaml)
with both engines, measures synth latency + real-time factor, and writes WAVs
to ~/.robot/tts_ab/ for human listening.

KittenTTS runs from its own venv (kittentts is NOT installed system-wide):
    ~/.robot/venvs/kitten-test/bin/python tools/tts_ab_test.py
Interactive playback:  add --play  (plays each pair through the speaker)
"""

import argparse
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

PIPER_BIN = Path("/usr/local/bin/piper")
PIPER_MODEL = Path.home() / ".robot/models/piper/en_US-lessac-medium.onnx"
OUT_DIR = Path.home() / ".robot/tts_ab"
KITTEN_VOICE = "expr-voice-2-f"   # closest playful register; try -m variants too

console = Console()


def cosmo_phrases() -> list[str]:
    import yaml
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config/personality.yaml").read_text())
    phrases = []
    for person in cfg["personality"]["known_persons"].values():
        phrases.extend(person["greetings"])
    return phrases[:10]


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def synth_piper(text: str, out: Path) -> float:
    t0 = time.monotonic()
    subprocess.run(
        [str(PIPER_BIN), "--model", str(PIPER_MODEL), "--output_file", str(out)],
        input=text.encode(), capture_output=True, timeout=30, check=True,
    )
    return time.monotonic() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--play", action="store_true", help="play each pair via aplay")
    ap.add_argument("--voice", default=KITTEN_VOICE, help="kitten voice id")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from kittentts import KittenTTS
    except ImportError:
        console.print("[red]kittentts not importable — run with "
                      "~/.robot/venvs/kitten-test/bin/python[/red]")
        sys.exit(1)

    import soundfile as sf

    console.print("[cyan]Loading KittenTTS (downloads ~25MB model on first run)...[/cyan]")
    t0 = time.monotonic()
    kitten = KittenTTS("KittenML/kitten-tts-nano-0.2")
    console.print(f"KittenTTS ready in {time.monotonic() - t0:.1f}s, voice={args.voice}")

    table = Table(title="ADR-016 — Piper lessac-medium vs KittenTTS nano (Pi 5)")
    for col in ("#", "Phrase", "Piper s", "Piper RTF", "Kitten s", "Kitten RTF"):
        table.add_column(col)

    totals = {"piper": 0.0, "kitten": 0.0}
    for i, text in enumerate(cosmo_phrases(), 1):
        p_wav = OUT_DIR / f"{i:02d}_piper.wav"
        k_wav = OUT_DIR / f"{i:02d}_kitten.wav"

        p_lat = synth_piper(text, p_wav)
        p_rtf = p_lat / wav_duration(p_wav)

        t0 = time.monotonic()
        audio = kitten.generate(text, voice=args.voice)
        k_lat = time.monotonic() - t0
        sf.write(str(k_wav), audio, 24000)
        k_rtf = k_lat / wav_duration(k_wav)

        totals["piper"] += p_lat
        totals["kitten"] += k_lat
        table.add_row(str(i), text[:38], f"{p_lat:.2f}", f"{p_rtf:.2f}",
                      f"{k_lat:.2f}", f"{k_rtf:.2f}")

        if args.play:
            console.print(f"[bold]{i}. {text}[/bold]  — Piper...")
            subprocess.run(["aplay", "-q", str(p_wav)])
            console.print("   ...Kitten")
            subprocess.run(["aplay", "-q", str(k_wav)])

    console.print(table)
    console.print(f"Total synth: piper={totals['piper']:.1f}s  kitten={totals['kitten']:.1f}s")
    console.print(f"WAVs in {OUT_DIR} — listen side by side, then decide per ADR-016.")


if __name__ == "__main__":
    main()
