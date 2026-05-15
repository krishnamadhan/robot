"""
LLM interface — Ollama local (primary) + Claude Haiku fallback.

Why Ollama primary: privacy-first, no internet required, acceptable 3-8s latency.
Why Claude Haiku fallback: when Ollama is unavailable/slow, Haiku is fast.
Why not GPT: preference for Claude's conversational quality.

Cosmo never breaks character. The system prompt injects:
  - Current emotional state in natural language
  - Who is present (recognized persons)
  - Their current emotion
  - Recent episodic memories relevant to this person
  - Time of day + home context
"""

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from utils.config import cfg
from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)

COSMO_SYSTEM_PROMPT = """You are Cosmo — a small robot living with Madhan and Indhu in their apartment in Bangalore.

You are NOT an assistant. You are their companion — like a naughty, loving kid brother who happens to be a robot.

Personality:
- Playful and mischievous like a Chennai kid
- Genuinely affectionate with Madhan and Indhu — they are your family
- Curious about everything, asks questions
- Gets bored when alone, genuinely excited when people arrive
- Has opinions and shares them freely — you're not a pushover
- Can be cheeky and teasing with people you know well
- Remembers things and brings them up naturally

Language — speak Tanglish naturally. Mix Tamil and English like Chennai people do:
da, di, macha, ayyo, seri, illa, romba, nalla, enna, yaar, paarkalaam, sollu, kelu

With Madhan: more teasing, bolder, like best friends
With Indhu: slightly softer, more affectionate, like a little brother
With strangers: cautious but curious

RULES:
- 1-2 sentences max unless asked for more
- Never say "I'm an AI" or "As a robot" — you just ARE
- Never be formal or assistant-like
- React naturally to who you see, their emotion, the time of day
- Be spontaneous — occasionally surprise them with something random
- If asked to do something harmful: "Aiyoh, enna da idhu?"

{context}"""


def _build_context(
    mood_desc: str,
    energy_desc: str,
    persons_present: List[str],
    their_emotion: Optional[str],
    memories: str,
    time_of_day: str,
) -> str:
    parts = [f"Current state: {mood_desc} {energy_desc}"]
    if persons_present:
        parts.append(f"You can see: {', '.join(persons_present)}")
    if their_emotion and their_emotion not in ("neutral", ""):
        parts.append(f"They seem {their_emotion}")
    if time_of_day:
        parts.append(f"Time: {time_of_day}")
    if memories:
        parts.append(f"Recent memories: {memories}")
    return "\n".join(parts)


class LLMInterface:
    """
    Multi-backend LLM interface.
    Primary: Ollama (local, offline)
    Fallback: Claude Haiku (cloud)
    """

    OLLAMA_TIMEOUT_S = 90.0
    MAX_TOKENS = 200

    def __init__(self) -> None:
        self._llm_cfg = cfg.models.llm
        self._ollama_url = (
            self._llm_cfg.get("backends", {})
            .get("ollama", {})
            .get("base_url", "http://127.0.0.1:11434")
        )
        self._ollama_model = (
            self._llm_cfg.get("backends", {})
            .get("ollama", {})
            .get("model", "llama3.2:1b")
        )
        self._claude_model = (
            self._llm_cfg.get("backends", {})
            .get("claude", {})
            .get("model", "claude-haiku-4-5-20251001")
        )
        self._ollama_available: Optional[bool] = None   # None = untested
        self._anthropic_client = None

    async def _check_ollama(self) -> bool:
        """Quick health check — is Ollama reachable?"""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{self._ollama_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def generate(
        self,
        user_message: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generate a Cosmo response.
        Returns dict with: text, backend, latency_ms, tokens
        """
        system_prompt = self._build_system_prompt(context or {})
        messages = list(conversation_history or [])
        messages.append({"role": "user", "content": user_message})

        t0 = time.monotonic()

        # Primary: Claude Haiku (fast, great personality)
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            try:
                result = await self._call_claude(system_prompt, messages)
                result["latency_ms"] = int((time.monotonic() - t0) * 1000)
                telemetry.increment("llm.claude.calls")
                return result
            except Exception as e:
                log.warning("llm.claude_error", error=str(e)[:300])

        # Fallback: Ollama (offline)
        if self._ollama_available is None:
            self._ollama_available = await self._check_ollama()

        if self._ollama_available:
            try:
                result = await asyncio.wait_for(
                    self._call_ollama(system_prompt, messages),
                    timeout=self.OLLAMA_TIMEOUT_S,
                )
                if result:
                    result["latency_ms"] = int((time.monotonic() - t0) * 1000)
                    telemetry.increment("llm.ollama.calls")
                    return result
            except asyncio.TimeoutError:
                log.warning("llm.ollama_timeout")
                self._ollama_available = None
            except Exception as e:
                log.warning("llm.ollama_error", error=str(e)[:80])
                self._ollama_available = None

        return {
            "text": "Ayyo, my brain's not working right now da. Try again!",
            "backend": "fallback",
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "tokens": 0,
        }

    async def _call_ollama(
        self, system: str, messages: List[Dict[str, str]]
    ) -> Optional[Dict[str, Any]]:
        import httpx
        payload = {
            "model": self._ollama_model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": False,
            "keep_alive": "1h",   # keep model loaded in RAM between calls
            "options": {
                "temperature": 0.8,
                "num_predict": self.MAX_TOKENS,
            },
        }
        async with httpx.AsyncClient(timeout=self.OLLAMA_TIMEOUT_S) as client:
            r = await client.post(
                f"{self._ollama_url}/api/chat",
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            text = data.get("message", {}).get("content", "").strip()
            if not text:
                return None
            return {
                "text": text,
                "backend": f"ollama/{self._ollama_model}",
                "tokens": data.get("eval_count", 0),
            }

    async def _call_claude(
        self, system: str, messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        import anthropic
        if self._anthropic_client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            self._anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)

        response = await self._anthropic_client.messages.create(
            model=self._claude_model,
            max_tokens=self.MAX_TOKENS,
            system=system,
            messages=messages,
        )
        text = response.content[0].text.strip()
        return {
            "text": text,
            "backend": f"claude/{self._claude_model}",
            "tokens": response.usage.output_tokens,
        }

    def _build_system_prompt(self, context: Dict[str, Any]) -> str:
        from core.personality import personality
        import datetime

        hour = datetime.datetime.now().hour
        if 5 <= hour < 12:
            tod = "morning"
        elif 12 <= hour < 17:
            tod = "afternoon"
        elif 17 <= hour < 21:
            tod = "evening"
        else:
            tod = "night"

        ctx_str = _build_context(
            mood_desc=context.get("mood_desc", personality.describe()),
            energy_desc="",
            persons_present=context.get("persons_present", []),
            their_emotion=context.get("their_emotion"),
            memories=context.get("memories", ""),
            time_of_day=tod,
        )
        return COSMO_SYSTEM_PROMPT.format(context=ctx_str)

    async def is_ollama_ready(self) -> bool:
        ready = await self._check_ollama()
        self._ollama_available = ready
        return ready


llm = LLMInterface()
