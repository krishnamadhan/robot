import lgpio, time

PIR = 4
h = lgpio.gpiochip_open(4)
lgpio.gpio_claim_input(h, PIR, lgpio.SET_PULL_NONE)

events = []
state = {'active': False, 'count': 0, 'last_on': None, 'last_off': None, 'duration': 0}

def status_bar(active):
    if active: return "▓▓▓▓▓▓▓▓▓▓  MOTION  ▓▓▓▓▓▓▓▓▓▓"
    return      "░░░░░░░░░░  quiet   ░░░░░░░░░░"

print("Warming up PIR sensor (30s)...", flush=True)
start = time.time()
while time.time() - start < 30:
    remaining = int(30 - (time.time() - start))
    print(f"\r  {remaining:2d}s remaining...  ", end="", flush=True)
    time.sleep(0.5)
print("\n\nPIR ready!\n", flush=True)
time.sleep(0.5)
print("\033[2J", flush=True)

# Seed initial state so startup HIGH doesn't log a false motion event
state['active'] = lgpio.gpio_read(h, PIR) == 1

try:
    while True:
        active = lgpio.gpio_read(h, PIR) == 1

        if active and not state['active']:
            state['active'] = True
            state['count'] += 1
            state['last_on'] = time.time()
            events.append(time.strftime('%H:%M:%S') + " MOTION")

        if not active and state['active']:
            state['active'] = False
            if state['last_on']:
                state['duration'] = time.time() - state['last_on']
            state['last_off'] = time.time()
            events.append(time.strftime('%H:%M:%S') + f" clear  ({state['duration']:.1f}s)")

        if len(events) > 6: events.pop(0)

        elapsed  = f"{time.time() - state['last_on']:.1f}s" if state['active'] and state['last_on'] else "--"
        last_dur = f"{state['duration']:.1f}s" if state['duration'] else "--"
        cooldown = max(0, 2.5 - (time.time() - state['last_off'])) if state['last_off'] else 0

        print("\033[H", end="", flush=True)
        print("=" * 44)
        print("   HC-SR501 PIR MOTION  —  Ctrl+C stop")
        print("=" * 44)
        print(f"\n  {status_bar(active)}\n")
        print(f"  Detections:  {state['count']}")
        print(f"  Active for:  {elapsed}")
        print(f"  Last motion: {last_dur}")
        if cooldown > 0:
            print(f"  Cooldown:    {cooldown:.1f}s remaining  ")
        else:
            print(f"  Cooldown:    ready              ")
        print(f"\n  Event log:")
        for e in (events[-5:] if events else ["  (none yet)"]):
            print(f"    {e}")
        print("=" * 44, flush=True)
        time.sleep(0.05)

except KeyboardInterrupt:
    pass
finally:
    lgpio.gpiochip_close(h)
    print("\nStopped.")
