# docs/HARDWARE_PLAN.md — Cosmo Physical Wiring Guide

> Phase 4 (doc-only). Code migration is complete (Phase 3 ✅).  
> This document is the step-by-step physical bring-up guide for wiring all peripherals.  
> Work top-to-bottom; do NOT skip steps — each gate test confirms the foundation for the next.  
> Last updated: 2026-06-12

---

## Current Wiring State

| Peripheral | Code | Physical |
|------------|------|----------|
| BH1750 light | ✅ | ✅ Wired & live |
| UPS HAT (MAX17043) | ✅ | ✅ Wired & live |
| OLED left eye (0x3C) | ✅ | ⚠️ Not wired |
| OLED right eye (0x3D) | ✅ | ⚠️ Not wired (A0 bridge needed) |
| ESP32-S3 serial link | ✅ | ✅ /dev/ttyUSB0 connected |
| TB6612FNG motors | ✅ | ⚠️ Still on Pi GPIO — rewire to ESP32 |
| PIR HC-SR501 | ✅ | ⚠️ Not wired |
| Touch ×4 TTP223 | ✅ | ⚠️ Not wired |
| MPU-6050 IMU | ✅ | ⚠️ Not wired |
| Cliff ×2 TCRT5000 | ✅ | ⚠️ Parcel pending |
| KY-038 sound | ✅ | ⚠️ Parcel pending |
| SW-420 vibration | ✅ | ⚠️ Not wired |
| HC-SR04 ultrasonic | ✅ | ⚠️ Blocked — XT60 pigtail not arrived |
| APDS-9960 gesture | ❌ | ⚠️ Replacement ordered |
| PCA9685 servo | ❌ | ⚠️ On order |

---

## Pre-Flight — Do This Before Any Wiring Session

Two `/boot/firmware/config.txt` overlays conflict with sensors. Fix once, reboot once.

### Fix 1 — KI-021: I2S overlay conflicts with motor direction pins

Motor direction pins AIN1/AIN2 (ESP32 GPIO15/16) are fine; but the Pi's `inmp441-pi5`
overlay claims Pi GPIO18–21. GPIO20/21 are currently the right-motor direction pins in
Pi `hardware.yaml`. Leaving the overlay active risks kernel/gpiozero ownership fights
on those pins when motors are exercised.

```bash
sudo nano /boot/firmware/config.txt
# Comment out:
#   dtoverlay=inmp441-pi5
#   dtparam=i2s=on
```

### Fix 2 — KI-024: software I2C-GPIO overlay double-claims GPIO8 (ESP32 I2C SDA)

The ESP32 uses GPIO8 for I2C SDA (MPU-6050). The Pi has an old `i2c-gpio` overlay
that also claims GPIO8. This has no effect until the IMU is wired; fix it now so it
does not silently corrupt IMU reads after Step 6.

```bash
sudo nano /boot/firmware/config.txt
# Comment out:
#   dtoverlay=i2c-gpio,bus=4,i2c_gpio_sda=8,i2c_gpio_scl=9
```

```bash
sudo reboot
# After reboot, confirm Pi I2C bus 1 still works:
sudo i2cdetect -y 1
# Expected: 0x10 (UPS power), 0x23 (BH1750), 0x36 (UPS fuel gauge)
```

---

## GPIO6 / GPIO16 — FIT0992 HAT Reserved Pins (never touch)

> These two Pi GPIO pins are owned by the UPS HAT hardware and cannot be reclaimed.

| Pi GPIO | HAT function | Consequence of driving it |
|---------|-------------|--------------------------|
| GPIO6   | FIT0992 adapter-fail detect | HAT drives this pin; driving it in software → **burned 5 chips** |
| GPIO16  | FIT0992 charging-disable | HAT drives this pin; any external drive corrupts charging state |

These pins do not appear in any robot code. **Never add them.** `hardware/pin_registry.py`
lists both as `RESERVED_HAT`. KI-005 documents the old `battery_monitor.py` conflict
where GPIO6 was mistakenly assigned to a motor direction pin — that is fixed and committed.

---

## MAX17040 / MAX17043 Fuel Gauge — Known Fragility

The UPS HAT ships with either a MAX17040 or MAX17043 (pin-compatible). The 0x36 address
is shared by both. Known failure modes observed in the field:

