"""Recompute the physical one-Rail code-revision ablation from raw arm logs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import statistics
from pathlib import Path


SAMPLE = re.compile(
    r"^sample,[^\n]*iteration=(\d+),bytes=(\d+),latency_us=(\d+),[^\n]*xxh3=([0-9a-f]+)$", re.M
)
SUMMARY = re.compile(
    r"^summary,[^\n]*bytes_per_iter=(\d+),iters=(\d+),median_us=(\d+),cpu_user_us=(\d+),cpu_system_us=(\d+),peak_rss_kb=(\d+),rail_bytes=\[(\d+)\]$",
    re.M,
)


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.results / "revision-ab"
    environment = json.loads((source / "environment.json").read_text())
    summaries = rows(source / "summary.csv")
    csv_samples = rows(source / "samples.csv")
    blocks = []
    hashes = {}
    for trial in range(1, int(environment["trials"]) + 1):
        pair = [row for row in summaries if int(row["trial"]) == trial]
        if len(pair) != 2 or {row["arm"] for row in pair} != {"baseline", "candidate"}:
            raise ValueError(f"missing/duplicate revision arm in block {trial}")
        by_arm = {row["arm"]: row for row in pair}
        expected_order = ("baseline", "candidate") if trial % 2 else ("candidate", "baseline")
        if (
            tuple(row["arm"] for row in sorted(pair, key=lambda row: int(row["order_position"])))
            != expected_order
        ):
            raise ValueError(f"wrong arm order in block {trial}")
        for arm in expected_order:
            row = by_arm[arm]
            path = source / f"trial-{trial:02d}-{arm}.log"
            text = path.read_text()
            samples = SAMPLE.findall(text)
            reported = SUMMARY.search(text)
            from_csv = [
                sample
                for sample in csv_samples
                if int(sample["trial"]) == trial and sample["arm"] == arm
            ]
            if (
                len(samples) != int(environment["iterations_per_arm"])
                or reported is None
                or len(from_csv) != len(samples)
            ):
                raise ValueError(f"incomplete raw arm: {trial} {arm}")
            if [(int(i), int(n), int(t), h) for i, n, t, h in samples] != [
                (int(s["iteration"]), int(s["bytes"]), int(s["latency_us"]), s["xxh3"])
                for s in from_csv
            ]:
                raise ValueError(f"CSV differs from raw arm: {trial} {arm}")
            size, count, median, cpu_user, cpu_system, rss, rail_bytes = map(int, reported.groups())
            if (
                size != environment["object_bytes"]
                or count != len(samples)
                or median != statistics.median(int(s[2]) for s in samples)
                or int(row["median_us"]) != median
                or int(row["cpu_user_us"]) != cpu_user
                or int(row["cpu_system_us"]) != cpu_system
                or int(row["peak_rss_kb"]) != rss
                or rail_bytes != size * count
                or any(int(s[1]) != size or s[3] != environment["object_xxh3"] for s in samples)
            ):
                raise ValueError(f"raw summary/object mismatch: {trial} {arm}")
            hashes[str(path.relative_to(args.results))] = sha256(path)
        old, new = by_arm["baseline"], by_arm["candidate"]
        blocks.append(
            {
                "trial": trial,
                "order": expected_order,
                "baseline_median_us": int(old["median_us"]),
                "candidate_median_us": int(new["median_us"]),
                "speedup": int(old["median_us"]) / int(new["median_us"]),
                "baseline_cpu_us_per_read": (int(old["cpu_user_us"]) + int(old["cpu_system_us"]))
                / int(environment["iterations_per_arm"]),
                "candidate_cpu_us_per_read": (int(new["cpu_user_us"]) + int(new["cpu_system_us"]))
                / int(environment["iterations_per_arm"]),
                "baseline_peak_rss_kb": int(old["peak_rss_kb"]),
                "candidate_peak_rss_kb": int(new["peak_rss_kb"]),
            }
        )
    ratios = [block["speedup"] for block in blocks]
    if len(ratios) != len(environment["paired_ratios"]) or any(
        not math.isclose(left, right) for left, right in zip(ratios, environment["paired_ratios"])
    ):
        raise ValueError("collector ratios disagree with raw audit")
    rng = random.Random(20261003)
    log_ratios = [math.log(ratio) for ratio in ratios]
    draws = sorted(
        math.exp(statistics.mean(rng.choices(log_ratios, k=len(ratios)))) for _ in range(20_000)
    )
    result = {
        "interpretation": "same physical HCA/object/server, AB/BA code-revision comparison; process blocks are the unit; no multi-HCA claim",
        "environment": environment,
        "source_sha256": {
            "summary.csv": sha256(source / "summary.csv"),
            "samples.csv": sha256(source / "samples.csv"),
            "environment.json": sha256(source / "environment.json"),
            **hashes,
        },
        "blocks": blocks,
        "geometric_mean_speedup": math.exp(statistics.mean(log_ratios)),
        "speedup_95pct_block_bootstrap": [draws[500], draws[19_500]],
        "blocks_favoring_candidate": sum(ratio > 1 for ratio in ratios),
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output, len(blocks), "paired revision blocks")


if __name__ == "__main__":
    main()
