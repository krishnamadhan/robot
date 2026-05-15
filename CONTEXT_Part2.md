# 🤖 Robot Pet Project — Context Part 2
## Wiring, Testing, Architecture & Code

---

## 8. Wiring & GPIO Assignments

### Final Corrected GPIO Map (v2.0)

> ⚠️ All Pi 5 GPIO pins are 3.3V maximum. No exceptions.

```
Pi Physical Pin  →  GPIO  →  Connected To                   →  Protection
─────────────────────────────────────────────────────────────────────────────
Pin 1  (3.3V)    →  PWR   →  TB6612 VCC, sensors, LLC LV    →  None
Pin 2  (5V)      →  PWR   →  HC-SR04, PIR, LLC HV, sensors  →  None
Pin 3  (GPIO2)   →  SDA   →  I2C Bus (all I2C devices)      →  None
Pin 5  (GPIO3)   →  SCL   →  I2C Bus (all I2C devices)      →  None
Pin 6  (GND)     →  GND   →  All GNDs — star ground point   →  None
Pin 11 (GPIO17)  →  AIN1  →  TB6612FNG left motor dir 1     →  None
Pin 12 (GPIO18)  →  PWMA  →  TB6612FNG left motor speed     →  HW PWM0 ✅
Pin 13 (GPIO27)  →  STBY  →  TB6612FNG enable/standby       →  LOW at boot
Pin 15 (GPIO22)  →  AIN2  →  TB6612FNG left motor dir 2     →  None
Pin 16 (GPIO23)  →  BIN1  →  TB6612FNG right motor dir 1    →  None
Pin 18 (GPIO24)  →  ECHO  →  HC-SR04 ECHO via LLC ch1       →  LLC ✅
Pin 22 (GPIO25)  →  OUT   →  TTP223 Touch RIGHT              →  None
Pin 29 (GPIO5)   →  OUT   →  TTP223 Touch LEFT               →  None
Pin 31 (GPIO6)   →  BIN2  →  TB6612FNG right motor dir 2    →  None [CORRECTED from GPIO24]
Pin 32 (GPIO12)  →  INT   →  APDS-9960 interrupt (optional) →  None
Pin 33 (GPIO13)  →  PWMB  →  TB6612FNG right motor speed    →  HW PWM1 ✅ [CORRECTED from GPIO25]
Pin 35 (GPIO19)  →  DO    →  KY-038 Sound via divider        →  10kΩ+10kΩ ✅
Pin 36 (GPIO16)  →  TRIG  →  HC-SR04 TRIG (DIRECT)          →  None (3.3V valid)
Pin 37 (GPIO26)  →  DO    →  SW-420 Vibration via divider    →  10kΩ+10kΩ ✅
Pin 38 (GPIO20)  →  DO    →  TCRT5000 Cliff LEFT via LLC ch3 →  LLC ✅
Pin 40 (GPIO21)  →  DO    →  TCRT5000 Cliff RIGHT via LLC ch4→  LLC ✅
```

### LLC Channel Assignment
```
LLC Channel 1:  HC-SR04 ECHO  (HV1 → LV1 → GPIO24)
LLC Channel 2:  FREE
LLC Channel 3:  TCRT5000 LEFT (HV3 → LV3 → GPIO20)
LLC Channel 4:  TCRT5000 RIGHT(HV4 → LV4 → GPIO21)
```

### I2C Device Addresses
```
0x10  →  DFRobot UPS HAT battery IC
0x23  →  BH1750 Light Sensor (ADDR pin → GND)
0x39  →  APDS-9960 Gesture Sensor (fixed address)
0x3C  →  OLED Left Eye (default address)
0x3D  →  OLED Right Eye (A0 pad bridged with solder blob)
0x68  →  MPU-6050 Gyroscope (AD0 → GND)
```

Verify: `sudo i2cdetect -y 1` should show all 6 addresses.

