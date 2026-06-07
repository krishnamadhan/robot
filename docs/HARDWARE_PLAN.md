# docs/HARDWARE_PLAN.md — Cosmo ESP32-S3 Body-Controller Migration Plan

> Created: 2026-06-05  
> Status: **BLOCKED — awaiting Madhan pin-map review before any wiring**  
> Author: Claude Code (session start)  
> Supersedes: docs/HARDWARE_NOTES.md (for migration-related content)

---

## 0. Purpose

This document is the authoritative hardware plan for migrating Cosmo's body peripherals
from Pi GPIO to an ESP32-S3-DevKitC-1. It must be reviewed and approved by Madhan before
any physical wiring or firmware is generated. After approval it becomes the wiring bible
for all subsequent migration sessions.

---

## 1. Ground Truth: Current Pi Hardware State

> Source of truth: `CLAUDE.md` GPIO map (confirmed 2026-05-30 with pin_test.py + multimeter)
> and `config/hardware.yaml` (last corrected 2026-05-14, updated incrementally).

### 1.1 Pi GPIO Map — Actual State (BCM numbering)

| GPIO | Pin | State / Owner |
|------|-----|---------------|
| 0    | 27  | RESERVED — HAT EEPROM I2C0 |
| 1    | 28  | RESERVED — HAT EEPROM I2C0 |
| 2    | 3   | I2C1 SDA — all sensors + UPS |
| 3    | 5   | I2C1 SCL — all sensors + UPS |
| 4    | 7   | **DEAD** — stuck LOW (do not use) |
| 5    | 29  | **DEAD** — stuck LOW (do not use) |
| 6    | 31  | **RESERVED — FIT0992 AC-fail detect** (HAT drives, never touch) |
| 7    | 26  | **DEAD** — stuck LOW (do not use) |
| 8    | 24  | **DEAD** — stuck HIGH (SPI0 CE0 idles HIGH — unfixable on RP1) |
| 9    | 21  | **DEAD** — stuck HIGH (SPI0 MISO idles HIGH — unfixable on RP1) |
| 10   | 19  | FREE |
| 11   | 23  | TB6612FNG PWMA — Left motor PWM |
| 12   | 32  | **DEAD** — stuck LOW (do not use) |
| 13   | 33  | **DEAD** — stuck HIGH (pwm_fan — Pi 5 CPU fan, cannot disable) |
| 14   | 8   | **DEAD** — stuck LOW (UART TX — console=serial0,115200) |
| 15   | 22  | FREE / HC-SR04 TRIG (config maps it here; ultrasonic available=false) |
| 16   | 36  | **RESERVED — FIT0992 charging-disable** (never touch) |
| 17   | 11  | TB6612FNG AIN1 — Left direction 1 |
| 18   | 12  | TB6612FNG PWMB — Right motor PWM |
| 19   | 35  | **DEAD** — stuck LOW (do not use) |
| 20   | 38  | TB6612FNG BIN1 — Right direction 1 |
| 21   | 40  | **DEAD** — stuck HIGH (do not use) |
| 22   | 15  | TB6612FNG AIN2 — Left direction 2 |
| 23   | 16  | FREE |
| 24   | 18  | TB6612FNG BIN2 — Right direction 2 |
| 25   | 22  | FREE |
| 26   | 37  | FREE |
| 27   | 13  | TB6612FNG STBY — HIGH = motors enabled |

**Active motor wiring (2WD confirmed):** AIN1=17, AIN2=22, PWMA=11 (Left) | BIN1=20, BIN2=24, PWMB=18 (Right) | STBY=27  
`left_rear` and `right_rear` entries in `hardware.yaml` are defined in code but NOT physically wired (phantom config — left TB6612FNG board was removed, caster at rear).

### 1.2 Pi I2C Bus (GPIO2 SDA / GPIO3 SCL — bus 1)

| Address | Device | Status |
|---------|--------|--------|
| 0x10    | DFRobot UPS HAT battery IC | Active |
| 0x23    | BH1750 Light Sensor | Wired, `available: true` |
| 0x36    | UPS HAT fuel gauge (MAX17043) | Active |
| 0x39    | APDS-9960 Gesture | Replacement ordered, `available: false` |
| 0x3C    | OLED Left Eye SSD1306 | Hardware arrived, not yet wired |
| 0x3D    | OLED Right Eye SSD1306 | Hardware arrived, not yet wired |
| 0x40    | PCA9685 Servo Driver | On order |
| 0x68    | MPU-6050 Gyro/Accel | Wired, `available: false` |

---

