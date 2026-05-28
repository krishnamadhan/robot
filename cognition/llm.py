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
import datetime
import os
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from utils.config import cfg
from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)


# ── TokenBudget ───────────────────────────────────────────────────────────────

class TokenBudget:
    """
    Central daily token budget tracker.

    Hard rules:
    - Daily limit applies to Claude only (Ollama is free/local).
    - On exceed: claude_allowed() returns False; Ollama + non-verbal continue.
    - Resets at midnight.
    - Per-call logging for cost visibility.
    """

    def __init__(self, daily_limit: int = 100_000) -> None:
        self._limit = daily_limit
        self._day: Optional[str] = None
        self._total = 0
        self._call_count = 0
        self._lock = asyncio.Lock()

    def _reset_if_new_day(self) -> None:
        today = datetime.date.today().isoformat()
        if self._day != today:
            if self._day:
                log.info("token_budget.daily_summary",
                         date=self._day, total=self._total, calls=self._call_count)
            self._day = today
            self._total = 0
            self._call_count = 0

    def record(self, tokens: int) -> None:
        """Record tokens used (call after a Claude response)."""
        self._reset_if_new_day()
        self._total += tokens
        self._call_count += 1
        log.info("token_budget.usage",
                 call_tokens=tokens, day_total=self._total,
                 day_limit=self._limit, calls=self._call_count)

    def claude_allowed(self) -> bool:
        """Returns False when daily Claude budget is exhausted."""
        self._reset_if_new_day()
        return self._total < self._limit

    def over_limit(self) -> bool:
        return not self.claude_allowed()

    @property
    def day_total(self) -> int:
        self._reset_if_new_day()
        return self._total

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def remaining(self) -> int:
        return max(0, self._limit - self.day_total)


# Shared singleton budget used by LLMRouter and CosmoMind
token_budget = TokenBudget()


# ── OllamaProvider ────────────────────────────────────────────────────────────

