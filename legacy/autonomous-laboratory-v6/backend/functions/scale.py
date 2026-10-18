"""RS485 scale transmitter interface using Modbus RTU.

The transmitter connects through Arduino Serial2 (D16/D17), with DE and RE
tied together on D36. Firmware streams measurements at the configured rate.
This module exposes cached data and configuration operations. The transport
reader records measurements and forwards them to the browser over SSE.
A backend ring buffer preserves recent measurements across browser refreshes.
"""
from __future__ import annotations

import io
import json
import math
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List

from ..registry import Context, FunctionError, Param, function

GROUP = "scale"

_history: Deque[Dict[str, Any]] = deque(maxlen=6000)
_last_seen: float = 0.0
_calibration_lock = threading.Lock()
_DEFAULT_CALIBRATION_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "scale_calibration.json"
)
_calibration_status: Dict[str, Any] = {
    "attempted": False,
    "file_exists": False,
    "loaded": False,
    "offset_loaded": False,
    "message": "Calibration has not yet been loaded",
}


def calibration_path() -> Path:
    """Locate the calibration file; an environment override isolates test output."""
    custom = os.environ.get("SCALE_CALIBRATION_FILE")
    return Path(custom) if custom else _DEFAULT_CALIBRATION_PATH


def calibration_status() -> Dict[str, Any]:
    """Expose calibration persistence and restore status to the startup interface."""
    return {**_calibration_status, "file": str(calibration_path())}


