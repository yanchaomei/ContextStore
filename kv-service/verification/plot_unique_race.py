"""Publication-size timeline and exact-overlap summary from saved RXE logs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


BLUE = "#0072B2"
VERMILION = "#D55E00"
GREY = "#7F8790"
PALE = "#E69F00"


def fields(line: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in line.split(",")[1:] if "=" in part)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text())
    has_put_end = audit["totals"].get("put_overlap_reads") is not None
    put_scope = audit["runs"][0]["put_interval_scope"] if has_put_end else None
    if has_put_end and put_scope not in ("prepare_plus_sdk_call", "sdk_call"):
        raise ValueError("unknown measured PUT interval scope")
    is_sdk_call = put_scope == "sdk_call"
    runs = []
    for index in range(1, len(audit["runs"]) + 1):
        lines = (args.logs / f"run{index}.log").read_text().splitlines()
        writes = [fields(line) for line in lines if line.startswith("UNIQUE_WRITE,")]
        reads = [fields(line) for line in lines if line.startswith("UNIQUE_READ,")]
        if len(writes) != 15 or len(reads) != audit["runs"][index - 1]["read_attempts"]:
            raise ValueError("plot inputs disagree with audited raw events")
        runs.append((writes, reads))

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.labelsize": 9,
            "axes.titlesize": 10,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    fig = plt.figure(figsize=(9.2, 4.4), constrained_layout=True)
    grid = fig.add_gridspec(1, 2, width_ratios=(1.9, 1.0))
    ax = fig.add_subplot(grid[0, 0])
    summary = fig.add_subplot(grid[0, 1])

    writes, reads = runs[0]
    max_read = max(int(item["end_ns"]) for item in reads) / 1e6
    for item in writes:
        start = int(item["start_ns"]) / 1e6
        end = int(item["put_end_ns"] if has_put_end else item["end_ns"]) / 1e6
        ax.axvspan(start, end, color=PALE, alpha=0.16, linewidth=0, zorder=0)
        ax.hlines(-1.25, start, end, color=PALE, linewidth=4.5, zorder=2)
    for item in reads:
        attempt = int(item["attempt"])
        start = int(item["start_ns"]) / 1e6
        end = int(item["end_ns"]) / 1e6
        color = BLUE if item["result"] == "accepted" else VERMILION
        ax.hlines(attempt, start, end, color=color, linewidth=3, zorder=3)
    ax.set_ylim(len(reads) - 0.15, -2.1)
    ax.set_xlim(0, max_read * 1.02)
    ax.set_xlabel("Elapsed monotonic time (ms)")
    ax.set_ylabel("Read attempt, run 1")
    tick_marks = list(range(0, len(reads), 5))
    if len(reads) - 1 not in tick_marks:
        tick_marks.append(len(reads) - 1)
    ax.set_yticks(tick_marks)
    ax.grid(axis="x", color="#E5E8EA", linewidth=0.55)
    ax.set_title("A  |  Timestamped write/read intervals", loc="left", fontweight="bold")
    ax.legend(
        handles=[
            Line2D(
                [0], [0], color=PALE, lw=4,
                label=("SDK PUT call" if is_sdk_call else "PUT attempt (prepare + SDK call)")
                if has_put_end else "PUT + Lookup round",
            ),
            Line2D([0], [0], color=BLUE, lw=3, label="Accepted generation"),
            Line2D([0], [0], color=VERMILION, lw=3, label="Safe rejection"),
        ],
        loc="lower left",
        frameon=False,
        fontsize=8,
    )

    labels = [f"Run {index}" for index in range(1, len(runs) + 1)]
    accepted_key = "put_overlap_accepted" if has_put_end else "round_overlap_accepted"
    rejected_key = "put_overlap_rejected" if has_put_end else "round_overlap_rejected"
    overlap_key = "put_overlap_reads" if has_put_end else "writer_round_overlap_reads"
    overlap_accepted = [run[accepted_key] for run in audit["runs"]]
    overlap_rejected = [run[rejected_key] for run in audit["runs"]]
    stable = [
        run["read_attempts"] - run[overlap_key]
        for run in audit["runs"]
    ]
    positions = range(len(runs))
    summary.bar(positions, overlap_accepted, color=BLUE, width=0.62, label="Accepted in overlap")
    summary.bar(
        positions,
        overlap_rejected,
        bottom=overlap_accepted,
        color=VERMILION,
        width=0.62,
        label="Rejected in overlap",
    )
    summary.bar(
        positions,
        stable,
        bottom=[a + b for a, b in zip(overlap_accepted, overlap_rejected)],
        color=GREY,
        width=0.62,
        label=("No SDK-call overlap" if is_sdk_call else "No attempt overlap")
        if has_put_end else "No round overlap",
    )
    for x, run in enumerate(audit["runs"]):
        summary.text(x, run["read_attempts"] + 0.4, str(run["read_attempts"]), ha="center", fontsize=8)
    summary.set_ylim(0, 26)
    summary.set_xticks(list(positions), labels)
    summary.set_ylabel("Read attempts")
    summary.set_title(
        ("B  |  SDK-call overlap" if is_sdk_call else "B  |  Client-attempt overlap")
        if has_put_end else "B  |  Writer-round overlap",
        loc="left", fontweight="bold",
    )
    summary.legend(frameon=False, fontsize=7.5, loc="upper right")
    summary.grid(axis="y", color="#E5E8EA", linewidth=0.55)
    summary.set_axisbelow(True)
    fig.suptitle(
        (
            f"Generation-unique direct reads: {audit['totals'][overlap_key]} "
            f"{'SDK-call' if is_sdk_call else 'client-attempt'} overlaps, "
            f"{audit['totals'][accepted_key]} accepted and {audit['totals'][rejected_key]} safely rejected"
            if has_put_end else
            f"Generation-unique reads: {audit['totals'][overlap_key]} writer-round overlaps (PUT + Lookup)"
        ),
        fontsize=11,
        fontweight="bold",
    )
    fig.text(
        0.5,
        -0.005,
        (
            (
                "Payload prepared before SDK call; server commit overlap is unproven. Two RXE paths, one VM host."
                if is_sdk_call else
                "Client PUT attempts include payload preparation; SDK-call/commit overlap is unproven. Two RXE paths, one VM host."
            )
            if has_put_end else
            "Writer-round intervals include post-PUT Lookup: overlap is an upper bound on PUT overlap."
        ),
        ha="center",
        fontsize=7.5,
        color="#535D65",
    )
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("svg", "pdf", "png"):
        fig.savefig(args.output_prefix.with_suffix(f".{suffix}"), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(args.output_prefix, "svg/pdf/png")


if __name__ == "__main__":
    main()
