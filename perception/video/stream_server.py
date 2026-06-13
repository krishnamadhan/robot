"""
MJPEG stream server — serves Cosmo's camera feed over HTTP.

Endpoints:
  GET /         — HTML viewer page
  GET /stream   — MJPEG stream (works in any browser)
  GET /snap     — single JPEG snapshot

Usage:
  from perception.video.stream_server import stream_server
  await stream_server.start()          # starts on port 8080
  url = stream_server.local_url()      # http://192.168.1.30:8080
  url = stream_server.tailscale_url()  # http://100.101.250.126:8080
"""

import asyncio
import json
import os
import socket
import subprocess
import tempfile
import time
from typing import Optional

import aiohttp as _aiohttp
import cv2
import numpy as np
from aiohttp import web

from hardware.motors import motor_controller
from perception.vision.camera import camera
from utils.logger import get_logger

log = get_logger(__name__)

PORT = 8080
STREAM_FPS = 12          # target FPS for MJPEG stream
JPEG_QUALITY = 72        # 0-100; lower = smaller, faster
FRAME_INTERVAL = 1.0 / STREAM_FPS
RECORD_FPS = 12
RECORD_SECONDS = 30
BANTERAGENT_URL = "http://127.0.0.1:3099"
OWNER_JID = "919487506127@c.us"