## 2. OLED Eyes Decision

**Decision: Keep OLED eyes on the Pi I2C bus (0x3C + 0x3D) — do not move to ESP32.**

**Rationale:**
- `expression/eyes.py` is tightly coupled to the personality and state machine — expressions
  fire inline with mood/event handlers; any inter-board latency would desync face from voice.
- The Pi I2C bus has capacity for both OLEDs plus the remaining sensors that stay.
- Moving to ESP32 would require a new animation RPC protocol and add ~5ms of latency to every
  blink/expression change — no benefit at this stage.
- Revisit: if Phase 5 needs offloaded OLED animation or the Pi I2C bus becomes saturated,
  OLED migration to ESP32 is a defined upgrade path (both addresses are I2C, ESP32 can drive
  SSD1306 trivially).

**Action:** Wire OLEDs to Pi I2C bus per current Phase 1.5 plan (Phase 1.5 task P0 is unchanged).

---

## 3. Peripheral Migration Table

What moves to ESP32, what stays on Pi:

| Peripheral | Current Pi GPIO/I2C | Migration Target | Phase |
|------------|---------------------|------------------|-------|
| TB6612FNG motors (2WD) | GPIO11/17/18/20/22/24/27 | ESP32 GPIO | M2 |
| HC-SR04 ultrasonic | GPIO15 (TRIG), GPIO14 (ECHO dead) | ESP32 GPIO + RMT | M3 |
| MPU-6050 IMU | Pi I2C 0x68 | ESP32 I2C 0x68 | M3 |
| APDS9960 gesture | Pi I2C 0x39 | ESP32 I2C 0x39 | M3 |
| PCA9685 + 4× MG90S | Pi I2C 0x40 | ESP32 I2C 0x40 | M5 |
| TTP223 touch × 4 | No free Pi GPIO | ESP32 GPIO | M4 |
| PIR HC-SR501 | No free Pi GPIO | ESP32 GPIO | M4 |
| KY-038 sound | No free Pi GPIO | ESP32 GPIO | M4 |
| SW-420 vibration | No free Pi GPIO | ESP32 GPIO | M4 |
| OLED Left Eye (0x3C) | Pi I2C (wire now) | **STAYS on Pi** | — |
| OLED Right Eye (0x3D) | Pi I2C (wire now) | **STAYS on Pi** | — |
| BH1750 light (0x23) | Pi I2C | **STAYS on Pi** | — |
| UPS HAT (0x36/0x10) | Pi I2C | **STAYS on Pi** | — |
| C920 camera | USB | **STAYS on Pi** | — |
| INMP441 mic | Pi I2S / C920 USB | **STAYS on Pi** | — |
| JBL Flip 5 speaker | Bluetooth | **STAYS on Pi** | — |

---

## 4. ESP32-S3 Pin Registry Plan

**Board:** 7Semi ESP32-S3-DevKitC-1, module ESP32-S3-WROOM-1, N8R8  
**Forbidden ESP32 pins (never wire peripherals here):**
- GPIO33–37: Octal PSRAM bus (always active even when PSRAM disabled in firmware)
- GPIO19/20: Native USB D-/D+ (leave free for the USB-CDC serial link to Pi)
- GPIO43/44: UART0 TX/RX (default debug console — keep for flashing/debug)
- GPIO0, GPIO3, GPIO45, GPIO46: Strapping pins — avoid; safe as input only

**Onboard:** GPIO38 = RGB status LED (used for heartbeat + phase blink indicators)

### 4.1 Full ESP32 GPIO Assignment

