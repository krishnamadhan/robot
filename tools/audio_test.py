"""
Interactive INMP441 mic + BT speaker test tool.
Run: python3 tools/audio_test.py

Menu:
  [r] Record 3s and play back
  [l] Live level meter (see mic is working)
  [d] Device info
  [v] Set playback volume
  [q] Quit
"""

import asyncio
import os
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pyaudio
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

console = Console()

# ── Constants ──────────────────────────────────────────────────────────────
RATE = 16000
CHUNK = 512
CHANNELS = 2          # INMP441 requires stereo; audio is on left ch only
MIC_DEVICE_KW = "inmp441"
BT_SINK = "bluez_output.28_FA_19_C1_73_F8.1"
BT_CARD = "bluez_card.28_FA_19_C1_73_F8"
XDG = f"XDG_RUNTIME_DIR=/run/user/{os.getuid()}"


# ── Helpers ────────────────────────────────────────────────────────────────

def _find_mic(pa: pyaudio.PyAudio) -> tuple[int, str]:
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d["maxInputChannels"] > 0 and MIC_DEVICE_KW in d["name"].lower():
            return i, d["name"]
    return -1, "not found"


def _ensure_bt_active() -> bool:
    """Cycle BT card profile to force A2DP connection."""
    r1 = subprocess.run(
        f"{XDG} pactl set-card-profile {BT_CARD} off",
        shell=True, capture_output=True
    )
    time.sleep(0.5)
    r2 = subprocess.run(
        f"{XDG} pactl set-card-profile {BT_CARD} a2dp-sink",
        shell=True, capture_output=True
    )
    time.sleep(1.5)
    return r1.returncode == 0 and r2.returncode == 0


def _bt_sink_state() -> str:
    r = subprocess.run(
        f"{XDG} pactl list sinks short", shell=True, capture_output=True, text=True
    )
    for line in r.stdout.splitlines():
        if BT_SINK in line:
            parts = line.split()
            return parts[-1] if parts else "unknown"
    return "not found"


def _play(path: str) -> int:
    return subprocess.run(
        f"{XDG} paplay {path}", shell=True
    ).returncode


def _record_raw(pa: pyaudio.PyAudio, device_index: int, seconds: int) -> bytes:
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        input_device_index=device_index,
        frames_per_buffer=CHUNK,
    )
    frames = []
    total = int(RATE / CHUNK * seconds)
    for i in range(total):
        raw = stream.read(CHUNK, exception_on_overflow=False)
        arr = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2)
        frames.append(arr[:, 0].tobytes())  # left channel only
        pct = int((i + 1) / total * 30)
        bar = "█" * pct + "░" * (30 - pct)
        console.print(f"  [cyan]REC[/cyan] [{bar}] {i+1}/{total}", end="\r")
    stream.stop_stream()
    stream.close()
    console.print()
    return b"".join(frames)


def _save_wav(data: bytes, path: str, rate: int = RATE) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data)


def _rms(data: bytes) -> float:
    arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(arr ** 2))) if len(arr) else 0.0


# ── Menu actions ───────────────────────────────────────────────────────────

def do_device_info(pa: pyaudio.PyAudio) -> None:
    mic_idx, mic_name = _find_mic(pa)
    bt_state = _bt_sink_state()

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="bold")
    t.add_column()
    t.add_row("Mic index",   str(mic_idx))
    t.add_row("Mic name",    mic_name)
    t.add_row("Sample rate", f"{RATE} Hz")
    t.add_row("Capture ch",  f"{CHANNELS} (left=audio, right=silent)")
    t.add_row("BT sink",     BT_SINK)
    t.add_row("BT state",    f"[green]{bt_state}[/green]" if bt_state == "IDLE"
              else f"[yellow]{bt_state}[/yellow]")
    console.print(Panel(t, title="[bold]Device Info[/bold]", border_style="cyan"))


