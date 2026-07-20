# CLAUDE.md — Cosmo Robot

**Project state lives in `docs/STATE.md`. Read it first — it is the only source of truth.**

## Session Protocol

**START:** `cat docs/STATE.md && cat COSMO_BACKLOG.md && git log --oneline -5 && pm2 status`
Take the top unchecked item from COSMO_BACKLOG.md. State which task you're taking before coding.

**MCP tools available in this session:**
- **Graphify** — AST knowledge graph of this codebase is pre-built at `graphify-out/graph.json`.
  Before editing any module, ask the graph first: "What calls X?", "Which files import Y?", "Trace the path from Z to the API".
  Refresh after major merges: `graphify /home/pi/robot --update` (incremental, ~10s).
- **Context7** — for any external library (picamera2, asyncio, py_trees, onnxruntime, aiosqlite, etc.)
  append `use context7` to get version-exact docs: *"how does py_trees Behaviour.update() work? use context7"*

**END:** Update `docs/STATE.md` (component status + Next Priority). If bugs found → `docs/KNOWN_ISSUES.md`. If architecture changed → `docs/DECISIONS.md`. Sync and commit are automated by hooks.

**Don't restart `banteragent` without reason** (corrected 2026-07-20: auth survives restarts; avoid gratuitous ones — drops in-flight messages).

## Project Layout

```
~/robot/
├── CLAUDE.md, COSMO_BACKLOG.md
├── docs/  STATE.md · KNOWN_ISSUES.md · DECISIONS.md · CHANGELOG.md
├── core/  event_bus.py · personality.py · behavior_tree.py · action_router.py · capabilities.py · memory/
├── cognition/  mind.py · llm.py · conversation.py
├── perception/vision/  person.py(yolo11n) · face.py(SFace) · emotion.py
├── perception/audio/  wake_word.py · vad.py · stt.py · pipeline.py
├── behavior/  ambilight.py · exploration.py · navigation.py
├── expression/  eyes.py(SSD1306) · speech.py · sounds.py · idle_motion.py
├── hardware/  esp32_bridge.py · motors.py · sensor_manager.py · led_strip.py · wipro_light.py · i2c_bus.py
├── esp32/  main.py(MicroPython firmware) · driver_tb6612.py
├── config/  hardware.yaml · personality.yaml
└── tools/  cosmo_demo.py(PM2 entry) · esp32_test.py · …
```

Key: `hardware/esp32_bridge.py` = serial JSON bridge to ESP32 at `/dev/ttyUSB0` 115200 baud. `motors.py` sends JSON to bridge; no Pi GPIO. `sensor_manager.py` handles only BH1750 + UPS HAT directly on Pi I2C.

## Coding Standards

- Python 3.13, asyncio throughout — no threading
- `PYTHONPATH=/home/pi/robot` system python — no global pip install
- structlog JSON logging — no bare print()
- No hardcoded GPIO pins — read from `config/hardware.yaml`
- Every new hardware module: `tools/<module>_test.py` with Rich UI

## ESP32-S3 Pin Map (canonical — all sensors + motors on ESP32, not Pi GPIO)

```
Motors   AIN1=15  AIN2=16  PWMA=17   (left)
         BIN1=18  BIN2=19  PWMB=20   (right)   STBY=21
I2C      SDA=8    SCL=9    (MPU-6050@0x68, APDS9960@0x39)
HC-SR04  TRIG=10  ECHO=11  (ECHO via 2kΩ/1kΩ divider — 5V→3.3V)
PIR      12   Cliff L=13 R=14   Touch 1/2/3/4 (head/back/belly/tail)
Sound    5(ADC1)   Vibe=6
```

Enable a sensor: set `SENSORS["<id>"] = True` in `esp32/main.py`, deploy, run `python3 tools/esp32_test.py`.

## Pi I2C Bus (GPIO2/GPIO3)

`0x23` BH1750 (enabled) · `0x36` UPS HAT (enabled) · `0x3C` OLED left · `0x3D` OLED right (A0 bridged) · `0x40` PCA9685 (Phase 3)

## Motor Truth Table — violating destroys TB6612FNG

```
AIN1=1 AIN2=0  → Forward    AIN1=0 AIN2=1  → Backward
AIN1=0 AIN2=0  → Brake      AIN1=1 AIN2=1  → ❌ PROHIBITED
STBY LOW at boot; raise HIGH only after sensor self-test.
Set OFF pin LOW before ON pin HIGH.
```

## Power Safety

1. LiPo 7.4V → TB6612FNG VM only — **never to Pi 5V rail**
2. **Pi GPIO6 + GPIO16 = FIT0992 HAT reserved** — NEVER drive (burned 5 chips)
3. All Pi GPIO = 3.3V max — no exceptions
4. 470µF across TB6612FNG VM+GND; 220µF per motor terminal before first LiPo run

## Do Not Ever

- Don't restart `banteragent` PM2 gratuitously (auth survives, but in-flight messages drop)
- Never set AIN1=1 AND AIN2=1 — destroys TB6612FNG
- Never connect LiPo to Pi 5V rail — destroys Pi 5
- Never load model >500MB into RAM
- Never stream video frames to external API
- Never make Claude API calls in a loop (100K token daily hard limit)
- Never repeat an approach marked FAILED in `docs/KNOWN_ISSUES.md`
