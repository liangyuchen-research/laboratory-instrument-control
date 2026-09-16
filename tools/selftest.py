#!/usr/bin/env python3
"""Run end-to-end checks against the built-in simulator.

No Arduino connection is required. The checks validate hardware.yaml and pin
conflict detection, compare the generated firmware header, launch uvicorn on
an available local port with simulation forced on, exercise registered device
operations and error handling, verify SSE measurements, and check frontend
operation names against the backend registry.

Usage: python tools/selftest.py
"""
from __future__ import annotations

import os
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

PASS, FAIL = [], []


def check(ok: bool, label: str, extra: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    mark = "✔" if ok else "✘"
    print(f"  {mark} {label}" + (f"  ({extra})" if extra else ""))


def make_sim_config(work: Path) -> Path:
    """Copy configuration with simulation enabled, leaving the original unchanged."""
    src = ROOT / "config" / "hardware.yaml"
    cfg = yaml.safe_load(src.read_text(encoding="utf-8"))
    cfg["link"]["simulate"] = True
    cfg["link"]["boot_delay_s"] = 0
    # Preserve a realistic feedback cadence. Excessive speedup creates large jumps
    # between 10 Hz samples and tests simulator overshoot rather than the controller.
    cfg["link"]["simulation_speedup"] = 5
    # Verify production timings of 30/45/45 s; use shorter times for state-machine tests.
    cfg["cleaning"]["water_lead_s"] = 0.3
    cfg["cleaning"]["overlap_s"] = 0.5
    cfg["cleaning"]["out_tail_s"] = 0.4
    out = work / "hardware.yaml"
    out.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    return out


def _run(work: Path) -> int:
    print("\n[1] Configuration and generated firmware settings")
    from backend.config import load_config, ConfigError
    try:
        cfg = load_config()
        check(True, "hardware.yaml validates, including duplicate-pin checks")
    except ConfigError as exc:
        check(False, "hardware.yaml validation", str(exc))
        return 1

    # Introduce a pin conflict to verify configuration validation.
    import copy
    bad = copy.deepcopy(cfg)
    bad["valves"]["channels"][0]["pin"] = bad["motors"]["groups"][0]["driver"]["pul"]
    tmp = work / "invalid-hardware.yaml"
    tmp.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
    try:
        load_config(tmp)
        check(False, "Pin conflicts must be rejected")
    except ConfigError:
        check(True, "Pin conflicts are rejected")
    finally:
        tmp.unlink(missing_ok=True)

    from tools.gen_firmware_config import render, HEADER
    same = HEADER.exists() and HEADER.read_text(encoding="utf-8") == render(cfg)
    check(same, "hw_config.h matches hardware.yaml",
          "" if same else "Run python tools/gen_firmware_config.py")

    print("\n[2] Start the HTTP server in simulation mode")
    sim_cfg = make_sim_config(work)
    os.environ["LAB_CONFIG"] = str(sim_cfg)
    calibration_file = work / "scale-calibration.json"
    os.environ["SCALE_CALIBRATION_FILE"] = str(calibration_file)
    for mod in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[mod]

    import socket
    import threading
    import httpx
    import uvicorn
    from backend.main import app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if getattr(server, "started", False):
            break
        time.sleep(0.05)
    check(getattr(server, "started", False), f"uvicorn started at {base}")

    try:
        client = httpx.Client(base_url=base, timeout=20.0)
        r = client.get("/api/config")
        check(r.status_code == 200 and r.json()["link"]["simulated"], "GET /api/config")
        check(r.json()["calibration"]["attempted"]
              and not r.json()["calibration"]["file_exists"],
              "Startup attempts to load the calibration file")

        names = [f["name"] for f in client.get("/fn").json()["functions"]]
        check(len(names) >= 26, f"GET /fn lists {len(names)} operations")
        for group in ("motors.", "pump.", "scale."):
            check(any(n.startswith(group) for n in names), f"Operation group {group} exists")
        check(not any(n.startswith("valves.") for n in names),
              "Manual solenoid valve operations are not exposed")

        def call(name, **params):
            res = client.post("/fn/" + name, json=params)
            return res.status_code, (res.json() if res.content else {})

        print("\n[3] Relays and peristaltic pumps")
        code, body = call("motors.list")
        check(code == 200 and len(body["result"]["groups"]) == 2, "motors.list")
        grav_cfg = cfg["motors"]["gravimetric_dose"]
        check(cfg["motors"]["dose_flow_ml_min"] == 40
              and grav_cfg["fast_flow_ml_min"] == 40
              and grav_cfg["approach_flow_ml_min"] == 10
              and grav_cfg["trim_flow_ml_min"] == 3
              and grav_cfg["tolerance_percent"] == 0.5
              and grav_cfg["settle_s"] == 5
              and grav_cfg["timeout_s"] == 300,
              "Dosing uses 40 mL/min; gravimetric phases use 40/10/3 mL/min and +/-0.5% tolerance")
        check(cfg["motors"]["gravimetric_dose"]["default_target_g"] == 10,
              "Default gravimetric target is 10 g")
        channels = {int(ch["id"]): ch for group in cfg["motors"]["groups"]
                    for ch in group["channels"]}
        check(channels[2].get("fluid") == "Water"
              and channels[8].get("fluid") == "OUT"
              and channels[9].get("fluid") == "CuSO₄",
              "Fluid mapping is motor 2=Water, 8=OUT, 9=CuSO4")
        corrected_ppm = float(cfg["motors"]["pulses_per_ml"])
        reference_ppm = float(cfg["motors"]["reference_pulses_per_ml"])
        check(abs(corrected_ppm - reference_ppm * 10 / 13.25) < 0.000001,
              "Flow calibration is 4237.008 pulses/mL from 13.25 mL measured at a 10 mL setting")
        check(all(float(ch.get("pulses_per_ml", corrected_ppm)) == corrected_ppm
                  for ch in channels.values()),
              "Motors 2-9 share one flow conversion factor")
        common_dose_ppm = float(cfg["motors"]["dose_pulses_per_ml"])
        check(abs(common_dose_ppm - corrected_ppm * 50 / 46.469) < 0.000001,
              "Shared dose calibration uses motor 2: 46.469 mL measured at a 50 mL setting")
        check(all(float(ch.get("dose_pulses_per_ml", common_dose_ppm))
                  == common_dose_ppm for ch in channels.values()),
              "Motors 2-9 share one dose conversion factor")
        expected_weight_sps = [round(corrected_ppm * flow / 60)
                               for flow in (40, 10, 3)]
        check(expected_weight_sps == [2825, 706, 212],
              "Gravimetric phases use shared rates of 2825/706/212 steps/s")
        check(body["result"]["pulses_per_rev"] == 1600
              and body["result"]["driver_switches"]
              == ["ON", "OFF", "ON", "OFF", "OFF", "OFF", "ON", "ON"],
              "DM542J switches select 8 microsteps and 1600 pulses per revolution")
        code, _ = call("motors.select", motor=3)
        check(code == 200, "motors.select selects motor 3")
        default_flow = cfg["motors"]["default_flow_ml_min"]
        code, body = call("motors.run", motor=3, flow_ml_min=default_flow, direction=1)
        want = round(default_flow * cfg["motors"]["pulses_per_ml"] / 60)
        check(code == 200 and body["result"]["sps"] == want,
              f"motors.run converts {default_flow} mL/min to {want} steps/s")
        code, body = call("motors.dose", motor=3, volume_ml=2,
                          flow_ml_min=default_flow, direction=-1)
        check(code == 200 and body["result"]["steps"]
              == -round(2 * common_dose_ppm),
              "Reverse 2 mL dosing produces a negative step count")
        expected_eta = round(round(2 * common_dose_ppm) / want, 1)
        check(abs(body["result"]["eta_s"] - expected_eta) < 0.1,
              f"motors.dose estimates {expected_eta:g} s")
        code, body = call("motors.run", motor=3, flow_ml_min=99999, direction=1)
        check(code == 400, "Excessive flow is rejected", str(body.get("detail"))[:40])
        code, _ = call("motors.select", motor=99)
        check(code == 400, "Unconfigured motors are rejected")
        code, _ = call("motors.run", motor=3, flow_ml_min=default_flow, direction=7)
        check(code == 400, "Direction must be 1 or -1")
        call("motors.run", motor=3, flow_ml_min=default_flow, direction=1)
        code, body = call("motors.stop", motor=2)
        group_a = body["result"][cfg["motors"]["groups"][0]["id"]]
        check(code == 200 and group_a["selected"] == 3 and group_a["state"] == "run",
              "Stopping an inactive channel does not interrupt the active motor in its group")
        code, _ = call("motors.stop", motor=3)
        check(code == 200, "motors.stop")
        code, body = call("motors.dose_by_weight", motor=3, target_g=10,
                          direction=1)
        result = body.get("result", {})
        check(code == 200 and result.get("mode") == "gravimetric"
              and [result.get("fast_sps"), result.get("approach_sps"),
                   result.get("trim_sps")] == expected_weight_sps
              and [result.get("fast_flow_ml_min"), result.get("approach_flow_ml_min"),
                   result.get("trim_flow_ml_min")] == [40, 10, 3],
              "motors.dose_by_weight uses shared 40/10/3 mL/min phases")
        check(result.get("within_tolerance") is True
              and abs(result.get("error_g", 99)) <= 10 * 0.5 / 100,
              "Simulated gravimetric dosing reaches the target within +/-0.5%",
              str(result.get("error_g")))
        code, body = call("motors.dose", motor=2, volume_ml=50, direction=1)
        corrected = body.get("result", {})
        check(code == 200 and corrected.get("steps") == round(common_dose_ppm * 50)
              and corrected.get("steps") == 227948
              and corrected.get("sps") == round(corrected_ppm * 40 / 60)
              and corrected.get("dose_pulses_per_ml") == common_dose_ppm
              and corrected.get("flow_ml_min") == cfg["motors"]["dose_flow_ml_min"],
              "Motor 2 uses 227948 steps and the fixed rate for a 50 mL dose")
        call("motors.stop", motor=2)
        # Legacy flow arguments are ignored; identical settings on motors 2-9 must
        # produce identical step counts, step rates, and motor run times.
        code, body = call("motors.dose", motor=9, volume_ml=50,
                          flow_ml_min=7, direction=1)
        dose_9 = body.get("result", {})
        check(code == 200 and dose_9.get("sps") == corrected.get("sps")
              and dose_9.get("steps") == corrected.get("steps") == 227948
              and dose_9.get("dose_pulses_per_ml") == common_dose_ppm
              and dose_9.get("eta_s") == corrected.get("eta_s")
              and dose_9.get("flow_ml_min") == 40,
              "Motors 2 and 9 have equal step counts, rates, and run times for 50 mL")
        call("motors.stop", motor=9)
        all_motor_runs = []
        for motor in range(2, 10):
            code, body = call("motors.dose", motor=motor, volume_ml=50,
                              direction=1)
            run = body.get("result", {})
            all_motor_runs.append((
                code, run.get("steps"), run.get("sps"),
                round(abs(run.get("steps", 0)) / max(abs(run.get("sps", 0)), 1), 6)))
            call("motors.stop", motor=motor)
        check(len(set(all_motor_runs)) == 1
              and all_motor_runs[0] == (200, 227948, 2825, 80.689558),
              "Motors 2-9 use 227948 steps at 2825 steps/s for 80.690 s to dose 50 mL")

        print("\n[4] Automatic cleaning")
        check(cfg["cleaning"] == {
            "water_motor": 2, "out_motor": 8, "flow_ml_min": 40.0,
            "water_lead_s": 30.0, "overlap_s": 45.0, "out_tail_s": 45.0,
        }, "Production cleaning uses 30 s intake, 45 s overlap, and 45 s final drainage")
        from tools.gen_firmware_config import render as _render
        header = _render(cfg)
        check("#define CLEAN_WATER_LEAD_MS 30000UL" in header
              and "#define CLEAN_OVERLAP_MS 45000UL" in header
              and "#define CLEAN_OUT_TAIL_MS 45000UL" in header
              and "#define CLEAN_WATER_SPS 2825.0F" in header
              and "#define CLEAN_OUT_SPS 2825.0F" in header,
              "hw_config.h includes all three cleaning phases and both pump step rates")

        def wait_state(predicate, timeout):
            deadline = time.monotonic() + timeout
            latest = {}
            while time.monotonic() < deadline:
                latest = client.get("/api/state").json()
                if predicate(latest):
                    return latest
                time.sleep(0.01)
            return latest

        code, body = call("motors.clean")
        clean_state = body.get("result", {}).get("state", {})
        check(code == 200 and clean_state.get("cleaning") is True
              and clean_state["motors"]["A"]["state"] == "wait"
              and clean_state["motors"]["B"]["selected"] == 8
              and clean_state["valves"]["1"] is True,
              "motors.clean opens the water valve and preselects OUT motor 8")
        blocked_code, _ = call(
            "motors.run", motor=3, flow_ml_min=default_flow, direction=1)
        check(blocked_code == 400, "Cleaning blocks other peristaltic pump operations")
        water_only = wait_state(
            lambda s: s["motors"]["A"]["state"] == "run"
            and s["motors"]["B"]["state"] == "idle", 1.25)
        check(water_only.get("cleaning") is True,
              "Cleaning starts with only water motor 2 running")
        both = wait_state(
            lambda s: s["motors"]["A"]["state"] == "run"
            and s["motors"]["B"]["state"] == "run", 0.5)
        check(both.get("cleaning") is True,
              "Motors 2 and 8 run together after the intake delay")
        out_only = wait_state(
            lambda s: s["motors"]["A"]["state"] == "idle"
            and s["motors"]["B"]["state"] == "run", 0.8)
        check(out_only.get("cleaning") is True,
              "The OUT motor continues draining after the water motor stops")
        clean_done = wait_state(
            lambda s: not s.get("cleaning")
            and s["motors"]["A"]["state"] == "idle"
            and s["motors"]["B"]["state"] == "idle", 0.5)
        check(not clean_done.get("cleaning", True),
              "Cleaning completes when the OUT drainage phase ends")

        print("\n[5] Automatic valve sequencing")
        links = {(int(x["motor"]), int(x["valve"]))
                 for x in cfg["valves"]["motor_links"]}
        check(links == {(2, 1), (9, 2)}, "Motor 2 links to valve 1; motor 9 links to valve 2")
        check(cfg["valves"]["open_before_ms"] == 1000
              and cfg["valves"]["close_after_ms"] == 1000,
              "Valves open 1 s before pumping and close 1 s after stopping")

        code, body = call("motors.run", motor=2, flow_ml_min=default_flow, direction=1)
        state = client.get("/api/state").json()
        check(code == 200 and body["result"]["state"]["A"]["state"] == "wait"
              and state["valves"]["1"] is True,
              "Motor 2 waits while valve 1 opens")
        time.sleep(1.1)
        call("motors.select", motor=2)  # Refresh the Arduino state.
        state = client.get("/api/state").json()
        check(state["motors"]["A"]["state"] == "run",
              "Motor 2 starts after valve 1 has been open for 1 s")
        call("motors.stop", motor=2)
        state = client.get("/api/state").json()
        check(state["motors"]["A"]["state"] == "idle"
              and state["valves"]["1"] is True,
              "Valve 1 remains open immediately after motor 2 stops")
        time.sleep(1.1)
        call("motors.select", motor=2)
        state = client.get("/api/state").json()
        check(state["valves"]["1"] is False, "Valve 1 closes automatically 1 s after pumping stops")

        code, body = call("motors.run", motor=9, flow_ml_min=default_flow, direction=1)
        state = client.get("/api/state").json()
        check(code == 200 and body["result"]["state"]["B"]["state"] == "wait"
              and state["valves"]["2"] is True,
              "Motor 9 (CuSO4) opens valve 2 automatically")
        call("motors.stop", motor=9)
        time.sleep(1.1)
        call("motors.select", motor=9)

        print("\n[6] Syringe pump")
        code, body = call("pump.status")
        check(code == 200 and body["result"]["max_speed_ml_s"] == 2.5,
              "pump.status reports a 2.5 mL/s limit for a 5 mL syringe")
        code, body = call("pump.detect")
        check(code == 200 and body["result"]["online"], "pump.detect")
        code, body = call("pump.set_syringe", volume_ul=1250)
        check(code == 200 and body["result"]["max_speed_ml_s"] == 0.625,
              "A 1.25 mL syringe has a 0.625 mL/s limit")
        code, body = call("pump.aspirate", volume_ul=625)
        check(code == 200 and body["result"]["steps"] == 6000,
              "A 625 uL aspiration with a 1.25 mL syringe equals 6000 steps")
        code, _ = call("pump.aspirate", volume_ul=99999)
        check(code == 400, "Volumes exceeding syringe capacity are rejected")
        code, _ = call("pump.set_speed", speed_ml_s=99)
        check(code == 400, "Excessive syringe speed is rejected")
        code, _ = call("pump.set_valve", position=99)
        check(code == 400, "Out-of-range valve positions are rejected")
        call("pump.set_syringe", volume_ul=5000)

        print("\n[7] Scale transmitter")
        check(cfg["scale"]["average_samples"] == 1
              and cfg["scale"]["display_decimals"] == 3,
              "Live mass uses no moving average and displays three decimal places")
        time.sleep(1.2)                      # Wait for several simulated measurements.
        code, body = call("scale.read", points=50)
        res = body["result"]
        check(code == 200 and res["grams"] is not None, "scale.read returns a mass measurement")
        check(res["stats"]["count"] > 0, f"Backend retains {res['stats']['count']} history samples")
        check(res["stream_hz"] == cfg["scale"]["stream_hz"],
              f"Stream frequency is {cfg['scale']['stream_hz']} Hz")
        first_sequence = int(client.get("/api/state").json()["scale"]["sequence"])
        time.sleep(0.2)
        second_sequence = int(client.get("/api/state").json()["scale"]["sequence"])
        check(second_sequence > first_sequence,
              "Samples have increasing sequence numbers independent of the dose deadline")
        from backend.transport import get_link
        runtime_scale_cfg = get_link().send("W,CFG")
        check(any("avg=1" in line for line in runtime_scale_cfg)
              and any(row["kind"] == "tx" and row["text"] == "W,AVG,1"
                      for row in get_link().log),
              "Startup synchronizes AVG=1 to Arduino RAM")
        code, _ = call("scale.tare")
        check(code == 200, "scale.tare")
        code, body = call("scale.read", points=50)
        check(body["result"]["stats"]["count"] < 5, "Taring clears previous measurement history")
        code, body = call("scale.calibrate", grams=100)
        saved = json.loads(calibration_file.read_text(encoding="utf-8"))
        check(code == 200 and saved["reference_grams"] == 100
              and saved["divisor"] != 0, "scale.calibrate saves calibration to disk")
        code, _ = call("scale.calibrate", grams=200)
        overwritten = json.loads(calibration_file.read_text(encoding="utf-8"))
        check(code == 200 and overwritten["reference_grams"] == 200,
              "Recalibration replaces the saved calibration")
        code, _ = call("scale.calibrate", grams=-1)
        check(code == 400, "Negative reference masses are rejected")
        code, body = call("scale.csv", points=0)
        check(code == 200 and body["result"]["content"].startswith("time,weight_g"),
              "scale.csv includes a header")
        code, body = call("scale.diagnose", mode="scan")
        check(code == 200 and any("FOUND" in l for l in body["result"]["lines"]),
              "scale.diagnose scan")
        code, _ = call("scale.diagnose", mode="nope")
        check(code == 400, "Invalid diagnostic modes are rejected")
        code, _ = call("scale.set_stream", hz=99)
        check(code == 400, "Stream frequency is limited to 20 Hz")

        print("\n[8] Global operations and error handling")
        code, body = call("motors.stop_all")
        check(code == 200 and body["result"]["scale"]["stream_hz"] == 0,
              "motors.stop_all also stops scale streaming")
        from backend.transport import Link, Pending
        stop_commands = [row["text"] for row in get_link().log[-6:]
                         if row["kind"] == "tx"]
        check("X" in stop_commands and "W,STREAM,0" in stop_commands,
              "Stop all sends an explicit stream-stop command for older firmware")
        code, body = call("scale.restart")
        check(code == 200 and body["result"]["hz"] == cfg["scale"]["stream_hz"],
              "scale.restart resumes scale streaming")
        isolated_link = Link()
        stray_pending = Pending(command="V,3,200", terminators=("OK", "ERR"))
        isolated_link._pending = stray_pending
        isolated_link._handle_line("ERR,timeout(no response)")
        check(not stray_pending.done.is_set() and not stray_pending.lines,
              "A scale-stream timeout does not fail an unrelated motor command")
        code, body = call("scale.read", nonsense=1)
        check(code == 400, "Unknown parameters are rejected")
        res = client.post("/fn/does.not.exist", json={})
        check(res.status_code == 400, "Unknown operations return HTTP 400")
        st = client.get("/api/state").json()
        check(st["connected"] and st["simulated"], "GET /api/state")
        check(client.get("/").status_code == 200, "GET / serves the interface")
        check(client.get("/docs").status_code == 200, "GET /docs serves the documentation index")
        for name in ("index.html", "architecture.html", "wiring-motors.html",
                     "wiring-valves.html", "wiring-pump.html", "wiring-scale.html"):
            check(client.get(f"/docs/{name}").status_code == 200, f"Documentation is accessible: {name}")
        check(client.get("/docs/../config/hardware.yaml").status_code in (400, 404),
              "Path traversal is rejected")
        check(client.get("/api/docs").status_code == 200, "Swagger is available at /api/docs")

        # Simulate an Arduino reset and reconnect to verify calibration is restored to RAM.
        persisted = json.loads(calibration_file.read_text(encoding="utf-8"))
        persisted["divisor"] = 333.25
        persisted["offset"] = 0
        calibration_file.write_text(json.dumps(persisted), encoding="utf-8")
        reconnect = client.post("/api/reconnect")
        cfg_lines = get_link().send("W,CFG")
        check(reconnect.status_code == 200 and reconnect.json()["calibration_restored"]
              and any("div=333.2500" in line for line in cfg_lines),
              "Reconnection restores the saved calibration")

        docs_dir = ROOT / "docs"
        pages = {p.name for p in docs_dir.glob("*.html")}
        bad = []
        for page in pages:
            html = (docs_dir / page).read_text(encoding="utf-8")
            for href in set(re.findall(r'href="([^#/][^"]*)"', html)):
                if not (docs_dir / href).exists():
                    bad.append(f"{page} → {href}")
        check(not bad, "Documentation links resolve", "; ".join(bad[:3]))
        check((ROOT / "docs" / "index.html").is_file(), "Current documentation index exists")

        print("\n[9] SSE Live event stream")
        import json as _json
        got = {"weight": 0, "state": 0, "log": 0}
        content_type = ""
        with client.stream("GET", "/api/stream", timeout=8.0) as resp:
            content_type = resp.headers.get("content-type", "")
            deadline = time.time() + 5
            for line in resp.iter_lines():
                if time.time() > deadline:
                    break
                if not line.startswith("data: "):
                    continue
                ev = _json.loads(line[6:])
                got[ev["type"]] = got.get(ev["type"], 0) + 1
                if got["weight"] >= 8 and got["state"] >= 1:
                    break
        check(content_type.startswith("text/event-stream"),
              "SSE content type is correct", content_type)
        check(got["state"] >= 1, "SSE publishes state events")
        check(got["weight"] >= 8, f"SSE publishes weight events ({got['weight']} samples)")

        print("\n[10] Frontend and backend consistency")
        page = client.get("/").text
        used = set(re.findall(r'call\("([a-z_]+\.[a-z_]+)"', page))
        missing = sorted(used - set(names))
        check(not missing, "All frontend operations exist in the backend", ", ".join(missing))
        check("hardware.yaml" not in page or "/api/config" in page,
              "Frontend loads configuration from /api/config")
        check('CFG.motors.groups' in page and 'CFG.valves.channels' in page,
              "Frontend device lists come from configuration")
        check('call("valves.' not in page and "function valveCard" not in page,
              "Frontend does not expose manual solenoid valve control")
        check("weightTarget" in page and "weightPresets-" in page
              and "weightTarget[g.id] + v" in page
              and "target_options_g" not in page,
              "Mass and volume targets are independent and use incremental shortcuts")
        check("[10, 5, 1, -10, -5, -1]" in page
              and "dose[g.id] + v" in page
              and page.count("[10, 5, 1, -10, -5, -1]") == 2
              and page.count('class="presets six"') == 2,
              "Volume and mass targets support +10/+5/+1 and -10/-5/-1 adjustments")
        check("volumeHint" not in page and "weightHint" not in page
              and "Flow coefficient" not in page and "Shared step rate" not in page
              and "Driver PUL" not in page and "<small>D" not in page,
              "Pump cards omit internal conversion settings and pin labels")
        check("dose-mode+.dose-mode" in page
              and 'id="weightDn-' in page and 'id="weightUp-' in page,
              "Volume and mass controls share width, spacing, and adjustment layout")
        check('call("motors.clean"' in page and "Automatic cleaning · Water → OUT" in page
              and "main.appendChild(cleaningCard())" in page,
              "The cleaning card is at the top and invokes the backend operation")
        check("Calibration settings saved" in page,
              "Frontend confirms successful calibration persistence")

        client.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    sim_cfg.unlink(missing_ok=True)
    calibration_file.unlink(missing_ok=True)
    print(f"\nPassed: {len(PASS)}; failed: {len(FAIL)}")
    for label in FAIL:
        print("   Failed: " + label)
    return 1 if FAIL else 0


def main() -> int:
    previous = {key: os.environ.get(key)
                for key in ("LAB_CONFIG", "SCALE_CALIBRATION_FILE")}
    try:
        with tempfile.TemporaryDirectory(prefix="laboratory-selftest-") as work:
            return _run(Path(work))
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
