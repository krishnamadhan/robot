"""
Outbound WhatsApp notifications — Cosmo → owner DM.

Calls banteragent's internal /cosmo-notify endpoint (port 3099).
Rate-limited: 10 messages/day stored in SQLite; per-trigger cooldowns.
"""

import asyncio
import time
from pathlib import Path
from typing import Optional

import aiosqlite

from utils.logger import get_logger

log = get_logger(__name__)

_NOTIFY_URL = "http://127.0.0.1:3099/cosmo-notify"
_DB_PATH    = Path.home() / ".robot" / "memory" / "notifications.db"

DAILY_LIMIT = 10

# Per-trigger cooldowns (seconds)
COOLDOWNS: dict[str, float] = {
    "missing_you":     2 * 3600,   # 2 hr
    "found_something": 45 * 60,    # 45 min
    "discovery":       30 * 60,    # 30 min
    "morning":         20 * 3600,  # once per day-ish
    "curiosity":       3600,       # 1 hr
    "low_battery":     30 * 60,
}


class NotificationManager:
    """Send WhatsApp DMs to the owner from Cosmo.

    Call `await notifications.send(trigger, message)` anywhere in the robot.
    Returns True if the message was sent, False if rate-limited/offline.
    """

    def __init__(self) -> None:
        self._conn: Optional[aiosqlite.Connection] = None
        self._last_sent: dict[str, float] = {}

    async def initialize(self) -> None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(_DB_PATH))
        await self._conn.execute(
            """CREATE TABLE IF NOT EXISTS sent_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger   TEXT    NOT NULL,
                message   TEXT    NOT NULL,
                sent_at   REAL    NOT NULL
            )"""
        )
        await self._conn.commit()
        # Warm cooldown cache from today's sends
        cutoff = time.time() - 86400
        async with self._conn.execute(
            "SELECT trigger, MAX(sent_at) FROM sent_log WHERE sent_at > ? GROUP BY trigger",
            (cutoff,),
        ) as cur:
            async for row in cur:
                self._last_sent[row[0]] = row[1]
        log.info("notifications.initialized")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def send(self, trigger: str, message: str) -> bool:
        """Send a WhatsApp message to the owner.

        Returns False (and logs) if daily limit hit, cooldown active, or network error.
        """
        now = time.time()

        # Per-trigger cooldown
        cooldown = COOLDOWNS.get(trigger, 1800)
        last = self._last_sent.get(trigger, 0.0)
        if now - last < cooldown:
            log.debug("notifications.cooldown_skip", trigger=trigger,
                      remaining_s=int(cooldown - (now - last)))
            return False

        # Daily limit
        if self._conn:
            cutoff = now - 86400
            async with self._conn.execute(
                "SELECT COUNT(*) FROM sent_log WHERE sent_at > ?", (cutoff,)
            ) as cur:
                row = await cur.fetchone()
                if row and row[0] >= DAILY_LIMIT:
                    log.warning("notifications.daily_limit_hit")
                    return False

        # Send via banteragent internal endpoint
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _NOTIFY_URL,
                    json={"message": message},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        log.warning("notifications.send_failed", status=resp.status)
                        return False
        except Exception as exc:
            log.warning("notifications.send_error", error=str(exc))
            return False

        # Log success
        self._last_sent[trigger] = now
        if self._conn:
            await self._conn.execute(
                "INSERT INTO sent_log (trigger, message, sent_at) VALUES (?, ?, ?)",
                (trigger, message[:500], now),
            )
            await self._conn.commit()

        log.info("notifications.sent", trigger=trigger, preview=message[:60])
        return True

    def can_send(self, trigger: str) -> bool:
        """Cheap synchronous check — no DB, just in-memory cooldown cache."""
        cooldown = COOLDOWNS.get(trigger, 1800)
        return time.time() - self._last_sent.get(trigger, 0.0) >= cooldown


notifications = NotificationManager()
