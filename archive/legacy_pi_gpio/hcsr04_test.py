import lgpio, time
h = lgpio.gpiochip_open(4)
lgpio.gpio_claim_output(h, 24)
lgpio.gpio_claim_input(h, 26, lgpio.SET_PULL_NONE)

print("Wave hand in front of sensor...")
for i in range(30):
    lgpio.gpio_write(h, 24, 1)
    time.sleep(0.00001)
    lgpio.gpio_write(h, 24, 0)
    t = time.time()
    while lgpio.gpio_read(h, 26) == 0 and time.time()-t < 0.05: pass
    t1 = time.time()
    while lgpio.gpio_read(h, 26) == 1 and time.time()-t1 < 0.05: pass
    t2 = time.time()
    dist = (t2 - t1) * 34300 / 2
    bar = int(min(dist, 100) / 5)
    filled = "#" * bar
    empty = "-" * (20 - bar)
    print(f"{dist:6.1f} cm  {filled}{empty}", flush=True)
    time.sleep(0.15)

lgpio.gpiochip_close(h)
