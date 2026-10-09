"""Audit generation-unique direct-Rail race logs and actual overlap intervals."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def server_image_receipt(path: Path) -> dict:
    lines = path.read_text().splitlines()
    if len(lines) != 4 or not lines[0].startswith("server_pid="):
        raise ValueError(f"incomplete live server image receipt: {path}")
    if lines[1] != "/home/railtest/bin/contextstore-server":
        raise ValueError(f"unexpected live server executable: {path}")
    image_sha, image_path = lines[2].split("  ", 1)
    config_sha, config_path = lines[3].split("  ", 1)
    if (
        image_path != f"/proc/{lines[0].split('=', 1)[1]}/exe"
        or config_path != "/home/railtest/evidence/server.toml"
        or len(image_sha) != 64
        or len(config_sha) != 64
    ):
        raise ValueError(f"invalid server image/config hashes: {path}")
    return {
        "server_pid": lines[0].split("=", 1)[1],
        "live_executable_sha256": image_sha,
        "config_sha256": config_sha,
        "receipt_sha256": digest(path),
    }


def fields(line: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in line.split(",")[1:] if "=" in part)


def parse_one(path: Path, object_mib: int, binary_path: Path | None) -> dict:
    raw = path.read_text()
    if "test result: ok. 1 passed; 0 failed" not in raw:
        raise ValueError(f"test did not pass: {path}")
    launch = path.with_suffix(".launch")
    exit_file = path.with_suffix(".exit")
    launch_lines = launch.read_text().splitlines()
    if (
        len(launch_lines) != 3
        or not launch_lines[0].startswith("source_commit=")
        or launch_lines[1] != f"object_mib={object_mib}"
    ):
        raise ValueError(f"incomplete launch-time manifest: {path}")
    source_commit = launch_lines[0].split("=", 1)[1]
    binary_sha = launch_lines[2].split("  ", 1)[0]
    if len(source_commit) != 40 or len(binary_sha) != 64:
        raise ValueError(f"invalid source or binary identity: {path}")
    if binary_path and digest(binary_path) != binary_sha:
        raise ValueError(f"launch hash differs from supplied binary: {path}")
    if exit_file.read_text().strip() != "0":
        raise ValueError(f"test process failed: {path}")

    config = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_CONFIG,")]
    if len(config) != 1 or config[0].get("object_mib") != str(object_mib):
        raise ValueError(f"object size was not logged: {path}")
    if config[0].get("stripe_count") != str(object_mib // 4) or config[0].get(
        "direct_staging"
    ) != "true":
        raise ValueError(f"object layout or mode differs: {path}")
    object_key = config[0]["object_key"]
    writes = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_WRITE,")]
    if len(writes) != 15:
        raise ValueError(f"expected 15 committed generations: {path}")
    write_ranges = []
    for revision, item in enumerate(writes):
        if (
            int(item["revision"]) != revision
            or int(item["generation"]) != revision + 2
            or int(item["byte"]) != 0x20 + revision
        ):
            raise ValueError(f"generation-coded writer sequence changed: {path}")
        start, end = int(item["start_ns"]), int(item["end_ns"])
        if not 0 <= start < end:
            raise ValueError(f"invalid writer interval: {path}")
        write_ranges.append((start, end))

    reads = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_READ,")]
    if not 20 <= len(reads) <= 36:
        raise ValueError(f"unexpected read-attempt count: {path}")
    accepted = []
    rejected = 0
    missing = 0
    overlapped = 0
    overlap_accepted = 0
    overlap_rejected = 0
    rejection_reasons: dict[str, int] = {}
    for attempt, item in enumerate(reads):
        if int(item["attempt"]) != attempt:
            raise ValueError(f"read attempt sequence changed: {path}")
        start, end = int(item["start_ns"]), int(item["end_ns"])
        if not 0 <= start < end:
            raise ValueError(f"invalid reader interval: {path}")
        overlap = any(start < write_end and write_start < end for write_start, write_end in write_ranges)
        overlapped += overlap
        outcome = item["result"]
        if outcome == "accepted":
            value, generation = int(item["byte"]), int(item["generation_inferred"])
            expected = 1 if value == 0x10 else value - 0x20 + 2
            if value not in (0x10, *range(0x20, 0x2F)) or generation != expected:
                raise ValueError(f"accepted byte does not name one generation: {path}")
            accepted.append(generation)
            overlap_accepted += overlap
        elif outcome == "rejected":
            reason = item.get("error", "")
            if reason not in (
                "object version changed during rail read",
                "object descriptor became stale",
            ):
                raise ValueError(f"unexpected reject reason: {path}: {reason}")
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            rejected += 1
            overlap_rejected += overlap
        elif outcome == "missing":
            missing += 1
        else:
            raise ValueError(f"unknown read result: {path}")

    identities = [
        fields(line)
        for line in raw.splitlines()
        if line.startswith("RAIL_ACCEPTED_ID,")
    ]
    if len(identities) != len(accepted) + 1:
        raise ValueError(f"SDK accepted-identity trace incomplete: {path}")
    if any(
        item.get("namespace") != "rail-e2e"
        or item.get("object_key") != object_key
        or item.get("layout_version") != "1"
        for item in identities
    ):
        raise ValueError(f"SDK accepted identity belongs to another object: {path}")
    actual_generations = [int(item["generation"]) for item in identities]
    if actual_generations != accepted + [16]:
        raise ValueError(f"returned bytes differ from SDK accepted Generation: {path}")
    final = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_FINAL,")]
    if len(final) != 1 or final[0] != {
        "generation_inferred": "16",
        "byte": "46",
        "bytes": str(object_mib * 1024 * 1024),
    }:
        raise ValueError(f"post-rewrite final read changed: {path}")
    summary = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_SUMMARY,")]
    if len(summary) != 1 or any(
        int(summary[0][name]) != value
        for name, value in (
            ("attempts", len(reads)),
            ("accepted", len(accepted)),
            ("rejected", rejected),
            ("missing", missing),
            ("overlapping", overlapped),
            ("first_generation", 1),
            ("last_generation", 16),
        )
    ):
        raise ValueError(f"summary does not match timestamped events: {path}")
    if overlapped == 0 or len(accepted) == 0:
        raise ValueError(f"no actual write/read competition or complete read: {path}")
    modes = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_MODE,")]
    if len(modes) != 1 or int(modes[0]["direct"]) < len(accepted) + 1 or any(
        int(modes[0][name]) for name in ("one", "packed_greedy", "packed_contiguous")
    ):
        raise ValueError(f"read did not use direct mode: {path}")
    rails = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_RAIL,")]
    if len(rails) != 2 or {item["id"] for item in rails} != {"rail0", "rail1"}:
        raise ValueError(f"both Rail counters were not logged: {path}")
    if any(
        int(item["bytes"]) <= 0
        or any(int(item[name]) for name in ("inflight_requests", "inflight_bytes", "registered_bytes"))
        for item in rails
    ):
        raise ValueError(f"Rail payload or terminal resource closure failed: {path}")
    return {
        "file": path.name,
        "raw_sha256": digest(path),
        "launch_manifest_sha256": digest(launch),
        "exit_receipt_sha256": digest(exit_file),
        "source_commit": source_commit,
        "launch_time_binary_sha256": binary_sha,
        "object_key": object_key,
        "object_mib": object_mib,
        "write_commits": len(writes),
        "read_attempts": len(reads),
        "actually_overlapped_reads": overlapped,
        "accepted": len(accepted),
        "rejected": rejected,
        "missing": missing,
        "overlap_accepted": overlap_accepted,
        "overlap_rejected": overlap_rejected,
        "rejection_reasons": rejection_reasons,
        "accepted_generations": accepted,
        "per_rail_bytes": {item["id"]: int(item["bytes"]) for item in rails},
        "terminal_resources_zero": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--object-mib", type=int, required=True)
    parser.add_argument("--expected-runs", type=int, required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = [args.logs / f"run{index}.log" for index in range(1, args.expected_runs + 1)]
    runs = [parse_one(path, args.object_mib, args.binary) for path in paths]
    if len({run["object_key"] for run in runs}) != len(runs):
        raise ValueError("two runs reused the same object key")
    if len({run["source_commit"] for run in runs}) != 1 or len(
        {run["launch_time_binary_sha256"] for run in runs}
    ) != 1:
        raise ValueError("campaign mixed source commits or binaries")
    server_before = server_image_receipt(args.logs / "server-live-before.txt")
    server_after = server_image_receipt(args.logs / "server-live-after.txt")
    if any(
        server_before[key] != server_after[key]
        for key in ("server_pid", "live_executable_sha256", "config_sha256")
    ):
        raise ValueError("server process or image changed during the campaign")
    totals = {
        key: sum(run[key] for run in runs)
        for key in (
            "write_commits", "read_attempts", "actually_overlapped_reads",
            "accepted", "rejected", "missing", "overlap_accepted", "overlap_rejected",
        )
    }
    result = {
        "environment": "isolated two-RXE KVM guests on one host",
        "scope": "per-Generation unique byte values; exact SDK accepted Generation trace and monotonic read/write interval audit",
        "auditor_sha256": digest(Path(__file__)),
        "runs": runs,
        "server_image_before": server_before,
        "server_image_after": server_after,
        "totals": totals,
        "non_claim": "finite observed schedules and a logged accepted-ID implementation path do not prove all provider, crash, or aliasing behaviors",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output, totals)


if __name__ == "__main__":
    main()
