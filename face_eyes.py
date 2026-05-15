#!/usr/bin/env python3
"""Petbot face-tracking eyes.
   Thread 1: C920 camera + Haar face detection @ 10fps, 320x240
   Thread 2: Eye render loop @ 15fps — pupils follow detected face
"""
import time, random, math, threading
import cv2
from PIL import Image, ImageDraw
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106

# ── shared face state ─────────────────────────────────────────────────
_lock        = threading.Lock()
_face_px     = 0.0   # target pupil x (-20 to +20)
_face_py     = 0.0   # target pupil y (-8  to +8)
_face_seen   = False
_face_new    = False  # True for one cycle when face first appears


def _face_thread():
    global _face_px, _face_py, _face_seen, _face_new

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  320)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
    cap.set(cv2.CAP_PROP_FPS, 10)

    FW, FH = 320, 240
    was_seen = False

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))

        with _lock:
            if len(faces) > 0:
                # largest face
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                cx = x + w // 2
                cy = y + h // 2
                # map to pupil range: ±20 x, ±8 y
                _face_px  = (cx - FW / 2) / (FW / 2) * 20.0
                _face_py  = (cy - FH / 2) / (FH / 2) * 8.0
                _face_new  = not was_seen
                _face_seen = True
                was_seen   = True
            else:
                _face_seen = False
                _face_new  = False
                was_seen   = False

        time.sleep(0.1)   # 10fps detection


