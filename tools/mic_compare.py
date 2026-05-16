"""
Mic comparison tool — INMP441 I2S vs Logitech C920 webcam.
Run: python3 tools/mic_compare.py

Menu:
  [1] Level meter — INMP441
  [2] Level meter — C920
  [b] Both side-by-side (live)
  [r] Record 3s from both, play each back
  [s] SNR/noise floor test (record 3s silence → score both)
  [q] Quit
"""

import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pyaudio
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

console = Console()

RATE = 16000
CHUNK = 1024
XDG = f"XDG_RUNTIME_DIR=/run/user/{os.getuid()}"
BT_SINK = "bluez_output.28_FA_19_C1_73_F8.1"
BT_CARD = "bluez_card.28_FA_19_C1_73_F8"

MICS = {
    "INMP441": {
        "keyword": "inmp441",
        "channels": 2,
        "mono_ch": 0,   # left channel has audio, right is silent
        "color": "cyan",
    },
    "C920": {
        "keyword": "c920",
        "channels": 2,
        "mono_ch": None,  # average both channels
        "color": "magenta",
    },
}


# ── Device helpers ─────────────────────────────────────────────────────────

def find_device(pa: pyaudio.PyAudio, keyword: str) -> tuple[int, str]:
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d["maxInputChannels"] > 0 and keyword.lower() in d["name"].lower():
            return i, d["name"]
    return -1, "not found"


def to_mono(raw: bytes, channels: int, mono_ch) -> np.ndarray:
    arr = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels)
    if mono_ch is not None:
        return arr[:, mono_ch].astype(np.float32)
    return arr.mean(axis=1).astype(np.float32)


def rms_db(samples: np.ndarray) -> tuple[float, float]:
    rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
    db = 20 * np.log10(rms + 1e-9)
    return rms, db


def level_bar(db: float, width: int = 30, color: str = "green") -> str:
    filled = min(width, max(0, int((db + 80) / 80 * width)))
    bar_color = color if filled < int(width * 0.7) else "yellow" if filled < int(width * 0.9) else "red"
    return f"[{bar_color}]{'█' * filled}[/{bar_color}]{'░' * (width - filled)}"


def ensure_bt() -> None:
    subprocess.run(f"{XDG} pactl set-card-profile {BT_CARD} off",
                   shell=True, capture_output=True)
    time.sleep(0.4)
    subprocess.run(f"{XDG} pactl set-card-profile {BT_CARD} a2dp-sink",
                   shell=True, capture_output=True)
    time.sleep(1.2)


def play_wav(path: str) -> None:
    subprocess.run(f"{XDG} paplay {path}", shell=True)


# ── Actions ────────────────────────────────────────────────────────────────

def do_level_single(pa: pyaudio.PyAudio, name: str, duration: int = 8) -> None:
    cfg = MICS[name]
    idx, dev_name = find_device(pa, cfg["keyword"])
    if idx < 0:
        console.print(f"[red]{name} not found.[/red]")
        return

    stream = pa.open(format=pyaudio.paInt16, channels=cfg["channels"],
                     rate=RATE, input=True, input_device_index=idx,
                     frames_per_buffer=CHUNK)
    console.print(f"[{cfg['color']}]{name}[/{cfg['color']}] — {dev_name}")
    console.print(f"[dim]Live level {duration}s (Ctrl+C to stop)[/dim]\n")
    try:
        end = time.time() + duration
        with Live(console=console, refresh_per_second=20) as live:
            while time.time() < end:
                raw = stream.read(CHUNK, exception_on_overflow=False)
                samples = to_mono(raw, cfg["channels"], cfg["mono_ch"])
                rms, db = rms_db(samples)
                bar = level_bar(db, color=cfg["color"])
                live.update(Panel(
                    Text.from_markup(f"{bar}  RMS [bold]{rms:7.1f}[/bold]  [bold]{db:6.1f}[/bold] dBFS"),
                    title=f"[bold {cfg['color']}]{name}[/bold {cfg['color']}]",
                    border_style=cfg["color"],
                ))
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop_stream()
        stream.close()


