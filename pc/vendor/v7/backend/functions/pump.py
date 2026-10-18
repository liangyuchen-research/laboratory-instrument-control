"""syringe pump(RUNZE SY-01B).

Walk Arduino of Serial1(D18 / D19)+ MAX13487 automatically sends and receives RS485 adapter board,
ASCII(DT)agreement,9600 baud, address 0.full stroke 12000 step.

Users only want to speak µL and mL/s,So the conversion of volume and speed is done here.:
    steps     = µL × 12000 ÷ Syringe capacity(µL)
    original speed = mL/s × 12000 ÷ Syringe capacity(mL)
will be handed over to the firmware after replacement. Y, instruction.
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
    """raw speed cap 6000 converted to mL/s."""
    return round(_syringe_ul(ctx) / 1000.0 * 6000.0 / _stroke(ctx), 3)


def ul_to_steps(ctx: Context, ul: float) -> int:
    return round(ul * _stroke(ctx) / _syringe_ul(ctx))


@function("pump.status", "Syringe pump current connection status and conversion parameters", GROUP)
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


@function("pump.detect", "Detect whether the syringe pump is online", GROUP)
def detect(ctx: Context) -> Dict[str, Any]:
    lines = ctx.link.send("Y,DETECT")
    return {"online": any(l.startswith("FOUND") for l in lines), "lines": lines}


@function("pump.init", "initialization / Return to zero(Must be done when using it for the first time or when it gets stuck.)", GROUP, danger=True)
def init(ctx: Context) -> Dict[str, Any]:
    return {"lines": ctx.link.send("Y,INIT")}


@function("pump.set_syringe", "Set syringe capacity,after µL shall be used for conversion.", GROUP,
          params=[Param("volume_ul", "integer", label="Syringe capacity", unit="µL")])
def set_syringe(ctx: Context, volume_ul: int) -> Dict[str, Any]:
    options = ctx.config["syringe_pump"]["syringe_options_ul"]
    if volume_ul not in options:
        raise FunctionError(f"syringe capacity only supports {options} µL")
    lines = ctx.link.send(f"Y,SYR,{volume_ul}")
    ctx.link.refresh_state()
    return {"syringe_ul": volume_ul, "max_speed_ml_s": max_speed_ml_s(ctx),
            "lines": lines}


@function("pump.set_valve", "Switch the diverter valve position on the pump head", GROUP,
          params=[Param("position", "integer", label="Valve position", minimum=1, maximum=15)])
def set_valve(ctx: Context, position: int) -> Dict[str, Any]:
    top = int(ctx.config["syringe_pump"]["valve_positions"])
    if position > top:
        raise FunctionError(f"This pump only has {top} valve positions")
    return {"position": position, "lines": ctx.link.send(f"Y,VALVE,{position}")}


@function("pump.set_speed", "Set push speed(mL/s,will be converted into the original speed of the pump)", GROUP,
          params=[Param("speed_ml_s", "number", label="speed", unit="mL/s",
                        minimum=0.001)])
def set_speed(ctx: Context, speed_ml_s: float) -> Dict[str, Any]:
    top = max_speed_ml_s(ctx)
    if speed_ml_s > top:
        raise FunctionError(f"The current upper speed limit of the syringe is {top} mL/s")
    raw = max(1, min(6000, round(speed_ml_s * _stroke(ctx) / (_syringe_ul(ctx) / 1000.0))))
    return {"speed_ml_s": speed_ml_s, "raw": raw,
            "lines": ctx.link.send(f"Y,SPEED,{raw}")}


def _move(ctx: Context, kind: str, volume_ul: float) -> Dict[str, Any]:
    syringe = _syringe_ul(ctx)
    if not 0 < volume_ul <= syringe:
        raise FunctionError(f"Volume must be between 0 and syringe capacity {syringe} µL between")
    steps = ul_to_steps(ctx, volume_ul)
    if not 1 <= steps <= _stroke(ctx):
        raise FunctionError("The converted number of steps exceeds the stroke range")
    lines = ctx.link.send(f"Y,{kind},{int(volume_ul)}")
    return {"volume_ul": volume_ul, "steps": steps, "lines": lines}


@function("pump.aspirate", "Inhale the specified volume", GROUP,
          params=[Param("volume_ul", "number", label="volume", unit="µL", minimum=1)])
def aspirate(ctx: Context, volume_ul: float) -> Dict[str, Any]:
    return _move(ctx, "ASPUL", volume_ul)


@function("pump.dispense", "launches the specified volume", GROUP,
          params=[Param("volume_ul", "number", label="volume", unit="µL", minimum=1)])
def dispense(ctx: Context, volume_ul: float) -> Dict[str, Any]:
    return _move(ctx, "DISPUL", volume_ul)


@function("pump.stop", "Stop the syringe pump immediately", GROUP, danger=True)
def stop(ctx: Context) -> Dict[str, Any]:
    return {"lines": ctx.link.send("Y,STOP")}