def _device_calibration(link: Any) -> Dict[str, float]:
    """Read the applied offset and divisor from firmware rather than saving approximations."""
    lines = link.send("W,CFG")
    for line in reversed(lines):
        if not line.startswith("CFG,"):
            continue
        fields: Dict[str, str] = {}
        for item in line[4:].split(","):
            key, sep, value = item.partition("=")
            if sep:
                fields[key] = value
        try:
            offset = float(fields["offset"])
            divisor = float(fields["div"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FunctionError("Firmware returned malformed scale calibration data") from exc
        if not math.isfinite(offset) or not math.isfinite(divisor) or divisor == 0:
            raise FunctionError("Firmware returned invalid scale calibration values")
        return {"offset": offset, "divisor": divisor}
    raise FunctionError("Firmware did not return scale calibration data")


def _read_saved_calibration(path: Path | None = None) -> Dict[str, Any] | None:
    target = path or calibration_path()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        offset = float(data["offset"])
        divisor = float(data["divisor"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FunctionError(f"Could not read scale calibration file: {target}") from exc
    if not math.isfinite(offset) or not math.isfinite(divisor) or divisor == 0:
        raise FunctionError(f"Invalid scale calibration file: {target}")
    data["offset"] = offset
    data["divisor"] = divisor
    return data


def _save_device_calibration(link: Any,
                             reference_grams: float | None = None) -> Dict[str, Any]:
    values = _device_calibration(link)
    target = calibration_path()
    try:
        previous = _read_saved_calibration(target)
    except FunctionError:
        # A valid new calibration can replace a corrupted previous file.
        previous = None
    if reference_grams is None and previous is not None:
        reference_grams = previous.get("reference_grams")
    payload: Dict[str, Any] = {
        "version": 1,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "offset": values["offset"],
        "divisor": values["divisor"],
        "reference_grams": reference_grams,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise FunctionError(f"Calibration was applied but could not be saved: {target}") from exc
    _calibration_status.update({
        "attempted": True,
        "file_exists": True,
        "loaded": True,
        "offset_loaded": True,
        "message": "Calibration settings saved",
    })
    return {**payload, "file": str(target)}


def restore_calibration(link: Any) -> Dict[str, Any] | None:
    """Restore the saved calibration to Arduino RAM during backend startup."""
    target = calibration_path()
    _calibration_status.update({
        "attempted": True,
        "file_exists": target.exists(),
        "loaded": False,
        "offset_loaded": False,
        "message": "Loading calibration file",
    })
    saved = _read_saved_calibration()
    if saved is None:
        _calibration_status["message"] = "No calibration file found; one will be created after calibration"
        return None

    # Restore the divisor first because it defines the measurement scale. Older
    # firmware may reject OFFSET; sending DIV first preserves the main calibration.
    link.send(f"W,DIV,{saved['divisor']:.9g}")
    offset_loaded = saved["offset"] == 0
    warning = None
    if not offset_loaded:
        try:
            link.send(f"W,OFFSET,{saved['offset']:.9g}")
            offset_loaded = True
        except Exception as exc:
            warning = f"Calibration divisor restored, but offset could not be restored: {exc}"

    message = "Saved calibration restored" if offset_loaded else warning
    _calibration_status.update({
        "attempted": True,
        "file_exists": True,
        "loaded": True,
        "offset_loaded": offset_loaded,
        "message": message,
    })
    return {**saved, "file": str(target), "offset_loaded": offset_loaded,
            "warning": warning}


def record(sample: Dict[str, Any]) -> None:
    """Called by transport for each incoming WEIGHT sample."""
    global _last_seen
    _last_seen = sample.get("updated", time.time())
    _history.append({"t": _last_seen, "g": sample.get("grams"),
                     "raw": sample.get("raw"), "stable": bool(sample.get("stable"))})


def history(limit: int) -> List[Dict[str, Any]]:
    items = list(_history)
    return items[-limit:] if limit and limit < len(items) else items


def clear_history() -> None:
    _history.clear()


def _stats(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    values = [p["g"] for p in points if isinstance(p["g"], (int, float))]
    if not values:
        return {"count": 0, "min": None, "max": None, "span": None, "avg": None}
    return {"count": len(values), "min": min(values), "max": max(values),
            "span": max(values) - min(values), "avg": sum(values) / len(values)}


@function("scale.read", "Read current mass, statistics, and measurement history", GROUP,
          params=[Param("points", "integer", required=False, default=300,
                        minimum=0, maximum=6000, label="History points")])
def read(ctx: Context, points: int) -> Dict[str, Any]:
    scale = ctx.link.snapshot()["scale"]
    series = history(points)
    fresh = bool(scale.get("updated")) and (time.time() - scale["updated"] < 3.0)
    return {
        "grams": scale.get("grams"),
        "raw": scale.get("raw"),
        "stable": scale.get("stable"),
        "online": fresh,
        "stream_hz": scale.get("stream_hz"),
        "updated": scale.get("updated"),
        "stats": _stats(series),
        "series": series,
    }


@function("scale.tare", "Tare the scale with an empty container", GROUP)
def tare(ctx: Context) -> Dict[str, Any]:
    with _calibration_lock:
        lines = ctx.link.send("W,TARE")
        calibration = _save_device_calibration(ctx.link)
    clear_history()
    return {"lines": lines, "calibration": calibration}


@function("scale.calibrate", "Calibrate using a reference mass", GROUP,
          params=[Param("grams", "number", label="Reference mass", unit="g",
                        minimum=0.1, maximum=20000)])
def calibrate(ctx: Context, grams: float) -> Dict[str, Any]:
    with _calibration_lock:
        lines = ctx.link.send(f"W,CAL,{grams:g}")
        calibration = _save_device_calibration(ctx.link, reference_grams=grams)
    clear_history()
    return {"grams": grams, "lines": lines, "calibration": calibration}


@function("scale.set_stream", "Set the measurement stream frequency (0 stops streaming)", GROUP,
          params=[Param("hz", "integer", label="Frequency", unit="Hz",
                        minimum=0, maximum=20)])
def set_stream(ctx: Context, hz: int) -> Dict[str, Any]:
    lines = ctx.link.send(f"W,STREAM,{hz}")
    ctx.link.refresh_state()
    return {"hz": hz, "lines": lines}


@function("scale.restart", "Restart streaming after missing readings or an automatic stop", GROUP)
def restart(ctx: Context) -> Dict[str, Any]:
    hz = int(ctx.config["scale"]["stream_hz"])
    return set_stream(ctx, hz)


@function("scale.clear", "Clear history and statistics without changing calibration", GROUP)
def clear(ctx: Context) -> Dict[str, Any]:
    clear_history()
    return {"cleared": True}


@function("scale.csv", "Export measurement history as CSV", GROUP,
          params=[Param("points", "integer", required=False, default=0,
                        minimum=0, maximum=6000, label="Points (0 = all)")])
def csv(ctx: Context, points: int) -> Dict[str, Any]:
    buf = io.StringIO()
    buf.write("time,weight_g,raw,stable\n")
    for row in history(points):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(row["t"]))
        buf.write(f"{stamp},{row['g']},{row['raw']},{1 if row['stable'] else 0}\n")
    return {"filename": time.strftime("weight_%Y%m%d_%H%M%S.csv"),
            "content": buf.getvalue()}


# --- Advanced diagnostic operations are available through the API and scripts. -----------
@function("scale.diagnose", "Run a serial loopback test, passive listen, or Modbus scan", GROUP,
          params=[Param("mode", "enum", choices=["loop", "sniff", "scan"],
                        label="Mode")])
def diagnose(ctx: Context, mode: str) -> Dict[str, Any]:
    # Firmware diagnostics block normal command handling. Require idle pumps.
    from .motors import _weight_session_active
    state = ctx.link.snapshot()
    if (state.get("cleaning") or _weight_session_active() is not None
            or any(slot.get("state") not in (None, "idle")
                   for slot in state.get("motors", {}).values())):
        raise FunctionError("Stop peristaltic pumps and cleaning before scale diagnostics")
    cmd = {"loop": "W,LOOP", "sniff": "W,SNIFF,3", "scan": "W,SCAN"}[mode]
    return {"mode": mode, "lines": ctx.link.send(cmd)}


@function("scale.modbus", "Set Modbus address, function code, register, and data format", GROUP,
          params=[Param("address", "integer", minimum=1, maximum=247, label="Device address"),
                  Param("function_code", "integer", choices=[3, 4], label="Function code"),
                  Param("register", "integer", minimum=0, label="Register"),
                  Param("format", "integer", choices=[0, 1, 2, 3, 4], label="Data format")])
def modbus(ctx: Context, address: int, function_code: int,
           register: int, format: int) -> Dict[str, Any]:
    lines = ctx.link.send(f"W,SET,{address},{function_code},{register},{format}")
    clear_history()
    return {"lines": lines}
