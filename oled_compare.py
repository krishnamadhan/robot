#!/usr/bin/env python3
"""Eye style comparator.
   d1 (bus 1): 3 mini eyes side-by-side — v1 ellipse, v2 rrect, v3 rounded_rect
   d4 (bus 4): text panel showing current animation + labels
"""
import time, random, math
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106
from PIL import Image, ImageDraw

d1 = sh1106(i2c(port=1, address=0x3C))   # 3-eye compare display
d4 = sh1106(i2c(port=4, address=0x3C))   # info text display

W, H = 128, 64
FRAME_TIME = 1.0 / 15

MOODS = {
    'normal':    {'w':70,'h':50,'px':0, 'py':0, 'ps':22,'lid':0.0,'ang': 0},
    'happy':     {'w':74,'h':42,'px':0, 'py':-4,'ps':19,'lid':0.38,'ang':15},
    'sad':       {'w':64,'h':44,'px':0, 'py': 5,'ps':20,'lid':0.30,'ang':-20},
    'angry':     {'w':72,'h':42,'px':0, 'py': 0,'ps':18,'lid':0.42,'ang':26},
    'surprised': {'w':82,'h':58,'px':0, 'py': 0,'ps':28,'lid':0.0, 'ang': 0},
    'sleepy':    {'w':70,'h':18,'px':0, 'py': 4,'ps':13,'lid':0.60,'ang': 0},
    'curious':   {'w':66,'h':56,'px':8, 'py':-4,'ps':26,'lid':0.1, 'ang':-12},
}

st  = {k: float(v) for k, v in MOODS['normal'].items()}
tg  = dict(st)
blink = 0.0
current_anim = "idle"

SCALE = 0.50   # mini eye scale factor
EYE_CX = [21, 64, 107]
EYE_CY = 26
LABEL_Y = 50


def step(speed=0.18):
    for k in st:
        st[k] += (tg[k] - st[k]) * speed


