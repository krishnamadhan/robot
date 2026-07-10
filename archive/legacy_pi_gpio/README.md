# ⚠️ LEGACY PI-GPIO SCRIPTS — DO NOT RUN ⚠️

Archived 2026-07-10 (AB-013, closes KI-ROBOT-CTRL-DEPRECATED).

Every script in this directory predates the ESP32-S3 migration and drives
**Raspberry Pi GPIO pins directly**, including **GPIO6 and GPIO16 — reserved
for the FIT0992 UPS HAT. Driving those pins has burned 5 chips.**

These files are kept ONLY as pinout/wiring reference for the rewire session
(motors → ESP32 GPIO 15–21). They are not imported by anything.

Sanctioned GPIO code lives ONLY in:
- `hardware/` (drivers, ESP32 bridge)
- `battery_monitor.py` (owns the UPS HAT — legitimately)

For motor/sensor testing use `tools/motor_test.py` and the other `tools/*`
test utilities that go through `hardware/esp32_bridge.py`.
