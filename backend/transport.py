"""Shared serial transport for the laboratory controller.

A single owner manages the COM port and serializes commands so concurrent
device operations cannot consume each other's replies. A background reader
separates unsolicited measurements from command responses, updates the state
cache, and publishes events to SSE subscribers. Connection startup supports
port discovery and the configured Arduino reset delay.
"""
from __future__ import annotations

import queue
import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .config import get_config
from .simulator import SimulatedMega

try:  # pyserial is only required for a physical connection.
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover
    serial = None
    list_ports = None


LOG_MAX = 400


class LinkError(RuntimeError):
    pass


@dataclass
class Pending:
    """A command awaiting its response terminator."""
    command: str
    terminators: tuple
    lines: List[str] = field(default_factory=list)
    done: threading.Event = field(default_factory=threading.Event)
    error: Optional[str] = None


def _default_terminators(command: str) -> tuple:
    head = command.strip().upper()
    if head == "?":
        return ("STATE,",)
    if head.startswith("W,"):
        key = head[2:].split(",")[0]
        return {
            "READ":   ("WEIGHT,", "ERR"),
            "CFG":    ("CFG,", "ERR"),
            "SET":    ("CFG,", "ERR"),
            "HELP":   ("W,CFG", "  W,CFG", "ERR"),
            "SCAN":   ("SCAN,DONE", "ERR"),
            "SNIFF":  ("SNIFF,TOTAL", "ERR"),
            "LOOP":   ("HINT,", "ERR"),
            "RD":     ("REGS,", "ERR"),
        }.get(key, ("OK", "ERR"))
    if head.startswith("Y,"):
        return ("PUMP,", "FOUND,", "OK,", "ERR")
    if head.startswith("S,"):
        return ("DONE,S", "ERR")
    return ("OK", "ERR", "DONE")


def _is_slow(command: str) -> bool:
    head = command.strip().upper()
    return head.startswith(("W,SCAN", "W,SNIFF", "Y,INIT"))


