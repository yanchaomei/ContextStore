"""Audit SDK rejection identities in a generation-unique RDMA race run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from audit_unique_race import digest, fields, parse_one, server_image_receipt


VERSION_CHANGED = "object version changed during rail read"


def audit_rejection_ids(raw: str, reads: list[dict[str, str]], object_key: str) -> dict:
    """Count only observed Generation changes, failing on missing SDK traces.

    The E2E test issues reads sequentially through one SDK client. The trace
    sequence therefore maps to its VersionChanged outcomes in read order.
    A same-Generation descriptor/placement mismatch is reported separately.
    """
    version_rejection_attempts = [
        attempt
        for attempt, item in enumerate(reads)
        if item.get("result") == "rejected" and item.get("error") == VERSION_CHANGED
    ]
    identities = [
        fields(line)
        for line in raw.splitlines()
        if line.startswith("RAIL_REJECTED_ID,")
    ]
    if len(identities) != len(version_rejection_attempts):
        raise ValueError("SDK rejected-ID trace count differs from VersionChanged reads")
    transitions = []
    transition_attempts = []
    same_generation = 0
    for attempt, item in zip(version_rejection_attempts, identities):
        if (
            item.get("namespace") != "rail-e2e"
            or item.get("object_key") != object_key
            or item.get("reason") != "version_changed"
        ):
            raise ValueError("SDK rejected-ID trace belongs to another object or reason")
        if item.get("initial_layout_version") != "1" or item.get(
            "current_layout_version"
        ) != "1":
            raise ValueError("SDK rejected-ID trace changed layout version")
        initial = int(item["initial_generation"])
        current = int(item["current_generation"])
        if not 1 <= initial <= current <= 16:
            raise ValueError("SDK rejected-ID trace has an invalid Generation pair")
        if initial < current:
            transitions.append([initial, current])
            transition_attempts.append(attempt)
        else:
            same_generation += 1
    return {
        "version_changed_rejections": len(version_rejection_attempts),
        "generation_transition_rejections": len(transitions),
        "same_generation_identity_rejections": same_generation,
        "transitions": transitions,
        "transition_attempts": transition_attempts,
    }


def audit_run(path: Path, object_mib: int, binary: Path | None) -> dict:
    base = parse_one(path, object_mib, binary)
    raw = path.read_text()
    config = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_CONFIG,")]
    if len(config) != 1 or config[0].get("rejection_id_trace") != "true":
        raise ValueError(f"rejection identity instrumentation was not declared: {path}")
    if base["put_interval_scope"] != "sdk_call":
        raise ValueError(f"SDK-call timestamp scope was not declared: {path}")
    reads = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_READ,")]
    identity = audit_rejection_ids(raw, reads, base["object_key"])
    if identity["version_changed_rejections"] != base["rejection_reasons"].get(
        VERSION_CHANGED, 0
    ):
        raise ValueError(f"rejection outcome and SDK identity counts differ: {path}")
    writes = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_WRITE,")]
    write_ranges = [
        (int(item["start_ns"]), int(item["put_end_ns"])) for item in writes
    ]
    transitioned_in_sdk_call = 0
    for attempt in identity["transition_attempts"]:
        read = reads[attempt]
        start, end = int(read["start_ns"]), int(read["end_ns"])
        if any(start < put_end and write_start < end for write_start, put_end in write_ranges):
            transitioned_in_sdk_call += 1
    identity["transition_rejections_with_sdk_call_overlap"] = transitioned_in_sdk_call
    return {**base, "rejection_identity": identity}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--object-mib", type=int, required=True)
    parser.add_argument("--expected-runs", type=int, required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.expected_source_commit) != 40:
        parser.error("pass a full source commit SHA")
    runs = [
        audit_run(args.logs / f"run{index}.log", args.object_mib, args.binary)
        for index in range(1, args.expected_runs + 1)
    ]
    if len({run["object_key"] for run in runs}) != len(runs):
        raise ValueError("runs reused the same object key")
    if {run["source_commit_label"] for run in runs} != {args.expected_source_commit}:
        raise ValueError("launch source-commit label does not match expected commit")
    if len({run["launch_time_binary_sha256"] for run in runs}) != 1:
        raise ValueError("campaign mixed test binaries")
    server_before = server_image_receipt(args.logs / "server-live-before.txt")
    server_after = server_image_receipt(args.logs / "server-live-after.txt")
    if any(
        server_before[key] != server_after[key]
        for key in ("server_pid", "live_executable_sha256", "config_sha256")
    ):
        raise ValueError("server process or image changed during the campaign")
    totals = {
        key: sum(run["rejection_identity"][key] for run in runs)
        for key in (
            "version_changed_rejections",
            "generation_transition_rejections",
            "same_generation_identity_rejections",
            "transition_rejections_with_sdk_call_overlap",
        )
    }
    totals.update(
        write_commits=sum(run["write_commits"] for run in runs),
        read_attempts=sum(run["read_attempts"] for run in runs),
        sdk_call_overlap_reads=sum(run["put_overlap_reads"] for run in runs),
        accepted=sum(run["accepted"] for run in runs),
        rejected=sum(run["rejected"] for run in runs),
        missing=sum(run["missing"] for run in runs),
    )
    result = {
        "environment": "isolated two-RXE KVM guests on one host",
        "scope": "single sequential reader and single writer; an observed initial_generation < current_generation in one read establishes a metadata Generation transition between its two LookupObject snapshots, not its exact server commit timestamp",
        "auditor_sha256": digest(Path(__file__)),
        "base_auditor_sha256": digest(Path(parse_one.__code__.co_filename)),
        "runs": runs,
        "server_image_before": server_before,
        "server_image_after": server_after,
        "totals": totals,
        "source_commit_label_boundary": "launch manifest labels require the separate 20-file Git-to-build receipt for source attribution",
        "non_claim": "finite observed schedules do not prove all provider, crash, or buffer-aliasing behaviors",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output, totals)


if __name__ == "__main__":
    main()
