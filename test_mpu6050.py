#!/usr/bin/env python3
"""MPU-6050 gyro/accelerometer test — I2C 0x68, 4 wires (VCC→3.3V, GND, SDA→GPIO2, SCL→GPIO3)"""

import smbus2
import time

MPU_ADDR = 0x68
PWR_MGMT_1 = 0x6B
ACCEL_XOUT_H = 0x3B
GYRO_XOUT_H  = 0x43

def read_word_signed(bus, reg):
    high = bus.read_byte_data(MPU_ADDR, reg)
    low  = bus.read_byte_data(MPU_ADDR, reg + 1)
    val  = (high << 8) | low
    return val - 65536 if val > 32767 else val

def main():
    bus = smbus2.SMBus(1)
    # Wake the MPU-6050 (it starts in sleep mode)
    bus.write_byte_data(MPU_ADDR, PWR_MGMT_1, 0)
    time.sleep(0.1)

    who = bus.read_byte_data(MPU_ADDR, 0x75)
    if who != 0x68:
        print(f"ERROR: WHO_AM_I returned 0x{who:02X}, expected 0x68. Check wiring.")
        return

    print("MPU-6050 detected OK (WHO_AM_I = 0x68)")
    print("Press Ctrl+C to stop\n")
    print(f"{'AX':>8} {'AY':>8} {'AZ':>8}   {'GX':>8} {'GY':>8} {'GZ':>8}")

    try:
        while True:
            ax = read_word_signed(bus, ACCEL_XOUT_H)
            ay = read_word_signed(bus, ACCEL_XOUT_H + 2)
            az = read_word_signed(bus, ACCEL_XOUT_H + 4)
            gx = read_word_signed(bus, GYRO_XOUT_H)
            gy = read_word_signed(bus, GYRO_XOUT_H + 2)
            gz = read_word_signed(bus, GYRO_XOUT_H + 4)
            print(f"{ax:>8} {ay:>8} {az:>8}   {gx:>8} {gy:>8} {gz:>8}", end='\r')
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nDone.")

if __name__ == '__main__':
    main()