def do_level_both(pa: pyaudio.PyAudio, duration: int = 10) -> None:
    streams = {}
    for name, cfg in MICS.items():
        idx, _ = find_device(pa, cfg["keyword"])
        if idx < 0:
            console.print(f"[red]{name} not found — skipping.[/red]")
            continue
        streams[name] = pa.open(
            format=pyaudio.paInt16, channels=cfg["channels"],
            rate=RATE, input=True, input_device_index=idx,
            frames_per_buffer=CHUNK,
        )

    if not streams:
        return

    console.print(f"[dim]Side-by-side level meter {duration}s (Ctrl+C to stop)[/dim]\n")
    try:
        end = time.time() + duration
        with Live(console=console, refresh_per_second=15) as live:
            while time.time() < end:
                panels = []
                for name, stream in streams.items():
                    cfg = MICS[name]
                    raw = stream.read(CHUNK, exception_on_overflow=False)
                    samples = to_mono(raw, cfg["channels"], cfg["mono_ch"])
                    rms, db = rms_db(samples)
                    bar = level_bar(db, width=25, color=cfg["color"])
                    panels.append(Panel(
                        Text.from_markup(
                            f"{bar}\n"
                            f"RMS  [bold]{rms:8.1f}[/bold]\n"
                            f"dBFS [bold]{db:8.1f}[/bold]"
                        ),
                        title=f"[bold {cfg['color']}]{name}[/bold {cfg['color']}]",
                        border_style=cfg["color"],
                    ))
                live.update(Columns(panels, equal=True))
    except KeyboardInterrupt:
        pass
    finally:
        for s in streams.values():
            s.stop_stream()
            s.close()


