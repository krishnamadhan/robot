"""AB-010 — external calls must be time-bounded (KI-014 / KI-015).

These tests pin the *contracts*, not the network: the stream call must carry
a per-request timeout, paplay must be waited with a bound and killed on
expiry, and the budget reservation must be released when a call times out.
"""
import asyncio
import inspect
import subprocess
from unittest.mock import MagicMock, patch

import pytest


# ── KI-014: streaming carries a timeout ──────────────────────────────────────

def test_stream_source_passes_timeout():
    """generate_streaming must pass timeout= to messages.stream."""
    from cognition import llm
    src = inspect.getsource(llm.LLMInterface.generate_streaming)
    assert "timeout=self.CLAUDE_TIMEOUT_S" in src


def test_call_claude_releases_reservation_on_timeout():
    """A timed-out non-streaming call must not leak the budget reservation."""
    from cognition.llm import LLMInterface, token_budget

    iface = LLMInterface()

    class _NeverResolves:
        async def create(self, **kw):
            await asyncio.sleep(999)

    client = MagicMock()
    client.messages = _NeverResolves()
    iface._anthropic_client = client
    iface.CLAUDE_TIMEOUT_S = 0.05

    before = token_budget._reserved
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}):
        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(iface._call_claude("sys", [{"role": "user", "content": "x"}]))
    assert token_budget._reserved == before, "reservation leaked on timeout"


# ── KI-015: paplay wait is bounded ───────────────────────────────────────────

def test_paplay_wedge_is_killed():
    """A paplay that never exits must be killed after the audio-length bound."""
    from expression.speech import TTSEngine

    eng = TTSEngine.__new__(TTSEngine)  # skip __init__ (probes binaries)
    eng._lock = __import__("threading").Lock()
    eng._proc = None

    piper_proc = MagicMock()
    piper_proc.communicate.return_value = (b"\x00\x00" * 2205, b"")  # 0.1 s audio

    paplay_proc = MagicMock()
    paplay_proc.stdin = MagicMock()
    # First wait(timeout=...) raises (wedged); the post-kill wait() returns.
    paplay_proc.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="paplay", timeout=5.1), 0]

    with patch("expression.speech.subprocess.Popen",
               side_effect=[piper_proc, paplay_proc]):
        eng._speak_piper("hello")

    paplay_proc.kill.assert_called_once()
    assert paplay_proc.wait.call_count == 2
    # And the bound passed to the first wait must be finite and audio-derived.
    args, kwargs = paplay_proc.wait.call_args_list[0]
    assert kwargs.get("timeout") is not None and kwargs["timeout"] < 60


def test_describe_endpoint_uses_async_client_with_timeout():
    """/trigger/describe must not run a sync HTTP call on the event loop."""
    import services.api.service as svc
    src = inspect.getsource(svc.trigger_describe)
    assert "AsyncAnthropic" in src and "timeout=" in src
    assert "await client.messages.create" in src
