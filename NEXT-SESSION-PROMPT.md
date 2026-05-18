## Next Session — Wire the OLED Eyes

Hardware arrived and ready to wire:
- Left eye:  SSD1306 1.3" OLED → I2C 0x3C
- Right eye: SSD1306 1.3" OLED → I2C 0x3D (solder A0 pad first)

Wiring steps:
1. Solder A0 pad on RIGHT OLED PCB (changes address 0x3C → 0x3D)
2. VCC → Pi Pin 1 (3.3V)
3. GND → Pi Pin 6
4. SDA → Pi Pin 3 (GPIO2)
5. SCL → Pi Pin 5 (GPIO3)
6. Verify: sudo i2cdetect -y 1 → must show 0x3C AND 0x3D

Software steps:
1. In config/hardware.yaml set eye_engine.backend: "oled"
   (currently "terminal")
2. In expression/eyes.py the SSD1306 driver is already written
   just needs the backend switch
3. Run: python3 tools/eye_test.py → should animate on both OLEDs
4. Run cosmo_demo — eyes now show expressions physically

After eyes: wire sensors one at a time (hardware.yaml available: false → true)
Priority order: PIR → touch sensors → MPU6050 → BH1750 → HC-SR04