| ESP32 GPIO | Direction | Peripheral | Role | Voltage Note |
|------------|-----------|------------|------|--------------|
| **MOTORS** | | | | |
| GPIO1      | OUT       | TB6612FNG AIN1 | Left motor dir-A | 3.3V logic → VCC |
| GPIO2      | OUT       | TB6612FNG AIN2 | Left motor dir-B | 3.3V logic → VCC |
| GPIO4      | OUT       | TB6612FNG PWM-L | Left motor speed | 3.3V LEDC/PWM |
| GPIO5      | OUT       | TB6612FNG BIN1 | Right motor dir-A | 3.3V logic → VCC |
| GPIO6      | OUT       | TB6612FNG BIN2 | Right motor dir-B | 3.3V logic → VCC |
| GPIO7      | OUT       | TB6612FNG PWM-R | Right motor speed | 3.3V LEDC/PWM |
| GPIO15     | OUT       | TB6612FNG STBY | Shared standby | 3.3V; HIGH = enabled |
| **ULTRASONIC** | | | | |
| GPIO8      | OUT       | HC-SR04 TRIG | Trigger pulse | 3.3V → ok |
| GPIO9      | IN        | HC-SR04 ECHO | Echo return | 5V → 3.3V via LV/HV level shifter module (Madhan has one) |
| **I2C BUS** | | | | |
| GPIO10     | I/O       | I2C SDA | All ESP32-side I2C devices | 3.3V; 4.7kΩ pullup |
| GPIO11     | I/O       | I2C SCL | All ESP32-side I2C devices | 3.3V; 4.7kΩ pullup |
| **TOUCH (TTP223 — digital output)** | | | | |
| GPIO12     | IN        | TTP223 Head | Touch head | 3.3V |
| GPIO13     | IN        | TTP223 Left | Touch left side | 3.3V |
| GPIO14     | IN        | TTP223 Right | Touch right side | 3.3V |
| GPIO16     | IN        | TTP223 Belly | Touch belly | 3.3V |
| **DISCRETE SENSORS** | | | | |
| GPIO17     | IN        | PIR HC-SR501 | Motion detect | 3.3V (jumper selectable on PIR) |
| GPIO18     | IN        | KY-038 D0 | Sound/clap detect | 3.3V |
| GPIO21     | IN        | SW-420 D0 | Vibration/tilt | 3.3V |
| **SYSTEM** | | | | |
| GPIO19     | —         | Native USB D- | Pi ↔ ESP32 CDC serial | leave free |
| GPIO20     | —         | Native USB D+ | Pi ↔ ESP32 CDC serial | leave free |
| GPIO38     | OUT       | RGB LED | Status (onboard) | 3.3V WS2812 |
| GPIO43     | OUT       | UART0 TX | Debug console | leave for flashing |
| GPIO44     | IN        | UART0 RX | Debug console | leave for flashing |
| GPIO33–37  | —         | PSRAM | NEVER wire peripherals | hardware-reserved |

### 4.2 ESP32 I2C Devices

| Address | Device | Migrated From |
|---------|--------|---------------|
| 0x68    | MPU-6050 Gyro/Accel | Pi I2C bus |
| 0x39    | APDS-9960 Gesture | Pi I2C bus |
| 0x40    | PCA9685 Servo Driver | Pi I2C bus (was on order) |

### 4.3 Free ESP32 GPIO (available for future use)

GPIO22, GPIO23, GPIO25, GPIO26, GPIO27, GPIO28, GPIO29, GPIO30, GPIO31, GPIO32,  
GPIO39, GPIO40, GPIO41, GPIO42, GPIO47, GPIO48  
(16 pins free — ample headroom)

### 4.4 Native Capacitive Touch Note

ESP32-S3 GPIO1–14 support native capacitive touch sensing. The TTP223 ICs are assigned
to GPIO12–14/16 (digital read). In phase M4, Madhan can optionally swap 3 of the TTP223
ICs for bare copper-pad touch targets wired directly to GPIO12–14 and enable the ESP32
`touch_pad` driver — no code change on the Pi side (same event format). GPIO16 (belly)
would still need TTP223 since it falls outside T1–T14. Document the experiment result in
DECISIONS.md when M4 is attempted.

---

## 5. Pi GPIO After Migration

After M2–M5 complete, the Pi's active GPIO footprint shrinks to:

| GPIO | Role |
|------|------|
| 2    | I2C1 SDA (UPS HAT + BH1750 + OLEDs) |
| 3    | I2C1 SCL |
| 6    | RESERVED — FIT0992 AC-fail (HAT owns, never touch) |
| 16   | RESERVED — FIT0992 charging-disable (HAT owns, never touch) |

**Freed Pi GPIOs (available after migration):** 10, 11, 15, 17, 18, 20, 22, 23, 24, 25, 26, 27  
**Permanently dead (never recoverable):** 4, 5, 7, 8, 9, 12, 13, 14, 19, 21

**Conflict resolutions achieved by migration:**
- GPIO6: Old motor config had BIN2 here (burned 5 chips); moved to GPIO10 in current config,
  now fully freed by moving all motors to ESP32.
- GPIO16: Old HC-SR04 TRIG was here (toggled charging on every ping); remapped to GPIO15 in
  current config, now fully freed by moving HC-SR04 to ESP32.

---

## 6. Power Plan

**Default:** Pi USB-A (5V/1.5A typical) → ESP32-S3 USB-C (native USB port = GPIO19/20).
This gives power + CDC-serial in one cable — clean, no extra PSU.

