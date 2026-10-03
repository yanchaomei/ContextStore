"""Publication-style view of paired independent Soft-RoCE benchmark blocks."""

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
    audit = json.loads(args.audit.read_text())
    cells = audit["cells"]
    blocks = audit["blocks"]

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
    fig, ax = plt.subplots(figsize=(7.4, 3.4), dpi=180)
    fig.subplots_adjust(left=0.09, right=0.985, top=0.82, bottom=0.28)
    ax.axhline(1.0, color="#a93737", linewidth=1, linestyle="--", zorder=1)
    ax.axvspan(2.5, 7.5, color="#f2f5f7", zorder=0)

    for position, cell in enumerate(cells):
        selected = [
            block
            for block in blocks
            if block["dataset"] == cell["dataset"]
            and block["size_mib"] == cell["size_mib"]
            and block["concurrency"] == cell["concurrency"]
        ]
        for offset, block in enumerate(selected):
            jitter = (offset - (len(selected) - 1) / 2) * 0.12
            ax.scatter(
                position + jitter,
                block["paired_throughput_ratio"],
                s=26,
                color="#4a9cbb",
                edgecolor="white",
                linewidth=0.5,
                zorder=3,
                label="paired process block" if position == 0 and offset == 0 else None,
            )
        ax.scatter(
            position,
            cell["paired_ratio_geometric_mean"],
            marker="D",
            s=44,
            color="#142c47",
            zorder=4,
            label="geometric mean" if position == 0 else None,
        )
        ax.text(
            position,
            max(cell["paired_ratio_max"] + 0.017, 1.19),
            f"n={cell['independent_process_blocks']}",
            ha="center",
            va="bottom",
            color="#566270",
            fontsize=7,
        )

    ax.set_xlim(-0.55, len(cells) - 0.45)
    ax.set_ylim(0.95, 1.245)
    ax.set_xticks(range(len(cells)))
    ax.set_xticklabels([f"{c['size_mib']} MiB\nc{c['concurrency']}" for c in cells])
    ax.set_ylabel("two-Rail / one-Rail throughput")
    ax.set_title(
        "Soft-RoCE paired process blocks: modest gain, one negative block",
        loc="left",
        fontsize=10,
        fontweight="bold",
        pad=14,
    )
    ax.text(1, 1.255, "sequential", ha="center", fontsize=7, color="#566270")
    ax.text(5, 1.255, "concurrent", ha="center", fontsize=7, color="#566270")
    ax.yaxis.grid(True, color="#dde5ea", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(loc="upper left", frameon=False, ncol=2, fontsize=7)
    fig.text(
        0.09,
        0.045,
        "n = independent launched blocks (3 sequential, 2 concurrent); repeated reads are not independent. "
        "No confidence interval is asserted.",
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
