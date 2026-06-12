"""Phase 2.5 — greet-by-name: person_id threading, episodic recall, language config."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cognition.mind import (CosmoMind, _LANG_STYLES, _SPEAK_PROMPTS,
                            _SYSTEM_TMPL, _language)


def _bare_mind():
    mind = CosmoMind.__new__(CosmoMind)
    mind._enabled = True
    mind._budget_lock = asyncio.Lock()
    mind._speech_in_flight = asyncio.Event()
    mind._last_spoke = 0.0
    mind._trigger_last = {}
    mind._is_busy = lambda: False
    return mind


def _budget_ok():
    b = MagicMock()
    b.claude_allowed.return_value = True
    return b


class TestLanguageConfig:

    def test_defaults_to_english(self):
        with patch("utils.config.cfg") as cfg:
            cfg.personality.speech = None
            assert _language() == "english"

    def test_reads_tanglish(self):
        with patch("utils.config.cfg") as cfg:
            cfg.personality.speech = {"language": "tanglish"}
            assert _language() == "tanglish"

    def test_invalid_value_falls_back_to_english(self):
        with patch("utils.config.cfg") as cfg:
            cfg.personality.speech = {"language": "klingon"}
            assert _language() == "english"

    def test_ambient_prompts_pin_english(self):
        # Ambient triggers run Ollama-first — must stay English regardless of config
        for trig in ("alone_long", "obstacle", "dark_room"):
            assert "English" in _SPEAK_PROMPTS[trig](None)

    def test_person_prompts_do_not_pin_english(self):
        # Person-facing prompts defer language to the system prompt
        for trig in ("face_seen", "emotion_happy", "emotion_sad", "touched"):
            assert "English" not in _SPEAK_PROMPTS[trig]("Madhan")


class TestGreetByName:

    def _run_speak(self, mind, conv_person_id=None, event_person_id=None,
                   language="english"):
        mock_llm = MagicMock()
        mock_llm.generate_once = AsyncMock(
            return_value={"text": "hello!", "backend": "claude"})
        conv = MagicMock()
        conv._active_person_id = conv_person_id
        conv._their_emotion = None
        mind._build_rich_system_prompt = AsyncMock(return_value="RICH_SYS")
        mind._memory_context = AsyncMock(return_value="")

        async def run():
            with (
                patch("cognition.mind.tts") as mock_tts,
                patch("cognition.mind.router", MagicMock()),
                patch("cognition.llm.token_budget", _budget_ok()),
                patch("cognition.llm.llm", mock_llm),
                patch("cognition.conversation.conversation", conv),
                patch("cognition.mind._language", return_value=language),
                patch.object(CosmoMind, "_hour", staticmethod(lambda: 12)),
                patch("asyncio.sleep", AsyncMock()),
            ):
                mock_tts.is_speaking = False
                mock_tts.speak = AsyncMock()
                await mind._maybe_speak("face_seen", "Madhan",
                                        person_id=event_person_id)
        asyncio.run(run())
        return mind, mock_llm

    def test_event_person_id_used_before_conversation_set(self):
        # FACE_RECOGNIZED fires before conversation.set_person — the event's
        # person_id must still reach the rich (episodic-recall) prompt path
        mind = _bare_mind()
        mind, _ = self._run_speak(mind, conv_person_id=None,
                                  event_person_id="madhan_01")
        mind._build_rich_system_prompt.assert_awaited_once()
        assert mind._build_rich_system_prompt.await_args.args[0] == "madhan_01"

    def test_falls_back_to_conversation_person_id(self):
        mind = _bare_mind()
        mind, _ = self._run_speak(mind, conv_person_id="indhu_01",
                                  event_person_id=None)
        assert mind._build_rich_system_prompt.await_args.args[0] == "indhu_01"

    def test_no_person_uses_language_styled_system(self):
        mind = _bare_mind()
        mind, mock_llm = self._run_speak(mind, language="tanglish")
        system_prompt = mock_llm.generate_once.await_args.args[1]
        assert "Tanglish" in system_prompt
        mind._build_rich_system_prompt.assert_not_awaited()

    def test_rich_prompt_receives_lang_style(self):
        mind = _bare_mind()
        mind, _ = self._run_speak(mind, event_person_id="p1",
                                  language="tanglish")
        assert mind._build_rich_system_prompt.await_args.args[3] == \
            _LANG_STYLES["tanglish"]


class TestRichPrompt:

    def _build(self, mem, lang_style=_LANG_STYLES["english"]):
        mind = _bare_mind()
        episodic = MagicMock()
        episodic.get_context_for_person = AsyncMock(return_value=mem)
        pers = MagicMock()
        pers.state.mood, pers.state.energy = 0.5, 0.6
        attn = MagicMock()
        attn.state.focused = False

        async def run():
            with (
                patch("core.memory.episodic.episodic", episodic),
                patch("core.personality.personality", pers),
                patch("cognition.mind.attention", attn),
            ):
                return await mind._build_rich_system_prompt(
                    "p1", "Madhan", "happy", lang_style)
        return asyncio.run(run())

    def test_includes_memories_and_persons_table_context(self):
        prompt = self._build({
            "memories": ["[2h ago, happy] Played chase in the hall"],
            "total_interactions": 30,
            "familiarity": 1.0,
            "relationship_quality": 0.9,
            "away_s": 2 * 86400,
        })
        assert "Played chase in the hall" in prompt
        assert "someone I know very well" in prompt
        assert "you adore them" in prompt
        assert "2 day(s)" in prompt

    def test_recent_sighting_omits_away_line(self):
        prompt = self._build({
            "memories": [], "total_interactions": 1,
            "familiarity": 0.05, "relationship_quality": 0.5,
            "away_s": 120,
        })
        assert "haven't seen" not in prompt
        assert "last saw them" not in prompt

    def test_language_style_in_response_rules(self):
        prompt = self._build({}, lang_style=_LANG_STYLES["tanglish"])
        assert "Tanglish" in prompt

    def test_system_tmpl_default_is_english(self):
        assert "English" in _SYSTEM_TMPL.format(
            lang_style=_LANG_STYLES["english"])
