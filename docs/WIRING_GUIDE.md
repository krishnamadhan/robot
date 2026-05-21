# WIRING_GUIDE.md — Cosmo Hardware Bring-Up Checklist

> Work top to bottom. Tick each box as you go.
> Do not skip phases — each phase verifies the foundation the next phase depends on.
> Last updated: 2026-05-21

---

## Progress

```
Phase 0  Baseline             [✅ complete]
Phase 1  OLED eyes            [ ] in progress
Phase 2  I2C sensors          [partial — BH1750 live, MPU-6050 pending]
Phase 3  GPIO sensors         [ ] not started
Phase 4  LLC-routed sensors   [ ] not started
Phase 5  Voltage-div sensors  [ ] not started
Phase 6  Motor prep + test    [ ] blocked on XT60 pigtail
Phase 7  LiPo connect         [ ] blocked on Phase 6
```

Update this table as phases complete.

---

## GPIO Conflict Map — Read Before Wiring Anything

Two sensors are currently blocked by motor pin conflicts. Do NOT wire them
until new GPIO assignments are made in hardware.yaml.

| Sensor         | Old GPIO | Conflict          | Status     |
|----------------|----------|-------------------|------------|
| Touch belly    | GPIO25   | right_rear BIN1   | ⚠️ Blocked |
| SW-420 vibration | GPIO26 | right_rear BIN2   | ⚠️ Blocked |

Everything else in this guide is safe to wire in order.

---

## Phase 0 — Baseline Check

Before touching any hardware, confirm what's already live.

- [ ] `pm2 status` — cosmo_demo running, no error state
- [ ] `sudo i2cdetect -y 1` — confirm current I2C map:
  ```
  Expected now:  0x36 (UPS HAT), 0x23 (BH1750)
  ```
- [ ] `pm2 logs cosmo_demo --lines 30 --nostream | grep bh1750` — confirm light sensor reading
- [ ] `vcgencmd measure_temp` — under 65°C before starting wiring session
- [ ] `df -h / | tail -1` — disk under 93% (ALSA logs fill it fast)

---

## Phase 1 — OLED Eyes

**Hardware needed:** 2× SSD1306 0.96" OLED, 4-wire I2C (SDA/SCL/VCC/GND)
**Time estimate:** 30–45 min (including solder on right eye)

### ⚠️ Critical: Right Eye Address Bridge

The right eye board ships with A0 pad = GND → address 0x3C (same as left).
You MUST bridge the A0 pad to VCC before wiring or both eyes will share
0x3C and the second one will be invisible.

**How to bridge A0:**
- Find the A0 solder pad on the back of the right SSD1306 board
- Apply a small solder blob connecting the A0 pad to the adjacent VCC pad
- Some boards label it differently — look for two pads near "ADDR" or "A0"
- After bridging: right eye address becomes 0x3D

### Wiring (both eyes, identical except address)

| SSD1306 Pin | Pi Pin | Pi Signal |
|-------------|--------|-----------|
| VCC         | Pin 1  | 3.3V      |
| GND         | Pin 6  | GND       |
| SDA         | Pin 3  | GPIO2 SDA |
| SCL         | Pin 5  | GPIO3 SCL |

Both eyes wire to the **same 4 pins** — I2C bus is shared, address differentiates them.

### Verification

```bash
sudo i2cdetect -y 1
# Must show BOTH 0x3c AND 0x3d
# If only 0x3c: A0 bridge on right eye is missing or shorted wrong
# If nothing: check VCC/GND, check SDA/SCL not swapped
```

### Software enable

```bash
# In config/hardware.yaml, displays section should already have:
#   left_eye:  { i2c_address: 0x3C, available: true }
#   right_eye: { i2c_address: 0x3D, available: true }
# Verify:
grep -A3 "displays:" ~/robot/config/hardware.yaml
```

