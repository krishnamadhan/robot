# 🤖 Robot Pet Project — Complete Context & Handoff
**Madhan Krishnamadhan · Sobha Dream Acres, Balagere, Bangalore**
**Started: May 2026 · Status: Active Build**

---

## 📋 Table of Contents
1. [Project Overview](#1-project-overview)
2. [Raspberry Pi 5 Setup](#2-raspberry-pi-5-setup)
3. [UPS HAT Installation](#3-ups-hat-installation)
4. [Software Stack on Pi](#4-software-stack-on-pi)
5. [BanterAgent Deployment](#5-banteragent-deployment)
6. [Robot Hardware](#6-robot-hardware)
7. [All Purchases — Complete List](#7-all-purchases--complete-list)
8. [Wiring & GPIO Assignments](#8-wiring--gpio-assignments)
9. [Testing Done So Far](#9-testing-done-so-far)
10. [Pending Items](#10-pending-items)
11. [Architecture Decisions](#11-architecture-decisions)
12. [Safety Rules](#12-safety-rules)

---

## 1. Project Overview

Building a **fully autonomous robot pet** powered by Raspberry Pi 5.
The robot will eventually:
- Move and navigate autonomously (2WD chassis, TT gear motors)
- Display animated eye expressions (2× OLED displays)
- Detect and react to: voice, touch, gestures, light, motion, vibration, obstacles, cliffs
- Recognise faces locally (Google Coral USB accelerator — Phase 3)
- Speak back (USB speaker — Phase 2)
- Listen to voice commands (USB mic — Phase 2)
- Report health and accept admin commands via WhatsApp (!pi status, !pi restart bot, etc.)

The Pi simultaneously hosts:
- **BanterAgent** — WhatsApp bot for the Banter Squad Tamil friend group
- **Robot control** — Python motor control + sensor loop
- **Pi monitor** — health monitoring daemon

### Build Phases
| Phase | What | Status |
|---|---|---|
| 0 | Robot moves reliably + safety systems | 🔧 In progress |
| 1 | Eyes + voice + personality | ⏳ Parts arriving |
| 2 | Camera + fast storage | ⏳ Later |
| 3 | Local AI face recognition | ⏳ Later |

---

## 2. Raspberry Pi 5 Setup

### Hardware
- **Model**: Raspberry Pi 5 8GB RAM
- **OS**: Raspberry Pi OS (Debian trixie, 64-bit)
- **Storage**: MicroSD card
- **Cooling**: Official Pi 5 Active Cooler (clips on top, fan connector)
- **Power**: DFRobot FIT0992 UPS HAT (below Pi via pogo pins)

### Network
- **Local IP**: `192.168.1.30` (STATIC — configured in dhcpcd.conf)
  - Note: Pi was seen at 192.168.1.200 during one session
- **Tailscale IP**: `100.101.250.126`
- **Hostname**: `raspberrypi` (default)
- **SSH user**: `pi`

### SSH Access
```bash
# From home WiFi
ssh pi@192.168.1.30

# From anywhere via Tailscale
ssh pi@100.101.250.126
```

### SSH Config (on PC — ~/.ssh/config)
```
Host 192.168.1.30
    ServerAliveInterval 60
    ServerAliveCountMax 10

Host 100.101.250.126
    ServerAliveInterval 60
    ServerAliveCountMax 10
```

### Initial Setup Steps Done
1. Flashed Raspberry Pi OS using Raspberry Pi Imager
2. Connected monitor + keyboard for initial setup (has full desktop OS)
3. Enabled SSH: `sudo systemctl enable ssh && sudo systemctl start ssh`
4. Set static IP in `/etc/dhcpcd.conf`:
   ```
   interface wlan0
   static ip_address=192.168.1.30/24
   static routers=192.168.1.254
   static domain_name_servers=8.8.8.8 8.8.4.4
   ```
5. Installed Tailscale: `curl -fsSL https://tailscale.com/install.sh | sh`
6. Installed Node.js 20:
   ```bash
   curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
   sudo apt install -y nodejs git
   ```
7. Installed PM2: `sudo npm install -g pm2`
8. Installed Python GPIO: `sudo apt install -y python3-gpiozero`

### System Stats (at time of setup)
- Node.js: v20.20.2
- npm: 10.8.2
- PM2: 6.0.14
- OS: Debian trixie
- RAM: 7.9GB total, ~6.4GB free at idle
- Disk: 58GB total, 8.3GB used (15%)
- Temperature: 52–54°C at idle (healthy)

### Installed Python Libraries
```bash
pip3 install smbus2 --break-system-packages
```

### I2C Setup
```bash
# Enable I2C
sudo raspi-config → Interface Options → I2C → Enable → Reboot

# Verify
sudo i2cdetect -y 1
# Should show: 0x36 (UPS HAT) and 0x23 (BH1750 when connected)
```

---

## 3. UPS HAT Installation

### Hardware: DFRobot FIT0992 UPS HAT
- **Output**: 5.1V / 5A
- **Batteries**: 4× 18650 Li-ion (unprotected, flat-top)
- **Connection to Pi**: Pogo pins on bottom of Pi (test pads)
- **I2C monitoring**: Uses GPIO2 (SDA) and GPIO3 (SCL) at address **0x36**
- **Charging**: 12V DC via barrel jack (5.5×2.1mm, centre positive)
- **Charger**: FEDUS 12V 3A DC adapter

### Critical Notes
- UPS HAT is **below** the Pi — Pi sits on top
- GPIO header (40 pins) on top of Pi is **completely free**
- UPS HAT uses I2C (GPIO2/GPIO3) for battery monitoring — shared bus, no conflict with sensors
- The HAT uses 4× Pi mounting holes — you **cannot** put another HAT on top that needs these holes
- Active Cooler clips on top of Pi — compatible with UPS HAT below

### Power Architecture
```
Wall outlet (230V)
    ↓
FEDUS 12V 3A adapter (5.5×2.1mm barrel)
    ↓
UPS HAT DC jack (charges 18650 cells)
    ↓
UPS HAT pogo pins → Pi 5 (5.1V 5A)

LiPo 7.4V 2200mAh (separate from Pi power)
    ↓
T-plug → TB6612FNG VM → TT motors
```

### Pi Config for UPS HAT
Added to `/boot/firmware/config.txt`:
```
POWER_OFF_ON_HALT=1
PSU_MAX_CURRENT=5000
```

---

## 4. Software Stack on Pi

### PM2 Processes Running
```
┌─────┬──────────────┬─────────┬──────┬───────────┐
│ id  │ name         │ status  │  ↺   │ memory    │
├─────┼──────────────┼─────────┼──────┼───────────┤
│ 0   │ banteragent  │ online  │ 0    │ ~250MB    │
│ 1   │ pi-monitor   │ online  │ 0    │ ~50MB     │
└─────┴──────────────┴─────────┴──────┴───────────┘
```

### PM2 Commands
```bash
pm2 status                          # Check all processes
pm2 show banteragent                # Detailed stats
pm2 logs banteragent --lines 50     # Recent logs
pm2 restart banteragent             # Restart bot
pm2 save                            # Persist process list
pm2 startup                         # Auto-start on reboot
```

### Node.js Heap Fix
BanterAgent started with limited heap — fixed with:
```bash
pm2 start dist/index.js --name banteragent --node-args="--max-old-space-size=512"
```

### Claude Code Remote Session
Claude Code is installed on Pi and configured to start automatically after reboot.
This allows mobile → Claude.ai → Claude Code on Pi → live code changes workflow.

```bash
# Install
sudo npm install -g @anthropic-ai/claude-code

# Start session
cd ~/banteragent
claude
```

---

## 5. BanterAgent Deployment

### What It Is
WhatsApp bot for the Banter Squad Tamil friend group.
- **Tech**: whatsapp-web.js, TypeScript, Supabase, Anthropic Claude API
- **Repo**: https://github.com/krishnamadhan/banteragent
- **Location on Pi**: `~/banteragent/`
- **Process**: PM2 as `banteragent`

### Environment
- Chromium path: `/usr/bin/chromium-browser` (ARM64)
- `.env` contains: Supabase URL, anon key, Anthropic API key, and more
- `.wwebjs_auth/` stores WhatsApp session — **NEVER DELETE THIS**
- WhatsApp QR was scanned during setup — session active

### Deploy Workflow
```bash
# From PC
git add . && git commit -m "fix: description"
git push

# From Pi (or via deploy.sh)
cd ~/banteragent
git pull
npm install
npm run build
pm2 restart banteragent
pm2 save
```

### WhatsApp Admin Commands (via !pi prefix)
These are sent to bot from Madhan's WhatsApp number:
- `!pi status` — Full Pi health report
- `!pi temp` — CPU temperature
- `!pi battery` — UPS HAT battery level
- `!pi logs 20` — Last 20 BanterAgent log lines
- `!pi errors` — Recent error logs
- `!pi restart bot` — Restart BanterAgent via PM2
- `!pi update bot` — git pull + build + restart
- `!pi disk` — Disk usage
- `!pi clean` — Safe cleanup (logs + cache)
- `!pi network` — Network + Tailscale status
- `!pi uptime` — System uptime
- `!pi usage` — API usage (Claude + RapidAPI)
- `!pi help` — All commands list

### Pi Monitor Service
- **Location**: `~/pi-monitor/monitor.py`
- **PM2 name**: `pi-monitor`
- **Runs**: Python 3, checks every 60 seconds
- **Alerts sent via WhatsApp when**:
  - CPU temp > 75°C
  - RAM > 90%
  - Disk > 90%
  - Battery < 10%
  - BanterAgent crashes
  - Network down

---

## 6. Robot Hardware

### Chassis
- **Type**: 2WD Round Double-Deck Acrylic Chassis
- **Motors**: 2× TT Gear Motors (yellow, included in kit)
  - Rated: 3–6V, 160mA running, 1.5A stall
  - Motor wires: soldered during build
- **Wheels**: 2× large yellow drive wheels + 2× caster wheels
- **Status**: Assembled ✅

### Motor Driver
- **TB6612FNG** (primary — in use)
  - 3.3V native logic — direct Pi GPIO
  - 0.5V voltage drop (motors get 6.9V from 7.4V LiPo)
  - 1.2A continuous / 3.2A peak per channel
  - **Soldered**: Pin headers done ✅
- **L298N** (backup — not in use)
  - Needs 5V logic (would need LLC for Pi)
  - 2V voltage drop (motors only get 5.4V)
  - Kept as spare

### Motor Power (LiPo)
- **Battery**: Pro-Range 7.4V 2200mAh 45C 2S LiPo
  - T-plug (Deans) connector
  - Runtime: 90–120 minutes
  - **Status**: In parcel, not yet charged
- **Charger**: iMax B6 Digital LiPo Charger
  - Powered by FEDUS 12V 3A adapter
  - Charges 2S at 1–2A (never leave unattended)
- **Temporary**: 4× AA battery holder (6V) for testing while LiPo charges

### Sensors Inventory
| Sensor | Interface | GPIO | Status |
|---|---|---|---|
| BH1750 Light | I2C 0x23 | GPIO2/3 | ✅ Soldered + wired |
| MPU-6050 Gyro | I2C 0x68 | GPIO2/3 | ✅ In parcel |
| APDS9960 Gesture | I2C 0x39 | GPIO2/3 | ✅ In parcel (needs solder) |
| HC-SR501 PIR | Digital | GPIO16 | ✅ In parcel |
| TTP223 Touch ×4 | Digital | GPIO5, GPIO25 | ✅ In parcel |
| HC-SR04 Ultrasonic | Digital + LLC | GPIO16(TRIG), GPIO24(ECHO) | ✅ Have it |
| TCRT5000 Cliff ×2 | Digital + LLC | GPIO20, GPIO21 | ✅ In parcel |
| KY-038 Sound | Digital + Divider | GPIO19 | ✅ In parcel |
| SW-420 Vibration | Digital + Divider | GPIO26 | ✅ Have it |
| 2× OLED 1.3" | I2C 0x3C/3D | GPIO2/3 | ✅ In parcel (needs solder) |
| Logitech Webcam | USB | USB port | ✅ Already owned |

---

## 7. All Purchases — Complete List

### Robu.in Order (Delivered)
| Item | Price |
|---|---|
| 2WD Round Chassis Kit | ₹350 |
| Pro-Range 2200mAh 2S 45C LiPo | ₹1,551 |
| Official Pi 5 Active Cooler | ₹488 |
| 2× TCRT5000 IR Cliff Sensor | ₹78 |
| GPIO T-Breakout + Ribbon + Breadboard | ₹204 |
| HC-SR501 PIR Motion Sensor | ₹61 |
| GY-9960 APDS9960 Gesture Sensor | ₹349 |
| Analog Sound Sensor KY-038 | ₹99 |
| MPU-6050 Gyroscope | ₹169 |
| TTP223 Touch Module (2pcs × 2) | ₹36 |
| 2× 1.3" OLED White I2C | ₹640 |

### Robocraze Order (Delivered)
| Item | Price |
|---|---|
| iMax B6 Digital LiPo Charger | ₹1,919 |
| GY-302 BH1750 Light Sensor | ₹165 |
| Jumper Wire Set M2M+M2F+F2F | ₹128 |
| Veroboard 6×4" | ₹39 |
| SW-420 Vibration Sensor | ₹38 |
| T-plug Deans Connector (M+F) | ₹24 |
| Hook-up Wire 5 colours 1m | ₹52 |

### Amazon.in Orders (Delivered)
| Item | Price |
|---|---|
| Robodo L298N Motor Driver | ₹200 |
| TB6612FNG Motor Driver | ₹250 |
| HC-SR04 Ultrasonic Sensor | ₹80 |
| Logic Level Converter 4-ch | ₹100 |
| Aptechdeals Jumper Wires 120pcs | ₹190 |
| Electronic Spices 4× AA Battery Holder | ₹60 |
| FEDUS 12V 3A DC Adapter | ₹300 |

### Amazon.in Orders (Arriving 8 May)
| Item | Price |
|---|---|
| Electronic Spices 50× 10kΩ Resistors | ₹99 |

### Amazon.in Orders (Arriving 13 May)
| Item | Price |
|---|---|
| KITS4CREATORS 100µF Capacitors (10pcs) | ₹99 |
| JWCO 470µF 16V Capacitor | ₹159 |

### Already Owned
| Item | Source |
|---|---|
| Raspberry Pi 5 8GB | — |
| DFRobot FIT0992 UPS HAT + 4× 18650 | — |
| Logitech Carl Zeiss 1080p Webcam | — |
| Soldering iron + solder wire | — |
| 10× 1kΩ resistors | Local |

### Still to Buy
| Item | Store | Price |
|---|---|---|
| M3 Standoffs + Screws Kit | Amazon | ₹350 |
| Velcro + Zip ties + Foam tape | Amazon | ₹230 |
| LiPo Voltage Checker 1-8S | Amazon | ₹100 |
| Mini USB Speaker 3W | Amazon | ₹350 |
| Assorted Capacitor Kit (optional) | Robu.in | ₹150 |

### Nearest Electronics Shop for Components
**Rishab Electronics** — Marathahalli Main Rd (~3.5km from home)
📞 080-5005-7390 · Open 9:30AM–9:30PM daily · 4.5⭐ rating
For: resistors, capacitors, any small components on demand
