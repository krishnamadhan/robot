# NEXT_SESSION.md — Task Handoff
> Last updated: 2026-05-30

---

## Context
Pi was rebooted. All hardware disconnected (no UPS HAT, no motors, nothing on GPIO header).
Cosmo was DELETED from PM2 (not just stopped) — nothing holds any GPIO pin.
Goal this session: get a definitive, clean GPIO alive/dead map, then reconnect hardware one by one.

---

## Step 1 — Clean pin test (first thing, before connecting anything)

```bash
# Confirm cosmo is gone
pm2 status   # cosmo should NOT appear

# Run full pin test — nothing connected
python3 /home/pi/robot/tools/pin_test.py --all
```

This tests all 22 non-reserved GPIOs using `pinctrl` (Pi 5 native, reliable).
Expected result: most alive, some stuck LOW (truly dead), compare with pre-reboot.

---

## Step 2 — Manual multimeter check for any uncertain pins

For any pin you want to physically verify — drive it HIGH/LOW and measure:
```bash
pinctrl set <gpio> op dh   # drive HIGH → measure ~3.3V on header pin
# measure with multimeter: black GND (Pin 6/9/14/20/25/30/34/39), red on target pin
pinctrl set <gpio> op dl   # drive LOW → measure ~0V
pinctrl set <gpio> ip pn   # restore to input
```

Physical pin mapping for suspects:
| GPIO | Physical Pin | Pre-reboot pinctrl result |
|------|-------------|--------------------------|
| GPIO4  | Pin 7  | stuck LOW (0,0) |
| GPIO5  | Pin 29 | stuck LOW — was touch HEAD |
| GPIO7  | Pin 26 | stuck LOW |
| GPIO8  | Pin 24 | stuck HIGH — was PIR pin |
| GPIO9  | Pin 21 | stuck HIGH — motor right_rear PWM |
| GPIO12 | Pin 32 | stuck LOW |
| GPIO13 | Pin 33 | stuck HIGH — motor left_rear PWM |
| GPIO14 | Pin 8  | stuck LOW — UART TX (console=serial0 in cmdline.txt) |
| GPIO19 | Pin 35 | stuck LOW |
| GPIO21 | Pin 40 | stuck HIGH |

---

## Step 3 — If GPIO14 is still stuck LOW (UART conflict)

GPIO14 is needed for ultrasonic ECHO. Fix by removing serial console:
```bash
# Check current cmdline
cat /boot/firmware/cmdline.txt
# Should contain: console=serial0,115200

# Remove it (Pi accessed via Tailscale SSH — serial console not needed)
sudo sed -i 's/console=serial0,115200 //' /boot/firmware/cmdline.txt
sudo reboot
# After reboot: rerun pin test, GPIO14 should now respond
```

---

## Step 4 — Update configs based on confirmed results

After multimeter + software test gives final truth:
1. Update `config/hardware.yaml` — mark confirmed dead pins as null
2. Update `CLAUDE.md` GPIO map — mark with confirmed date
3. Add cosmo back to PM2: `pm2 start ecosystem.config.js && pm2 save`

---

## Step 5 — Connect hardware one by one

Order: UPS HAT → motors → PCA9685 (servos)

```bash
# After UPS HAT:
sudo i2cdetect -y 1   # expect 0x36

# After PCA9685:
sudo i2cdetect -y 1   # expect 0x36 + 0x40
python3 tools/servo_test_camera.py

# After motors:
python3 tools/motor_test_2wd.py
```

---

## Key files to know about
- `tools/pin_test.py` — GPIO test tool, uses pinctrl
- `config/hardware.yaml` — GPIO assignments (already cleaned up this session)
- `CLAUDE.md` — GPIO map section (updated this session)
- `/boot/firmware/cmdline.txt` — UART console (may be blocking GPIO14)
- `/boot/firmware/config.txt` — I2C at 100kHz (already correct, don't touch)

## System state going into reboot
- cosmo: DELETED from PM2
- banteragent: online (will auto-restart after reboot)
- battery-monitor, pi-monitor, pi-scheduler: online (will auto-restart)
- I2C: 100kHz configured, working
- hardware.yaml: all GPIO conflicts resolved
