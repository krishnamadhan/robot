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

    # Headroom reserved per in-flight Claude call (KI-017)
    EST_CALL_TOKENS = 2000

    def __init__(self, daily_limit: int = 100_000) -> None:
        self._limit = daily_limit
        self._day: Optional[str] = None
        self._total = 0
        self._call_count = 0
        self._reserved = 0   # in-flight reservations (KI-017 double-spend guard)
        self._conn = None   # lazy sqlite; False = persistence unavailable

    # ── Persistence (OQ-5: memory_meta table, atomic increment UPSERT) ──────
    def _db(self):
        if self._conn is None:
            try:
                import sqlite3
                from core.memory.episodic import DB_PATH
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS memory_meta "
                    "(key TEXT PRIMARY KEY, value TEXT)")
                self._conn.commit()
            except Exception as e:
                log.warning("token_budget.persistence_unavailable", error=str(e)[:120])
                self._conn = False
        return self._conn or None

    def _persist_add(self, tokens: int) -> None:
        conn = self._db()
        if not conn:
            return
        try:
            conn.execute(
                "INSERT INTO memory_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value = CAST(CAST(value AS INTEGER) + CAST(excluded.value AS INTEGER) AS TEXT)",
                (f"token_budget:{self._day}", str(tokens)))
            conn.commit()
        except Exception as e:
            log.warning("token_budget.persist_failed", error=str(e)[:120])

    def _load_persisted(self) -> int:
        conn = self._db()
        if not conn:
            return 0
        try:
            row = conn.execute(
                "SELECT value FROM memory_meta WHERE key = ?",
                (f"token_budget:{self._day}",)).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def _reset_if_new_day(self) -> None:
        today = datetime.date.today().isoformat()
        if self._day != today:
            if self._day:
                log.info("token_budget.daily_summary",
                         date=self._day, total=self._total, calls=self._call_count)
            self._day = today
            self._call_count = 0
            # Survive process restarts: resume today's spend from SQLite
            self._total = self._load_persisted()
            if self._total:
                log.info("token_budget.resumed", day_total=self._total)

    def record(self, tokens: int) -> None:
        """Record tokens used (call after a Claude response)."""
        self._reset_if_new_day()
        self._total += tokens
        self._call_count += 1
        self._persist_add(tokens)
        log.info("token_budget.usage",
                 call_tokens=tokens, day_total=self._total,
                 day_limit=self._limit, calls=self._call_count)

    def claude_allowed(self) -> bool:
        """Returns False when daily Claude budget is exhausted."""
        self._reset_if_new_day()
        return self._total + self._reserved < self._limit

    def try_reserve(self, estimated: int = EST_CALL_TOKENS) -> bool:
        """
        Atomically check + reserve budget headroom for one Claude call (KI-017).

        Sync on purpose: all callers run on the asyncio event loop thread and
        there is no await between check and reserve, so check-then-reserve
        cannot interleave. Pair every successful reserve with release().
        """
        self._reset_if_new_day()
        if self._total + self._reserved + estimated > self._limit:
            return False
        self._reserved += estimated
        return True

    def release(self, estimated: int = EST_CALL_TOKENS) -> None:
        """Release a reservation taken by try_reserve (actual spend via record)."""
        self._reserved = max(0, self._reserved - estimated)

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


# Shared singleton budget — sole Claude usage ledger (LLMInterface + CosmoMind)
token_budget = TokenBudget()


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


# Static prefix of the system prompt (everything before the dynamic context).
# Sent as its own block with cache_control so Anthropic can cache it across calls.
_PROMPT_STATIC = COSMO_SYSTEM_PROMPT.split("{context}")[0].rstrip()


