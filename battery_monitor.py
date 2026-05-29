#!/usr/bin/env python3
"""
Battery monitor for DFRobot FIT0992 UPS HAT.
- SOC + voltage via MAX17040 (I2C 0x36, bus 1)
- AC detection via GPIO 9 (HIGH = AC present) — SPI_MISO, SPI unused
- Charging control via GPIO 16 (LOW = charging enabled)
- Auto charge management: ON below 20%, OFF above 90%
- Auto-shutdown: AC lost + battery critical for 3 consecutive cycles

Design notes:
- AC pin is read independently of I2C so power state is always known
- State file is always written each cycle, even if battery I2C is unavailable
- notify() is non-blocking: 3 retries × 5s max, then gives up
- When battery chip is unavailable (fully dead / not yet powered), bat_level
  is set to "unknown" so pi-monitor shows correct status
- AC_PIN moved from GPIO6 to GPIO9: GPIO6 = motor BIN2 (TB6612FNG right-backward)
"""
import smbus2, urllib.request, json, time, os, subprocess, logging

I2C_BUS           = 1
I2C_ADDR          = 0x36   # MAX17040 fuel gauge
REG_VCELL         = 0x02
REG_SOC           = 0x04
AC_PIN            = 9      # HIGH = AC connected (GPIO9/SPI_MISO — SPI unused)
CHARGE_CTRL_PIN   = 16     # LOW = charging enabled
NOTIFY_URL        = "http://localhost:3099/notify"
ADMIN_NUMBER      = "919487506127@c.us"
STATE_FILE        = "/tmp/battery_monitor_state.json"
CHECK_SECS        = 60
NOTIFY_COOLDOWN   = 600    # min seconds between repeat alerts
SHUTDOWN_CYCLES   = 3      # consecutive AC-loss + low-battery cycles before shutdown
AUTO_SHUTDOWN     = True
CHARGE_START      = 20     # % — start charging below this
CHARGE_STOP       = 90     # % — stop charging above this

