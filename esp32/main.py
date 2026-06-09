"""
Cosmo ESP32-S3 firmware — sensor co-processor + motor controller
Protocol: newline-delimited JSON over USB serial (115200 baud)

Pi → ESP32  {"cmd":"move","l":0.6,"r":0.6}   speeds -1.0..1.0
            {"cmd":"stop"}
            {"cmd":"stby","v":1}              enable/disable motors
            {"cmd":"ping"}

ESP32 → Pi  {"t":"s","id":"ultra","v":23.4}
            {"t":"s","id":"pir","v":1}
            {"t":"s","id":"cliff","side":"l","v":0}
            {"t":"s","id":"touch","n":0,"v":1}
            {"t":"s","id":"sound","v":512}
            {"t":"s","id":"vibe","v":1}
            {"t":"s","id":"imu","ax":0,"ay":0,"az":9.8,"gx":0,"gy":0,"gz":0}
            {"t":"ack","cmd":"move","ok":true}
            {"t":"hb","up":12345}

Pin map (all 3.3V logic):
  Motors  AIN1=GPIO15  AIN2=GPIO16  PWMA=GPIO17  (left)
          BIN1=GPIO18  BIN2=GPIO19  PWMB=GPIO20  (right)
          STBY=GPIO21
  I2C     SDA=GPIO8    SCL=GPIO9    (MPU-6050 @ 0x68, APDS9960 @ 0x39)
  HC-SR04 TRIG=GPIO10  ECHO=GPIO11  (ECHO via 2kΩ/1kΩ divider — 5V→3.3V)
  PIR     GPIO12
  Cliff   L=GPIO13     R=GPIO14
  Touch   GPIO1 GPIO2 GPIO3 GPIO4
  Sound   GPIO5  (ADC1 — analog)
  Vibrate GPIO6
"""

import uasyncio as asyncio
import ujson as json
import utime
import sys
from machine import Pin, ADC, I2C

# ── Feature flags: set True only when sensor is physically wired ──────────────
SENSORS = {
    "ultra":  False,   # HC-SR04 ultrasonic
    "pir":    False,   # HC-SR501 PIR
    "cliff":  False,   # TCRT5000 cliff × 2
    "touch":  False,   # TTP223 touch × 4
    "sound":  False,   # KY-038 analog sound
    "vibe":   False,   # SW-420 vibration
    "imu":    False,   # MPU-6050 via I2C
    "motors": False,   # TB6612FNG motor driver
}

# ── Output queue (send to Pi) ─────────────────────────────────────────────────
_outq = []

def _send(obj):
    _outq.append(json.dumps(obj) + "\n")

# ── Motor driver ──────────────────────────────────────────────────────────────
_motors = None
if SENSORS["motors"]:
    from driver_tb6612 import DualMotor
    _motors = DualMotor()

_motor_cmd = {"l": 0.0, "r": 0.0}
_last_motor_cmd_ms = utime.ticks_ms()
_MOTOR_WATCHDOG_MS = 1000  # stop motors if no command for 1s

# ── Sensor hardware init ──────────────────────────────────────────────────────
if SENSORS["ultra"]:
    from machine import time_pulse_us
    _ultra_trig = Pin(10, Pin.OUT)
    _ultra_echo = Pin(11, Pin.IN)

if SENSORS["pir"]:
    _pir = Pin(12, Pin.IN)
    _pir_last = 0

if SENSORS["cliff"]:
    _cliff_l = Pin(13, Pin.IN, Pin.PULL_UP)
    _cliff_r = Pin(14, Pin.IN, Pin.PULL_UP)
    _cliff_last = [1, 1]

if SENSORS["touch"]:
    _touch_pins = [Pin(p, Pin.IN) for p in [1, 2, 3, 4]]
    _touch_last = [0, 0, 0, 0]

if SENSORS["sound"]:
    _sound_adc = ADC(Pin(5), atten=ADC.ATTN_11DB)
    _SOUND_THRESHOLD = 2000  # 0-4095

if SENSORS["vibe"]:
    _vibe = Pin(6, Pin.IN)
    _vibe_last = 0

if SENSORS["imu"]:
    _i2c = I2C(0, sda=Pin(8), scl=Pin(9), freq=400000)
    _MPU_ADDR = 0x68
    # Wake MPU-6050
    _i2c.writeto_mem(_MPU_ADDR, 0x6B, b'\x00')

# ── Sensor read functions ─────────────────────────────────────────────────────

def _read_ultrasonic():
    from machine import time_pulse_us
    _ultra_trig.value(0)
    utime.sleep_us(2)
    _ultra_trig.value(1)
    utime.sleep_us(10)
    _ultra_trig.value(0)
    duration = time_pulse_us(_ultra_echo, 1, 30000)
    if duration < 0:
        return None
    return round(duration / 58.0, 1)

def _read_imu():
    raw = _i2c.readfrom_mem(_MPU_ADDR, 0x3B, 14)
    def s16(hi, lo):
        v = (hi << 8) | lo
        return v - 65536 if v > 32767 else v
    ax = s16(raw[0], raw[1]) / 16384.0
    ay = s16(raw[2], raw[3]) / 16384.0
    az = s16(raw[4], raw[5]) / 16384.0
    gx = s16(raw[8], raw[9]) / 131.0
    gy = s16(raw[10], raw[11]) / 131.0
    gz = s16(raw[12], raw[13]) / 131.0
    return {"ax": round(ax,3), "ay": round(ay,3), "az": round(az,3),
            "gx": round(gx,2), "gy": round(gy,2), "gz": round(gz,2)}