### TB6612FNG Wiring (Critical)
```
Pi 3.3V (Pin 1)  →  TB VCC       (logic power — 3.3V ONLY)
LiPo (+) T-plug  →  TB VM        (motor power — LiPo ONLY, NEVER Pi 5V)
Pi GND  (Pin 6)  →  TB GND       (shared ground)
LiPo (−)         →  TB GND       (motor ground — same rail)
Pi GPIO17        →  TB AIN1
Pi GPIO18        →  TB PWMA      (hardware PWM0)
Pi GPIO27        →  TB STBY
Pi GPIO22        →  TB AIN2
Pi GPIO23        →  TB BIN1
Pi GPIO6         →  TB BIN2      (CORRECTED — was GPIO24)
Pi GPIO13        →  TB PWMB      (hardware PWM1 CORRECTED — was GPIO25)
TB AO1/AO2       →  Left motor wires
TB BO1/BO2       →  Right motor wires
```

> ⚠️ NEVER connect Pi 5V to TB VM. LiPo (7.4V) would back-feed into Pi 5V rail → instant destruction.

### TB6612FNG Truth Table
```
AIN1=1, AIN2=0, PWMA=0–100%  →  Left motor Forward ✅
AIN1=0, AIN2=1, PWMA=0–100%  →  Left motor Backward ✅
AIN1=0, AIN2=0, PWMA=0        →  Brake/Stop ✅
AIN1=1, AIN2=1  (any PWMA)   →  PROHIBITED — short circuit ❌

Same logic for BIN1/BIN2/PWMB → Right motor
```

> Always set OFF pin LOW before setting ON pin HIGH.

### Voltage Divider (for 5V sensors → GPIO)
```
Sensor DO (5V) → R1 (10kΩ) → GPIO pin junction → R2 (10kΩ) → GND
Result at GPIO: ~2.5V (safe for 3.3V GPIO)

Sensors needing divider: KY-038 Sound, SW-420 Vibration
Temporarily using 1kΩ+1kΩ for testing (gives same 2.5V, ok for bench test)
```

### Capacitors (Not yet installed — arriving 13 May)
```
470µF electrolytic  →  across TB6612FNG VM and GND terminals (bulk cap)
100µF electrolytic  →  across each motor's two wire terminals (×2, back-EMF)
```

---

## 9. Testing Done So Far

### ✅ System & Connectivity
- Pi boots successfully from UPS HAT (battery power)
- SSH works from PC: `ssh pi@192.168.1.30`
- Tailscale connected: `100.101.250.126`
- Static IP confirmed: `192.168.1.30`
- I2C enabled and working
- `i2cdetect -y 1` shows: `0x23` (BH1750) and `0x36` (UPS HAT)

### ✅ BH1750 Light Sensor
- **Wired**: VCC→Pin1, GND→Pin6, SCL→Pin5, SDA→Pin3, ADDR→Pin6
- **I2C address**: 0x23 confirmed
- **Test**: `smbus2` library, reads lux values
- **Behaviour**: Lux drops when covered, rises with torch
- **Status**: Working ✅

### ⏳ Motor Test (Ready to run)
- TB6612FNG soldered and wired to Pi GPIO (all 9 connections)
- Motors wired to AO1/AO2 and BO1/BO2
- AA battery holder connected to VM and GND
- Test script ready: `~/robot/test_motors.py`
- **Not yet run** — waiting to confirm wiring before first power-on

### ⏳ All Other Sensors
Not yet tested — queued in order:
1. MPU-6050 (just 4 wires, test now)
2. HC-SR501 PIR (just 3 wires, test now)
3. TTP223 Touch (just 3 wires, test now)
4. HC-SR04 (needs LLC soldered)
5. KY-038 Sound (needs resistors — arriving 8 May)
6. SW-420 Vibration (needs resistors — arriving 8 May)
7. TCRT5000 ×2 (needs LLC)
8. OLED ×2 (needs pin headers soldered)
9. APDS9960 (needs pin headers soldered)

---

## 10. Pending Items

### Soldering Queue
| Item | Pins | Notes |
|---|---|---|
| LLC 4-channel | 4+4 pins | Needed for HC-SR04 ECHO, TCRT5000 |
| OLED Left Eye | 4 pins | VCC GND SCL SDA |
| OLED Right Eye | 4 pins | Same + bridge A0 pad for 0x3D address |
| APDS9960 | 6 pins | VCC GND SCL SDA INT LED |

### Orders Arriving
| Date | Item |
|---|---|
| **8 May** | 50× 10kΩ resistors |
| **13 May** | 100µF capacitors (10pcs) |
| **13 May** | 470µF capacitor |

