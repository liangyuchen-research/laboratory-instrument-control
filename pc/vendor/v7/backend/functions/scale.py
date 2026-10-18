"""weight module(RS485 weighing transmitter,Modbus RTU).

Walk Arduino of Serial2(D16 / D17),DE and RE short circuit then connect D36.
The firmware boots up as hardware.yaml Continuous reporting of the set frequency,No need to keep asking it ——
So most of the functions of this module are actually ”get cache” and ”download settings”,
The real information is provided by transport is caught and processed. SSE is pushed to the front end.

historical data is retained in the backend(ring buffer),will not drop even if the front end is reorganized.
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
    "message": "No attempt has been made to load the calibration file yet",
}


def calibration_path() -> Path:
    """Calibration file position; test available environment variables are changed to the isolation path."""
    custom = os.environ.get("SCALE_CALIBRATION_FILE")
    return Path(custom) if custom else _DEFAULT_CALIBRATION_PATH


def calibration_status() -> Dict[str, Any]:
    """Displays on the startup screen whether the calibration file has been saved and loaded successfully."""
    return {**_calibration_status, "file": str(calibration_path())}


def _device_calibration(link: Any) -> Dict[str, float]:
    """reads back the actual applied offset/div,Avoid saving approximations of input values."""
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
            raise FunctionError("The format of the weight correction data returned by the firmware is incorrect.") from exc
        if not math.isfinite(offset) or not math.isfinite(divisor) or divisor == 0:
            raise FunctionError("The firmware returned an invalid weight correction value.")
        return {"offset": offset, "divisor": divisor}
    raise FunctionError("Firmware does not return weight correction data")


def _read_saved_calibration(path: Path | None = None) -> Dict[str, Any] | None:
    target = path or calibration_path()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        offset = float(data["offset"])
        divisor = float(data["divisor"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FunctionError(f"The weight correction file cannot be read:{target}") from exc
    if not math.isfinite(offset) or not math.isfinite(divisor) or divisor == 0:
        raise FunctionError(f"The content of the weight correction file is invalid:{target}")
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
        # When the new correction itself is valid, it should be able to directly overwrite the damaged old file.
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
        raise FunctionError(f"Calibration has been applied, but the calibration file cannot be written:{target}") from exc
    _calibration_status.update({
        "attempted": True,
        "file_exists": True,
        "loaded": True,
        "offset_loaded": True,
        "message": "Calibration conditions have been accessed",
    })
    return {**payload, "file": str(target)}


def restore_calibration(link: Any) -> Dict[str, Any] | None:
    """Reapply the last correction value to Enter the actual amount after Arduino RAM."""
    target = calibration_path()
    _calibration_status.update({
        "attempted": True,
        "file_exists": target.exists(),
        "loaded": False,
        "offset_loaded": False,
        "message": "Reading calibration file",
    })
    saved = _read_saved_calibration()
    if saved is None:
        _calibration_status["message"] = "The calibration file was not found. It will be created automatically after the calibration is completed."
        return None

    # The divisor is a necessary value for correcting the measurement ratio and must be sent first. Old version of firmware does not recognize OFFSET,
    # first delivery DIV avoidable OFFSET failed even the main correction values were not loaded at all.
    link.send(f"W,DIV,{saved['divisor']:.9g}")
    offset_loaded = saved["offset"] == 0
    warning = None
    if not offset_loaded:
        try:
            link.send(f"W,OFFSET,{saved['offset']:.9g}")
            offset_loaded = True
        except Exception as exc:
            warning = f"The correction divisor has been loaded, but the zero offset has not yet been loaded:{exc}"

    message = "The last calibration conditions have been loaded" if offset_loaded else warning
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
    """Depend on transport receives every WEIGHT . Only used when"""
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


@function("scale.read", "Current weight, statistics and curve information", GROUP,
          params=[Param("points", "integer", required=False, default=300,
                        minimum=0, maximum=6000, label="curve points")])
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


@function("scale.tare", "Empty cup returns to zero(Peel)", GROUP)
def tare(ctx: Context) -> Dict[str, Any]:
    with _calibration_lock:
        lines = ctx.link.send("W,TARE")
        calibration = _save_device_calibration(ctx.link)
    clear_history()
    return {"lines": lines, "calibration": calibration}


@function("scale.calibrate", "Calibrate after placing a weight of known weight", GROUP,
          params=[Param("grams", "number", label="Weight weight", unit="g",
                        minimum=0.1, maximum=20000)])
def calibrate(ctx: Context, grams: float) -> Dict[str, Any]:
    with _calibration_lock:
        lines = ctx.link.send(f"W,CAL,{grams:g}")
        calibration = _save_device_calibration(ctx.link, reference_grams=grams)
    clear_history()
    return {"grams": grams, "lines": lines, "calibration": calibration}


@function("scale.set_stream", "Set the frequency of continuous reporting(0 = Stop)", GROUP,
          params=[Param("hz", "integer", label="frequency", unit="Hz",
                        minimum=0, maximum=20)])
def set_stream(ctx: Context, hz: int) -> Dict[str, Any]:
    lines = ctx.link.send(f"W,STREAM,{hz}")
    ctx.link.refresh_state()
    return {"hz": hz, "lines": lines}


@function("scale.restart", "Restart continuous reading(cannot read the value and automatically stops.)", GROUP)
def restart(ctx: Context) -> Dict[str, Any]:
    hz = int(ctx.config["scale"]["stream_hz"])
    return set_stream(ctx, hz)


@function("scale.clear", "Clear curves and statistics(Does not affect calibration)", GROUP)
def clear(ctx: Context) -> Dict[str, Any]:
    clear_history()
    return {"cleared": True}


@function("scale.csv", "Export the curve data into CSV text", GROUP,
          params=[Param("points", "integer", required=False, default=0,
                        minimum=0, maximum=6000, label="Points(0 = All)")])
def csv(ctx: Context, points: int) -> Dict[str, Any]:
    buf = io.StringIO()
    buf.write("time,weight_g,raw,stable\n")
    for row in history(points):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(row["t"]))
        buf.write(f"{stamp},{row['g']},{row['raw']},{1 if row['stable'] else 0}\n")
    return {"filename": time.strftime("weight_%Y%m%d_%H%M%S.csv"),
            "content": buf.getvalue()}


# --- Advanced:is used when the module cannot be read.,There are no buttons on the front end,leave to API with script -----------
@function("scale.diagnose", "diagnosis:loop loop test / sniff monitor / scan scan", GROUP,
          params=[Param("mode", "enum", choices=["loop", "sniff", "scan"],
                        label="model")])
def diagnose(ctx: Context, mode: str) -> Dict[str, Any]:
    cmd = {"loop": "W,LOOP", "sniff": "W,SNIFF,3", "scan": "W,SCAN"}[mode]
    return {"mode": mode, "lines": ctx.link.send(cmd)}


@function("scale.modbus", "Apply Modbus address / function code / scratchpad / Format", GROUP,
          params=[Param("address", "integer", minimum=1, maximum=247, label="station number"),
                  Param("function_code", "integer", choices=[3, 4], label="function code"),
                  Param("register", "integer", minimum=0, label="scratchpad"),
                  Param("format", "integer", choices=[0, 1, 2, 3, 4], label="Format")])
def modbus(ctx: Context, address: int, function_code: int,
           register: int, format: int) -> Dict[str, Any]:
    lines = ctx.link.send(f"W,SET,{address},{function_code},{register},{format}")
    clear_history()
    return {"lines": lines}
