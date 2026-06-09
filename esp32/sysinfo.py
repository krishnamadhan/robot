import sys, os, gc, esp, machine, network, time

print("=" * 40)
print("ESP32-S3 SYSTEM INFO")
print("=" * 40)

print("MicroPython :", sys.version)
print("Machine     :", sys.platform)

# Flash & RAM
flash = esp.flash_size()
gc.collect()
free_ram = gc.mem_free()
alloc_ram = gc.mem_alloc()
total_ram = free_ram + alloc_ram
print("Flash       : " + str(flash // 1024) + "KB (" + str(flash // (1024*1024)) + "MB)")
print("RAM free    : " + str(free_ram // 1024) + "KB / " + str(total_ram // 1024) + "KB")

# CPU freq
print("CPU freq    : " + str(machine.freq() // 1000000) + "MHz")

# Unique ID / MAC base
uid = machine.unique_id()
mac_str = ":".join("{:02x}".format(b) for b in uid)
print("Chip ID     : " + mac_str)

# WiFi MAC
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
mac_bytes = wlan.config('mac')
mac_wifi = ":".join("{:02x}".format(b) for b in mac_bytes)
print("WiFi MAC    : " + mac_wifi)

# Filesystem
stat = os.statvfs('/')
fs_total = stat[0] * stat[2]
fs_free  = stat[0] * stat[3]
print("Filesystem  : " + str(fs_free // 1024) + "KB free / " + str(fs_total // 1024) + "KB total")

# I2C scan
from machine import I2C, Pin
print("\nI2C scan (SDA=GPIO8, SCL=GPIO9):")
try:
    i2c = I2C(0, sda=Pin(8), scl=Pin(9), freq=100000)
    devices = i2c.scan()
    if devices:
        for d in devices:
            print(f"  0x{d:02X}")
    else:
        print("  (no devices found)")
except Exception as e:
    print("  I2C error:", e)

print("=" * 40)
print("READY")