```bash
# In expression/eyes.py — change render target:
# Find: eye_engine.set_render_target("terminal")   (or wherever default is set)
# Change to: eye_engine.set_render_target("oled")
grep -n "render_target\|terminal\|oled" ~/robot/expression/eyes.py
```

```bash
pm2 restart cosmo_demo
pm2 logs cosmo_demo -f | grep -E "eye|oled|display"
# Expected: eyes.started, left_eye.init OK, right_eye.init OK
```

- [ ] 0x3C appears in i2cdetect
- [ ] 0x3D appears in i2cdetect
- [ ] Eyes render to OLED (not terminal)
- [ ] Log entry `expression.eyes` or similar confirms both eyes init

---

## Phase 2 — I2C Sensors

### BH1750 Light Sensor (I2C 0x23) — ALREADY LIVE

- [ ] Confirm: `sudo i2cdetect -y 1` shows 0x23
- [ ] Confirm: `pm2 logs cosmo_demo --lines 50 --nostream | grep bh1750`
  ```
  Expected: sensor.bh1750.reading with lux value
  ```

Nothing to wire — already done.

### MPU-6050 Gyro / Accelerometer (I2C 0x68)

**Hardware needed:** MPU-6050 breakout board

| MPU-6050 Pin | Pi Pin | Pi Signal |
|--------------|--------|-----------|
| VCC          | Pin 1  | 3.3V      |
| GND          | Pin 6  | GND       |
| SDA          | Pin 3  | GPIO2 SDA |
| SCL          | Pin 5  | GPIO3 SCL |
| AD0          | GND    | → sets address to 0x68 |

AD0 to GND is important — AD0 floating can give address 0x69 which won't match config.

```bash
sudo i2cdetect -y 1
# Must show 0x68
```

**Software enable:**
```bash
# config/hardware.yaml:
#   mpu6050:
#     available: false   →   available: true
nano ~/robot/config/hardware.yaml   # find mpu6050 section

pm2 restart cosmo_demo
pm2 logs cosmo_demo -f | grep mpu
# Expected: sensor.mpu6050.started, gyro readings in logs
```

- [ ] 0x68 in i2cdetect
- [ ] `available: true` in hardware.yaml
- [ ] Pickup detection firing in logs when physically lifted

---

## Phase 3 — GPIO Sensors (direct 3.3V, no LLC needed)

### PIR Motion Sensor HC-SR501 (GPIO8)

**Note:** HC-SR501 output is open-collector, Pi internal pull-up handles it.
3.3V-safe direct connection.

| HC-SR501 Pin | Pi Pin | Pi Signal  |
|--------------|--------|------------|
| VCC (+)      | Pin 2  | **5V**     |
| GND (-)      | Pin 6  | GND        |
| OUT          | Pin 24 | GPIO8      |

⚠️ HC-SR501 requires 5V supply (Pin 2 or 4). Output signal is 3.3V-safe — direct GPIO connection is fine.

PIR warmup takes ~30 seconds after power-on before reliable detection.

```bash
# Software enable:
# hardware.yaml → pir → available: false → true
pm2 restart cosmo_demo
pm2 logs cosmo_demo -f | grep pir
# Wave hand in front — expect: sensor.pir.motion_detected
```

- [ ] PIR LED blinks during calibration period (~30s)
- [ ] `available: true` set
- [ ] Motion detected in logs on hand wave

---

### TTP223 Capacitive Touch ×3 (GPIO5 / GPIO4 / GPIO7)

**Note:** Belly touch (was GPIO25) is blocked — GPIO25 taken by right_rear motor BIN1.
Wire only 3 pads: head, left, right. Belly to be reassigned later.

| Pad    | GPIO  | Pi Pin | Connection |
|--------|-------|--------|------------|
| Head   | GPIO5 | Pin 29 | TTP223 OUT |
| Left   | GPIO4 | Pin 7  | TTP223 OUT |
| Right  | GPIO7 | Pin 26 | TTP223 OUT |

