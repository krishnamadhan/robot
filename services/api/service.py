"""
Debug HTTP API — port 8000.
Runs inside the cosmo process as an async task.

Endpoints:
  GET  /state           → emotional state, attention, active behavior, last 3 events
  GET  /memory/recent   → last 20 episodic memory rows
  GET  /health          → uptime, cpu_temp, free_ram_mb
  POST /trigger/describe → capture frame + LLM vision describe (logs token cost)
"""

import asyncio
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from utils.logger import get_logger

log = get_logger(__name__)

app = FastAPI(title="Cosmo Debug API", docs_url="/docs")
_start_time = time.monotonic()

# Injected at startup by wire_state()
_state_ref: dict = {}
_events_ref: list = []


def wire_state(state: dict, events: list) -> None:
    """Call once at startup to give the API access to cosmo's live state dicts."""
    global _state_ref, _events_ref
    _state_ref = state
    _events_ref = events


# ── /health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    from utils.telemetry import LatencyTracker
    from core.personality import personality
    uptime = int(time.monotonic() - _start_time)
    cpu_temp = _read_cpu_temp()
    free_ram = _read_free_ram_mb()
    return {
        "status":           "ok",
        "uptime_s":         uptime,
        "cpu_temp_c":       cpu_temp,
        "free_ram_mb":      free_ram,
        "mood":             round(personality.state.mood, 2),
        "energy":           round(personality.state.energy, 2),
        "latency":          LatencyTracker.snapshot(),
    }


@app.get("/latency")
async def latency_report():
    from utils.telemetry import LatencyTracker
    return {"report": LatencyTracker.report(), "data": LatencyTracker.snapshot()}


def _read_cpu_temp() -> float:
    try:
        import subprocess
        r = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=2)
        return float(r.stdout.strip().replace("temp=", "").replace("'C", ""))
    except Exception:
        try:
            raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
            return round(int(raw) / 1000, 1)
        except Exception:
            return -1.0


def _read_free_ram_mb() -> int:
    try:
        data = Path("/proc/meminfo").read_text()
        for line in data.splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return -1


# ── /state ────────────────────────────────────────────────────────────────────

@app.get("/state")
async def state():
    s = _state_ref

    # Pull emotional state from personality if available
    emotion_state = {}
    try:
        from core.personality import personality
        ps = personality.state
        emotion_state = {
            "mood": round(ps.mood, 3),
            "energy": round(ps.energy, 3),
            "arousal": round(ps.arousal, 3),
            "attachment": round(ps.attachment, 3),
            "description": personality.describe(),
        }
    except Exception:
        emotion_state = s.get("emotion_state", {})

    # Attention target
    attention = {
        "person": s.get("person_name", "no one"),
        "face_conf": round(s.get("face_conf", 0.0), 3),
        "emotion": s.get("emotion", "—"),
        "distance_cm": s.get("distance_cm", None),
        "persons_visible": s.get("persons_visible", 0),
    }

    # Active behavior
    behavior = {
        "listen_state": s.get("listen_state", "—"),
        "nav_state": s.get("nav_state", "—"),
        "eye_expression": s.get("eye_expr", "—"),
        "last_response": s.get("last_response", "—"),
        "backend": s.get("backend", "—"),
        "latency_ms": s.get("latency_ms", 0),
    }

    return {
        "emotion": emotion_state,
        "attention": attention,
        "behavior": behavior,
        "last_events": list(_events_ref)[-3:] if _events_ref else [],
        "battery_pct": s.get("battery_pct", None),
    }


# ── /memory/recent ────────────────────────────────────────────────────────────

@app.get("/memory/recent")
async def memory_recent():
    try:
        from core.memory.episodic import episodic
        episodes = await episodic.retrieve(limit=20)
        return [
            {
                "ts": time.strftime("%Y-%m-%d %H:%M", time.localtime(e.timestamp)),
                "type": e.episode_type,
                "summary": e.summary,
                "person_id": e.person_id,
                "valence": round(e.emotional_valence, 2),
                "importance": round(e.importance, 2),
            }
            for e in episodes
        ]
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── /trigger/describe ─────────────────────────────────────────────────────────

@app.post("/trigger/describe")
async def trigger_describe():
    """Capture a camera frame and ask Claude to describe what it sees."""
    import base64, tempfile, os, subprocess

    # Capture frame
    frame_b64 = None
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        ret, frame = cap.read()
        cap.release()
        if ret:
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            frame_b64 = base64.b64encode(buf.tobytes()).decode()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Camera capture failed: {e}"})

    if not frame_b64:
        return JSONResponse(status_code=503, content={"error": "No frame captured"})

    # Call Claude vision (claude-sonnet-4-6 — haiku 4.5 has no vision)
    try:
        import anthropic, os as _os
        key = _os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return JSONResponse(status_code=503, content={"error": "No ANTHROPIC_API_KEY"})

        client = anthropic.Anthropic(api_key=key)
        t0 = time.monotonic()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}},
                    {"type": "text", "text": "Describe what you see in this robot camera frame in 2 sentences. Focus on people, objects, lighting."},
                ],
            }],
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        text = response.content[0].text.strip()
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        log.info("api.describe", tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms)
        return {
            "description": text,
            "model": "claude-sonnet-4-6",
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "latency_ms": latency_ms,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Server lifecycle ──────────────────────────────────────────────────────────

_server: uvicorn.Server | None = None


async def start() -> None:
    global _server
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="warning",
        access_log=False,
    )
    _server = uvicorn.Server(config)
    log.info("api.starting", port=8000)
    asyncio.create_task(_server.serve())


async def stop() -> None:
    if _server:
        _server.should_exit = True
        log.info("api.stopped")
