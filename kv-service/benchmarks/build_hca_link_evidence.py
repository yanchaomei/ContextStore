"""Preserve physical single-HCA link microbenchmarks and bounded comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "kv-service/benchmarks/results"
RESULT = re.compile(r"^\s*1048576\s+(\d+)\s+\S+\s+([0-9.]+)\s+\S+\s+([0-9.]+)", re.M)


def source(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "file": path.name,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "full_output": raw.decode("utf-8"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", type=Path, default=RESULTS / "paper-hca-link-raw-2026-10-03")
    parser.add_argument("--app", type=Path, default=RESULTS / "2026-10-02-skv-single-hca.json")
    parser.add_argument(
        "--output", type=Path, default=RESULTS / "2026-10-03-skv-single-hca-link.json"
    )
    args = parser.parse_args()
    server_stage = args.logs / "server-final-slab128.log"
    trials = []
    for trial, suffix in enumerate(("", "_t2", "_t3"), 1):
        sender = args.logs / f"ib_write_bw_sender{suffix}.log"
        receiver = args.logs / f"ib_write_bw_receiver{suffix}.log"
        text = sender.read_text()
        match = RESULT.search(text)
        if match is None:
            raise RuntimeError(f"missing perftest result: {sender}")
        if "Link type       : Ethernet" not in text or "GID index       : 3" not in text:
            raise RuntimeError(f"unexpected transport: {sender}")
        trials.append(
            {
                "trial": trial,
                "iterations": int(match.group(1)),
                "average_gbit_per_s": float(match.group(2)),
                "reported_cpu_util_percent": float(match.group(3)),
                "sender": source(sender),
                "receiver": source(receiver),
            }
        )

    median_gbit = statistics.median(row["average_gbit_per_s"] for row in trials)
    median_gib = median_gbit * 1e9 / 8 / 1024**3
    app = json.loads(args.app.read_text())
    slab_run = next(row for row in app["single_rail_runs"] if row["mode"] == "slab_128_mib")
    app_median_us = slab_run["median_us"]
    app_gib = app["environment"]["object_bytes"] / (app_median_us / 1e6) / 1024**3
    server_raw = server_stage.read_bytes()
    stage_text = server_raw.decode("utf-8")
    subset_us = [
        int(value) for value in re.findall(r"RDMA_SUBSET_DETAIL[^\n]*total_us=(\d+)", stage_text)
    ]
    poll_us = [
        int(value) for value in re.findall(r"RDMA_SUBSET_DETAIL[^\n]*poll_us=(\d+)", stage_text)
    ]
    if not subset_us or len(subset_us) != len(poll_us):
        raise RuntimeError("missing server subset stage diagnostics")

    document = {
        "schema_version": 1,
        "label": "physical RoCE v2 single HCA link calibration, not dual-rail aggregation",
        "topology": {
            "sender": "skv-node1; mlx5_1 port 1; GID index 3; 10.0.0.61; NUMA 1",
            "receiver": "skv-node2; mlx5_1 port 1; GID index 3; 10.0.0.62; NUMA 1",
            "link_nominal_gbit_per_s": 100,
            "perftest_version": "5.99",
        },
        "calibration": {
            "operation": "RDMA WRITE",
            "message_bytes": 1048576,
            "qp_count": 1,
            "duration_seconds_per_trial": 8,
            "trials": trials,
            "median_average_gbit_per_s": median_gbit,
            "median_average_gib_per_s": median_gib,
        },
        "application_reference": {
            "source_file": str(args.app.relative_to(REPO)),
            "source_sha256": hashlib.sha256(args.app.read_bytes()).hexdigest(),
            "source_commit": app["source_commit"],
            "mode": slab_run["mode"],
            "object_bytes": app["environment"]["object_bytes"],
            "median_latency_us": app_median_us,
            "derived_payload_gib_per_s": app_gib,
            "link_capacity_over_application_payload_ratio": median_gib / app_gib,
        },
        "server_stage_reference": {
            "source_file": server_stage.name,
            "source_sha256": hashlib.sha256(server_raw).hexdigest(),
            "full_output": stage_text,
            "subset_total_us": subset_us,
            "subset_total_median_us": statistics.median(subset_us),
            "subset_poll_us": poll_us,
            "subset_poll_median_us": statistics.median(poll_us),
            "request_mapping": "not request-ID matched to the client benchmark; stage values are diagnostic only",
        },
        "interpretation": (
            "Three perftest runs show the physical link can approach 100 Gb/s. "
            "The older ContextStore single-rail object's payload rate was about 30 times lower, "
            "so this workload has not demonstrated a one-NIC bandwidth bottleneck. "
            "Perftest bypasses storage, metadata, QP setup, MR registration, validation and assembly; "
            "the ratio is capacity headroom, not a same-path speedup prediction."
        ),
    }
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    print(args.output, args.output.stat().st_size)


if __name__ == "__main__":
    main()