# ── v1 style: ellipse sclera, circle pupil ───────────────────────────
def draw_v1(draw, cx, cy):
    s = SCALE
    ew = max(4, int(st['w'] * s / 2))
    eh = max(2, int(st['h'] * s / 2))
    px = int(st['px'] * s * 0.6)
    py = int(st['py'] * s * 0.6)
    ps = max(3, int(st['ps'] * s))
    lid = max(st['lid'], blink)

    draw.ellipse([cx-ew, cy-eh, cx+ew, cy+eh], fill="white")

    if lid > 0.01:
        lid_y = cy - eh + int(eh * 2 * lid)
        draw.rectangle([cx-ew-1, cy-eh-1, cx+ew+1, lid_y], fill="black")

    if lid < 0.93:
        ppx = max(cx-ew+ps//2+1, min(cx+ew-ps//2-1, cx+px))
        ppy = max(cy-eh+ps//2+1, min(cy+eh-ps//2-1, cy+py))
        draw.ellipse([ppx-ps//2, ppy-ps//2, ppx+ps//2, ppy+ps//2], fill="black")
        draw.rectangle([ppx-ps//2+1, ppy-ps//2+1,
                         ppx-ps//2+3, ppy-ps//2+3], fill="white")


# ── v2 style: manual rrect sclera, rounded square pupil ─────────────
def _rrect(draw, x1, y1, x2, y2, r, fill="white"):
    r = min(r, (x2-x1)//2, (y2-y1)//2)
    draw.rectangle([x1+r, y1, x2-r, y2], fill=fill)
    draw.rectangle([x1, y1+r, x2, y2-r], fill=fill)
    draw.ellipse([x1,    y1,    x1+r*2, y1+r*2], fill=fill)
    draw.ellipse([x2-r*2, y1,    x2,    y1+r*2], fill=fill)
    draw.ellipse([x1,    y2-r*2, x1+r*2, y2],    fill=fill)
    draw.ellipse([x2-r*2, y2-r*2, x2,    y2],    fill=fill)


def draw_v2(draw, cx, cy):
    s = SCALE
    hw = max(4, int(st['w'] * s / 2))
    hh = max(2, int(st['h'] * s / 2))
    px = int(st['px'] * s * 0.6)
    py = int(st['py'] * s * 0.6)
    ps = max(3, int(st['ps'] * s))
    lid = max(st['lid'], blink)
    la  = st['ang'] * 0.3   # scale down angle effect

    _rrect(draw, cx-hw, cy-hh, cx+hw, cy+hh, 5)

    if lid > 0.01:
        lid_px = int(hh * 2 * lid)
        inner = int(la * hh * 0.5)
        outer = -inner
        draw.polygon([
            (cx-hw-1, cy-hh-1), (cx+hw+1, cy-hh-1),
            (cx+hw+1, cy-hh + lid_px + outer),
            (cx-hw-1, cy-hh + lid_px + inner),
        ], fill="black")

    if lid < 0.93:
        ppx = max(cx-hw+ps//2+1, min(cx+hw-ps//2-1, cx+px))
        ppy = max(cy-hh+ps//2+1, min(cy+hh-ps//2-1, cy+py))
        _rrect(draw, ppx-ps//2, ppy-ps//2, ppx+ps//2, ppy+ps//2, 3, fill="black")
        draw.rectangle([ppx-ps//2+1, ppy-ps//2+1,
                         ppx-ps//2+3, ppy-ps//2+3], fill="white")


# ── v3 style: Pillow rounded_rectangle, angled lid, 2-dot shine ─────
def draw_v3(draw, cx, cy):
    s = SCALE
    w  = max(4, int(st['w'] * s))
    h  = max(2, int(st['h'] * s))
    px = int(st['px'] * s * 0.6)
    py = int(st['py'] * s * 0.6)
    ps = max(3, int(st['ps'] * s))
    lid = max(st['lid'], blink)
    ang = st['ang']

    x1, y1 = cx - w//2, cy - h//2
    x2, y2 = cx + w//2, cy + h//2

    draw.rounded_rectangle((x1, y1, x2, y2), radius=6, fill="white")

    if lid > 0.01:
        lid_y = y1 + int(h * lid)
        tilt  = math.tan(math.radians(ang)) * (w / 2)
        draw.polygon([
            (x1-1, y1-1),         (x2+1, y1-1),
            (x2+1, lid_y + tilt), (x1-1, lid_y - tilt),
        ], fill="black")

    if lid < 0.93:
        ppx = max(x1+ps//2+1, min(x2-ps//2-1, cx+px))
        ppy = max(y1+ps//2+1, min(y2-ps//2-1, cy+py))
        draw.rounded_rectangle(
            (ppx-ps//2, ppy-ps//2, ppx+ps//2, ppy+ps//2),
            radius=3, fill="black")
        draw.rectangle([ppx-ps//2+1, ppy-ps//2+1,
                         ppx-ps//2+3, ppy-ps//2+3], fill="white")
        draw.rectangle([ppx+ps//2-3, ppy-ps//2+1,
                         ppx+ps//2-1, ppy-ps//2+3], fill="white")


# ── renderers ────────────────────────────────────────────────────────
def render_compare():
    img = Image.new('1', (W, H), 0)
    draw = ImageDraw.Draw(img)
    for i, cx in enumerate(EYE_CX):
        [draw_v1, draw_v2, draw_v3][i](draw, cx, EYE_CY)
    for cx, label in zip(EYE_CX, ["v1", "v2", "v3"]):
        draw.text((cx - 6, LABEL_Y), label, fill="white")
    draw.line([(42, 4), (42, 44)], fill="white", width=1)
    draw.line([(85, 4), (85, 44)], fill="white", width=1)
    d1.display(img)


def render_info():
    img = Image.new('1', (W, H), 0)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 127, 63], outline="white")
    draw.text((4, 4),  "ANIM COMPARE", fill="white")
    draw.line([(0, 14), (127, 14)], fill="white")
    draw.text((4, 18), "Now playing:", fill="white")
    draw.text((4, 28), f"> {current_anim[:20]}", fill="white")
    draw.line([(0, 42), (127, 42)], fill="white")
    draw.text((4, 46), "v1=oval v2=rrect", fill="white")
    draw.text((4, 55), "v3=pillow(best)", fill="white")
    d4.display(img)


def render_all():
    render_compare()
    render_info()


# ── animation helpers ─────────────────────────────────────────────────
def set_anim(name):
    global current_anim
    current_anim = name


def run_for(seconds, speed=0.18):
    end = time.time() + seconds
    while time.time() < end:
        t = time.time()
        step(speed)
        render_all()
        time.sleep(max(0, FRAME_TIME - (time.time() - t)))


def restore_normal():
    tg.update({k: float(v) for k, v in MOODS['normal'].items()})


def do_blink():
    global blink
    set_anim("do_blink")
    for v in [0.35, 0.7, 1.0, 1.0, 0.7, 0.35, 0.0]:
        blink = v
        t = time.time()
        render_all()
        time.sleep(max(0, FRAME_TIME - (time.time() - t)))
    blink = 0.0


def do_look(tx, ty):
    set_anim(f"do_look({tx},{ty})")
    ox, oy = tg['px'], tg['py']
    tg['px'], tg['py'] = float(tx), float(ty)
    run_for(0.40, 0.24)
    time.sleep(0.15)
    tg['px'], tg['py'] = ox, oy
    run_for(0.35, 0.18)


def do_expression(mood):
    set_anim(f"do_expr:{mood}")
    tg.update({k: float(v) for k, v in MOODS[mood].items()})
    run_for(1.2, 0.13)
    restore_normal()
    run_for(0.6, 0.11)


def do_dart():
    set_anim("do_dart")
    ox = tg['px']
    for x in [22.0, -22.0, 15.0, -15.0, 0.0]:
        tg['px'] = x
        run_for(0.09, 0.55)
    tg['px'] = ox


def do_wink():
    global blink
    set_anim("do_wink")
    for v in [0.4, 0.8, 1.0, 1.0, 0.8, 0.4, 0.0]:
        blink = v
        t = time.time()
        render_all()
        time.sleep(max(0, FRAME_TIME - (time.time() - t)))
    blink = 0.0


def do_squint():
    set_anim("do_squint")
    tg['lid'] = 0.48; tg['ang'] = 12.0
    run_for(0.8, 0.22)
    restore_normal()
    run_for(0.4, 0.15)


def do_roll_eyes():
    set_anim("do_roll_eyes")
    path = [(0,-18),(12,-14),(18,0),(12,14),(0,18),(-12,14),(-18,0),(-12,-14),(0,0)]
    for tx, ty in path:
        tg['px'] = float(tx); tg['py'] = float(ty)
        run_for(0.07, 0.45)
    restore_normal()


def do_excited():
    set_anim("do_excited")
    tg.update({k: float(v) for k, v in MOODS['surprised'].items()})
    run_for(0.25, 0.35)
    for _ in range(4):
        tg['py'] = -6.0; run_for(0.06, 0.6)
        tg['py'] =  4.0; run_for(0.06, 0.6)
    restore_normal()
    run_for(0.4, 0.15)


def do_think():
    set_anim("do_think")
    tg['px'] = 14.0; tg['py'] = -10.0
    tg['lid'] = 0.18; tg['ang'] = -6.0
    run_for(0.5, 0.16)
    for dx in [16.0, 12.0, 18.0, 14.0]:
        tg['px'] = dx; run_for(0.18, 0.22)
    restore_normal(); run_for(0.5, 0.14)


def do_confused():
    set_anim("do_confused")
    tg['lid'] = 0.15; tg['ang'] = -18.0; tg['py'] = -4.0
    run_for(0.4, 0.2)
    for tx in [8.0, -8.0, 8.0, -8.0, 0.0]:
        tg['px'] = tx; run_for(0.08, 0.5)
    restore_normal(); run_for(0.5, 0.14)


def do_scan():
    set_anim("do_scan")
    tg.update({k: float(v) for k, v in MOODS['curious'].items()})
    run_for(0.3, 0.2)
    for tx in [-20.0,-10.0,0.0,10.0,20.0,10.0,0.0,-10.0,-20.0,0.0]:
        tg['px'] = tx; run_for(0.08, 0.5)
    restore_normal(); run_for(0.4, 0.15)


def do_shy():
    set_anim("do_shy")
    tg['py'] = 10.0; tg['lid'] = 0.32; tg['ang'] = 8.0
    tg['w'] = float(MOODS['normal']['w']) - 6
    run_for(0.4, 0.18)
    end = time.time() + 0.9
    while time.time() < end:
        t = time.time(); render_all()
        time.sleep(max(0, FRAME_TIME - (time.time() - t)))
    restore_normal(); run_for(0.5, 0.14)


# ── main loop ─────────────────────────────────────────────────────────
restore_normal()
set_anim("idle")
print("Eye comparator — d1=3 styles, d4=info  |  Ctrl+C stop")

t_blink  = time.time() + random.uniform(2, 4)
t_action = time.time() + random.uniform(3, 7)

ACTIONS = [
    ('look',    5), ('expr',    3), ('dart',    2),
    ('wink',    1), ('squint',  1), ('roll',    1),
    ('excited', 2), ('think',   2), ('confused',1),
    ('scan',    1), ('shy',     1),
]
choices, weights = zip(*ACTIONS)

try:
    while True:
        t = time.time()
        set_anim("idle")
        step()
        render_all()

        now = time.time()
        if now > t_blink:
            do_blink()
            t_blink = time.time() + random.uniform(2, 5)

        if now > t_action:
            choice = random.choices(choices, weights=list(weights))[0]
            if choice == 'look':
                do_look(random.choice([-20,-12,0,12,20]),
                        random.choice([-8,0,8]))
            elif choice == 'expr':
                do_expression(random.choice(
                    ['happy','sad','angry','surprised','sleepy','curious']))
            elif choice == 'dart':    do_dart()
            elif choice == 'wink':    do_wink()
            elif choice == 'squint':  do_squint()
            elif choice == 'roll':    do_roll_eyes()
            elif choice == 'excited': do_excited()
            elif choice == 'think':   do_think()
            elif choice == 'confused':do_confused()
            elif choice == 'scan':    do_scan()
            elif choice == 'shy':     do_shy()
            t_action = time.time() + random.uniform(3, 7)

        time.sleep(max(0, FRAME_TIME - (time.time() - t)))

except KeyboardInterrupt:
    d1.clear(); d4.clear()
    print("\nStopped.")