def do_record_compare(pa: pyaudio.PyAudio, seconds: int = 3) -> None:
    results = {}

    for name, cfg in MICS.items():
        idx, dev_name = find_device(pa, cfg["keyword"])
        if idx < 0:
            console.print(f"[red]{name} not found — skipping.[/red]")
            continue

        console.print(f"\n[bold {cfg['color']}]Recording {name}[/bold {cfg['color']}] ({dev_name}) — speak now...")
        stream = pa.open(format=pyaudio.paInt16, channels=cfg["channels"],
                         rate=RATE, input=True, input_device_index=idx,
                         frames_per_buffer=CHUNK)
        frames = []
        total = int(RATE / CHUNK * seconds)
        for i in range(total):
            raw = stream.read(CHUNK, exception_on_overflow=False)
            samples = to_mono(raw, cfg["channels"], cfg["mono_ch"])
            frames.append(samples.astype(np.int16).tobytes())
            pct = int((i + 1) / total * 30)
            console.print(f"  [cyan]{'█'*pct}{'░'*(30-pct)}[/cyan] {i+1}/{total}", end="\r")
        stream.stop_stream()
        stream.close()
        console.print()

        data = b"".join(frames)
        arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        rms, db = rms_db(arr)
        peak = int(np.abs(arr).max())

        f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(f.name, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(RATE)
            wf.writeframes(data)
        results[name] = {"path": f.name, "rms": rms, "db": db, "peak": peak, "cfg": cfg}

    # Summary table
    t = Table(title="Recording Stats", border_style="dim")
    t.add_column("Mic", style="bold")
    t.add_column("RMS", justify="right")
    t.add_column("dBFS", justify="right")
    t.add_column("Peak", justify="right")
    t.add_column("Signal quality")
    for name, r in results.items():
        quality = "[green]good[/green]" if r["rms"] > 500 else "[yellow]low[/yellow]" if r["rms"] > 100 else "[red]very low[/red]"
        t.add_row(name, f"{r['rms']:.0f}", f"{r['db']:.1f}", str(r["peak"]), quality)
    console.print(t)

    # Playback each
    ensure_bt()
    for name, r in results.items():
        console.print(f"\n[bold {r['cfg']['color']}]Playing back {name}...[/bold {r['cfg']['color']}]")
        input("  Press Enter to play →")
        play_wav(r["path"])
        os.unlink(r["path"])

    # Winner
    if len(results) == 2:
        names = list(results.keys())
        winner = max(results, key=lambda n: results[n]["rms"])
        loser = [n for n in names if n != winner][0]
        ratio = results[winner]["rms"] / max(results[loser]["rms"], 1)
        console.print(f"\n[bold green]{winner}[/bold green] is louder by {ratio:.1f}x RMS")


def do_noise_floor(pa: pyaudio.PyAudio, seconds: int = 3) -> None:
    console.print(f"\n[bold yellow]Noise floor test — stay SILENT for {seconds}s each[/bold yellow]")
    results = {}

    for name, cfg in MICS.items():
        idx, _ = find_device(pa, cfg["keyword"])
        if idx < 0:
            continue
        input(f"  Press Enter → recording {name} silence...")
        stream = pa.open(format=pyaudio.paInt16, channels=cfg["channels"],
                         rate=RATE, input=True, input_device_index=idx,
                         frames_per_buffer=CHUNK)
        frames = []
        for i in range(int(RATE / CHUNK * seconds)):
            raw = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(to_mono(raw, cfg["channels"], cfg["mono_ch"]))
            console.print(f"  [dim]{'█' * (i % 20)}[/dim]", end="\r")
        stream.stop_stream()
        stream.close()
        console.print()

        arr = np.concatenate(frames)
        rms, db = rms_db(arr)
        results[name] = {"rms": rms, "db": db, "cfg": cfg}

    t = Table(title="Noise Floor (lower = better)", border_style="dim")
    t.add_column("Mic", style="bold")
    t.add_column("Noise RMS", justify="right")
    t.add_column("Noise dBFS", justify="right")
    t.add_column("Rating")
    for name, r in results.items():
        rating = "[green]excellent[/green]" if r["db"] < -50 else "[yellow]ok[/yellow]" if r["db"] < -35 else "[red]noisy[/red]"
        t.add_row(name, f"{r['rms']:.1f}", f"{r['db']:.1f}", rating)
    console.print(t)

    if len(results) == 2:
        winner = min(results, key=lambda n: results[n]["rms"])
        console.print(f"\n[bold green]{winner}[/bold green] has lower noise floor")


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    pa = pyaudio.PyAudio()

    # Quick device check
    found = []
    for name, cfg in MICS.items():
        idx, dev = find_device(pa, cfg["keyword"])
        status = f"[green]idx {idx}[/green]" if idx >= 0 else "[red]NOT FOUND[/red]"
        found.append(f"  [{cfg['color']}]{name}[/{cfg['color']}]: {status}  {dev}")

    console.print(Panel(
        "[bold]Mic Comparison — INMP441 vs C920[/bold]\n\n"
        + "\n".join(found) + "\n\n"
        "[dim]1[/dim] INMP441 level  [dim]2[/dim] C920 level  [dim]b[/dim] both side-by-side\n"
        "[dim]r[/dim] record+playback compare  [dim]s[/dim] noise floor  [dim]q[/dim] quit",
        border_style="bold white",
    ))

    try:
        while True:
            choice = Prompt.ask("\n[bold]>[/bold]",
                                choices=["1", "2", "b", "r", "s", "q"],
                                show_choices=False)
            if choice == "1":
                do_level_single(pa, "INMP441")
            elif choice == "2":
                do_level_single(pa, "C920")
            elif choice == "b":
                do_level_both(pa)
            elif choice == "r":
                secs = Prompt.ask("Duration (seconds)", default="3")
                do_record_compare(pa, int(secs))
            elif choice == "s":
                do_noise_floor(pa)
            elif choice == "q":
                break
    except KeyboardInterrupt:
        pass
    finally:
        pa.terminate()
        console.print("\n[dim]Bye.[/dim]")


if __name__ == "__main__":
    main()
