"""Figure for five balanced Rail-mode controls on one checksummed object."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt


ARM_LABELS = {
    "single_r0": "one r0",
    "single_r1": "one r1",
    "packed": "packed\ngreedy",
    "packed_contiguous": "packed\ncontiguous",
    "direct": "direct\ncontiguous",
}
COMPARISONS = (
    ("direct_over_contiguous_throughput", "direct / same plan\npacked"),
    ("direct_over_r0_throughput", "direct / one r0"),
    ("direct_over_r1_throughput", "direct / one r1"),
    ("contiguous_over_packed_throughput", "contiguous /\ngreedy"),
)


def main() -> None:
    plt.switch_backend("Agg")
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text())
    blocks = audit["blocks"]
    arms = tuple(audit["environment"]["arms"])
    if len(blocks) != 10 or tuple(ARM_LABELS) != arms:
        raise ValueError("expected ten complete five-arm blocks")
    size_mib = audit["environment"]["object_bytes"] // 1024**2

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
    fig, (left, right) = plt.subplots(1, 2, figsize=(10.3, 4.2), dpi=180)
    fig.subplots_adjust(left=0.07, right=0.985, top=0.83, bottom=0.24, wspace=0.28)
    for block in blocks:
        values = [block["median_us"][arm] / 1000 for arm in arms]
        left.plot(
            range(5),
            values,
            color="#a5afb9",
            marker="o",
            markersize=2.6,
            linewidth=0.65,
            alpha=0.75,
        )
    for position, arm in enumerate(arms):
        median = statistics.median(block["median_us"][arm] / 1000 for block in blocks)
        left.scatter(position, median, color="#183a57", marker="D", s=40, zorder=4)
    left.set_xticks(range(5), [ARM_LABELS[arm] for arm in arms], fontsize=7)
    left.set_xlim(-0.28, 4.28)
    left.set_ylabel("process median read latency (ms)")
    left.set_title("A. Same binary, object and server", loc="left", fontweight="bold")
    left.yaxis.grid(True, color="#e2e8ed", linewidth=0.6)
    left.set_axisbelow(True)

    ratios = [block[key] for key, _ in COMPARISONS for block in blocks]
    bottom = min(0.85, min(ratios) - 0.045)
    top = max(1.25, max(ratios) + 0.07)
    right.axhline(1, color="#aa3c3c", linestyle="--", linewidth=1)
    for position, (key, _label) in enumerate(COMPARISONS):
        summary = audit["comparisons"][key]
        for i, block in enumerate(blocks):
            value = block[key]
            jitter = (i - 4.5) * 0.044
            right.scatter(
                position + jitter,
                value,
                color="#4395b6" if value >= 1 else "#c27c61",
                s=18,
                edgecolor="white",
                linewidth=0.4,
                zorder=3,
            )
        gm = summary["geometric_mean"]
        low, high = summary["block_bootstrap_95pct"]
        right.errorbar(
            position,
            gm,
            yerr=[[gm - low], [high - gm]],
            color="#183a57",
            fmt="D",
            markersize=5,
            capsize=3,
            linewidth=1.3,
            zorder=4,
        )
        right.text(
            position,
            top - 0.03,
            f"{gm:.3f}×\n{summary['blocks_above_one']}/10 > 1",
            ha="center",
            va="top",
            fontsize=7,
            color="#29465c",
        )
    right.set_xticks(range(4), [label for _key, label in COMPARISONS], fontsize=7)
    right.set_xlim(-0.52, 3.52)
    right.set_ylim(bottom, top)
    right.set_ylabel("throughput ratio")
    right.set_title("B. Primary and secondary paired contrasts", loc="left", fontweight="bold")
    right.yaxis.grid(True, color="#e2e8ed", linewidth=0.6)
    right.set_axisbelow(True)
    fig.suptitle(
        f"{size_mib} MiB: direct private staging with both single-Rail controls",
        x=0.07,
        ha="left",
        fontsize=10,
        fontweight="bold",
    )
    fig.text(
        0.07,
        0.048,
        "Ten launched process blocks; five cyclic arm orders plus their reverses. One warmup and five measured reads per arm.",
        fontsize=7,
        color="#566270",
    )
    fig.text(
        0.07,
        0.025,
        "Intervals resample paired blocks (20,000 seeded draws). Software RXE and one virtual disk; no physical multi-HCA claim.",
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
