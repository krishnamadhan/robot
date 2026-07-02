# TV Ambilight — Handoff to Cursor (2026-07-02)

> Full handoff prompt for continuing the TV-ambilight feature. The Pi camera watches
> the TV; a LEDDMX BLE strip matches the on-screen colour. Current quality ~70%.

## Goal (user priority order)
1. Vibrant colour matching the screen (not washed out)
2. Black/off screen → strip dims OFF (no reflection glow)
3. No flicker when the scene isn't significantly changing
4. Damp flashes/strobes
Verify colour-vs-screen correspondence systematically.

## Access
- SSH: `ssh pi@100.101.250.126` (Tailscale). Code at `/home/pi/robot`. Test ON the Pi (camera+strip are there).
- Repo: `git@github.com:krishnamadhan/robot.git`, branch `master`.
- Deploy: `pm2 restart cosmo`. **NEVER touch `banteragent` (WhatsApp auth).**

## Architecture
`cosmo` runs the MINIMAL `tools/led_service.py`: camera + API(:8000) + stream(:8080) + ambilight only. Full robot stack OFF. Recreate:
`pm2 start /home/pi/robot/tools/led_service.py --name cosmo --interpreter python3 --cwd /home/pi/robot && pm2 save`

Key files: `behavior/ambilight.py` (the algorithm — tunables at top), `hardware/led_strip.py` (BLE driver), `services/api/service.py` (/led, /led/tv), `tools/{led_test,ambilight_test,led_service}.py`.

## LED strip (already reverse-engineered)
- LEDDMX-03-A715, MAC `41:42:CD:95:A7:15`. Write-Without-Response to char `0xFFE1`.
- Frames `7B FF <cmd> <data> BF`: colour `07 RR GG BB 00 FF`, brightness `01 AA PP 00 FF FF` (PP 0-100, AA=PP*32//100). **Power opcode 04 is BROKEN — never send; use brightness 0.**
- Colour + brightness are independent registers. SINGLE-CONNECTION: connect by address (it stops advertising once connected); `bluetoothctl disconnect 41:42:CD:95:A7:15` to unstick. Keep the phone's "LED LAMP" app closed.

## Current algorithm (behavior/ambilight.py)
6 Hz loop on `camera.latest_frame`. `analyze()`:
- **vivid_frac** = share of pixels saturated (S>0.35) AND bright (V>0.35). CONTENT GATE. TV off ≈0.02-0.08, content ≈0.5. (Whole-frame brightness failed — lit wall.)
- dominant hue = sat-weighted circular mean of vivid pixels; output re-saturated to SAT_FLOOR.
- brightness from vivid pixels' value.
Gate: hysteresis (CONTENT_ON 0.16 / OFF 0.09) + 2-frame debounce → below → dim OFF. Colour EMA; brightness EMA + rate-limited rise (flash damping). Deadband: repaint only if ΔRGB>22 or Δbright≥4 (steady scene holds). Verified: red→vivid red, TV-off clip→gate off, blue→vivid blue.

## The real remaining problem (main task)
Camera sees the WHOLE ROOM, not just the TV → colour accuracy suffers + strip glow feedback. Highest-value fix: **Region Of Interest on the TV screen**:
- (A) one-time calibration: mark/auto-detect TV corners, store in config, sample only inside. Most robust.
- (B) temporal-variance mask: TV = pixels that change; wall = static. Combine with vivid mask fallback for static images.
- (C) perspective-correct the ROI (TV viewed at an angle).
Also: consider LOCKING camera AWB/AE for stability (see `perception/vision/camera.py`) — but the user APPROVED the camera's base colour output; do NOT change that pipeline, only lock gains if it demonstrably helps ambilight.
Then build a verify tool: show camera frame beside chosen colour, per-frame.

## Testing (dev-mode API, no token)
```
curl -s -X POST http://127.0.0.1:8000/led/tv -d '{"on":true}'    # sync on
curl -s -X POST http://127.0.0.1:8000/led/tv -d '{"on":false}'   # sync off
curl -s http://127.0.0.1:8000/led                                # state
curl -s -X POST http://127.0.0.1:8000/led -d '{"cmd":"named","value":"red"}'
curl -s http://127.0.0.1:8000/camera/snapshot                    # JSON {jpeg_b64}
PYTHONPATH=/home/pi/robot python3 tools/ambilight_test.py 30
ffmpeg -i clip.mov -vf "fps=3,scale=320:-1" frame_%03d.jpg       # analyze recorded content
```
Live view: http://100.101.250.126:8080 . Logs: `pm2 logs cosmo`. The "plug in C920" log is harmless (CSI camera works).

## Agreement
Commit+push each change; restart cosmo to deploy; verify on hardware. Keep named-constant tuning. Don't touch banteragent or the camera's approved colour pipeline.
