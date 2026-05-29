# Battery Fix State

**Date:** 2026-05-29  
**Issue:** "I2C sensor unavailable / Last known: ?% (?V)" in WhatsApp battery command

## Root Cause

Pi 5 uses the RP1 peripheral chip whose I2C driver returns `EAGAIN` (errno=11) when a second
process tries `ioctl` on `/dev/i2c-1` while any other open file descriptor holds an active
transaction. `cosmo` (PID 1228) opened 4 separate `smbus.SMBus(1)` instances — one per sensor
class (BH1750, APDS9960, MPU6050, UPS HAT). `battery_monitor` (separate PM2 process) could
never get a clean window and logged EAGAIN every 60 s for hours.

## Diagnostics Run

| Check | Result |
|-------|--------|
| `i2cdetect -y 1` | EAGAIN on all addresses |
| `lsof /dev/i2c-1` | PID 1228 (cosmo): 4 open FDs |
| `battery-monitor.log` | `[Errno 11] Resource temporarily unavailable` every 60 s |
| bus 13/14 scan | False positives (RP1 internal buses — ignored) |
| MAX17040 byte math | VCELL scaling was correct in battery_monitor.py |

## Fix Applied (2026-05-29)

### 1. `hardware/sensor_manager.py` — shared I2C singleton
- Added `_i2c_bus_1` / `_i2c_lock_1` module-level singletons
- All sensor classes (BH1750, APDS9960, MPU6050, UPS HAT) now call `_i2c_bus()` instead of
  opening their own `smbus.SMBus(1)` — reduces FD count from 4 → 1
- asyncio.Lock serializes concurrent reads within cosmo process
- Failed read returns `{"percent": None, "voltage": None, "charging": False}` (not fake 50%)

### 2. `services/api/service.py` — `GET /battery` endpoint
- Exposes `sensor_manager.get_battery()` at `http://localhost:8000/battery`
- battery_monitor reads this endpoint as primary path

### 3. `battery_monitor.py` — API-first read
- `read_battery()` now tries `http://localhost:8000/battery` (timeout=3s) first
- Falls back to direct smbus2 only if cosmo API is unreachable
- Eliminates the EAGAIN race entirely while cosmo is running

### 4. `hardware/pin_registry.py` — UPS pins reserved
- `register_from_config()` pre-claims GPIO6 (ups_hat.ac_detect) and GPIO16 (ups_hat.charge_ctrl)
  before any motor/sensor pins are loaded
- Prevents any future motor or sensor driver from silently reclaiming these pins

## Physical Blockers (Madhan to action)

| Item | Description |
|------|-------------|
| BIN2 jumper | Move from GPIO6 → GPIO10 pin header. Config already reflects GPIO10. |
| Ultrasonic TRIG | Currently GPIO16 (disabled). Move to free pin when enabling HC-SR04. |

## Verification Steps

After `pm2 restart cosmo`:
```bash
# Should see battery readings, no EAGAIN
tail -f /home/pi/logs/battery-monitor.log

# API spot check
curl http://localhost:8000/battery
```

Expected: `{"percent": <N>, "voltage": <V>, "charging": <bool>, "source": "sensor_manager"}`
