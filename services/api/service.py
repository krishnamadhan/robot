"""
Debug HTTP API — port 8000.
Runs inside the cosmo process as an async task.

Endpoints:
  GET  /state           → emotional state, attention, active behavior, last 3 events
  GET  /memory/recent   → last 20 episodic memory rows
  GET  /health          → uptime, cpu_temp, free_ram_mb
  POST /trigger/describe → capture frame + LLM vision describe (logs token cost)
  GET  /camera/snapshot  → latest frame as JPEG from the live camera pipeline
  GET  /camera/color     → current software colour-correction config
  POST /camera/color     → update colour-correction config live (+ writes color.toml)
"""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse

from utils.logger import get_logger

log = get_logger(__name__)

app = FastAPI(title="Cosmo Debug API", docs_url="/docs")


def _require_token(authorization: Optional[str] = Header(default=None)) -> None:
    """Bearer-token guard for mutation endpoints.

    If ROBOT_API_TOKEN env var is not set, all requests pass (dev mode).
    If set, requests must include `Authorization: Bearer <token>`.
    """
    expected = os.environ.get("ROBOT_API_TOKEN", "")
    if not expected:
        return  # dev mode — no token required
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header required")
    if authorization[len("Bearer "):].strip() != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


_AuthRequired = Depends(_require_token)
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
    total_ram = _read_total_ram_mb()
    disk_pct = _read_disk_pct()
    return {
        "status":           "ok",
        "uptime_s":         uptime,
        "cpu_temp_c":       cpu_temp,
        "free_ram_mb":      free_ram,
        "total_ram_mb":     total_ram,
        "disk_pct":         disk_pct,
        "mood":             round(personality.state.mood, 2),
        "energy":           round(personality.state.energy, 2),
        "latency":          LatencyTracker.snapshot(),
    }


@app.get("/latency")
async def latency_report():
    from utils.telemetry import LatencyTracker
    return {"report": LatencyTracker.report(), "data": LatencyTracker.snapshot()}


@app.get("/hardware")
async def hardware_status():
    from hardware.registry import hw_registry
    components = hw_registry.as_dict()
    return {
        "real":       hw_registry.real,
        "mocked":     hw_registry.mocked,
        "errors":     hw_registry.errors,
        "components": components,
    }


# ── /battery ──────────────────────────────────────────────────────────────────

@app.get("/battery")
async def battery():
    try:
        from hardware.sensor_manager import sensor_manager
        data = sensor_manager.get_battery()
        return {
            "percent":  data.get("percent"),
            "voltage":  data.get("voltage"),
            "charging": data.get("charging", False),
            "mock":     data.get("mock", False),
            "source":   "sensor_manager",
        }
    except Exception as e:
        return JSONResponse(status_code=503, content={"error": str(e)})


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


def _read_total_ram_mb() -> int:
    try:
        data = Path("/proc/meminfo").read_text()
        for line in data.splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 8192