- **Voltage glitch on motor start:** when TB6612FNG energises (inrush ~2A), LiPo voltage
  dips briefly. If the dip hits the critical_voltage threshold (6.8V), the battery monitor
  fires BATTERY_LOW and may trigger a shutdown before the robot has moved. Mitigation:
  lower `critical_voltage` to 6.5V in `hardware.yaml` only after measuring the actual
  dip with a multimeter during the first LiPo motor test.

- **MAX17040 SOC reading freezes at 100%:** seen on some units — if `python3 tools/battery_monitor.py`
  shows SOC=100% regardless of charge state, the fuel gauge IC needs a software reset:
  ```bash
  python3 -c "
  import smbus2, time
  bus = smbus2.SMBus(1)
  bus.write_word_data(0x36, 0xFE, 0x5400)  # POR command
  time.sleep(0.5)
  print(bus.read_word_data(0x36, 0x04))    # SOC register
  "
  ```
  If it still reads 0x6400 (100%) after POR, the IC is faulty — replace the UPS HAT.

- **I2C bus lockup on hot-unplug:** if the LiPo is disconnected while the fuel gauge is
  mid-transaction, the I2C bus can freeze. Symptom: `i2cdetect -y 1` hangs. Fix:
  ```bash
  sudo i2cdetect -y 1  # if it hangs, Ctrl-C
  sudo modprobe -r i2c_bcm2835 && sudo modprobe i2c_bcm2835
  ```
  Never hot-unplug the LiPo while `cosmo_demo` is running.

---

## Step 1 — OLED Eyes (Pi I2C bus)

**Parts:** 2× SSD1306 0.96" OLED, 4-wire I2C

### ⚠️ Right eye: solder A0 pad first

Both OLEDs ship with I2C address 0x3C. Bridge the A0 pad on the **right** OLED to VCC
to set it to 0x3D. Without this, both eyes share 0x3C and the second one is invisible.

```
Right eye PCB back → find "A0" or "ADDR" pads → solder blob connecting A0 to VCC pad
```

### Wiring (same for both eyes — I2C bus is shared)

| OLED pin | Pi header pin | Signal |
|----------|---------------|--------|
| VCC | Pin 1 | 3.3V |
| GND | Pin 6 | GND |
| SDA | Pin 3 | GPIO2 I2C1 SDA |
| SCL | Pin 5 | GPIO3 I2C1 SCL |

### Test

```bash
sudo i2cdetect -y 1
# Must show BOTH 0x3c AND 0x3d
# If only 0x3c → A0 bridge missing or shorted wrong
# If nothing → check VCC/GND and SDA/SCL not swapped
```

### Capability flip

```bash
# expression/eyes.py — find the render target setting and change to "oled"
grep -n "render_target\|terminal\|oled" /home/pi/robot/expression/eyes.py

# After changing:
pm2 restart cosmo_demo
pm2 logs cosmo_demo --lines 30 --nostream | grep -E "eye|oled|init"
# Expected: left_eye.init OK, right_eye.init OK
```

- [ ] 0x3C in i2cdetect
- [ ] 0x3D in i2cdetect
- [ ] Eyes render to OLED (not terminal)

---

## Step 2 — ESP32 Serial Link Baseline

> The ESP32 should already be connected via `/dev/ttyUSB0` (UART bridge). Verify before
> any sensor/motor work so bridge issues are caught early.

```bash
ls -la /dev/ttyUSB0   # must exist
python3 tools/esp32_test.py
# Rich dashboard should show: bridge online, heartbeat counter incrementing
# All sensor rows = "disabled" (expected — nothing wired yet)
```

If `esp32_test.py` shows bridge offline or no heartbeat:
```bash
# Re-flash ESP32 firmware:
cd /home/pi/robot/esp32
# connect ESP32 USB-C while holding BOOT button, then:
# (use mpremote or ampy to upload main.py and driver_tb6612.py)
mpremote connect /dev/ttyUSB0 cp main.py :main.py + cp driver_tb6612.py :driver_tb6612.py
mpremote connect /dev/ttyUSB0 reset
```

- [ ] `/dev/ttyUSB0` present
- [ ] `esp32_test.py` shows heartbeat live

---

## Step 3 — Motor Rewire (Pi GPIO → ESP32 GPIO 15–21)

> **BLOCKED until XT60 pigtail arrives (Robocraze order, KI-003).**  
> Use 4× AA battery holder (6V, ~1A) for initial motor testing before LiPo.

### Pre-motor safety checklist