def do_record_playback(pa: pyaudio.PyAudio, seconds: int = 3) -> None:
    mic_idx, mic_name = _find_mic(pa)
    if mic_idx < 0:
        console.print("[red]Mic not found.[/red]")
        return

    console.print(f"\n[bold cyan]Recording {seconds}s[/bold cyan] — speak now...")
    data = _record_raw(pa, mic_idx, seconds)
    rms = _rms(data)
    peak = np.abs(np.frombuffer(data, dtype=np.int16)).max()
    console.print(f"  RMS: [yellow]{rms:.1f}[/yellow]  Peak: [yellow]{peak}[/yellow]")

    if rms < 50:
        console.print("[red]  Warning: very low signal — check mic wiring.[/red]")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    _save_wav(data, path)

    state = _bt_sink_state()
    if state == "SUSPENDED":
        console.print("[yellow]  BT suspended — reconnecting...[/yellow]")
        _ensure_bt_active()

    console.print("[bold green]Playing back...[/bold green]")
    rc = _play(path)
    if rc != 0:
        console.print(f"[red]  paplay failed (exit {rc})[/red]")
    else:
        console.print("[green]  Done.[/green]")

    os.unlink(path)


def do_level_meter(pa: pyaudio.PyAudio, duration: int = 10) -> None:
    mic_idx, _ = _find_mic(pa)
    if mic_idx < 0:
        console.print("[red]Mic not found.[/red]")
        return

    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        input_device_index=mic_idx,
        frames_per_buffer=CHUNK,
    )

    console.print(f"[cyan]Live level meter — {duration}s (Ctrl+C to stop early)[/cyan]\n")
    try:
        end = time.time() + duration
        with Live(console=console, refresh_per_second=15) as live:
            while time.time() < end:
                raw = stream.read(CHUNK, exception_on_overflow=False)
                arr = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2)
                left = arr[:, 0].astype(np.float32)
                rms = float(np.sqrt(np.mean(left ** 2)))
                db = 20 * np.log10(rms + 1e-9)

                width = 40
                filled = min(width, max(0, int((db + 80) / 80 * width)))
                color = "green" if filled < 28 else "yellow" if filled < 36 else "red"
                bar = f"[{color}]{'█' * filled}[/{color}]{'░' * (width - filled)}"
                label = f"  RMS [bold]{rms:7.1f}[/bold]  {db:6.1f} dBFS"
                live.update(Panel(Text.from_markup(f"{bar}  {label}"),
                                  title="[bold]Mic Level — INMP441[/bold]",
                                  border_style=color))
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop_stream()
        stream.close()
    console.print("\n[dim]Level meter stopped.[/dim]")


def do_set_volume() -> None:
    vol = Prompt.ask("Volume %", default="85")
    r = subprocess.run(
        f"{XDG} pactl set-sink-volume {BT_SINK} {vol}%",
        shell=True, capture_output=True
    )
    if r.returncode == 0:
        console.print(f"[green]Volume set to {vol}%[/green]")
    else:
        console.print(f"[red]Failed: {r.stderr.decode().strip()}[/red]")


# ── Main loop ──────────────────────────────────────────────────────────────

def main() -> None:
    pa = pyaudio.PyAudio()

    console.print(Panel(
        "[bold cyan]INMP441 Mic + JBL Speaker — Interactive Test[/bold cyan]\n"
        "[dim]r[/dim] record+play  [dim]l[/dim] level meter  "
        "[dim]d[/dim] device info  [dim]v[/dim] volume  [dim]q[/dim] quit",
        border_style="cyan",
    ))

    try:
        while True:
            choice = Prompt.ask("\n[bold]>[/bold]",
                                choices=["r", "l", "d", "v", "q"],
                                show_choices=False)
            if choice == "r":
                secs = Prompt.ask("Duration (seconds)", default="3")
                do_record_playback(pa, int(secs))
            elif choice == "l":
                do_level_meter(pa)
            elif choice == "d":
                do_device_info(pa)
            elif choice == "v":
                do_set_volume()
            elif choice == "q":
                break
    except KeyboardInterrupt:
        pass
    finally:
        pa.terminate()
        console.print("\n[dim]Bye.[/dim]")


if __name__ == "__main__":
    main()
