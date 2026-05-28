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
from fastapi.responses import JSONResponse, HTMLResponse

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


# ── /mind/on · /mind/off ─────────────────────────────────────────────────────

@app.post("/mind/on")
async def mind_on():
    try:
        from cognition.mind import cosmo_mind
        cosmo_mind.enable()
        return {"mind": "enabled"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/mind/off")
async def mind_off():
    try:
        from cognition.mind import cosmo_mind
        cosmo_mind.disable()
        return {"mind": "disabled", "day_total": cosmo_mind._budget.day_total}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── /dashboard ────────────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Phone-friendly live dashboard. No frames sent off-device."""
    return HTMLResponse(content=_DASHBOARD_HTML)


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cosmo</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0d1117; color: #e6edf3;
         padding: 12px; max-width: 480px; margin: auto; }
  h1 { font-size: 1.3rem; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: #3fb950; display: inline-block; }
  .dot.off { background: #8b949e; }
  section { background: #161b22; border-radius: 8px; padding: 12px; margin-bottom: 10px; }
  section h2 { font-size: 0.8rem; text-transform: uppercase; color: #8b949e; margin-bottom: 8px; }
  .row { display: flex; justify-content: space-between; padding: 3px 0;
         font-size: 0.9rem; border-bottom: 1px solid #21262d; }
  .row:last-child { border-bottom: none; }
  .label { color: #8b949e; }
  .val { font-weight: 600; }
  .bar-wrap { background: #21262d; border-radius: 4px; height: 8px;
              width: 120px; overflow: hidden; display: inline-block; }
  .bar { height: 100%; border-radius: 4px; background: #3fb950; transition: width 0.4s; }
  .bar.warn { background: #d29922; }
  .bar.crit { background: #f85149; }
  .memories { font-size: 0.8rem; line-height: 1.5; color: #c9d1d9; }
  .mem-item { padding: 4px 0; border-bottom: 1px solid #21262d; }
  .mem-item:last-child { border-bottom: none; }
  .mem-meta { color: #8b949e; font-size: 0.72rem; }
  .btn-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
  button { background: #21262d; color: #e6edf3; border: 1px solid #30363d;
           border-radius: 6px; padding: 8px 14px; font-size: 0.85rem; cursor: pointer; }
  button:hover { background: #30363d; }
  button.danger { border-color: #f85149; }
  button.primary { background: #238636; border-color: #238636; }
  .ts { font-size: 0.72rem; color: #8b949e; text-align: right; margin-top: 8px; }
  .badge { font-size: 0.7rem; padding: 2px 6px; border-radius: 10px;
           background: #21262d; color: #8b949e; }
  .badge.real { background: #1f4a2e; color: #3fb950; }
  .badge.mock { background: #2d2a0f; color: #d29922; }
  .badge.error { background: #3d1a1a; color: #f85149; }
</style>
</head>
<body>
<h1><span class="dot" id="status-dot"></span> Cosmo</h1>

<section>
  <h2>Personality</h2>
  <div class="row"><span class="label">Mood</span>
    <span><span class="val" id="mood">—</span>
    <span class="bar-wrap"><div class="bar" id="mood-bar"></div></span></span></div>
  <div class="row"><span class="label">Energy</span>
    <span><span class="val" id="energy">—</span>
    <span class="bar-wrap"><div class="bar" id="energy-bar"></div></span></span></div>
  <div class="row"><span class="label">Arousal</span>
    <span class="val" id="arousal">—</span></div>
  <div class="row"><span class="label">Attachment</span>
    <span class="val" id="attachment">—</span></div>
  <div class="row"><span class="label">Description</span>
    <span class="val" id="description">—</span></div>
</section>

<section>
  <h2>Attention</h2>
  <div class="row"><span class="label">Sees</span>
    <span class="val" id="person">—</span></div>
  <div class="row"><span class="label">Emotion</span>
    <span class="val" id="emotion">—</span></div>
  <div class="row"><span class="label">Distance</span>
    <span class="val" id="distance">—</span></div>
  <div class="row"><span class="label">Persons visible</span>
    <span class="val" id="visible">—</span></div>
</section>

<section>
  <h2>System</h2>
  <div class="row"><span class="label">Uptime</span>
    <span class="val" id="uptime">—</span></div>
  <div class="row"><span class="label">CPU temp</span>
    <span class="val" id="temp">—</span></div>
  <div class="row"><span class="label">Free RAM</span>
    <span class="val" id="ram">—</span></div>
  <div class="row"><span class="label">Listen state</span>
    <span class="val" id="listen">—</span></div>
  <div class="row"><span class="label">Battery</span>
    <span class="val" id="battery">—</span></div>
</section>

<section>
  <h2>Hardware</h2>
  <div id="hw-list" style="font-size:0.8rem;line-height:1.8"></div>
</section>

<section>
  <h2>Controls</h2>
  <div class="btn-row">
    <button class="primary" onclick="postCmd('/mind/on')">Mind ON</button>
    <button onclick="postCmd('/mind/off')">Mind OFF</button>
    <button onclick="postCmd('/sound/mute?seconds=3600')">Mute 1h</button>
    <button onclick="postCmd('/sound/unmute')">Unmute</button>
  </div>
</section>

<section>
  <h2>Recent Memories <span class="badge" id="mem-count"></span></h2>
  <div class="memories" id="memories">Loading…</div>
</section>

<div class="ts" id="refreshed"></div>

<script>
const BASE = '';
let lastOk = false;

function fmt(v, decimals=2) {
  return typeof v === 'number' ? v.toFixed(decimals) : (v ?? '—');
}

function barWidth(v, lo=-1, hi=1) {
  const pct = Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  return pct;
}

function setBar(id, pct) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.width = pct + '%';
  el.className = 'bar' + (pct < 20 ? ' crit' : pct < 40 ? ' warn' : '');
}

async function refresh() {
  try {
    const [stateR, healthR, hwR, memR] = await Promise.all([
      fetch(BASE+'/state'),
      fetch(BASE+'/health'),
      fetch(BASE+'/hardware'),
      fetch(BASE+'/memory/recent'),
    ]);

    const s = await stateR.json();
    const h = await healthR.json();
    const hw = await hwR.json();
    const mems = await memR.json();

    lastOk = true;
    document.getElementById('status-dot').className = 'dot';

    // Personality
    const e = s.emotion || {};
    document.getElementById('mood').textContent = fmt(e.mood);
    setBar('mood-bar', barWidth(e.mood || 0, -1, 1));
    document.getElementById('energy').textContent = fmt(e.energy);
    setBar('energy-bar', (e.energy || 0) * 100);
    document.getElementById('arousal').textContent = fmt(e.arousal);
    document.getElementById('attachment').textContent = fmt(e.attachment);
    document.getElementById('description').textContent = e.description || '—';

    // Attention
    const a = s.attention || {};
    document.getElementById('person').textContent = a.person || '—';
    document.getElementById('emotion').textContent = a.emotion || '—';
    document.getElementById('distance').textContent = a.distance_cm != null ? a.distance_cm+'cm' : '—';
    document.getElementById('visible').textContent = a.persons_visible ?? '—';

    // System
    const uptimeSec = h.uptime_s || 0;
    const hh = Math.floor(uptimeSec/3600), mm = Math.floor((uptimeSec%3600)/60);
    document.getElementById('uptime').textContent = hh+'h '+mm+'m';
    document.getElementById('temp').textContent = h.cpu_temp_c != null ? h.cpu_temp_c+'°C' : '—';
    document.getElementById('ram').textContent = h.free_ram_mb != null ? h.free_ram_mb+'MB' : '—';
    document.getElementById('listen').textContent = (s.behavior||{}).listen_state || '—';
    document.getElementById('battery').textContent = s.battery_pct != null ? s.battery_pct+'%' : '—';

    // Hardware
    const hwEl = document.getElementById('hw-list');
    const items = Object.entries(hw.components || {});
    hwEl.innerHTML = items.map(([name, info]) =>
      `<div style="display:flex;justify-content:space-between;padding:2px 0">
        <span style="color:#c9d1d9">${name}</span>
        <span class="badge ${info.status}">${info.status}</span></div>`
    ).join('') || '<span style="color:#8b949e">no components registered</span>';

    // Memories
    const memEl = document.getElementById('memories');
    document.getElementById('mem-count').textContent = mems.length;
    if (mems.length === 0) {
      memEl.innerHTML = '<span style="color:#8b949e">No memories yet</span>';
    } else {
      memEl.innerHTML = mems.slice(0,10).map(m =>
        `<div class="mem-item">
          <div>${m.summary}</div>
          <div class="mem-meta">${m.ts} · ${m.type} · val=${m.valence}</div>
        </div>`
      ).join('');
    }

    document.getElementById('refreshed').textContent = 'Updated ' + new Date().toLocaleTimeString();

  } catch(err) {
    lastOk = false;
    document.getElementById('status-dot').className = 'dot off';
    document.getElementById('refreshed').textContent = 'Offline — retrying…';
  }
}

async function postCmd(path) {
  try {
    const r = await fetch(BASE+path, {method:'POST'});
    const j = await r.json();
    alert(JSON.stringify(j, null, 2));
    refresh();
  } catch(e) { alert('Error: '+e); }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


# ── /sound/mute · /sound/unmute ──────────────────────────────────────────────

@app.post("/sound/mute")
async def sound_mute(seconds: int = 3600):
    """Mute Cosmo's sounds for N seconds (default 1 hour)."""
    try:
        from expression.sounds import sounds
        sounds.mute(seconds)
        return {"muted": True, "seconds": seconds}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/sound/unmute")
async def sound_unmute():
    """Unmute Cosmo immediately."""
    try:
        from expression.sounds import sounds
        sounds.mute(0)
        return {"muted": False}
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
