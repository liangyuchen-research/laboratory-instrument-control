"""Record a gravimetric dose from the running (simulated) backend for plotting.

Start the service first (``python -m backend.main``), then:

    python tools/record_dose_trace.py --motor 3 --target-g 10 --out docs/figures/gravimetric_dose_trace.json
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request
from pathlib import Path


def get_state(base: str) -> dict:
    with urllib.request.urlopen(f"{base}/api/state", timeout=5) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--motor", type=int, default=3)
    parser.add_argument("--target-g", type=float, default=10.0)
    parser.add_argument("--out", default="docs/figures/gravimetric_dose_trace.json")
    args = parser.parse_args()

    samples: list[tuple[float, float]] = []
    stop = threading.Event()

    def sampler() -> None:
        while not stop.is_set():
            try:
                samples.append((time.time(), float(get_state(args.base)["scale"]["grams"])))
            except Exception:  # noqa: BLE001 - keep sampling through transient errors
                pass
            time.sleep(0.1)

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    time.sleep(1.0)
    body = json.dumps({"motor": args.motor, "target_g": args.target_g}).encode()
    request = urllib.request.Request(f"{args.base}/fn/motors.dose_by_weight", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        result = json.loads(response.read())["result"]
    time.sleep(1.0)
    stop.set()
    thread.join()
    record = {
        "motor": args.motor, "target_g": args.target_g, "baseline_g": result["baseline_g"],
        "dispensed_g": result["dispensed_g"], "error_g": result["error_g"],
        "tolerance_g": result["tolerance_g"], "elapsed_s": result["elapsed_s"],
        "simulated": True, "samples": samples,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(record), encoding="utf-8")
    print(f"dispensed {result['dispensed_g']:.3f} g (error {result['error_g']:+.3f} g) in {result['elapsed_s']:.1f} s -> {args.out}")


if __name__ == "__main__":
    main()
