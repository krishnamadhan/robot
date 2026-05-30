# FIX_BATTERY_MONITOR.md — Self-Driving Fix for FIT0992 UPS Battery Reads

> Paste into a Claude Code session. Work iteratively: diagnose with real commands FIRST,
> fix smallest-thing-first, prove each fix with actual output, checkpoint. Never claim
> fixed without showing the live reading. Hardware reference: DFRobot FIT0992
> (suptronics x120x design) — fuel gauge MAX17040-family @ I2C 0x36 on bus 1.

-----

## 0. SYMPTOM

Admin WhatsApp "battery" command returns:

```
[Monitor] Battery
⚠️ I2C sensor unavailable
Last known: ?% (?V)
AC: Connected | Charging: ON
```

AC + Charging read correctly (GPIO lines); the **I2C fuel-gauge read at 0x36 fails** and
falls into a catch-all that emits "unavailable" + "?".

## 1. CONFIRMED HARDWARE FACTS (from FIT0992 wiki — treat as ground truth)

The UPS HAT **hardwires** these and they CANNOT be reassigned in software:

- **GPIO2 (pin 3) = I2C SDA**, **GPIO3 (pin 5) = I2C SCL** — fuel gauge bus (I2C-1).
- **GPIO6 (pin 31) = AC-loss / adapter-failure detect** (board drives it; High = OK, Low = fail).
- **GPIO16 (pin 36) = charging control** (Pi drives it; High = charging disabled, Low = enabled).
- Fuel gauge address **0x36**; VCELL register 0x02, SOC register 0x04 (MAX17040 semantics).

## 2. TWO PIN CONFLICTS TO FIX (root-cause fragility, do these too)

- **C1 — GPIO6 contention (Critical, electrical):** right-motor **BIN2 is wired to GPIO6**,
  which the UPS board already drives for AC-detect. Two drivers on one line. **Rewire BIN2
  to a free BCM pin** (candidates: GPIO5, GPIO12, GPIO25, GPIO26 — verify free via the pin
  registry / `config/hardware.yaml`), update `config/hardware.yaml`, and physically move the
  jumper. AC-detect stays on GPIO6 (cannot move).
- **C2 — GPIO16 collision (High):** HC-SR04 **TRIG is on GPIO16**, which the UPS uses for
  charging enable/disable — every ranging ping toggles charging. **Move TRIG to a free pin**
  (e.g. GPIO12/25/26), update config + wiring.

After both: add/extend `hardware/pin_registry.py` so GPIO6 and GPIO16 are reserved as
UPS-owned and boot fails loudly if anything else claims them.

## 3. DIAGNOSE THE I2C READ (do in order, record real output each step)

1. `sudo raspi-config nonint get_i2c` (0 = enabled) and `grep -i i2c /boot/firmware/config.txt`
   — confirm `dtparam=i2c_arm=on`.
1. `ls -l /dev/i2c-*` — bus present?
1. `i2cdetect -y 1` — **is `0x36` shown?** Also note every other address (BH1750 0x23, etc.)
   to map bus crowding. If 0x36 is absent → wiring/HAT-seating/pull-up problem, not software.
1. Raw read sanity check (do NOT add a global dep; use what's installed, e.g. i2c-tools):
   `i2cget -y 1 0x36 0x02 w` (VCELL) and `i2cget -y 1 0x36 0x04 w` (SOC). Non-error = chip alive.
1. Read `battery_monitor.py`: find the exact exception that maps to "I2C sensor unavailable".
   Log the real exception type/message instead of swallowing it (FileNotFoundError on
   /dev/i2c-1 vs OSError 121 remote-I/O = no ACK tell very different stories).

## 4. FIX THE READ (smallest viable, MAX17040 semantics)

Implement/repair against the suptronics x120x reference (github.com/suptronics/x120x):

- Open bus 1 with `smbus2`; read word at 0x02 (voltage) and 0x04 (capacity); **byte-swap**
  (big-endian word → little) before scaling. Voltage = swapped * 1.25/1000/16;
  capacity = swapped / 256. Clamp capacity 0–100.
- Wrap in async-safe access (no blocking the event loop; offload the sync smbus read
  appropriately within asyncio, no threading) and reuse a single bus handle (don't open/close
  per call — that's a common cause of intermittent 121 errors on a busy bus).
- On read failure: retry once after a short async sleep; only then report degraded. Keep
  AC/charging readings independent so a fuel-gauge miss doesn't blank the whole status.
- Persist `last_known % / V` to a small state file so the WhatsApp reply shows the real last
  value, never "?", even during a transient miss.

## 5. VERIFY (show live output — no claims without it)

- `i2cdetect -y 1` shows 0x36; `i2cget` returns plausible words.
- Run battery_monitor standalone: prints real % and V matching the board's 4 LED level
  (3.87–4.2V = 100%, see wiki table) and the multimeter/LED state.
- Trigger the actual **admin WhatsApp battery command** → returns real `%(V)`, correct
  AC + charging, no "unavailable".
- Confirm motors still drive after BIN2 rewire, and that ultrasonic ranging no longer
  toggles charging (watch the charge LED while pinging).
- Full suite + `brain_replay --all` (if present) stay green.

## 6. GUARDRAILS

asyncio only, no threading. No global pip (system Python, PYTHONPATH). Don't open/close the
I2C bus per read. Don't reassign GPIO2/3/6/16 — they belong to the UPS. Record every change
in `docs/PROJECT_STATE.md` and `docs/AUDIT.md`, and note the physical rewires explicitly in
the commit body (hardware state changed).

## 7. STATE / RESUMPTION

Maintain a short `docs/BATTERY_FIX_STATE.md`: step reached, last real command output, what's
fixed, what's BLOCKED (e.g. "need to physically move BIN2 jumper — Madhan"). On resume,
re-run §3.3 `i2cdetect` to confirm reality before continuing.

## 8. FIRST ACTION

Run §3 diagnostics and paste the real output into BATTERY_FIX_STATE.md before changing any
code. If 0x36 is missing from i2cdetect, stop and report — it's physical (seating/pullups),
not software.
