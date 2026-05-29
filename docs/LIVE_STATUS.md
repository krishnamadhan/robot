# Cosmo Live Status
> Auto-generated: 2026-05-29 06:00:01
> Run `tools/update_docs.sh` to refresh

## System Health
| Metric | Value |
|--------|-------|
| Uptime | 5h 55m |
| CPU Temp | 56.5°C |
| Free RAM | 6044 MB |
| Mood | 0.54 |
| Energy | 0.0 |

## Hardware Components
| Component | Status | Reason |
|-----------|--------|--------|
| camera | ❌ error | failed to open /dev/video0 |
| sensor.bh1750 | ⚠️ mock | hardware not detected |
| sensor.pir | ⚠️ mock | hardware not detected |
| sensor.touch | ✅ real |  |
| sensor.apds9960 | ⚠️ mock | hardware not detected |
| sensor.mpu6050 | ⚠️ mock | hardware not detected |
| sensor.cliff | ⚠️ mock | hardware not detected |
| sensor.ultrasonic | ✅ real |  |
| sensor.sound | ✅ real |  |
| sensor.vibration | ⚠️ mock | hardware not detected |
| sensor.ups | ⚠️ mock | hardware not detected |
| motors | ⚠️ mock | GPIO unavailable or sim=always |
| servos | ⚠️ mock | adafruit-servokit not installed |

**Real components:** sensor.touch, sensor.ultrasonic, sensor.sound
**Mocked:** sensor.bh1750, sensor.pir, sensor.apds9960, sensor.mpu6050, sensor.cliff, sensor.vibration, sensor.ups, motors, servos
**Errors:** camera
