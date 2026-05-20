# Cosmo — Complete Hardware Wiring Guide

> Version: 2.0 (4WD + Pan/Tilt Servo Mount)  
> Last chip meltdown: TB6612FNG burned due to missing decoupling capacitors on VM.  
> This guide includes all protection circuits. Never skip the capacitor steps.

---

## Why the Last Chip Burned

The TB6612FNG burned because:
1. **No decoupling capacitors** on the VM (motor voltage) pin
2. Motor back-EMF spikes travel back through VM → exceed chip's absolute max rating (13.5V)
3. Instantaneous voltage spike destroys the internal MOSFETs

**This guide prevents that from happening again.**

---

## Power Architecture (Critical — Read First)

```
                    ┌─────────────────────────────────────┐
  7.4V LiPo or  ──▶ │  3A Resettable Polyfuse (x2)       │
  6xAA battery      │  One per TB6612FNG motor driver     │
                    └──────┬──────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
     4700µF cap                   4700µF cap
     + 100nF cap                  + 100nF cap
     to GND                       to GND
              │                         │
         TB6612FNG #1             TB6612FNG #2
         (Left motors)            (Right motors)
```

```
  7.4V LiPo ──▶ Buck Converter (7.4V → 5V 3A) ──▶ Pi 5 power input (USB-C)
                                               ──▶ PCA9685 VCC
                                               ──▶ Sensor logic (3.3V via Pi GPIO)
```

**NEVER power the Pi directly from the motor battery without a buck converter.**  
Motor switching noise will crash the Pi or corrupt SD card.

---

## Component List (cross-reference with SHOPPING_LIST.md)

| Ref | Component | Qty | Notes |
|-----|-----------|-----|-------|
| U1, U2 | TB6612FNG motor driver | 2 | One per side (left/right) |
| C1-C4 | 4700µF 16V electrolytic capacitor | 4 | 2 per motor driver (VM to GND) |
| C5-C12 | 100nF ceramic capacitor | 8 | 2 per motor driver + 4 for logic |
| F1, F2 | 3A polyfuse (resettable) | 2 | One per motor driver VM line |
| U3 | PCA9685 16-channel PWM board | 1 | Motor PWM + servos |
| M1-M4 | TT gear motor with encoder (if possible) | 4 | 4WD |
| SV1, SV2 | MG90S servo | 2 | Pan + tilt for camera |
| LLC1, LLC2 | 4-channel Logic Level Converter | 2 | 5V sensor → 3.3V Pi |
| R1 | Cliff sensor (TCRT5000 or IR module) | 2 | Left + right |
| PIR1 | HC-SR501 PIR motion sensor | 1 | GPIO8 |
| US1 | HC-SR04 ultrasonic | 1 | Already wired |
| IMU1 | MPU6050 | 1 | I2C 0x68 |
| DISP1,2 | SSD1306 OLED 128×64 | 2 | Eyes (0x3C + 0x3D) |
| T1-T3 | TTP223 capacitive touch sensor | 3 | Head + left cheek + right cheek |
| BAT1 | MAX17043 UPS HAT | 1 | Already installed |
| BLT1 | BH1750 light sensor | 1 | Already wired |

---

## GPIO Pin Map (Full 4WD Configuration)

