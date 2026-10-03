"""Plot each audited physical single-HCA read as an exact stage decomposition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt


COLORS = {
    "lookup": "#7a8da3",
    "QP connect": "#50a5b1",
    "allocate": "#72bc87",
    "MR register": "#b4d5bd",
    "GET/CQ": "#174d77",
    "QP teardown": "#71a5c6",
    "assemble": "#e5a154",
    "checksum": "#efca7e",
    "publish": "#b15959",
    "other": "#d8dde3",
}


def disjoint_stages(sample: dict) -> dict[str, int]:
    transport = sample["stages_us"]["TRANSPORT"]
    staged = sample["stages_us"]["STAGED"]
    outer = sample["stages_us"]["OUTER"]
    values = {
        "lookup": outer["lookup"] + outer["post_lookup"],
        "QP connect": transport["connect"],
        "allocate": transport["allocation"],
        "MR register": transport["registration"],
        "GET/CQ": transport["get"],
        "QP teardown": transport["teardown"],
        "assemble": staged["assembly"],
        "checksum": staged["checksum"],
        "publish": outer["publish"],
    }
    values["other"] = sample["wall_us"] - sum(values.values())
    if values["other"] < 0 or values["other"] / sample["wall_us"] > 0.1:
        raise ValueError("unaccounted end-to-end time outside expected 0-10% range")
    return values


def main() -> None:
    plt.switch_backend("Agg")
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text())
    traced = [process for process in audit["processes"] if "trace" in process["log"]]
    controls = [process for process in audit["processes"] if "control" in process["log"]]

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
    fig, (ax, right) = plt.subplots(
        1, 2, figsize=(9.8, 4.7), dpi=180, gridspec_kw={"width_ratios": [2.35, 1]}
    )
    fig.subplots_adjust(left=0.075, right=0.98, top=0.83, bottom=0.20, wspace=0.32)

    samples = [
        (block, sample) for block, process in enumerate(traced, 1) for sample in process["samples"]
    ]
    for index, (block, sample) in enumerate(samples):
        left = 0
        for name, micros in disjoint_stages(sample).items():
            ax.barh(
                index,
                micros / 1000,
                left=left / 1000,
                height=0.72,
                color=COLORS[name],
                linewidth=0,
                label=name if index == 0 else None,
            )
            left += micros
    ax.axhline(4.5, color="#8795a1", linewidth=0.8, linestyle=":")
    ax.set_yticks(range(len(samples)))
    ax.set_yticklabels([f"B{block} / {sample['iteration']}" for block, sample in samples])
    ax.invert_yaxis()
    ax.set_xlim(0, 190)
    ax.set_xlabel("end-to-end read latency (ms)")
    ax.set_title("A. Every traced read (one physical HCA)", loc="left", fontweight="bold")
    ax.xaxis.grid(True, color="#e4e9ed", linewidth=0.6)
    ax.set_axisbelow(True)

    positions = [0, 1]
    for i, (trace, control) in enumerate(zip(traced, controls)):
        right.plot(
            positions,
            [trace["median_wall_us"] / 1000, control["median_wall_us"] / 1000],
            color=["#237c93", "#ad7844"][i],
            marker="o",
            linewidth=1.5,
            label=f"block {i+1}",
        )
    right.set_xticks(positions, ["trace", "control"])
    right.set_xlim(-0.25, 1.25)
    right.set_ylim(130, 175)
    right.set_ylabel("process median (ms)")
    right.set_title("B. Probe overhead control", loc="left", fontweight="bold")
    right.yaxis.grid(True, color="#e4e9ed", linewidth=0.6)
    right.set_axisbelow(True)
    right.legend(frameon=False, loc="upper left")

    handles, labels = ax.get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        ncol=5,
        loc="upper left",
        bbox_to_anchor=(0.075, 0.94),
        frameon=False,
        fontsize=7,
        columnspacing=1.2,
    )
    fig.text(
        0.075,
        0.052,
        "64 MiB / 16 stripes, node1 → node2, mlx5_1 port 1, warm-slab eligible. "
        "Five measured reads after one warmup per process.",
        fontsize=7,
        color="#566270",
    )
    fig.text(
        0.075,
        0.028,
        "'Other' reconciles each bar exactly. Two process blocks are descriptive only.",
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