def _read_disk_pct() -> int:
    try:
        import shutil
        usage = shutil.disk_usage("/")
        return round(usage.used / usage.total * 100)
    except Exception:
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

        # KI-014: async client + timeout — the previous sync client blocked the
        # entire :8000 event loop (incl. /led) for the duration of the call,
        # indefinitely on a hung connection.
        client = anthropic.AsyncAnthropic(api_key=key, timeout=30.0)
        t0 = time.monotonic()
        response = await client.messages.create(
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


# ── /camera/snapshot · /camera/color ─────────────────────────────────────────

@app.get("/camera/snapshot")
async def camera_snapshot():
    """Return the latest camera frame as a JPEG image (from the live pipeline)."""
    import base64
    import cv2
    try:
        from perception.vision.camera import camera as _cam
        frame = _cam.latest_frame
        if frame is None:
            return JSONResponse(status_code=503, content={"error": "No frame available — camera not running"})
        _, buf = cv2.imencode(".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        jpeg_b64 = base64.b64encode(buf.tobytes()).decode()
        return {
            "jpeg_b64": jpeg_b64,
            "frame_id": frame.frame_id,
            "age_ms": round(frame.age_ms(), 1),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/camera/color")
async def camera_color_get():
    """Return the current software colour-correction config."""
    try:
        from perception.vision.camera import color_config
        return dict(color_config)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/camera/color")
async def camera_color_set(request: Request, _: None = _AuthRequired):
    """
    Update colour-correction config live and persist to config/color.toml.
    Only recognised keys are updated; unknown keys are ignored.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})
    try:
        from perception.vision.camera import color_config, camera as _cam
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    allowed = {"hw_r", "hw_b", "sw_r", "sw_g", "sw_b", "saturation", "shadow", "ev"}
    updated = {}
    for k, v in body.items():
        if k in allowed:
            try:
                color_config[k] = float(v)
                updated[k] = float(v)
            except (TypeError, ValueError):
                pass

    # Push ISP gains immediately so next frame uses them.
    try:
        _cam._backend.apply_hw_gains()  # type: ignore[union-attr]
    except Exception:
        pass

    # Persist to color.toml
    toml_path = Path(__file__).parent.parent.parent / "config" / "color.toml"
    try:
        import tomlkit as _tk
        doc = _tk.document()
        for k in allowed:
            if k in color_config:
                doc.add(k, color_config[k])
        from utils.atomic_write import atomic_write_text
        atomic_write_text(toml_path, _tk.dumps(doc))
        persisted = True
    except Exception:
        persisted = False

    log.info("api.camera_color_set", updated=updated, persisted=persisted)
    return {"ok": True, "updated": updated, "persisted": persisted, "current": dict(color_config)}


# ── /mind/on · /mind/off ─────────────────────────────────────────────────────

@app.post("/mind/on")
async def mind_on(_: None = _AuthRequired):
    try:
        from cognition.mind import cosmo_mind
        cosmo_mind.enable()
        return {"mind": "enabled"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/mind/off")
async def mind_off(_: None = _AuthRequired):
    try:
        from cognition.mind import cosmo_mind
        cosmo_mind.disable()
        return {"mind": "disabled", "day_total": cosmo_mind._budget.day_total}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── /session ─────────────────────────────────────────────────────────────────

@app.get("/session")
async def session_state():
    path = Path.home() / ".claude/session-state.md"
    try:
        content = path.read_text()
        lines = content.splitlines()
        # Extract key fields for quick display
        updated = next((l.replace("**Updated:**", "").strip() for l in lines if "**Updated:**" in l), "unknown")
        focus   = next((l.replace("**Session focus:**", "").strip() for l in lines if "**Session focus:**" in l), "")
        # Find "Next Priority" section
        next_p = ""
        in_next = False
        for l in lines:
            if l.startswith("## Next Priority"):
                in_next = True
                continue
            if in_next:
                if l.startswith("## "):
                    break
                if l.strip():
                    next_p = l.strip()
                    break
        return {"content": content, "updated": updated, "focus": focus, "next": next_p, "exists": True}
    except FileNotFoundError:
        return {"content": "", "updated": "never", "focus": "", "next": "No session-state.md found", "exists": False}


# ── /pm2 ──────────────────────────────────────────────────────────────────────

@app.get("/pm2")
async def pm2_status():
    try:
        r = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=5)
        procs = json.loads(r.stdout)
        result = []
        for p in procs:
            env = p.get("pm2_env", {})
            monit = p.get("monit", {})
            uptime_s = int((time.time() * 1000 - env.get("pm_uptime", time.time() * 1000)) / 1000)
            result.append({
                "name":      p.get("name"),
                "status":    env.get("status"),
                "pid":       p.get("pid"),
                "uptime_s":  max(0, uptime_s),
                "memory_mb": round(monit.get("memory", 0) / 1024 / 1024, 1),
                "cpu_pct":   monit.get("cpu", 0),
                "restarts":  env.get("restart_time", 0),
            })
        return result
    except Exception as e:
        return JSONResponse(status_code=503, content={"error": str(e)})


# ── /git/log ──────────────────────────────────────────────────────────────────

@app.get("/git/log")
async def git_log():
    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "-8"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent.parent),
            timeout=5,
        )
        return {"commits": [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]}
    except Exception as e:
        return {"commits": [], "error": str(e)}


# ── /dashboard ────────────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Phone-friendly live dashboard. No frames sent off-device."""
    token = os.environ.get("ROBOT_API_TOKEN", "")
    html = _DASHBOARD_HTML.replace("__ROBOT_TOKEN__", token)
    return HTMLResponse(content=html)




_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Cosmo</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, -apple-system, sans-serif; background: #0d1117; color: #e6edf3;
       padding: 10px; max-width: 540px; margin: auto; }
h1 { font-size: 1.2rem; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: #3fb950; display: inline-block;
       box-shadow: 0 0 6px #3fb950; }
.dot.off { background: #8b949e; box-shadow: none; }
section { background: #161b22; border-radius: 10px; padding: 12px; margin-bottom: 8px; }
section h2 { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
             color: #8b949e; margin-bottom: 8px; }
.row { display: flex; justify-content: space-between; align-items: center;
       padding: 4px 0; font-size: 0.88rem; border-bottom: 1px solid #21262d; }
.row:last-child { border-bottom: none; }
.label { color: #8b949e; }
.val { font-weight: 600; font-family: monospace; }
.bar-outer { background: #21262d; border-radius: 4px; height: 8px; width: 100px;
             overflow: hidden; display: inline-block; vertical-align: middle; }
.bar-inner { height: 100%; border-radius: 4px; background: #3fb950;
             transition: width 0.5s ease; }
.bar-inner.warn { background: #d29922; }
.bar-inner.crit { background: #f85149; }
.badge { font-size: 0.7rem; padding: 2px 7px; border-radius: 10px;
         font-weight: 600; white-space: nowrap; }
.badge.real { background: #1a3326; color: #3fb950; border: 1px solid #2d5a3d; }
.badge.mock { background: #332a0d; color: #d29922; border: 1px solid #5a4a1a; }
.badge.error { background: #3d1515; color: #f85149; border: 1px solid #6d2020; }
.badge.unknown { background: #21262d; color: #8b949e; border: 1px solid #30363d; }
.hw-row { display: flex; justify-content: space-between; align-items: center;
          padding: 3px 0; border-bottom: 1px solid #21262d; font-size: 0.82rem; }
.hw-row:last-child { border-bottom: none; }
.hw-name { color: #c9d1d9; }
.cam-wrap { background: #000; border-radius: 8px; overflow: hidden; text-align: center;
            margin-top: 6px; }
.cam-wrap img { width: 100%; max-width: 480px; display: block; }
.cam-wrap .cam-off { color: #8b949e; font-size: 0.8rem; padding: 20px; }
.mem-item { padding: 5px 0; border-bottom: 1px solid #21262d; font-size: 0.82rem; }
.mem-item:last-child { border-bottom: none; }
.mem-summary { color: #c9d1d9; }
.mem-meta { color: #8b949e; font-size: 0.72rem; margin-top: 1px; }
.valence-pos { color: #3fb950; }
.valence-neg { color: #f85149; }
.valence-neu { color: #8b949e; }
.log-box { background: #0d1117; border-radius: 6px; padding: 8px;
           font-family: monospace; font-size: 0.72rem; line-height: 1.5;
           color: #8b949e; max-height: 200px; overflow-y: auto; margin-top: 4px; }
.log-line { white-space: pre-wrap; word-break: break-all; }
.log-line.warn { color: #d29922; }
.log-line.error { color: #f85149; }
.log-line.info { color: #58a6ff; }
.btn-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 4px; }
.btn-grid.motor { grid-template-columns: 1fr 1fr 1fr; }
button { background: #21262d; color: #e6edf3; border: 1px solid #30363d;
         border-radius: 8px; padding: 10px 8px; font-size: 0.85rem; cursor: pointer;
         transition: background 0.15s; width: 100%; }
button:hover { background: #30363d; }
button:active { background: #21262d; transform: scale(0.97); }
button.green { background: #1a3326; border-color: #2d5a3d; color: #3fb950; }
button.green:hover { background: #2d5a3d; }
button.red { background: #3d1515; border-color: #6d2020; color: #f85149; }
button.red:hover { background: #6d2020; }
button.blue { background: #0d2040; border-color: #1a4080; color: #58a6ff; }
button.blue:hover { background: #1a4080; }
.ts-bar { display: flex; justify-content: space-between; align-items: center;
          font-size: 0.72rem; color: #8b949e; margin-top: 8px; padding: 0 2px; }
.motor-pad { display: grid; grid-template-columns: 1fr 1fr 1fr;
             grid-template-rows: auto auto auto; gap: 6px; }
.motor-pad button { min-height: 44px; font-size: 1.1rem; }
.motor-pad .center { grid-column: 2; }
.motor-stop { grid-column: 1 / -1; margin-top: 2px; }
.bar-section { display: flex; align-items: center; gap: 6px; }
</style>
</head>
<body>
<h1><span class="dot off" id="status-dot"></span> Cosmo
  <span style="font-weight:400;font-size:0.8rem;color:#8b949e;margin-left:auto" id="mind-status"></span>
</h1>

<!-- Session Context -->
<section>
  <h2>Session Context</h2>
  <div id="sess-next" style="font-size:0.85rem;color:#3fb950;font-weight:600;margin-bottom:6px;line-height:1.4"></div>
  <div class="row"><span class="label">Last updated</span><span class="val" id="sess-ts">—</span></div>
  <div class="row"><span class="label">Focus</span><span class="val" id="sess-focus" style="text-align:right;max-width:220px;font-size:0.8rem">—</span></div>
  <details style="margin-top:8px">
    <summary style="font-size:0.72rem;color:#8b949e;cursor:pointer">Full session notes ▸</summary>
    <pre id="sess-full" style="margin-top:6px;font-size:0.7rem;color:#8b949e;white-space:pre-wrap;word-break:break-word;line-height:1.5"></pre>
  </details>
</section>

<!-- Pi Services -->
<section>
  <h2>Pi Services</h2>
  <div id="pm2-list"><span style="color:#8b949e;font-size:0.8rem">Loading…</span></div>
  <div style="margin-top:10px">
    <div style="font-size:0.72rem;color:#8b949e;margin-bottom:4px">Recent commits (robot)</div>
    <div id="git-log" class="log-box" style="max-height:90px"></div>
  </div>
</section>

<!-- Camera -->
<section>
  <h2>Camera</h2>
  <div class="cam-wrap" id="cam-wrap">
    <div class="cam-off" id="cam-off">Loading stream…</div>
    <img id="cam-img" src="" alt="Live stream" style="display:none"
         onerror="this.style.display='none';document.getElementById('cam-off').style.display='block'"
         onload="this.style.display='block';document.getElementById('cam-off').style.display='none'">
  </div>
</section>

<!-- Personality -->
<section>
  <h2>Personality State</h2>
  <div class="row">
    <span class="label">Mood</span>
    <span class="bar-section">
      <span class="val" id="mood">—</span>
      <span class="bar-outer"><div class="bar-inner" id="mood-bar" style="width:50%"></div></span>
    </span>
  </div>
  <div class="row">
    <span class="label">Energy</span>
    <span class="bar-section">
      <span class="val" id="energy">—</span>
      <span class="bar-outer"><div class="bar-inner" id="energy-bar" style="width:70%"></div></span>
    </span>
  </div>
  <div class="row">
    <span class="label">Arousal</span>
    <span class="bar-section">
      <span class="val" id="arousal">—</span>
      <span class="bar-outer"><div class="bar-inner" id="arousal-bar" style="width:50%"></div></span>
    </span>
  </div>
  <div class="row">
    <span class="label">Attachment</span>
    <span class="bar-section">
      <span class="val" id="attachment">—</span>
      <span class="bar-outer"><div class="bar-inner" id="attachment-bar" style="width:60%"></div></span>
    </span>
  </div>
  <div class="row"><span class="label">Description</span>
    <span class="val" id="description" style="text-align:right;max-width:200px;font-size:0.8rem"></span></div>
</section>

<!-- Attention -->
<section>
  <h2>Current Attention</h2>
  <div class="row"><span class="label">Sees</span><span class="val" id="person">—</span></div>
  <div class="row"><span class="label">Emotion</span><span class="val" id="emotion">—</span></div>
  <div class="row"><span class="label">Distance</span><span class="val" id="distance">—</span></div>
  <div class="row"><span class="label">Gesture</span><span class="val" id="gesture">—</span></div>
  <div class="row"><span class="label">Persons visible</span><span class="val" id="visible">—</span></div>
</section>

<!-- System Health -->
<section>
  <h2>System Health</h2>
  <div class="row"><span class="label">Uptime</span><span class="val" id="uptime">—</span></div>
  <div class="row"><span class="label">CPU Temp</span><span class="val" id="temp">—</span></div>
  <div class="row">
    <span class="label">RAM</span>
    <span class="bar-section">
      <span class="val" id="ram">—</span>
      <span class="bar-outer"><div class="bar-inner" id="ram-bar" style="width:20%"></div></span>
    </span>
  </div>
  <div class="row"><span class="label">Disk</span><span class="val" id="disk">—</span></div>
  <div class="row"><span class="label">Battery</span><span class="val" id="battery">—</span></div>
  <div class="row"><span class="label">Audio state</span><span class="val" id="listen">—</span></div>
</section>

<!-- Token Budget -->
<section>
  <h2>Token Budget (Today)</h2>
  <div class="row">
    <span class="label">Used</span>
    <span class="bar-section">
      <span class="val" id="budget-used">—</span>
      <span class="bar-outer"><div class="bar-inner" id="budget-bar" style="width:0%"></div></span>
    </span>
  </div>
  <div class="row"><span class="label">Remaining</span><span class="val" id="budget-rem">—</span></div>
  <div class="row"><span class="label">Claude allowed</span><span class="val" id="budget-ok">—</span></div>
</section>

<!-- Hardware -->
<section>
  <h2>Hardware Components</h2>
  <div id="hw-list"><span style="color:#8b949e;font-size:0.8rem">Loading…</span></div>
</section>

<!-- Motor Control -->
<section>
  <h2>Motor Control</h2>
  <div style="margin-bottom:8px;font-size:0.78rem;color:#8b949e" id="motor-status">Status: —</div>
  <div class="motor-pad">
    <div></div>
    <button onclick="motorCmd('forward')" class="blue">▲</button>
    <div></div>
    <button onclick="motorCmd('left')" class="blue">◀</button>
    <button onclick="motorCmd('stop')" class="red center">■</button>
    <button onclick="motorCmd('right')" class="blue">▶</button>
    <div></div>
    <button onclick="motorCmd('back')" class="blue">▼</button>
    <div></div>
  </div>
</section>

<!-- Brain Controls -->
<section>
  <h2>Brain Controls</h2>
  <div class="btn-grid">
    <button class="green" onclick="postCmd('/mind/on')">🧠 Mind ON</button>
    <button class="red" onclick="postCmd('/mind/off')">Mind OFF</button>
    <button onclick="postCmd('/sound/mute?seconds=3600')">🔇 Mute 1h</button>
    <button onclick="postCmd('/sound/unmute')">🔊 Unmute</button>
  </div>
  <div style="margin-top:8px">
    <div style="font-size:0.72rem;color:#8b949e;margin-bottom:6px">Test Triggers</div>
    <div class="btn-grid">
      <button onclick="postCmd('/trigger/face_seen')">👤 Face Seen</button>
      <button onclick="postCmd('/trigger/touched')">👆 Touched</button>
      <button onclick="postCmd('/trigger/emotion_happy')">😄 Happy</button>
      <button onclick="postCmd('/trigger/describe')">📷 Describe</button>
    </div>
  </div>
</section>

<!-- Memories -->
<section>
  <h2>Recent Memories</h2>
  <div class="memories" id="memories"><span style="color:#8b949e">Loading…</span></div>
</section>

<!-- Live Logs -->
<section>
  <h2>Live Log Tail</h2>
  <div class="log-box" id="log-box">Loading…</div>
</section>

<div class="ts-bar">
  <span id="refreshed">—</span>
  <button onclick="doRefresh()" style="width:auto;padding:4px 12px;font-size:0.75rem">↺ Refresh</button>
</div>

<script>
const BASE = '';
const ROBOT_TOKEN = '__ROBOT_TOKEN__';
const AUTH_HDR = ROBOT_TOKEN ? {'Authorization': 'Bearer ' + ROBOT_TOKEN, 'Content-Type': 'application/json'} : {'Content-Type': 'application/json'};
let camHost = window.location.hostname;

// Set camera stream src once
(function() {
  const img = document.getElementById('cam-img');
  img.src = 'http://' + camHost + ':8080';
})();

function fmt(v, dp=2) {
  return (typeof v === 'number') ? v.toFixed(dp) : (v ?? '—');
}

function setBar(id, pct) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.width = Math.max(0, Math.min(100, pct)) + '%';
  el.className = 'bar-inner' + (pct > 80 ? ' crit' : pct > 60 ? ' warn' : '');
}

function moodPct(v) { return ((v + 1) / 2) * 100; }   // -1..1 → 0..100%
function pct01(v) { return (v || 0) * 100; }            // 0..1 → 0..100%

async function refresh() {
  try {
    const [stR, hlR, hwR, memR, budR, logR, sessR, pm2R, gitR] = await Promise.all([
      fetch(BASE+'/state').then(r=>r.json()),
      fetch(BASE+'/health').then(r=>r.json()),
      fetch(BASE+'/hardware').then(r=>r.json()),
      fetch(BASE+'/memory/recent').then(r=>r.json()),
      fetch(BASE+'/budget').then(r=>r.json()),
      fetch(BASE+'/logs/tail').then(r=>r.json()),
      fetch(BASE+'/session').then(r=>r.json()).catch(()=>({})),
      fetch(BASE+'/pm2').then(r=>r.json()).catch(()=>[]),
      fetch(BASE+'/git/log').then(r=>r.json()).catch(()=>({commits:[]})),
    ]);

    // Session Context
    if (sessR && sessR.exists !== false) {
      document.getElementById('sess-next').textContent = sessR.next || '—';
      document.getElementById('sess-ts').textContent   = sessR.updated || '—';
      document.getElementById('sess-focus').textContent = sessR.focus || '—';
      document.getElementById('sess-full').textContent  = sessR.content || '';
    }

    // PM2 Services
    const pm2El = document.getElementById('pm2-list');
    const statusColor = s => s === 'online' ? '#3fb950' : (s === 'stopping' ? '#d29922' : '#f85149');
    if (Array.isArray(pm2R) && pm2R.length > 0) {
      pm2El.innerHTML = pm2R.map(p => {
        const ut = p.uptime_s || 0;
        const utStr = ut > 3600 ? Math.floor(ut/3600)+'h' : Math.floor(ut/60)+'m';
        const sc = statusColor(p.status);
        return `<div class="hw-row">
          <span class="hw-name">${p.name}</span>
          <span style="display:flex;gap:6px;align-items:center;font-size:0.75rem">
            <span style="color:${sc};font-weight:600">${p.status}</span>
            <span style="color:#8b949e">${p.memory_mb}MB</span>
            <span style="color:#8b949e">${utStr}</span>
            ${p.restarts > 0 ? `<span style="color:#d29922">↺${p.restarts}</span>` : ''}
          </span>
        </div>`;
      }).join('');
    } else {
      pm2El.innerHTML = '<span style="color:#8b949e;font-size:0.8rem">PM2 unavailable</span>';
    }

    // Git log
    const gitEl = document.getElementById('git-log');
    const commits = (gitR && gitR.commits) || [];
    gitEl.innerHTML = commits.length
      ? commits.map(c => `<div class="log-line">${escHtml(c)}</div>`).join('')
      : '<span style="color:#8b949e">No commits</span>';

    document.getElementById('status-dot').className = 'dot';

    // Mind status
    const mindOn = (stR.behavior || {}).mind_enabled;
    document.getElementById('mind-status').textContent =
      mindOn === true ? '🧠 Mind ON' : (mindOn === false ? 'Mind OFF' : '');

    // Personality
    const e = stR.emotion || {};
    document.getElementById('mood').textContent = fmt(e.mood);
    setBar('mood-bar', moodPct(e.mood || 0));
    document.getElementById('energy').textContent = fmt(e.energy);
    setBar('energy-bar', pct01(e.energy));
    document.getElementById('arousal').textContent = fmt(e.arousal);
    setBar('arousal-bar', pct01(e.arousal));
    document.getElementById('attachment').textContent = fmt(e.attachment);
    setBar('attachment-bar', pct01(e.attachment));
    document.getElementById('description').textContent = e.description || '—';

    // Attention
    const a = stR.attention || {};
    document.getElementById('person').textContent = a.person || '—';
    document.getElementById('emotion').textContent = a.emotion || '—';
    document.getElementById('distance').textContent =
      a.distance_cm != null ? a.distance_cm + ' cm' : '—';
    document.getElementById('gesture').textContent = a.gesture || '—';
    document.getElementById('visible').textContent = a.persons_visible ?? '—';

    // System
    const ut = hlR.uptime_s || 0;
    document.getElementById('uptime').textContent =
      Math.floor(ut/3600) + 'h ' + Math.floor((ut%3600)/60) + 'm';
    document.getElementById('temp').textContent =
      hlR.cpu_temp_c != null ? hlR.cpu_temp_c + '°C' : '—';
    const freeMB = hlR.free_ram_mb || 0;
    const totalMB = hlR.total_ram_mb || 8192;
    document.getElementById('ram').textContent =
      freeMB + 'MB free / ' + Math.round(totalMB/1024) + 'GB';
    const usedPct = Math.round((1 - freeMB/totalMB)*100);
    setBar('ram-bar', usedPct);
    document.getElementById('disk').textContent =
      hlR.disk_pct != null ? hlR.disk_pct + '% used' : '—';
    document.getElementById('battery').textContent =
      stR.battery_pct != null ? stR.battery_pct + '%' : '—';
    document.getElementById('listen').textContent =
      (stR.behavior || {}).listen_state || '—';

    // Budget
    if (budR.limit) {
      document.getElementById('budget-used').textContent =
        (budR.day_total||0).toLocaleString() + ' / ' + budR.limit.toLocaleString();
      setBar('budget-bar', budR.pct_used || 0);
      document.getElementById('budget-rem').textContent =
        (budR.remaining||0).toLocaleString();
      document.getElementById('budget-ok').textContent =
        budR.claude_allowed ? '✅ Yes' : '❌ Budget exhausted';
    }

    // Hardware
    const hwEl = document.getElementById('hw-list');
    const comps = hwR.components || {};
    const entries = Object.entries(comps);
    if (entries.length === 0) {
      hwEl.innerHTML = '<span style="color:#8b949e;font-size:0.8rem">No components registered</span>';
    } else {
      hwEl.innerHTML = entries.map(([name, info]) => {
        const s = info.status || 'unknown';
        return `<div class="hw-row">
          <span class="hw-name">${name}</span>
          <span class="badge ${s}">${s}${info.mock ? ' (mock)' : ''}</span>
        </div>`;
      }).join('');
    }

    // Memories
    const memEl = document.getElementById('memories');
    const mems = Array.isArray(memR) ? memR : (memR.memories || []);
    if (mems.length === 0) {
      memEl.innerHTML = '<span style="color:#8b949e;font-size:0.8rem">No memories yet</span>';
    } else {
      memEl.innerHTML = mems.slice(0, 10).map(m => {
        const v = m.valence || 0;
        const vc = v > 0.2 ? 'valence-pos' : (v < -0.2 ? 'valence-neg' : 'valence-neu');
        const vi = v > 0.2 ? '😊' : (v < -0.2 ? '😟' : '😐');
        return `<div class="mem-item">
          <div class="mem-summary">${vi} ${m.summary || '—'}</div>
          <div class="mem-meta">${m.ts || ''} · ${m.type || ''} · <span class="${vc}">val=${v.toFixed(2)}</span></div>
        </div>`;
      }).join('');
    }

    // Logs
    const logEl = document.getElementById('log-box');
    const lines = logR.lines || [];
    if (lines.length === 0) {
      logEl.innerHTML = '<span style="color:#8b949e">No log lines found</span>';
    } else {
      logEl.innerHTML = lines.slice(-20).map(line => {
        let cls = 'log-line';
        const llow = line.toLowerCase();
        if (llow.includes('[warn') || llow.includes('warning')) cls += ' warn';
        else if (llow.includes('[error') || llow.includes('error') || llow.includes('critical')) cls += ' error';
        else if (llow.includes('[info') || llow.includes('info')) cls += ' info';
        return `<div class="${cls}">${escHtml(line)}</div>`;
      }).join('');
      logEl.scrollTop = logEl.scrollHeight;
    }

    document.getElementById('refreshed').textContent =
      'Updated ' + new Date().toLocaleTimeString();

  } catch(err) {
    document.getElementById('status-dot').className = 'dot off';
    document.getElementById('refreshed').textContent = 'Offline — ' + err.message;
  }
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function postCmd(path, body) {
  try {
    const opts = {method:'POST', headers: AUTH_HDR};
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(BASE + path, opts);
    const j = await r.json();
    const msg = j.ok ? '✅ ' + (j.action || 'done') : '❌ ' + JSON.stringify(j);
    document.getElementById('motor-status').textContent = msg;
    await doRefresh();
  } catch(e) {
    document.getElementById('motor-status').textContent = '❌ ' + e.message;
  }
}

async function motorCmd(dir) {
  document.getElementById('motor-status').textContent = 'Sending ' + dir + '…';
  await postCmd('/motor/' + dir);
}

async function doRefresh() {
  document.getElementById('refreshed').textContent = 'Refreshing…';
  await refresh();
}

// Auto-refresh every 3 seconds
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>"""


# ── /sound/mute · /sound/unmute ──────────────────────────────────────────────

@app.post("/sound/mute")
async def sound_mute(seconds: int = 3600, _: None = _AuthRequired):
    """Mute Cosmo's sounds for N seconds (default 1 hour)."""
    try:
        from expression.sounds import sounds
        sounds.mute(seconds)
        return {"muted": True, "seconds": seconds}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/sound/unmute")
async def sound_unmute(_: None = _AuthRequired):
    """Unmute Cosmo immediately."""
    try:
        from expression.sounds import sounds
        sounds.mute(0)
        return {"muted": False}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Motor controls ───────────────────────────────────────────────────────────

async def _timed_move(coro, duration: float) -> None:
    await coro
    await asyncio.sleep(duration)
    from hardware.motors import motor_controller
    await motor_controller.stop()


@app.post("/led")
async def led_control(request: Request, _: None = _AuthRequired):
    """LEDDMX BLE strip control.

    Body: {"cmd": "color"|"named"|"bright"|"on"|"off"|"pattern"|"music"|"temp",
           "value": "<name>" | [r,g,b] | <pct> | <pattern> | <eq> }
    """
    try:
        from hardware.led_strip import strip, COLORS
        from behavior.ambilight import ambilight
        body = await request.json()
        cmd = (body.get("cmd") or "").lower()
        val = body.get("value")
        # A manual command overrides any scene animation or TV sync.
        await strip.stop_animation()
        if ambilight.active:
            await ambilight.stop()
        if cmd == "pattern":
            value = val
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    return JSONResponse(status_code=400, content={
                        "error": "value must be a pattern name or integer"})
                try:
                    value = int(value)
                except ValueError:
                    pass
            if value is None:
                return JSONResponse(status_code=400, content={
                    "error": "value must be a pattern name or integer"})
            try:
                ok = await strip.set_pattern(value)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={
                    "error": "value must be a pattern name or integer"})
        elif cmd == "music":
            value = val
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    return JSONResponse(status_code=400, content={
                        "error": "value must be a music name, integer, or off"})
                if value.lower() == "off":
                    value = 0
                else:
                    try:
                        value = int(value)
                    except ValueError:
                        value = value.lower()
            if value is None:
                return JSONResponse(status_code=400, content={
                    "error": "value must be a music name, integer, or off"})
            try:
                ok = await strip.set_music(value)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={
                    "error": "value must be a music name, integer, or off"})
        elif cmd == "temp":
            try:
                value = int(val)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={
                    "error": "value must be an integer 0-100"})
            ok = await strip.set_color_temp(value)
        elif cmd == "named":
            ok = await strip.set_named(str(val))
        elif cmd == "color" and isinstance(val, (list, tuple)) and len(val) == 3:
            ok = await strip.set_color(int(val[0]), int(val[1]), int(val[2]))
        elif cmd == "bright":
            ok = await strip.set_brightness(int(val))
        elif cmd == "on":
            ok = await strip.power(True)
        elif cmd == "off":
            ok = await strip.power(False)
        else:
            return JSONResponse(status_code=400, content={
                "error": "cmd must be named/color/bright/on/off/pattern/music/temp",
                "colors": list(COLORS)})
        if not ok:
            return JSONResponse(status_code=503, content={
                "error": "strip unreachable — powered? phone app disconnected?"})
        return {"ok": True, **strip.state}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/led")
async def led_state(_: None = _AuthRequired):
    from hardware.led_strip import strip, COLORS
    try:
        from behavior.ambilight import LEGACY_ROI_CONFIG, ROI_CONFIG, ambilight
        tv_sync = ambilight.active
    except Exception:
        tv_sync = False
        roi_active = False
        roi_config = None
    else:
        roi_active = ROI_CONFIG.exists() or LEGACY_ROI_CONFIG.exists()
        roi_config = str(ROI_CONFIG if ROI_CONFIG.exists() else LEGACY_ROI_CONFIG)
    from hardware.led_strip import SCENES
    from hardware.wipro_light import wipro
    return {
        **strip.state,
        "tv_sync": tv_sync,
        "roi_active": roi_active,
        "roi_config": roi_config,
        "colors": list(COLORS),
        "scenes": list(SCENES),
        "bulb": {"enabled": wipro.enabled, **wipro.stats},
    }


@app.get("/led/health")
async def led_health(_: None = _AuthRequired):
    """LED strip + ambilight health for monitoring/admin reports."""
    from hardware.led_strip import strip
    try:
        from behavior.ambilight import ambilight
        tv_sync = ambilight.active
    except Exception:
        tv_sync = False
    from hardware.wipro_light import wipro
    return {**strip.health, "tv_sync": tv_sync, "scene": strip.state.get("scene"),
            "bulb": {"enabled": wipro.enabled, **wipro.stats}}


@app.get("/led/bulb")
async def led_bulb_state(_: None = _AuthRequired):
    """Wipro bulb state — driver stats + last manual command (AB-014)."""
    from hardware.wipro_light import wipro
    return {"enabled": wipro.enabled, **wipro.stats}


@app.post("/led/bulb")
async def led_bulb(request: Request, _: None = _AuthRequired):
    """Manual Wipro bulb control (AB-014).
    Body: {"cmd": "color", "value": [r,g,b], "bright": 0-100}
        | {"cmd": "bright", "value": 0-100}
        | {"cmd": "on"} | {"cmd": "off"}
    All sends go through the single wipro worker (single-TCP invariant)."""
    from hardware.wipro_light import wipro
    if not wipro.enabled:
        return JSONResponse(status_code=503, content={"error": "bulb disabled (WIPRO_LOCAL_KEY not set)"})
    try:
        body = await request.json()
        cmd = (body.get("cmd") or "").lower()
        if cmd == "color":
            v = body.get("value") or []
            if len(v) != 3 or not all(isinstance(x, (int, float)) and 0 <= x <= 255 for x in v):
                return JSONResponse(status_code=400, content={"error": "value must be [r,g,b] 0-255"})
            bright = body.get("bright", 100)
            if not isinstance(bright, (int, float)) or not 0 < bright <= 100:
                return JSONResponse(status_code=400, content={"error": "bright must be 1-100"})
            wipro.set_color_manual(int(v[0]), int(v[1]), int(v[2]), int(bright))
        elif cmd == "bright":
            v = body.get("value")
            if not isinstance(v, (int, float)) or not 0 < v <= 100:
                return JSONResponse(status_code=400, content={"error": "value must be 1-100"})
            wipro.set_bright_manual(int(v))
        elif cmd == "on":
            wipro.set_color_manual(255, 240, 220, 80)   # warm white default
        elif cmd == "off":
            wipro.power_off()
        else:
            return JSONResponse(status_code=400, content={"error": "cmd must be color|bright|on|off"})
        return {"ok": True, "cmd": cmd, **wipro.stats}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/led/scene")
async def led_scene(request: Request, _: None = _AuthRequired):
    """Apply a hands-free scene preset. Body {"scene": "movie|chill|night|..."}."""
    try:
        from hardware.led_strip import strip, SCENES
        from behavior.ambilight import ambilight
        body = await request.json()
        name = (body.get("scene") or "").lower()
        if name not in SCENES:
            return JSONResponse(status_code=400, content={"error": "unknown scene", "scenes": list(SCENES)})
        if ambilight.active:      # a scene overrides TV sync
            await ambilight.stop()
        ok = await strip.set_scene(name)
        if not ok:
            return JSONResponse(status_code=503, content={"error": "strip unreachable"})
        # Scene fan-out (AB-014): bulb follows static scenes; drivers stay dumb.
        from hardware.wipro_light import wipro
        preset = SCENES.get(name) or {}
        if wipro.enabled and "rgb" in preset:
            r, g, b = preset["rgb"]
            wipro.set_color_manual(r, g, b, preset.get("bright", 100))
        return {"ok": True, "scene": name, **strip.state}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/led/calibrate")
async def led_calibrate(_: None = _AuthRequired):
    """Calibrate TV ROI from a full-red TV screen and save config/ambilight_roi.json."""
    try:
        import base64
        import cv2
        import numpy as np
        from behavior.ambilight import ROI_CONFIG, _order_quad
        from perception.vision.camera import camera

        frame_obj = camera.latest_frame
        if frame_obj is None or frame_obj.is_stale(3000):
            return JSONResponse(status_code=503, content={"error": "no camera frame"})

        img = frame_obj.image.copy()
        h, w = img.shape[:2]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        red_low = cv2.inRange(hsv, (0, 90, 70), (12, 255, 255))
        red_high = cv2.inRange(hsv, (168, 90, 70), (179, 255, 255))
        mask = cv2.morphologyEx(red_low | red_high, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return JSONResponse(status_code=422, content={"error": "no red TV rectangle found"})

        contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(contour)
        if area < 0.04 * w * h:
            return JSONResponse(status_code=422, content={"error": "red region too small"})

        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.025 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            pts = approx.reshape(4, 2).astype("float32")
        else:
            x, y, ww, hh = cv2.boundingRect(contour)
            pad_x, pad_y = ww * 0.03, hh * 0.03
            pts = np.float32([
                [x + pad_x, y + pad_y],
                [x + ww - pad_x, y + pad_y],
                [x + ww - pad_x, y + hh - pad_y],
                [x + pad_x, y + hh - pad_y],
            ])
        ordered = _order_quad(pts)
        # Guard on the FINAL quad (the bounding-rect fallback can inflate a
        # modest blob to near-whole-frame): a quad covering >80% of the frame
        # is reflected wall glow, not the TV rectangle — the camera isn't
        # aimed at the screen. Refuse rather than overwrite a good ROI.
        quad_area = cv2.contourArea(ordered.astype(np.float32).reshape(-1, 1, 2))
        if quad_area > 0.80 * w * h:
            return JSONResponse(status_code=422, content={
                "error": "detected red region spans nearly the whole frame — "
                         "that's wall glow, the camera doesn't see the TV as a "
                         "distinct rectangle. Re-aim the camera at the TV "
                         "(check the live feed :8080) and retry. Existing ROI kept."})
        norm = [[round(float(x) / w, 6), round(float(y) / h, 6)] for x, y in ordered]

        from utils.atomic_write import atomic_write_text
        atomic_write_text(ROI_CONFIG, json.dumps({
            "version": 1,
            "normalized": True,
            "points": norm,
            "source_shape": [w, h],
            "note": "Calibrated from /led/calibrate full-red screen.",
        }, indent=2) + "\n")

        cv2.polylines(img, [ordered.astype(int).reshape((-1, 1, 2))], True, (0, 255, 0), 3)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        preview = base64.b64encode(buf).decode() if ok else None
        return {"ok": True, "roi_config": str(ROI_CONFIG), "points": norm, "preview_b64": preview}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/led/tv")
async def led_tv_sync(request: Request, _: None = _AuthRequired):
    """Toggle camera-based TV ambilight sync.

    Body: {"on": true|false}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})

    on = bool((body or {}).get("on"))
    # Persist desired state so a cosmo restart mid-evening restores the sync
    # (this session: three restarts silently killed it).
    try:
        from utils.atomic_write import atomic_write_json
        state_file = Path.home() / ".robot" / "ambilight_state.json"
        atomic_write_json(state_file, {"tv_sync": on})
    except Exception:
        pass
    try:
        from behavior.ambilight import LEGACY_ROI_CONFIG, ROI_CONFIG, ambilight
        from hardware.led_strip import strip
        roi_active = ROI_CONFIG.exists() or LEGACY_ROI_CONFIG.exists()
        roi_config = str(ROI_CONFIG if ROI_CONFIG.exists() else LEGACY_ROI_CONFIG)
        if on:
            ok = await ambilight.start()
            return {
                "ok": ok,
                "tv_sync": ambilight.active,
                "roi_config": roi_config,
                "roi_active": roi_active,
                **strip.state,
            }
        await ambilight.stop()
        strip_off = False
        try:
            strip_off = await asyncio.wait_for(strip.power(False), timeout=4.0)
        except Exception as e:
            log.warning("api.led_tv_strip_off_failed", error=str(e)[:80])
        return {
            "ok": True,
            "strip_off": strip_off,
            "tv_sync": ambilight.active,
            "roi_config": roi_config,
            "roi_active": roi_active,
            **strip.state,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/motor/forward")
async def motor_forward(speed: float = 0.4, duration: float = 1.0, _: None = _AuthRequired):
    try:
        from hardware.motors import motor_controller
        asyncio.create_task(_timed_move(motor_controller.forward(speed=speed), duration))
        return {"ok": True, "action": "forward"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/motor/back")
async def motor_back(speed: float = 0.3, duration: float = 1.0, _: None = _AuthRequired):
    try:
        from hardware.motors import motor_controller
        asyncio.create_task(_timed_move(motor_controller.backward(speed=speed), duration))
        return {"ok": True, "action": "back"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/motor/left")
async def motor_left(speed: float = 0.35, duration: float = 0.6, _: None = _AuthRequired):
    try:
        from hardware.motors import motor_controller
        asyncio.create_task(motor_controller.turn_left(speed=speed, duration=duration))
        return {"ok": True, "action": "left"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/motor/right")
async def motor_right(speed: float = 0.35, duration: float = 0.6, _: None = _AuthRequired):
    try:
        from hardware.motors import motor_controller
        asyncio.create_task(motor_controller.turn_right(speed=speed, duration=duration))
        return {"ok": True, "action": "right"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/motor/stop")
async def motor_stop(_: None = _AuthRequired):
    try:
        from hardware.motors import motor_controller
        await motor_controller.stop()
        return {"ok": True, "action": "stop"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Token budget status ───────────────────────────────────────────────────────

@app.get("/budget")
async def budget_status():
    try:
        from cognition.llm import token_budget
        return {
            "day_total": token_budget.day_total,
            "limit": token_budget.limit,
            "remaining": token_budget.remaining,
            "claude_allowed": token_budget.claude_allowed(),
            "pct_used": round(token_budget.day_total / max(1, token_budget.limit) * 100, 1),
        }
    except Exception as e:
        return {"day_total": 0, "limit": 100000, "remaining": 100000,
                "claude_allowed": True, "pct_used": 0.0}


# ── Log tail ─────────────────────────────────────────────────────────────────

@app.get("/logs/tail")
async def logs_tail(lines: int = 20):
    """Return last N lines from PM2 log or cosmo-out.log."""
    # PM2 suffixes the app id when merge_logs is false (cosmo-out-6.log),
    # so glob and sort by mtime instead of hardcoding names
    log_paths = sorted(
        [
            *(Path.home() / ".robot" / "logs").glob("cosmo-out*.log"),
            *(Path.home() / ".pm2" / "logs").glob("cosmo*-out*.log"),
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for lp in log_paths:
        if lp.exists():
            try:
                with open(lp, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    # Read last ~8KB
                    f.seek(max(0, size - 8192))
                    data = f.read().decode("utf-8", errors="replace")
                raw_lines = data.splitlines()
                return {"lines": raw_lines[-lines:], "source": str(lp)}
            except Exception:
                pass
    # Fallback: try pm2 logs command
    try:
        import subprocess
        result = subprocess.run(
            ["pm2", "logs", "cosmo_demo", "--lines", str(lines), "--nostream", "--raw"],
            capture_output=True, text=True, timeout=5
        )
        return {"lines": result.stdout.splitlines()[-lines:], "source": "pm2"}
    except Exception:
        pass
    return {"lines": [], "source": "unavailable"}


# ── Brain event triggers ──────────────────────────────────────────────────────

@app.post("/trigger/face_seen")
async def trigger_face_seen(_: None = _AuthRequired):
    try:
        from cognition.mind import cosmo_mind
        asyncio.create_task(cosmo_mind._maybe_speak("face_seen", "Madhan"))
        return {"ok": True, "trigger": "face_seen"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/trigger/touched")
async def trigger_touched(_: None = _AuthRequired):
    try:
        from cognition.mind import cosmo_mind
        asyncio.create_task(cosmo_mind._maybe_speak("touched", None))
        return {"ok": True, "trigger": "touched"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/trigger/emotion_happy")
async def trigger_emotion_happy(_: None = _AuthRequired):
    try:
        from cognition.mind import cosmo_mind
        asyncio.create_task(cosmo_mind._maybe_speak("emotion_happy", "Madhan"))
        return {"ok": True, "trigger": "emotion_happy"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Smart home ingestion ─────────────────────────────────────────────────────

@app.post("/smarthome/event")
async def smarthome_event(payload: dict):
    """Ingest a smart home event and publish it onto the robot event bus.

    Caller: Home Assistant webhook / MQTT bridge / any local service.
    Body: { "type": "device_on|device_off|motion|presence|scene",
            "device": "<name>", "state": "<value>", ... }

    No auth required from local network — events are informational, not actuating.
    """
    from core.event_bus import bus, Event, EventType

    etype_map = {
        "device_on":  EventType.SMARTHOME_DEVICE_ON,
        "device_off": EventType.SMARTHOME_DEVICE_OFF,
        "motion":     EventType.SMARTHOME_MOTION,
        "presence":   EventType.SMARTHOME_PRESENCE,
        "scene":      EventType.SMARTHOME_SCENE,
    }

    raw_type = str((payload or {}).get("type", "")).lower()
    etype = etype_map.get(raw_type)
    if etype is None:
        return JSONResponse(status_code=400, content={
            "error": f"unknown type '{raw_type}'",
            "valid": list(etype_map.keys()),
        })

    await bus.publish(Event(type=etype, data=payload))
    log.info("smarthome.event_ingested", type=raw_type, device=payload.get("device"))
    return {"ok": True, "event": etype.value}


# ── Phase 5: WhatsApp control surface (!cosmo in banteragent proxies here) ───

@app.get("/caps")
async def caps():
    """Capability registry snapshot — what Cosmo can do right now."""
    try:
        from core.capabilities import registry
        return registry.snapshot()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/cosmo/sim")
async def cosmo_sim(payload: dict):
    """Force a capability into SIMULATED state (dev/testing via WhatsApp)."""
    from core.capabilities import Capability, registry
    name = str((payload or {}).get("cap", "")).strip().lower()
    try:
        cap = Capability(name)
    except ValueError:
        return JSONResponse(status_code=400, content={
            "error": f"unknown capability '{name}'",
            "valid": [c.value for c in Capability],
        })
    registry.simulate(cap, "whatsapp !cosmo sim")
    return {"ok": True, "cap": cap.value, "state": registry.state(cap).value}


@app.post("/cosmo/say")
async def cosmo_say(payload: dict):
    """Speak the given text through Cosmo's TTS."""
    text = str((payload or {}).get("text", "")).strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "no text"})
    if len(text) > 300:
        text = text[:300]
    try:
        from expression.speech import tts
        asyncio.create_task(tts.speak(text))
        return {"ok": True, "text": text}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/cosmo/last")
async def cosmo_last(n: int = 5):
    """Last N events Cosmo reacted to."""
    n = max(1, min(n, 20))
    return {"events": list(_events_ref)[-n:],
            "last_response": _state_ref.get("last_response", "—")}


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
