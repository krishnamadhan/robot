"""
ESP32 serial bridge — reads JSON sensor data from /dev/ttyUSB0,
publishes to event bus. Accepts motor commands and forwards to ESP32.

All sensor GPIO handling moves to ESP32. Pi only sees parsed events.
"""

import asyncio
import json
import time
import structlog
from typing import Optional, Callable

import serial

from core.capabilities import Capability, CapState, registry
from core.event_bus import bus, Event, EventType, EventPriority

log = structlog.get_logger(__name__)

_SENSOR_CAPS = {
    "ultra": Capability.PROXIMITY,
    "pir": Capability.MOTION_SENSE,
    "cliff": Capability.CLIFF_SENSE,
    "touch": Capability.TOUCH,
    "sound": Capability.SOUND_SENSE,
    "vibe": Capability.VIBRATION_SENSE,
    "imu": Capability.ORIENTATION,
}

_BAUD = 115200
_READ_TIMEOUT = 0.1
_RECONNECT_DELAY = 3.0
_HEARTBEAT_TIMEOUT = 5.0  # declare offline if no HB for this long


class ESP32Bridge:
    def __init__(self, port: str = "/dev/ttyUSB0"):
        self._port = port
        self._serial: Optional[serial.Serial] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._writer_task: Optional[asyncio.Task] = None
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._connected = False
        self._mock = False
        self._last_hb = 0.0
        self._esp_uptime = 0
        self._sensor_flags: dict = {}
        self._on_sensor: Optional[Callable] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> bool:
        try:
            self._serial = serial.Serial(
                self._port, _BAUD, timeout=_READ_TIMEOUT
            )
            self._connected = True
            self._mock = False
            log.info("esp32_bridge.connected", port=self._port)
        except Exception as e:
            log.warning("esp32_bridge.fallback_mock", error=str(e))
            self._connected = False
            self._mock = True

        self._reader_task = asyncio.create_task(self._reader_loop())
        self._writer_task = asyncio.create_task(self._writer_loop())
        if self._mock:
            asyncio.create_task(self._mock_sensor_loop())
        return True

    async def stop(self):
        for t in [self._reader_task, self._writer_task]:
            if t:
                t.cancel()
        if self._serial and self._serial.is_open:
            self._serial.close()
        log.info("esp32_bridge.stopped")

    # ── Status ────────────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        if self._mock:
            return False
        age = time.monotonic() - self._last_hb
        return self._connected and age < _HEARTBEAT_TIMEOUT

    @property
    def is_mock(self) -> bool:
        return self._mock

    def get_status(self) -> dict:
        return {
            "connected": self.is_connected,
            "mock": self._mock,
            "port": self._port,
            "esp_uptime_s": self._esp_uptime,
            "last_hb_age_s": round(time.monotonic() - self._last_hb, 1),
            "sensors": self._sensor_flags,
        }

    # ── Sending commands ──────────────────────────────────────────────────────

    async def send_motor(self, left: float, right: float):
        """Send motor speeds. -1.0 (full back) to +1.0 (full fwd)."""
        await self._send_queue.put({"cmd": "move", "l": round(left, 3), "r": round(right, 3)})

    async def send_stop(self):
        await self._send_queue.put({"cmd": "stop"})

    async def send_stby(self, enabled: bool):
        await self._send_queue.put({"cmd": "stby", "v": 1 if enabled else 0})

    async def ping(self):
        await self._send_queue.put({"cmd": "ping"})

    async def enable_sensor(self, key: str, enabled: bool = True):
        await self._send_queue.put({"cmd": "set_sensor", "key": key, "v": 1 if enabled else 0})

    # ── Internal I/O loops ────────────────────────────────────────────────────

    async def _reader_loop(self):
        loop = asyncio.get_event_loop()
        buf = b""
        while True:
            try:
                if not self._connected or not self._serial:
                    await asyncio.sleep(_RECONNECT_DELAY)
                    await self._try_reconnect()
                    continue

                chunk = await loop.run_in_executor(
                    None, lambda: self._serial.read(256)
                )
                if not chunk:
                    await asyncio.sleep(0.01)
                    continue

                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if line:
                        await self._handle_line(line)

            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("esp32_bridge.read_error", error=str(e))
                self._connected = False
                if not self._mock:
                    for c in (*_SENSOR_CAPS.values(), Capability.LOCOMOTION):
                        if registry.state(c) not in (CapState.ABSENT, CapState.SIMULATED):
                            registry.set_state(c, CapState.FAILED, "bridge disconnected")
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _writer_loop(self):
        loop = asyncio.get_event_loop()
        while True:
            try:
                cmd = await self._send_queue.get()
                if self._mock:
                    continue  # discard in mock mode silently
                if not self._connected or not self._serial:
                    continue
                line = json.dumps(cmd) + "\n"
                await loop.run_in_executor(
                    None, lambda: self._serial.write(line.encode())
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("esp32_bridge.write_error", error=str(e))

    async def _try_reconnect(self):
        try:
            if self._serial:
                self._serial.close()
            self._serial = serial.Serial(self._port, _BAUD, timeout=_READ_TIMEOUT)
            self._connected = True
            log.info("esp32_bridge.reconnected", port=self._port)
        except Exception:
            pass

    # ── Message dispatcher ────────────────────────────────────────────────────

    async def _handle_line(self, raw: bytes):
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            return

        t = msg.get("t")

        if t == "hb":
            self._last_hb = time.monotonic()
            self._esp_uptime = msg.get("up", 0)
            self._sensor_flags = msg.get("sensors", {})
            if not self._mock:
                # Motors live on the ESP32 — its heartbeat is locomotion health
                registry.mark_seen(Capability.LOCOMOTION, "esp32 hb")
            return

        if t == "s":
            await self._dispatch_sensor(msg)
            return

        if t == "ack":
            return  # motor command acks — nothing to do

        if t == "err":
            log.warning("esp32_bridge.esp_error", msg=msg.get("msg"))
            return

        if t == "pong":
            return

    async def _dispatch_sensor(self, msg: dict):
        sensor_id = msg.get("id")
        val = msg.get("v")

        cap = _SENSOR_CAPS.get(sensor_id)
        if cap:
            if self._mock:
                registry.simulate(cap)
            else:
                registry.mark_seen(cap, sensor_id)

        if sensor_id == "ultra":
            dist = float(val)
            await bus.publish(Event(
                type=EventType.DISTANCE_UPDATED,
                data={"distance_cm": dist, "source": "esp32"},
                source="esp32_bridge", priority=EventPriority.LOW,
            ))
            if dist < 20:
                await bus.publish(Event(
                    type=EventType.OBSTACLE_CRITICAL,
                    data={"distance_cm": dist},
                    source="esp32_bridge", priority=EventPriority.SAFETY,
                ))
            elif dist < 40:
                await bus.publish(Event(
                    type=EventType.OBSTACLE_WARNING,
                    data={"distance_cm": dist},
                    source="esp32_bridge", priority=EventPriority.HIGH,
                ))

        elif sensor_id == "pir":
            if int(val) == 1:
                await bus.publish(Event(
                    type=EventType.MOTION_DETECTED,
                    data={"source": "pir"},
                    source="esp32_bridge", priority=EventPriority.HIGH,
                ))

        elif sensor_id == "cliff":
            if int(val) == 0:  # 0 = cliff detected (TCRT5000 LOW = no reflectance)
                await bus.publish(Event(
                    type=EventType.CLIFF_DETECTED,
                    data={"side": msg.get("side"), "source": "cliff_ir"},
                    source="esp32_bridge", priority=EventPriority.SAFETY,
                ))

        elif sensor_id == "touch":
            zones = {0: "head", 1: "back", 2: "belly", 3: "tail"}
            if int(val) == 1:
                await bus.publish(Event(
                    type=EventType.TOUCH_DETECTED,
                    data={"zone": zones.get(msg.get("n", 0), "head"),
                          "source": "esp32"},
                    source="esp32_bridge", priority=EventPriority.HIGH,
                ))

        elif sensor_id == "sound":
            await bus.publish(Event(
                type=EventType.SOUND_DETECTED,
                data={"level": int(val), "source": "ky038"},
                source="esp32_bridge", priority=EventPriority.NORMAL,
            ))

        elif sensor_id == "vibe":
            if int(val) == 1:
                await bus.publish(Event(
                    type=EventType.MOTION_DETECTED,
                    data={"source": "vibration"},
                    source="esp32_bridge", priority=EventPriority.HIGH,
                ))

        elif sensor_id == "imu":
            accel_mag = (msg["ax"]**2 + msg["ay"]**2 + msg["az"]**2) ** 0.5
            await bus.publish(Event(
                type=EventType.DISTANCE_UPDATED,  # reuse until IMU event added
                data={"imu": {k: msg[k] for k in ["ax","ay","az","gx","gy","gz"]},
                      "accel_g": round(accel_mag, 3)},
                source="esp32_bridge", priority=EventPriority.LOW,
            ))
            if accel_mag > 2.5:
                await bus.publish(Event(
                    type=EventType.PICKUP_DETECTED,
                    data={"accel_g": round(accel_mag, 3), "source": "imu"},
                    source="esp32_bridge", priority=EventPriority.SAFETY,
                ))

    # ── Mock sensor loop (when ESP32 not connected) ───────────────────────────

    async def _mock_sensor_loop(self):
        import random
        tick = 0
        while True:
            tick += 1
            self._last_hb = time.monotonic()

            # Mock ultrasonic
            dist = 80.0 + random.gauss(0, 2)
            await self._dispatch_sensor({"id": "ultra", "v": dist})

            # Mock IMU (stationary)
            await self._dispatch_sensor({
                "id": "imu",
                "ax": round(random.gauss(0, 0.02), 3),
                "ay": round(random.gauss(0, 0.02), 3),
                "az": round(9.81 + random.gauss(0, 0.05), 3),
                "gx": 0.0, "gy": 0.0, "gz": 0.0,
            })

            # Occasional mock PIR
            if random.random() < 0.002:
                await self._dispatch_sensor({"id": "pir", "v": 1})

            await asyncio.sleep(0.2)


# Singleton
bridge = ESP32Bridge()
