"""
Config loader with Pydantic validation.
All YAML files are loaded once at startup and validated into typed models.
Modules import `cfg` directly — no file I/O at runtime.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml
from pydantic import BaseModel, Field

CONFIG_DIR = Path(__file__).parent.parent / "config"


# ── Pydantic models for each config section ─────────────────────────────────

class SimulationConfig(BaseModel):
    enabled: str = "auto"          # "auto" | "always" | "never"
    noise_level: float = 0.05
    latency_simulation: bool = True
    scenario: Optional[str] = None


class MotorChannelConfig(BaseModel):
    """Pin config for one TB6612FNG H-bridge channel (A or B side)."""
    ain1: int = 0
    ain2: int = 0
    bin1: int = 0
    bin2: int = 0
    pwm: int = 0
    model_config = {"extra": "allow"}


class MotorConfig(BaseModel):
    driver: str = "TB6612FNG"
    boards: int = 1
    stby: int = 27
    max_speed: int = 100
    default_speed: int = 60
    ramp_ms: int = 150
    stall_detect_ms: int = 500
    left_trim: float = 0.600
    right_trim: float = 1.000
    # 4WD nested channel configs
    left_front:  Optional[MotorChannelConfig] = None
    left_rear:   Optional[MotorChannelConfig] = None
    right_front: Optional[MotorChannelConfig] = None
    right_rear:  Optional[MotorChannelConfig] = None
    model_config = {"extra": "allow"}


class ServoLimits(BaseModel):
    min: int
    max: int
    center: int


class ServoConfig(BaseModel):
    driver: str = "PCA9685"
    i2c_address: int = 0x40
    frequency: int = 50
    channels: Dict[str, int] = {}
    limits: Dict[str, ServoLimits] = {}


class DisplayConfig(BaseModel):
    i2c_address: int
    width: int = 128
    height: int = 64
    driver: str = "SSD1306"


class SensorEntry(BaseModel):
    available: bool = False
    model_config = {"extra": "allow"}   # allow hardware-specific keys


class BatteryConfig(BaseModel):
    class UPSConfig(BaseModel):
        i2c_address: int = 0x36
        driver: str = "MAX17043"
        available: bool = True
    class LiPoConfig(BaseModel):
        critical_voltage: float = 6.8
        low_voltage: float = 7.0
        full_voltage: float = 8.4
        cells: int = 2
    ups_hat: UPSConfig = UPSConfig()
    lipo: LiPoConfig = LiPoConfig()


class CameraConfig(BaseModel):
    device: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30
    backend: str = "opencv"


class HardwareConfig(BaseModel):
    simulation: SimulationConfig = SimulationConfig()
    i2c: Dict[str, Any] = {"bus": 1}
    motors: MotorConfig = MotorConfig()
    servos: ServoConfig = ServoConfig()
    displays: Dict[str, DisplayConfig] = {}
    sensors: Dict[str, Any] = {}
    battery: BatteryConfig = BatteryConfig()
    camera: CameraConfig = CameraConfig()
    audio: Dict[str, Any] = {}


class PersonalityTraits(BaseModel):
    curiosity: float = 0.8
    affection: float = 0.75
    playfulness: float = 0.75
    caution: float = 0.4
    independence: float = 0.45
    expressiveness: float = 0.85


class EmotionalState(BaseModel):
    mood: float = 0.6
    energy: float = 0.7
    arousal: float = 0.5
    attachment: float = 0.6


class PersonalityConfig(BaseModel):
    name: str = "Cosmo"
    home: str = "Bangalore"
    owners: List[Dict[str, Any]] = []
    base_traits: PersonalityTraits = PersonalityTraits()
    emotional_state: EmotionalState = EmotionalState()
    decay_rates: Dict[str, float] = {}
    baselines: Dict[str, float] = {}
    thresholds: Dict[str, float] = {}
    quirks: List[Dict[str, Any]] = []
    time_of_day: Dict[str, Any] = {}
    event_impacts: Dict[str, Dict[str, float]] = {}

    model_config = {"extra": "allow"}


class ThresholdsConfig(BaseModel):
    safety: Dict[str, Any] = {}
    light: Dict[str, Any] = {}
    motion: Dict[str, Any] = {}
    sound: Dict[str, Any] = {}
    touch: Dict[str, Any] = {}
    distance: Dict[str, Any] = {}
    attention: Dict[str, Any] = {}
    vision: Dict[str, Any] = {}

    model_config = {"extra": "allow"}


class ModelsConfig(BaseModel):
    llm: Dict[str, Any] = {}
    person_detection: Dict[str, Any] = {}
    face_detection: Dict[str, Any] = {}
    face_recognition: Dict[str, Any] = {}
    speech: Dict[str, Any] = {}
    model_config = {"extra": "allow"}


# ── Config container ─────────────────────────────────────────────────────────

class RobotConfig:
    """
    Singleton config object. Access via module-level `cfg`.
    Loads and validates all YAML files on import.
    """

    def __init__(self) -> None:
        self.hardware = self._load(HardwareConfig, "hardware.yaml")
        self.personality = self._load_personality()
        self.thresholds = self._load(ThresholdsConfig, "thresholds.yaml")
        self.models = self._load(ModelsConfig, "models.yaml")

    def _load(self, model: type, filename: str) -> Any:
        path = CONFIG_DIR / filename
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            return model(**data)
        except Exception as e:
            print(f"[config] WARNING: Failed to load {filename}: {e} — using defaults")
            return model()

    def _load_personality(self) -> PersonalityConfig:
        path = CONFIG_DIR / "personality.yaml"
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            # personality.yaml has a nested "personality" key
            inner = data.get("personality", data)
            return PersonalityConfig(**inner)
        except Exception as e:
            print(f"[config] WARNING: Failed to load personality.yaml: {e} — using defaults")
            return PersonalityConfig()

    def simulation_enabled(self) -> bool:
        """Resolve 'auto' to a real bool based on hardware availability."""
        mode = self.hardware.simulation.enabled
        if mode == "always":
            return True
        if mode == "never":
            return False
        # auto: check if we're on a Pi
        return not Path("/proc/device-tree/model").exists()

    def get(self, *keys: str, default: Any = None) -> Any:
        """Dot-path access: cfg.get('hardware', 'motors', 'ain1')"""
        obj = self
        for key in keys:
            try:
                obj = getattr(obj, key)
            except AttributeError:
                try:
                    obj = obj[key]
                except (KeyError, TypeError):
                    return default
        return obj


# Module-level singleton — import this everywhere
cfg = RobotConfig()