# ── eye renderer ──────────────────────────────────────────────────────
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
        self.pupil_mode = 'round'
        self.heart_size = 0.0
        self.tear_phase = 0.0

        self._tracking   = False  # currently tracking a face
        self._idle_px    = 0.0    # saved idle pupil x when not tracking
        self._idle_py    = 0.0

    # ── primitives ────────────────────────────────────────────────
    def _restore_normal(self):
        self.tg.update({k: float(v) for k, v in self.MOODS['normal'].items()})

    def _animate_blink_overlay(self, close_frames=2, hold_frames=1, open_frames=4):
        for i in range(1, close_frames + 1):
            self.blink = i / close_frames
            t = time.time(); self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))
        for _ in range(hold_frames):
            t = time.time(); self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))
        for i in range(1, open_frames + 1):
            self.blink = 1.0 - i / open_frames
            t = time.time(); self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))
        self.blink = 0.0

    def _hold_pose(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            t = time.time(); self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))

    # ── special pupil shapes ──────────────────────────────────────
    def _draw_heart(self, draw, cx, cy, size, fill="black"):
        s = max(6, int(size)); r = max(2, s // 4)
        draw.ellipse([cx-s//2, cy-r*2, cx,      cy+r//2], fill=fill)
        draw.ellipse([cx,      cy-r*2, cx+s//2,  cy+r//2], fill=fill)
        draw.polygon([(cx-s//2, cy-r//2),(cx+s//2, cy-r//2),(cx, cy+s//2)], fill=fill)

    def _draw_dizzy(self, draw, cx, cy, size, fill="black"):
        s = max(6, int(size)); t = max(2, s // 5)
        draw.line([cx-s//2, cy-s//2, cx+s//2, cy+s//2], fill=fill, width=t)
        draw.line([cx+s//2, cy-s//2, cx-s//2, cy+s//2], fill=fill, width=t)
        for dx, dy in [(-s//2,-s//2),(s//2,-s//2),(-s//2,s//2),(s//2,s//2)]:
            draw.ellipse([cx+dx-t, cy+dy-t, cx+dx+t, cy+dy+t], fill=fill)

    # ── interpolation ─────────────────────────────────────────────
    def _step(self, speed=0.18):
        for k in self.st:
            self.st[k] += (self.tg[k] - self.st[k]) * speed

    # ── drawing ───────────────────────────────────────────────────
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

        draw.rounded_rectangle((x1, y1, x2, y2), radius=11, fill="white")

        if lid > 0.01:
            lid_y = y1 + int(h * lid)
            tilt  = math.tan(math.radians(ang)) * (w / 2)
            draw.polygon([
                (x1-2, y1-2),         (x2+2, y1-2),
                (x2+2, lid_y + tilt), (x1-2, lid_y - tilt),
            ], fill="black")

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
            else:
                draw.rounded_rectangle(
                    (ppx-ps//2, ppy-ps//2, ppx+ps//2, ppy+ps//2),
                    radius=5, fill="black")
                draw.rectangle([ppx-ps//2+3, ppy-ps//2+3,
                                 ppx-ps//2+7, ppy-ps//2+7], fill="white")
                draw.rectangle([ppx+ps//2-7, ppy-ps//2+4,
                                 ppx+ps//2-4, ppy-ps//2+7], fill="white")

        if self.tear_phase > 0.05:
            ty = y2 + int(self.tear_phase * 8)
            draw.ellipse([cx-3, ty-4, cx+3, ty+4], fill="white")

    def _render(self):
        img = Image.new('1', (self.W, self.H), 0)
        draw = ImageDraw.Draw(img)
        self._draw(draw)
        self.d1.display(img)
        self.d4.display(img)

    def _run_for(self, seconds, speed=0.18):
        end = time.time() + seconds
        while time.time() < end:
            t = time.time()
            self._step(speed); self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))

    # ── blink / idle actions ──────────────────────────────────────
    def do_blink(self):
        self._animate_blink_overlay(close_frames=2, hold_frames=1, open_frames=4)

    def do_look(self, tx, ty):
        ox, oy = self.tg['px'], self.tg['py']
        self.tg['px'], self.tg['py'] = float(tx), float(ty)
        self._run_for(0.40, 0.24)
        time.sleep(0.15)
        self.tg['px'], self.tg['py'] = ox, oy
        self._run_for(0.35, 0.18)

    def do_expression(self, mood, duration=1.2):
        self.tg.update({k: float(v) for k, v in self.MOODS[mood].items()})
        self._run_for(duration, 0.13)
        self._restore_normal()
        self._run_for(0.6, 0.11)

    def do_dart(self):
        ox = self.tg['px']
        for x in [22.0, -22.0, 15.0, -15.0, 0.0]:
            self.tg['px'] = x
            self._run_for(0.09, 0.55)
        self.tg['px'] = ox

    def do_wink(self):
        for v in [0.4, 0.8, 1.0, 1.0, 0.8, 0.4, 0.0]:
            self.blink = v
            t = time.time(); self._render()
            time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))
        self.blink = 0.0

    # ── face-seen reaction ────────────────────────────────────────
    def _react_to_face(self):
        # Wide surprised eyes, then settle to curious/attentive
        self.tg.update({k: float(v) for k, v in self.MOODS['surprised'].items()})
        self._run_for(0.4, 0.35)
        self.tg.update({k: float(v) for k, v in self.MOODS['curious'].items()})
        self._run_for(0.3, 0.2)

    # ── main loop ─────────────────────────────────────────────────
    def run(self):
        self._restore_normal()
        print("Face-tracking eyes — Ctrl+C to stop")
        print("Camera warming up...")
        time.sleep(1.5)

        t_blink  = time.time() + random.uniform(2, 4)
        t_action = time.time() + random.uniform(4, 8)

        try:
            while True:
                t = time.time()

                # read shared face state
                with _lock:
                    seen   = _face_seen
                    new    = _face_new
                    fpx    = _face_px
                    fpy    = _face_py

                if new:
                    # face just appeared — react then track
                    self._tracking = True
                    self._react_to_face()

                elif seen:
                    # smoothly track face position
                    self._tracking = True
                    self.tg['px'] = fpx
                    self.tg['py'] = fpy
                    # keep eye slightly open/attentive while tracking
                    self.tg['lid'] = 0.05
                    self.tg['w']   = float(self.MOODS['normal']['w'])
                    self.tg['h']   = float(self.MOODS['normal']['h'])
                    self.tg['ps']  = float(self.MOODS['normal']['ps'])
                    self.tg['ang'] = 0.0

                else:
                    # no face — idle animations
                    if self._tracking:
                        # face just left — glance around then idle
                        self._tracking = False
                        self._restore_normal()

                    now = time.time()
                    if now > t_blink:
                        self.do_blink()
                        t_blink = time.time() + random.uniform(2, 5)

                    if now > t_action:
                        choice = random.choices(
                            ['look', 'expr', 'dart', 'wink'],
                            weights=[5, 3, 2, 1])[0]
                        if choice == 'look':
                            self.do_look(random.choice([-20,-12,0,12,20]),
                                         random.choice([-8,0,8]))
                        elif choice == 'expr':
                            self.do_expression(random.choice(
                                ['happy','sad','curious','surprised','sleepy']))
                        elif choice == 'dart':
                            self.do_dart()
                        elif choice == 'wink':
                            self.do_wink()
                        t_action = time.time() + random.uniform(4, 8)

                self._step()
                self._render()
                time.sleep(max(0, self.FRAME_TIME - (time.time() - t)))

        except KeyboardInterrupt:
            self.d1.clear(); self.d4.clear()
            print("\nStopped.")


# ── start ──────────────────────────────────────────────────────────────
t = threading.Thread(target=_face_thread, daemon=True)
t.start()
RobotEyes().run()