Each TTP223 board also needs:

| TTP223 Pin | Pi Pin | Pi Signal |
|------------|--------|-----------|
| VCC        | Pin 1  | 3.3V      |
| GND        | Pin 6  | GND       |

TTP223 output is 3.3V — direct GPIO connection, no LLC needed.

```bash
# Software enable:
# hardware.yaml → touch → available: false → true
# Pins should already be [5, 4, 7] in config
pm2 restart cosmo_demo
pm2 logs cosmo_demo -f | grep touch
# Touch each pad — expect: sensor.touch.detected with pin number
```

- [ ] Head touch fires
- [ ] Left touch fires
- [ ] Right touch fires
- [ ] Belly: leave unwired until GPIO25 reassigned

---

## Phase 4 — LLC-Routed Sensors (5V output, needs level shifting)

### Logic Level Converter (LLC) Setup

Your LLC has a HV side (5V) and LV side (3.3V).

| LLC Pin | Connection     |
|---------|----------------|
| HV      | 5V (Pi Pin 2)  |
| LV      | 3.3V (Pi Pin 1)|
| GND     | GND (Pin 6)    |

Wire sensor output → LLC HV channel input.
LLC LV channel output → Pi GPIO.

---

### HC-SR04 Ultrasonic Distance (GPIO16 TRIG, GPIO24 ECHO)

### ⚠️ Critical: ECHO Must Go Through LLC

The HC-SR04 ECHO pin outputs 5V. Connecting it directly to GPIO24 will
damage the Pi 5 GPIO. It must route through the LLC.

TRIG is a Pi output (3.3V) — HC-SR04 accepts it directly, no LLC needed for TRIG.

| HC-SR04 Pin | Connection                        |
|-------------|-----------------------------------|
| VCC         | 5V (Pi Pin 2)                     |
| GND         | GND (Pi Pin 6)                    |
| TRIG        | GPIO16 direct (Pin 36)            |
| ECHO        | → LLC HV ch1 in → LLC LV ch1 out → GPIO24 (Pin 18) |

```bash
sudo i2cdetect -y 1   # no change expected — ultrasonic is GPIO, not I2C

# Quick hardware test before enabling in software:
python3 -c "
import gpiozero, time
trig = gpiozero.OutputDevice(16)
echo = gpiozero.InputDevice(24)
trig.on(); time.sleep(0.00001); trig.off()
t = time.monotonic()
while not echo.is_active and time.monotonic() - t < 0.03: pass
t1 = time.monotonic()
while echo.is_active and time.monotonic() - t1 < 0.03: pass
dist_cm = (time.monotonic() - t1) * 17150
print(f'Distance: {dist_cm:.1f} cm')
"
# Should print a sensible distance (5–200cm range)
```

```bash
# Software enable:
# hardware.yaml → ultrasonic → mock: true → mock: false (or similar flag)
pm2 restart cosmo_demo
pm2 logs cosmo_demo -f | grep ultrasonic
```

- [ ] ECHO wired through LLC (not direct)
- [ ] Python one-liner gives sane distance reading
- [ ] `available` / `mock` flag updated in hardware.yaml

---

### TCRT5000 Cliff Sensors ×2 (GPIO14 LEFT, GPIO15 RIGHT)

TCRT5000 digital output is 5V — must go through LLC.

| TCRT5000 Pin | Connection                            |
|--------------|---------------------------------------|
| VCC          | 5V (Pi Pin 2)                         |
| GND          | GND (Pi Pin 6)                        |
| DO (left)    | → LLC HV ch2 in → LLC LV ch2 out → GPIO14 (Pin 8) |
| DO (right)   | → LLC HV ch3 in → LLC LV ch3 out → GPIO15 (Pin 22)|

