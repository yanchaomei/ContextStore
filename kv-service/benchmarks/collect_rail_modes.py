"""Collect balanced three- or five-arm Rail reads on one binary and object."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import itertools
import re
import statistics
import subprocess
from pathlib import Path


ARMS = ("single", "packed", "direct")
FIVE_ARMS = ("single_r0", "single_r1", "packed", "packed_contiguous", "direct")


def balanced_orders(arms: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    if len(arms) == 3:
        return tuple(itertools.permutations(arms))
    rotations = tuple(arms[index:] + arms[:index] for index in range(len(arms)))
    return rotations + tuple(tuple(reversed(order)) for order in rotations)


ORDERS = balanced_orders(ARMS)
ASSIGNMENT = re.compile(r"^plan_assignment,id=([^,]+),stripes=\[([0-9, ]+)\],bytes=(\d+)$", re.M)
SAMPLE = re.compile(
    r"^sample,environment=([^,]+),rails=(\d+),iteration=(\d+),"
    r"bytes=(\d+),latency_us=(\d+),gib_per_s=([0-9.]+),xxh3=([0-9a-f]+)$",
    re.MULTILINE,
)
SUMMARY = re.compile(
    r"^summary,environment=([^,]+),rails=(\d+),bytes_per_iter=(\d+),"
    r"iters=(\d+),median_us=(\d+),cpu_user_us=(\d+),cpu_system_us=(\d+),"
    r"peak_rss_kb=(\d+),rail_bytes=\[([^]]+)\]$",
    re.MULTILINE,
)


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--environment", choices=("physical", "soft-roce"), required=True)
    parser.add_argument("--coordinator", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--object-key", required=True)
    parser.add_argument("--rail", action="append", required=True)
    parser.add_argument("--arm-set", choices=("three", "five"), default="three")
    parser.add_argument("--blocks", type=int, default=12)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    arms = ARMS if args.arm_set == "three" else FIVE_ARMS
    orders = balanced_orders(arms)
    if len(args.rail) != 2 or args.blocks < len(orders) or args.blocks % len(orders) != 0:
        parser.error(f"provide two Rails and a positive multiple of {len(orders)} blocks")
    if args.iterations < 1 or args.warmup < 1:
        parser.error("iterations and warmup must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    samples = []
    expected_hash = None
    expected_size = None
    expected_identity = None
    expected_assignments: dict[str, list[tuple[str, tuple[int, ...], int]]] = {}
    for block in range(1, args.blocks + 1):
        order = orders[(block - 1) % len(orders)]
        block_assignments = {}
        for position, arm in enumerate(order):
            rails = (
                args.rail[:1]
                if arm in ("single", "single_r0")
                else args.rail[1:2] if arm == "single_r1" else args.rail
            )
            command = [
                str(args.binary),
                "--environment",
                args.environment,
                "--coordinator",
                args.coordinator,
                "--namespace",
                args.namespace,
                "--object-key",
                args.object_key,
                "--warmup",
                str(args.warmup),
                "--iterations",
                str(args.iterations),
            ]
            for rail in rails:
                command.extend(("--rail", rail))
            if arm == "direct":
                command.append("--direct-staging")
            if arm == "packed_contiguous":
                command.append("--contiguous-schedule")
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            output = completed.stdout
            (args.output_dir / f"block-{block:02d}-{arm}.log").write_text(output)
            if completed.returncode:
                raise RuntimeError(
                    f"block {block} {arm} exited {completed.returncode}: {output[-2000:]}"
                )
            if f"mode,direct_staging={str(arm == 'direct').lower()}" not in output:
                raise ValueError(f"direct mode label disagrees: block {block} {arm}")
            if args.arm_set == "five":
                if (
                    f"mode,contiguous_schedule={str(arm == 'packed_contiguous').lower()}"
                    not in output
                ):
                    raise ValueError(f"contiguous mode label disagrees: block {block} {arm}")
                before = [
                    line for line in output.splitlines() if line.startswith("object_identity,")
                ]
                after = [
                    line
                    for line in output.splitlines()
                    if line.startswith("object_identity_after,")
                ]
                if (
                    len(before) != 1
                    or len(after) != 1
                    or before[0].split(",", 1)[1] != after[0].split(",", 1)[1]
                ):
                    raise ValueError(f"object identity changed within arm: block {block} {arm}")
                identity = before[0].split(",", 1)[1]
                if expected_identity is not None and identity != expected_identity:
                    raise ValueError(
                        f"descriptor or placement changed across arms: block {block} {arm}"
                    )
                expected_identity = identity
                stripe_match = re.search(r"(?:^|,)stripe_count=(\d+)", identity)
                if stripe_match is None:
                    raise ValueError("descriptor stripe count missing from identity")
                stripes = int(stripe_match.group(1))
                assignments = [
                    (
                        route_id,
                        tuple(int(value.strip()) for value in indices.split(",")),
                        int(byte_count),
                    )
                    for route_id, indices, byte_count in ASSIGNMENT.findall(output)
                ]
                if len(assignments) != len(rails) or sorted(
                    index for _, values, _ in assignments for index in values
                ) != list(range(stripes)):
                    raise ValueError(f"incomplete or duplicate plan stripes: block {block} {arm}")
                if arm in expected_assignments and assignments != expected_assignments[arm]:
                    raise ValueError(f"stripe plan drifted between blocks: {block} {arm}")
                expected_assignments[arm] = assignments
                block_assignments[arm] = assignments
                effective = [
                    line for line in output.splitlines() if line.startswith("mode_effective,")
                ]
                if len(effective) != 1:
                    raise ValueError(f"missing effective mode counter: block {block} {arm}")
                counts = dict(field.split("=", 1) for field in effective[0].split(",")[1:])
                mode = (
                    "one"
                    if arm.startswith("single_")
                    else (
                        "packed_greedy"
                        if arm == "packed"
                        else "packed_contiguous" if arm == "packed_contiguous" else "direct"
                    )
                )
                if set(counts) != {"one", "packed_greedy", "packed_contiguous", "direct"} or any(
                    int(value) != (args.iterations if key == mode else 0)
                    for key, value in counts.items()
                ):
                    raise ValueError(f"requested and effective modes disagree: block {block} {arm}")
            sample_rows = SAMPLE.findall(output)
            reported = SUMMARY.search(output)
            if len(sample_rows) != args.iterations or reported is None:
                raise ValueError(f"incomplete read output: block {block} {arm}")
            digests = {row[6] for row in sample_rows}
            sizes = {int(row[3]) for row in sample_rows}
            if len(digests) != 1 or len(sizes) != 1:
                raise ValueError(f"inconsistent object within block {block} {arm}")
            digest = digests.pop()
            size = sizes.pop()
            if expected_hash is not None and (digest != expected_hash or size != expected_size):
                raise ValueError(f"different object across arms: block {block} {arm}")
            expected_hash, expected_size = digest, size
            (
                environment,
                rail_count,
                summary_size,
                count,
                median_us,
                cpu_user_us,
                cpu_system_us,
                peak_rss_kb,
                rail_bytes,
            ) = reported.groups()
            byte_counts = [int(value.strip()) for value in rail_bytes.split(",")]
            if (
                environment != args.environment
                or int(rail_count) != len(rails)
                or int(summary_size) != size
                or int(count) != args.iterations
                or int(median_us) != statistics.median(int(row[4]) for row in sample_rows)
                or len(byte_counts) != len(rails)
                or any(value == 0 for value in byte_counts)
                or sum(byte_counts) != size * args.iterations
            ):
                raise ValueError(f"summary or completed Rail bytes disagree: block {block} {arm}")
            summaries.append(
                {
                    "block": block,
                    "arm": arm,
                    "order_position": position,
                    "rails": len(rails),
                    "median_us": int(median_us),
                    "cpu_user_us": int(cpu_user_us),
                    "cpu_system_us": int(cpu_system_us),
                    "peak_rss_kb": int(peak_rss_kb),
                    "rail0_bytes": byte_counts[0],
                    "rail1_bytes": byte_counts[1] if len(byte_counts) == 2 else 0,
                    "bytes_per_read": size,
                    "xxh3": digest,
                }
            )
            samples.extend(
                {
                    "block": block,
                    "arm": arm,
                    "iteration": int(row[2]),
                    "bytes": int(row[3]),
                    "latency_us": int(row[4]),
                    "xxh3": row[6],
                }
                for row in sample_rows
            )
            print(f"block={block} arm={arm} median_us={median_us}", flush=True)
        if (
            args.arm_set == "five"
            and block_assignments["packed_contiguous"] != block_assignments["direct"]
        ):
            raise ValueError(f"contiguous control and direct stripe maps differ in block {block}")

    write_csv(args.output_dir / "summary.csv", summaries)
    write_csv(args.output_dir / "samples.csv", samples)
    environment = {
        "environment": args.environment,
        "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        "coordinator": args.coordinator,
        "namespace": args.namespace,
        "object_key": args.object_key,
        "object_bytes": expected_size,
        "object_xxh3": expected_hash,
        "rails": args.rail,
        "blocks": args.blocks,
        "orders": [orders[(block - 1) % len(orders)] for block in range(1, args.blocks + 1)],
        "warmup_reads_per_arm": args.warmup,
        "measured_reads_per_arm": args.iterations,
        "arms": arms,
    }
    if args.arm_set == "five":
        environment["arm_set"] = "five"
        environment["object_identity"] = expected_identity
        environment["plan_assignments"] = expected_assignments
    (args.output_dir / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")


if __name__ == "__main__":
    main()
