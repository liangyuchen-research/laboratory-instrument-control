"""Arduino Mega protocol simulator for interface development and tests.

Set link.simulate to true in config/hardware.yaml to run the interface without
physical devices. The simulator implements the text protocol used by
firmware/Arduino/Arduino.ino and supports tools/selftest.py. Its synthetic
measurements are intended for software testing, not hardware calibration.
"""
from __future__ import annotations

import math
import queue
import random
import threading
import time
from typing import Any, Dict, List, Optional


class SimulatedMega:
    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self._out: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        groups = cfg["motors"]["groups"]
        self.sel: Dict[str, Optional[int]] = {g["id"]: None for g in groups}
        self.mstate: Dict[str, str] = {g["id"]: "idle" for g in groups}
        self.sps: Dict[str, float] = {g["id"]: 0.0 for g in groups}
        self._pending_start: Dict[str, Optional[Dict[str, Any]]] = {
            g["id"]: None for g in groups
        }
        self._move_end: Dict[str, Optional[float]] = {g["id"]: None for g in groups}
        self.valves: Dict[int, bool] = {c["id"]: False for c in cfg["valves"]["channels"]}
        self._valve_close_at: Dict[int, Optional[float]] = {
            c["id"]: None for c in cfg["valves"]["channels"]
        }
        self.cleaning = False
        self._clean_out_started = False
        self._clean_water_stopped = False
        self._clean_out_start_at = 0.0
        self._clean_water_stop_at = 0.0
        self._clean_out_stop_at = 0.0
        self.pump_online = False
        self.syringe_ul = int(cfg["syringe_pump"]["default_syringe_ul"])
        self.stream_hz = int(cfg["scale"]["stream_hz"])
        self.scale_div = 250.0
        self.scale_offset = 0.0
        self.avg_n = int(cfg["scale"].get("average_samples", 4))
        self.stable_g = float(cfg["scale"].get("stable_threshold_g", 0.05))
        self._t0 = time.time()
        self._payload_g = 0.0
        self._simulation_speedup = float(cfg["link"].get("simulation_speedup", 1.0))
        self._last_weight = time.monotonic()

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._emit("READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0")
        self._thread = threading.Thread(target=self._tick, daemon=True, name="sim")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def readline(self, timeout: float = 0.2) -> Optional[str]:
        try:
            return self._out.get(timeout=timeout)
        except queue.Empty:
            return None

    def write(self, payload: bytes) -> None:
        for raw in payload.decode("utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line:
                self._handle(line)

    # ------------------------------------------------------------------
    def _emit(self, text: str) -> None:
        self._out.put(text)

    def _tick(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.01)
            now = time.monotonic()
            weight_line: Optional[str] = None
            state_line: Optional[str] = None
            with self._lock:
                for gid, pending in self._pending_start.items():
                    if pending is not None and now >= float(pending["at"]):
                        self._pending_start[gid] = None
                        self._start_now(gid, bool(pending["continuous"]),
                                        int(pending["steps"]), float(pending["sps"]), now)
                        state_line = self._state_line()
                for gid, ending in self._move_end.items():
                    if ending is not None and now >= ending:
                        self._move_end[gid] = None
                        self.mstate[gid] = "idle"
                        self.sps[gid] = 0.0
                        motor = self.sel[gid]
                        if motor is not None:
                            self._schedule_valve_close(motor, now)
                        state_line = self._state_line()
                if self.cleaning:
                    clean = self.cfg["cleaning"]
                    changed = False
                    if not self._clean_out_started and now >= self._clean_out_start_at:
                        self._queue_motor_start(
                            int(clean["out_motor"]), True, 0,
                            self._clean_sps(int(clean["out_motor"])))
                        self._clean_out_started = True
                        changed = True
                    if not self._clean_water_stopped and now >= self._clean_water_stop_at:
                        water_gid = self._group_of(int(clean["water_motor"]))
                        if water_gid is not None:
                            self._stop_group(water_gid)
                        self._clean_water_stopped = True
                        changed = True
                    if self._clean_out_started and now >= self._clean_out_stop_at:
                        out_gid = self._group_of(int(clean["out_motor"]))
                        if out_gid is not None:
                            self._stop_group(out_gid)
                        self.cleaning = False
                        changed = True
                    if changed:
                        state_line = self._state_line()
                for valve, closing in self._valve_close_at.items():
                    if closing is not None and now >= closing:
                        self._valve_close_at[valve] = None
                        self.valves[valve] = False
                        state_line = self._state_line()

                hz = self.stream_hz
                if hz > 0 and now - self._last_weight >= 1.0 / hz:
                    self._last_weight = now
                    weight_line = self._weight_line()
            if weight_line is not None:
                self._emit(weight_line)
            if state_line is not None:
                self._emit(state_line)

    def _raw(self) -> float:
        drift = math.sin((time.time() - self._t0) / 7.0) * 0.5
        noise = random.gauss(0, 0.5)
        delivered_ml_s = sum(
            abs(self.sps[gid]) / self._motor_ppm(self.sel[gid])
            for gid, state in self.mstate.items()
            if state == "run" and self.sel[gid] is not None
        )
        if delivered_ml_s:
            self._payload_g += (delivered_ml_s / max(self.stream_hz, 1)
                                * self._simulation_speedup)
        return self.scale_offset + self._payload_g * self.scale_div + drift + noise

    def _weight_line(self) -> str:
        raw = self._raw()
        grams = (raw - self.scale_offset) / self.scale_div if self.scale_div else 0.0
        stable = 0 if any(s == "run" for s in self.mstate.values()) else 1
        return f"WEIGHT,{grams:.3f},RAW,{raw:.1f},ST,{stable}"

    def _state_line(self) -> str:
        groups = self.cfg["motors"]["groups"]
        a, b = groups[0]["id"], groups[1]["id"]
        bits = "".join("1" if self.valves[c["id"]] else "0"
                       for c in self.cfg["valves"]["channels"])
        return (
            f"STATE,a={self.sel[a] if self.sel[a] is not None else -1},sa={self.mstate[a]},"
            f"b={self.sel[b] if self.sel[b] is not None else -1},sb={self.mstate[b]},"
            f"pump={'online' if self.pump_online else 'unknown'},"
            f"proto=ascii,baud=9600,addr=0,syringe_uL={self.syringe_ul},"
            f"valves={bits},"
            f"cal_pulses_per_ml={self.cfg['motors']['pulses_per_ml']},"
            f"max_flow_uL_min={int(self.cfg['motors']['max_flow_ml_min']) * 1000},"
            f"w_stream_hz={self.stream_hz},"
            f"cleaning={1 if self.cleaning else 0}"
        )

    def _motor_ppm(self, motor: int) -> float:
        for group in self.cfg["motors"]["groups"]:
            for channel in group["channels"]:
                if int(channel["id"]) == int(motor):
                    return float(channel.get(
                        "pulses_per_ml", self.cfg["motors"]["pulses_per_ml"]))
        return float(self.cfg["motors"]["pulses_per_ml"])

    def _clean_sps(self, motor: int) -> int:
        return max(1, round(float(self.cfg["cleaning"]["flow_ml_min"])
                            * self._motor_ppm(motor) / 60.0))

    def _group_of(self, motor: int) -> Optional[str]:
        for g in self.cfg["motors"]["groups"]:
            if any(c["id"] == motor for c in g["channels"]):
                return g["id"]
        return None

    def _linked_valve(self, motor: int) -> Optional[int]:
        for link in self.cfg["valves"]["motor_links"]:
            if int(link["motor"]) == motor:
                return int(link["valve"])
        return None

    def _schedule_valve_close(self, motor: int, now: Optional[float] = None) -> None:
        valve = self._linked_valve(motor)
        if valve is None:
            return
        delay = float(self.cfg["valves"]["close_after_ms"]) / 1000.0
        if delay <= 0:
            self.valves[valve] = False
            self._valve_close_at[valve] = None
        else:
            self._valve_close_at[valve] = (now if now is not None else time.monotonic()) + delay

    def _stop_group(self, gid: str, close_valve: bool = True) -> None:
        active = self.mstate[gid] in ("wait", "run", "move")
        motor = self.sel[gid]
        self._pending_start[gid] = None
        self._move_end[gid] = None
        self.mstate[gid] = "idle"
        self.sps[gid] = 0.0
        if close_valve and active and motor is not None:
            self._schedule_valve_close(motor)

    def _start_now(self, gid: str, continuous: bool, steps: int,
                   sps: float, now: Optional[float] = None) -> None:
        if continuous:
            self.mstate[gid] = "run"
            self.sps[gid] = sps
            self._move_end[gid] = None
        else:
            self.mstate[gid] = "move"
            self.sps[gid] = 0.0
            duration = abs(steps) / max(abs(sps), 1.0) / max(self._simulation_speedup, 1.0)
            self._move_end[gid] = (now if now is not None else time.monotonic()) + duration

    def _queue_motor_start(self, motor: int, continuous: bool,
                           steps: int, sps: float) -> None:
        gid = self._group_of(motor)
        if gid is None:
            return
        pending = self._pending_start[gid]
        if self.sel[gid] == motor and self.mstate[gid] == "run" and continuous:
            self.sps[gid] = sps
            return
        if (self.sel[gid] == motor and pending is not None and continuous
                and bool(pending["continuous"])):
            pending["sps"] = sps
            return

        if self.sel[gid] != motor:
            self._stop_group(gid)
            self.sel[gid] = motor
        self._stop_group(gid)
        valve = self._linked_valve(motor)
        if valve is None:
            self._start_now(gid, continuous, steps, sps)
            return
        self.valves[valve] = True
        self._valve_close_at[valve] = None
        self.mstate[gid] = "wait"
        self._pending_start[gid] = {
            "continuous": continuous,
            "steps": steps,
            "sps": sps,
            "at": time.monotonic()
                  + float(self.cfg["valves"]["open_before_ms"]) / 1000.0,
        }

    # ------------------------------------------------------------------
    def _handle(self, line: str) -> None:
        up = line.upper()
        with self._lock:
            if up == "?":
                self._emit(self._state_line()); return
            if up in ("X", "STOP"):
                self.cleaning = False
                self._clean_out_started = False
                self._clean_water_stopped = False
                for gid in self.sel:
                    self._pending_start[gid] = None
                    self._move_end[gid] = None
                    self.sel[gid] = None
                    self.mstate[gid] = "idle"
                    self.sps[gid] = 0.0
                for vid in self.valves:
                    self.valves[vid] = False
                    self._valve_close_at[vid] = None
                self.stream_hz = 0
                self._emit("OK,all_stopped"); return

            parts = line.split(",")
            head = parts[0].upper()

            if head == "C" and len(parts) == 1:
                if self.cleaning:
                    self._emit("ERR,cleaning_active"); return
                for gid in self.sel:
                    self._pending_start[gid] = None
                    self._move_end[gid] = None
                    self.sel[gid] = None
                    self.mstate[gid] = "idle"
                    self.sps[gid] = 0.0
                for vid in self.valves:
                    self.valves[vid] = False
                    self._valve_close_at[vid] = None

                clean = self.cfg["cleaning"]
                water_motor = int(clean["water_motor"])
                out_motor = int(clean["out_motor"])
                out_gid = self._group_of(out_motor)
                if out_gid is None:
                    self._emit("ERR,clean_config"); return
                # Match firmware by selecting OUT before the water-only phase finishes.
                self.sel[out_gid] = out_motor
                self._queue_motor_start(
                    water_motor, True, 0, self._clean_sps(water_motor))
                water_gid = self._group_of(water_motor)
                pending = self._pending_start.get(water_gid) if water_gid else None
                water_starts = (float(pending["at"])
                                if pending is not None else time.monotonic())
                water_lead_s = float(clean["water_lead_s"])
                overlap_s = float(clean["overlap_s"])
                out_tail_s = float(clean["out_tail_s"])
                self._clean_out_start_at = water_starts + water_lead_s
                self._clean_water_stop_at = self._clean_out_start_at + overlap_s
                self._clean_out_stop_at = self._clean_water_stop_at + out_tail_s
                self._clean_out_started = False
                self._clean_water_stopped = False
                self.cleaning = True
                self._emit("OK,cleaning_started"); return

            if self.cleaning and head in ("S", "V", "P"):
                self._emit("ERR,cleaning_active"); return

            if head == "S" and len(parts) == 2:
                motor = int(parts[1])
                gid = self._group_of(motor)
                if gid is None:
                    self._emit("ERR,idx(2~9)"); return
                if self.sel[gid] != motor:
                    self._stop_group(gid)
                    self.sel[gid] = motor
                self._emit("DONE,S"); return

            if head == "V" and len(parts) == 3:
                motor, sps = int(parts[1]), float(parts[2])
                gid = self._group_of(motor)
                if gid is None:
                    self._emit("ERR,arg"); return
                if sps == 0:
                    # An unselected motor is already stopped. Do not switch away from the
                    # running motor in the same group to stop an inactive channel.
                    if self.sel[gid] == motor:
                        self._stop_group(gid)
                    self._emit("OK"); return
                self._queue_motor_start(motor, True, 0, sps)
                self._emit("OK"); return

            if head == "P" and len(parts) == 4:
                motor = int(parts[1])
                gid = self._group_of(motor)
                if gid is None:
                    self._emit("ERR,arg"); return
                steps, sps = int(parts[2]), float(parts[3])
                self._queue_motor_start(motor, False, steps, sps)
                self._emit("OK"); return

            if head == "Y":
                self._handle_pump(parts); return
            if head == "W":
                self._handle_scale(parts); return

        self._emit("ERR,bad_cmd")

    def _handle_pump(self, parts: List[str]) -> None:
        key = parts[1].upper() if len(parts) > 1 else ""
        arg = parts[2] if len(parts) > 2 else ""
        if key in ("DETECT", "SCAN"):
            self.pump_online = True
            self._emit("FOUND,proto=ascii,baud=9600,addr=0"); return
        if key == "SYR":
            self.syringe_ul = int(arg)
            self.pump_online = True
            self._emit("PUMP,proto=ascii,ready=1,error=0"); return
        if key in ("INIT", "STOP", "Q", "ST", "VALVE", "SPEED",
                   "ASP", "DISP", "ASPUL", "DISPUL", "POS"):
            self.pump_online = True
            extra = f",data={arg}" if arg else ""
            self._emit(f"PUMP,proto=ascii,ready=1,error=0{extra}"); return
        self._emit("ERR,bad_pump_cmd")

    def _handle_scale(self, parts: List[str]) -> None:
        key = parts[1].upper() if len(parts) > 1 else ""
        arg = parts[2] if len(parts) > 2 else ""
        if key == "READ":
            self._emit(self._weight_line()); return
        if key == "STREAM":
            self.stream_hz = int(arg)
            self._emit(f"OK,stream_hz={self.stream_hz}"); return
        if key == "TARE":
            self.scale_offset = self._raw()
            self._payload_g = 0.0
            self._emit(f"OK,tare_raw={self.scale_offset:.1f}"); return
        if key == "CAL":
            grams = float(arg)
            if grams <= 0:
                self._emit("ERR,arg"); return
            self._emit(f"OK,div={self.scale_div:.4f}"); return
        if key == "DIV":
            self.scale_div = float(arg)
            self._emit(f"OK,div={self.scale_div:.4f}"); return
        if key == "OFFSET":
            self.scale_offset = float(arg)
            self._emit(f"OK,offset={self.scale_offset:.3f}"); return
        if key == "AVG":
            self.avg_n = int(arg)
            self._emit(f"OK,avg={self.avg_n}"); return
        if key == "STABLE":
            self.stable_g = float(arg)
            self._emit(f"OK,stable_g={self.stable_g:.3f}"); return
        if key == "CFG":
            m = self.cfg["scale"]["modbus"]
            self._emit(
                f"CFG,baud={m['baud']},addr={m['address']},fc={m['function_code']},"
                f"reg={m['register']},fmt={m['format']},div={self.scale_div:.4f},"
                f"offset={self.scale_offset:.1f},avg={self.avg_n},"
                f"stable_g={self.stable_g:.3f},stream_hz={self.stream_hz}")
            return
        if key == "SCAN":
            m = self.cfg["scale"]["modbus"]
            self._emit("SCAN,START,estimated_seconds=40")
            self._emit(f"  FOUND,baud={m['baud']} addr={m['address']} "
                       f"fc={m['function_code']} reg={m['register']} data=00 01 A2 3F")
            self._emit("SCAN,DONE,found=1")
            return
        if key == "LOOP":
            self._emit('LOOP,received="LOOPTEST"')
            self._emit("HINT,Mega Serial2 responds; check the RS485 adapter and downstream wiring")
            return
        if key == "SNIFF":
            self._emit("SNIFF,START,seconds=3")
            self._emit("SNIFF,TOTAL,bytes=0")
            return
        if key == "SET":
            self._emit("CFG,baud=9600,addr=1,fc=3,reg=0,fmt=1,div=250.0000,"
                       f"offset=0.0,avg={self.avg_n},stable_g={self.stable_g:.3f},"
                       f"stream_hz={self.stream_hz}")
            return
        self._emit("ERR,bad_scale_cmd")
