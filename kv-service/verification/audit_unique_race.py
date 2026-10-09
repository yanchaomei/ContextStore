"""Audit generation-unique direct-Rail race logs and measured intervals."""

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
    put_scope = config[0].get("put_interval_scope", "prepare_plus_sdk_call")
    if put_scope not in ("prepare_plus_sdk_call", "sdk_call"):
        raise ValueError(f"unknown PUT interval scope: {path}")
    object_key = config[0]["object_key"]
    writes = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_WRITE,")]
    if len(writes) != 15:
        raise ValueError(f"expected 15 committed generations: {path}")
    has_put_end = all("put_end_ns" in item for item in writes)
    if any(("put_end_ns" in item) != has_put_end for item in writes):
        raise ValueError(f"mixed writer timestamp formats: {path}")
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
        put_end = int(item["put_end_ns"]) if has_put_end else None
        if put_end is not None and not start < put_end <= end:
            raise ValueError(f"invalid PUT completion timestamp: {path}")
        write_ranges.append((start, put_end, end))

    reads = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_READ,")]
    if not 20 <= len(reads) <= 36:
        raise ValueError(f"unexpected read-attempt count: {path}")
    accepted = []
    rejected = 0
    missing = 0
    round_overlapped = 0
    round_accepted = 0
    round_rejected = 0
    put_overlapped = 0
    put_accepted = 0
    put_rejected = 0
    rejection_reasons: dict[str, int] = {}
    for attempt, item in enumerate(reads):
        if int(item["attempt"]) != attempt:
            raise ValueError(f"read attempt sequence changed: {path}")
        start, end = int(item["start_ns"]), int(item["end_ns"])
        if not 0 <= start < end:
            raise ValueError(f"invalid reader interval: {path}")
        round_overlap = any(
            start < round_end and write_start < end
            for write_start, _, round_end in write_ranges
        )
        put_overlap = has_put_end and any(
            start < put_end and write_start < end
            for write_start, put_end, _ in write_ranges
        )
        round_overlapped += round_overlap
        put_overlapped += put_overlap
        outcome = item["result"]
        if outcome == "accepted":
            value, generation = int(item["byte"]), int(item["generation_inferred"])
            expected = 1 if value == 0x10 else value - 0x20 + 2
            if value not in (0x10, *range(0x20, 0x2F)) or generation != expected:
                raise ValueError(f"accepted byte does not name one generation: {path}")
            accepted.append(generation)
            round_accepted += round_overlap
            put_accepted += put_overlap
        elif outcome == "rejected":
            reason = item.get("error", "")
            if reason not in (
                "object version changed during rail read",
                "object descriptor became stale",
            ):
                raise ValueError(f"unexpected reject reason: {path}: {reason}")
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            rejected += 1
            round_rejected += round_overlap
            put_rejected += put_overlap
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
    expected_summary = {
        "attempts": len(reads),
        "accepted": len(accepted),
        "rejected": rejected,
        "missing": missing,
        "first_generation": 1,
        "last_generation": 16,
    }
    if has_put_end:
        expected_summary["put_overlapping"] = put_overlapped
        expected_summary["round_overlapping"] = round_overlapped
    else:
        expected_summary["overlapping"] = round_overlapped
    if len(summary) != 1 or set(summary[0]) != set(expected_summary) or any(
        int(summary[0][name]) != value for name, value in expected_summary.items()
    ):
        raise ValueError(f"summary does not match timestamped events: {path}")
    if round_overlapped == 0 or (has_put_end and put_overlapped == 0) or len(accepted) == 0:
        raise ValueError(f"no writer/read intersection or complete read: {path}")
    modes = [fields(line) for line in raw.splitlines() if line.startswith("UNIQUE_MODE,")]
    if len(modes) != 1 or int(modes[0]["direct"]) != len(reads) + 1 or any(
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
        "source_commit_label": source_commit,
        "launch_time_binary_sha256": binary_sha,
        "object_key": object_key,
        "object_mib": object_mib,
        "write_commits": len(writes),
        "read_attempts": len(reads),
        "writer_round_overlap_reads": round_overlapped,
        "put_overlap_reads": put_overlapped if has_put_end else None,
        "accepted": len(accepted),
        "rejected": rejected,
        "missing": missing,
        "round_overlap_accepted": round_accepted,
        "round_overlap_rejected": round_rejected,
        "put_overlap_accepted": put_accepted if has_put_end else None,
        "put_overlap_rejected": put_rejected if has_put_end else None,
        "direct_mode_admissions": int(modes[0]["direct"]),
        "put_end_timestamp_logged": has_put_end,
        "put_interval_scope": put_scope if has_put_end else None,
        "put_interval_definition": (
            "start_ns is recorded immediately before put_stream_chunks and "
            "after payload Bytes allocation/segmentation; put_end_ns is "
            "recorded after the SDK call returns. Overlap establishes SDK "
            "call overlap, not server commit overlap."
            if has_put_end and put_scope == "sdk_call" else
            "start_ns is recorded before payload Bytes allocation/segmentation; "
            "put_end_ns is recorded after put_stream_chunks returns. Overlap "
            "with this client PUT attempt includes preparation time and does "
            "not establish overlap with the SDK call or server commit."
            if has_put_end else None
        ),
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
    if len({run["source_commit_label"] for run in runs}) != 1 or len(
        {run["launch_time_binary_sha256"] for run in runs}
    ) != 1:
        raise ValueError("campaign mixed source commits or binaries")
    if len({run["put_interval_scope"] for run in runs}) != 1:
        raise ValueError("campaign mixed PUT interval scopes")
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
            "write_commits", "read_attempts", "writer_round_overlap_reads",
            "accepted", "rejected", "missing", "round_overlap_accepted", "round_overlap_rejected",
        )
    }
    if all(run["put_end_timestamp_logged"] for run in runs):
        for key in ("put_overlap_reads", "put_overlap_accepted", "put_overlap_rejected"):
            totals[key] = sum(run[key] for run in runs)
    else:
        totals.update(
            put_overlap_reads=None,
            put_overlap_accepted=None,
            put_overlap_rejected=None,
        )
    result = {
        "environment": "isolated two-RXE KVM guests on one host",
        "scope": "per-Generation unique byte values; SDK accepted Generation trace; writer-round overlap for all runs; put_overlap_* counts intersection with the per-run put_interval_scope, not proven server commit overlap",
        "auditor_sha256": digest(Path(__file__)),
        "runs": runs,
        "server_image_before": server_before,
        "server_image_after": server_after,
        "totals": totals,
        "source_commit_label_boundary": "source_commit in launch manifests is an operator-supplied label; binary SHA was recorded before execution and source/build identity requires a separate build receipt",
        "put_interval_boundary": (
            "put_overlap_* counts SDK put_stream_chunks call intersections; payload "
            "allocation/segmentation ended before start_ns, but server commit overlap is unknown"
            if runs[0]["put_interval_scope"] == "sdk_call" else
            "put_overlap_* counts payload preparation plus SDK call intersections; "
            "SDK call and server commit overlap are unknown"
            if runs[0]["put_interval_scope"] == "prepare_plus_sdk_call" else
            "put_end_ns is absent; only the wider writer-round interval is available"
        ),
        "non_claim": "finite observed schedules and a logged accepted-ID implementation path do not prove all provider, crash, or aliasing behaviors",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output, totals)


if __name__ == "__main__":
    main()