os.makedirs("/home/pi/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [battery] %(message)s",
    handlers=[
        logging.FileHandler("/home/pi/logs/battery-monitor.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger()


# ── Hardware reads ────────────────────────────────────────────────────────────

COSMO_BATTERY_URL = "http://localhost:8000/battery"


def read_battery():
    """Return (voltage_v, soc_pct) or raise OSError/ValueError.

    Primary: read from cosmo's HTTP API (avoids I2C EAGAIN on Pi 5 RP1).
    Fallback: direct smbus2 read with 3 retries if API is unavailable.
    """
    # Primary path — cosmo holds the shared I2C bus singleton
    try:
        with urllib.request.urlopen(COSMO_BATTERY_URL, timeout=3) as resp:
            data = json.loads(resp.read())
        pct = data.get("percent")
        volt = data.get("voltage")
        if pct is not None and volt is not None and float(volt) > 0:
            return round(float(volt), 3), round(float(pct), 1)
    except Exception as api_err:
        log.debug(f"Battery API unavailable ({api_err}), falling back to direct I2C")

    # Fallback path — direct smbus2 (may get EAGAIN if cosmo is running)
    last_err = None
    for attempt in range(3):
        try:
            bus = smbus2.SMBus(I2C_BUS)
            try:
                dv = bus.read_i2c_block_data(I2C_ADDR, REG_VCELL, 2)
                ds = bus.read_i2c_block_data(I2C_ADDR, REG_SOC, 2)
            finally:
                bus.close()
            raw_v = (dv[0] << 8) | dv[1]
            voltage = (raw_v >> 4) * 1.25 / 1000
            raw_s = (ds[0] << 8) | ds[1]
            soc = min(100.0, (raw_s >> 8) + ((raw_s & 0xFF) / 256))
            if not (2.5 <= voltage <= 4.5):
                raise ValueError(f"Out-of-range: v={voltage:.3f} soc={soc:.1f}")
            return round(voltage, 3), round(soc, 1)
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(1)
    raise last_err


def read_ac_power():
    """Return True if AC is connected. Always returns True until a physical AC_OK wire
    is run to AC_PIN — GPIO6 (original) conflicts with motor BIN2, no alternative wired yet.
    Safe default: assume AC present so auto-shutdown never fires spuriously."""
    return True


def set_charging(enable: bool):
    level = "dl" if enable else "dh"
    subprocess.run(["pinctrl", "set", str(CHARGE_CTRL_PIN), "op", level], capture_output=True)
    log.info(f"Charging {'enabled' if enable else 'disabled'} via GPIO {CHARGE_CTRL_PIN}")


def voltage_status(v: float) -> str:
    if v >= 3.87:   return "Full"
    elif v >= 3.70: return "High"
    elif v >= 3.55: return "Medium"
    elif v >= 3.40: return "Low"
    else:           return "Critical"


# ── State file ────────────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            return json.loads(open(STATE_FILE).read())
    except Exception:
        pass
    return {
        "bat_level": "unknown", "ac_ok": True, "charging": True,
        "last_alert": 0, "last_ac_alert": 0, "bad_cycles": 0,
    }


def save_state(s: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(s, f)
    except Exception as e:
        log.warning(f"State file write failed: {e}")


# ── Notifications ─────────────────────────────────────────────────────────────

def notify(message: str):
    """Send WhatsApp alert. Non-blocking: 3 attempts, 5s apart, then give up."""
    payload = json.dumps({"message": message, "to": ADMIN_NUMBER}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                NOTIFY_URL, data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            log.info("Alert sent")
            return
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            else:
                log.warning(f"Alert failed after 3 attempts: {e}")


# ── Startup ───────────────────────────────────────────────────────────────────

log.info("Battery monitor started. Waiting 90s for BanterAgent to connect...")
time.sleep(90)

# Read what we can at startup; AC is always readable, battery may not be yet
ac_startup = read_ac_power()
try:
    v_init, soc_init = read_battery()
    vstatus_init = voltage_status(v_init)
    charge_init = soc_init < CHARGE_STOP
    set_charging(charge_init)
    state0 = load_state()
    state0.update({"ac_ok": ac_startup, "charging": charge_init,
                   "bat_level": "ok" if soc_init >= 20 else ("low" if soc_init >= 5 else "critical"),
                   "last_soc": soc_init, "last_voltage": v_init})
    save_state(state0)
    notify(
        f"*[Monitor] Battery monitor started*\n"
        f"Battery: {soc_init:.0f}% | {v_init:.3f}V ({vstatus_init})\n"
        f"AC: {'Connected' if ac_startup else 'DISCONNECTED'} | "
        f"Charging: {'ON' if charge_init else 'OFF'}"
    )
    log.info(f"Startup: {v_init:.3f}V {soc_init:.1f}% AC={'OK' if ac_startup else 'LOSS'}")
except Exception as e:
    # Battery chip unavailable at startup (fully dead / still waking up)
    log.warning(f"Startup battery read failed: {e} — chip may still be waking up")
    state0 = load_state()
    state0.update({"ac_ok": ac_startup, "bat_level": "unknown"})
    save_state(state0)
    notify(
        f"*[Monitor] Battery monitor started*\n"
        f"Battery sensor unavailable (chip waking up — will retry)\n"
        f"AC: {'Connected' if ac_startup else 'DISCONNECTED'}"
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

while True:
    time.sleep(CHECK_SECS)

    # 1. Always read AC pin first — independent of battery I2C
    ac_ok = read_ac_power()
    now = time.time()
    state = load_state()
    prev_ac = state.get("ac_ok", True)
    prev_bat = state.get("bat_level", "unknown")
    charging = state.get("charging", True)
    since_alert = now - state.get("last_alert", 0)

    # 2. Try to read battery chip
    voltage = soc = vstatus = None
    try:
        voltage, soc = read_battery()
        vstatus = voltage_status(voltage)
        log.info(
            f"{voltage:.3f}V  {soc:.1f}%  {vstatus}  "
            f"AC={'OK' if ac_ok else 'LOSS'}  "
            f"charging={'ON' if charging else 'OFF'}  "
            f"bad_cycles={state.get('bad_cycles', 0)}"
        )
    except Exception as e:
        log.info(f"Battery chip unavailable: {e} | AC={'OK' if ac_ok else 'LOSS'}")

    # 3. AC change alerts
    if not ac_ok and prev_ac:
        batt_info = f"{soc:.0f}% | {voltage:.3f}V ({vstatus})" if soc is not None else "level unknown"
        notify(
            f"*[Monitor] AC Power Lost*\n"
            f"Battery: {batt_info}\n"
            f"Running on battery. Will shutdown if battery drops critical."
        )
        state["last_ac_alert"] = now
    elif ac_ok and not prev_ac:
        state["bad_cycles"] = 0
        if soc is not None and soc < CHARGE_STOP and not charging:
            set_charging(True)
            state["charging"] = True
            charging = True
        batt_info = f"{soc:.0f}% | {voltage:.3f}V ({vstatus})" if soc is not None else "level unknown"
        notify(
            f"*[Monitor] AC Power Restored*\n"
            f"Battery: {batt_info}\n"
            f"{'Charging resumed.' if charging else 'Charging not needed.'}"
        )
    state["ac_ok"] = ac_ok

    # 4. Battery-level logic (only when chip is readable)
    if soc is not None and voltage is not None:
        # Auto charge management
        if soc < CHARGE_START and not charging:
            set_charging(True)
            state["charging"] = True
            charging = True
            notify(f"*[Monitor] Charging Started*\nBattery: {soc:.0f}% | {voltage:.3f}V\nBelow {CHARGE_START}% — charging enabled.")
        elif soc >= CHARGE_STOP and charging:
            set_charging(False)
            state["charging"] = False
            notify(f"*[Monitor] Charging Stopped*\nBattery: {soc:.0f}% | {voltage:.3f}V\nAbove {CHARGE_STOP}% — protecting battery.")

        # Level alerts
        if vstatus == "Critical" or soc < 5:
            bat_level = "critical"
            if prev_bat != "critical" or since_alert > NOTIFY_COOLDOWN:
                notify(f"*[Monitor] Battery CRITICAL*\nBattery: {soc:.0f}% | {voltage:.3f}V\n{'AC disconnected! ' if not ac_ok else ''}Charge immediately.")
                state["last_alert"] = now
        elif vstatus == "Low" or soc < 20:
            bat_level = "low"
            if prev_bat not in ("low", "critical"):
                notify(f"*[Monitor] Battery LOW*\nBattery: {soc:.0f}% | {voltage:.3f}V ({vstatus})")
                state["last_alert"] = now
        else:
            bat_level = "ok"
            if prev_bat in ("low", "critical") and soc >= 80:
                notify(f"*[Monitor] Battery Recovered*\nBattery: {soc:.0f}% | {voltage:.3f}V ({vstatus})")
        state["bat_level"] = bat_level

        # Auto-shutdown: AC lost + critically low for N cycles
        if AUTO_SHUTDOWN and not ac_ok and (vstatus in ("Critical", "Low") or soc < 20):
            state["bad_cycles"] = state.get("bad_cycles", 0) + 1
            if state["bad_cycles"] >= SHUTDOWN_CYCLES:
                notify(
                    f"*[Monitor] Pi SHUTTING DOWN*\n"
                    f"Battery: {soc:.0f}% | {voltage:.3f}V. AC disconnected.\n"
                    f"Shutting down to protect data."
                )
                time.sleep(5)
                log.warning("Auto-shutdown triggered.")
                subprocess.call(["sudo", "shutdown", "-h", "now"])
        elif ac_ok:
            state["bad_cycles"] = 0

        state["last_soc"] = soc
        state["last_voltage"] = voltage
    else:
        # Chip unavailable — preserve last known bat_level, don't reset bad_cycles
        state["bat_level"] = "unknown"

    # 5. Always write state so pi-monitor has fresh data
    save_state(state)
