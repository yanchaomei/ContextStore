"""Compare two client binaries on the same one-Rail object in paired processes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import subprocess
from pathlib import Path


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
    parser.add_argument("--baseline-binary", type=Path, required=True)
    parser.add_argument("--candidate-binary", type=Path, required=True)
    parser.add_argument("--coordinator", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--object-key", required=True)
    parser.add_argument("--rail", required=True)
    parser.add_argument("--environment", choices=["physical", "soft-roce"], required=True)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.trials < 1 or args.iterations < 1:
        parser.error("trials and iterations must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    binaries = {"baseline": args.baseline_binary, "candidate": args.candidate_binary}
    summary = []
    samples = []
    object_hash = None
    object_bytes = None
    for trial in range(1, args.trials + 1):
        order = ("baseline", "candidate") if trial % 2 else ("candidate", "baseline")
        for position, arm in enumerate(order):
            command = [
                str(binaries[arm]),
                "--coordinator",
                args.coordinator,
                "--namespace",
                args.namespace,
                "--object-key",
                args.object_key,
                "--rail",
                args.rail,
                "--environment",
                args.environment,
                "--warmup",
                str(args.warmup),
                "--iterations",
                str(args.iterations),
            ]
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            output = completed.stdout
            (args.output_dir / f"trial-{trial:02d}-{arm}.log").write_text(output)
            if completed.returncode != 0:
                raise RuntimeError(f"trial {trial} {arm} failed: {output[-2000:]}")
            sample_rows = SAMPLE.findall(output)
            summary_match = SUMMARY.search(output)
            if len(sample_rows) != args.iterations or summary_match is None:
                raise ValueError(f"incomplete output: trial {trial} {arm}")
            values = [int(row[4]) for row in sample_rows]
            hashes = {row[6] for row in sample_rows}
            sizes = {int(row[3]) for row in sample_rows}
            if len(hashes) != 1 or len(sizes) != 1:
                raise ValueError(f"inconsistent object: trial {trial} {arm}")
            digest = hashes.pop()
            size = sizes.pop()
            if object_hash is not None and (digest != object_hash or size != object_bytes):
                raise ValueError(f"different object/version across arms: trial {trial} {arm}")
            object_hash, object_bytes = digest, size
            (
                environment,
                rails,
                reported_size,
                count,
                median_us,
                cpu_user_us,
                cpu_system_us,
                peak_rss_kb,
                rail_bytes,
            ) = summary_match.groups()
            if (
                environment != args.environment
                or int(rails) != 1
                or int(reported_size) != size
                or int(count) != args.iterations
                or int(median_us) != statistics.median(values)
                or int(rail_bytes) != size * args.iterations
            ):
                raise ValueError(f"summary mismatch: trial {trial} {arm}")
            summary.append(
                {
                    "trial": trial,
                    "arm": arm,
                    "order_position": position,
                    "median_us": int(median_us),
                    "cpu_user_us": int(cpu_user_us),
                    "cpu_system_us": int(cpu_system_us),
                    "peak_rss_kb": int(peak_rss_kb),
                    "bytes": size,
                    "xxh3": digest,
                }
            )
            for row in sample_rows:
                samples.append(
                    {
                        "trial": trial,
                        "arm": arm,
                        "iteration": int(row[2]),
                        "bytes": size,
                        "latency_us": int(row[4]),
                        "xxh3": digest,
                    }
                )
            print(f"trial={trial} arm={arm} median_us={median_us}", flush=True)

    write_csv(args.output_dir / "summary.csv", summary)
    write_csv(args.output_dir / "samples.csv", samples)
    ratios = []
    for trial in range(1, args.trials + 1):
        pair = {row["arm"]: row for row in summary if row["trial"] == trial}
        ratios.append(pair["baseline"]["median_us"] / pair["candidate"]["median_us"])
    environment = {
        "environment": args.environment,
        "object_key": args.object_key,
        "object_xxh3": object_hash,
        "object_bytes": object_bytes,
        "rail": args.rail,
        "trials": args.trials,
        "iterations_per_arm": args.iterations,
        "warmup_reads": args.warmup,
        "baseline_binary_sha256": hashlib.sha256(args.baseline_binary.read_bytes()).hexdigest(),
        "candidate_binary_sha256": hashlib.sha256(args.candidate_binary.read_bytes()).hexdigest(),
        "ratio_definition": "baseline median latency divided by candidate median latency, per block",
        "paired_ratio_geometric_mean": math.exp(statistics.mean(math.log(x) for x in ratios)),
        "paired_ratios": ratios,
    }
    (args.output_dir / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")


if __name__ == "__main__":
    main()
