#!/usr/bin/env python3
"""Petbot eyes — v4 with extended animations.
   25fps, dual SH1106 128x64 on I2C bus 1 & 4.
"""
import time, random, math
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106
from PIL import Image, ImageDraw


class RobotEyes:

    MOODS = {
        'normal':    {'w':70,'h':50,'px':0, 'py':0, 'ps':22,'lid':0.0,'ang': 0},
        'happy':     {'w':74,'h':42,'px':0, 'py':-4,'ps':19,'lid':0.38,'ang':15},
        'sad':       {'w':64,'h':44,'px':0, 'py': 5,'ps':20,'lid':0.30,'ang':-20},
        'angry':     {'w':72,'h':42,'px':0, 'py': 0,'ps':18,'lid':0.42,'ang':26},
        'surprised': {'w':82,'h':58,'px':0, 'py': 0,'ps':28,'lid':0.0, 'ang': 0},
        'sleepy':    {'w':70,'h':18,'px':0, 'py': 4,'ps':13,'lid':0.60,'ang': 0},
        'curious':   {'w':66,'h':56,'px':8, 'py':-4,'ps':26,'lid':0.1, 'ang':-12},
    }

    def __init__(self):
        self.d1 = sh1106(i2c(port=1, address=0x3C))
        self.d4 = sh1106(i2c(port=4, address=0x3C))
        self.W, self.H  = 128, 64
        self.FRAME_TIME = 1.0 / 15

        self.st = {k: float(v) for k, v in self.MOODS['normal'].items()}
        self.tg = self.st.copy()
        self.blink      = 0.0
        self.pupil_mode = 'round'   # 'round' | 'heart' | 'dizzy' | 'star' | 'spiral'
        self.heart_size = 0.0
        self.tear_phase = 0.0       # 0 = no tear; > 0 = teardrop below eye

    # ── primitives ───────────────────────────────────────────────────
    def _restore_normal(self):
        self.tg.update({k: float(v) for k, v in self.MOODS['normal'].items()})

    def _animate_blink_overlay(self, close_frames=2, hold_frames=1, open_frames=4):
        for i in range(1, close_frames + 1):
            self.blink = i / close_frames
            t = time.time()
            self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))
        for _ in range(hold_frames):
            t = time.time()
            self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))
        for i in range(1, open_frames + 1):
            self.blink = 1.0 - i / open_frames
            t = time.time()
            self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))
        self.blink = 0.0

    def _hold_pose(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            t = time.time()
            self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))

    # ── special pupil shapes ─────────────────────────────────────────
    def _draw_heart(self, draw, cx, cy, size, fill="black"):
        s = max(6, int(size))
        r = max(2, s // 4)
        draw.ellipse([cx - s//2, cy - r*2, cx,       cy + r//2], fill=fill)
        draw.ellipse([cx,        cy - r*2, cx + s//2, cy + r//2], fill=fill)
        draw.polygon([
            (cx - s//2, cy - r//2),
            (cx + s//2, cy - r//2),
            (cx,        cy + s//2),
        ], fill=fill)

    def _draw_dizzy(self, draw, cx, cy, size, fill="black"):
        s = max(6, int(size))
        t = max(2, s // 5)
        draw.line([cx-s//2, cy-s//2, cx+s//2, cy+s//2], fill=fill, width=t)
        draw.line([cx+s//2, cy-s//2, cx-s//2, cy+s//2], fill=fill, width=t)
        r = t
        for dx, dy in [(-s//2,-s//2),(s//2,-s//2),(-s//2,s//2),(s//2,s//2)]:
            draw.ellipse([cx+dx-r, cy+dy-r, cx+dx+r, cy+dy+r], fill=fill)

    def _star_points(self, cx, cy, outer_r, inner_r, points=5, rotation=0):
        pts = []
        for i in range(points * 2):
            angle = math.radians(rotation + i * 180 / points - 90)
            r = outer_r if i % 2 == 0 else inner_r
            pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        return pts

    def _draw_spiral_pupil(self, draw, cx, cy, size, fill="black"):
        s = max(6, int(size))
        for i in range(3):
            r = max(2, s * (3 - i) // 3)
            w = max(2, s // 6)
            draw.arc([cx - r, cy - r, cx + r, cy + r],
                     start=i * 120, end=i * 120 + 270, fill=fill, width=w)

    # ── interpolation ────────────────────────────────────────────────
    def _step(self, speed=0.18):
        for k in self.st:
            self.st[k] += (self.tg[k] - self.st[k]) * speed

    # ── drawing ──────────────────────────────────────────────────────
    def _draw(self, draw):
        cx, cy = self.W // 2, self.H // 2
        w   = max(4, int(self.st['w']))
        h   = max(2, int(self.st['h']))
        px  = int(self.st['px'])
        py  = int(self.st['py'])
        ps  = max(4, int(self.st['ps']))
        lid = max(self.st['lid'], self.blink)
        ang = self.st['ang']

        x1, y1 = cx - w//2, cy - h//2
        x2, y2 = cx + w//2, cy + h//2

        # sclera
        draw.rounded_rectangle((x1, y1, x2, y2), radius=11, fill="white")

        # eyelid mask
        if lid > 0.01:
            lid_y = y1 + int(h * lid)
            tilt  = math.tan(math.radians(ang)) * (w / 2)
            draw.polygon([
                (x1-2, y1-2),         (x2+2, y1-2),
                (x2+2, lid_y + tilt), (x1-2, lid_y - tilt),
            ], fill="black")

        # pupil
        if lid < 0.93:
            ppx = max(x1+ps//2+3, min(x2-ps//2-3, cx+px))
            ppy = max(y1+ps//2+3, min(y2-ps//2-3, cy+py))

            if self.pupil_mode == 'heart':
                hs = int(ps * (0.6 + 0.5 * self.heart_size))
                self._draw_heart(draw, ppx, ppy, hs, fill="black")
                draw.ellipse([ppx-hs//4-1, ppy-hs//2,
                               ppx-hs//4+3, ppy-hs//2+4], fill="white")

            elif self.pupil_mode == 'dizzy':
                draw.rounded_rectangle(
                    (ppx-ps//2, ppy-ps//2, ppx+ps//2, ppy+ps//2),
                    radius=5, fill="black")
                self._draw_dizzy(draw, ppx, ppy, ps-4, fill="white")

            elif self.pupil_mode == 'star':
                pts = self._star_points(ppx, ppy, ps//2, ps//4, 5, -90)
                draw.polygon(pts, fill="black")
                draw.ellipse([ppx-2, ppy-2, ppx+2, ppy+2], fill="white")

            elif self.pupil_mode == 'spiral':
                draw.rounded_rectangle(
                    (ppx-ps//2, ppy-ps//2, ppx+ps//2, ppy+ps//2),
                    radius=5, fill="black")
                self._draw_spiral_pupil(draw, ppx, ppy, ps-4, fill="white")

            else:  # round
                draw.rounded_rectangle(
                    (ppx-ps//2, ppy-ps//2, ppx+ps//2, ppy+ps//2),
                    radius=5, fill="black")
                draw.rectangle([ppx-ps//2+3, ppy-ps//2+3,
                                 ppx-ps//2+7, ppy-ps//2+7], fill="white")
                draw.rectangle([ppx+ps//2-7, ppy-ps//2+4,
                                 ppx+ps//2-4, ppy-ps//2+7], fill="white")

        # teardrop (below eye when tear_phase > 0)
        if self.tear_phase > 0.05:
            ty = y2 + int(self.tear_phase * 8)
            draw.ellipse([cx-3, ty-4, cx+3, ty+4], fill="white")

    def _render(self):
        img = Image.new('1', (self.W, self.H), 0)
        draw = ImageDraw.Draw(img)
        self._draw(draw)
        self.d1.display(img)
        self.d4.display(img)

    # ── animation loop ────────────────────────────────────────────────
    def _run_for(self, seconds, speed=0.18):
        end = time.time() + seconds
        while time.time() < end:
            t = time.time()
            self._step(speed)
            self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))

    # ── blink ─────────────────────────────────────────────────────────
    def do_blink(self):
        # faster close (2f ~80ms), brief hold (1f), slower open (4f ~160ms)
        self._animate_blink_overlay(close_frames=2, hold_frames=1, open_frames=4)

    def do_double_blink(self):
        self.do_blink()
        time.sleep(0.06)
        self.do_blink()

    # ── look ──────────────────────────────────────────────────────────
    def do_look(self, tx, ty):
        ox, oy = self.tg['px'], self.tg['py']
        self.tg['px'], self.tg['py'] = float(tx), float(ty)
        self._run_for(0.40, speed=0.24)
        time.sleep(0.15)
        self.tg['px'], self.tg['py'] = ox, oy
        self._run_for(0.35, speed=0.18)

    # ── mood expressions ──────────────────────────────────────────────
    def do_expression(self, mood, duration=1.2):
        self.tg.update({k: float(v) for k, v in self.MOODS[mood].items()})
        self._run_for(duration, speed=0.13)
        self._restore_normal()
        self._run_for(0.6, speed=0.11)

    def do_dart(self):
        ox = self.tg['px']
        for x in [22.0, -22.0, 15.0, -15.0, 0.0]:
            self.tg['px'] = x
            self._run_for(0.09, speed=0.55)
        self.tg['px'] = ox

    def do_wink(self):
        for v in [0.4, 0.8, 1.0, 1.0, 0.8, 0.4, 0.0]:
            self.blink = v
            t = time.time()
            self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))
        self.blink = 0.0

    # ── extended animations ───────────────────────────────────────────
    def do_squint(self, duration=0.8):
        self.tg['lid'] = 0.48
        self.tg['ang'] = 12.0
        self._run_for(duration, speed=0.22)
        self._restore_normal()
        self._run_for(0.4, speed=0.15)

    def do_roll_eyes(self):
        # full circular roll: up → right → down → left → center
        path = [
            (0, -18), (12, -14), (18, 0), (12, 14),
            (0, 18), (-12, 14), (-18, 0), (-12, -14), (0, 0),
        ]
        for tx, ty in path:
            self.tg['px'] = float(tx)
            self.tg['py'] = float(ty)
            self._run_for(0.07, speed=0.45)
        self._restore_normal()

    def do_shy(self):
        self.tg['py']  = 10.0
        self.tg['lid'] = 0.32
        self.tg['ang'] = 8.0
        self.tg['w']   = float(self.MOODS['normal']['w']) - 6
        self._run_for(0.4, speed=0.18)
        self._hold_pose(0.9)
        self._restore_normal()
        self._run_for(0.5, speed=0.14)

    def do_excited(self):
        self.tg.update({k: float(v) for k, v in self.MOODS['surprised'].items()})
        self._run_for(0.25, speed=0.35)
        for _ in range(4):
            self.tg['py'] = -6.0
            self._run_for(0.06, speed=0.6)
            self.tg['py'] = 4.0
            self._run_for(0.06, speed=0.6)
        self._restore_normal()
        self._run_for(0.4, speed=0.15)

    def do_think(self):
        self.tg['px']  = 14.0
        self.tg['py']  = -10.0
        self.tg['lid'] = 0.18
        self.tg['ang'] = -6.0
        self._run_for(0.5, speed=0.16)
        for dx in [16.0, 12.0, 18.0, 14.0]:
            self.tg['px'] = dx
            self._run_for(0.18, speed=0.22)
        self._restore_normal()
        self._run_for(0.5, speed=0.14)

    def do_cry(self):
        self.tg.update({k: float(v) for k, v in self.MOODS['sad'].items()})
        self._run_for(0.5, speed=0.14)
        for _ in range(3):
            for phase in [0.2, 0.5, 0.8, 1.0, 1.2, 1.5]:
                self.tear_phase = phase
                t = time.time()
                self._step(0.04)
                self._render()
                time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))
            self.tear_phase = 0.0
            time.sleep(0.1)
        self._restore_normal()
        self._run_for(0.6, speed=0.12)

    def do_laugh(self):
        self.tg.update({k: float(v) for k, v in self.MOODS['happy'].items()})
        self._run_for(0.25, speed=0.3)
        for _ in range(4):
            self._animate_blink_overlay(close_frames=2, hold_frames=0, open_frames=2)
            time.sleep(0.04)
        self._hold_pose(0.3)
        self._restore_normal()
        self._run_for(0.5, speed=0.14)

    def do_scan(self):
        self.tg.update({k: float(v) for k, v in self.MOODS['curious'].items()})
        self._run_for(0.3, speed=0.2)
        for tx in [-20.0, -10.0, 0.0, 10.0, 20.0, 10.0, 0.0, -10.0, -20.0, 0.0]:
            self.tg['px'] = tx
            self._run_for(0.08, speed=0.5)
        self._restore_normal()
        self._run_for(0.4, speed=0.15)

    def do_flirt(self):
        self.tg.update({k: float(v) for k, v in self.MOODS['happy'].items()})
        self._run_for(0.25, speed=0.3)
        self.do_wink()
        self.pupil_mode = 'heart'
        for _ in range(2):
            for v in [0.0, 0.5, 1.0, 0.5, 0.0]:
                self.heart_size = v
                t = time.time()
                self._step(0.08)
                self._render()
                time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))
            time.sleep(0.05)
        self.pupil_mode = 'round'
        self.heart_size = 0.0
        self._restore_normal()
        self._run_for(0.5, speed=0.14)

    def do_confused(self):
        self.tg['lid'] = 0.15
        self.tg['ang'] = -18.0
        self.tg['py']  = -4.0
        self._run_for(0.4, speed=0.2)
        for tx in [8.0, -8.0, 8.0, -8.0, 0.0]:
            self.tg['px'] = tx
            self._run_for(0.08, speed=0.5)
        self._hold_pose(0.3)
        self._restore_normal()
        self._run_for(0.5, speed=0.14)

    # ── special sequences ─────────────────────────────────────────────
    def do_love(self):
        self.tg.update({k: float(v) for k, v in self.MOODS['surprised'].items()})
        self._run_for(0.3, speed=0.3)
        self.pupil_mode = 'heart'
        for _ in range(3):
            for v in [0.0, 0.4, 0.8, 1.0, 1.0, 0.8, 0.4, 0.0]:
                self.heart_size = v
                t = time.time()
                self._step(0.05)
                self._render()
                time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))
            time.sleep(0.1)
        self.pupil_mode = 'round'
        self.heart_size = 0.0
        self._restore_normal()
        self._run_for(0.5, speed=0.15)

    def do_dizzy(self):
        self.tg.update({k: float(v) for k, v in self.MOODS['surprised'].items()})
        self._run_for(0.2, speed=0.35)
        self.pupil_mode = 'dizzy'
        self._run_for(1.2, speed=0.08)
        self.pupil_mode = 'round'
        self._restore_normal()
        self._run_for(0.5, speed=0.12)

    # ── main loop ─────────────────────────────────────────────────────
    def run(self):
        self._restore_normal()
        print("Petbot eyes v4 — Ctrl+C to stop")

        t_blink  = time.time() + random.uniform(2, 4)
        t_action = time.time() + random.uniform(3, 7)

        ACTIONS = [
            ('look',         5),
            ('expr',         3),
            ('dart',         2),
            ('wink',         1),
            ('love',         2),
            ('dizzy',        1),
            ('double_blink', 2),
            ('squint',       1),
            ('roll',         1),
            ('shy',          1),
            ('excited',      2),
            ('think',        2),
            ('scan',         1),
            ('flirt',        1),
            ('confused',     1),
        ]
        choices, weights = zip(*ACTIONS)

        try:
            while True:
                t = time.time()
                self._step()
                self._render()

                now = time.time()
                if now > t_blink:
                    self.do_blink()
                    t_blink = time.time() + random.uniform(2, 5)

                if now > t_action:
                    choice = random.choices(choices, weights=list(weights))[0]
                    if choice == 'look':
                        self.do_look(random.choice([-20,-12,0,12,20]),
                                     random.choice([-8,0,8]))
                    elif choice == 'expr':
                        self.do_expression(random.choice(
                            ['happy','sad','angry','surprised','sleepy','curious']))
                    elif choice == 'dart':         self.do_dart()
                    elif choice == 'wink':         self.do_wink()
                    elif choice == 'love':         self.do_love()
                    elif choice == 'dizzy':        self.do_dizzy()
                    elif choice == 'double_blink': self.do_double_blink()
                    elif choice == 'squint':       self.do_squint()
                    elif choice == 'roll':         self.do_roll_eyes()
                    elif choice == 'shy':          self.do_shy()
                    elif choice == 'excited':      self.do_excited()
                    elif choice == 'think':        self.do_think()
                    elif choice == 'scan':         self.do_scan()
                    elif choice == 'flirt':        self.do_flirt()
                    elif choice == 'confused':     self.do_confused()
                    t_action = time.time() + random.uniform(3, 7)

                time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))

        except KeyboardInterrupt:
            self.d1.clear(); self.d4.clear()
            print("\nStopped.")


RobotEyes().run()
