"""Audit the single-HCA read timing pilot from unmodified raw process logs.

Stage records are aligned only within one sequential client process. Server
diagnostics have no request ID and are deliberately not joined to client reads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from pathlib import Path


FIELDS = {
    "TRANSPORT": "connect allocation registration get teardown".split(),
    "STAGED": "plan reserve workers assembly checksum finish".split(),
    "OUTER": "lookup transfer post_lookup publish".split(),
}
EXPECTED_HASH = "a0a4cbfa5cad46af"
EXPECTED_BYTES = 64 * 1024 * 1024


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key_values(line: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in line.split() if "=" in item)


def stage_values(line: str, kind: str) -> dict[str, int]:
    values = key_values(line)
    result = {name: int(values[f"{name}_us"]) for name in FIELDS[kind]}
    result["total"] = int(values["total_us"])
    result["unaccounted"] = result["total"] - sum(result[name] for name in FIELDS[kind])
    if result["unaccounted"] < 0:
        raise ValueError(f"negative unaccounted time in {kind}: {line}")
    if result["unaccounted"] / result["total"] > 0.1:
        raise ValueError(f"more than 10% unaccounted time in {kind}: {line}")
    if int(values["bytes"]) != EXPECTED_BYTES:
        raise ValueError(f"wrong byte count in {kind}: {line}")
    return result


def process(path: Path, traced: bool) -> dict:
    samples = []
    pending: dict[str, dict[str, int]] = {}
    warmup = None
    for line in path.read_text().splitlines():
        if line.startswith("RAIL_"):
            match = re.match(r"RAIL_(TRANSPORT|STAGED|OUTER)_TIMING ", line)
            if match:
                kind = match.group(1)
                if kind in pending:
                    raise ValueError(f"duplicate stage before sample: {path}: {kind}")
                pending[kind] = stage_values(line, kind)
                if len(pending) == 3 and warmup is None:
                    warmup = pending
                    pending = {}
        elif line.startswith("sample,"):
            values = key_values(line.replace(",", " "))
            if values["xxh3"] != EXPECTED_HASH or int(values["bytes"]) != EXPECTED_BYTES:
                raise ValueError(f"object mismatch: {path}: {line}")
            if int(values["rails"]) != 1 or values["environment"] != "physical":
                raise ValueError(f"wrong environment: {path}: {line}")
            if traced and set(pending) != set(FIELDS):
                raise ValueError(f"missing stages: {path}: {sorted(pending)}")
            if not traced and pending:
                raise ValueError(f"untraced process emitted timing: {path}")
            sample = {"iteration": int(values["iteration"]), "wall_us": int(values["latency_us"])}
            if traced:
                sample["stages_us"] = pending
                outer = pending["OUTER"]
                staged = pending["STAGED"]
                transport = pending["TRANSPORT"]
                if outer["total"] > sample["wall_us"]:
                    raise ValueError(f"outer clock exceeds CLI clock: {path}")
                if (sample["wall_us"] - outer["total"]) / sample["wall_us"] > 0.1:
                    raise ValueError(f"more than 10% unaccounted outside outer clock: {path}")
                if staged["total"] > outer["transfer"] or transport["total"] > staged["workers"]:
                    raise ValueError(f"nested clock exceeds parent: {path}")
            samples.append(sample)
            pending = {}
    if len(samples) != 5 or [x["iteration"] for x in samples] != list(range(1, 6)):
        raise ValueError(f"expected five numbered samples: {path}")
    if traced and (warmup is None or pending):
        raise ValueError(f"missing warmup or extra timing records: {path}")
    reported = re.search(r"median_us=(\d+)", path.read_text())
    if not reported or int(reported.group(1)) != statistics.median(x["wall_us"] for x in samples):
        raise ValueError(f"reported median mismatch: {path}")
    return {
        "log": path.name,
        "sha256": sha256(path),
        "median_wall_us": int(reported.group(1)),
        "samples": samples,
        "warmup_stages_us": warmup if traced else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    processes = []
    for name in ("stage-trace-1", "stage-control-1", "stage-trace-2", "stage-control-2"):
        processes.append(process(args.logs / f"{name}.log", "trace" in name))
    trace_samples = [sample for p in processes if "trace" in p["log"] for sample in p["samples"]]
    control_samples = [
        sample for p in processes if "control" in p["log"] for sample in p["samples"]
    ]
    stage_medians = {
        kind: {
            field: statistics.median(sample["stages_us"][kind][field] for sample in trace_samples)
            for field in (*FIELDS[kind], "total", "unaccounted")
        }
        for kind in FIELDS
    }
    server_path = args.logs / "server-stage-20261003.log"
    server_subset = [
        key_values(line)
        for line in server_path.read_text().splitlines()
        if "RDMA_SUBSET_DETAIL" in line and "paperhca202610030/__combined__" in line
    ]
    if len(server_subset) != 24:
        raise ValueError(f"expected 24 server subset diagnostics, got {len(server_subset)}")
    result = {
        "environment": "physical single ConnectX-6 DX HCA, node1 to node2, mlx5_1 port 1/GID 3, one Rail, warm server slab, 64 MiB/16 stripes",
        "interpretation": "pilot stage accounting only; two process blocks per arm, no confidence interval, no physical multi-HCA scaling claim",
        "object_xxh3": EXPECTED_HASH,
        "bytes_per_read": EXPECTED_BYTES,
        "client_binary_sha256": "41ee6b203c806653c1b422ec1157494f9ccf18f9b8de1fd6c299b8685e6e9679",
        "source_file_sha256": {
            "client-rs/src/lib.rs": "7b385c24efc1ad559859c7f8e389e6c4c5cd98a428297120e1dd33db5e0500d4",
            "client-rs/src/rail_read.rs": "b11185d7d1e4bc3cf0868b73ee5dba343258ee846b8f0cdac5889452922ec37d",
        },
        "processes": processes,
        "paired_trace_over_control_process_median": [
            processes[index]["median_wall_us"] / processes[index + 1]["median_wall_us"]
            for index in (0, 2)
        ],
        "traced_sample_count": len(trace_samples),
        "control_sample_count": len(control_samples),
        "traced_median_wall_us": statistics.median(sample["wall_us"] for sample in trace_samples),
        "control_median_wall_us": statistics.median(
            sample["wall_us"] for sample in control_samples
        ),
        "stage_medians_us": stage_medians,
        "server_diagnostics": {
            "log": server_path.name,
            "sha256": sha256(server_path),
            "records": len(server_subset),
            "median_subset_total_us_including_warmups": statistics.median(
                int(row["total_us"]) for row in server_subset
            ),
            "request_matching": "none: server records lack a shared request ID",
        },
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output, f"{len(trace_samples)} traced and {len(control_samples)} control reads")


if __name__ == "__main__":
    main()
