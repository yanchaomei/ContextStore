"""Show every paired software-RoCE process block and block-bootstrap interval."""

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
    parser.add_argument("--title", default="Soft-RoCE: ten paired process blocks per workload")
    parser.add_argument(
        "--note",
        default="One server/client binary and object per cell; AB/BA order alternates. Dots are launched process blocks, not individual reads.",
    )
    args = parser.parse_args()
    data = json.loads(args.audit.read_text())
    cells = data["cells"]
    blocks = data["blocks"]
    if len(cells) != 4 or any(cell["independent_process_blocks"] != 10 for cell in cells):
        raise ValueError("expected exactly four cells with ten independent blocks each")

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
    fig, ax = plt.subplots(figsize=(7.7, 3.8), dpi=180)
    fig.subplots_adjust(left=0.095, right=0.985, top=0.83, bottom=0.26)
    ax.axhline(1.0, color="#a93737", linestyle="--", linewidth=1, zorder=1)
    ax.axvspan(1.5, 3.5, color="#f2f5f7", zorder=0)
    values = [block["paired_throughput_ratio"] for block in blocks]
    bottom = min(0.85, min(values) - 0.035)
    top = max(1.255, max(values) + 0.065)

    for position, cell in enumerate(cells):
        selected = [
            block
            for block in blocks
            if block["dataset"] == cell["dataset"]
            and block["size_mib"] == cell["size_mib"]
            and block["concurrency"] == cell["concurrency"]
        ]
        for i, block in enumerate(selected):
            jitter = (i - 4.5) * 0.042
            ax.scatter(
                position + jitter,
                block["paired_throughput_ratio"],
                s=19,
                color="#4a9cbb" if block["paired_throughput_ratio"] >= 1 else "#c17b61",
                edgecolor="white",
                linewidth=0.4,
                zorder=3,
                label="paired process block" if position == 0 and i == 0 else None,
            )
        low, high = cell["ratio_95pct_block_bootstrap"]
        estimate = cell["paired_ratio_geometric_mean"]
        ax.errorbar(
            position,
            estimate,
            yerr=[[estimate - low], [high - estimate]],
            fmt="D",
            markersize=5.5,
            color="#142c47",
            capsize=3,
            linewidth=1.5,
            zorder=4,
            label="geometric mean + 95% block bootstrap" if position == 0 else None,
        )
        ax.text(
            position,
            top - 0.03,
            f"{estimate:.3f}×\n{cell['blocks_favoring_dual']}/10 favor dual",
            ha="center",
            va="top",
            fontsize=7,
            color="#24394d",
        )

    ax.set_xlim(-0.55, 3.55)
    ax.set_ylim(bottom, top)
    ax.set_xticks(range(4))
    ax.set_xticklabels([f"{cell['size_mib']} MiB\nc{cell['concurrency']}" for cell in cells])
    ax.set_ylabel("two-Rail / one-Rail throughput")
    ax.set_title(
        args.title,
        loc="left",
        fontsize=10,
        fontweight="bold",
        pad=13,
    )
    ax.yaxis.grid(True, color="#dde5ea", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right", frameon=False, fontsize=7)
    fig.text(
        0.095,
        0.052,
        args.note,
        color="#566270",
        fontsize=7,
    )
    fig.text(
        0.095,
        0.028,
        "Intervals resample ten paired blocks (20,000 seeded draws); VM/RXE software results, not physical HCA aggregation.",
        color="#566270",
        fontsize=7,
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
