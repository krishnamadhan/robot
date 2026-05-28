"""
PersonalityPromptBuilder — converts EmotionalState into deterministic system-prompt preamble.

Design goals:
- Deterministic: same state → same string (I7)
- Compact: ~100-150 chars to save tokens
- Tone-shifted: extreme states produce visibly different language
- No random — all choices derived from state values
"""

from typing import Optional

from core.personality import EmotionalState


# Mood thresholds → tone word
_MOOD_WORDS = [
    (0.7,  "super happy"),
    (0.3,  "happy"),
    (0.0,  "okay"),
    (-0.3, "a bit grumpy"),
    (-1.1, "sad"),
]

_ENERGY_WORDS = [
    (0.7, "full of energy"),
    (0.4, "moderately energetic"),
    (0.0, "tired"),
    (-1.1, "exhausted"),
]

_AROUSAL_WORDS = [
    (0.7, "very excited"),
    (0.4, "alert"),
    (0.2, "calm"),
    (-1.1, "very calm"),
]

_ATTACHMENT_WORDS = [
    (0.8, "deeply bonded"),
    (0.5, "warm"),
    (0.2, "polite"),
    (-1.1, "distant"),
]


def _bucket(value: float, thresholds) -> str:
    for threshold, word in thresholds:
        if value >= threshold:
            return word
    return thresholds[-1][1]


class PersonalityPromptBuilder:
    """
    Converts an EmotionalState into a compact, deterministic system-prompt preamble.

    Usage:
        builder = PersonalityPromptBuilder()
        preamble = builder.build(state)

    The preamble is injected at the top of every Tier-2 LLM system prompt.
    It shifts Cosmo's tone based on current emotional state.
    """

    @staticmethod
    def build(state: EmotionalState, person_name: Optional[str] = None) -> str:
        """
        Build a deterministic personality preamble string.

        Args:
            state: Current EmotionalState (mood, energy, arousal, attachment)
            person_name: Optional — affects pronoun/reference (future use)

        Returns:
            A compact string like:
              "Cosmo feels happy, full of energy, alert, warm."
              "Cosmo feels sad, exhausted, very calm, distant."
        """
        mood_word       = _bucket(state.mood,       _MOOD_WORDS)
        energy_word     = _bucket(state.energy,     _ENERGY_WORDS)
        arousal_word    = _bucket(state.arousal,    _AROUSAL_WORDS)
        attachment_word = _bucket(state.attachment, _ATTACHMENT_WORDS)

        parts = [mood_word, energy_word, arousal_word, attachment_word]
        return f"Cosmo feels {', '.join(parts)}."

    @staticmethod
    def build_full(state: EmotionalState, person_name: Optional[str] = None) -> str:
        """
        Build a fuller preamble with numerical values for richer prompts.
        Still deterministic — no random elements.
        """
        mood_word       = _bucket(state.mood,       _MOOD_WORDS)
        energy_word     = _bucket(state.energy,     _ENERGY_WORDS)
        arousal_word    = _bucket(state.arousal,    _AROUSAL_WORDS)
        attachment_word = _bucket(state.attachment, _ATTACHMENT_WORDS)

        # Tone guidance shifts with extremes
        tone_guidance = _build_tone_guidance(state)

        lines = [
            f"[Cosmo's state: mood={state.mood:+.2f} ({mood_word}), "
            f"energy={state.energy:.2f} ({energy_word}), "
            f"arousal={state.arousal:.2f} ({arousal_word}), "
            f"attachment={state.attachment:.2f} ({attachment_word})]",
            tone_guidance,
        ]
        return "\n".join(lines)


def _build_tone_guidance(state: EmotionalState) -> str:
    """
    Deterministic tone instruction based on emotional extremes.
    Returns a single sentence describing how Cosmo should speak.
    """
    parts = []

    # Mood → verbal warmth
    if state.mood > 0.7:
        parts.append("Speak with warmth and enthusiasm")
    elif state.mood < -0.3:
        parts.append("Speak quietly, a bit subdued")

    # Energy → verbosity
    if state.energy > 0.7:
        parts.append("be a bit chatty")
    elif state.energy < 0.2:
        parts.append("keep it brief — you're tired")

    # Arousal → expressiveness
    if state.arousal > 0.7:
        parts.append("use exclamation marks freely")
    elif state.arousal < 0.2:
        parts.append("be measured and soft-spoken")

    # Attachment → intimacy
    if state.attachment > 0.8:
        parts.append("speak like family")
    elif state.attachment < 0.2:
        parts.append("stay a bit reserved with this person")

    if not parts:
        return "Speak naturally."

    # Deterministically build the sentence
    return parts[0] + (", " + ", ".join(parts[1:]) if len(parts) > 1 else "") + "."