```
Pi GPIO  │ Function                  │ Connected to
─────────┼───────────────────────────┼─────────────────────────────
GPIO2    │ I2C SDA                   │ All I2C devices (BH1750, MPU6050, PCA9685, SSD1306×2, MAX17043)
GPIO3    │ I2C SCL                   │ All I2C devices
GPIO4    │ Touch left cheek          │ TTP223 sensor OUT → direct (3.3V)
GPIO5    │ Touch head                │ TTP223 sensor OUT → direct (3.3V)
GPIO6    │ Motor right-front AIN1    │ TB6612FNG #2 AIN1
GPIO7    │ Touch right cheek         │ TTP223 sensor OUT → direct (3.3V)
GPIO8    │ PIR motion                │ HC-SR501 OUT → direct (3.3V model) or LLC
GPIO9    │ APDS9960 INT (optional)   │ APDS9960 INT pin (future)
GPIO10   │ Motor right-front AIN2    │ TB6612FNG #2 AIN2
GPIO11   │ Sound level               │ KY-038 D0 → direct
GPIO14   │ Cliff left                │ TCRT5000 OUT → LLC → GPIO14
GPIO15   │ Cliff right               │ TCRT5000 OUT → LLC → GPIO15
GPIO16   │ HC-SR04 TRIG              │ HC-SR04 TRIG → direct (3.3V output, OK for 5V device)
GPIO17   │ Motor left-front AIN1     │ TB6612FNG #1 AIN1
GPIO19   │ Motor right-rear BIN1     │ TB6612FNG #2 BIN1
GPIO20   │ Motor right-rear BIN2     │ TB6612FNG #2 BIN2
GPIO22   │ Motor left-front AIN2     │ TB6612FNG #1 AIN2
GPIO23   │ Motor left-rear BIN1      │ TB6612FNG #1 BIN1
GPIO24   │ HC-SR04 ECHO              │ HC-SR04 ECHO → LLC → GPIO24
GPIO25   │ Touch belly               │ TTP223 sensor OUT → direct (3.3V)
GPIO26   │ Motor left-rear BIN2      │ TB6612FNG #1 BIN2
GPIO27   │ Motor STBY (both chips)   │ TB6612FNG #1 STBY + TB6612FNG #2 STBY (shared)

I2C Bus  │ Address │ Device
─────────┼─────────┼──────────────────
SDA/SCL  │ 0x23    │ BH1750 light sensor
SDA/SCL  │ 0x36    │ MAX17043 battery (UPS HAT)
SDA/SCL  │ 0x39    │ APDS9960 (future)
SDA/SCL  │ 0x40    │ PCA9685 PWM (motor speeds + servos)
SDA/SCL  │ 0x3C    │ SSD1306 OLED left eye
SDA/SCL  │ 0x3D    │ SSD1306 OLED right eye (A0 pad bridged)
SDA/SCL  │ 0x68    │ MPU6050 IMU

PCA9685  │ Channel │ Connected to
─────────┼─────────┼──────────────────
I2C 0x40 │ CH0     │ TB6612FNG #1 PWMA (left-front speed)
         │ CH1     │ TB6612FNG #1 PWMB (left-rear speed)
         │ CH2     │ TB6612FNG #2 PWMA (right-front speed)
         │ CH3     │ TB6612FNG #2 PWMB (right-rear speed)
         │ CH4     │ Pan servo signal
         │ CH5     │ Tilt servo signal
         │ CH6-15  │ Reserved
```

---

## Step-by-Step Wiring Process

### PHASE 0 — Preparation (30 minutes)
**Do this BEFORE touching any wires.**

1. **Power off everything.** Pi unplugged, battery disconnected.
2. **Gather components**: soldering iron, multimeter, breadboard (for testing), flux, solder.
3. **Test all ICs before wiring**:
   - For each TB6612FNG: measure VM→GND resistance > 10Ω (if <1Ω, chip is shorted)
   - For each SSD1306: check I2C address with `i2cdetect -y 1` when Pi is on
4. **Label all wires** before starting. Use coloured wire:
   - Red = power (VM motor rail)
   - Orange = 5V logic
   - Black = GND
   - Blue = I2C
   - Yellow = GPIO signal

---

### PHASE 1 — Motor Protection Circuits (Most Critical)

**This is what prevented before. Do not skip any step.**

**For EACH TB6612FNG chip (repeat twice):**

1. **Solder polyfuse** in series with VM (motor power) input:
   ```
   Battery (+) ──── [3A Polyfuse] ──── VM pin on TB6612FNG
   ```
   - Polyfuse resets after cooling — protects against shorts
   - Voltage drop: ~0.1V at 1A (negligible)

2. **Solder decoupling capacitors on VM pin**:
   ```
   VM pin ──┬── 4700µF electrolytic (+) → GND
            └── 100nF ceramic → GND
   ```
   - Electrolytic: observe polarity (+) toward VM, (-) toward GND
   - Place as close to the VM pin as physically possible (<1cm)
   - This absorbs back-EMF spikes from motor braking

3. **Solder logic decoupling on VCC pin**:
   ```
   VCC pin ──── 100nF ceramic → GND
   ```