# ── Mock sensor data (when sensor not wired) ──────────────────────────────────

_mock_tick = 0

def _mock_sensors():
    global _mock_tick
    _mock_tick += 1
    data = []
    data.append({"t":"s","id":"ultra","v": 80.0 + (_mock_tick % 20)})
    data.append({"t":"s","id":"pir","v": 0})
    data.append({"t":"s","id":"cliff","side":"l","v": 0})
    data.append({"t":"s","id":"cliff","side":"r","v": 0})
    data.append({"t":"s","id":"sound","v": 100 + (_mock_tick % 50)})
    data.append({"t":"s","id":"vibe","v": 0})
    data.append({"t":"s","id":"imu",
                 "ax": 0.0, "ay": 0.0, "az": 9.81,
                 "gx": 0.0, "gy": 0.0, "gz": 0.0})
    return data

# ── Async tasks ───────────────────────────────────────────────────────────────

async def sensor_task():
    while True:
        try:
            if not any(SENSORS[k] for k in ["ultra","pir","cliff","touch","sound","vibe","imu"]):
                # All sensors mocked
                for item in _mock_sensors():
                    _send(item)
                await asyncio.sleep_ms(200)
                continue

            if SENSORS["ultra"]:
                d = _read_ultrasonic()
                if d is not None:
                    _send({"t":"s","id":"ultra","v": d})

            if SENSORS["pir"]:
                global _pir_last
                v = _pir.value()
                if v != _pir_last:
                    _send({"t":"s","id":"pir","v": v})
                    _pir_last = v

            if SENSORS["cliff"]:
                for i, (pin, last) in enumerate(zip([_cliff_l, _cliff_r], _cliff_last)):
                    v = pin.value()
                    if v != last:
                        _send({"t":"s","id":"cliff","side":"lr"[i],"v": v})
                        _cliff_last[i] = v

            if SENSORS["touch"]:
                for i, pin in enumerate(_touch_pins):
                    v = pin.value()
                    if v != _touch_last[i]:
                        _send({"t":"s","id":"touch","n": i,"v": v})
                        _touch_last[i] = v

            if SENSORS["sound"]:
                v = _sound_adc.read_u16() >> 4  # 16→12 bit
                if v > _SOUND_THRESHOLD:
                    _send({"t":"s","id":"sound","v": v})

            if SENSORS["vibe"]:
                global _vibe_last
                v = _vibe.value()
                if v != _vibe_last:
                    _send({"t":"s","id":"vibe","v": v})
                    _vibe_last = v

            if SENSORS["imu"]:
                _send({"t":"s","id":"imu"} | _read_imu())

        except Exception as e:
            _send({"t":"err","msg": str(e)})

        await asyncio.sleep_ms(100)


async def motor_watchdog_task():
    while True:
        await asyncio.sleep_ms(200)
        if SENSORS["motors"] and _motors and _motors._enabled:
            age = utime.ticks_diff(utime.ticks_ms(), _last_motor_cmd_ms)
            if age > _MOTOR_WATCHDOG_MS:
                _motors.stop()


async def command_reader_task():
    global _motor_cmd, _last_motor_cmd_ms
    reader = asyncio.StreamReader(sys.stdin)
    while True:
        try:
            line = await reader.readline()
            if not line:
                continue
            cmd = json.loads(line.strip())
            c = cmd.get("cmd","")

            if c == "move":
                _motor_cmd["l"] = float(cmd.get("l", 0))
                _motor_cmd["r"] = float(cmd.get("r", 0))
                _last_motor_cmd_ms = utime.ticks_ms()
                if SENSORS["motors"] and _motors:
                    _motors.set(_motor_cmd["l"], _motor_cmd["r"])
                _send({"t":"ack","cmd":"move","ok": True})

            elif c == "stop":
                _motor_cmd["l"] = 0.0
                _motor_cmd["r"] = 0.0
                if SENSORS["motors"] and _motors:
                    _motors.stop()
                _send({"t":"ack","cmd":"stop","ok": True})

            elif c == "stby":
                v = bool(cmd.get("v", 0))
                if SENSORS["motors"] and _motors:
                    _motors.enable() if v else _motors.disable()
                _send({"t":"ack","cmd":"stby","ok": True})

            elif c == "ping":
                _send({"t":"pong","up": utime.ticks_ms() // 1000})

            elif c == "set_sensor":
                key = cmd.get("key")
                val = bool(cmd.get("v", False))
                if key in SENSORS:
                    SENSORS[key] = val
                _send({"t":"ack","cmd":"set_sensor","key": key,"ok": True})

        except Exception as e:
            _send({"t":"err","msg": str(e)})


async def serial_writer_task():
    writer = asyncio.StreamWriter(sys.stdout, {})
    while True:
        if _outq:
            line = _outq.pop(0)
            writer.write(line.encode())
            await writer.drain()
        else:
            await asyncio.sleep_ms(10)


async def heartbeat_task():
    while True:
        _send({"t":"hb","up": utime.ticks_ms() // 1000,
               "sensors": {k: v for k, v in SENSORS.items()}})
        await asyncio.sleep_ms(1000)


async def main():
    await asyncio.gather(
        sensor_task(),
        motor_watchdog_task(),
        command_reader_task(),
        serial_writer_task(),
        heartbeat_task(),
    )

asyncio.run(main())
