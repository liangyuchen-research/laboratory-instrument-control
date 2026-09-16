"""Relay-selected peristaltic pumps driven by stepper motors.

Each group of four motors shares a DM542J driver through four MY4N-GS relays.
Firmware performs cold switching: disable the driver, open all relay contacts,
select the target motor, then enable the driver. This module converts flow and
volume into steps per second and total steps. Groups A and B have independent
drivers and can each operate one motor at a time.
"""
from __future__ import annotations

import math
import statistics
import threading
import time
from collections import deque
from typing import Any, Dict, List

from ..registry import Context, FunctionError, Param, function

GROUP = "motors"

_weight_dose_lock = threading.Lock()
_weight_session_lock = threading.Lock()
_weight_cancel: threading.Event | None = None
_weight_motor: int | None = None


class _DoseCancelled(RuntimeError):
    pass


# ---------------------------------------------------------------------
# Unit conversions use coefficients from hardware.yaml.
# ---------------------------------------------------------------------
def motor_pulses_per_ml(cfg: Dict[str, Any], motor: int) -> float:
    """Shared flow conversion ensures equal flow settings produce equal step rates."""
    for group in cfg["motors"]["groups"]:
        for channel in group["channels"]:
            if int(channel["id"]) == int(motor):
                return float(channel.get(
                    "pulses_per_ml", cfg["motors"]["pulses_per_ml"]))
    raise FunctionError(f"Motor {motor} is not configured")


def motor_dose_pulses_per_ml(cfg: Dict[str, Any], motor: int) -> float:
    """Shared volume conversion ensures equal volumes have equal motor run times."""
    for group in cfg["motors"]["groups"]:
        for channel in group["channels"]:
            if int(channel["id"]) == int(motor):
                return float(channel.get(
                    "dose_pulses_per_ml", cfg["motors"]["dose_pulses_per_ml"]))
    raise FunctionError(f"Motor {motor} is not configured")


def flow_to_sps(cfg: Dict[str, Any], ml_min: float, motor: int | None = None) -> int:
    ppm = (motor_pulses_per_ml(cfg, motor) if motor is not None
           else float(cfg["motors"]["pulses_per_ml"]))
    return max(1, round(ml_min * ppm / 60.0))


def volume_to_steps(cfg: Dict[str, Any], ml: float, motor: int | None = None) -> int:
    ppm = (motor_dose_pulses_per_ml(cfg, motor) if motor is not None
           else float(cfg["motors"]["pulses_per_ml"]))
    return max(1, round(ml * ppm))


