"""
Eye animation engine — 12 expressions, 30 FPS loop.

Render modes:
  "terminal" → Unicode ASCII art (active now)
  "oled"     → luma.oled SSD1306 128×64 (when hardware arrives)
  "png"      → saves frames to /tmp/ for visual testing

When OLED arrives: change render_target in config or call
  eye_engine.set_render_target("oled")
  No other code changes needed.
"""

import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from core.event_bus import Event, EventPriority, EventType, bus
from utils.logger import get_logger

log = get_logger(__name__)


class EyeExpression(Enum):
    NEUTRAL   = "neutral"
    HAPPY     = "happy"
    EXCITED   = "excited"
    SAD       = "sad"
    ANGRY     = "angry"
    SURPRISED = "surprised"
    SLEEPY    = "sleepy"
    LOVING    = "loving"
    CURIOUS   = "curious"
    SCARED    = "scared"
    CONFUSED  = "confused"
    PLAYFUL   = "playful"   # wink


@dataclass
class EyeState:
    expression: EyeExpression       = EyeExpression.NEUTRAL
    target_expression: EyeExpression = EyeExpression.NEUTRAL
    pupil_x: float                  = 0.0   # -1.0 (left) to 1.0 (right)
    pupil_y: float                  = 0.0   # -1.0 (up)   to 1.0 (down)
    blink_progress: float           = 0.0   # 0 open, 1 closed
    transition_progress: float      = 1.0   # 1.0 = fully arrived
    brightness: float               = 1.0
    scared_tremor: float            = 0.0   # 0-1, adds jitter


# ── ASCII frame data for each expression ─────────────────────────────────────
# Each entry: (left_eye_lines, right_eye_lines) — 5 rows each

_FRAMES: Dict[EyeExpression, Tuple[List[str], List[str]]] = {
    EyeExpression.NEUTRAL: (
        ["╭───╮", "│ ◉ │", "│   │", "╰───╯", "     "],
        ["╭───╮", "│ ◉ │", "│   │", "╰───╯", "     "],
    ),
    EyeExpression.HAPPY: (
        ["╭───╮", "│ ^ │", "╰───╯", "  ‿  ", "     "],
        ["╭───╮", "│ ^ │", "╰───╯", "  ‿  ", "     "],
    ),
    EyeExpression.EXCITED: (
        ["╭─────╮", "│ ◎ ✦ │", "│     │", "╰─────╯", "       "],
        ["╭─────╮", "│ ✦ ◎ │", "│     │", "╰─────╯", "       "],
    ),
    EyeExpression.SAD: (
        ["  ___  ", "╱     ╲", "│  ●  │", "│_____│", "       "],
        ["  ___  ", "╱     ╲", "│  ●  │", "│_____│", "       "],
    ),
    EyeExpression.ANGRY: (
        ["╲─────╮", "│  ●  │", "│─────│", "╰─────╯", "       "],
        ["╭─────╱", "│  ●  │", "│─────│", "╰─────╯", "       "],
    ),
    EyeExpression.SURPRISED: (
        ["╭─────╮", "│     │", "│  ⊙  │", "│     │", "╰─────╯"],
        ["╭─────╮", "│     │", "│  ⊙  │", "│     │", "╰─────╯"],
    ),
    EyeExpression.SLEEPY: (
        ["       ", "───────", "│  ─  │", "╰─────╯", "       "],
        ["       ", "───────", "│  ─  │", "╰─────╯", "       "],
    ),
    EyeExpression.LOVING: (
        ["╭─────╮", "│  ♥  │", "│     │", "╰─────╯", "       "],
        ["╭─────╮", "│  ♥  │", "│     │", "╰─────╯", "       "],
    ),
    EyeExpression.CURIOUS: (
        ["╭─────╮", "│  ◉  │", "│─────│", "╰─────╯", "       "],
        [" ╭────╮", " │  ◉ │", " ╰────╯", "       ", "       "],
    ),
    EyeExpression.SCARED: (
        ["╭─────╮", "│ ○○○ │", "│     │", "╰─────╯", "       "],
        ["╭─────╮", "│ ○○○ │", "│     │", "╰─────╯", "       "],
    ),
    EyeExpression.CONFUSED: (
        ["╭─────╮", "│  ●  │", "│─────│", "╰─────╯", "       "],
        ["╭~────╮", "│  ~  │", "│─────│", "╰─────╯", "       "],
    ),
    EyeExpression.PLAYFUL: (
        ["╭─────╮", "│  ─  │", "╰─────╯", "  ‿  ",  "       "],  # wink
        ["╭─────╮", "│  ◉  │", "│     │", "╰─────╯", "       "],
    ),
}

