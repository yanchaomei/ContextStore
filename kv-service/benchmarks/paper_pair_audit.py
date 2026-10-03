"""Audit independent paired blocks in the checked-in one/two-Rail Verbs data.

Reads raw request/batch CSVs, not rounded aggregate summaries, and deliberately
does not produce confidence intervals from only two or three process blocks.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path


PREFIXES = (
    "softroce-vm-paired",
    "softroce-vm-concurrent-64-128",
    "softroce-vm-concurrent-256",
)


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        result = list(csv.DictReader(handle))
    if not result:
        raise ValueError(f"empty CSV: {path}")
    return result


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def block_bootstrap_interval(ratios: list[float], draws: int = 20_000) -> tuple[float, float]:
    """Seeded percentile interval for the geometric mean of independent blocks."""
    log_ratios = [math.log(value) for value in ratios]
    rng = random.Random(20261003)
    means = sorted(
        math.exp(statistics.mean(rng.choices(log_ratios, k=len(log_ratios)))) for _ in range(draws)
    )
    return means[int(0.025 * draws)], means[int(0.975 * draws)]


def one_group(results: Path, prefix: str) -> tuple[list[dict], list[dict], dict]:
    summary_path = results / f"{prefix}-summary.csv"
    samples_path = results / f"{prefix}-samples.csv"
    environment_path = results / f"{prefix}-environment.json"
    runs = rows(summary_path)
    samples = rows(samples_path)
    environment = json.loads(environment_path.read_text())
    concurrent = "concurrency" in runs[0]
    batch_path = results / f"{prefix}-batches.csv" if concurrent else None
    batches = rows(batch_path) if batch_path else []

    grouped_runs: dict[tuple[int, int, int], list[dict[str, str]]] = defaultdict(list)
    for run in runs:
        key = (int(run["size_mib"]), int(run.get("concurrency", 1)), int(run["trial"]))
        grouped_runs[key].append(run)
    blocks = []
    for (size, concurrency, trial), pair in sorted(grouped_runs.items()):
        order = [int(run["rails"]) for run in pair]
        if order not in ([1, 2], [2, 1]):
            raise ValueError(f"missing or duplicate arm: {prefix} {size} {trial} {order}")
        by_rail = {int(run["rails"]): run for run in pair}
        if len({run["xxh3"] for run in pair}) != 1:
            raise ValueError("different object hashes within a paired block")

        arm_values = {}
        for rail_count in (1, 2):
            run = by_rail[rail_count]
            selected = [
                sample
                for sample in samples
                if int(sample["size_mib"]) == size
                and int(sample.get("concurrency", 1)) == concurrency
                and int(sample["trial"]) == trial
                and int(sample["rails"]) == rail_count
            ]
            count = int(run["batches" if concurrent else "iterations"]) * concurrency
            if len(selected) != count:
                raise ValueError(f"sample count disagrees: {prefix} {size} {trial} {rail_count}")
            if any(
                int(sample["bytes"]) != size * 1024 * 1024 or sample["xxh3"] != run["xxh3"]
                for sample in selected
            ):
                raise ValueError("sample byte count or hash disagrees with run")
            completed_bytes = size * 1024 * 1024 * count
            if int(run["rail0_bytes"]) + int(run["rail1_bytes"]) != completed_bytes or (
                rail_count == 2 and int(run["rail1_bytes"]) == 0
            ):
                raise ValueError("per-Rail byte conservation failed")

            if concurrent:
                selected_batches = [
                    batch
                    for batch in batches
                    if int(batch["size_mib"]) == size
                    and int(batch["concurrency"]) == concurrency
                    and int(batch["trial"]) == trial
                    and int(batch["rails"]) == rail_count
                ]
                if len(selected_batches) != int(run["batches"]):
                    raise ValueError("batch count disagrees with run")
                wall_us = sum(int(batch["wall_us"]) for batch in selected_batches)
                gib_per_s = completed_bytes / (wall_us / 1e6) / 1024**3
                if abs(gib_per_s - float(run["aggregate_gib_per_s"])) > 0.002:
                    raise ValueError("rounded aggregate throughput disagrees with raw batches")
                decisive_time_us = wall_us
            else:
                decisive_time_us = statistics.median(
                    int(sample["latency_us"]) for sample in selected
                )
                if decisive_time_us != int(run["median_us"]):
                    raise ValueError("run median disagrees with raw request samples")
                gib_per_s = (size / 1024) / (decisive_time_us / 1e6)

            arm_values[rail_count] = {
                "decisive_time_us": decisive_time_us,
                "throughput_gib_per_s": gib_per_s,
                "cpu_us_per_read": (int(run["cpu_user_us"]) + int(run["cpu_system_us"])) / count,
                "peak_rss_kb": int(run["peak_rss_kb"]),
                "raw_request_samples": count,
            }

        one = arm_values[1]
        two = arm_values[2]
        ratio = one["decisive_time_us"] / two["decisive_time_us"]
        # Both arms complete the same number of bytes; the time ratio is the
        # throughput ratio. It avoids rounding the CLI's printed GiB/s values.
        assert math.isclose(ratio, two["throughput_gib_per_s"] / one["throughput_gib_per_s"])
        blocks.append(
            {
                "dataset": prefix,
                "size_mib": size,
                "concurrency": concurrency,
                "trial": trial,
                "run_order": order,
                "object_xxh3": pair[0]["xxh3"],
                "single": one,
                "dual": two,
                "paired_throughput_ratio": ratio,
                "dual_cpu_over_single_cpu": two["cpu_us_per_read"] / one["cpu_us_per_read"],
                "dual_minus_single_peak_rss_kb": two["peak_rss_kb"] - one["peak_rss_kb"],
            }
        )

    summaries = []
    cells = sorted({(block["size_mib"], block["concurrency"]) for block in blocks})
    for size, concurrency in cells:
        selected = [
            block
            for block in blocks
            if block["size_mib"] == size and block["concurrency"] == concurrency
        ]
        ratios = [block["paired_throughput_ratio"] for block in selected]
        interval = block_bootstrap_interval(ratios) if len(ratios) >= 10 else None
        summaries.append(
            {
                "dataset": prefix,
                "size_mib": size,
                "concurrency": concurrency,
                "independent_process_blocks": len(selected),
                "repeated_request_samples_per_arm": sum(
                    block["single"]["raw_request_samples"] for block in selected
                ),
                "paired_ratio_geometric_mean": math.exp(
                    statistics.mean(math.log(value) for value in ratios)
                ),
                "paired_ratio_min": min(ratios),
                "paired_ratio_max": max(ratios),
                "blocks_favoring_dual": sum(value > 1 for value in ratios),
                "blocks_favoring_single": sum(value < 1 for value in ratios),
                "ratio_95pct_block_bootstrap": interval,
                "interval_status": (
                    "20,000 seeded block-resampling draws; percentile interval"
                    if interval
                    else "not estimated: fewer than 10 independent blocks"
                ),
            }
        )
    inputs = {
        path.name: digest(path)
        for path in (summary_path, samples_path, environment_path)
        if path.exists()
    }
    if batch_path:
        inputs[batch_path.name] = digest(batch_path)
    return blocks, summaries, {"environment": environment, "input_sha256": inputs}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--prefix", action="append", help="dataset prefix; defaults to legacy datasets"
    )
    args = parser.parse_args()
    all_blocks = []
    all_summaries = []
    provenance = {}
    for prefix in args.prefix or PREFIXES:
        blocks, summaries, meta = one_group(args.results, prefix)
        all_blocks.extend(blocks)
        all_summaries.extend(summaries)
        provenance[prefix] = meta
    result = {
        "interpretation": "paired launched process blocks; confidence intervals only when n>=10, and no physical-HCA aggregation claim",
        "provenance": provenance,
        "blocks": all_blocks,
        "cells": all_summaries,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output, len(all_blocks), "paired blocks", len(all_summaries), "cells")


if __name__ == "__main__":
    main()
