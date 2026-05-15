#!/usr/bin/env python3
"""Battery status check for DFRobot FIT0992 UPS HAT.

Uses MAX17040 fuel gauge at I2C address 0x36 on bus 1.
Registers:
  0x02 VCELL  — pack voltage (bits 15:4 * 1.25mV)
  0x04 SOC    — state of charge (byte0 = %, byte1 / 256 = fraction)
  0x08 VERSION
"""
import smbus2
import sys

BUS     = 1
ADDRESS = 0x36

REG_VCELL   = 0x02
REG_SOC     = 0x04
REG_VERSION = 0x08

def read_word(bus, addr, reg):
    data = bus.read_i2c_block_data(addr, reg, 2)
    return (data[0] << 8) | data[1]

try:
    bus = smbus2.SMBus(BUS)
except Exception as e:
    print(f"Cannot open I2C bus {BUS}: {e}")
    sys.exit(1)

try:
    version = read_word(bus, ADDRESS, REG_VERSION)
    vcell_raw = read_word(bus, ADDRESS, REG_VCELL)
    soc_raw   = read_word(bus, ADDRESS, REG_SOC)
except Exception as e:
    print(f"Cannot read MAX17040 at {hex(ADDRESS)}: {e}")
    print("Check that UPS HAT is seated on GPIO header and I2C is enabled.")
    sys.exit(1)

voltage_mv = (vcell_raw >> 4) * 1.25
voltage_v  = voltage_mv / 1000
soc_pct    = (soc_raw >> 8) + ((soc_raw & 0xFF) / 256)

print(f"DFRobot FIT0992 UPS HAT — Battery Status")
print(f"─────────────────────────────────────────")
print(f"MAX17040 version : {hex(version)}")
print(f"Pack voltage     : {voltage_v:.3f} V")
print(f"Charge level     : {soc_pct:.1f}%")

if soc_pct > 80:
    status = "GOOD"
elif soc_pct > 40:
    status = "OK"
elif soc_pct > 15:
    status = "LOW — consider charging"
else:
    status = "CRITICAL — charge now"

print(f"Status           : {status}")
bus.close()
