"""
WhatsApp notifier — sends hardware events and decision logs to BanterAgent group.

Sends to the main Banter Squad group (BOT_GROUP_ID) via BanterAgent internal API.
Events sent:
  - Hardware connect/disconnect (camera, speaker, sensors)
  - Decision tree choices (which sensors drove which behavior)
  - Daily performance summary

Rate-limited: max 1 message per 30 seconds to avoid flooding.
"""

import asyncio
import json
import time
from collections import deque
from typing import Optional

import aiohttp

from core.event_bus import bus, EventType
from hardware.registry import hw_registry, HWStatus
from utils.logger import get_logger

log = get_logger(__name__)

# BanterAgent internal API
_BA_URL    = "http://localhost:3099"
_ADMIN_ENV = "ADMIN_WA_ID"    # admin's personal WhatsApp chat ID (e.g. 919876543210@c.us)

# Rate limiting
_MIN_INTERVAL_S   = 30
_last_sent: float = 0.0

# Decision log buffer — flushed to WA in daily summary
_decision_log: deque = deque(maxlen=500)


async def _send(message: str) -> bool:
    """Send a WhatsApp message via BanterAgent. Rate-limited."""
    global _last_sent
    now = time.monotonic()
    if now - _last_sent < _MIN_INTERVAL_S:
        log.debug("notifier.rate_limited", seconds_since=round(now - _last_sent, 1))
        return False

    try:
        import os
        admin_id = os.environ.get(_ADMIN_ENV, "")
        if not admin_id:
            log.warning("notifier.no_admin_id")
            return False

        payload: dict = {"message": message, "to": admin_id}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_BA_URL}/notify",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                ok = resp.status == 200
                if ok:
                    _last_sent = now
                    log.info("notifier.sent", preview=message[:60])
                else:
                    log.warning("notifier.failed", status=resp.status)
                return ok
    except Exception as e:
        log.warning("notifier.error", error=str(e)[:80])
        return False


# ── Hardware event notifications ─────────────────────────────────────────────

_hw_snapshot: dict = {}


async def check_hardware_changes() -> None:
    """Compare current hardware registry to last snapshot, notify on changes."""
    current = hw_registry.as_dict()

    for name, info in current.items():
        prev = _hw_snapshot.get(name)
        status = info.get("status", "unknown")

        if prev is None:
            # First time seeing this component
            _hw_snapshot[name] = info
            continue

        prev_status = prev.get("status", "unknown")
        if status != prev_status:
            # Status changed — notify
            _hw_snapshot[name] = info
            emoji = "✅" if status == "real" else "⚠️" if status == "mock" else "❌"
            msg = (
                f"🤖 *Cosmo Hardware Change*\n"
                f"{emoji} *{name}*: `{prev_status}` → `{status}`\n"
                f"Reason: {info.get('reason', '—')}\n"
                f"Time: {_timestamp()}"
            )
            await _send(msg)

    # Detect removed components (in snapshot but not in current)
    for name in list(_hw_snapshot.keys()):
        if name not in current:
            del _hw_snapshot[name]
            await _send(
                f"🤖 *Cosmo Hardware Removed*\n"
                f"❌ *{name}* no longer detected\n"
                f"Time: {_timestamp()}"
            )


# ── Decision logging ─────────────────────────────────────────────────────────

def log_decision(
    trigger: str,
    sensors_active: list[str],
    behavior_chosen: str,
    reason: str,
    movement_capability: Optional[str] = None,
) -> None:
    """
    Log a behavior decision for review.
    Called by behavior tree whenever a significant choice is made.
    """
    entry = {
        "ts": time.time(),
        "trigger": trigger,
        "sensors": sensors_active,
        "behavior": behavior_chosen,
        "reason": reason,
        "capability": movement_capability,
    }
    _decision_log.append(entry)
    log.info("decision.logged", trigger=trigger, behavior=behavior_chosen,
             sensors=sensors_active)


async def send_decision_summary() -> None:
    """
    Send a compact decision summary to WhatsApp.
    Called every hour or on-demand.
    """
    if not _decision_log:
        return

    recent = list(_decision_log)[-10:]  # last 10 decisions
    lines = ["🤖 *Cosmo Decision Log* (last 10 choices)\n"]

    for d in recent:
        ts = time.strftime("%H:%M", time.localtime(d["ts"]))
        sensors = ", ".join(d["sensors"]) if d["sensors"] else "none"
        lines.append(
            f"• `{ts}` *{d['trigger']}* → *{d['behavior']}*\n"
            f"  Sensors: {sensors}\n"
            f"  Why: {d['reason']}"
        )

    await _send("\n".join(lines))


async def send_daily_summary() -> None:
    """Send a daily health + performance summary."""
    from core.personality import personality
    from hardware.registry import hw_registry

    ps = personality.state
    real = hw_registry.real
    mocked = hw_registry.mocked

    total_decisions = len(_decision_log)
    behavior_counts: dict = {}
    for d in _decision_log:
        b = d["behavior"]
        behavior_counts[b] = behavior_counts.get(b, 0) + 1

    top_behaviors = sorted(behavior_counts.items(), key=lambda x: -x[1])[:5]
    top_str = "\n".join(f"  {b}: {n}x" for b, n in top_behaviors)

    msg = (
        f"🤖 *Cosmo Daily Report* — {_timestamp()}\n\n"
        f"*Mood:* {personality.describe()}\n"
        f"*Energy:* {round(ps.energy * 100)}% | *Mood:* {round(ps.mood, 2)}\n\n"
        f"*Hardware:*\n"
        f"  ✅ Real: {', '.join(real) or 'none'}\n"
        f"  ⚠️ Mocked: {', '.join(mocked) or 'none'}\n\n"
        f"*Decisions today:* {total_decisions}\n"
        f"*Top behaviors:*\n{top_str}"
    )
    await _send(msg)


# ── Background task ───────────────────────────────────────────────────────────

async def start() -> None:
    """Start the notifier background tasks."""
    # Initialize snapshot from current registry
    _hw_snapshot.update(hw_registry.as_dict())

    asyncio.create_task(_hardware_monitor_loop(), name="hw_notifier")
    asyncio.create_task(_hourly_decision_summary_loop(), name="decision_summary")
    log.info("notifier.started")


async def _hardware_monitor_loop() -> None:
    """Check hardware state every 60 seconds."""
    while True:
        await asyncio.sleep(60)
        await check_hardware_changes()


async def _hourly_decision_summary_loop() -> None:
    """Send decision summary every hour."""
    while True:
        await asyncio.sleep(3600)
        await send_decision_summary()


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M")