4. **Measure before connecting motors**:
   - Multimeter between VM and GND: should read open circuit (no shorts)
   - Multimeter between VCC and GND: should read >1kΩ
   - If any reading is <100Ω: find the short before proceeding

---

### PHASE 2 — I2C Bus Setup

1. Connect all I2C devices in parallel on SDA (GPIO2) and SCL (GPIO3):
   ```
   Pi GPIO2 ────┬──── BH1750 SDA
               ├──── MAX17043 SDA  (UPS HAT, already connected)
               ├──── PCA9685 SDA
               ├──── SSD1306 #1 SDA (left eye)
               ├──── SSD1306 #2 SDA (right eye)
               └──── MPU6050 SDA
   ```
   Same for SCL on GPIO3.

2. **SSD1306 right eye — A0 pad:**
   - Locate the A0 solder pad on the back of the PCB
   - Bridge it to VCC with a small solder blob
   - Verify with: `i2cdetect -y 1` → should show both 0x3C and 0x3D
   - If you see only one address, the bridge isn't solid

3. **I2C pull-up resistors**: Most breakout boards have 4.7kΩ pull-ups. If you see repeated I2C errors, add a 4.7kΩ from SDA→3.3V and SCL→3.3V at the Pi end.

4. **Test each device individually**:
   ```bash
   # Power on Pi only (no motor power yet)
   i2cdetect -y 1
   # Should show: 23, 36, 40, 3c, 3d, 68
   python3 -c "from smbus2 import SMBus; b=SMBus(1); print('OK')"
   ```

---

### PHASE 3 — PCA9685 (PWM Board)

1. Connect PCA9685 to I2C bus (done in Phase 2)
2. Connect PCA9685 power:
   ```
   PCA9685 VCC  → Pi 3.3V (logic only)
   PCA9685 GND  → Pi GND
   PCA9685 V+   → 5V (servo/motor PWM power, NOT motor rail)
   ```
3. Address: default 0x40, all A0-A5 pads unsoldered
4. **Do NOT connect servos yet** — test PWM output first:
   ```bash
   python3 -c "
   from adafruit_pca9685 import PCA9685
   from board import SCL, SDA
   import busio
   i2c = busio.I2C(SCL, SDA)
   pca = PCA9685(i2c)
   pca.frequency = 50
   print('PCA9685 OK, freq=50Hz')
   pca.deinit()
   "
   ```

---

### PHASE 4 — TB6612FNG Motor Drivers

**Connect Chip 1 (Left motors) first. Test completely before Chip 2.**

**Chip 1 wiring:**
```
TB6612FNG #1 Pin │ Connect to
─────────────────┼──────────────────────────
VM               │ Battery+ (through 3A polyfuse)
VCC              │ Pi 3.3V
GND              │ Common GND
STBY             │ GPIO27 (shared with Chip 2)
AIN1             │ GPIO17
AIN2             │ GPIO22
PWMA             │ PCA9685 CH0
BIN1             │ GPIO23
BIN2             │ GPIO26
PWMB             │ PCA9685 CH1
AO1, AO2         │ Left-front motor
BO1, BO2         │ Left-rear motor
```

**Test Chip 1 before connecting Chip 2:**
```bash
# With motors connected but at LOW speed first
python3 tools/test_motor_chip1.py
# Verify left-front and left-rear rotate correctly
# Check chip temperature after 30s run: should be <45°C
```

**Chip 2 wiring:**
```
TB6612FNG #2 Pin │ Connect to
─────────────────┼──────────────────────────
VM               │ Battery+ (through separate 3A polyfuse)
VCC              │ Pi 3.3V
GND              │ Common GND
STBY             │ GPIO27 (shared with Chip 1)
AIN1             │ GPIO6
AIN2             │ GPIO10
PWMA             │ PCA9685 CH2
BIN1             │ GPIO19
BIN2             │ GPIO20
PWMB             │ PCA9685 CH3
AO1, AO2         │ Right-front motor
BO1, BO2         │ Right-rear motor
```

**Motor wire polarity test**:
```bash
python3 tools/motor_polarity_check.py
# Drive each motor forward for 0.5s
# If any motor goes backward when commanded forward, swap AO1/AO2 for that motor
```

