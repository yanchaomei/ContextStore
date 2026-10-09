"""Audit balanced three- or five-arm Rail process blocks from raw logs."""

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


ARMS = ("single", "packed", "direct")
FIVE_ARMS = ("single_r0", "single_r1", "packed", "packed_contiguous", "direct")
ASSIGNMENT = re.compile(r"^plan_assignment,id=([^,]+),stripes=\[([0-9, ]+)\],bytes=(\d+)$", re.M)


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key_values(line: str) -> dict[str, str]:
    return dict(field.split("=", 1) for field in line.split(",") if "=" in field)


def bootstrap(ratios: list[float]) -> list[float]:
    rng = random.Random(20261009)
    logged = [math.log(value) for value in ratios]
    draws = sorted(
        math.exp(statistics.mean(rng.choices(logged, k=len(logged)))) for _ in range(20_000)
    )
    return [draws[500], draws[19_500]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    environment_path = args.results / "environment.json"
    summary_path = args.results / "summary.csv"
    samples_path = args.results / "samples.csv"
    environment = json.loads(environment_path.read_text())
    summaries = csv_rows(summary_path)
    samples = csv_rows(samples_path)
    blocks = []
    log_hashes = {}
    rail_resources = []
    nblocks = int(environment["blocks"])
    arms = tuple(environment["arms"])
    if arms not in (ARMS, FIVE_ARMS):
        raise ValueError("unrecognized experiment arms")
    five = arms == FIVE_ARMS
    if nblocks != (10 if five else 12) or len(summaries) != len(arms) * nblocks:
        raise ValueError("expected complete balanced process blocks")
    for block in range(1, nblocks + 1):
        selected = [row for row in summaries if int(row["block"]) == block]
        by_arm = {row["arm"]: row for row in selected}
        if len(selected) != len(arms) or set(by_arm) != set(arms):
            raise ValueError(f"missing or duplicated arm in block {block}")
        order = [row["arm"] for row in sorted(selected, key=lambda row: int(row["order_position"]))]
        if order != environment["orders"][block - 1]:
            raise ValueError(f"unbalanced or mislabeled order in block {block}")
        plan_assignments = {}
        for arm in arms:
            row = by_arm[arm]
            path = args.results / f"block-{block:02d}-{arm}.log"
            text = path.read_text()
            log_hashes[path.name] = digest(path)
            label = f"mode,direct_staging={str(arm == 'direct').lower()}"
            if label not in text:
                raise ValueError(f"mode label mismatch in {path}")
            if five:
                if (
                    f"mode,contiguous_schedule={str(arm == 'packed_contiguous').lower()}"
                    not in text
                ):
                    raise ValueError(f"contiguous request label mismatch in {path}")
                identity = [
                    line for line in text.splitlines() if line.startswith("object_identity,")
                ]
                after_identity = [
                    line for line in text.splitlines() if line.startswith("object_identity_after,")
                ]
                if (
                    len(identity) != 1
                    or len(after_identity) != 1
                    or identity[0].split(",", 1)[1] != after_identity[0].split(",", 1)[1]
                    or identity[0].split(",", 1)[1] != environment["object_identity"]
                ):
                    raise ValueError(f"descriptor or placement identity drifted: {path}")
                stripe_match = re.search(r"(?:^|,)stripe_count=(\d+)", identity[0])
                if stripe_match is None:
                    raise ValueError(f"missing stripe count in {path}")
                assignments = [
                    [
                        route_id,
                        [int(value.strip()) for value in indices.split(",")],
                        int(byte_count),
                    ]
                    for route_id, indices, byte_count in ASSIGNMENT.findall(text)
                ]
                if (
                    assignments != environment["plan_assignments"][arm]
                    or sorted(index for _, indices, _ in assignments for index in indices)
                    != list(range(int(stripe_match.group(1))))
                    or sum(byte_count for _, _, byte_count in assignments)
                    != environment["object_bytes"]
                ):
                    raise ValueError(f"effective stripe assignment mismatch in {path}")
                plan_assignments[arm] = assignments
                effective = [
                    line for line in text.splitlines() if line.startswith("mode_effective,")
                ]
                if len(effective) != 1:
                    raise ValueError(f"missing effective plan mode in {path}")
                counts = key_values(effective[0])
                expected_mode = (
                    "one"
                    if arm.startswith("single_")
                    else (
                        "packed_greedy"
                        if arm == "packed"
                        else "packed_contiguous" if arm == "packed_contiguous" else "direct"
                    )
                )
                if set(counts) != {"one", "packed_greedy", "packed_contiguous", "direct"} or any(
                    int(value)
                    != (environment["measured_reads_per_arm"] if key == expected_mode else 0)
                    for key, value in counts.items()
                ):
                    raise ValueError(f"requested and effective mode counts differ: {path}")
            observed = [
                key_values(line) for line in text.splitlines() if line.startswith("sample,")
            ]
            expected = [
                sample
                for sample in samples
                if int(sample["block"]) == block and sample["arm"] == arm
            ]
            if len(observed) != environment["measured_reads_per_arm"] or len(expected) != len(
                observed
            ):
                raise ValueError(f"incomplete samples in {path}")
            if any(
                int(raw["iteration"]) != int(csv_sample["iteration"])
                or int(raw["bytes"]) != int(csv_sample["bytes"])
                or int(raw["latency_us"]) != int(csv_sample["latency_us"])
                or raw["xxh3"] != csv_sample["xxh3"]
                or int(raw["bytes"]) != environment["object_bytes"]
                or raw["xxh3"] != environment["object_xxh3"]
                for raw, csv_sample in zip(observed, expected)
            ):
                raise ValueError(f"CSV/object differs from raw request samples: {path}")
            reported = next(
                (line for line in text.splitlines() if line.startswith("summary,")), None
            )
            if reported is None:
                raise ValueError(f"missing summary in {path}")
            values = key_values(reported.split("rail_bytes=[", 1)[0])
            rail_match = re.search(r"rail_bytes=\[([0-9, ]+)\]$", reported)
            if rail_match is None:
                raise ValueError(f"missing Rail byte counts in {path}")
            rail_bytes = [int(value.strip()) for value in rail_match.group(1).split(",")]
            expected_rails = 1 if arm == "single" or arm.startswith("single_") else 2
            if (
                int(values["rails"]) != expected_rails
                or int(values["bytes_per_iter"]) != environment["object_bytes"]
                or int(values["iters"]) != len(observed)
                or int(values["median_us"])
                != statistics.median(int(sample["latency_us"]) for sample in observed)
                or int(values["median_us"]) != int(row["median_us"])
                or int(values["cpu_user_us"]) != int(row["cpu_user_us"])
                or int(values["cpu_system_us"]) != int(row["cpu_system_us"])
                or int(values["peak_rss_kb"]) != int(row["peak_rss_kb"])
                or len(rail_bytes) != expected_rails
                or sum(rail_bytes) != environment["object_bytes"] * len(observed)
                or rail_bytes[0] != int(row["rail0_bytes"])
                or (rail_bytes[1] if expected_rails == 2 else 0) != int(row["rail1_bytes"])
            ):
                raise ValueError(f"raw summary or Rail byte conservation failed: {path}")
            if five:
                rail_lines = [
                    key_values(line)
                    for line in text.splitlines()
                    if line.startswith("rail_stats,")
                ]
                by_route = {line["id"]: line for line in rail_lines}
                planned_bytes = {route_id: byte_count for route_id, _, byte_count in assignments}
                if len(rail_lines) != expected_rails or set(by_route) != set(planned_bytes):
                    raise ValueError(f"Rail counters do not match the plan: {path}")
                total_reads = (
                    environment["warmup_reads_per_arm"]
                    + environment["measured_reads_per_arm"]
                )
                for route_id, line in by_route.items():
                    if (
                        int(line["reads_ok"]) != total_reads
                        or int(line["reads_err"]) != 0
                        or int(line["bytes"]) != planned_bytes[route_id] * total_reads
                        or any(
                            int(line[name]) != 0
                            for name in (
                                "inflight_requests",
                                "inflight_bytes",
                                "registered_bytes_reserved",
                            )
                        )
                    ):
                        raise ValueError(f"Rail counters leaked or misattributed bytes: {path}")
                rail_resources.append(
                    {
                        "arm": arm,
                        "sum_peak_registered_bytes": sum(
                            int(line["peak_registered_bytes"])
                            for line in rail_lines
                        ),
                        "sum_peak_inflight_bytes": sum(
                            int(line["peak_inflight_bytes"])
                            for line in rail_lines
                        ),
                    }
                )
        if five:
            if plan_assignments["packed_contiguous"] != plan_assignments["direct"]:
                raise ValueError(f"contiguous control and direct plan differ in block {block}")
            one0 = int(by_arm["single_r0"]["median_us"])
            one1 = int(by_arm["single_r1"]["median_us"])
            packed = int(by_arm["packed"]["median_us"])
            contiguous = int(by_arm["packed_contiguous"]["median_us"])
            direct = int(by_arm["direct"]["median_us"])
            ratios = {
                "packed_over_r0_throughput": one0 / packed,
                "contiguous_over_r0_throughput": one0 / contiguous,
                "direct_over_r0_throughput": one0 / direct,
                "direct_over_r1_throughput": one1 / direct,
                "direct_over_contiguous_throughput": contiguous / direct,
                "contiguous_over_packed_throughput": packed / contiguous,
                "r1_over_r0_throughput": one0 / one1,
            }
        else:
            single = int(by_arm["single"]["median_us"])
            packed = int(by_arm["packed"]["median_us"])
            direct = int(by_arm["direct"]["median_us"])
            ratios = {
                "packed_over_single_throughput": single / packed,
                "direct_over_single_throughput": single / direct,
                "direct_over_packed_throughput": packed / direct,
            }
        blocks.append(
            {
                "block": block,
                "order": order,
                "median_us": {arm: int(by_arm[arm]["median_us"]) for arm in arms},
                **ratios,
            }
        )
    comparisons = {}
    for name in ratios:
        ratios = [block[name] for block in blocks]
        comparisons[name] = {
            "geometric_mean": math.exp(statistics.mean(math.log(ratio) for ratio in ratios)),
            "block_bootstrap_95pct": bootstrap(ratios),
            "range": [min(ratios), max(ratios)],
            "blocks_above_one": sum(ratio > 1 for ratio in ratios),
        }
    resources = {}
    # Process CPU includes the warmup read. Divide by every completed read,
    # while throughput ratios above use only the measured-read latency median.
    cpu_denominator = (
        environment["warmup_reads_per_arm"] + environment["measured_reads_per_arm"]
    )
    for arm in arms:
        selected = [row for row in summaries if row["arm"] == arm]
        resources[arm] = {
            "median_cpu_us_per_read": statistics.median(
                (int(row["cpu_user_us"]) + int(row["cpu_system_us"]))
                / cpu_denominator
                for row in selected
            ),
            "median_peak_rss_kb": statistics.median(int(row["peak_rss_kb"]) for row in selected),
        }
        if five:
            matching = [row for row in rail_resources if row["arm"] == arm]
            resources[arm]["median_sum_per_rail_peak_registered_bytes"] = statistics.median(
                row["sum_peak_registered_bytes"] for row in matching
            )
            resources[arm]["median_sum_per_rail_peak_inflight_bytes"] = statistics.median(
                row["sum_peak_inflight_bytes"] for row in matching
            )
    result = {
        "interpretation": (
            "balanced five-arm launched-process blocks with both one-Rail paths and same-planner packed control; exploratory block-bootstrap intervals, no physical-HCA aggregation claim"
            if five
            else "balanced three-arm launched-process blocks on one RXE environment; exploratory block-bootstrap intervals, no physical-HCA aggregation claim"
        ),
        "environment": environment,
        "source_sha256": {
            environment_path.name: digest(environment_path),
            summary_path.name: digest(summary_path),
            samples_path.name: digest(samples_path),
            **log_hashes,
        },
        "blocks": blocks,
        "comparisons": comparisons,
        "resources": resources,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output, len(blocks), "complete balanced blocks")


if __name__ == "__main__":
    main()
