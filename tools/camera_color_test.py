"""
Camera colour-balance test tool.

Two modes:
  --api   Poll the running cosmo API at port 8000 (default when cosmo is up).
          Works without stopping cosmo.
  --direct  Open picamera2 directly (cosmo must be stopped first).

Usage:
    python3 tools/camera_color_test.py [--api] [--direct] [--host 127.0.0.1]
                                       [--hw-r 1.8] [--hw-b 2.0]
                                       [--sw-r 1.05] [--sw-g 0.90] [--sw-b 0.96]
                                       [--sat 1.1] [--shadow 8.0] [--ev 0.6]
                                       [--save /tmp/cosmo_cam] [--interval 2]

Keyboard controls (API mode only — push new gains live):
  r / R  decrease / increase hw_r by 0.1
  b / B  decrease / increase hw_b by 0.1
  g / G  decrease / increase sw_g by 0.05
  s / S  decrease / increase saturation by 0.05
  e / E  decrease / increase EV by 0.1
  w      write current values to color.toml via API
  q      quit
"""

import argparse
import base64
import io
import os
import sys
import time
import threading
import queue
from collections import deque
from pathlib import Path

import cv2
import numpy as np

ROBOT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROBOT_ROOT))

# ── argument parsing ───────────────────────────────────────────────────────────

def _parse():
    p = argparse.ArgumentParser(description="Cosmo camera colour-balance test")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--api",    action="store_true", help="Use running cosmo API (default)")
    mode.add_argument("--direct", action="store_true", help="Open picamera2 directly (stop cosmo first)")
    p.add_argument("--host",   default="127.0.0.1")
    p.add_argument("--port",   type=int, default=8000)
    p.add_argument("--hw-r",   type=float, default=None)
    p.add_argument("--hw-b",   type=float, default=None)
    p.add_argument("--sw-r",   type=float, default=None)
    p.add_argument("--sw-g",   type=float, default=None)
    p.add_argument("--sw-b",   type=float, default=None)
    p.add_argument("--sat",    type=float, default=None)
    p.add_argument("--shadow", type=float, default=None)
    p.add_argument("--ev",     type=float, default=None)
    p.add_argument("--save",   default="/tmp/cosmo_cam", help="Directory for JPEG samples")
    p.add_argument("--interval", type=float, default=2.0, help="Seconds between captures")
    p.add_argument("--count",  type=int,   default=0,   help="Stop after N frames (0 = run forever)")
    return p.parse_args()


# ── colour analysis ────────────────────────────────────────────────────────────

def analyze_frame(bgr: np.ndarray) -> dict:
    """Compute per-channel stats. bgr is a uint8 BGR image from OpenCV."""
    b, g, r = cv2.split(bgr)
    def _stats(ch):
        m = float(np.mean(ch))
        return {"mean": round(m, 1), "p5": int(np.percentile(ch, 5)), "p95": int(np.percentile(ch, 95))}
    r_s, g_s, b_s = _stats(r), _stats(g), _stats(b)
    rg = round(r_s["mean"] / g_s["mean"], 3) if g_s["mean"] > 1 else 0
    bg = round(b_s["mean"] / g_s["mean"], 3) if g_s["mean"] > 1 else 0
    return {
        "R": r_s, "G": g_s, "B": b_s,
        "R:G": rg, "B:G": bg,
        "dominant": "RED" if rg > 1.1 else ("BLUE" if bg > 1.1 else ("GREEN" if rg < 0.9 and bg < 0.9 else "NEUTRAL")),
    }


def _balance_bar(ratio: float, width: int = 20) -> str:
    """Visual bar showing deviation from 1.0 (neutral)."""
    mid = width // 2
    deviation = ratio - 1.0
    filled = int(deviation * mid * 3)
    filled = max(-mid, min(mid, filled))
    bar = [" "] * width
    bar[mid] = "|"
    if filled > 0:
        for i in range(mid + 1, min(mid + 1 + filled, width)):
            bar[i] = "█"
    elif filled < 0:
        for i in range(max(0, mid + filled), mid):
            bar[i] = "█"
    return "".join(bar)


# ── API mode ───────────────────────────────────────────────────────────────────

