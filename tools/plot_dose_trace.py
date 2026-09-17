"""Plot a gravimetric-dosing trajectory recorded from the simulator.

Record a trace by polling ``GET /api/state`` at 10 Hz while ``motors.dose_by_weight``
runs (see ``tools/record_dose_trace.py``), then:

    python tools/plot_dose_trace.py docs/figures/gravimetric_dose_trace.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np


def main(path: str) -> None:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    samples = np.array([(s[0], s[1]) for s in record["samples"]], dtype=float)
    target = float(record["target_g"])
    baseline = float(record["baseline_g"])
    tol = float(record.get("tolerance_g", 0.05))
    t = samples[:, 0] - samples[0, 0]
    dispensed = samples[:, 1] - baseline

    mpl.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
        "font.size": 9, "axes.labelsize": 9.5, "xtick.labelsize": 8.5, "ytick.labelsize": 8.5,
        "legend.fontsize": 8, "axes.linewidth": 0.7, "xtick.direction": "in", "ytick.direction": "in",
        "xtick.top": True, "ytick.right": True, "legend.frameon": False,
        "savefig.bbox": "tight", "savefig.pad_inches": 0.03,
    })
    fig, ax = plt.subplots(figsize=(6.4, 3.0), constrained_layout=True)
    ax.axhspan(target - tol, target + tol, color="#009E73", alpha=0.18, lw=0, label=f"target ± {tol:g} g")
    ax.axhline(target, color="#009E73", lw=0.8, ls="--")
    fast_end = target - min(2.0, 0.25 * target)
    trim_start = target - min(0.5, 0.05 * target)
    for level, text in ((fast_end, "40 → 10 mL/min"), (trim_start, "10 → 3 mL/min")):
        ax.axhline(level, color="0.6", lw=0.6, ls=":")
        ax.text(t.max() * 0.995, level - 0.15, text, ha="right", va="top", fontsize=7.5, color="0.35")
    ax.plot(t, dispensed, color="#0072B2", lw=1.3, label="dispensed mass (10 Hz scale stream)")
    final = float(record["dispensed_g"])
    ax.annotate(f"final {final:.3f} g\nerror {final - target:+.3f} g", xy=(t[-1], dispensed[-1]),
                xytext=(0.6, 0.5), textcoords="axes fraction", fontsize=8,
                arrowprops={"arrowstyle": "-", "color": "0.4", "lw": 0.6})
    ax.set_xlabel("Time since command (s)")
    ax.set_ylabel("Dispensed mass (g)")
    ax.set_xlim(0, t.max())
    ax.set_ylim(-0.3, target + 1.2)
    ax.legend(loc="lower right")
    ax.text(0.02, 0.96, f"device simulator, motor {record['motor']}, target {target:g} g", transform=ax.transAxes, va="top", fontsize=8, color="0.3")
    out = Path(path).with_suffix(".png")
    fig.savefig(out, dpi=200)
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/figures/gravimetric_dose_trace.json")