def _channels(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [ch for g in cfg["motors"]["groups"] for ch in g["channels"]]


def _check_motor(cfg: Dict[str, Any], motor: int) -> Dict[str, Any]:
    for ch in _channels(cfg):
        if ch["id"] == motor:
            return ch
    raise FunctionError(f"Motor {motor} is not configured")


def _check_flow(cfg: Dict[str, Any], ml_min: float, motor: int | None = None) -> float:
    top = float(cfg["motors"]["max_flow_ml_min"])
    if not 0 < ml_min <= top:
        raise FunctionError(f"Flow must be greater than 0 and at most {top} mL/min")
    sps = flow_to_sps(cfg, ml_min, motor)
    if sps > float(cfg["motors"]["max_sps"]):
        raise FunctionError(f"{ml_min} mL/min converts to {sps} steps/s, exceeding the driver limit")
    return ml_min


def _valve_sequence(cfg: Dict[str, Any], motor: int) -> Dict[str, Any] | None:
    """Return the automatic valve sequence, or None for an unlinked channel."""
    for link in cfg["valves"]["motor_links"]:
        if int(link["motor"]) == motor:
            return {
                **link,
                "open_before_ms": int(cfg["valves"]["open_before_ms"]),
                "close_after_ms": int(cfg["valves"]["close_after_ms"]),
            }
    return None


def _weight_session_active() -> int | None:
    with _weight_session_lock:
        return _weight_motor


def _reject_during_weight_dose() -> None:
    motor = _weight_session_active()
    if motor is not None:
        raise FunctionError(f"Motor {motor} is dispensing by weight; stop it or wait for completion")


def _reject_during_cleaning(ctx: Context) -> None:
    if bool(ctx.link.snapshot().get("cleaning")):
        raise FunctionError("Cleaning is in progress; wait for completion or use Stop all")


def _cancel_weight_dose(motor: int | None = None) -> None:
    with _weight_session_lock:
        if _weight_cancel is not None and (motor is None or motor == _weight_motor):
            _weight_cancel.set()


def _new_weight_sample(link: Any, last_sequence: int, timeout_s: float,
                       cancel: threading.Event) -> tuple[Dict[str, Any], int]:
    """Wait for a fresh sample; timeout_s applies to this sample, not the entire dose."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cancel.is_set():
            raise _DoseCancelled()
        scale = link.snapshot()["scale"]
        sequence = int(scale.get("sequence") or 0)
        grams = scale.get("grams")
        if (scale.get("online") and sequence > last_sequence
                and isinstance(grams, (int, float)) and math.isfinite(float(grams))):
            return scale, sequence
        time.sleep(0.01)
    raise FunctionError("No fresh valid scale reading was received; the peristaltic pump has been stopped")


def _collect_weights(link: Any, count: int, timeout_s: float,
                     cancel: threading.Event) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    last_sequence = 0
    while len(samples) < count:
        sample, last_sequence = _new_weight_sample(
            link, last_sequence, timeout_s, cancel)
        samples.append(sample)
    return samples


def _median_grams(samples: List[Dict[str, Any]]) -> float:
    return float(statistics.median(float(s["grams"]) for s in samples))


def _stop_quietly(link: Any, motor: int) -> None:
    try:
        link.send(f"V,{motor},0")
    except Exception:
        pass


_MOTOR_PARAM = Param("motor", "integer", label="Motor ID")
_FLOW_PARAM = Param("flow_ml_min", "number", label="Flow rate", unit="mL/min", minimum=0.01)


# ---------------------------------------------------------------------
@function("motors.list", "List peristaltic pump channels and selected motors", GROUP)
def list_motors(ctx: Context) -> Dict[str, Any]:
    cfg = ctx.config
    state = ctx.link.snapshot()["motors"]
    groups = []
    for g in cfg["motors"]["groups"]:
        groups.append({
            "id": g["id"],
            "label": g["label"],
            "color": g.get("color", "blue"),
            "driver": g["driver"],
            "channels": g["channels"],
            "selected": state.get(g["id"], {}).get("selected"),
            "state": state.get(g["id"], {}).get("state", "idle"),
        })
    return {"groups": groups,
            "pulses_per_ml": cfg["motors"]["pulses_per_ml"],
            "dose_pulses_per_ml": cfg["motors"]["dose_pulses_per_ml"],
            "pulses_per_rev": cfg["motors"]["pulses_per_rev"],
            "driver_model": cfg["motors"].get("driver_model"),
            "driver_switches": cfg["motors"].get("driver_switches"),
            "pump_head_model": cfg["motors"].get("pump_head_model"),
            "gravimetric_dose": cfg["motors"].get("gravimetric_dose", {}),
            "cleaning": {**cfg["cleaning"],
                         "active": bool(ctx.link.snapshot().get("cleaning"))},
            "valve_sequence": {
                "open_before_ms": cfg["valves"]["open_before_ms"],
                "close_after_ms": cfg["valves"]["close_after_ms"],
                "motor_links": cfg["valves"]["motor_links"],
            },
            "max_flow_ml_min": cfg["motors"]["max_flow_ml_min"]}


@function("motors.clean",
          "Clean with 30 s water intake, 45 s simultaneous intake and drainage, then 45 s drainage",
          GROUP)
def clean(ctx: Context) -> Dict[str, Any]:
    _reject_during_weight_dose()
    cfg = ctx.config
    before = ctx.link.refresh_state()
    if bool(before.get("cleaning")):
        raise FunctionError("Cleaning is already in progress")
    active = [
        gid for gid, slot in before.get("motors", {}).items()
        if slot.get("state") not in (None, "idle")
    ]
    if active:
        raise FunctionError("Stop the running peristaltic pumps before starting cleaning")

    ctx.link.send("C")
    state = ctx.link.refresh_state()
    cleaning = cfg["cleaning"]
    return {
        "started": bool(state.get("cleaning")),
        "water_motor": int(cleaning["water_motor"]),
        "out_motor": int(cleaning["out_motor"]),
        "flow_ml_min": float(cleaning["flow_ml_min"]),
        "water_lead_s": float(cleaning["water_lead_s"]),
        "overlap_s": float(cleaning["overlap_s"]),
        "out_tail_s": float(cleaning["out_tail_s"]),
        "total_pump_timeline_s": sum(float(cleaning[key]) for key in (
            "water_lead_s", "overlap_s", "out_tail_s")),
        "state": state,
    }


@function("motors.select", "Select a motor within its relay group", GROUP,
          params=[_MOTOR_PARAM])
def select(ctx: Context, motor: int) -> Dict[str, Any]:
    _check_motor(ctx.config, motor)
    _reject_during_cleaning(ctx)
    _reject_during_weight_dose()
    ctx.link.send(f"S,{motor}")
    return ctx.link.refresh_state()["motors"]


@function("motors.run", "Run continuously at the requested flow, opening any linked valve before starting", GROUP,
          params=[_MOTOR_PARAM, _FLOW_PARAM,
                  Param("direction", "integer", default=1, choices=[1, -1],
                        label="Direction")])
def run(ctx: Context, motor: int, flow_ml_min: float, direction: int) -> Dict[str, Any]:
    cfg = ctx.config
    _check_motor(cfg, motor)
    _reject_during_cleaning(ctx)
    _reject_during_weight_dose()
    _check_flow(cfg, flow_ml_min, motor)
    sps = flow_to_sps(cfg, flow_ml_min, motor) * (1 if direction > 0 else -1)
    ctx.link.send(f"V,{motor},{sps}")
    return {"motor": motor, "sps": sps, "flow_ml_min": flow_ml_min,
            "pulses_per_ml": motor_pulses_per_ml(cfg, motor),
            "valve_sequence": _valve_sequence(cfg, motor),
            "state": ctx.link.refresh_state()["motors"]}


@function("motors.stop", "Stop a motor and close its linked valve after the configured delay", GROUP, params=[_MOTOR_PARAM])
def stop(ctx: Context, motor: int) -> Dict[str, Any]:
    _check_motor(ctx.config, motor)
    _reject_during_cleaning(ctx)
    _cancel_weight_dose(motor)
    ctx.link.send(f"V,{motor},0")
    return ctx.link.refresh_state()["motors"]


@function("motors.dose", "Dispense a specified volume with automatic valve sequencing", GROUP,
          params=[_MOTOR_PARAM,
                  Param("volume_ml", "number", label="Volume", unit="mL",
                        minimum=0.01, maximum=1000),
                  Param("flow_ml_min", "number", label="Legacy flow rate (configured dose rate takes precedence)",
                        unit="mL/min", minimum=0.01, required=False),
                  Param("direction", "integer", default=1, choices=[1, -1],
                        label="Direction")])
def dose(ctx: Context, motor: int, volume_ml: float,
         flow_ml_min: float | None, direction: int) -> Dict[str, Any]:
    cfg = ctx.config
    _check_motor(cfg, motor)
    _reject_during_cleaning(ctx)
    _reject_during_weight_dose()
    # Dosing always uses the configured fixed flow. The legacy flow_ml_min argument
    # is accepted for compatibility but cannot override the shared pump-speed setting.
    flow_ml_min = float(cfg["motors"].get(
        "dose_flow_ml_min", cfg["motors"]["default_flow_ml_min"]))
    _check_flow(cfg, flow_ml_min, motor)
    steps = volume_to_steps(cfg, volume_ml, motor) * (1 if direction > 0 else -1)
    sps = flow_to_sps(cfg, flow_ml_min, motor)
    sequence = _valve_sequence(cfg, motor)
    ctx.link.send(f"P,{motor},{steps},{sps}")
    return {"motor": motor, "steps": steps, "sps": sps,
            "flow_ml_min": flow_ml_min,
            "pulses_per_ml": motor_pulses_per_ml(cfg, motor),
            "dose_pulses_per_ml": motor_dose_pulses_per_ml(cfg, motor),
            "eta_s": round(abs(steps) / max(abs(sps), 1)
                           + ((sequence["open_before_ms"] + sequence["close_after_ms"])
                              / 1000.0 if sequence else 0.0), 1),
            "valve_sequence": sequence,
            "state": ctx.link.refresh_state()["motors"]}


@function("motors.dose_by_weight",
          "Dispense to a target mass relative to the initial scale baseline",
          GROUP,
          params=[_MOTOR_PARAM,
                  Param("target_g", "number", label="Target mass", unit="g",
                        minimum=0.01, maximum=1000, required=False),
                  # Retain volume_ml and flow_ml_min for older clients. The current interface sends
                  # target_g, and hardware.yaml determines the coarse flow rate.
                  Param("volume_ml", "number", label="Target volume", unit="mL",
                        minimum=0.01, maximum=1000, required=False),
                  Param("flow_ml_min", "number", label="Legacy coarse flow rate", unit="mL/min",
                        minimum=0.01, required=False),
                  Param("direction", "integer", default=1, choices=[1, -1],
                        label="Direction")])
def dose_by_weight(ctx: Context, motor: int, target_g: float | None,
                   volume_ml: float | None, flow_ml_min: float | None,
                   direction: int) -> Dict[str, Any]:
    """Dose using mass feedback, excluding liquid used to fill an initially dry line."""
    global _weight_cancel, _weight_motor

    cfg = ctx.config
    _check_motor(cfg, motor)
    _reject_during_cleaning(ctx)
    grav = cfg["motors"].get("gravimetric_dose", {})
    if not grav.get("enabled", False):
        raise FunctionError("Gravimetric dosing is not enabled in hardware.yaml")
    if target_g is None:
        if volume_ml is None:
            raise FunctionError("Gravimetric dosing requires target_g, or volume_ml for legacy clients")
        target_g = float(volume_ml) * float(grav["density_g_ml"])
    target_g = float(target_g)
    if not 0 < target_g <= 1000:
        raise FunctionError("Target mass must be greater than 0 and at most 1000 g")
    if not _weight_dose_lock.acquire(blocking=False):
        active = _weight_session_active()
        raise FunctionError(f"Motor {active} is already using the scale for dosing")

    cancel = threading.Event()
    with _weight_session_lock:
        _weight_cancel = cancel
        _weight_motor = motor

    tolerance_percent = float(grav["tolerance_percent"])
    tolerance_g = target_g * tolerance_percent / 100.0
    lower_g, upper_g = target_g - tolerance_g, target_g + tolerance_g
    ready_timeout = float(grav["scale_ready_timeout_s"])
    # All three dosing phases use configured flow rates and shared pulses_per_ml.
    # Motors 2-9 therefore use identical step rates; legacy flow_ml_min is ignored.
    coarse_flow = float(grav["fast_flow_ml_min"])
    approach_flow = min(coarse_flow, float(grav["approach_flow_ml_min"]))
    trim_flow = min(approach_flow, float(grav["trim_flow_ml_min"]))
    _check_flow(cfg, coarse_flow, motor)
    slow_zone = min(float(grav["slow_zone_g"]), target_g * 0.25)
    trim_zone = min(float(grav["trim_zone_g"]), target_g * 0.05)
    sign = 1 if direction > 0 else -1
    started = time.monotonic()
    phase = "priming"
    running = False
    final_samples: List[Dict[str, Any]] = []
    baseline_g = 0.0
    peak_gain_g = 0.0
    pump_started = 0.0
    liquid_detected_s: float | None = None

    try:
        scale_state = ctx.link.snapshot()["scale"]
        wanted_hz = int(cfg["scale"]["stream_hz"])
        if int(scale_state.get("stream_hz") or 0) != wanted_hz:
            ctx.link.send(f"W,STREAM,{wanted_hz}")
            ctx.link.refresh_state()

        baseline_samples = _collect_weights(
            ctx.link, int(grav["baseline_samples"]), ready_timeout, cancel)
        baseline_g = _median_grams(baseline_samples)

        coarse_sps = flow_to_sps(cfg, coarse_flow, motor) * sign
        approach_sps = flow_to_sps(cfg, approach_flow, motor) * sign
        trim_sps = flow_to_sps(cfg, trim_flow, motor) * sign
        ctx.link.send(f"V,{motor},{coarse_sps}")
        running = True
        pump_started = time.monotonic()

        recent: deque[float] = deque(maxlen=3)
        last_sequence = int(baseline_samples[-1].get("sequence") or 0)
        operation_deadline = pump_started + float(grav["timeout_s"])
        progress_gain_g = 0.0
        last_progress_at = pump_started
        minimum_gain_g = float(grav["minimum_gain_g"])
        no_gain_timeout_s = float(grav["no_gain_timeout_s"])
        while True:
            if time.monotonic() >= operation_deadline:
                raise FunctionError(
                    f"Gravimetric dosing exceeded {float(grav['timeout_s']):g} s without reaching the target; the pump has been stopped")
            sample, last_sequence = _new_weight_sample(
                ctx.link, last_sequence, ready_timeout, cancel)
            gain = float(sample["grams"]) - baseline_g
            recent.append(gain)
            measured_gain = float(statistics.median(recent))
            peak_gain_g = max(peak_gain_g, measured_gain)
            remaining = target_g - measured_gain

            if (liquid_detected_s is None
                    and peak_gain_g >= minimum_gain_g):
                liquid_detected_s = time.monotonic() - pump_started
                phase = "coarse"

            if measured_gain >= progress_gain_g + minimum_gain_g:
                progress_gain_g = measured_gain
                last_progress_at = time.monotonic()

            if measured_gain >= lower_g:
                break
            if time.monotonic() - last_progress_at >= no_gain_timeout_s:
                raise FunctionError(
                    f"Mass has not increased for {no_gain_timeout_s:g} s; check tubing, pump direction, and scale placement")
            if remaining <= trim_zone and phase != "trim":
                ctx.link.send(f"V,{motor},{trim_sps}")
                phase = "trim"
            elif remaining <= slow_zone and phase == "coarse":
                ctx.link.send(f"V,{motor},{approach_sps}")
                phase = "approach"

        ctx.link.send(f"V,{motor},0")
        running = False
        settle_s = float(grav["settle_s"])
        settle_deadline = time.monotonic() + settle_s
        while time.monotonic() < settle_deadline:
            if cancel.is_set():
                raise _DoseCancelled()
            time.sleep(0.02)

        final_samples = _collect_weights(
            ctx.link, int(grav["final_samples"]), ready_timeout, cancel)
        dispensed_g = _median_grams(final_samples) - baseline_g

        # If settled mass falls below the lower bound, top up at the trim flow rate.
        topups = 0
        while dispensed_g < lower_g:
            if time.monotonic() >= operation_deadline:
                raise FunctionError(
                    f"Gravimetric dosing exceeded {float(grav['timeout_s']):g} s without reaching the target; the pump has been stopped")
            topups += 1
            ctx.link.send(f"V,{motor},{trim_sps}")
            running = True
            last_sequence = int(final_samples[-1].get("sequence") or 0)
            last_progress_at = time.monotonic()
            progress_gain_g = dispensed_g
            while True:
                if time.monotonic() >= operation_deadline:
                    raise FunctionError(
                        f"Gravimetric dosing exceeded {float(grav['timeout_s']):g} s without reaching the target; the pump has been stopped")
                sample, last_sequence = _new_weight_sample(
                    ctx.link, last_sequence, ready_timeout, cancel)
                gain = float(sample["grams"]) - baseline_g
                if gain >= lower_g:
                    break
                if gain >= progress_gain_g + minimum_gain_g:
                    progress_gain_g = gain
                    last_progress_at = time.monotonic()
                if time.monotonic() - last_progress_at >= no_gain_timeout_s:
                    raise FunctionError(
                        f"Mass did not increase for {no_gain_timeout_s:g} s during top-up; the pump has been stopped")
            ctx.link.send(f"V,{motor},0")
            running = False
            time.sleep(settle_s)
            final_samples = _collect_weights(
                ctx.link, int(grav["final_samples"]), ready_timeout, cancel)
            dispensed_g = _median_grams(final_samples) - baseline_g

        error_g = dispensed_g - target_g
        within = lower_g <= dispensed_g <= upper_g
        return {
            "motor": motor,
            "mode": "gravimetric",
            "valve_sequence": _valve_sequence(cfg, motor),
            "volume_ml": volume_ml,
            "density_g_ml": (float(grav["density_g_ml"])
                               if volume_ml is not None else None),
            "target_g": round(target_g, 4),
            "tolerance_g": tolerance_g,
            "tolerance_percent": tolerance_percent,
            "fast_flow_ml_min": coarse_flow,
            "approach_flow_ml_min": approach_flow,
            "trim_flow_ml_min": trim_flow,
            "fast_sps": abs(coarse_sps),
            "approach_sps": abs(approach_sps),
            "trim_sps": abs(trim_sps),
            "liquid_detected_s": (round(liquid_detected_s, 2)
                                    if liquid_detected_s is not None else None),
            "baseline_g": round(baseline_g, 4),
            "dispensed_g": round(dispensed_g, 4),
            "error_g": round(error_g, 4),
            "within_tolerance": within,
            "stable": all(bool(s.get("stable")) for s in final_samples),
            "topups": topups,
            "elapsed_s": round(time.monotonic() - started, 2),
            "warning": None if within else (
                "Final mass is outside tolerance; reduce trim flow, isolate scale vibration, and recalibrate"),
            "state": ctx.link.refresh_state()["motors"],
        }
    except _DoseCancelled as exc:
        raise FunctionError("Gravimetric dosing was stopped by the user") from exc
    finally:
        if running:
            _stop_quietly(ctx.link, motor)
        else:
            # V,motor,0 is a no-op for an unselected motor, including failures before startup.
            _stop_quietly(ctx.link, motor)
        with _weight_session_lock:
            if _weight_cancel is cancel:
                _weight_cancel = None
                _weight_motor = None
        _weight_dose_lock.release()


@function("motors.stop_all", "Stop motors, the syringe pump, and scale streaming, and close all valves", GROUP, danger=True)
def stop_all(ctx: Context) -> Dict[str, Any]:
    _cancel_weight_dose()
    # X stops all actuators. Explicitly stop weight streaming as well so older
    # Arduino firmware also stops polling the scale.
    ctx.link.send("X")
    ctx.link.send("W,STREAM,0")
    return ctx.link.refresh_state()