def _get_token() -> str:
    env_files = [
        Path.home() / "secrets" / "robot.env",
        Path.home() / "secrets" / "cosmo.env",
    ]
    for f in env_files:
        if f.exists():
            for line in f.read_text().splitlines():
                if line.startswith("ROBOT_API_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("ROBOT_API_TOKEN", "")


def api_get_frame(base_url: str, token: str) -> tuple[np.ndarray | None, dict]:
    import urllib.request, json as _json
    url = f"{base_url}/camera/snapshot"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
        if "jpeg_b64" not in data:
            return None, data
        raw = base64.b64decode(data["jpeg_b64"])
        arr = np.frombuffer(raw, np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return bgr, data
    except Exception as e:
        return None, {"error": str(e)}


def api_get_color(base_url: str, token: str) -> dict:
    import urllib.request, json as _json
    req = urllib.request.Request(f"{base_url}/camera/color")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return _json.loads(resp.read())
    except Exception:
        return {}


def api_set_color(base_url: str, token: str, cfg: dict) -> dict:
    import urllib.request, json as _json
    data = _json.dumps(cfg).encode()
    req = urllib.request.Request(
        f"{base_url}/camera/color",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return _json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


# ── direct picamera2 mode ──────────────────────────────────────────────────────

class DirectCamera:
    """Thin wrapper around picamera2 for the test tool."""

    def __init__(self, cfg: dict):
        from picamera2 import Picamera2
        try:
            tuning = Picamera2.load_tuning_file("imx708_wide.json")
            self._cam = Picamera2(tuning=tuning)
        except Exception:
            self._cam = Picamera2()
        video_cfg = self._cam.create_video_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            controls={"FrameRate": 15.0, "AwbEnable": True, "AeEnable": True},
        )
        self._cam.configure(video_cfg)
        self._cam.start()
        time.sleep(3.0)
        self._cam.set_controls({
            "AwbEnable": False,
            "ColourGains": (cfg["hw_r"], cfg["hw_b"]),
            "ExposureValue": cfg["ev"],
        })
        self._cfg = cfg

    def read(self) -> np.ndarray | None:
        try:
            rgb = self._cam.capture_array()
            f = rgb.astype("float32")
            c = self._cfg
            f[:, :, 0] = np.clip(f[:, :, 0] * c["sw_r"], 0, 255)
            f[:, :, 1] = np.clip(f[:, :, 1] * c["sw_g"], 0, 255)
            f[:, :, 2] = np.clip(f[:, :, 2] * c["sw_b"], 0, 255)
            sat = c["saturation"]
            if sat != 1.0:
                luma = f[:, :, 0] * 0.299 + f[:, :, 1] * 0.587 + f[:, :, 2] * 0.114
                luma3 = luma[:, :, np.newaxis]
                f = np.clip(luma3 + sat * (f - luma3), 0, 255)
            if c["shadow"] > 0:
                luma = f[:, :, 0] * 0.299 + f[:, :, 1] * 0.587 + f[:, :, 2] * 0.114
                shadow_w = np.clip((100.0 - luma) / 100.0, 0.0, 1.0) ** 1.5
                f[:, :, 2] = np.clip(f[:, :, 2] - shadow_w * c["shadow"], 0, 255)
            return cv2.cvtColor(f.astype("uint8"), cv2.COLOR_RGB2BGR)
        except Exception:
            return None

    def apply_gains(self):
        try:
            self._cam.set_controls({
                "ColourGains": (self._cfg["hw_r"], self._cfg["hw_b"]),
                "ExposureValue": self._cfg["ev"],
            })
        except Exception:
            pass

    def close(self):
        try:
            self._cam.stop()
            self._cam.close()
        except Exception:
            pass


# ── Rich display ───────────────────────────────────────────────────────────────

def _render(stats_history: deque, cfg: dict, save_dir: str, last_save: str, mode: str, status: str):
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns
    from rich import box

    console = Console()
    console.clear()

    # Current averages over last N frames
    if stats_history:
        avg_R = round(sum(s["R"]["mean"] for s in stats_history) / len(stats_history), 1)
        avg_G = round(sum(s["G"]["mean"] for s in stats_history) / len(stats_history), 1)
        avg_B = round(sum(s["B"]["mean"] for s in stats_history) / len(stats_history), 1)
        avg_rg = round(sum(s["R:G"] for s in stats_history) / len(stats_history), 3)
        avg_bg = round(sum(s["B:G"] for s in stats_history) / len(stats_history), 3)
        latest = stats_history[-1]
    else:
        avg_R = avg_G = avg_B = 0.0
        avg_rg = avg_bg = 1.0
        latest = {"R": {"mean": 0}, "G": {"mean": 0}, "B": {"mean": 0},
                  "R:G": 1.0, "B:G": 1.0, "dominant": "?"}

    # Colour stats table
    t = Table(title=f"Colour Stats  ({len(stats_history)} frames)", box=box.SIMPLE, show_header=True)
    t.add_column("Channel", style="bold")
    t.add_column("Mean (latest)", justify="right")
    t.add_column("Mean (avg)", justify="right")
    t.add_column("Ratio:G", justify="right")
    t.add_column("Balance bar", min_width=22)

    rg_col = "green" if 0.9 <= avg_rg <= 1.1 else "red"
    bg_col = "green" if 0.9 <= avg_bg <= 1.1 else "blue"

    t.add_row("R", f"[red]{latest['R']['mean']:.1f}[/]", f"[red]{avg_R}[/]",
              f"[{rg_col}]{avg_rg:.3f}[/]", _balance_bar(avg_rg))
    t.add_row("G", f"[green]{latest['G']['mean']:.1f}[/]", f"[green]{avg_G}[/]", "1.000 (ref)", "")
    t.add_row("B", f"[blue]{latest['B']['mean']:.1f}[/]", f"[blue]{avg_B}[/]",
              f"[{bg_col}]{avg_bg:.3f}[/]", _balance_bar(avg_bg))

    dominant = latest.get("dominant", "?")
    dom_col = {"NEUTRAL": "green", "RED": "red", "BLUE": "blue", "GREEN": "green"}.get(dominant, "white")
    console.print(Panel(t, title=f"[{dom_col}]Dominant: {dominant}[/]", border_style=dom_col))

    # Colour config table
    cfg_table = Table(title="Colour Config", box=box.SIMPLE)
    cfg_table.add_column("Key")
    cfg_table.add_column("Value", justify="right")
    cfg_table.add_column("Note", style="dim")
    notes = {
        "hw_r": "ISP ColourGains red (lower=more red on wide lens)",
        "hw_b": "ISP ColourGains blue (higher=less blue)",
        "sw_r": "software R multiplier",
        "sw_g": "software G multiplier",
        "sw_b": "software B multiplier",
        "saturation": "vividity (1.0=neutral)",
        "shadow": "blue subtraction in shadows",
        "ev": "AE exposure stops",
    }
    for k, v in cfg.items():
        cfg_table.add_row(k, f"{v:.3f}", notes.get(k, ""))
    console.print(cfg_table)

    console.print(f"[dim]Mode: {mode}  |  Samples: {save_dir}  |  Last save: {last_save or 'none yet'}[/dim]")
    console.print(f"[dim]Status: {status}[/dim]")
    if mode == "api":
        console.print("\n[bold]Keys:[/bold] r/R=hw_r±0.1  b/B=hw_b±0.1  g/G=sw_g±0.05  s/S=sat±0.05  e/E=ev±0.1  w=write  q=quit")
    else:
        console.print("\n[bold]Ctrl+C[/bold] to quit")


# ── keyboard listener (non-blocking, Unix only) ────────────────────────────────

def _start_key_listener(key_q: queue.Queue):
    import tty, termios, select

    def _loop():
        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
        except Exception:
            return  # not a real TTY (e.g. piped input) — skip keyboard listener
        try:
            tty.setraw(fd)
            while True:
                r, _, _ = select.select([sys.stdin], [], [], 0.1)
                if r:
                    ch = sys.stdin.read(1)
                    key_q.put(ch)
                    if ch == "q":
                        break
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    args = _parse()

    save_dir = Path(args.save)
    save_dir.mkdir(parents=True, exist_ok=True)

    # Build initial colour config
    cfg = {
        "hw_r": 1.8, "hw_b": 2.0,
        "sw_r": 1.05, "sw_g": 0.90, "sw_b": 0.96,
        "saturation": 1.1, "shadow": 8.0, "ev": 0.6,
    }
    overrides = {
        "hw_r": args.hw_r, "hw_b": args.hw_b, "sw_r": args.sw_r,
        "sw_g": args.sw_g, "sw_b": args.sw_b, "saturation": args.sat,
        "shadow": args.shadow, "ev": args.ev,
    }
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v

    # Pick mode
    use_direct = args.direct
    if not use_direct and not args.api:
        # Auto-detect: try API first
        import urllib.request
        try:
            urllib.request.urlopen(f"http://{args.host}:{args.port}/health", timeout=2)
            use_direct = False
        except Exception:
            use_direct = True

    base_url = f"http://{args.host}:{args.port}"
    token = _get_token()
    mode = "direct" if use_direct else "api"

    # In API mode, load current config from server
    if not use_direct:
        server_cfg = api_get_color(base_url, token)
        if server_cfg:
            for k in cfg:
                if k in server_cfg:
                    cfg[k] = server_cfg[k]
            for k, v in overrides.items():
                if v is not None:
                    cfg[k] = v
            # push any CLI overrides to server
            if any(v is not None for v in overrides.values()):
                api_set_color(base_url, token, {k: v for k, v in overrides.items() if v is not None})

    # Open direct camera if needed
    direct_cam = None
    if use_direct:
        print("Opening picamera2 directly (make sure cosmo is stopped)…")
        try:
            direct_cam = DirectCamera(cfg)
        except Exception as e:
            print(f"Failed to open camera: {e}")
            sys.exit(1)

    stats_history: deque = deque(maxlen=15)
    last_save = ""
    status = "starting…"
    key_q: queue.Queue = queue.Queue()
    frame_n = 0

    if mode == "api":
        _start_key_listener(key_q)

    try:
        while True:
            t0 = time.monotonic()

            # Capture frame
            bgr = None
            if use_direct:
                bgr = direct_cam.read()
                if bgr is None:
                    status = "camera read failed"
                else:
                    status = f"direct  frame#{frame_n}"
            else:
                bgr, meta = api_get_frame(base_url, token)
                if bgr is None:
                    status = f"API error: {meta.get('error', '?')}"
                else:
                    status = f"API  frame#{meta.get('frame_id','?')}  age={meta.get('age_ms','?')}ms"

            if bgr is not None:
                frame_n += 1
                stats = analyze_frame(bgr)
                stats_history.append(stats)

                # Save sample
                fname = save_dir / f"frame_{frame_n:05d}.jpg"
                cv2.imwrite(str(fname), bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
                last_save = str(fname)

            # Render dashboard
            _render(stats_history, cfg, str(save_dir), last_save, mode, status)

            if args.count > 0 and frame_n >= args.count:
                break

            # Handle keyboard input (API mode)
            if mode == "api":
                changed = False
                try:
                    while True:
                        ch = key_q.get_nowait()
                        if ch == "q":
                            raise KeyboardInterrupt
                        elif ch == "r":
                            cfg["hw_r"] = round(cfg["hw_r"] - 0.1, 2); changed = True
                        elif ch == "R":
                            cfg["hw_r"] = round(cfg["hw_r"] + 0.1, 2); changed = True
                        elif ch == "b":
                            cfg["hw_b"] = round(cfg["hw_b"] - 0.1, 2); changed = True
                        elif ch == "B":
                            cfg["hw_b"] = round(cfg["hw_b"] + 0.1, 2); changed = True
                        elif ch == "g":
                            cfg["sw_g"] = round(cfg["sw_g"] - 0.05, 3); changed = True
                        elif ch == "G":
                            cfg["sw_g"] = round(cfg["sw_g"] + 0.05, 3); changed = True
                        elif ch == "s":
                            cfg["saturation"] = round(cfg["saturation"] - 0.05, 3); changed = True
                        elif ch == "S":
                            cfg["saturation"] = round(cfg["saturation"] + 0.05, 3); changed = True
                        elif ch == "e":
                            cfg["ev"] = round(cfg["ev"] - 0.1, 2); changed = True
                        elif ch == "E":
                            cfg["ev"] = round(cfg["ev"] + 0.1, 2); changed = True
                        elif ch == "w":
                            result = api_set_color(base_url, token, cfg)
                            status = f"wrote: {result.get('updated', result.get('error', '?'))}"
                except queue.Empty:
                    pass

                if changed:
                    api_set_color(base_url, token, cfg)

            # Sleep for remaining interval
            elapsed = time.monotonic() - t0
            sleep = args.interval - elapsed
            if sleep > 0:
                time.sleep(sleep)

    except KeyboardInterrupt:
        pass
    finally:
        if direct_cam:
            direct_cam.close()

    # Print summary
    print(f"\nSaved {frame_n} frames to {save_dir}/")
    print("Final colour config:")
    for k, v in cfg.items():
        print(f"  {k} = {v}")
    print("\nTo update color.toml manually:")
    kv = "  ".join(f'{k} = {v}' for k, v in cfg.items())
    print(f"  Edit config/color.toml: {kv}")


if __name__ == "__main__":
    main()
