#!/usr/bin/env python3
"""
Memory browser — browse Cosmo's episodic memories.

Usage:
  python3 tools/memory_browser.py

Commands:
  list [n]           List last n memories (default 20)
  show <id>          Show full memory record by UUID prefix
  person <name/id>   Memories involving a person
  type <type>        Memories of a given episode_type
  positive           Positive memories (valence > 0.3)
  negative           Negative memories (valence < -0.3)
  delete <id>        Delete a memory by ID (not yet implemented)
  stats              Memory statistics
  persons            List known persons
  help               Show this help
  q / quit           Exit
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.memory.episodic import episodic, Episode
from utils.logger import get_logger

log = get_logger(__name__)


def _fmt_episode(ep) -> str:
    if isinstance(ep, dict):
        ts    = ep.get("timestamp", 0)
        pid   = ep.get("person_id", "—") or "—"
        val   = ep.get("emotional_valence", 0)
        summ  = ep.get("summary", "")[:70]
        eid   = ep.get("id", "?")[:8]
    else:
        ts    = getattr(ep, "timestamp", 0)
        pid   = getattr(ep, "person_id", None) or "—"
        val   = getattr(ep, "emotional_valence", 0)
        summ  = getattr(ep, "summary", "")[:70]
        eid   = getattr(ep, "id", "?")[:8]
    ts_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    v_char = "+" if val > 0.1 else ("-" if val < -0.1 else " ")
    return f"[{eid}] {ts_str}  {pid:<14} [{v_char}{abs(val):.1f}]  {summ}"


async def cmd_list(args):
    n = int(args[0]) if args else 20
    episodes = await episodic.retrieve(limit=n)
    if not episodes:
        print("  (no memories stored)")
        return
    print(f"\n  {'ID':>8}  {'Timestamp':<16}  {'Person':<14}  Valence  Summary")
    print("  " + "─" * 80)
    for ep in episodes:
        print("  " + _fmt_episode(ep))
    print(f"\n  {len(episodes)} memories shown\n")


async def cmd_show(args):
    if not args:
        print("  Usage: show <id-prefix>")
        return
    eid = args[0]
    ep = await episodic.get_by_id(eid)
    if not ep:
        print(f"  No memory with ID starting '{eid}'")
        return
    print()
    for attr in ["id", "timestamp", "episode_type", "person_id", "room_id",
                 "emotional_valence", "importance", "summary"]:
        val = getattr(ep, attr, "—")
        if attr == "timestamp" and isinstance(val, float):
            val = f"{val:.0f} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(val))})"
        print(f"  {attr:<20}: {val}")
    if hasattr(ep, "raw_data") and ep.raw_data:
        print(f"  {'raw_data':<20}: {json.dumps(ep.raw_data, indent=2)}")
    print()


async def cmd_person(args):
    if not args:
        print("  Usage: person <person_id or name>")
        return
    pid = " ".join(args)
    episodes = await episodic.retrieve(person_id=pid, limit=30)
    if not episodes:
        print(f"  No memories for person: {pid!r}")
        return
    print(f"\n  Memories involving '{pid}':")
    print("  " + "─" * 70)
    for ep in episodes:
        print("  " + _fmt_episode(ep))
    print()


async def cmd_type(args):
    if not args:
        print("  Usage: type <episode_type>")
        return
    etype = "_".join(args)
    episodes = await episodic.retrieve(episode_type=etype, limit=20)
    if not episodes:
        print(f"  No memories of type: {etype!r}")
        return
    print(f"\n  Memories of type '{etype}':")
    print("  " + "─" * 70)
    for ep in episodes:
        print("  " + _fmt_episode(ep))
    print()


async def cmd_positive(args):
    episodes = await episodic.retrieve(min_valence=0.3, limit=20)
    if not episodes:
        print("  No positive memories found")
        return
    print("\n  Positive memories (valence > 0.3):")
    print("  " + "─" * 70)
    for ep in episodes:
        print("  " + _fmt_episode(ep))
    print()


async def cmd_negative(args):
    episodes = await episodic.retrieve(max_valence=-0.3, limit=20)
    if not episodes:
        print("  No negative memories found")
        return
    print("\n  Negative memories (valence < -0.3):")
    print("  " + "─" * 70)
    for ep in episodes:
        print("  " + _fmt_episode(ep))
    print()


async def cmd_stats(args):
    stats = await episodic.stats()
    print("\n  Memory Statistics")
    print("  " + "─" * 40)
    for k, v in stats.items():
        print(f"  {k:<30}: {v}")
    print()


async def cmd_persons(args):
    persons = await episodic.list_persons()
    if not persons:
        print("  No persons in memory")
        return
    print(f"\n  {'ID':<20}  {'Name':<12}  {'Quality':>7}  {'Count':>5}  Last seen")
    print("  " + "─" * 65)
    for p in persons:
        pid   = p.get("id", "?")
        name  = p.get("name") or "?"
        qual  = p.get("relationship_quality", 0.5)
        count = p.get("interaction_count", 0)
        last  = p.get("last_seen")
        last_str = time.strftime("%m-%d %H:%M", time.localtime(last)) if last else "never"
        print(f"  {pid:<20}  {name:<12}  {qual:>7.2f}  {count:>5}  {last_str}")
    print()


async def run() -> None:
    print("\n  Cosmo Memory Browser")
    print("  Type 'help' for commands, 'q' to quit\n")

    try:
        await episodic.initialize()
    except Exception as e:
        print(f"  ERROR: Could not initialize episodic memory: {e}")
        return

    commands = {
        "list":     cmd_list,
        "show":     cmd_show,
        "person":   cmd_person,
        "type":     cmd_type,
        "positive": cmd_positive,
        "negative": cmd_negative,
        "stats":    cmd_stats,
        "persons":  cmd_persons,
    }

    while True:
        try:
            line = input("  memory> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line.lower() in ("q", "quit", "exit"):
            break
        if line.lower() == "help":
            print(__doc__)
            continue

        parts = line.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in commands:
            try:
                await commands[cmd](args)
            except Exception as e:
                print(f"  Error: {e}")
        else:
            print(f"  Unknown command: {cmd!r}  (type 'help')")


if __name__ == "__main__":
    asyncio.run(run())