- [ ] KI-021 config.txt fix applied and rebooted (Step 0)
- [ ] 470µF capacitor across TB6612FNG VM+ and GND (stripe = negative)
- [ ] 220µF capacitor across each motor terminal pair (× 2 motors on single board)
- [ ] Multimeter: confirm VM+ reads battery voltage, VCC reads 3.3V from ESP32 rail

### Disconnect from Pi GPIO first

Disconnect the existing Pi GPIO wires from the TB6612FNG:
AIN1 (Pi GPIO17), AIN2 (Pi GPIO22), PWMA (Pi GPIO11), BIN1 (Pi GPIO20), BIN2 (Pi GPIO24), PWMB (Pi GPIO18), STBY (Pi GPIO27).

**Leave the Pi end of each wire unconnected — do not tie to GND.**

### Wire to ESP32

| TB6612FNG pin | ESP32 GPIO | Role |
|---------------|------------|------|
| AIN1 | GPIO15 | Left motor dir-A |
| AIN2 | GPIO16 | Left motor dir-B |
| PWMA | GPIO17 | Left motor speed (PWM) |
| BIN1 | GPIO18 | Right motor dir-A |
| BIN2 | GPIO19 | Right motor dir-B |
| PWMB | GPIO20 | Right motor speed (PWM) |
| STBY | GPIO21 | Standby (HIGH = enabled) |
| VCC  | ESP32 3.3V | Logic supply |
| GND  | ESP32 GND | Shared ground |

> **Motor truth table — violating destroys TB6612FNG:**  
> AIN1=1 AIN2=0 → Forward | AIN1=0 AIN2=1 → Backward | AIN1=0 AIN2=0 → Brake  
> **AIN1=1 AIN2=1 → ❌ PROHIBITED — destroys driver**  
> STBY LOW at boot; esp32 main.py holds STBY LOW until `stby:1` command received.

### Capability flip

```python
# esp32/main.py — line ~47
SENSORS = {
    ...
    "motors": True,   # ← change this
}
```

```bash
# Deploy to ESP32:
mpremote connect /dev/ttyUSB0 cp esp32/main.py :main.py + reset
python3 tools/esp32_test.py
# Move command → motors should spin at 30% (place robot on blocks first)
```

