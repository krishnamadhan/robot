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
            {"t":"ack","cmd":"move","ok":false,"why":"cliff"}   (reflex hold)
            {"t":"hb","up":12345}

Safety: cliff pins fire Pin.irq → motors brake locally (no Pi round-trip);
move commands are refused while a cliff is active or within 500ms of one.

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
# Bounded (OQ-2): if the Pi stalls, evict oldest non-critical entries instead
# of growing without bound. Heartbeats and cliff events are never evicted.
_OUTQ_MAX = 100
_outq = []   # list of (line, critical)

def _send(obj, critical=False):
    if len(_outq) >= _OUTQ_MAX:
        for i in range(len(_outq)):
            if not _outq[i][1]:
                _outq.pop(i)
                break
        else:
            _outq.pop(0)
    _outq.append((json.dumps(obj) + "\n", critical))

# ── Motor driver ──────────────────────────────────────────────────────────────
_motors = None
if SENSORS["motors"]:
    from driver_tb6612 import DualMotor
    _motors = DualMotor()

_motor_cmd = {"l": 0.0, "r": 0.0}
_last_motor_cmd_ms = utime.ticks_ms()
_MOTOR_WATCHDOG_MS = 1000  # stop motors if no command for 1s

# ── Cliff reflex (OQ-1) ──────────────────────────────────────────────────────
# Local Pin.irq stop — no Pi round-trip. v=0 (LOW, no reflectance) = cliff.
_CLIFF_HOLD_MS = 500       # refuse move commands this long after a cliff edge
_cliff_reflex_until = 0

def _cliff_active():
    if utime.ticks_diff(_cliff_reflex_until, utime.ticks_ms()) > 0:
        return True
    if SENSORS["cliff"]:
        return _cliff_l.value() == 0 or _cliff_r.value() == 0
    return False

# ── Sensor hardware init ──────────────────────────────────────────────────────
if SENSORS["ultra"]:
    from machine import time_pulse_us
    _ultra_trig = Pin(10, Pin.OUT)
    _ultra_echo = Pin(11, Pin.IN)

if SENSORS["pir"]:
    _pir = Pin(12, Pin.IN)
    _pir.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING,
             handler=lambda p: _send({"t":"s","id":"pir","v": p.value()}))

if SENSORS["cliff"]:
    def _cliff_irq(pin):
        global _cliff_reflex_until
        v = pin.value()
        if v == 0:  # cliff! stop right here, don't wait for the Pi
            if _motors:
                _motors.stop()
            _cliff_reflex_until = utime.ticks_add(utime.ticks_ms(), _CLIFF_HOLD_MS)
        side = "l" if pin is _cliff_l else "r"
        _send({"t":"s","id":"cliff","side": side,"v": v}, critical=True)

    _cliff_l = Pin(13, Pin.IN, Pin.PULL_UP)
    _cliff_r = Pin(14, Pin.IN, Pin.PULL_UP)
    _cliff_l.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=_cliff_irq)
    _cliff_r.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=_cliff_irq)

if SENSORS["touch"]:
    _touch_pins = [Pin(p, Pin.IN) for p in [1, 2, 3, 4]]
    for _i, _tp in enumerate(_touch_pins):
        _tp.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING,
                handler=lambda p, n=_i: _send({"t":"s","id":"touch","n": n,"v": p.value()}))

if SENSORS["sound"]:
    _sound_adc = ADC(Pin(5), atten=ADC.ATTN_11DB)
    _SOUND_THRESHOLD = 2000  # 0-4095

if SENSORS["vibe"]:
    # SW-420 chatters — rising edges only, min 50ms apart
    _vibe_last_ms = 0
    def _vibe_irq(p):
        global _vibe_last_ms
        now = utime.ticks_ms()
        if utime.ticks_diff(now, _vibe_last_ms) > 50:
            _vibe_last_ms = now
            _send({"t":"s","id":"vibe","v": 1})
    _vibe = Pin(6, Pin.IN)
    _vibe.irq(trigger=Pin.IRQ_RISING, handler=_vibe_irq)

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
    # v=1 (reflectance HIGH) = surface present; v=0 would mean cliff
    data.append({"t":"s","id":"cliff","side":"l","v": 1})
    data.append({"t":"s","id":"cliff","side":"r","v": 1})
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

            # pir/cliff/touch/vibe are IRQ-driven — no polling here

            if SENSORS["sound"]:
                v = _sound_adc.read_u16() >> 4  # 16→12 bit
                if v > _SOUND_THRESHOLD:
                    _send({"t":"s","id":"sound","v": v})

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
                if _cliff_active():
                    _motor_cmd["l"] = 0.0
                    _motor_cmd["r"] = 0.0
                    if SENSORS["motors"] and _motors:
                        _motors.stop()
                    _send({"t":"ack","cmd":"move","ok": False,"why":"cliff"})
                else:
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
            line, _ = _outq.pop(0)
            writer.write(line.encode())
            await writer.drain()
        else:
            await asyncio.sleep_ms(10)


async def heartbeat_task():
    while True:
        _send({"t":"hb","up": utime.ticks_ms() // 1000,
               "sensors": {k: v for k, v in SENSORS.items()}}, critical=True)
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
