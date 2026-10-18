"""relay + peristaltic pump(stepper motor).

A group of four motors share one DM542J,consists of four MY4N-GS relay switching.
switch is a ”cold switch”:Free the drive first → Relay is all off → Connect target → Re-energize,
Therefore, the contact will not open and close when current is flowing. This sequence is in the firmware,is only responsible here
Change the user’s ”mL/min”“mL”is translated into the ”steps” required by the firmware/seconds” ”number of steps”.

A group and B groups each have their own drive,can transfer one of each at the same time.
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
# conversion - coefficients all come from hardware.yaml,Don’t write it to death
# ---------------------------------------------------------------------
def motor_pulses_per_ml(cfg: Dict[str, Any], motor: int) -> float:
    """Speed conversion coefficient; all motors must be the same to ensure the same mL/min corresponds to the same cadence."""
    for group in cfg["motors"]["groups"]:
        for channel in group["channels"]:
            if int(channel["id"]) == int(motor):
                return float(channel.get(
                    "pulses_per_ml", cfg["motors"]["pulses_per_ml"]))
    raise FunctionError(f"No number {motor} motor")


def motor_dose_pulses_per_ml(cfg: Dict[str, Any], motor: int) -> float:
    """Quantitative total steps coefficient; shared by all motors to ensure the same rotation time for the same volume."""
    for group in cfg["motors"]["groups"]:
        for channel in group["channels"]:
            if int(channel["id"]) == int(motor):
                return float(channel.get(
                    "dose_pulses_per_ml", cfg["motors"]["dose_pulses_per_ml"]))
    raise FunctionError(f"No number {motor} motor")


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
    raise FunctionError(f"No number {motor} motor")


def _check_flow(cfg: Dict[str, Any], ml_min: float, motor: int | None = None) -> float:
    top = float(cfg["motors"]["max_flow_ml_min"])
    if not 0 < ml_min <= top:
        raise FunctionError(f"flow must be between 0 and {top} mL/min between")
    sps = flow_to_sps(cfg, ml_min, motor)
    if sps > float(cfg["motors"]["max_sps"]):
        raise FunctionError(f"{ml_min} mL/min after conversion is {sps} step/Second,exceeds the drive upper limit")
    return ml_min


def _valve_sequence(cfg: Dict[str, Any], motor: int) -> Dict[str, Any] | None:
    """Automatic valve linkage of return motor; channel return without linkage None."""
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
        raise FunctionError(f"motor {motor} is weighing and quantifying; please stop or wait for it to complete")


def _reject_during_cleaning(ctx: Context) -> None:
    if bool(ctx.link.snapshot().get("cleaning")):
        raise FunctionError("Automatic cleaning is being executed; please wait for completion or click ”Stop All”")


def _cancel_weight_dose(motor: int | None = None) -> None:
    with _weight_session_lock:
        if _weight_cancel is not None and (motor is None or motor == _weight_motor):
            _weight_cancel.set()


def _new_weight_sample(link: Any, last_sequence: int, timeout_s: float,
                       cancel: threading.Event) -> tuple[Dict[str, Any], int]:
    """is waiting for a real new reading;timeout_s Only this amount is counted, not the entire delivery."""
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
    raise FunctionError("The weight module did not provide a new valid reading and the peristaltic pump has been stopped.")


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


_MOTOR_PARAM = Param("motor", "integer", label="Motor number")
_FLOW_PARAM = Param("flow_ml_min", "number", label="flow", unit="mL/min", minimum=0.01)


# ---------------------------------------------------------------------
@function("motors.list", "Lists all peristaltic pump channels and currently selected motors", GROUP)
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
          "Automatic cleaning: advanced water 30 seconds, simultaneous water supply and drainage 45 seconds, final emptying 45 Second",
          GROUP)
def clean(ctx: Context) -> Dict[str, Any]:
    _reject_during_weight_dose()
    cfg = ctx.config
    before = ctx.link.refresh_state()
    if bool(before.get("cleaning")):
        raise FunctionError("Automatic cleaning is already being executed")
    active = [
        gid for gid, slot in before.get("motors", {}).items()
        if slot.get("state") not in (None, "idle")
    ]
    if active:
        raise FunctionError("There is currently a peristaltic pump running; please stop it before starting cleaning.")

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


@function("motors.select", "switching relay,Select which motor to drive in a certain group", GROUP,
          params=[_MOTOR_PARAM])
def select(ctx: Context, motor: int) -> Dict[str, Any]:
    _check_motor(ctx.config, motor)
    _reject_during_cleaning(ctx)
    _reject_during_weight_dose()
    ctx.link.send(f"S,{motor}")
    return ctx.link.refresh_state()["motors"]


@function("motors.run", "operates continuously at the specified flow rate; the linkage channel will automatically open the valve before starting", GROUP,
          params=[_MOTOR_PARAM, _FLOW_PARAM,
                  Param("direction", "integer", default=1, choices=[1, -1],
                        label="direction")])
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


@function("motors.stop", "Stop the specified motor; the linkage valve will automatically close after a delay", GROUP, params=[_MOTOR_PARAM])
def stop(ctx: Context, motor: int) -> Dict[str, Any]:
    _check_motor(ctx.config, motor)
    _reject_during_cleaning(ctx)
    _cancel_weight_dose(motor)
    ctx.link.send(f"V,{motor},0")
    return ctx.link.refresh_state()["motors"]


@function("motors.dose", "quantitatively delivers the specified volume; the linkage valve will automatically open and close in sequence", GROUP,
          params=[_MOTOR_PARAM,
                  Param("volume_ml", "number", label="volume", unit="mL",
                        minimum=0.01, maximum=1000),
                  Param("flow_ml_min", "number", label="flow (if you don’t fill it in, use the configuration file)",
                        unit="mL/min", minimum=0.01, required=False),
                  Param("direction", "integer", default=1, choices=[1, -1],
                        label="direction")])
def dose(ctx: Context, motor: int, volume_ml: float,
         flow_ml_min: float | None, direction: int) -> Dict[str, Any]:
    cfg = ctx.config
    _check_motor(cfg, motor)
    _reject_during_cleaning(ctx)
    _reject_during_weight_dose()
    # Quantification always uses the fixed speed of the set file. old API incoming flow_ml_min For compatibility still
    # accepted, but no longer covers the fixed value to avoid inconsistent pump speeds at different operating ends.
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
          "Take the moment of pressing as the zero point, and continue to rotate until the specified weight is added.",
          GROUP,
          params=[_MOTOR_PARAM,
                  Param("target_g", "number", label="target weight", unit="g",
                        minimum=0.01, maximum=1000, required=False),
                  # volume_ml and flow_ml_min Reserved for older versions API; new version of front-end direct transmission
                  # target_g,Fast forwarding speed is given by hardware.yaml Unified decision.
                  Param("volume_ml", "number", label="target volume", unit="mL",
                        minimum=0.01, maximum=1000, required=False),
                  Param("flow_ml_min", "number", label="Old version fast transfer traffic", unit="mL/min",
                        minimum=0.01, required=False),
                  Param("direction", "integer", default=1, choices=[1, -1],
                        label="direction")])
def dose_by_weight(ctx: Context, motor: int, target_g: float | None,
                   volume_ml: float | None, flow_ml_min: float | None,
                   direction: int) -> Dict[str, Any]:
    """Direct quantification based on weight feedback; the dry pipe filling amount will not be included in the weight of the delivery container."""
    global _weight_cancel, _weight_motor

    cfg = ctx.config
    _check_motor(cfg, motor)
    _reject_during_cleaning(ctx)
    grav = cfg["motors"].get("gravimetric_dose", {})
    if not grav.get("enabled", False):
        raise FunctionError("hardware.yaml Weighing and quantification has not been enabled yet")
    if target_g is None:
        if volume_ml is None:
            raise FunctionError("Weighing and quantitative requirements target_g (Old versions can also be transferred volume_ml) ")
        target_g = float(volume_ml) * float(grav["density_g_ml"])
    target_g = float(target_g)
    if not 0 < target_g <= 1000:
        raise FunctionError("The target weight must be between 0 and 1000 g between")
    if not _weight_dose_lock.acquire(blocking=False):
        active = _weight_session_active()
        raise FunctionError(f"motor {active} is using the weight module for quantification")

    cancel = threading.Event()
    with _weight_session_lock:
        _weight_cancel = cancel
        _weight_motor = motor

    tolerance_percent = float(grav["tolerance_percent"])
    tolerance_g = target_g * tolerance_percent / 100.0
    lower_g, upper_g = target_g - tolerance_g, target_g + tolerance_g
    ready_timeout = float(grav["scale_ready_timeout_s"])
    # The three-stage speed of the weighing scale is fixed to the set file. mL/min; All share the same pulses_per_ml,
    # so 2-9 will get the exact same cadence. old API of flow_ml_min will be ignored.
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
                    f"The weighing quantity has exceeded {float(grav['timeout_s']):g} The target has not been reached in seconds and the peristaltic pump has been stopped.")
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
                    f"weight has been continuous {no_gain_timeout_s:g} seconds do not continue to rise; please check the pipeline, direction and weighing position")
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

        # If the reading value falls below the lower limit after stopping, use fine acceleration to compensate for the lower limit. When the
        topups = 0
        while dispensed_g < lower_g:
            if time.monotonic() >= operation_deadline:
                raise FunctionError(
                    f"The weighing quantity has exceeded {float(grav['timeout_s']):g} The target has not been reached in seconds and the peristaltic pump has been stopped.")
            topups += 1
            ctx.link.send(f"V,{motor},{trim_sps}")
            running = True
            last_sequence = int(final_samples[-1].get("sequence") or 0)
            last_progress_at = time.monotonic()
            progress_gain_g = dispensed_g
            while True:
                if time.monotonic() >= operation_deadline:
                    raise FunctionError(
                        f"The weighing quantity has exceeded {float(grav['timeout_s']):g} The target has not been reached in seconds and the peristaltic pump has been stopped.")
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
                        f"The weight has been continued during rehydration. Checklist in {no_gain_timeout_s:g} seconds did not continue to rise and the peristaltic pump was stopped.")
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
                "The final weight exceeds the allowable range; please reduce the finishing flow rate, confirm the vibration isolation of the scale platform and recalibrate"),
            "state": ctx.link.refresh_state()["motors"],
        }
    except _DoseCancelled as exc:
        raise FunctionError("Weighing and rationing has been stopped by the user") from exc
    finally:
        if running:
            _stop_quietly(ctx.link, motor)
        else:
            # Even if the exception occurs before startup,V,motor,0 is also safe for unselected motors no-op.
            _stop_quietly(ctx.link, motor)
        with _weight_session_lock:
            if _weight_cancel is cancel:
                _weight_cancel = None
                _weight_motor = None
        _weight_dose_lock.release()


@function("motors.stop_all", "Stop the motor, syringe pump, weight reading and close the solenoid valve", GROUP, danger=True)
def stop_all(ctx: Context) -> Dict[str, Any]:
    _cancel_weight_dose()
    # X will stop all actuators; explicitly send a weight stop again to allow the old version that has not been updated
    # Arduino firmware also correctly stops weight polling.
    ctx.link.send("X")
    ctx.link.send("W,STREAM,0")
    return ctx.link.refresh_state()
