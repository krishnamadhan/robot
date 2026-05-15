import time
import math
import random
from copy import deepcopy

from PIL import ImageDraw
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106
from luma.core.render import canvas


serial1 = i2c(port=1, address=0x3C)
serial2 = i2c(port=4, address=0x3C)

device1 = sh1106(serial1, width=128, height=64)
device2 = sh1106(serial2, width=128, height=64)

FPS = 25
FRAME_TIME = 1.0 / FPS

WIDTH = 128
HEIGHT = 64

EYE_CENTER_Y = 32
LEFT_EYE_X = 36
RIGHT_EYE_X = 92
MAX_EYE_W = 34
MAX_EYE_H = 40

def rrect(draw, x1, y1, x2, y2, radius=6, fill=255):
    radius = max(1, int(radius))
    draw.rectangle((x1 + radius, y1, x2 - radius, y2), fill=fill)
    draw.rectangle((x1, y1 + radius, x2, y2 - radius), fill=fill)
    draw.pieslice((x1, y1, x1 + radius * 2, y1 + radius * 2), 180, 270, fill=fill)
    draw.pieslice((x2 - radius * 2, y1, x2, y1 + radius * 2), 270, 360, fill=fill)
    draw.pieslice((x1, y2 - radius * 2, x1 + radius * 2, y2), 90, 180, fill=fill)
    draw.pieslice((x2 - radius * 2, y2 - radius * 2, x2, y2), 0, 90, fill=fill)

NORMAL_STATE = {
    "eye_w": 30, "eye_h": 32, "pupil_x": 0, "pupil_y": 0,
    "pupil_size": 10, "lid_top": 0.0, "lid_angle": 0.0,
}

MOODS = {
    "normal":    {"eye_w":30,"eye_h":32,"pupil_x":0,"pupil_y":0,"pupil_size":10,"lid_top":0.0,"lid_angle":0.0},
    "happy":     {"eye_w":32,"eye_h":22,"pupil_x":0,"pupil_y":2,"pupil_size":8,"lid_top":0.25,"lid_angle":-0.25},
    "sad":       {"eye_w":28,"eye_h":28,"pupil_x":0,"pupil_y":4,"pupil_size":9,"lid_top":0.15,"lid_angle":0.25},
    "angry":     {"eye_w":32,"eye_h":24,"pupil_x":0,"pupil_y":0,"pupil_size":9,"lid_top":0.35,"lid_angle":-0.45},
    "surprised": {"eye_w":34,"eye_h":40,"pupil_x":0,"pupil_y":0,"pupil_size":8,"lid_top":0.0,"lid_angle":0.0},
    "sleepy":    {"eye_w":30,"eye_h":14,"pupil_x":0,"pupil_y":2,"pupil_size":7,"lid_top":0.55,"lid_angle":0.0},
    "curious":   {"eye_w":34,"eye_h":30,"pupil_x":4,"pupil_y":-2,"pupil_size":8,"lid_top":0.1,"lid_angle":-0.15},
}

current_state = deepcopy(NORMAL_STATE)
target_state  = deepcopy(NORMAL_STATE)

def lerp(a, b, t): return a + (b - a) * t

def update_state(speed=0.18):
    for key in current_state:
        current_state[key] = lerp(current_state[key], target_state[key], speed)

def set_target(state_dict):
    global target_state
    target_state = deepcopy(state_dict)

def draw_eye(draw, cx, cy, state):
    eye_w = state["eye_w"]; eye_h = state["eye_h"]
    pupil_x = state["pupil_x"]; pupil_y = state["pupil_y"]
    pupil_size = state["pupil_size"]
    lid_top = state["lid_top"]; lid_angle = state["lid_angle"]

    x1=int(cx-eye_w/2); y1=int(cy-eye_h/2)
    x2=int(cx+eye_w/2); y2=int(cy+eye_h/2)

    rrect(draw, x1, y1, x2, y2, radius=8, fill=255)

    max_px = eye_w * 0.22; max_py = eye_h * 0.18
    px = int(cx + max(-max_px, min(max_px, pupil_x)))
    py = int(cy + max(-max_py, min(max_py, pupil_y)))
    ps = int(pupil_size)
    rrect(draw, px-ps//2, py-ps//2, px+ps//2, py+ps//2, radius=3, fill=0)

    if lid_top > 0:
        lid_h = int(eye_h * lid_top)
        left_offset  = int(lid_angle * eye_w)
        right_offset = int(-lid_angle * eye_w)
        draw.polygon([
            (x1-2, y1-2), (x2+2, y1-2),
            (x2+2, y1+lid_h+right_offset),
            (x1-2, y1+lid_h+left_offset),
        ], fill=0)

def render_frame():
    for device in (device1, device2):
        with canvas(device) as draw:
            draw_eye(draw, LEFT_EYE_X,  EYE_CENTER_Y, current_state)
            draw_eye(draw, RIGHT_EYE_X, EYE_CENTER_Y, current_state)

def animate_for(seconds, speed=0.18):
    end_time = time.time() + seconds
    while time.time() < end_time:
        start = time.time()
        update_state(speed)
        render_frame()
        time.sleep(max(0, FRAME_TIME - (time.time() - start)))

def do_blink():
    original = deepcopy(target_state)
    closed = deepcopy(target_state); closed["eye_h"]=4; closed["lid_top"]=1.0
    set_target(closed);    animate_for(0.12, speed=0.35)
    set_target(original);  animate_for(0.18, speed=0.28)

def do_look(tx, ty):
    original = deepcopy(target_state)
    look = deepcopy(target_state); look["pupil_x"]=tx; look["pupil_y"]=ty
    set_target(look);     animate_for(0.35, speed=0.14)
    set_target(original); animate_for(0.35, speed=0.12)

def do_expression(mood, duration=1.5):
    if mood not in MOODS: return
    set_target(MOODS[mood]);    animate_for(duration,  speed=0.12)
    set_target(MOODS["normal"]); animate_for(0.7, speed=0.10)

def do_dart():
    original = deepcopy(target_state)
    for x in [-10, 10, -8, 8, 0]:
        dart = deepcopy(target_state); dart["pupil_x"] = x
        set_target(dart); animate_for(0.08, speed=0.35)
    set_target(original); animate_for(0.2, speed=0.18)

last_blink  = time.time()
next_blink  = random.uniform(2.0, 5.0)
last_action = time.time()
set_target(MOODS["normal"])

print("Eyes v3 — Ctrl+C to stop")
try:
    while True:
        start = time.time()
        update_state(); render_frame()
        now = time.time()
        if now - last_blink > next_blink:
            do_blink(); last_blink=time.time(); next_blink=random.uniform(2.0,5.0)
        if now - last_action > random.uniform(3.0, 6.0):
            action = random.choice(["look","dart","mood"])
            if action == "look":
                do_look(random.randint(-10,10), random.randint(-5,5))
            elif action == "dart":
                do_dart()
            elif action == "mood":
                do_expression(random.choice(["happy","sad","angry","surprised","sleepy","curious"]),
                              duration=random.uniform(0.8,1.8))
            last_action = time.time()
        time.sleep(max(0, FRAME_TIME-(time.time()-start)))
except KeyboardInterrupt:
    device1.clear(); device2.clear()
    print("\nStopped.")