```bash
# Quick test — sensors should read LOW over surface, HIGH over edge/gap:
python3 -c "
from gpiozero import InputDevice
import time
left = InputDevice(14, pull_up=True)
right = InputDevice(15, pull_up=True)
for _ in range(10):
    print(f'left={left.value} right={right.value}')
    time.sleep(0.3)
"
# Hold over table → both 0. Hold over edge → affected side goes 1.
```

```bash
# Software enable:
# hardware.yaml → cliff → available: false → true
pm2 restart cosmo_demo
pm2 logs cosmo_demo -f | grep cliff
```

- [ ] Left sensor reads correctly (0=surface, 1=edge)
- [ ] Right sensor reads correctly
- [ ] `available: true` in hardware.yaml
- [ ] Cliff event fires in logs when sensor lifted off surface

---

## Phase 5 — Voltage-Divider Sensors

### KY-038 Sound Sensor (GPIO11)

KY-038 digital output is 5V. Two 10kΩ resistors as a divider bring it to ~2.5V.

```
KY-038 DO → R1 (10kΩ) → GPIO11 (Pin 23)
                       ↓
                    R2 (10kΩ)
                       ↓
                      GND
```

| KY-038 Pin | Connection     |
|------------|----------------|
| VCC (+)    | 5V (Pin 2)     |
| GND (-)    | GND (Pin 6)    |
| DO         | → 10kΩ → GPIO11 (junction) → 10kΩ → GND |
| AO         | Not used       |

Adjust the onboard sensitivity trimpot so the DO LED flickers on a sharp clap.

```bash
python3 -c "
from gpiozero import InputDevice
import time
mic = InputDevice(11, pull_up=False)
print('Clap near sensor...')
for _ in range(30):
    print('SOUND' if mic.is_active else '....', end=' ', flush=True)
    time.sleep(0.1)
"
```

```bash
# Software enable:
# hardware.yaml → sound → available: false → true
pm2 restart cosmo_demo
pm2 logs cosmo_demo -f | grep sound
```

- [ ] Voltage divider soldered (2× 10kΩ)
- [ ] Python test detects clap
- [ ] `available: true` in hardware.yaml

### SW-420 Vibration (BLOCKED)

GPIO26 is taken by right_rear motor BIN2. Do not wire until reassigned.

- [ ] Blocked — note in hardware.yaml: `pin: null  # GPIO26 taken by motors`

---

### Phase 5 Complete — Full Sensor Verification

When all above phases are done:

```bash
python3 tools/sensor_monitor.py
# Rich dashboard should show live readings for:
# BH1750 lux, MPU-6050 accel/gyro, PIR state,
# Touch ×3, HC-SR04 distance, TCRT5000 cliff ×2, KY-038 sound
```

**Do not proceed to Phase 6 until sensor_monitor.py shows all sensors live.**

---

## Phase 6 — Motor Prep and Dry-Run (no LiPo yet)

### ⚠️ Read Before Starting

- 470µF capacitor must be soldered across TB6612FNG VM+GND before ANY LiPo connection
- 220µF capacitors must be soldered across each motor terminal pair (×4 motors = ×4 caps)
- The XT60 pigtail must NOT be connected until STBY software control is confirmed
- All motor pin assignments use GPIO17/22/12, GPIO23/6/13, GPIO20/21/18, GPIO25/26/19 + STBY GPIO27

### Step 1 — Confirm caps are installed

- [ ] 470µF across TB6612FNG VM (+) and GND (correct polarity — stripe = negative)
- [ ] 220µF across left_front motor terminal pair
- [ ] 220µF across left_rear motor terminal pair
- [ ] 220µF across right_front motor terminal pair
- [ ] 220µF across right_rear motor terminal pair

### Step 2 — Confirm STBY LOW at boot (software)

