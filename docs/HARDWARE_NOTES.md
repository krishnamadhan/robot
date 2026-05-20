# HARDWARE_NOTES.md — Wiring Reference & Safety Rules

> Reference before any GPIO, I2C, or power work.  
> Mistakes here destroy hardware. Read carefully.

---

## ⚡ Power Architecture

```
SYSTEM 1 — Pi / Logic Power (always-on)
  Wall 230V AC
    → FEDUS 12V 3A DC adapter (5.5×2.1mm barrel)
    → DFRobot FIT0992 UPS HAT (4× 18650 cells, pogo pins)
    → Pi 5 (5.1V, 5A via pogo pins — NOT 40-pin header)
    → Pi 3.3V rail → TB6612FNG VCC, all sensors, LLC LV side

SYSTEM 2 — Motor Power (completely isolated)
  Pro-Range 7.4V 2200mAh 2S LiPo
    → XT60 female pigtail (on order — using 6V AA pack until arrives)
    → TB6612FNG VM terminal
    → TT gear motors via AO1/AO2, BO1/BO2

  Shared: Pi GND and LiPo GND share common at TB6612FNG GND — intentional and required
```

## ⚠️ Power Safety Rules — Violating these destroys hardware

1. **LiPo 7.4V → TB6612FNG VM ONLY** — never to Pi 5V rail. Back-feed = permanent Pi destruction (₹7,500+)
2. **Pi 3.3V → TB6612FNG VCC** — NOT 5V (TB6612FNG logic runs at 3.3V)
3. **Pi GND + LiPo GND share common** at TB6612FNG GND — required for reference
4. **All Pi 5 GPIO pins are 3.3V maximum** — no exceptions, no 5V-tolerant pins on Pi 5
5. **AIN1 + AIN2 must NEVER both be HIGH** — shorts H-bridge, destroys TB6612FNG
6. **Always set OFF pin LOW before ON pin HIGH** — prevents momentary both-HIGH
7. **STBY pin must be LOW at boot** — raise HIGH only after sensor self-test passes
8. **470µF bulk cap across VM+GND required** before connecting LiPo
9. **220µF caps per motor terminal pair required** before connecting LiPo

---

## GPIO Map (BCM numbering — always BCM)

| GPIO | Physical Pin | Connected To | Voltage | Notes |
|---|---|---|---|---|
| GPIO2 | Pin 3 | I2C SDA (shared) | 3.3V | All I2C devices |
| GPIO3 | Pin 5 | I2C SCL (shared) | 3.3V | All I2C devices |
| GPIO4 | Pin 7 | TTP223 Touch LEFT | 3.3V | Active HIGH |
| GPIO5 | Pin 29 | TTP223 Touch HEAD | 3.3V | Active HIGH |
| GPIO6 | Pin 31 | TB6612FNG BIN2 | 3.3V | Right motor backward ⚠️ CONFLICT FIXED |
| GPIO7 | Pin 26 | TTP223 Touch RIGHT | 3.3V | Active HIGH |
| GPIO8 | Pin 24 | HC-SR501 PIR OUT | 3.3V | Active HIGH on motion |
| GPIO13 | Pin 33 | TB6612FNG PWMB (left_rear PWM) | 3.3V | HW PWM1 |
| GPIO14 | Pin 8  | TCRT5000 Cliff LEFT | 3.3V | Via LLC (5V→3.3V) — moved from GPIO20 |
| GPIO15 | Pin 10 | TCRT5000 Cliff RIGHT | 3.3V | Via LLC (5V→3.3V) — moved from GPIO21 |
| GPIO16 | Pin 36 | HC-SR04 TRIG | 3.3V | Direct — sensor accepts 3.3V |
| GPIO17 | Pin 11 | TB6612FNG AIN1 (left_front dir1) | 3.3V | Left forward direction |
| GPIO18 | Pin 12 | TB6612FNG PWMA (right_front PWM) | 3.3V | HW PWM0, freed I2S BCLK |
| GPIO19 | Pin 35 | TB6612FNG PWMB (right_rear PWM) | 3.3V | HW PWM1 alt, freed I2S LRCLK |
| GPIO20 | Pin 38 | TB6612FNG AIN1 (right_front dir1) | 3.3V | Freed I2S DIN |
| GPIO21 | Pin 40 | TB6612FNG AIN2 (right_front dir2) | 3.3V | Freed I2S DOUT |
| GPIO22 | Pin 15 | TB6612FNG AIN2 (left_front dir2) | 3.3V | Left backward direction |
| GPIO23 | Pin 16 | TB6612FNG BIN1 (left_rear dir1) | 3.3V | Left rear forward |
| GPIO24 | Pin 18 | HC-SR04 ECHO | 3.3V | Via LLC ch1 (5V→3.3V) |
| GPIO25 | Pin 22 | TB6612FNG BIN1 (right_rear dir1) | 3.3V | Reassigned from belly touch |
| GPIO26 | Pin 37 | TB6612FNG BIN2 (right_rear dir2) | 3.3V | Reassigned from vibration sensor |
| GPIO27 | Pin 13 | TB6612FNG STBY | 3.3V | LOW at boot, HIGH after self-test |
| GPIO11 | Pin 23 | KY-038 Sound DO | 2.5V | Via 10kΩ+10kΩ divider — moved from GPIO19 |