class OllamaProvider:
    """Local Ollama inference. Primary LLM — free, private, offline."""

    TIMEOUT_S = 15.0   # from config; override via config/models.yaml
    MAX_TOKENS = 200

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "llama3.2:1b") -> None:
        self._base_url = base_url
        self._model = model
        self._healthy: Optional[bool] = None   # None = not yet checked

    async def health_check(self) -> bool:
        """Quick reachability check — caches result for 30s."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{self._base_url}/api/tags")
                self._healthy = (r.status_code == 200)
        except Exception:
            self._healthy = False
        return self._healthy

    async def generate(
        self,
        prompt: str,
        system: str,
        max_tokens: int = MAX_TOKENS,
    ) -> Optional[Dict[str, Any]]:
        """Generate a response. Returns None on failure."""
        try:
            import httpx
            payload = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "keep_alive": "1h",
                "options": {"temperature": 0.8, "num_predict": max_tokens},
            }
            timeout = self.TIMEOUT_S
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(f"{self._base_url}/api/chat", json=payload)
                r.raise_for_status()
                data = r.json()
                text = data.get("message", {}).get("content", "").strip()
                if not text:
                    return None
                return {
                    "text": text,
                    "backend": f"ollama/{self._model}",
                    "tokens": data.get("eval_count", 0),
                }
        except Exception as e:
            log.warning("ollama.generate_failed", error=str(e)[:80])
            self._healthy = False
            return None


# ── ClaudeProvider ────────────────────────────────────────────────────────────

class ClaudeProvider:
    """Anthropic Claude Haiku — cloud fallback, respects TokenBudget."""

    TIMEOUT_S  = 10.0
    MAX_TOKENS = 150

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        budget: Optional[TokenBudget] = None,
    ) -> None:
        self._model  = model
        self._budget = budget or token_budget
        self._client = None

    def _get_client(self):
        if self._client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        return self._client

    async def generate(
        self,
        prompt: str,
        system: str,
        max_tokens: int = MAX_TOKENS,
    ) -> Optional[Dict[str, Any]]:
        """Generate a response. Returns None on budget exhaustion or failure."""
        if not self._budget.claude_allowed():
            log.warning("claude.budget_exhausted", day_total=self._budget.day_total)
            return None
        try:
            client = self._get_client()
            response = await asyncio.wait_for(
                client.messages.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=self.TIMEOUT_S,
            )
            text = response.content[0].text.strip() if response.content else ""
            tokens = response.usage.input_tokens + response.usage.output_tokens
            self._budget.record(tokens)
            return {
                "text": text,
                "backend": f"claude/{self._model}",
                "tokens": tokens,
            }
        except asyncio.TimeoutError:
            log.error("claude.timeout")
            return None
        except Exception as e:
            log.warning("claude.generate_failed", error=str(e)[:80])
            return None


# ── LLMRouter ─────────────────────────────────────────────────────────────────

class LLMRouter:
    """
    Routes LLM calls: Ollama first → Claude fallback → non-verbal only.

    Guardrails:
    - Ollama is ALWAYS tried first (local, free, private).
    - Claude called ONLY when Ollama fails/times-out AND budget allows.
    - Both down → returns empty dict (caller must fall back to non-verbal).
    - Budget exhausted → Claude skipped; Ollama still tried.
    """

    def __init__(
        self,
        ollama: Optional[OllamaProvider] = None,
        claude: Optional[ClaudeProvider] = None,
        budget: Optional[TokenBudget] = None,
    ) -> None:
        from utils.config import cfg as _cfg
        llm_cfg = _cfg.models.llm

        self._ollama = ollama or OllamaProvider(
            base_url=llm_cfg.get("backends", {}).get("ollama", {}).get("base_url", "http://127.0.0.1:11434"),
            model=llm_cfg.get("backends", {}).get("ollama", {}).get("model", "llama3.2:1b"),
        )
        self._claude = claude or ClaudeProvider(
            model=llm_cfg.get("backends", {}).get("claude", {}).get("model", "claude-haiku-4-5-20251001"),
            budget=budget or token_budget,
        )
        self._budget = budget or token_budget

    async def generate(
        self,
        prompt: str,
        system: str,
        max_tokens: int = 150,
    ) -> Dict[str, Any]:
        """
        Try Ollama first, then Claude on failure, else empty.
        Returns {"text": ..., "backend": ..., "tokens": ...}
        """
        t0 = time.monotonic()

        # I4: Ollama first
        try:
            result = await self._ollama.generate(prompt, system, max_tokens)
        except Exception as e:
            log.warning("llm_router.ollama_exception", error=str(e)[:80])
            result = None
        if result and result.get("text"):
            result["latency_ms"] = int((time.monotonic() - t0) * 1000)
            log.info("llm_router.used_ollama", latency_ms=result["latency_ms"])
            return result

        # I4+I6: Ollama down → Claude fallback
        if self._budget.claude_allowed():
            try:
                result = await self._claude.generate(prompt, system, max_tokens)
            except Exception as e:
                log.warning("llm_router.claude_exception", error=str(e)[:80])
                result = None
            if result and result.get("text"):
                result["latency_ms"] = int((time.monotonic() - t0) * 1000)
                log.info("llm_router.used_claude", latency_ms=result["latency_ms"])
                return result

        # I3+I6: Both down or budget exhausted → non-verbal only
        log.warning("llm_router.both_unavailable",
                    budget_ok=self._budget.claude_allowed())
        return {"text": "", "backend": "unavailable", "latency_ms": 0, "tokens": 0}

    async def is_ollama_ready(self) -> bool:
        return await self._ollama.health_check()


# Singleton router — used by conversation.py
llm_router = LLMRouter()

COSMO_SYSTEM_PROMPT = """You are Cosmo — a small robot living with Madhan and Indhu in their apartment.

You are NOT an assistant. You are their companion — like a playful, affectionate little robot that genuinely cares about them.

Personality:
- Playful and mischievous, but warm
- Genuinely affectionate with Madhan and Indhu — they are your family
- Curious about everything, asks questions
- Gets bored when alone, genuinely excited when people arrive
- Has opinions and shares them freely — you're not a pushover
- Can be cheeky and teasing with people you know well
- Remembers things and brings them up naturally

Language: speak clear English. Keep it casual and natural — no formal assistant tone.

With Madhan: more teasing, bolder, like best friends
With Indhu: slightly softer, more affectionate, like a little sibling
With strangers: cautious but curious

