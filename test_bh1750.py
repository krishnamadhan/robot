from smbus2 import SMBus, i2c_msg
import time

ADDR = 0x23
bus = SMBus(1)

# Power on + reset
bus.write_byte(ADDR, 0x01)
time.sleep(0.1)
bus.write_byte(ADDR, 0x07)  # Reset data register
time.sleep(0.1)

def read_lux():
    bus.write_byte(ADDR, 0x20)  # One-time high-res measurement
    time.sleep(0.18)
    msg = i2c_msg.read(ADDR, 2)
    bus.i2c_rdwr(msg)
    raw = list(msg)
    return (raw[0] << 8 | raw[1]) / 1.2

readings = []
print('BH1750 Light Sensor Test — 10 readings')
print('Cover with hand for first 5, shine torch for last 5')
print('-' * 45)

for i in range(10):
    lux = read_lux()
    readings.append(lux)
    bar = '#' * int(min(lux, 1000) / 50)
    print(f'Reading {i+1:2d}: {lux:8.1f} lux  {bar}')
    time.sleep(0.82)  # total ~1s per reading

bus.close()
print('-' * 45)
print(f'MIN: {min(readings):.1f} lux')
print(f'MAX: {max(readings):.1f} lux')
print(f'RANGE: {max(readings) - min(readings):.1f} lux')
print('BH1750 PASS ✓' if max(readings) - min(readings) > 5 else 'WARNING: cover then shine the sensor to test range')
