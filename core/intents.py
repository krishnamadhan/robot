"""
core/intents.py

The Intent enum — WHAT Cosmo wants to do, never HOW.

Intents are produced by every decision source (behavior tree, voice commands
via cognition.intent.VoiceCommand, CosmoMind rule engine / LLM) and consumed
exclusively by core.action_router, which maps each intent to the best output
that physically exists right now (see core/capabilities.py).

This separation is the north star: same inner life, different body
completeness. New hardware adds capability with zero new behavior code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class Intent(Enum):
    # --- social ---
    GREET = "greet"                        # params: name, person_id
    COMFORT = "comfort"                    # params: name, emotion
    ASK_QUESTION = "ask_question"          # params: name, topic ("curiosity"|"memory")
    ALERT = "alert"                        # params: reason

    # --- expression (no locomotion needed) ---
    EXPRESS_JOY = "express_joy"            # params: variant ("dance"|"reaction")
    EXPRESS_FEAR = "express_fear"
    EXPRESS_CURIOSITY = "express_curiosity"  # params: variant ("look_around")
    EXPRESS_AFFECTION = "express_affection"
    IDLE_FIDGET = "idle_fidget"            # params: variant (blink/purr/breathe/...)
    SLEEP = "sleep"                        # wind-down expression

    # --- movement (requires LOCOMOTION; expressive fallback otherwise) ---
    APPROACH = "approach"                  # params: person_x, speed
    FLEE = "flee"                          # params: speed
    FOLLOW = "follow"                      # params: duration
    COME = "come"
    WANDER = "wander"                      # params: duration
    STOP = "stop"                          # params: emergency


@dataclass
class IntentRequest:
    """A single intent emission, carried to the router."""
    intent: Intent
    params: Dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"
