"""Load and validate the shared configuration in config/hardware.yaml.

Other modules obtain configuration through get_config() rather than reading
the file independently or duplicating pin assignments and conversion factors.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("LAB_CONFIG", ROOT / "config" / "hardware.yaml"))

_lock = threading.Lock()
_cache: Dict[str, Any] | None = None


class ConfigError(RuntimeError):
    pass


def _require(cfg: Dict[str, Any], path: str) -> Any:
    node: Any = cfg
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"Required hardware.yaml field is missing: {path}")
        node = node[part]
    return node


def _validate(cfg: Dict[str, Any]) -> None:
    for path in ("link.baud", "server.port", "motors.groups",
                 "motors.pulses_per_rev", "motors.pulses_per_ml",
                 "motors.dose_pulses_per_ml",
                 "motors.reference_pulses_per_ml",
                 "motors.gravimetric_dose", "cleaning",
                 "cleaning.water_motor", "cleaning.out_motor",
                 "cleaning.flow_ml_min", "cleaning.water_lead_s",
                 "cleaning.overlap_s", "cleaning.out_tail_s",
                 "valves.channels",
                 "valves.open_before_ms", "valves.close_after_ms",
                 "valves.motor_links",
                 "syringe_pump.baud", "scale.stream_hz",
                 "scale.average_samples", "scale.stable_threshold_g"):
        _require(cfg, path)

    used: Dict[int, str] = {}

    def claim(pin: Any, who: str) -> None:
        if pin is None:
            return
        pin = int(pin)
        if pin in used:
            raise ConfigError(f"Pin D{pin} is assigned to both {used[pin]} and {who}")
        used[pin] = who

    for group in cfg["motors"]["groups"]:
        for key in ("pul", "dir", "ena"):
            claim(group["driver"][key], f"driver {group['id']}.{key}")
        for ch in group["channels"]:
            claim(ch["pin"], f"motor {ch['id']} relay")

    for ch in cfg["valves"]["channels"]:
        claim(ch["pin"], f"solenoid valve {ch['id']}")

    motor_ids = {
        int(ch["id"])
        for group in cfg["motors"]["groups"]
        for ch in group["channels"]
    }
    valve_ids = {int(ch["id"]) for ch in cfg["valves"]["channels"]}
    linked_motors: set[int] = set()
    linked_valves: set[int] = set()
    for link in cfg["valves"]["motor_links"]:
        motor, valve = int(link["motor"]), int(link["valve"])
        if motor not in motor_ids:
            raise ConfigError(f"Valve linkage references unconfigured motor {motor}")
        if valve not in valve_ids:
            raise ConfigError(f"Valve linkage references unconfigured valve {valve}")
        if motor in linked_motors:
            raise ConfigError(f"Motor {motor} has duplicate valve linkages")
        if valve in linked_valves:
            raise ConfigError(f"Valve {valve} has duplicate motor linkages")
        linked_motors.add(motor)
        linked_valves.add(valve)
    for key in ("open_before_ms", "close_after_ms"):
        delay = int(cfg["valves"][key])
        if not 0 <= delay <= 60000:
            raise ConfigError(f"valves.{key} must be between 0 and 60000 ms")

    cleaning = cfg["cleaning"]
    water_motor = int(cleaning["water_motor"])
    out_motor = int(cleaning["out_motor"])
    if water_motor not in motor_ids or out_motor not in motor_ids:
        raise ConfigError("cleaning.water_motor and out_motor must reference configured motors")
    if water_motor == out_motor:
        raise ConfigError("cleaning.water_motor and out_motor must differ")
    motor_groups = {
        int(ch["id"]): str(group["id"])
        for group in cfg["motors"]["groups"]
        for ch in group["channels"]
    }
    if motor_groups[water_motor] == motor_groups[out_motor]:
        raise ConfigError("Cleaning water and OUT motors must use separate driver groups for simultaneous operation")
    if water_motor not in linked_motors:
        raise ConfigError("cleaning.water_motor must have an automatic valve linkage")
    if out_motor in linked_motors:
        raise ConfigError("cleaning.out_motor must not have an automatic valve linkage")
    if not 0 < float(cleaning["flow_ml_min"]) <= float(cfg["motors"]["max_flow_ml_min"]):
        raise ConfigError("cleaning.flow_ml_min must be positive and not exceed max_flow_ml_min")
    if not 0 <= float(cleaning["water_lead_s"]) <= 3600:
        raise ConfigError("cleaning.water_lead_s must be between 0 and 3600 s")
    if not 0 < float(cleaning["overlap_s"]) <= 3600:
        raise ConfigError("cleaning.overlap_s must be between 0 and 3600 s")
    if not 0 <= float(cleaning["out_tail_s"]) <= 3600:
        raise ConfigError("cleaning.out_tail_s must be between 0 and 3600 s")

    pump = cfg["syringe_pump"]
    claim(pump.get("tx_pin"), "Syringe pump TX")
    claim(pump.get("rx_pin"), "Syringe pump RX")

    scale = cfg["scale"]
    claim(scale.get("tx_pin"), "Scale transmitter TX")
    claim(scale.get("rx_pin"), "Scale transmitter RX")
    claim(scale.get("de_pin"), "Scale transmitter DE/RE")

    if not 1 <= int(scale["stream_hz"]) <= 20:
        raise ConfigError("scale.stream_hz must be between 1 and 20")
    motors = cfg["motors"]
    if float(motors["pulses_per_ml"]) <= 0:
        raise ConfigError("motors.pulses_per_ml must be positive")
    if float(motors["dose_pulses_per_ml"]) <= 0:
        raise ConfigError("motors.dose_pulses_per_ml must be positive")
    if float(motors["reference_pulses_per_ml"]) <= 0:
        raise ConfigError("motors.reference_pulses_per_ml must be positive")
    for group in motors["groups"]:
        for ch in group["channels"]:
            ppm = float(ch.get("pulses_per_ml", motors["pulses_per_ml"]))
            if ppm <= 0:
                raise ConfigError(f"Motor {ch['id']} pulses_per_ml must be positive")
            if ppm != float(motors["pulses_per_ml"]):
                raise ConfigError(
                    f"Motor {ch['id']} pulses_per_ml differs from the shared value; equal pump speeds are required")
            dose_ppm = float(ch.get(
                "dose_pulses_per_ml", motors["dose_pulses_per_ml"]))
            if dose_ppm <= 0:
                raise ConfigError(
                    f"Motor {ch['id']} dose_pulses_per_ml must be positive")
            if dose_ppm != float(motors["dose_pulses_per_ml"]):
                raise ConfigError(
                    f"Motor {ch['id']} dose_pulses_per_ml differs from the shared value; "
                    "equal dosing durations are required")
            channel_sps = float(motors["max_flow_ml_min"]) * ppm / 60.0
            if channel_sps > float(motors["max_sps"]):
                raise ConfigError(
                    f"Motor {ch['id']} exceeds motors.max_sps at max_flow_ml_min")
    clean_sps = (float(cleaning["flow_ml_min"])
                 * float(motors["pulses_per_ml"]) / 60.0)
    if clean_sps > float(motors["max_sps"]):
        raise ConfigError("Converted cleaning.flow_ml_min exceeds motors.max_sps")
    if int(motors["pulses_per_rev"]) != (
            int(motors["motor_full_steps_per_rev"]) * int(motors["microsteps"])):
        raise ConfigError("motors.pulses_per_rev must equal full steps times microsteps")
    if len(motors.get("driver_switches", [])) != 8:
        raise ConfigError("motors.driver_switches must list SW1-SW8 in order")
    grav = motors["gravimetric_dose"]
    for key in ("default_target_g", "density_g_ml", "tolerance_percent",
                "slow_zone_g", "trim_zone_g", "timeout_s",
                "fast_flow_ml_min", "approach_flow_ml_min", "trim_flow_ml_min"):
        if float(grav.get(key, 0)) <= 0:
            raise ConfigError(f"motors.gravimetric_dose.{key} must be positive")
    if float(grav["tolerance_percent"]) > 100:
        raise ConfigError("motors.gravimetric_dose.tolerance_percent must not exceed 100")
    for key in ("fast_flow_ml_min", "approach_flow_ml_min", "trim_flow_ml_min"):
        if float(grav[key]) > float(motors["max_flow_ml_min"]):
            raise ConfigError(
                f"motors.gravimetric_dose.{key} must not exceed max_flow_ml_min")
    if not (float(grav["trim_flow_ml_min"]) <= float(grav["approach_flow_ml_min"])
            <= float(grav["fast_flow_ml_min"])):
        raise ConfigError(
            "Gravimetric flow rates must satisfy trim <= approach <= fast")
    dose_flow = float(motors.get("dose_flow_ml_min",
                                 motors["default_flow_ml_min"]))
    if not 0 < dose_flow <= float(motors["max_flow_ml_min"]):
        raise ConfigError("motors.dose_flow_ml_min must be positive and not exceed max_flow_ml_min")
    if not 1 <= int(scale["average_samples"]) <= 16:
        raise ConfigError("scale.average_samples must be between 1 and 16")
    if not 1 <= int(scale.get("display_decimals", 3)) <= 4:
        raise ConfigError("scale.display_decimals must be between 1 and 4")
    if not 0 < float(scale["stable_threshold_g"]) <= 10:
        raise ConfigError("scale.stable_threshold_g must be between 0 and 10 g")


def load_config(path: Path | None = None) -> Dict[str, Any]:
    target = Path(path) if path else CONFIG_PATH
    if not target.exists():
        raise ConfigError(f"Configuration file not found: {target}")
    with open(target, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict):
        raise ConfigError("hardware.yaml must contain a top-level mapping")
    _validate(cfg)
    return cfg


def get_config(reload: bool = False) -> Dict[str, Any]:
    global _cache
    with _lock:
        if _cache is None or reload:
            _cache = load_config()
        return _cache


def all_motor_ids() -> List[int]:
    return [ch["id"] for g in get_config()["motors"]["groups"] for ch in g["channels"]]


def group_of(motor_id: int) -> str | None:
    for g in get_config()["motors"]["groups"]:
        if any(ch["id"] == motor_id for ch in g["channels"]):
            return g["id"]
    return None


def valve_ids() -> List[int]:
    return [ch["id"] for ch in get_config()["valves"]["channels"]]