### Still to Order (Amazon)
- M3 standoffs + screws kit (₹350)
- Velcro + zip ties + foam tape (₹230)
- LiPo voltage checker 1-8S (₹100)
- Mini USB speaker 3W (₹350)

### LiPo Battery
- Not yet charged — charger (iMax B6) arrived but first charge not done
- First charge: connect FEDUS 12V adapter → iMax B6 → LiPo main T-plug + balance connector
- Set: LiPo Charge, 2S, 1.0A, START
- Time: ~2.5 hours for first charge
- **Using AA batteries temporarily for motor testing**

---

## 11. Architecture Decisions

### Why TB6612FNG Instead of L298N
- L298N needs 5V logic → requires LLC for Pi 3.3V GPIO
- TB6612FNG: 3.3V native → direct GPIO, no LLC
- L298N: 2V voltage drop (motors get 5.4V from 7.4V LiPo)
- TB6612FNG: 0.5V drop (motors get 6.9V) — more power
- TB6612FNG is smaller, cooler, simpler wiring

### Why Two Separate Power Systems
- Pi: powered by UPS HAT 18650 cells (always clean 5.1V)
- Motors: powered by LiPo via TB6612FNG
- Separation prevents motor current spikes from affecting Pi or sensors
- Both share GND (required for PWM signal reference)

### Why Hardware PWM (GPIO18 + GPIO13)
- Software PWM uses CPU timing → jitter when CPU busy
- Hardware PWM is handled by dedicated silicon → zero jitter regardless of load
- GPIO18 = PWM0, GPIO13 = PWM1 on Pi 5

### Why Not Use HATs for Sensors
- UPS HAT uses Pi mounting holes (via pogo pins below) → no top HATs
- All sensors connect via jumper wires to GPIO header directly
- I2C, SPI, and direct GPIO all available without any HAT conflicts

### Why Logitech Webcam Instead of Pi Camera Module 3
- Already owned → saves ₹3,650
- USB plug-and-play, no CSI cable drama
- 1080p is sufficient for face detection
- No Pi 5 special ribbon cable needed

### Safe Boot Sequence (in robot_control.py)
```
1. Import → STBY = LOW immediately (motors disabled)
2. Sensor warm-up (1.5 second sleep)
3. HC-SR04 + cliff sensor self-test
4. Start watchdog thread (must run BEFORE motors enable)
5. Start sensor loop (feeds heartbeat)
6. Verify heartbeat age < 500ms
7. STBY = HIGH (motors enabled — only if all above passed)
8. Start motor loop
```

### Safety Priority Stack
```
Priority 1 (highest): FAILSAFE — I2C hang, watchdog timeout
Priority 2: CLIFF DETECTED — TCRT5000 triggers → instant reverse
Priority 3: OBSTACLE < 10cm — HC-SR04 → timed reverse
Priority 4: OBSTACLE 10-30cm — turn to escape
Priority 5: USER COMMAND — from keyboard/API (TTL 300ms)
Priority 6 (lowest): AUTONOMOUS WANDER
```

Emergency actions (cliff, obstacle) bypass motor ramping for instant response.
Normal actions (user commands, wander) use 150ms ramp.

### Dead-Man Stop
- Heartbeat updated in `sensor_loop()` after each successful sensor read
- If sensor_loop stalls → heartbeat ages
- Watchdog kills motors within 500ms of stale heartbeat
- This is separate from crash detection (process kill handled by PM2)

---

## 12. Safety Rules

### Electrical
1. **ALWAYS power off Pi before wiring** — `sudo shutdown now` + unplug USB-C
2. **Pi 5 GPIO = 3.3V MAX on ALL pins** — no exceptions, no 5V tolerant pins
3. **LiPo to TB VM ONLY** — never connect Pi 5V to TB VM (back-feed destroys Pi)
4. **TB6612FNG truth table** — AIN1+AIN2 must NEVER both be HIGH (H-bridge short)
5. **Set OFF pin LOW before ON pin HIGH** — prevents momentary both-HIGH state
6. **5V sensor outputs need protection** — LLC or voltage divider before GPIO

### LiPo Battery
1. Never short-circuit LiPo terminals
2. Never discharge below 3.0V per cell (6.0V total for 2S)
3. Never charge unattended — monitor first charge fully
4. Never puncture or bend
5. If battery swells — stop using immediately
6. Store in metal tin or ceramic tray while charging
7. Threshold: 6.8V (3.4V/cell) = stop motors immediately

