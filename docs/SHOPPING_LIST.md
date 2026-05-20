# Cosmo — Shopping List

> Last updated: 2026-05-20  
> Based on 4WD + pan/tilt servo mount configuration

## What You Already Have ✅

| Item | Status | Notes |
|------|--------|-------|
| Raspberry Pi 5 8GB | ✅ Have | Running |
| Logitech C920 webcam | ✅ Have | Camera + mic |
| JBL Flip 5 BT speaker | ✅ Have | Paired (28:FA:19:C1:73:F8) |
| HC-SR04 ultrasonic | ✅ Have | Wired, working |
| BH1750 light sensor | ✅ Have | I2C 0x23, working |
| MAX17043 UPS HAT | ✅ Have | Battery monitor, working |
| SSD1306 OLED 128×64 | ✅ Have ×2 | A0 solder needed on right |
| PCA9685 PWM board | ✅ Have | Not wired yet |
| MPU6050 IMU | ✅ Have | Not wired yet |
| 4WD robot chassis | ✅ Have | Motors and wheels included? |
| LLC (Logic Level Converter) | ✅ Have? | Verify qty — need ×2 boards |

---

## Must Buy Before Phase 1 (Motors Working)

| Item | Qty | Where to Buy | Approx Cost | Why |
|------|-----|-------------|-------------|-----|
| TB6612FNG motor driver module | 2 | Amazon/Robocraze | ₹120-150 each | Replacement for burned chips |
| 4700µF 16V electrolytic capacitor | 6 | Local electronics shop / Amazon | ₹15-20 each | Motor back-EMF protection (buy extra) |
| 100nF ceramic capacitor | 20 | Local electronics shop | ₹2-5 each | Decoupling (buy in pack) |
| 3A polyfuse (resettable, 5V) | 4 | Amazon / Electroncomponents | ₹30-50 each | Short circuit protection |
| 7.4V 2S LiPo 2200mAh+ | 1 | Amazon / RC hobby shop | ₹800-1200 | Motor power (better than AA) |
| LiPo balance charger | 1 | Amazon | ₹600-1000 | Charge 2S LiPo safely |
| USB-C PD 5V 3A buck converter | 1 | Amazon | ₹300-500 | Clean logic power from LiPo |
| Pan-tilt bracket kit | 1 | Amazon/Robocraze | ₹250-400 | Camera servo mount |
| MG90S metal gear servo | 2 | Amazon/Robocraze | ₹150-200 each | Pan + tilt |

---

## Should Buy for Phase 2 (Full Sensing)

| Item | Qty | Where to Buy | Approx Cost | Why |
|------|-----|-------------|-------------|-----|
| HC-SR501 PIR sensor | 1 | Amazon | ₹60-80 | Motion detection |
| TCRT5000 cliff sensor module | 2 | Amazon/Robocraze | ₹40-60 each | Cliff detection |
| TTP223 capacitive touch sensor | 4 | Amazon | ₹30-50 each | Head + 2 cheeks + belly |
| JST 2-pin connectors (pack) | 1 | Amazon | ₹100-150 | Clean motor connections |
| Dupont wire assortment | 1 | Amazon | ₹100-150 | Jumper wires |
| Heat shrink tube pack | 1 | Amazon/local | ₹80-100 | Protect solder joints |
| Multimeter (if you don't have) | 1 | Amazon | ₹300-500 | Essential for debugging |

---

## Optional (Phase 3+)

| Item | Qty | Approx Cost | Why |
|------|-----|-------------|-----|
| INMP441 I2S microphone | 1 | ₹150-200 | Better voice input vs C920 mic |
| APDS9960 proximity+gesture | 1 | ₹200-300 | Close proximity + raw gesture |
| Picovoice account + "Hey Cosmo" | 1 | Free (personal tier) | Custom wake word |
| Encoders for TT motors | 4 | ₹80-100 each | Accurate odometry (big upgrade) |

---

## Where to Buy in India

| Vendor | Good for | Link |
|--------|----------|------|
| Robocraze | Robot parts, motors, sensors | robocraze.com |
| Amazon India | Everything, fast delivery | amazon.in |
| EVELTA / Sunrom | Electronic components, caps | evelta.com / sunrom.com |
| Local shop | Capacitors, resistors (buy in person) | SP Road Bangalore / Lamington Road Mumbai |

---

## Total Budget Estimate

| Phase | Items | Cost |
|-------|-------|------|
| Phase 1 (motors working) | TB6612FNG ×2 + caps + fuses + LiPo + charger + buck + servos | ₹4,000-6,000 |
| Phase 2 (full sensing) | PIR + cliff ×2 + touch ×4 + connectors | ₹800-1,200 |
| Phase 3 (optional) | INMP441 + encoders | ₹600-1,000 |
| **Total** | | **₹5,400-8,200** |