**Motor + servo power:** LiPo 7.4V → TB6612FNG VM terminal. ESP32 GPIO logic-only → VCC (3.3V).
Never route motor power through ESP32 pins.

**ESP32 VCC (3.3V) → TB6612FNG VCC (logic supply):** use the ESP32's onboard 3.3V rail.

**Fallback:** If Pi USB current budget causes brownouts with C920 also attached, tap the
FIT0992 5V output rail for ESP32 power and use the second USB-C port (UART bridge) for data only.
Confirm by measuring Pi USB rail voltage under load before committing to fallback.

---

## 7. Serial Link

- **Transport:** USB-CDC (`/dev/ttyACM0`) — native USB from ESP32-S3. Falls back to UART
  bridge (`/dev/ttyUSB0`) if native USB has issues.
- **Protocol:** Newline-delimited JSON, UTF-8, per spec §7 in MIGRATE_TO_ESP32.md.
- **Heartbeat:** Pi → ESP32 `{"v":1,"t":"hb","seq":N}` at ~10 Hz.
- **Failsafe:** If no hb or cmd received within 200ms, ESP32 drives STBY LOW (motors off)
  and centers all servos — hardware-enforced, cannot be defeated by Pi-side crash.

---

## 8. Pi-Side Config Flag Design

Each subsystem gets a backend key in `config/hardware.yaml` and/or env var:

```yaml
# Added under each section once ESP32 backend is proven
motors:
  backend: pi_gpio    # → esp32_serial when M2 validated

sensors:
  ultrasonic:
    backend: pi_gpio  # → esp32_serial when M3 validated
  mpu6050:
    backend: pi_gpio  # → esp32_serial when M3 validated
```

The ESP32 serial backend (`hardware/backends/esp32_serial.py`) implements the same ABCs
as the current GPIO drivers. Switching a subsystem = one YAML line change + PM2 restart.
No brain/personality/state-machine code changes.

---

## 9. Phased Migration Order

| Phase | Description | Physical action needed | Gate |
|-------|-------------|----------------------|------|
| **M0** | Scaffolding: protocol spec, `esp32_serial.py` stub, fake-ESP32 simulator, offline tests | None (software only) | Green offline tests |
| **M1** | Firmware skeleton: serial JSON loop, heartbeat watchdog, RGB status, telemetry tick | Flash ESP32 (Madhan + USB-C cable) | `BLOCKED:` Pi/ESP32 handshake seen in logs |
| **M2** | Motors on ESP32 | Rewire TB6612FNG: disconnect Pi GPIO11/17/18/20/22/24/27 → connect ESP32 GPIO1/2/4/5/6/7/15 | `BLOCKED:` rewire + failsafe test (kill Pi link → motors stop) |
| **M3** | HC-SR04 + MPU-6050 + APDS9960 | Move HC-SR04 wires (TRIG→ESP32 GPIO8, ECHO→GPIO9 via divider); move MPU-6050 + APDS9960 I2C wires to ESP32 GPIO10/11 | `BLOCKED:` rewire |
| **M4** | Discrete sensors | Wire TTP223×4, PIR, KY-038, SW-420 to ESP32 GPIO12-14/16-18/21 | `BLOCKED:` rewire |
| **M5** | Servos | Wire PCA9685 to ESP32 I2C (GPIO10/11); confirm center-on-failsafe | `BLOCKED:` PCA9685 arrival + rewire |
| **M6** | Retire + harden | Remove dead Pi-GPIO code paths; update both pin registries; soak-test serial reconnect | Software + final validation |

**Rollback per subsystem:** flip `backend: esp32_serial` back to `backend: pi_gpio` in
hardware.yaml and PM2 restart. The Pi-GPIO backend is never deleted until M6.

---

## 10. ESP32-Side Pin Registry (Firmware)

The ESP32 firmware must maintain a `pin_registry[]` array checked at startup:

```c
// esp32_pin_registry.h  (compile-time assertion table)
static const struct { int gpio; const char* owner; } ESP32_PIN_MAP[] = {
    {1,  "motors.left.ain1"},
    {2,  "motors.left.ain2"},
    {4,  "motors.left.pwm"},
    {5,  "motors.right.bin1"},
    {6,  "motors.right.bin2"},
    {7,  "motors.right.pwm"},
    {8,  "ultrasonic.trig"},
    {9,  "ultrasonic.echo"},
    {10, "i2c.sda"},
    {11, "i2c.scl"},
    {12, "touch.head"},
    {13, "touch.left"},
    {14, "touch.right"},
    {15, "motors.stby"},
    {16, "touch.belly"},
    {17, "pir"},
    {18, "sound.d0"},
    {21, "vibration.d0"},
    {38, "status_led"},
    // FORBIDDEN — assert these are never claimed:
    // 19, 20 (native USB), 33-37 (PSRAM), 43, 44 (UART0)
};
// Boot: loop and assert no duplicate gpio values.
// Any duplicate → halt with Serial error message.
```

