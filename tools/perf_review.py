"""
Performance review tool — runs checks and prints a report.
Also sends a WhatsApp summary if BanterAgent is running.

Usage:
  python3 tools/perf_review.py             # full report
  python3 tools/perf_review.py --send-wa   # also send to WhatsApp
  python3 tools/perf_review.py --watch     # live monitor (refresh every 30s)
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

BASE_URL = "http://localhost:8000"


def fetch(path: str) -> dict:
    try:
        r = requests.get(f"{BASE_URL}{path}", timeout=3)
        return r.json()
    except Exception:
        return {}


def fetch_latency() -> dict:
    try:
        r = requests.get(f"{BASE_URL}/latency", timeout=3)
        return r.json()
    except Exception:
        return {}


def check_cosmo_running() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def render_report() -> str:
    """Render full performance report. Returns plain text summary."""
    if not check_cosmo_running():
        console.print("[red]❌ Cosmo API not reachable — is cosmo running? (pm2 list)[/red]")
        return "OFFLINE"

    health = fetch("/health")
    hw = fetch("/hardware")
    state = fetch("/state")
    latency = fetch_latency()

    lines = []

    # ── Header ──────────────────────────────────────────────────────────────
    uptime_s = health.get("uptime_s", 0)
    h, m = divmod(uptime_s // 60, 60)
    temp = health.get("cpu_temp_c", -1)
    ram = health.get("free_ram_mb", -1)
    mood = health.get("mood", 0)
    energy = health.get("energy", 0)

    temp_color = "green" if temp < 70 else "yellow" if temp < 80 else "red"
    ram_color = "green" if ram > 1500 else "yellow" if ram > 800 else "red"

    console.print(Panel(
        f"[bold]Cosmo Performance Review[/bold]  —  {time.strftime('%Y-%m-%d %H:%M')}\n"
        f"Uptime: {h}h {m}m  │  "
        f"CPU Temp: [{temp_color}]{temp}°C[/{temp_color}]  │  "
        f"Free RAM: [{ram_color}]{ram}MB[/{ram_color}]  │  "
        f"Mood: {round(mood, 2)}  │  Energy: {round(energy, 2)}",
        style="bold blue"
    ))
    lines.append(f"Uptime {h}h{m}m | Temp {temp}°C | RAM {ram}MB | Mood {mood} | Energy {energy}")

    # ── Hardware Table ───────────────────────────────────────────────────────
    hw_table = Table(title="Hardware Status", box=box.SIMPLE)
    hw_table.add_column("Component", style="bold")
    hw_table.add_column("Status")
    hw_table.add_column("Reason")

    real = hw.get("real", [])
    mocked = hw.get("mocked", [])
    errors = hw.get("errors", [])

    for name, info in hw.get("components", {}).items():
        s = info.get("status", "?")
        r = info.get("reason", "")
        if s == "real":
            status_str = "[green]✅ real[/green]"
        elif s == "mock":
            status_str = "[yellow]⚠ mock[/yellow]"
        else:
            status_str = "[red]❌ error[/red]"
        hw_table.add_row(name, status_str, r[:70])

    console.print(hw_table)
    lines.append(f"Real: {', '.join(real)} | Mocked: {', '.join(mocked)} | Errors: {', '.join(errors)}")

    # ── Latency Table ────────────────────────────────────────────────────────
    lat_data = latency.get("data", {})
    if lat_data:
        lat_table = Table(title="Latency (ms)", box=box.SIMPLE)
        lat_table.add_column("Metric")
        lat_table.add_column("p50")
        lat_table.add_column("p95")
        lat_table.add_column("p99")

        for metric, vals in lat_data.items():
            p50 = vals.get("p50", "?")
            p95 = vals.get("p95", "?")
            p99 = vals.get("p99", "?")
            color = "green" if isinstance(p95, (int, float)) and p95 < 500 else "yellow"
            lat_table.add_row(metric, str(p50), f"[{color}]{p95}[/{color}]", str(p99))

        console.print(lat_table)

    # ── Behavior State ───────────────────────────────────────────────────────
    behavior = state.get("behavior", {})
    attention = state.get("attention", {})
    console.print(f"\n[bold]Current state:[/bold]")
    console.print(f"  Listen: {behavior.get('listen_state', '?')}  │  "
                 f"Nav: {behavior.get('nav_state', '?')}  │  "
                 f"Eyes: {behavior.get('eye_expression', '?')}")
    console.print(f"  Person: {attention.get('person', 'no one')}  │  "
                 f"Emotion: {attention.get('emotion', '?')}  │  "
                 f"Distance: {attention.get('distance_cm', '?')}cm")
    console.print(f"  Last response: {str(behavior.get('last_response', '—'))[:80]}")

    # ── Warnings ─────────────────────────────────────────────────────────────
    warnings = []
    if temp > 80:
        warnings.append(f"🌡 CPU too hot: {temp}°C (throttling likely)")
    if ram < 800:
        warnings.append(f"🧠 Low RAM: {ram}MB (may cause crashes)")
    if errors:
        warnings.append(f"❌ Hardware errors: {', '.join(errors)}")
    if mocked:
        warnings.append(f"⚠ Mocked hardware: {', '.join(mocked)}")

    if warnings:
        console.print("\n[yellow bold]Warnings:[/yellow bold]")
        for w in warnings:
            console.print(f"  {w}")
        lines.append(" | ".join(warnings))

    return "\n".join(lines)


async def send_to_whatsapp(summary: str) -> None:
    """Send performance summary to WhatsApp via BanterAgent."""
    import os
    group_id = os.environ.get("BOT_GROUP_ID", "")
    if not group_id:
        console.print("[yellow]⚠ BOT_GROUP_ID not set, skipping WhatsApp[/yellow]")
        return
    try:
        r = requests.post(
            "http://localhost:3099/send-message",
            json={"groupId": group_id, "message": f"🤖 *Cosmo Perf Review*\n{summary}"},
            timeout=5,
        )
        if r.status_code == 200:
            console.print("[green]✅ Sent to WhatsApp[/green]")
        else:
            console.print(f"[yellow]⚠ WhatsApp send failed: {r.status_code}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]⚠ WhatsApp error: {e}[/yellow]")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-wa", action="store_true", help="Send summary to WhatsApp")
    parser.add_argument("--watch", action="store_true", help="Live monitor (refresh 30s)")
    args = parser.parse_args()

    if args.watch:
        try:
            while True:
                console.clear()
                summary = render_report()
                console.print(f"\n[dim]Refreshing in 30s... Ctrl+C to exit[/dim]")
                time.sleep(30)
        except KeyboardInterrupt:
            pass
    else:
        summary = render_report()
        if args.send_wa:
            asyncio.run(send_to_whatsapp(summary))


if __name__ == "__main__":
    main()