### Software
1. STBY = LOW at boot, HIGH only after all sensors verified
2. Watchdog running before motors enable
3. Snapshot state for thread safety (not raw dict access)
4. TTL on user commands (300ms expiry)
5. Rate limit motor commands (20Hz max)
6. Emergency actions bypass ramp — all others ramp over 150ms

---

## 13. Key File Locations on Pi

```
~/banteragent/          - BanterAgent WhatsApp bot
~/banteragent/.env      - Environment variables (never commit)
~/banteragent/.wwebjs_auth/  - WhatsApp session (NEVER DELETE)
~/robot/                - Robot control scripts
~/robot/robot_control.py     - Main robot control (complete)
~/robot/test_bh1750.py  - BH1750 test script
~/pi-monitor/           - Health monitoring daemon
~/pi-monitor/monitor.py - Python monitor script
~/pi-monitor/status.json - Latest Pi stats (updated every 60s)
~/SETUP.md              - Initial setup context for Claude Code
```

---

## 14. Diagnostic Commands

```bash
# I2C scan — verify all sensors detected
sudo i2cdetect -y 1

# CPU temperature
vcgencmd measure_temp

# Throttle/voltage warnings
vcgencmd get_throttled

# System memory
free -h

# Disk space
df -h /

# PM2 all processes
pm2 status

# BanterAgent logs
pm2 logs banteragent --lines 50

# Robot logs
pm2 logs robot --lines 50

# Restart robot
pm2 restart robot

# GPIO pin states
pinctrl get

# Check Pi is getting full voltage (0x0 = healthy)
vcgencmd get_throttled
# 0x0 = good, anything else = throttling or undervoltage

# Test specific GPIO pin
python3 -c "from gpiozero import LED; l=LED(17); l.on(); import time; time.sleep(2); l.off()"
```

---

## 15. Robot Control Code Summary

### File: `~/robot/robot_control.py`
Complete production-ready motor control loop. Key features:
- Direct TB6612FNG control (no gpiozero.Robot conflict)
- Hardware PWM on GPIO18 (PWMA) and GPIO13 (PWMB)
- Correct pin ordering (OFF first, then ON — prevents H-bridge short)
- Heartbeat in sensor_loop (not separate thread)
- Dead-man stop (500ms watchdog)
- Rate limiting at 20Hz
- Thread-safe state snapshot
- TTL user commands (300ms expiry)
- Timed actions inside motor loop (no threading.Thread for safety actions)
- Motor ramping (150ms, 10 steps) — bypassed for emergencies
- LEFT_TRIM / RIGHT_TRIM constants for drift correction
- Keyboard control (W/A/S/D) for SSH testing
- `get_robot_status()` function for WhatsApp integration

### Tuning Constants (top of robot_control.py)
```python
LOOP_HZ           = 20      # Motor loop frequency
DEADMAN_TIMEOUT   = 0.5     # Seconds before watchdog kills
CMD_TTL           = 0.3     # User command lifetime
RAMP_TIME         = 0.15    # Speed ramp duration (seconds)
DIST_STOP         = 10      # cm — full stop + reverse
DIST_SLOW         = 30      # cm — turn to escape
NORMAL_SPEED      = 0.55    # Default speed (0–1)
WANDER_SPEED      = 0.25    # Autonomous wander speed
LEFT_TRIM         = 1.0     # Adjust if robot drifts right
RIGHT_TRIM        = 1.0     # Adjust if robot drifts left
```

### Deploy Robot Control via PM2
```bash
# Copy robot_control.py to Pi first
scp robot_control.py pi@192.168.1.30:~/robot/robot_control.py

# Install dependencies on Pi
sudo apt install -y python3-gpiozero

# Test interactively (keyboard control)
python3 ~/robot/robot_control.py

# Run headless via PM2
pm2 start robot_control.py --name robot --interpreter python3
pm2 save
```

---

## 16. Next Steps (Priority Order)

