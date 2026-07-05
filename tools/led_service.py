#!/usr/bin/env python3
"""
led_service.py — minimal Cosmo: camera + LED strip + TV ambilight ONLY.

A lightweight PM2 entry that boots just what the LED features need — the camera
pipeline, the HTTP API (:8000, for the !led command), and the TV-ambilight loop —
and skips the whole personality / audio / behaviour / vision-AI stack (~800MB,
none of which is useful until the robot hardware is wired).

Runs the same FastAPI app as the full robot, so /led, /led/tv, /health,
/camera/* all work; other routes simply have nothing to talk to and return
their own errors if called.

PM2:  pm2 start /home/pi/robot/tools/led_service.py --name cosmo \
        --interpreter python3 --cwd /home/pi/robot && pm2 save
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/pi/robot")

# Load robot/.env (WIPRO_LOCAL_KEY etc.) before any hardware imports.
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from core.event_bus import bus              # noqa: E402
from hardware.wipro_light import wipro      # noqa: E402
from perception.vision.camera import camera  # noqa: E402
from perception.video.stream_server import stream_server  # noqa: E402
from services.api import service as api      # noqa: E402
from utils.logger import get_logger          # noqa: E402

log = get_logger(__name__)


async def main() -> None:
    log.info("led_service.starting")
    await bus.start()
    wipro.init()  # no-op if WIPRO_LOCAL_KEY not set

    cam_ok = await camera.start()
    log.info("led_service.camera", ok=cam_ok)
    if not cam_ok:
        log.warning("led_service.no_camera",
                    hint="TV ambilight needs the camera; !led colour commands still work")

    await api.start()  # FastAPI on :8000 — /led, /led/tv, /health

    # Live camera stream on :8080 (the "!cosmo live" / dashboard view).
    if cam_ok:
        try:
            if await stream_server.start():
                log.info("led_service.stream", url=stream_server.best_url())
        except Exception as e:
            log.warning("led_service.stream_failed", error=str(e)[:80])

    log.info("led_service.ready", api="http://0.0.0.0:8000",
             features="led + tv-ambilight + live-stream (personality/audio/behaviour DISABLED)")

    # Idle forever; the API server + camera thread do the work.
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await camera.stop()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