---

## 11. Key Voltage / Wiring Rules

1. **HC-SR04 ECHO is 5V output.** ESP32-S3 GPIO input max = 3.3V. Use LV/HV level shifter
   module (Madhan has one): HV side → HC-SR04 ECHO (5V), LV side → ESP32 GPIO9 (3.3V).
   Connect HV to 5V rail, LV to 3.3V rail, GND common.
2. **TB6612FNG VCC (logic)** = 3.3V from ESP32 3.3V rail. VM (motor) = 7.4V LiPo. Never swap.
3. **I2C pullups on ESP32 bus:** 4.7kΩ to 3.3V on SDA (GPIO10) and SCL (GPIO11). The Pi I2C
   bus keeps its own pullups; the two buses are electrically independent after migration.
4. **HC-SR501 (PIR) output:** typically 3.3V configurable — check jumper on board before wiring.
5. **470µF cap across TB6612FNG VM+GND** and **220µF caps per motor terminal pair** — install
   before first LiPo motor test regardless of which controller is driving.
6. **ESP32 GPIO logic drives only** — all power for motors/servos comes from LiPo/PSU rail,
   not ESP32 pins.

---

## 12. Files This Plan Creates/Modifies

| File | Action | Phase |
|------|--------|-------|
| `docs/HARDWARE_PLAN.md` | THIS FILE | M0 |
| `docs/ESP32_MIGRATION_STATE.md` | Created at M0 start | M0 |
| `hardware/backends/esp32_serial.py` | New ESP32 HAL backend | M0 |
| `hardware/backends/fake_esp32.py` | Offline test simulator | M0 |
| `config/hardware.yaml` | Add `backend:` keys per subsystem | M0+ |
| `hardware/pin_registry.py` | Extend to mark ESP32-migrated pins as released | M6 |
| `esp32_firmware/` (new dir) | PlatformIO/Arduino project | M1 |
| `esp32_firmware/src/pin_registry.h` | ESP32-side pin map | M1 |

---

## BLOCKED — Waiting for Madhan to Review

Before any wiring, firmware, or code is generated, Madhan must sanity-check this plan.

**Review checklist for Madhan:**

- [ ] **ESP32 GPIO1/2 (motors AIN1/AIN2):** GPIO1 is also T1 (touch) and GPIO2 is T2. Using as digital OUT is fine, but confirm no boot-time pull conflicts. Strapping on S3 is GPIO0/3/45/46 only, so GPIO1/2 are clean.
- [ ] **GPIO3 left free:** GPIO3 is a strapping pin (VDD_SPI). Leaving it unconnected — confirm this is correct for your board variant.
- [x] **HC-SR04 ECHO level shifting:** Madhan has an LV/HV level shifter module — use that. HV side → ECHO (5V), LV side → GPIO9 (3.3V). No resistors needed.
- [ ] **Motor wiring scope:** Only left_front and right_front channels are physically wired (2WD confirmed 2026-05-30). The ESP32 firmware will drive 2 channels + STBY. Confirm: do NOT add wiring for left_rear/right_rear until 4WD board arrives.
- [ ] **PIR HC-SR501 logic level:** Check jumper on the PIR PCB — ensure 3.3V mode before wiring to GPIO17.
- [ ] **Power source choice:** Start with Pi USB-A → ESP32 native USB-C (one cable). If C920 brownouts appear, fallback to FIT0992 5V rail for power + UART bridge for serial. Confirm this is acceptable.
- [ ] **I2C pullup resistors:** 4.7kΩ resistors on ESP32 SDA/SCL to 3.3V — do you have these? MPU-6050 breakout usually has built-in pullups (check if they're 3.3V or 5V).
- [ ] **OLED eyes staying on Pi:** Confirmed acceptable — wire to Pi I2C as Phase 1.5 P0 task.
- [ ] **Serial port detection:** `/dev/ttyACM0` (native USB) or `/dev/ttyUSB0` (UART bridge) — will make it auto-detect + configurable in `hardware.yaml`.

**Reply with:** "Plan approved" or corrections to any of the items above. Then M0 software work begins.

---

*Do not generate firmware, ESP32 code, or move any wiring until Madhan approves this plan.*
