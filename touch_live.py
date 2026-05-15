#!/usr/bin/env python3
"""TTP223 Touch Sensor Live — GPIO5 (left), GPIO25 (right)
   Wire: VCC→3.3V, GND→GND, OUT→GPIO pin
"""
from gpiozero import Button
import time, threading

TOUCH_L = Button(5,  pull_up=False)
TOUCH_R = Button(25, pull_up=False)

state = {
    'l': False, 'r': False,
    'l_count': 0, 'r_count': 0,
    'last': 'waiting...',
    'combo': 0, 'combo_timer': 0
}

def on_l():
    state['l'] = True
    state['l_count'] += 1
    state['last'] = f"LEFT  touched  (total: {state['l_count']})"
    if state['r']:
        state['combo'] += 1
        state['combo_timer'] = 15
        state['last'] = f"BOTH at once!  combo x{state['combo']}"

def off_l(): state['l'] = False
def on_r():
    state['r'] = True
    state['r_count'] += 1
    state['last'] = f"RIGHT touched  (total: {state['r_count']})"
    if state['l']:
        state['combo'] += 1
        state['combo_timer'] = 15
        state['last'] = f"BOTH at once!  combo x{state['combo']}"

def off_r(): state['r'] = False

TOUCH_L.when_pressed  = on_l
TOUCH_L.when_released = off_l
TOUCH_R.when_pressed  = on_r
TOUCH_R.when_released = off_r

def finger_art(l, r):
    left  = " ██ TOUCH ██ " if l else "   (left)    "
    right = " ██ TOUCH ██ " if r else "   (right)   "
    return left, right

print("\033[2J", flush=True)

try:
    while True:
        if state['combo_timer'] > 0: state['combo_timer'] -= 1
        else: pass

        left_art, right_art = finger_art(state['l'], state['r'])
        combo_line = f"  COMBO! Both touched x{state['combo']}  " if state['combo_timer'] > 0 else ""

        print("\033[H", end="", flush=True)
        print("=" * 42)
        print("   TTP223 TOUCH SENSORS  —  Ctrl+C stop")
        print("=" * 42)
        print(f"\n  {left_art}   {right_art}")
        print(f"\n  Left:  {'ACTIVE  ◉' if state['l'] else 'idle    ○'}   count: {state['l_count']}")
        print(f"  Right: {'ACTIVE  ◉' if state['r'] else 'idle    ○'}   count: {state['r_count']}")
        print(f"\n  Last:  {state['last']:<35}")
        print(f"  {combo_line:<40}")
        print(f"\n  Tip: tap fast, hold, tap both together!")
        print("=" * 42, flush=True)
        time.sleep(0.05)

except KeyboardInterrupt:
    print("\nStopped.")
