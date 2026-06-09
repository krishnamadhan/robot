from machine import Pin, PWM

_PWM_FREQ = 1000
_MAX_DUTY = 65535  # MicroPython PWM uses 16-bit

class TB6612:
    """Single-channel TB6612FNG driver (one motor)."""

    def __init__(self, in1: int, in2: int, pwm_pin: int):
        self._in1 = Pin(in1, Pin.OUT)
        self._in2 = Pin(in2, Pin.OUT)
        self._pwm = PWM(Pin(pwm_pin), freq=_PWM_FREQ, duty_u16=0)
        self.brake()

    def forward(self, speed: float):
        """speed: 0.0 – 1.0"""
        self._in1.value(1)
        self._in2.value(0)
        self._pwm.duty_u16(int(min(speed, 1.0) * _MAX_DUTY))

    def backward(self, speed: float):
        """speed: 0.0 – 1.0"""
        self._in1.value(0)
        self._in2.value(1)
        self._pwm.duty_u16(int(min(speed, 1.0) * _MAX_DUTY))

    def brake(self):
        self._in1.value(0)
        self._in2.value(0)
        self._pwm.duty_u16(0)

    def set_speed(self, speed: float):
        """-1.0 (full back) … +1.0 (full forward)"""
        if speed > 0:
            self.forward(speed)
        elif speed < 0:
            self.backward(-speed)
        else:
            self.brake()


class DualMotor:
    """Dual TB6612FNG wrapper for left+right drive."""

    # ESP32-S3 pin assignments — matches wiring doc
    PIN_AIN1 = 15
    PIN_AIN2 = 16
    PIN_PWMA = 17   # left
    PIN_BIN1 = 18
    PIN_BIN2 = 19
    PIN_PWMB = 20   # right
    PIN_STBY = 21

    def __init__(self):
        self._stby = Pin(self.PIN_STBY, Pin.OUT)
        self._stby.value(0)  # disabled at boot — safety
        self._left  = TB6612(self.PIN_AIN1, self.PIN_AIN2, self.PIN_PWMA)
        self._right = TB6612(self.PIN_BIN1, self.PIN_BIN2, self.PIN_PWMB)
        self._enabled = False

    def enable(self):
        self._stby.value(1)
        self._enabled = True

    def disable(self):
        self.stop()
        self._stby.value(0)
        self._enabled = False

    def set(self, left: float, right: float):
        """-1.0 to +1.0 for each side. Auto-enables STBY."""
        if not self._enabled:
            self.enable()
        self._left.set_speed(left)
        self._right.set_speed(right)

    def stop(self):
        self._left.brake()
        self._right.brake()