## I2C Address Map (verify with: `sudo i2cdetect -y 1`)

| Address | Device | Status | Notes |
|---|---|---|---|
| 0x10 | DFRobot UPS HAT battery IC | ✅ Active | Read-only monitoring |
| 0x23 | BH1750 Light Sensor | ⚠️ Wired, not enabled | Enable in hardware.yaml |
| 0x36 | UPS HAT main IC | ✅ Active | Battery %, voltage |
| 0x39 | APDS-9960 Gesture | ❌ Faulty unit | Replacement ordered |
| 0x3C | OLED Left Eye SSD1306 | ⚠️ Not yet wired | Wire this session |
| 0x3D | OLED Right Eye SSD1306 | ⚠️ Not yet wired | Bridge A0 pad first |
| 0x40 | PCA9685 Servo Driver | ❌ Not arrived | Phase 3 |
| 0x68 | MPU-6050 Gyro/Accel | ⚠️ Wired, not enabled | AD0 pin → GND |

## Logic Level Converter Channels

| LLC Channel | Connected | Direction | Notes |
|---|---|---|---|
| Ch 1 | HC-SR04 ECHO | HV→LV | 5V ECHO → 3.3V GPIO24 |
| Ch 2 | TCRT5000 LEFT | HV→LV | 5V OUT → 3.3V GPIO14 (moved from GPIO20) |
| Ch 3 | TCRT5000 RIGHT | HV→LV | 5V OUT → 3.3V GPIO15 (moved from GPIO21) |
| Ch 4 | FREE | — | Spare |
| LV rail | Pi 3.3V | — | — |
| HV rail | Pi 5V | — | — |

## Voltage Divider Network

| Sensor | Output V | Resistors | Result at GPIO | Notes |
|---|---|---|---|---|
| KY-038 Sound (DO) | 5V | 10kΩ + 10kΩ | 2.5V ✅ | GPIO11 (moved from GPIO19) |
| SW-420 Vibration (DO) | 5V | 10kΩ + 10kΩ | — | GPIO26 now motor BIN2 — sensor unconnected |

## OLED Wiring (hardware arrived — wire now)

```
Both OLEDs:
  VCC → Pi Pin 1 (3.3V)
  GND → Pi Pin 6 (GND)
  SDA → Pi Pin 3 (GPIO2 — shared I2C)
  SCL → Pi Pin 5 (GPIO3 — shared I2C)

Right eye ONLY — before wiring:
  Bridge the A0 pad on the back of the board with a solder blob
  This changes address from default 0x3C → 0x3D

Verify after wiring:
  sudo i2cdetect -y 1
  Should show BOTH 0x3C and 0x3D
```

## /boot/firmware/config.txt required entry

```
PSU_MAX_CURRENT=5000
```
(Required for UPS HAT + Pi 5 Active Cooler power negotiation)

---

## Nearest Component Shop

**Rishab Electronics** — Marathahalli Main Rd (~3.5km from Sobha Dream Acres)  
📞 080-5005-7390 · Open 9:30AM–9:30PM daily · 4.5⭐  
Buy: resistors, capacitors, small passives — same day