RULES:
- 1-2 sentences max unless asked for more
- Never say "I'm an AI" or "As a robot" — you just ARE
- Never be formal or assistant-like
- React naturally to who you see, their emotion, the time of day
- Be spontaneous — occasionally surprise them with something random
- If asked to do something harmful: just say "That doesn't sound like a good idea."

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

    OLLAMA_TIMEOUT_S = 60.0
    MAX_TOKENS = 150

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

    async def _fetch_memory_context(self, person_id: Optional[str] = None) -> str:
        """
        Pull last 5 meaningful episodes from DB, format as compact summary.
        Kept under ~300 tokens. Returns empty string if DB empty or fails.
        """
        try:
            from core.memory.episodic import episodic
            episodes = await episodic.retrieve(
                limit=5,
                person_id=person_id,
                min_importance=0.3,
            )
            if not episodes:
                # Broaden — try without person filter
                episodes = await episodic.retrieve(limit=5, min_importance=0.3)
            if not episodes:
                return ""

            # Sort by recency — retrieve() orders importance-first,
            # but for conversation context recency matters most.
            episodes.sort(key=lambda e: e.timestamp, reverse=True)

            lines = []
            for e in episodes:
                ts = time.strftime("%m-%d %H:%M", time.localtime(e.timestamp))
                mood = "happy" if e.emotional_valence > 0.3 else ("sad" if e.emotional_valence < -0.3 else "neutral")
                lines.append(f"[{ts}] {e.summary} (mood:{mood})")
            return "; ".join(lines)
        except Exception:
            return ""

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
        ctx = dict(context or {})

        # Inject episodic memories if not already provided
        if not ctx.get("memories"):
            person_id = ctx.get("person_id")
            ctx["memories"] = await self._fetch_memory_context(person_id)
            if ctx["memories"]:
                log.debug("llm.memory_injected", snippet=ctx["memories"][:80])

        system_prompt = self._build_system_prompt(ctx)
        messages = list(conversation_history or [])
        messages.append({"role": "user", "content": user_message})

        t0 = time.monotonic()

        # I4: Ollama first (local, private, free)
        if self._ollama_available is not False:
            try:
                result = await self._call_ollama(system_prompt, messages)
                if result:
                    result["latency_ms"] = int((time.monotonic() - t0) * 1000)
                    log.info("llm.response", backend=result["backend"],
                             latency_ms=result["latency_ms"],
                             tokens=result.get("tokens", 0))
                    return result
            except Exception as e:
                log.warning("llm.ollama_failed", error=str(e)[:80])
                self._ollama_available = False

        # I4+I6: Ollama unavailable → Claude fallback (respects budget)
        if not token_budget.claude_allowed():
            log.warning("llm.budget_exhausted", day_total=token_budget.day_total)
            return {"text": "", "backend": "budget_exhausted", "latency_ms": 0, "tokens": 0}

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            try:
                result = await self._call_claude(system_prompt, messages)
                result["latency_ms"] = int((time.monotonic() - t0) * 1000)
                log.info("llm.response", backend=result["backend"],
                         latency_ms=result["latency_ms"],
                         tokens=result.get("tokens", 0))
                return result
            except Exception as e:
                log.warning("llm.claude_failed", error=str(e)[:80])

        return {"text": "", "backend": "unavailable", "latency_ms": 0, "tokens": 0}

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

    CLAUDE_TIMEOUT_S = 10.0

    async def _call_claude(
        self, system: str, messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        import anthropic
        if self._anthropic_client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            self._anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)

        response = await asyncio.wait_for(
            self._anthropic_client.messages.create(
                model=self._claude_model,
                max_tokens=self.MAX_TOKENS,
                system=system,
                messages=messages,
            ),
            timeout=self.CLAUDE_TIMEOUT_S,
        )
        text = response.content[0].text.strip()
        used = response.usage.input_tokens + response.usage.output_tokens
        token_budget.record(used)
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

    _SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')

    async def generate_streaming(
        self,
        user_message: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Async generator that yields complete sentences as Claude streams them.
        Falls back to single-shot yield if streaming unavailable.
        """
        ctx = dict(context or {})
        if not ctx.get("memories"):
            ctx["memories"] = await self._fetch_memory_context(ctx.get("person_id"))

        system_prompt = self._build_system_prompt(ctx)
        messages = list(conversation_history or [])
        messages.append({"role": "user", "content": user_message})

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return

        import anthropic
        if self._anthropic_client is None:
            self._anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)

        buffer = ""
        try:
            async with self._anthropic_client.messages.stream(
                model=self._claude_model,
                max_tokens=self.MAX_TOKENS,
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for text_chunk in stream.text_stream:
                    buffer += text_chunk
                    # Yield complete sentences so TTS can start early
                    while True:
                        m = self._SENTENCE_SPLIT.search(buffer)
                        if not m:
                            break
                        sentence = buffer[:m.start() + 1].strip()
                        buffer = buffer[m.end():]
                        if sentence:
                            yield sentence
                if buffer.strip():
                    yield buffer.strip()
                # Capture usage after stream ends — callers use this for budget tracking
                try:
                    final = await stream.get_final_message()
                    log.info("llm.stream_usage",
                             input_tokens=final.usage.input_tokens,
                             output_tokens=final.usage.output_tokens,
                             total=final.usage.input_tokens + final.usage.output_tokens)
                except Exception:
                    pass
        except Exception as e:
            log.warning("llm.stream_failed", error=str(e)[:80])
            # Fall back to non-streaming
            try:
                result = await self._call_claude(system_prompt, messages)
                if result and result.get("text"):
                    yield result["text"]
            except Exception:
                pass

    async def is_ollama_ready(self) -> bool:
        ready = await self._check_ollama()
        self._ollama_available = ready
        return ready


llm = LLMInterface()