- [ ] Motors wired to ESP32 GPIO 15–21
- [ ] `SENSORS["motors"] = True` deployed
- [ ] Forward/backward at 30% confirmed
- [ ] No TB6612FNG heat after 30s run
- [ ] STBY LOW at boot confirmed (motors don't spin on power-up)

---

## Step 4 — PIR Motion Sensor (ESP32 GPIO12)

> PIR is the safest sensor to wire first — 3.3V direct, no level shifting needed,
> no I2C bus interaction.

| HC-SR501 pin | ESP32 pin | Note |
|--------------|-----------|------|
| VCC (+) | 5V (from Pi USB via header) | HC-SR501 needs 5V supply |
| GND (-) | ESP32 GND | |
| OUT | GPIO12 | Direct 3.3V output — no LLC needed |

> HC-SR501 has a jumper to select 3.3V or 5V output. Set to **3.3V** before wiring to GPIO12.
> PIR needs ~30s warm-up after power-on before reliable detection.

### Capability flip

```python
# esp32/main.py
SENSORS["pir"] = True
```

```bash
mpremote connect /dev/ttyUSB0 cp esp32/main.py :main.py + reset
python3 tools/esp32_test.py
# Wave hand in front at ~1m — PIR row should flip 0→1
```

- [ ] PIR wired to ESP32 GPIO12
- [ ] Output jumper set to 3.3V
- [ ] `SENSORS["pir"] = True` deployed
- [ ] Motion detected in esp32_test.py on hand wave

---

## Step 5 — Touch Sensors ×4 (ESP32 GPIO1–4)

| Sensor | ESP32 GPIO | Body location |
|--------|-----------|---------------|
| Touch 0 | GPIO1 | Head |
| Touch 1 | GPIO2 | Back |
| Touch 2 | GPIO3 | Belly |
| Touch 3 | GPIO4 | Tail |

Each TTP223 board:

| TTP223 pin | Connection |
|------------|------------|
| VCC | 3.3V |
| GND | GND |
| I/O | ESP32 GPIO1/2/3/4 |

TTP223 output is 3.3V — direct GPIO, no LLC.

### Capability flip

```python
SENSORS["touch"] = True
```

```bash
mpremote connect /dev/ttyUSB0 cp esp32/main.py :main.py + reset
python3 tools/esp32_test.py
# Touch each pad — row should show touch n=0/1/2/3
```

- [ ] All 4 touch pads wired
- [ ] Head, back, belly, tail all fire in esp32_test.py

---

## Step 6 — IMU + Cliff Sensors (ESP32 I2C + GPIO13/14)

> ⚠️ Requires KI-024 pre-flight fix (Step 0) — GPIO8/9 must be free of the i2c-gpio overlay.

### MPU-6050 IMU (ESP32 I2C GPIO8/9)

| MPU-6050 pin | ESP32 pin | Note |
|-------------|-----------|------|
| VCC | 3.3V | |
| GND | GND | |
| SDA | GPIO8 | ESP32 I2C |
| SCL | GPIO9 | ESP32 I2C |
| AD0 | GND | Sets I2C address to 0x68 |

> MPU-6050 breakout usually has built-in I2C pullups. If both MPU-6050 and the HC-SR04
> are on the same ESP32 I2C bus, their pullups combine — acceptable at 100 kHz.

### Cliff sensors ×2 (ESP32 GPIO13/14) — parcel pending

> **Blocked until Robocraze cliff parcel arrives.**

TCRT5000 digital output is open-collector with onboard pullup to VCC.
The ESP32 GPIO input must be held HIGH via internal or external pullup.
Enable internal pullup in firmware (`Pin(13, Pin.IN, Pin.PULL_UP)`).

| TCRT5000 pin | ESP32 GPIO | Side |
|-------------|-----------|------|
| DO | GPIO13 | Left cliff |
| DO | GPIO14 | Right cliff |
| VCC | 3.3V | |
| GND | GND | |

> The cliff IRQ reflex (3.1) is already coded — it brakes motors within one firmware tick,
> no Pi round-trip. After wiring, cliff events will also propagate to Pi as `CLIFF_DETECTED`.

### Capability flip

```python
SENSORS["imu"]   = True   # after MPU wired
SENSORS["cliff"] = True   # after cliff sensors arrive and wired
```

```bash
mpremote connect /dev/ttyUSB0 cp esp32/main.py :main.py + reset
python3 tools/esp32_test.py
# IMU row: live ax/ay/az readings; tilt robot → values change
# Cliff row: 0 on surface, 1 when lifted off table
```

- [ ] MPU-6050 wired to ESP32 I2C
- [ ] IMU readings live in esp32_test.py
- [ ] Cliff sensors wired (when parcel arrives)
- [ ] Cliff reflex confirmed: hold robot over edge → motors brake before Pi receives event

---

## Step 7 — Sound + Vibration (ESP32 GPIO5/6) — parcel pending

> **Blocked until sound sensor parcel arrives.**

### KY-038 Sound Sensor (ESP32 GPIO5 — ADC1)

GPIO5 = ESP32 ADC1 channel. The firmware reads the raw ADC value; the Pi-side bridge
interprets amplitude > threshold as `SOUND_DETECTED`.

| KY-038 pin | ESP32 pin |
|------------|-----------|
| VCC | 3.3V |
| GND | GND |
| AO | GPIO5 (ADC1) |
| DO | Not connected |

> Use the AO (analog) output, not DO. Adjust the onboard trimpot so the ambient AO value
> is ~200–400 ADC counts and a sharp clap spikes to >700.

### SW-420 Vibration (ESP32 GPIO6)

| SW-420 pin | ESP32 pin |
|------------|-----------|
| VCC | 3.3V |
| GND | GND |
| DO | GPIO6 |

### Capability flip

```python
SENSORS["sound"] = True
SENSORS["vibe"]  = True
```

```bash
mpremote connect /dev/ttyUSB0 cp esp32/main.py :main.py + reset
python3 tools/esp32_test.py
# Sound row: clap near sensor → spike visible
# Vibe row: tap robot body → 1 fires briefly
```

- [ ] KY-038 wired to ESP32 GPIO5
- [ ] Trimpot calibrated (clap → ADC spike > 700)
- [ ] SW-420 wired to ESP32 GPIO6
- [ ] Both sensors fire in esp32_test.py

---

## Step 8 — HC-SR04 Ultrasonic (ESP32 GPIO10/11) — blocked on XT60 pigtail

> **Blocked until XT60 female pigtail arrives (same order as KI-003).**

HC-SR04 ECHO is 5V. It must go through a resistor divider before ESP32 GPIO11.
The firmware header already shows the expected values: 2kΩ + 1kΩ.

```
HC-SR04 ECHO (5V) → 2kΩ → GPIO11 junction → 1kΩ → GND
                              ↑
                          3.3V at GPIO11  (5V × 1/(2+1) = 1.67V ... use values from header)
```

Actually the correct divider for 5V→3.3V: use 2kΩ (top) + 3.3kΩ (bottom):
`Vout = 5V × 3.3/(2+3.3) ≈ 3.1V`. Use whatever resistors bring ECHO below 3.3V.
The firmware comment says 2kΩ/1kΩ → Vout = 5×(1/3) ≈ 1.67V — acceptable (logic HIGH threshold is ~1.5V on ESP32).

| HC-SR04 pin | Connection |
|-------------|------------|
| VCC | 5V |
| GND | GND |
| TRIG | GPIO10 (direct 3.3V) |
| ECHO | → 2kΩ → GPIO11 → 1kΩ → GND |

```python
SENSORS["ultra"] = True
```

---

## Maintenance Checklist

Run before any wiring session and after any hardware change:

```bash
# System health
vcgencmd measure_temp          # < 65°C
free -h                        # > 1GB free
df -h / | tail -1              # < 90% (ALSA logs fill fast)
pm2 status                     # all green; banteragent must stay online

# I2C integrity
sudo i2cdetect -y 1            # expect 0x10, 0x23, 0x36 (+ wired OLED/servo when added)

# Bridge health
python3 tools/esp32_test.py    # heartbeat live; only enabled sensors show readings
```

**Weekly:**
- Check `pm2 logs cosmo_demo --lines 100` for recurring ERROR entries
- Check `df -h` — ALSA/journal logs can fill `/` silently
- `sudo journalctl --vacuum-size=200M` if disk > 85%

---

## SD Card Risk

> The Pi 5 runs from an SD card. Every SQLite write, log rotation, and PM2 restart
> increments write cycles. SD cards have finite write endurance (~3,000–10,000 cycles
> per cell). On a busy robot (constant SQLite episodic writes, ALSA logs, PM2 logs),
> an SD card can fail within months.

**Mitigations already in place:**
- SQLite `episodic.db` uses WAL mode (reduces fsync frequency)
- `pm2-logrotate` caps log size

**Additional mitigations to apply:**
```bash
# Move SQLite and logs to tmpfs (RAM) for session data:
# Add to /etc/fstab:
#   tmpfs  /run/cosmo  tmpfs  defaults,noatime,size=64M  0 0
# Then point episodic.db to /run/cosmo/episodic.db
# Trade-off: memory is lost on reboot — acceptable for session cache, not for
# permanent episodic memories. Permanent memories should write-through to SD.

# Reduce log verbosity in production:
# In cosmo_demo.py / structlog config: set level=WARNING not DEBUG after dev sessions
```

**Warning signs of SD failure:**
- `dmesg | grep -i "mmc\|ext4\|error"` shows I/O errors
- Files that should exist are suddenly missing or zero-length
- PM2 processes restart unexpectedly with no apparent reason

**Recovery plan:**
```bash
# If SD card fails:
# 1. Pi Imager → flash fresh SD with Pi OS Bookworm 64-bit
# 2. git clone https://github.com/<repo>/cosmo ~/robot
# 3. ~/robot/tools/setup.sh  (dependencies + PM2 config)
# 4. Restore secrets from backup: ~/secrets/*.env
# 5. Restore robot memory: ~/.robot/  (faces, episodic.db, spatial.json)
```

> Keep a monthly backup of `~/.robot/` and `~/secrets/` to a USB drive or cloud.
> `git push` covers all code; the memory files are not in git (personal data).

---

## Blocked Items Summary

| Item | Blocker | Action when unblocked |
|------|---------|----------------------|
| Motors real-mode | XT60 pigtail not arrived | Step 3, then set `SENSORS["motors"]=True` |
| Cliff sensors | Parcel from Robocraze | Step 6 cliff section |
| KY-038 sound | Parcel from Robocraze | Step 7 |
| HC-SR04 | Same XT60 pigtail order | Step 8 |
| APDS-9960 | Faulty — replacement ordered | Keep `available: false` until confirmed at 0x39 |
| PCA9685 servo | On order | Phase 5 — not in this guide |

---

## After All Steps Complete — Full Verification

```bash
python3 tools/esp32_test.py
# All sensor rows green, no "disabled" rows except APDS/servo (on order)

pm2 logs cosmo_demo --lines 50 --nostream | grep -E "ERROR|WARNING"
# Should be quiet

python3 -m pytest tests/unit/ tests/brain/ tests/hardware/ -q
# 260 passed (or more), 0 failed
```
