import lgpio, time, os, math

TRIG, ECHO = 24, 26

h = lgpio.gpiochip_open(4)
lgpio.gpio_claim_output(h, TRIG)
lgpio.gpio_claim_input(h, ECHO, lgpio.SET_PULL_NONE)

def read_dist():
    lgpio.gpio_write(h, TRIG, 1)
    time.sleep(0.00001)
    lgpio.gpio_write(h, TRIG, 0)
    t = time.time()
    while lgpio.gpio_read(h, ECHO) == 0 and time.time()-t < 0.05: pass
    t1 = time.time()
    while lgpio.gpio_read(h, ECHO) == 1 and time.time()-t1 < 0.05: pass
    t2 = time.time()
    return (t2 - t1) * 34300 / 2

ZONES = [
    (10,  "DANGER ZONE",   "🚨", "GET AWAY FROM ME!!"),
    (20,  "TOO CLOSE",     "😳", "Personal space please!"),
    (40,  "CLOSE",         "👀", "I can see you..."),
    (70,  "NEAR",          "😊", "Hey there!"),
    (120, "COMFORTABLE",   "😌", "Nice distance."),
    (200, "FAR",           "👋", "Helloooo out there!"),
    (999, "EMPTY",         "😴", "zzz... anyone there?"),
]

def get_zone(d):
    for limit, name, emoji, msg in ZONES:
        if d <= limit:
            return name, emoji, msg
    return ZONES[-1][1], ZONES[-1][2], ZONES[-1][3]

def radar_bar(dist, max_d=200, width=30):
    pct = min(dist / max_d, 1.0)
    log_pct = math.log10(max(dist, 1)) / math.log10(max_d)
    filled = int(log_pct * width)
    filled = max(1, min(filled, width))
    if dist < 10:   ch = "!"
    elif dist < 30: ch = "#"
    elif dist < 80: ch = "="
    else:           ch = "-"
    return ch * filled + " " * (width - filled)

history = []
MIN_D = 999
MAX_D = 0

print("\033[2J\033[?25l", flush=True)  # clear, hide cursor

try:
    while True:
        d = read_dist()
        d = round(d, 1)
        history.append(d)
        if len(history) > 30: history.pop(0)

        if d < MIN_D: MIN_D = d
        if d > MAX_D and d < 400: MAX_D = d

        zone, emoji, msg = get_zone(d)
        bar = radar_bar(d)

        # sparkline from history
        spark_chars = " ▁▂▃▄▅▆▇█"
        hi = max(history) if max(history) > 0 else 1
        spark = "".join(spark_chars[int(v / hi * 8)] for v in history)

        print("\033[H", end="", flush=True)
        print("╔══════════════════════════════════════════╗")
        print("║        HC-SR04  PROXIMITY  RADAR         ║")
        print("╚══════════════════════════════════════════╝")
        print(f"")
        print(f"   Distance : {d:>6.1f} cm")
        print(f"   Zone     : {zone:<12}  {emoji}  {msg}")
        print(f"")
        print(f"   [{bar}]")
        print(f"    near                          far")
        print(f"")
        print(f"   History  : {spark}")
        print(f"   Min/Max  : {MIN_D:.1f} cm  /  {MAX_D:.1f} cm")
        print(f"")
        print(f"   ─────────────────────────────────────")
        print(f"   Move hand closer/farther to the sensor")
        print(f"   Ctrl+C to stop", flush=True)

        time.sleep(0.1)

except KeyboardInterrupt:
    pass
finally:
    print("\033[?25h")  # show cursor
    lgpio.gpiochip_close(h)
    print(f"\nStopped. Session min={MIN_D}cm  max={MAX_D}cm")
