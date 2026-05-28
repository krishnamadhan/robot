"""
GPIO pin registry — single source of truth for BCM pin → owner.

Asserted at boot so two subsystems can never claim the same pin silently.
Every hardware module that uses a GPIO pin calls claim() after init.

Usage:
    from hardware.pin_registry import pin_registry
    pin_registry.claim(17, "motors.left_front.ain1")
    pin_registry.assert_no_conflicts()   # call once at startup end
"""

from typing import Dict, List, Optional
from utils.logger import get_logger

log = get_logger(__name__)


class PinConflictError(Exception):
    """Raised when two subsystems claim the same GPIO pin."""


class PinRegistry:

    # Known-bad pins on this specific Pi 5 unit — reject at claim time
    DEAD_PINS = {4, 5, 7, 12, 19, 21}

    def __init__(self) -> None:
        self._owners: Dict[int, str] = {}
        self._frozen = False

    def claim(self, bcm_pin: int, owner: str) -> None:
        """Register a pin for an owner. Raises PinConflictError on double-claim."""
        if bcm_pin in self.DEAD_PINS:
            raise PinConflictError(
                f"GPIO{bcm_pin} is a known-dead pin on this Pi 5 unit — "
                f"{owner} must use a different pin"
            )
        if bcm_pin in self._owners:
            existing = self._owners[bcm_pin]
            if existing != owner:
                raise PinConflictError(
                    f"GPIO{bcm_pin} already claimed by '{existing}', "
                    f"'{owner}' cannot also claim it"
                )
        self._owners[bcm_pin] = owner
        log.debug("pin_registry.claimed", gpio=bcm_pin, owner=owner)

    def claim_many(self, pins: Dict[int, str]) -> None:
        for pin, owner in pins.items():
            self.claim(pin, owner)

    def release(self, bcm_pin: int) -> None:
        self._owners.pop(bcm_pin, None)

    def owner_of(self, bcm_pin: int) -> Optional[str]:
        return self._owners.get(bcm_pin)

    def all_claims(self) -> Dict[int, str]:
        return dict(self._owners)

    def check_conflicts(self) -> List[str]:
        """Return list of conflict descriptions (empty = clean)."""
        seen: Dict[int, str] = {}
        conflicts = []
        for pin, owner in self._owners.items():
            if pin in seen:
                conflicts.append(
                    f"GPIO{pin}: claimed by both '{seen[pin]}' and '{owner}'"
                )
            seen[pin] = owner
        return conflicts

    def assert_no_conflicts(self) -> None:
        """Call once at boot end. Raises PinConflictError if any conflicts found."""
        conflicts = self.check_conflicts()
        if conflicts:
            msg = "; ".join(conflicts)
            log.error("pin_registry.conflict", details=msg)
            raise PinConflictError(f"GPIO pin conflicts at boot: {msg}")
        log.info("pin_registry.clean", total_pins=len(self._owners))

    def log_summary(self) -> None:
        for pin in sorted(self._owners):
            log.info("pin_registry.pin", gpio=pin, owner=self._owners[pin])


pin_registry = PinRegistry()


def register_from_config() -> None:
    """
    Read config/hardware.yaml and pre-register all known GPIO pins.
    Call once at startup before any hardware driver inits.
    Import errors are non-fatal — motors/sensors register themselves.
    """
    try:
        from utils.config import cfg
        mc = cfg.hardware.motors

        stby = mc.stby
        pin_registry.claim(stby, "motors.stby")

        for side_name in ("left_front", "left_rear", "right_front", "right_rear"):
            side = getattr(mc, side_name, None)
            if side is None:
                continue
            for attr, role in [("ain1", "ain1"), ("ain2", "ain2"), ("bin1", "bin1"),
                                ("bin2", "bin2"), ("pwm", "pwm")]:
                pin = getattr(side, attr, None)
                if pin is not None:
                    pin_registry.claim(pin, f"motors.{side_name}.{role}")

        sc = cfg.hardware.sensors
        if getattr(getattr(sc, "ultrasonic", None), "available", False):
            pin_registry.claim(sc.ultrasonic.trigger_pin, "sensors.ultrasonic.trig")
            pin_registry.claim(sc.ultrasonic.echo_pin,    "sensors.ultrasonic.echo")

        if getattr(getattr(sc, "pir", None), "available", False):
            pin_registry.claim(sc.pir.pin, "sensors.pir")

        touch_pins = getattr(getattr(sc, "touch", None), "pins", [])
        if getattr(getattr(sc, "touch", None), "available", False):
            for i, p in enumerate(touch_pins):
                pin_registry.claim(p, f"sensors.touch[{i}]")

        cliff_pins = getattr(getattr(sc, "cliff", None), "pins", [])
        if getattr(getattr(sc, "cliff", None), "available", False):
            for i, p in enumerate(cliff_pins):
                pin_registry.claim(p, f"sensors.cliff[{i}]")

        vib_pin = getattr(getattr(sc, "vibration", None), "pin", None)
        if vib_pin and getattr(getattr(sc, "vibration", None), "available", False):
            pin_registry.claim(vib_pin, "sensors.vibration")

        sound_pin = getattr(getattr(sc, "sound", None), "pin", None)
        if sound_pin and getattr(getattr(sc, "sound", None), "available", False):
            pin_registry.claim(sound_pin, "sensors.sound")

        apds_int = getattr(getattr(sc, "apds9960", None), "int_pin", None)
        if apds_int and getattr(getattr(sc, "apds9960", None), "available", False):
            pin_registry.claim(apds_int, "sensors.apds9960.int")

        log.info("pin_registry.loaded_from_config", pins=len(pin_registry.all_claims()))

    except Exception as e:
        log.warning("pin_registry.config_load_failed", error=str(e))