### Immediate (Today)
1. **Test motors** — `python3 ~/robot/test_motors.py` with AA batteries
2. **Wire MPU-6050** — 4 jumper wires, run `~/robot/test_mpu6050.py`
3. **Wire HC-SR501 PIR** — 3 wires, test motion detection
4. **Wire TTP223 Touch** — 3 wires per sensor, test touch response
5. **Solder LLC** — needed for HC-SR04 and TCRT5000

### When Resistors Arrive (8 May)
6. Build voltage dividers for KY-038 and SW-420
7. Test sound sensor and vibration sensor

### When Capacitors Arrive (13 May)
8. Install 470µF across TB6612FNG VM and GND
9. Install 100µF across each motor terminal

### Assembly Phase
10. Solder OLED pin headers and bridge A0 for second display
11. Solder APDS9960 pin headers
12. Mount all components on chassis
13. Wire everything permanently on veroboard
14. Charge LiPo and run first full robot_control.py test

### Phase 1 Build
15. Deploy robot_control.py via PM2
16. Animate OLED eyes (RoboEyes library)
17. Add PIR → wake-up behaviour
18. Add touch → heart eyes animation
19. Add mood system (sleepy at night, active during day)

---

## 17. Reference Links

| Resource | URL |
|---|---|
| DFRobot UPS HAT Wiki | https://wiki.dfrobot.com/fit0992/ |
| TB6612FNG SparkFun Guide | https://learn.sparkfun.com/tutorials/tb6612fng-hookup-guide |
| gpiozero Documentation | https://gpiozero.readthedocs.io/ |
| Pi 5 GPIO Pinout | https://pinout.xyz/ |
| RoboEyes OLED Animation | https://github.com/ldrs/roboEyes |
| SSD1306 OLED Python | https://github.com/adafruit/Adafruit_CircuitPython_SSD1306 |
| APDS-9960 Python | https://github.com/liske/python-apds9960 |
| MPU-6050 Python | https://github.com/m-rtijn/mpu6050 |
| HC-SR04 Pi Tutorial | https://www.raspberrypi-spy.co.uk/2012/12/ultrasonic-distance-measurement-using-python-part-1/ |
| BanterAgent Repo | https://github.com/krishnamadhan/banteragent |

---

## 18. Claude Code Prompt Template

Use this to continue work in Claude Code on Pi or PC:

```
I am building a robot pet on Raspberry Pi 5. Here is my current context:

Pi SSH: pi@192.168.1.30 (LAN) | pi@100.101.250.126 (Tailscale)
Pi user: pi

HARDWARE:
- Pi 5 8GB, Raspberry Pi OS Debian trixie
- DFRobot FIT0992 UPS HAT (below Pi, pogo pins, I2C 0x36)
- Official Pi 5 Active Cooler (on top, fan connector)
- 2WD chassis with 2 TT gear motors
- TB6612FNG motor driver (soldered, wired)
  AIN1=GPIO17, PWMA=GPIO18, STBY=GPIO27, AIN2=GPIO22
  BIN1=GPIO23, BIN2=GPIO6, PWMB=GPIO13
- Pro-Range 7.4V 2200mAh 2S LiPo (motor power)
- BH1750 light sensor wired at I2C 0x23
- All other sensors in ~/robot/ test queue

RUNNING PROCESSES (PM2):
- banteragent (WhatsApp bot, Node.js)
- pi-monitor (health monitoring, Python)
- robot (robot_control.py when deployed)

KEY FILES:
- ~/banteragent/ - WhatsApp bot
- ~/robot/robot_control.py - main robot control
- ~/pi-monitor/monitor.py - health daemon

WIRING CORRECTIONS (IMPORTANT):
- PWMB → GPIO13 (NOT GPIO25 — hardware PWM1)
- BIN2 → GPIO6 (NOT GPIO24 — conflict with ECHO)
- HC-SR04 TRIG → GPIO16 DIRECT (no LLC needed)
- HC-SR04 ECHO → GPIO24 via LLC channel 1
- TCRT5000 → GPIO20/21 via LLC channels 3+4
- KY-038/SW-420 → need 10kΩ+10kΩ voltage divider
- LiPo to TB VM ONLY (never Pi 5V to VM)

DO NOT:
- Delete ~/banteragent/.wwebjs_auth/
- Touch ~/robot/ GPIO scripts without care
- Connect 5V signals directly to GPIO
- Run !pi restart pi automatically

[Describe your specific task here]
```