# Event → expression mapping
_EVENT_EXPR: Dict[EventType, EyeExpression] = {
    EventType.FACE_RECOGNIZED:   EyeExpression.HAPPY,
    EventType.TOUCH_DETECTED:    EyeExpression.LOVING,
    EventType.PICKUP_DETECTED:   EyeExpression.SURPRISED,
    EventType.GESTURE_DETECTED:  EyeExpression.CURIOUS,
    EventType.BATTERY_CRITICAL:  EyeExpression.SCARED,
    EventType.OBSTACLE_CRITICAL: EyeExpression.SCARED,
}

_EMOTION_EXPR: Dict[str, EyeExpression] = {
    "happy":     EyeExpression.HAPPY,
    "sad":       EyeExpression.SAD,
    "angry":     EyeExpression.ANGRY,
    "surprised": EyeExpression.SURPRISED,
    "fearful":   EyeExpression.SCARED,
    "fear":      EyeExpression.SCARED,
    "disgust":   EyeExpression.ANGRY,
    "neutral":   EyeExpression.NEUTRAL,
}


class EyeEngine:
    """
    30 FPS eye animation loop with expressions, blinking, and pupil tracking.
    Subscribes to event bus automatically when start() is called.
    """

    BLINK_INTERVAL_MIN = 3.0
    BLINK_INTERVAL_MAX = 7.0
    BLINK_SPEED        = 0.15    # seconds for full blink cycle
    TRANSITION_SPEED   = 0.3     # seconds for expression transition
    FPS                = 30

    def __init__(self) -> None:
        self._state             = EyeState()
        self._running           = False
        self._render_target     = "terminal"
        self._next_blink        = time.monotonic() + random.uniform(3, 7)
        self._blinking          = False
        self._blink_start       = 0.0
        self._transition_start  = 0.0
        self._prev_expression   = EyeExpression.NEUTRAL
        self._timed_expr_end: Optional[float] = None
        self._timed_expr_prev:  Optional[EyeExpression] = None
        self._frame_callbacks: List[Callable] = []
        self._oled_left  = None
        self._oled_right = None

    async def start(self) -> None:
        self._running = True

        @bus.on(EventType.EMOTION_DETECTED)
        async def _on_emotion(event: Event) -> None:
            emotion = event.data.get("emotion", "")
            expr = _EMOTION_EXPR.get(emotion.lower())
            if expr:
                self.set_expression(expr, duration=4.0)

        for evt_type, expr in _EVENT_EXPR.items():
            async def _handler(event: Event, _expr=expr) -> None:
                self.set_expression(_expr, duration=3.0)
            bus.on(evt_type)(_handler)

        asyncio.create_task(self._animation_loop())
        log.info("eyes.started", target=self._render_target)

    async def stop(self) -> None:
        self._running = False

    def set_expression(self, expr: EyeExpression,
                       duration: Optional[float] = None) -> None:
        if self._state.target_expression == expr:
            return
        self._prev_expression       = self._state.expression
        self._state.target_expression = expr
        self._state.transition_progress = 0.0
        self._transition_start      = time.monotonic()
        if duration:
            self._timed_expr_end  = time.monotonic() + duration
            self._timed_expr_prev = self._prev_expression
        else:
            self._timed_expr_end  = None
            self._timed_expr_prev = None

    def set_pupil(self, x: float, y: float) -> None:
        self._state.pupil_x = max(-1.0, min(1.0, x))
        self._state.pupil_y = max(-1.0, min(1.0, y))

    def set_render_target(self, target: str) -> None:
        self._render_target = target
        if target == "oled":
            self._init_oled()
        log.info("eyes.render_target", target=target)

    def _init_oled(self) -> None:
        """Initialise luma.oled SSD1306 on both I2C addresses."""
        try:
            from luma.oled.device import ssd1306
            from luma.core.interface.serial import i2c as luma_i2c
            self._oled_left  = ssd1306(luma_i2c(port=1, address=0x3C))
            self._oled_right = ssd1306(luma_i2c(port=1, address=0x3D))
            log.info("eyes.oled_init", left="0x3C", right="0x3D")
        except Exception as e:
            log.warning("eyes.oled_init_failed", error=str(e)[:80])
            self._render_target = "terminal"  # fallback

    def get_state(self) -> EyeState:
        return self._state

    # ── Animation loop ────────────────────────────────────────────────────────

    async def _animation_loop(self) -> None:
        frame_time = 1.0 / self.FPS
        while self._running:
            now = time.monotonic()
            self._tick(now)
            for cb in self._frame_callbacks:
                try:
                    cb(self._state)
                except Exception:
                    pass
            if self._render_target == "oled" and self._oled_left and self._oled_right:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._render_oled)
            await asyncio.sleep(frame_time)

    def _tick(self, now: float) -> None:
        s = self._state

        # Timed expression revert
        if self._timed_expr_end and now >= self._timed_expr_end:
            prev = self._timed_expr_prev or EyeExpression.NEUTRAL
            self._timed_expr_end = None
            self.set_expression(prev)

        # Transition progress
        if s.transition_progress < 1.0:
            elapsed = now - self._transition_start
            s.transition_progress = min(1.0, elapsed / self.TRANSITION_SPEED)
            if s.transition_progress >= 1.0:
                s.expression = s.target_expression

        # Auto blink
        if not self._blinking and now >= self._next_blink:
            self._blinking  = True
            self._blink_start = now
        if self._blinking:
            elapsed = now - self._blink_start
            half = self.BLINK_SPEED / 2
            if elapsed < half:
                s.blink_progress = elapsed / half
            elif elapsed < self.BLINK_SPEED:
                s.blink_progress = 1.0 - (elapsed - half) / half
            else:
                s.blink_progress = 0.0
                self._blinking   = False
                self._next_blink = now + random.uniform(
                    self.BLINK_INTERVAL_MIN, self.BLINK_INTERVAL_MAX
                )

        # Scared tremor
        if s.expression == EyeExpression.SCARED:
            s.scared_tremor = random.uniform(0.3, 1.0)
        else:
            s.scared_tremor = 0.0

        # Breathing (NEUTRAL only) — subtle brightness pulse
        if s.expression == EyeExpression.NEUTRAL:
            s.brightness = 0.9 + 0.1 * math.sin(now * 1.2)
        else:
            s.brightness = 1.0

    # ── OLED pixel renderer ───────────────────────────────────────────────────

    # Expression → (eye_w_ratio, eye_h_ratio, brow_angle, pupil_size_ratio)
    # eye dimensions as fraction of display height; brow_angle in degrees (+up/-down)
    _OLED_PARAMS: Dict[str, Tuple] = {
        EyeExpression.NEUTRAL:   (0.55, 0.65, 0,    0.40),
        EyeExpression.HAPPY:     (0.58, 0.50, 8,    0.38),  # squint up
        EyeExpression.EXCITED:   (0.60, 0.75, 12,   0.42),
        EyeExpression.SAD:       (0.50, 0.55, -10,  0.36),
        EyeExpression.ANGRY:     (0.52, 0.58, -15,  0.34),
        EyeExpression.SURPRISED: (0.58, 0.85, 5,    0.45),
        EyeExpression.SLEEPY:    (0.50, 0.35, -5,   0.30),  # half-closed
        EyeExpression.LOVING:    (0.56, 0.58, 10,   0.42),
        EyeExpression.CURIOUS:   (0.54, 0.70, 6,    0.38),
        EyeExpression.SCARED:    (0.56, 0.82, 0,    0.46),
        EyeExpression.CONFUSED:  (0.52, 0.65, -5,   0.36),
        EyeExpression.PLAYFUL:   (0.58, 0.68, 14,   0.40),
    }

    def _draw_eye(self, draw, cx: int, cy: int, ew: int, eh: int,
                  px: float, py: float, blink: float,
                  brow_deg: float, pupil_r: int) -> None:
        """Draw one eye onto a Pillow ImageDraw context."""
        # Blink: shrink vertical radius
        actual_eh = max(2, int(eh * (1.0 - blink)))

        # Eye white (filled ellipse)
        draw.ellipse(
            [cx - ew // 2, cy - actual_eh // 2,
             cx + ew // 2, cy + actual_eh // 2],
            fill=255, outline=255,
        )
        # Pupil (dark circle, moves with pupil_x/y)
        pupil_cx = cx + int(px * (ew // 2 - pupil_r))
        pupil_cy = cy + int(py * (actual_eh // 2 - pupil_r))
        draw.ellipse(
            [pupil_cx - pupil_r, pupil_cy - pupil_r,
             pupil_cx + pupil_r, pupil_cy + pupil_r],
            fill=0,
        )
        # Highlight dot (white, top-left of pupil)
        hl = max(2, pupil_r // 3)
        draw.ellipse(
            [pupil_cx - pupil_r // 2 - hl, pupil_cy - pupil_r // 2 - hl,
             pupil_cx - pupil_r // 2 + hl, pupil_cy - pupil_r // 2 + hl],
            fill=255,
        )
        # Eyebrow (line above eye)
        if not blink > 0.8:
            brow_y = cy - actual_eh // 2 - 6
            brow_dx = int(math.tan(math.radians(brow_deg)) * ew // 2)
            draw.line(
                [cx - ew // 2, brow_y + brow_dx,
                 cx + ew // 2, brow_y - brow_dx],
                fill=255, width=2,
            )

    def _render_oled(self) -> None:
        """Render current eye state to both SSD1306 OLEDs. Runs in executor."""
        try:
            from PIL import Image, ImageDraw
            s    = self._state
            expr = s.expression
            W, H = 128, 64
            params = self._OLED_PARAMS.get(expr, self._OLED_PARAMS[EyeExpression.NEUTRAL])
            ew_ratio, eh_ratio, brow_deg, pr_ratio = params
            ew = int(W * ew_ratio)
            eh = int(H * eh_ratio)
            pupil_r = int(min(ew, eh) * pr_ratio * 0.5)
            cx, cy  = W // 2, H // 2

            for oled, eye_side in [(self._oled_left, -1), (self._oled_right, 1)]:
                img  = Image.new("1", (W, H), 0)
                draw = ImageDraw.Draw(img)
                # Mirror pupil_x for left eye
                px = s.pupil_x * eye_side
                py = s.pupil_y
                # Mirror brow angle for left eye
                brow = brow_deg * eye_side
                self._draw_eye(draw, cx, cy, ew, eh,
                               px, py, s.blink_progress,
                               brow, pupil_r)
                oled.display(img)
        except Exception as e:
            log.debug("eyes.oled_render_error", error=str(e)[:60])

    # ── Terminal renderer ─────────────────────────────────────────────────────

    def render_terminal(self) -> str:
        s      = self._state
        expr   = s.expression
        left_f, right_f = _FRAMES[expr]
        rows   = max(len(left_f), len(right_f))

        # Blink override — replace with flat lines when closed
        blink_thresh = 0.5
        if s.blink_progress > blink_thresh:
            blink_row = "───────"
            width = max(len(left_f[0]), 7)
            left_f  = [blink_row.center(width)] * rows
            right_f = [blink_row.center(width)] * rows

        # Scared tremor offset
        tremor_offset = ""
        if s.scared_tremor > 0.5:
            tremor_offset = " " * random.randint(0, 1)

        gap = "   "
        lines = []
        for i in range(rows):
            l = left_f[i]  if i < len(left_f)  else " " * len(left_f[0])
            r = right_f[i] if i < len(right_f) else " " * len(right_f[0])
            lines.append(tremor_offset + l + gap + r)

        return "\n".join(lines)

    def render_frame(self) -> dict:
        """Returns structured frame dict for OLED renderer (future)."""
        s = self._state
        return {
            "expression":          s.expression.value,
            "pupil_x":             s.pupil_x,
            "pupil_y":             s.pupil_y,
            "blink_progress":      s.blink_progress,
            "transition_progress": s.transition_progress,
            "brightness":          s.brightness,
        }

    def add_frame_callback(self, cb: Callable) -> None:
        """Register a callback called every frame with current EyeState."""
        self._frame_callbacks.append(cb)

    @property
    def current_expression(self) -> EyeExpression:
        return self._state.expression


eye_engine = EyeEngine()
