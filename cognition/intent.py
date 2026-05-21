"""
Intent parser — offline, Tamil+English command matching.

Fast pattern matching without LLM. Returns None if no match → let LLM handle.
Patterns cover Tanglish phrases used in the Banter Squad friend group style.
"""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.event_bus import Event, EventPriority, EventType, bus
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class Intent:
    name:       str
    action:     str
    confidence: float
    raw_text:   str
    params:     Dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"Intent({self.name}, conf={self.confidence:.2f}, action={self.action})"


# ── Intent definitions ────────────────────────────────────────────────────────

_INTENTS: Dict[str, Dict] = {
    "come_here": {
        "patterns": [
            "come here", "come to me", "inga vaa", "inga va",
            "vaa vaa", "this way", "innga", "come da",
        ],
        "action": "navigation.approach_person",
    },
    "follow_me": {
        "patterns": [
            "follow me", "follow", "enna follow panni",
            "come with me", "follow pannu",
        ],
        "action": "navigation.follow_mode",
    },
    "stop": {
        "patterns": [
            "stop", "stay", "dont move", "nillu", "nil",
            "pause", "thungu", "niruththu", "freeze",
        ],
        "action": "navigation.stop",
    },
    "go_away": {
        "patterns": [
            "go away", "leave me", "poda", "po po", "poo",
            "get away", "move away", "leave", "podi",
        ],
        "action": "navigation.retreat",
    },
    "how_are_you": {
        "patterns": [
            "how are you", "eppadi irukka", "eppadi irukkiye",
            "how feeling", "whats up", "enna panna",
            "how do you feel", "nee eppadi",
        ],
        "action": "respond.mood_report",
    },
    "dance": {
        "patterns": [
            "dance", "aadunga", "aadu", "move", "shake",
            "kuthu", "kuthu aadu", "dance da",
        ],
        "action": "behavior.dance",
    },
    "sleep": {
        "patterns": [
            "sleep", "go to sleep", "thoonga po", "rest",
            "paduthu thoonga", "thoodu", "good night",
        ],
        "action": "state.transition.SLEEPING",
    },
    "wake_up": {
        "patterns": [
            "wake up", "ezhu", "arise", "get up", "levi",
            "rise", "wake", "good morning",
        ],
        "action": "state.transition.IDLE",
    },
    "be_quiet": {
        "patterns": [
            "quiet", "shut up", "shhh", "be quiet",
            "pesade", "thuppu", "stop talking", "silence",
        ],
        "action": "audio.mute_30s",
    },
    "good_boy": {
        "patterns": [
            "good boy", "good robot", "nalla irukka", "shabash",
            "well done", "good job", "clever", "smart boy",
        ],
        "action": "behavior.happy_reaction",
    },
    "i_love_you": {
        "patterns": [
            "i love you", "love you", "kadhal", "en kanmani",
            "priya", "i like you", "you are cute",
        ],
        "action": "behavior.love_reaction",
    },
    "whats_your_name": {
        "patterns": [
            "whats your name", "your name", "yaar nee",
            "en peru", "who are you", "what are you called",
        ],
        "action": "respond.name_intro",
    },
    "spin": {
        "patterns": [
            "spin", "rotate", "turn around", "circle",
            "do a spin", "vattam podu",
        ],
        "action": "navigation.spin_360",
    },
    "look_around": {
        "patterns": [
            "look around", "scan", "search", "paaru",
            "look", "check around",
        ],
        "action": "behavior.look_around",
    },
    "mind_off": {
        "patterns": [
            "mind off", "stop thinking", "save power", "power save",
            "brain off", "stop mind", "manam off",
        ],
        "action": "cosmo_mind.disable",
    },
    "mind_on": {
        "patterns": [
            "mind on", "start thinking", "brain on", "think again",
            "manam on", "start mind", "enable mind",
        ],
        "action": "cosmo_mind.enable",
    },
}

_ACTION_MAP: Dict[str, str] = {
    intent: data["action"]
    for intent, data in _INTENTS.items()
}


# ── Parser ────────────────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _word_match(pattern: str, text: str) -> bool:
    """Match pattern as whole words — prevents 'nil' matching 'pencil' etc."""
    return bool(re.search(r'\b' + re.escape(pattern) + r'\b', text))


class IntentParser:

    def parse(self, text: str) -> Optional[Intent]:
        norm = _normalise(text)
        best_match: Optional[Intent] = None
        best_pattern_len = 0

        for name, data in _INTENTS.items():
            for pattern in data["patterns"]:
                if _word_match(pattern, norm):
                    # Prefer longer pattern matches (more specific)
                    if len(pattern) > best_pattern_len:
                        best_pattern_len = len(pattern)
                        best_match = Intent(
                            name=name,
                            action=data["action"],
                            confidence=min(1.0, len(pattern) / max(len(norm), 1) + 0.6),
                            raw_text=text,
                        )

        if best_match:
            log.info("intent.matched",
                     intent=best_match.name,
                     conf=round(best_match.confidence, 2),
                     text=text[:50])
        return best_match

    async def parse_and_publish(self, text: str) -> Optional[Intent]:
        intent = self.parse(text)
        if intent:
            await bus.publish(Event(
                type=EventType.USER_INTENT,
                data={
                    "intent": intent.name,
                    "action": intent.action,
                    "confidence": intent.confidence,
                    "text": text,
                },
                priority=EventPriority.HIGH,
            ))
        return intent

    def get_action(self, intent_name: str) -> Optional[str]:
        return _ACTION_MAP.get(intent_name)

    def all_intents(self) -> List[str]:
        return list(_INTENTS.keys())


intent_parser = IntentParser()
