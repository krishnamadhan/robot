"""
Working memory — last ~5 minutes of events and current context.
Fast in-memory, never persisted. Cleared on restart.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional


@dataclass
class WorkingMemoryEntry:
    key: str
    value: Any
    timestamp: float = field(default_factory=time.monotonic)
    ttl_s: float = 300.0   # 5 minutes default

    def is_expired(self) -> bool:
        return (time.monotonic() - self.timestamp) > self.ttl_s


class WorkingMemory:
    """
    Fast key-value store for current context.

    Used by: behavior engine (what is Cosmo doing?),
    conversation manager (what are we talking about?),
    attention system (what is Cosmo looking at?).
    """

    MAX_EVENT_HISTORY = 200

    def __init__(self) -> None:
        self._store: Dict[str, WorkingMemoryEntry] = {}
        self._event_log: Deque[Dict[str, Any]] = deque(maxlen=self.MAX_EVENT_HISTORY)
        self._conversation: List[Dict[str, str]] = []
        self._max_conversation_turns = 10

    def set(self, key: str, value: Any, ttl_s: float = 300.0) -> None:
        self._store[key] = WorkingMemoryEntry(key=key, value=value, ttl_s=ttl_s)

    def get(self, key: str, default: Any = None) -> Any:
        entry = self._store.get(key)
        if entry is None or entry.is_expired():
            return default
        return entry.value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        self._event_log.append({
            "type": event_type,
            "data": data,
            "ts": time.monotonic(),
        })

    def recent_events(self, event_type: Optional[str] = None,
                      max_age_s: float = 60.0) -> List[Dict[str, Any]]:
        now = time.monotonic()
        return [
            e for e in reversed(self._event_log)
            if (now - e["ts"]) <= max_age_s
            and (event_type is None or e["type"] == event_type)
        ]

    # Conversation context
    def add_turn(self, role: str, content: str) -> None:
        self._conversation.append({"role": role, "content": content})
        if len(self._conversation) > self._max_conversation_turns * 2:
            self._conversation = self._conversation[-self._max_conversation_turns * 2:]

    def get_conversation(self) -> List[Dict[str, str]]:
        return list(self._conversation)

    def clear_conversation(self) -> None:
        self._conversation.clear()

    def purge_expired(self) -> int:
        expired = [k for k, v in self._store.items() if v.is_expired()]
        for k in expired:
            del self._store[k]
        return len(expired)

    def snapshot(self) -> Dict[str, Any]:
        self.purge_expired()
        return {
            "store_size": len(self._store),
            "event_log_size": len(self._event_log),
            "conversation_turns": len(self._conversation) // 2,
            "keys": list(self._store.keys()),
        }


wm = WorkingMemory()
