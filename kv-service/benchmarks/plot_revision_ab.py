"""Plot the physical single-HCA code-revision ablation by paired process block."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt


def main() -> None:
    plt.switch_backend("Agg")
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.audit.read_text())
    blocks = data["blocks"]
    if len(blocks) != 10:
        raise ValueError("expected ten paired process blocks")

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    fig, (left, right) = plt.subplots(1, 2, figsize=(8.4, 3.7), dpi=180)
    fig.subplots_adjust(left=0.075, right=0.98, top=0.82, bottom=0.23, wspace=0.28)
    for block in blocks:
        color = "#4a9cbb" if block["trial"] % 2 else "#b88051"
        left.plot(
            [0, 1],
            [block["baseline_median_us"] / 1000, block["candidate_median_us"] / 1000],
            color=color,
            linewidth=1.1,
            marker="o",
            markersize=3,
            alpha=0.85,
        )
        right.scatter(
            block["trial"],
            block["speedup"],
            color=color,
            s=24,
            edgecolor="white",
            linewidth=0.5,
            zorder=3,
        )
    left.set_xticks([0, 1], ["original", "no-copy one Rail"])
    left.set_ylabel("process median latency (ms)")
    left.set_xlim(-0.2, 1.2)
    left.set_ylim(75, 195)
    left.set_title("A. Same object/HCA, paired processes", loc="left", fontweight="bold")
    left.yaxis.grid(True, color="#e1e7ec", linewidth=0.6)
    left.set_axisbelow(True)

    gm = data["geometric_mean_speedup"]
    low, high = data["speedup_95pct_block_bootstrap"]
    right.axhline(1.0, color="#a93737", linestyle="--", linewidth=1)
    right.axhline(gm, color="#142c47", linewidth=1.3)
    right.fill_between([0.5, 10.5], low, high, color="#dce8ef", alpha=0.7)
    right.text(
        5.5,
        gm + 0.035,
        f"GM {gm:.3f}×; 95% block interval {low:.3f}–{high:.3f}×",
        ha="center",
        fontsize=7,
    )
    right.set_xticks(range(1, 11))
    right.set_xlim(0.5, 10.5)
    right.set_ylim(0.95, 1.9)
    right.set_ylabel("original / no-copy latency")
    right.set_xlabel("paired process block")
    right.set_title("B. All ten blocks favor no-copy", loc="left", fontweight="bold")
    right.yaxis.grid(True, color="#e1e7ec", linewidth=0.6)
    right.set_axisbelow(True)
    fig.text(
        0.075,
        0.046,
        "64 MiB / 16 stripes, node1 → node2, one physical HCA. AB/BA order alternates; five reads after one warmup per arm.",
        fontsize=7,
        color="#566270",
    )
    fig.text(
        0.075,
        0.023,
        "Colors indicate arm order. The interval resamples ten launched process blocks; this is a code-revision ablation, not dual-HCA scaling.",
        fontsize=7,
        color="#566270",
    )
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("svg", "png", "pdf"):
        path = args.output_prefix.with_suffix(f".{suffix}")
        fig.savefig(path, dpi=220)
        if suffix == "svg":
            path.write_text(
                "\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n"
            )
    plt.close(fig)


if __name__ == "__main__":
    main()
