#!/usr/bin/env python3
"""Bundle config/hardware.yaml generates firmware/Arduino/hw_config.h.

This is IaC the last mile:pin is only written in YAML a place,firmware, backend, frontend Once the
Where are all three sides obtained from? The measurement program cannot be found on,”File writing D28, program writing D10”This kind of thing.

usage:
    python tools/gen_firmware_config.py          # produce
    python tools/gen_firmware_config.py --check  # only checks whether it has expired(CI use)

modified hardware.yaml after the foot position:
    1. Run this program
    2. use Arduino IDE Re-burn firmware/Arduino/Arduino.ino
    3. Restart the backend
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import load_config  # noqa: E402

HEADER = ROOT / "firmware" / "Arduino" / "hw_config.h"


def render(cfg: dict) -> str:
    motors = cfg["motors"]
    groups = motors["groups"]
    if len(groups) != 2:
        raise SystemExit("The current firmware is fixed to two sets of drives.,motors.groups must be exactly two")

    ids = [ch["id"] for g in groups for ch in g["channels"]]
    pins = [ch["pin"] for g in groups for ch in g["channels"]]
    if ids != pins:
        raise SystemExit("firmware assumes ”motor n Depend on D<n> control”,Please allow each channel id and pin same")
    if ids != list(range(min(ids), max(ids) + 1)):
        raise SystemExit("Motor numbers must be consecutive,For example 2~9")

    valves = cfg["valves"]["channels"]
    valve_links = {
        int(link["motor"]): int(link["valve"])
        for link in cfg["valves"]["motor_links"]
    }
    motor_valves = [valve_links.get(motor, 0) for motor in ids]
    cleaning = cfg["cleaning"]

    def motor_ppm(motor_id: int) -> float:
        for group in groups:
            for channel in group["channels"]:
                if int(channel["id"]) == motor_id:
                    return float(channel.get("pulses_per_ml", motors["pulses_per_ml"]))
        raise SystemExit(f"Cleaning motor not found {motor_id}")

    clean_flow = float(cleaning["flow_ml_min"])
    clean_water_sps = max(
        1, round(clean_flow * motor_ppm(int(cleaning["water_motor"])) / 60.0))
    clean_out_sps = max(
        1, round(clean_flow * motor_ppm(int(cleaning["out_motor"])) / 60.0))
    pump = cfg["syringe_pump"]
    scale = cfg["scale"]
    mb = scale["modbus"]
    a, b = groups[0]["driver"], groups[1]["driver"]

    lines = [
        "// ===================================================================",
        "//  hw_config.h — Depend on tools/gen_firmware_config.py automatically generated,Please do not modify by hand",
        "//  source:config/hardware.yaml",
        f"//  {cfg['meta']['name']} — {cfg['meta']['description']}",
        "// ===================================================================",
        "#ifndef HW_CONFIG_H",
        "#define HW_CONFIG_H",
        "",
        f"// ---------- two {motors.get('driver_model', 'DM542J')} ----------",
        f"#define PUL1 {a['pul']}",
        f"#define DIR1 {a['dir']}",
        f"#define ENA1 {a['ena']}",
        f"#define PUL2 {b['pul']}",
        f"#define DIR2 {b['dir']}",
        f"#define ENA2 {b['ena']}",
        "",
        "// ---------- relay:motor n Depend on D<n> control ----------",
        f"#define FIRST_MOTOR {min(ids)}",
        f"#define LAST_MOTOR  {max(ids)}",
        f"#define GROUP_B_FIRST {groups[1]['channels'][0]['id']}",
        "",
        f"#define MAX_SPS   {float(motors['max_sps']):.1f}",
        f"#define SETTLE_MS {int(motors['driver_settle_ms'])}",
        # Minimum pulse width required by driver(us).DM542J Specification 2.5us; 
        # TB6600 module is much slower,Narrow pulses will miss steps,So pull it 5us.
        f"#define MIN_PULSE_US {int(motors.get('min_pulse_us', 3))}",
        "",
        "// ---------- Convert to panel mL use ----------",
        f"#define PULSES_PER_ML   {float(motors['pulses_per_ml']):.6f}F",
        f"#define PULSES_PER_REV  {int(motors['pulses_per_rev'])}L",
        f"#define MAX_FLOW_UL_MIN {int(float(motors['max_flow_ml_min']) * 1000)}L",
        "",
        "// ---------- Automatic cleaning: water inlet front section → Same transfer → OUT Empty ----------",
        f"#define CLEAN_WATER_MOTOR {int(cleaning['water_motor'])}",
        f"#define CLEAN_OUT_MOTOR {int(cleaning['out_motor'])}",
        f"#define CLEAN_WATER_SPS {clean_water_sps}.0F",
        f"#define CLEAN_OUT_SPS {clean_out_sps}.0F",
        f"#define CLEAN_WATER_LEAD_MS {round(float(cleaning['water_lead_s']) * 1000)}UL",
        f"#define CLEAN_OVERLAP_MS {round(float(cleaning['overlap_s']) * 1000)}UL",
        f"#define CLEAN_OUT_TAIL_MS {round(float(cleaning['out_tail_s']) * 1000)}UL",
        "",
        "// ---------- solenoid valve ----------",
        f"#define NUM_VALVES {len(valves)}",
        "#define VALVE_PIN_LIST { " + ", ".join(str(v["pin"]) for v in valves) + " }",
        "#define MOTOR_VALVE_LIST { " + ", ".join(str(v) for v in motor_valves) + " }",
        f"#define VALVE_OPEN_BEFORE_MS {int(cfg['valves']['open_before_ms'])}UL",
        f"#define VALVE_CLOSE_AFTER_MS {int(cfg['valves']['close_after_ms'])}UL",
        "",
        "// ---------- syringe pump ----------",
        f"#define PUMP_SERIAL {pump['serial']}          // TX{pump['serial'][-1]}"
        f"=D{pump['tx_pin']}, RX{pump['serial'][-1]}=D{pump['rx_pin']}",
        f"#define PUMP_BAUD {int(pump['baud'])}UL",
        f"#define PUMP_ADDR {int(pump['address'])}",
        f"#define PUMP_FULL_STROKE {int(pump['full_stroke_steps'])}L",
        f"#define PUMP_DEFAULT_SYRINGE_UL {int(pump['default_syringe_ul'])}L",
        "",
        "// ---------- weight module ----------",
        f"#define SCALE_SERIAL {scale['serial']}         // TX{scale['serial'][-1]}"
        f"=D{scale['tx_pin']}, RX{scale['serial'][-1]}=D{scale['rx_pin']}",
        f"#define SCALE_DE_PIN {int(scale['de_pin'])}",
        f"#define SCALE_STREAM_HZ {int(scale['stream_hz'])}",
        f"#define SCALE_AVG_SAMPLES {int(scale['average_samples'])}",
        f"#define SCALE_STABLE_G {float(scale['stable_threshold_g']):.6f}F",
        f"#define SCALE_BAUD {int(mb['baud'])}L",
        f"#define SCALE_ADDR {int(mb['address'])}",
        f"#define SCALE_FUNC {int(mb['function_code'])}",
        f"#define SCALE_REG  {int(mb['register'])}",
        f"#define SCALE_FMT  {int(mb['format'])}",
        "",
        "#endif  // HW_CONFIG_H",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="Compare only,Do not write;Returned when the content expires 1")
    args = ap.parse_args()

    text = render(load_config())
    current = HEADER.read_text(encoding="utf-8") if HEADER.exists() else ""

    if args.check:
        if current == text:
            print("hw_config.h and hardware.yaml consistent")
            return 0
        print("hw_config.h Expired,Please execute python tools/gen_firmware_config.py")
        return 1

    if current == text:
        print("hw_config.h is the latest,No change")
        return 0
    HEADER.parent.mkdir(parents=True, exist_ok=True)
    HEADER.write_text(text, encoding="utf-8")
    print(f"has been written {HEADER.relative_to(ROOT)} —— Remember to re-burn Arduino")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
