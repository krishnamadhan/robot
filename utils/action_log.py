"""
Structured action log — every robot output with its triggering reason.

Usage:
  from utils.action_log import action_log

  # Set WHY before the output fires (stays until next set_context call)
  action_log.set_context("face_recognized", "Madhan 92%")
  await sounds.play("chime_greeting")   # auto-logged

  # Or record manually
  action_log.record("move", "forward 55%")

Output types: sound | speech | move | expr | display (future: servo, arm)
Stores last 100 in memory + rolling JSON for WhatsApp debugging.
"""
import json
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List

LOG_FILE   = Path.home() / ".robot/logs/action_log.json"
MAX_ENTRIES = 100

# Output type → emoji, for WhatsApp formatting
OUTPUT_EMOJI: Dict[str, str] = {
    "sound":   "🔊",
    "speech":  "🗣️",
    "move":    "🚶",
    "expr":    "👁️",
    "display": "🖥️",
    "servo":   "🦾",
}


@dataclass
class ActionEntry:
    ts:           float  # unix timestamp
    ts_str:       str    # HH:MM:SS
    trigger:      str    # "face_recognized" | "gesture:Open_Palm" | "behavior_wander" …
    detail:       str    # extra context: "Madhan 92%" | "alone 127s" | ""
    output_type:  str    # "sound" | "speech" | "move" | "expr" | "display"
    output_detail: str   # "chime_greeting" | "Hey Madhan!" | "forward 55%" | "HAPPY 4s"


class ActionLog:
    """
    Singleton action log. Module-level context variables are asyncio-safe
    (single-threaded event loop — no races possible).
    """

    def __init__(self) -> None:
        self._log: Deque[ActionEntry] = deque(maxlen=MAX_ENTRIES)
        self._trigger: str = "idle"
        self._detail:  str = ""
        self._load()

    # ── context ───────────────────────────────────────────────────────────────

    def set_context(self, trigger: str, detail: str = "") -> None:
        """Call this before any output — sets the 'why' for all subsequent records."""
        self._trigger = trigger
        self._detail  = detail

    # ── recording ─────────────────────────────────────────────────────────────

    def record(self, output_type: str, output_detail: str) -> None:
        """Record one output action against the current context."""
        entry = ActionEntry(
            ts=time.time(),
            ts_str=datetime.now().strftime("%H:%M:%S"),
            trigger=self._trigger,
            detail=self._detail,
            output_type=output_type,
            output_detail=output_detail,
        )
        self._log.append(entry)
        self._flush()

    # ── retrieval ─────────────────────────────────────────────────────────────

    def get_recent(self, n: int = 20) -> List[Dict]:
        entries = list(self._log)
        return [asdict(e) for e in entries[-n:]]

    def format_for_whatsapp(self, n: int = 20) -> str:
        entries = self.get_recent(n)
        if not entries:
            return "🤖 No reactions logged yet."

        lines = []
        for e in reversed(entries):  # newest first
            emoji = OUTPUT_EMOJI.get(e["output_type"], "•")
            detail = f": {e['detail']}" if e["detail"] else ""
            lines.append(
                f"{e['ts_str']}  {emoji} *{e['output_detail']}*\n"
                f"    📍 {e['trigger']}{detail}"
            )

        header = f"🤖 *Cosmo — Last {len(lines)} Reactions*\n{'━' * 20}\n\n"
        return header + "\n\n".join(lines)

    # ── persistence ───────────────────────────────────────────────────────────

    def _flush(self) -> None:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            LOG_FILE.write_text(json.dumps([asdict(e) for e in self._log]))
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if LOG_FILE.exists():
                for entry in json.loads(LOG_FILE.read_text()):
                    self._log.append(ActionEntry(**entry))
        except Exception:
            pass


action_log = ActionLog()