```bash
python3 -c "
from gpiozero import OutputDevice
import time
stby = OutputDevice(27)
stby.off()
print('STBY LOW — motor driver disabled')
time.sleep(2)
stby.on()
print('STBY HIGH — motor driver enabled')
time.sleep(1)
stby.off()
print('STBY LOW — disabled again')
"
# Must NOT hear any motor movement (LiPo not connected yet)
# This confirms GPIO27 is wired correctly to STBY pin
```

- [ ] STBY GPIO wired to TB6612FNG STBY pin
- [ ] Script runs without error
- [ ] Software-confirmed STBY control working

### Step 3 — Mock-mode motor test

```bash
# Confirm motors.py is still in mock mode (no real movement):
python3 -c "
from hardware.motors import motor_controller
import asyncio
async def test():
    await motor_controller.initialize()
    print('Mode:', 'mock' if motor_controller._mock else 'REAL — check config!')
asyncio.run(test())
"
```

- [ ] Mock mode confirmed before LiPo connect

---

## Phase 7 — LiPo Connect and First Motor Run

**Only start this phase when:**
- All Phase 6 steps complete ✅
- Capacitors physically installed ✅
- STBY control confirmed in software ✅
- XT60 pigtail has arrived ✅

### ⚠️ Power isolation rules — do not violate

- LiPo 7.4V → TB6612FNG VM terminal ONLY
- Pi 3.3V → TB6612FNG VCC (logic) — NOT 5V
- Pi GND + LiPo GND → shared GND at TB6612FNG — intentional, required
- NEVER connect LiPo positive to Pi 5V rail — destroys Pi (₹7,500+)

### Step 1 — Connect XT60 pigtail (NO LiPo yet)

- [ ] XT60 female pigtail soldered to TB6612FNG VM+ and VM GND
- [ ] Polarity double-checked: red wire → VM+, black wire → VM GND
- [ ] Inspect solder joints — no bridges, no cold joints

### Step 2 — First LiPo connect

- [ ] LiPo battery voltage checked with multimeter: should read 7.0–8.4V
- [ ] PM2 cosmo_demo confirmed running (STBY will be held LOW by software at boot)
- [ ] **Connect LiPo XT60** — watch for sparks (small spark on first connect is normal due to cap charging; a big arc is a wiring error — disconnect immediately)
- [ ] 30 seconds after connect: measure VM terminal with multimeter — should read battery voltage

### Step 3 — First motor test at 30% speed

```bash
# Enable real motor mode in hardware.yaml first:
# motors → mock: false (or similar flag)
# Then test at 30% — low speed, easy to stop

python3 -c "
from hardware.motors import motor_controller
import asyncio, time
async def test():
    await motor_controller.initialize()
    print('Forward 30%...')
    await motor_controller.set_speed(0.30, 0.30)
    await asyncio.sleep(2)
    await motor_controller.stop()
    print('Stop.')
    await asyncio.sleep(1)
    print('Backward 30%...')
    await motor_controller.set_speed(-0.30, -0.30)
    await asyncio.sleep(2)
    await motor_controller.stop()
    print('Done.')
asyncio.run(test())
"
```

- [ ] Both sides move forward on command
- [ ] Both sides move backward on command
- [ ] No TB6612FNG heat (check chip temp with finger after 30s run)
- [ ] Log result in `docs/PERFORMANCE_LOG.md` — first motor run entry

---

## Known Blocked Items (do not wire until resolved)

| Item | Blocker | Resolution |
|------|---------|------------|
| Touch belly | GPIO25 taken by right_rear BIN1 | Reassign BIN1 to free GPIO, update hardware.yaml |
| SW-420 vibration | GPIO26 taken by right_rear BIN2 | Reassign BIN2 to free GPIO, update hardware.yaml |
| APDS-9960 gesture | Faulty unit — replacement on order | Set `available: true` only after confirmed at 0x39 |
| PCA9685 servo driver | Hardware on order | Phase 3 — enable after arrival |
| INMP441 mic | GPIO18-21 used by motors | Re-evaluate after motor wiring finalized |
