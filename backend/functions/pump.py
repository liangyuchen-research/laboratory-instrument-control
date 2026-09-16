"""RUNZE SY-01B syringe pump control.

The pump uses Arduino Serial1 (D18/D19), a MAX13487 automatic-direction RS485
adapter, the ASCII (DT) protocol, 9600 baud, and address 0. A full stroke is
12000 steps. This module converts user-facing volume and speed to device units:

    steps = volume_uL * 12000 / syringe_capacity_uL
    raw_speed = speed_mL_s * 12000 / syringe_capacity_mL

The resulting requests are sent through the firmware Y commands.
"""
from __future__ import annotations

from typing import Any, Dict

from ..registry import Context, FunctionError, Param, function

GROUP = "pump"


def _syringe_ul(ctx: Context) -> int:
    value = ctx.link.snapshot()["pump"].get("syringe_ul")
    return int(value or ctx.config["syringe_pump"]["default_syringe_ul"])


def _stroke(ctx: Context) -> int:
    return int(ctx.config["syringe_pump"]["full_stroke_steps"])


def max_speed_ml_s(ctx: Context) -> float:
    """Convert the raw speed limit of 6000 to mL/s."""
    return round(_syringe_ul(ctx) / 1000.0 * 6000.0 / _stroke(ctx), 3)


def ul_to_steps(ctx: Context, ul: float) -> int:
    return round(ul * _stroke(ctx) / _syringe_ul(ctx))


@function("pump.status", "Read syringe pump connection status and conversion settings", GROUP)
def status(ctx: Context) -> Dict[str, Any]:
    cfg = ctx.config["syringe_pump"]
    pump = ctx.link.snapshot()["pump"]
    return {
        "online": pump["online"],
        "syringe_ul": _syringe_ul(ctx),
        "syringe_options_ul": cfg["syringe_options_ul"],
        "full_stroke_steps": _stroke(ctx),
        "valve_positions": cfg["valve_positions"],
        "max_speed_ml_s": max_speed_ml_s(ctx),
        "serial": {"port": cfg["serial"], "tx": cfg["tx_pin"],
                   "rx": cfg["rx_pin"], "baud": cfg["baud"], "address": cfg["address"]},
    }


@function("pump.detect", "Detect the syringe pump", GROUP)
def detect(ctx: Context) -> Dict[str, Any]:
    lines = ctx.link.send("Y,DETECT")
    return {"online": any(l.startswith("FOUND") for l in lines), "lines": lines}


@function("pump.init", "Initialize and home the syringe pump before first use or after a stall", GROUP, danger=True)
def init(ctx: Context) -> Dict[str, Any]:
    return {"lines": ctx.link.send("Y,INIT")}


@function("pump.set_syringe", "Set the syringe capacity used for subsequent volume conversions", GROUP,
          params=[Param("volume_ul", "integer", label="Syringe capacity", unit="µL")])
def set_syringe(ctx: Context, volume_ul: int) -> Dict[str, Any]:
    options = ctx.config["syringe_pump"]["syringe_options_ul"]
    if volume_ul not in options:
        raise FunctionError(f"Supported syringe capacities are {options} uL")
    lines = ctx.link.send(f"Y,SYR,{volume_ul}")
    ctx.link.refresh_state()
    return {"syringe_ul": volume_ul, "max_speed_ml_s": max_speed_ml_s(ctx),
            "lines": lines}


@function("pump.set_valve", "Select the syringe pump distribution valve position", GROUP,
          params=[Param("position", "integer", label="Valve position", minimum=1, maximum=15)])
def set_valve(ctx: Context, position: int) -> Dict[str, Any]:
    top = int(ctx.config["syringe_pump"]["valve_positions"])
    if position > top:
        raise FunctionError(f"This pump supports {top} valve positions")
    return {"position": position, "lines": ctx.link.send(f"Y,VALVE,{position}")}


@function("pump.set_speed", "Set delivery speed in mL/s, converted to native pump units", GROUP,
          params=[Param("speed_ml_s", "number", label="Speed", unit="mL/s",
                        minimum=0.001)])
def set_speed(ctx: Context, speed_ml_s: float) -> Dict[str, Any]:
    top = max_speed_ml_s(ctx)
    if speed_ml_s > top:
        raise FunctionError(f"The current syringe supports a maximum speed of {top} mL/s")
    raw = max(1, min(6000, round(speed_ml_s * _stroke(ctx) / (_syringe_ul(ctx) / 1000.0))))
    return {"speed_ml_s": speed_ml_s, "raw": raw,
            "lines": ctx.link.send(f"Y,SPEED,{raw}")}


def _move(ctx: Context, kind: str, volume_ul: float) -> Dict[str, Any]:
    syringe = _syringe_ul(ctx)
    if not 0 < volume_ul <= syringe:
        raise FunctionError(f"Volume must be greater than 0 and at most the syringe capacity of {syringe} uL")
    steps = ul_to_steps(ctx, volume_ul)
    if not 1 <= steps <= _stroke(ctx):
        raise FunctionError("Converted step count exceeds the pump stroke")
    lines = ctx.link.send(f"Y,{kind},{int(volume_ul)}")
    return {"volume_ul": volume_ul, "steps": steps, "lines": lines}


@function("pump.aspirate", "Aspirate the requested volume", GROUP,
          params=[Param("volume_ul", "number", label="Volume", unit="µL", minimum=1)])
def aspirate(ctx: Context, volume_ul: float) -> Dict[str, Any]:
    return _move(ctx, "ASPUL", volume_ul)


@function("pump.dispense", "Dispense the requested volume", GROUP,
          params=[Param("volume_ul", "number", label="Volume", unit="µL", minimum=1)])
def dispense(ctx: Context, volume_ul: float) -> Dict[str, Any]:
    return _move(ctx, "DISPUL", volume_ul)


@function("pump.stop", "Stop the syringe pump immediately", GROUP, danger=True)
def stop(ctx: Context) -> Dict[str, Any]:
    return {"lines": ctx.link.send("Y,STOP")}