def _system_to_blocks(system: str) -> List[Dict[str, Any]]:
    """Split a built system prompt into a cacheable static block + dynamic tail."""
    if system.startswith(_PROMPT_STATIC):
        dynamic = system[len(_PROMPT_STATIC):].strip()
        blocks: List[Dict[str, Any]] = [{
            "type": "text", "text": _PROMPT_STATIC,
            "cache_control": {"type": "ephemeral"},
        }]
        if dynamic:
            blocks.append({"type": "text", "text": dynamic})
        return blocks
    return [{"type": "text", "text": system,
             "cache_control": {"type": "ephemeral"}}]


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

    async def generate_once(
        self,
        prompt: str,
        system: str,
        max_tokens: int = 80,
        claude_direct: bool = False,
    ) -> Dict[str, Any]:
        """
        One-shot generation with a caller-supplied system prompt (D4 two-tier).

        claude_direct=False (ambient: lonely/startle/dark): Ollama first,
          Claude fallback — cheap, frequent, low-stakes.
        claude_direct=True (greet-by-name / conversation): straight to Claude —
          rare by construction, carries episodic recall a 1B model fumbles.
        """
        messages = [{"role": "user", "content": prompt}]
        t0 = time.monotonic()

        if not claude_direct and self._ollama_available is not False:
            try:
                result = await self._call_ollama(system, messages, max_tokens)
                if result:
                    result["latency_ms"] = int((time.monotonic() - t0) * 1000)
                    return result
            except Exception as e:
                log.warning("llm.once_ollama_failed", error=str(e)[:80])
                self._ollama_available = False

        if not token_budget.claude_allowed():
            log.warning("llm.once_budget_exhausted", day_total=token_budget.day_total)
            return {"text": "", "backend": "budget_exhausted", "latency_ms": 0, "tokens": 0}
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return {"text": "", "backend": "unavailable", "latency_ms": 0, "tokens": 0}
        try:
            result = await self._call_claude(system, messages, max_tokens)
            result["latency_ms"] = int((time.monotonic() - t0) * 1000)
            return result
        except Exception as e:
            log.warning("llm.once_claude_failed", error=str(e)[:80])
            return {"text": "", "backend": "unavailable", "latency_ms": 0, "tokens": 0}

    @staticmethod
    def _free_ram_mb() -> int:
        """Available RAM in MB (MemAvailable from /proc/meminfo)."""
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) // 1024
        except Exception:
            pass
        return 9999  # assume ok if unreadable

    # Ollama model (~800MB) needs ~1.2GB headroom to load safely
    OLLAMA_MIN_FREE_MB = 1200

    async def _call_ollama(
        self, system: str, messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        free_mb = self._free_ram_mb()
        if free_mb < self.OLLAMA_MIN_FREE_MB:
            log.warning("llm.ollama_skipped_low_ram", free_mb=free_mb, threshold=self.OLLAMA_MIN_FREE_MB)
            return None
        import httpx
        payload = {
            "model": self._ollama_model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": False,
            "keep_alive": "5m",   # unload after 5 min idle to free ~800MB RAM
            "options": {
                "temperature": 0.8,
                "num_predict": max_tokens or self.MAX_TOKENS,
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
        self, system: str, messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        import anthropic
        if self._anthropic_client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            self._anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)

        if not token_budget.try_reserve():
            raise RuntimeError("claude budget exhausted (KI-017 reservation)")
        try:
            response = await asyncio.wait_for(
                self._anthropic_client.messages.create(
                    model=self._claude_model,
                    max_tokens=max_tokens or self.MAX_TOKENS,
                    system=_system_to_blocks(system),
                    messages=messages,
                ),
                timeout=self.CLAUDE_TIMEOUT_S,
            )
        finally:
            token_budget.release()
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

        if not token_budget.try_reserve():
            log.warning("llm.stream_budget_exhausted",
                        day_total=token_budget.day_total)
            return

        import anthropic
        if self._anthropic_client is None:
            self._anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)

        buffer = ""
        try:
            async with self._anthropic_client.messages.stream(
                model=self._claude_model,
                max_tokens=self.MAX_TOKENS,
                system=_system_to_blocks(system_prompt),
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
                # Record usage against the daily budget (OQ-7a: this path was
                # uncounted — the main conversation path bypassed the limit).
                try:
                    final = await stream.get_final_message()
                    used = final.usage.input_tokens + final.usage.output_tokens
                    token_budget.record(used)
                    log.info("llm.stream_usage",
                             input_tokens=final.usage.input_tokens,
                             output_tokens=final.usage.output_tokens,
                             total=used)
                except Exception as e:
                    log.warning("llm.stream_usage_unrecorded", error=str(e)[:80])
        except Exception as e:
            log.warning("llm.stream_failed", error=str(e)[:80])
            # Fall back to non-streaming (_call_claude takes its own reservation;
            # ours is released in finally — briefly double-reserved, conservative)
            try:
                result = await self._call_claude(system_prompt, messages)
                if result and result.get("text"):
                    yield result["text"]
            except Exception:
                pass
        finally:
            token_budget.release()

    async def is_ollama_ready(self) -> bool:
        ready = await self._check_ollama()
        self._ollama_available = ready
        return ready


llm = LLMInterface()