_HTML_PAGE = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
  <title>Cosmo Live</title>
  <style>
    *{{margin:0;padding:0;box-sizing:border-box;}}
    body{{background:#111;display:flex;flex-direction:column;align-items:center;
          justify-content:flex-start;min-height:100vh;padding:12px;gap:12px;
          font-family:system-ui;color:#fff;touch-action:none;}}
    h1{{font-size:1rem;letter-spacing:.05em;opacity:.8;}}
    #stream-wrap{{width:100%;max-width:640px;border-radius:8px;overflow:hidden;
                  box-shadow:0 0 30px #0008;background:#000;}}
    #stream-wrap img{{width:100%;display:block;}}
    #status{{font-size:.75rem;color:#888;height:16px;}}
    #joy-area{{width:100%;max-width:640px;display:flex;justify-content:center;
               align-items:center;padding:10px 0;}}
    #pad{{width:200px;height:200px;border-radius:50%;background:#222;border:2px solid #444;
          position:relative;touch-action:none;cursor:pointer;flex-shrink:0;}}
    #knob{{width:72px;height:72px;border-radius:50%;background:radial-gradient(circle at 35% 35%,#fff,#888);
           position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
           pointer-events:none;box-shadow:0 2px 8px #0006;transition:background .1s;}}
    #pad.active #knob{{background:radial-gradient(circle at 35% 35%,#4af,#06c);}}
    #dpad{{display:none;}}
    @media(max-width:400px){{#pad{{width:160px;height:160px;}}#knob{{width:58px;height:58px;}}}}
  </style>
</head>
<body>
  <h1>🤖 Cosmo Live</h1>
  <div id="stream-wrap">
    <img src="/stream" alt="Live feed">
  </div>
  <div id="status">idle</div>
  <div id="joy-area">
    <div id="pad">
      <div id="knob"></div>
    </div>
  </div>
  <script>
    const pad = document.getElementById('pad');
    const knob = document.getElementById('knob');
    const status = document.getElementById('status');
    const R = 100;          // pad radius
    const DEAD = 0.12;      // deadzone
    let active = false, cx = 0, cy = 0, hbTimer = null, driveTimer = null;
    let lastX = 0, lastY = 0;

    function padCenter() {{
      const r = pad.getBoundingClientRect();
      return [r.left + r.width/2, r.top + r.height/2];
    }}

    function clamp(v, lo, hi) {{ return Math.max(lo, Math.min(hi, v)); }}

    function applyDeadzone(v) {{
      if (Math.abs(v) < DEAD) return 0;
      return (v - Math.sign(v)*DEAD) / (1 - DEAD);
    }}

    function moveKnob(nx, ny) {{
      // nx, ny in -1..1
      const px = nx * (R - 36);
      const py = ny * (R - 36);
      knob.style.transform = `translate(calc(-50% + ${{px}}px), calc(-50% + ${{py}}px))`;
    }}

    async function sendDrive(x, y) {{
      try {{
        await fetch('/drive', {{
          method: 'POST',
          headers: {{'Content-Type':'application/json'}},
          body: JSON.stringify({{x, y}})
        }});
      }} catch(e) {{}}
    }}

    async function sendStop() {{
      try {{ await fetch('/drive/stop', {{method:'POST'}}); }} catch(e) {{}}
    }}

    function startHb() {{
      if (hbTimer) return;
      hbTimer = setInterval(async () => {{
        if (active) await sendDrive(lastX, lastY);
      }}, 80);
    }}

    function stopHb() {{
      clearInterval(hbTimer); hbTimer = null;
    }}

    function onStart(ex, ey) {{
      active = true;
      pad.classList.add('active');
      startHb();
      onMove(ex, ey);
    }}

    function onMove(ex, ey) {{
      if (!active) return;
      const [pcx, pcy] = padCenter();
      let dx = (ex - pcx) / R;
      let dy = -(ey - pcy) / R;
      const mag = Math.sqrt(dx*dx + dy*dy);
      if (mag > 1) {{ dx /= mag; dy /= mag; }}
      moveKnob(dx, dy);
      lastX = applyDeadzone(clamp(dx, -1, 1));
      lastY = applyDeadzone(clamp(dy, -1, 1));
      const dir = lastY > 0.1 ? 'FWD' : lastY < -0.1 ? 'BWD' : lastX > 0.1 ? 'RIGHT' : lastX < -0.1 ? 'LEFT' : 'STOP';
      status.textContent = `${{dir}}  x=${{lastX.toFixed(2)}}  y=${{lastY.toFixed(2)}}`;
    }}

    function onEnd() {{
      active = false;
      pad.classList.remove('active');
      stopHb();
      lastX = 0; lastY = 0;
      moveKnob(0, 0);
      status.textContent = 'idle';
      sendStop();
    }}

    // Touch
    pad.addEventListener('touchstart', e => {{ e.preventDefault(); const t=e.touches[0]; onStart(t.clientX, t.clientY); }}, {{passive:false}});
    pad.addEventListener('touchmove',  e => {{ e.preventDefault(); const t=e.touches[0]; onMove(t.clientX, t.clientY); }}, {{passive:false}});
    pad.addEventListener('touchend',   e => {{ e.preventDefault(); onEnd(); }}, {{passive:false}});

    // Mouse
    pad.addEventListener('mousedown', e => {{ onStart(e.clientX, e.clientY); }});
    window.addEventListener('mousemove', e => {{ if(active) onMove(e.clientX, e.clientY); }});
    window.addEventListener('mouseup',   () => {{ if(active) onEnd(); }});
  </script>
</body>
</html>
""".format(fps=STREAM_FPS)


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_tailscale_ip() -> Optional[str]:
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3
        )
        ip = result.stdout.strip()
        return ip if ip else None
    except Exception:
        return None


BLUR_KERNEL = 5  # 0 = off, odd number = Gaussian blur kernel size


def _encode_frame(frame_bgr: np.ndarray, blur: bool = False) -> Optional[bytes]:
    try:
        img = frame_bgr
        if blur and BLUR_KERNEL > 0:
            img = cv2.GaussianBlur(img, (BLUR_KERNEL, BLUR_KERNEL), 0)
        ret, buf = cv2.imencode(
            ".jpg", img,
            [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        return buf.tobytes() if ret else None
    except Exception:
        return None


class StreamServer:

    def __init__(self) -> None:
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._local_ip: str = _get_local_ip()
        self._tailscale_ip: Optional[str] = None
        self._active_streams: int = 0

    async def start(self, port: int = PORT) -> bool:
        self._tailscale_ip = _get_tailscale_ip()
        self._app = web.Application()
        self._app.router.add_get("/",             self._handle_index)
        self._app.router.add_get("/stream",       self._handle_stream)
        self._app.router.add_get("/snap",         self._handle_snap)
        self._app.router.add_post("/snap-send",   self._handle_snap_send)
        self._app.router.add_post("/record-send", self._handle_record_send)
        self._app.router.add_post("/drive",       self._handle_drive)
        self._app.router.add_post("/drive/stop",  self._handle_drive_stop)

        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", port)
        try:
            await site.start()
            log.info("stream_server.started",
                     local=self.local_url(),
                     tailscale=self.tailscale_url() or "n/a")
            return True
        except Exception as e:
            log.error("stream_server.start_failed", error=str(e))
            return False

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            log.info("stream_server.stopped")

    def local_url(self) -> str:
        return f"http://{self._local_ip}:{PORT}"

    def tailscale_url(self) -> Optional[str]:
        if self._tailscale_ip:
            return f"http://{self._tailscale_ip}:{PORT}"
        return None

    def best_url(self) -> str:
        return self.tailscale_url() or self.local_url()

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=_HTML_PAGE, content_type="text/html")

    async def _handle_snap(self, request: web.Request) -> web.Response:
        frame_obj = camera.latest_frame
        if frame_obj is None or frame_obj.is_stale(3000):
            return web.Response(status=503, text="No frame available")
        jpg = _encode_frame(frame_obj.image)
        if jpg is None:
            return web.Response(status=500, text="Encode failed")
        return web.Response(body=jpg, content_type="image/jpeg")

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        self._active_streams += 1
        log.info("stream_server.client_connected",
                 peer=request.remote, active=self._active_streams)
        response = web.StreamResponse(headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=--cosmoframe",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        try:
            await response.prepare(request)
            last_frame_id = -1
            while True:
                frame_obj = camera.latest_frame
                if frame_obj is None or frame_obj.is_stale(2000):
                    await asyncio.sleep(0.05)
                    continue

                # Only encode if we have a new frame
                if frame_obj.frame_id == last_frame_id:
                    await asyncio.sleep(0.02)
                    continue
                last_frame_id = frame_obj.frame_id

                loop = asyncio.get_event_loop()
                jpg = await loop.run_in_executor(
                    None, _encode_frame, frame_obj.image
                )
                if jpg is None:
                    continue

                await response.write(
                    b"--cosmoframe\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" +
                    jpg + b"\r\n"
                )
                await asyncio.sleep(FRAME_INTERVAL)

        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._active_streams -= 1
            log.info("stream_server.client_disconnected",
                     peer=request.remote, active=self._active_streams)
        return response

    async def _send_media(self, path: str, caption: str) -> bool:
        payload = json.dumps({"file": path, "to": OWNER_JID, "caption": caption})
        try:
            async with _aiohttp.ClientSession() as sess:
                async with sess.post(
                    f"{BANTERAGENT_URL}/send-media",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=_aiohttp.ClientTimeout(total=30),
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            log.error("stream_server.send_media_failed", error=str(e))
            return False

    async def _handle_snap_send(self, request: web.Request) -> web.Response:
        frame_obj = camera.latest_frame
        if frame_obj is None or frame_obj.is_stale(3000):
            return web.Response(status=503, text="No frame available")
        loop = asyncio.get_event_loop()
        jpg = await loop.run_in_executor(None, _encode_frame, frame_obj.image, True)
        if jpg is None:
            return web.Response(status=500, text="Encode failed")

        fd, path = tempfile.mkstemp(suffix=".jpg", prefix="cosmo_snap_")
        try:
            os.write(fd, jpg)
            os.close(fd)
            ok = await self._send_media(path, "📸 Cosmo's view")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

        if ok:
            log.info("stream_server.snap_sent")
            return web.Response(text="ok")
        return web.Response(status=500, text="send failed")

    async def _handle_record_send(self, request: web.Request) -> web.Response:
        frame_check = camera.latest_frame
        if frame_check is None or frame_check.is_stale(3000):
            return web.Response(status=503, text="No frame available")

        fd, raw_path = tempfile.mkstemp(suffix=".avi", prefix="cosmo_raw_")
        os.close(fd)
        fd2, out_path = tempfile.mkstemp(suffix=".mp4", prefix="cosmo_rec_")
        os.close(fd2)
        loop = asyncio.get_event_loop()
        try:
            ok = await loop.run_in_executor(None, self._record_clip, raw_path)
        except Exception as e:
            log.error("stream_server.record_failed", error=str(e))
            for p in (raw_path, out_path):
                try: os.unlink(p)
                except OSError: pass
            return web.Response(status=500, text=str(e))

        if not ok:
            for p in (raw_path, out_path):
                try: os.unlink(p)
                except OSError: pass
            return web.Response(status=500, text="record failed")

        # Re-encode to H.264 MP4 for WhatsApp compatibility
        try:
            await loop.run_in_executor(None, self._reencode, raw_path, out_path)
        except Exception as e:
            log.error("stream_server.reencode_failed", error=str(e))
            for p in (raw_path, out_path):
                try: os.unlink(p)
                except OSError: pass
            return web.Response(status=500, text=str(e))
        finally:
            try: os.unlink(raw_path)
            except OSError: pass

        sent = await self._send_media(out_path, f"🎥 Cosmo — {RECORD_SECONDS}s clip")
        try: os.unlink(out_path)
        except OSError: pass

        if sent:
            log.info("stream_server.clip_sent")
            return web.Response(text="ok")
        return web.Response(status=500, text="send failed")

    def _record_clip(self, path: str) -> bool:
        # Determine actual frame size from live camera feed
        probe = camera.latest_frame
        if probe is None:
            return False
        h, w = probe.image.shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        out = cv2.VideoWriter(path, fourcc, RECORD_FPS, (w, h))
        if not out.isOpened():
            return False
        deadline = time.monotonic() + RECORD_SECONDS
        interval = 1.0 / RECORD_FPS
        try:
            while time.monotonic() < deadline:
                t0 = time.monotonic()
                frame_obj = camera.latest_frame
                if frame_obj is not None and not frame_obj.is_stale(2000):
                    img = frame_obj.image
                    if BLUR_KERNEL > 0:
                        img = cv2.GaussianBlur(img, (BLUR_KERNEL, BLUR_KERNEL), 0)
                    out.write(img)
                elapsed = time.monotonic() - t0
                remaining = interval - elapsed
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            out.release()
        return True

    def _reencode(self, src: str, dst: str) -> None:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", src,
                "-vcodec", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-an",
                dst,
            ],
            capture_output=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode()[-300:])

    @property
    def active_streams(self) -> int:
        return self._active_streams

    async def _handle_drive(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            x = float(data.get("x", 0.0))   # -1=left, +1=right
            y = float(data.get("y", 0.0))   # -1=back, +1=forward
        except Exception:
            return web.Response(status=400, text="bad json")

        mc = motor_controller
        if not mc._left:
            return web.Response(text="ok")

        mc._safety_stop = False
        mc._web_drive = True
        await mc.heartbeat()
        left  = max(-1.0, min(1.0, y + x))
        right = max(-1.0, min(1.0, y - x))
        mc._left.set(left,  mc.LEFT_TRIM)
        mc._right.set(right, mc.RIGHT_TRIM)
        mc._safety_stop = False
        return web.Response(text="ok")

    async def _handle_drive_stop(self, request: web.Request) -> web.Response:
        motor_controller._web_drive = False
        await motor_controller.stop(emergency=True)
        return web.Response(text="ok")


stream_server = StreamServer()