---

### PHASE 5 — Sensors

**Cliff sensors (via Logic Level Converter):**
```
TCRT5000 VCC  → 5V
TCRT5000 GND  → GND
TCRT5000 D0   → LLC HIGH side → LLC LOW side → GPIO14 (left)
TCRT5000 D0   → LLC HIGH side → LLC LOW side → GPIO15 (right)
```
After wiring: run `tools/calibrate_cliff.py` immediately. Set sensor height to ~2cm above floor.

**PIR motion sensor (HC-SR501):**
```
HC-SR501 VCC  → 5V
HC-SR501 GND  → GND
HC-SR501 OUT  → GPIO8 (HC-SR501 outputs 3.3V on Pi-compatible models, check label)
```
If your HC-SR501 outputs 5V, add LLC or a 2kΩ/3.3kΩ voltage divider.

**MPU6050 IMU:**
```
MPU6050 VCC   → 3.3V
MPU6050 GND   → GND
MPU6050 SDA   → GPIO2 (I2C bus)
MPU6050 SCL   → GPIO3 (I2C bus)
MPU6050 INT   → GPIO6 (optional interrupt)
MPU6050 AD0   → GND (sets I2C address to 0x68)
```

**Touch sensors (TTP223):**
```
TTP223 VCC → 3.3V
TTP223 GND → GND
TTP223 OUT → GPIO5 (head), GPIO4 (left cheek), GPIO7 (right cheek)
```
TTP223 outputs are 3.3V compatible. No LLC needed.

---

### PHASE 6 — Pan/Tilt Servo Mount

```
Pan servo  signal → PCA9685 CH4
Tilt servo signal → PCA9685 CH5
Both servos VCC   → 5V (separate supply or PCA9685 V+)
Both servos GND   → Common GND
```

**Servo calibration:**
```bash
python3 tools/calibrate_servos.py
# Centers both servos
# Records min/max PWM pulse widths
# Saves to config/servo_calibration.yaml
```

---

## Wiring Verification Checklist

Run through this before powering motors:

- [ ] VM to GND resistance > 10Ω on each TB6612FNG
- [ ] 4700µF cap installed on each VM pin (correct polarity)
- [ ] 100nF cap installed on each VM pin
- [ ] 3A polyfuse installed in each VM line
- [ ] i2cdetect shows all 6 expected addresses (0x23, 0x36, 0x40, 0x3C, 0x3D, 0x68)
- [ ] SSD1306 right eye at 0x3D (A0 bridged)
- [ ] Motor wire polarity verified for all 4 motors
- [ ] Cliff sensor calibration complete
- [ ] Servo centers verified
- [ ] Pi and motor power on separate rails (not shared)
- [ ] All GNDs connected (common ground between Pi, motor driver, sensors)
- [ ] Motor STBY pin tested (GPIO27 HIGH = motors enabled)

---

## Common Mistakes to Avoid

| Mistake | Consequence | Prevention |
|---------|-------------|------------|
| No cap on VM | Chip burns on first motor stop | Always add 4700µF + 100nF |
| Shared power rail | Pi crashes when motors run | Buck converter for logic power |
| Wrong cap polarity | Capacitor explodes | + side toward VM, - toward GND |
| Motors connected while powered | Spike on VM at connection moment | Always disconnect power before wiring |
| I2C without common GND | Phantom I2C errors | All GNDs must be joined |
| Cliff sensors untested | Robot falls off table | Always calibrate before autonomy |
| No polyfuse | Short circuit burns motor driver | Add polyfuse always |
| Sharing STBY across chips | Chip 2 can't be individually stopped | Share STBY only if you accept this |

---

## Power-On Sequence (Every Time)

1. Connect Pi USB-C power → Pi boots, sensors initialize
2. Verify `curl http://localhost:8000/hardware` shows expected components
3. Verify i2cdetect output is clean
4. **Only then**: Connect motor battery
5. Run `python3 tools/motor_test_safe.py` → brief low-speed test
6. If any chip gets hot (>50°C in 30s), DISCONNECT IMMEDIATELY and check wiring

**Never connect motor battery step 4 before completing steps 1-3.**