class Link:
    def __init__(self) -> None:
        cfg = get_config()
        self.cfg = cfg
        self._link_cfg = cfg["link"]
        self._ser: Any = None
        self._sim: Optional[SimulatedMega] = None
        self._tx_lock = threading.Lock()
        self._pending: Optional[Pending] = None
        self._pending_lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self.connected = False
        self.port_name = "-"
        self.simulated = bool(self._link_cfg.get("simulate", False))
        self.last_error: Optional[str] = None

        self.state: Dict[str, Any] = {
            "connected": False,
            "simulated": self.simulated,
            "port": "-",
            "cleaning": False,
            "motors": {},          # {"A": {"selected": None, "state": "idle"}}
            "valves": {},          # {1: False, 2: False}
            "pump": {"online": False, "syringe_ul": None},
            "scale": {"grams": None, "raw": None, "stable": False,
                      "online": False, "stream_hz": 0, "updated": 0.0,
                      "sequence": 0},
            "firmware": None,
            "last_error": None,
        }
        for group in cfg["motors"]["groups"]:
            self.state["motors"][group["id"]] = {"selected": None, "state": "idle"}
        for ch in cfg["valves"]["channels"]:
            self.state["valves"][ch["id"]] = False
        self.state["pump"]["syringe_ul"] = cfg["syringe_pump"]["default_syringe_ul"]

        self.log: List[Dict[str, Any]] = []
        self._subscribers: List[queue.Queue] = []
        self._sub_lock = threading.Lock()
        # Measurement hooks let device modules receive samples without importing transport.
        self.weight_hooks: List[Callable[[Dict[str, Any]], None]] = []

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def _find_port(self) -> str:
        want = str(self._link_cfg.get("port", "auto"))
        if want and want.lower() != "auto":
            return want
        if list_ports is None:
            raise LinkError("Install pyserial to enable serial-port discovery")
        candidates = list(list_ports.comports())
        for p in candidates:
            text = f"{p.description} {p.manufacturer or ''}".lower()
            if any(k in text for k in ("arduino", "mega", "ch340", "usb-serial", "wch")):
                return p.device
        if candidates:
            return candidates[0].device
        raise LinkError("No serial port found; set link.port explicitly in hardware.yaml")

    def start(self) -> None:
        if self.connected:
            return
        try:
            if self.simulated:
                self._sim = SimulatedMega(self.cfg)
                self._sim.start()
                self.port_name = "simulator"
            else:
                if serial is None:
                    raise LinkError("pyserial is not installed; run run.bat to install dependencies")
                self.port_name = self._find_port()
                self._ser = serial.Serial(
                    self.port_name, int(self._link_cfg["baud"]), timeout=0.1)
                time.sleep(float(self._link_cfg.get("boot_delay_s", 2.5)))
            self.connected = True
            self.last_error = None
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            self._push_state()
            raise LinkError(str(exc)) from exc

        self._stop.clear()
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="serial-reader")
        self._reader.start()
        self._push_state()
        try:
            self.send("?")
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
        if self._reader:
            self._reader.join(timeout=1.5)
        if self._sim:
            self._sim.stop()
            self._sim = None
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        self.connected = False
        self._push_state()

    # ------------------------------------------------------------------
    # Incoming messages
    # ------------------------------------------------------------------
    def _readline(self) -> Optional[str]:
        if self._sim is not None:
            return self._sim.readline(timeout=0.2)
        if self._ser is None:
            return None
        raw = self._ser.readline()
        if not raw:
            return None
        return raw.decode("utf-8", errors="replace").strip()

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                line = self._readline()
            except Exception as exc:
                self.last_error = f"Serial read interrupted: {exc}"
                self.connected = False
                self._push_state()
                break
            if not line:
                continue
            self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        streamed = False
        if line.startswith("WEIGHT,"):
            streamed = self._apply_weight(line)
        elif line.startswith("STATE,"):
            self._apply_state(line)
        elif line.startswith("READY"):
            self.state["firmware"] = line
        elif line.startswith("ERR,stream_stopped") or line.startswith("ERR,timeout"):
            self.state["scale"]["online"] = False
            self._push_state()

        if not streamed:
            self._append_log("rx", line)

        with self._pending_lock:
            pending = self._pending
            if pending is not None:
                # Exclude streamed weights unless this command explicitly requests a weight.
                if line.startswith("WEIGHT,") and "WEIGHT," not in pending.terminators:
                    return
                # An asynchronous scale timeout may arrive after the next motor command.
                # Only W,READ consumes these errors; they do not indicate a motor-command failure.
                if (line.startswith(("ERR,timeout", "ERR,stream_stopped"))
                        and not pending.command.upper().startswith("W,READ")):
                    return
                pending.lines.append(line)
                if any(line.startswith(t) for t in pending.terminators):
                    pending.done.set()

    def _apply_weight(self, line: str) -> bool:
        parts = line.split(",")
        if len(parts) < 6:
            return True
        try:
            grams = float(parts[1])
        except ValueError:
            return True
        if not math.isfinite(grams):
            return True
        scale = self.state["scale"]
        sequence = int(scale.get("sequence") or 0) + 1
        scale.update({
            "grams": grams,
            "raw": parts[3],
            "stable": parts[5].strip() == "1",
            "online": True,
            "updated": time.time(),
            "sequence": sequence,
        })
        sample = dict(scale)
        for hook in self.weight_hooks:
            try:
                hook(sample)
            except Exception:
                pass
        self._broadcast({"type": "weight", "data": sample})
        return True

    _KV = re.compile(r"([A-Za-z_]+)=([^,]*)")

    def _apply_state(self, line: str) -> None:
        kv = dict(self._KV.findall(line[len("STATE,"):]))
        groups = self.cfg["motors"]["groups"]
        for idx, group in enumerate(groups):
            sel_key = "a" if idx == 0 else "b"
            st_key = "sa" if idx == 0 else "sb"
            slot = self.state["motors"][group["id"]]
            if sel_key in kv:
                try:
                    value = int(kv[sel_key])
                except ValueError:
                    value = -1
                ids = [c["id"] for c in group["channels"]]
                slot["selected"] = value if value in ids else None
            if st_key in kv:
                slot["state"] = kv[st_key]
        if "pump" in kv:
            self.state["pump"]["online"] = kv["pump"] == "online"
        if "syringe_uL" in kv:
            try:
                self.state["pump"]["syringe_ul"] = int(kv["syringe_uL"])
            except ValueError:
                pass
        if "valves" in kv:
            bits = kv["valves"]
            for i, ch in enumerate(self.cfg["valves"]["channels"]):
                if i < len(bits):
                    self.state["valves"][ch["id"]] = bits[i] == "1"
        if "cleaning" in kv:
            self.state["cleaning"] = kv["cleaning"] == "1"
        if "w_stream_hz" in kv:
            try:
                hz = int(kv["w_stream_hz"])
                self.state["scale"]["stream_hz"] = hz
                if hz == 0:
                    self.state["scale"]["online"] = False
            except ValueError:
                pass
        self._push_state()

    # ------------------------------------------------------------------
    # Outgoing commands
    # ------------------------------------------------------------------
    def send(self, command: str, terminators: Optional[tuple] = None,
             timeout: Optional[float] = None) -> List[str]:
        command = command.strip()
        if not command:
            return []
        if not self.connected:
            raise LinkError("Arduino is not connected")

        term = terminators or _default_terminators(command)
        if timeout is None:
            timeout = float(self._link_cfg["long_command_timeout_s"] if _is_slow(command)
                            else self._link_cfg["command_timeout_s"])

        with self._tx_lock:
            pending = Pending(command=command, terminators=term)
            with self._pending_lock:
                self._pending = pending
            self._append_log("tx", command)
            payload = (command + "\n").encode("utf-8")
            try:
                if self._sim is not None:
                    self._sim.write(payload)
                else:
                    self._ser.write(payload)
                    self._ser.flush()
            except Exception as exc:
                with self._pending_lock:
                    self._pending = None
                raise LinkError(f"Could not send command: {exc}") from exc

            ok = pending.done.wait(timeout)
            with self._pending_lock:
                self._pending = None

        if not ok:
            raise LinkError(f"Command timed out ({command})")
        for line in pending.lines:
            if line.startswith("ERR"):
                raise LinkError(line)
        return pending.lines

    def send_many(self, commands: List[str]) -> List[str]:
        out: List[str] = []
        for cmd in commands:
            out.extend(self.send(cmd))
        return out

    def refresh_state(self) -> Dict[str, Any]:
        self.send("?")
        return self.snapshot()

    # ------------------------------------------------------------------
    # State and events
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        self.state["connected"] = self.connected
        self.state["simulated"] = self.simulated
        self.state["port"] = self.port_name
        self.state["last_error"] = self.last_error
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in self.state.items()}

    def _push_state(self) -> None:
        self._broadcast({"type": "state", "data": self.snapshot()})

    def _append_log(self, kind: str, text: str) -> None:
        entry = {"t": time.time(), "kind": kind, "text": text}
        self.log.append(entry)
        if len(self.log) > LOG_MAX:
            del self.log[:-LOG_MAX]
        self._broadcast({"type": "log", "data": entry})

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _broadcast(self, event: Dict[str, Any]) -> None:
        with self._sub_lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


_link: Optional[Link] = None
_link_lock = threading.Lock()


def get_link() -> Link:
    global _link
    with _link_lock:
        if _link is None:
            _link = Link()
        return _link
