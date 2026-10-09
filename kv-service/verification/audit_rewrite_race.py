"""Audit saved direct-staging concurrent-rewrite RXE test transcripts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


WRITE = re.compile(r"^RACE_WRITE,revision=(\d+),generation=(\d+)$", re.M)
READ = re.compile(
    r"^RACE_READ,attempt=(\d+),result=(accepted_A|accepted_B|rejected|missing)"
    r"(?:,error=(.*))?$",
    re.M,
)
SUMMARY = re.compile(
    r"^RACE_SUMMARY,accepted_a=(\d+),accepted_b=(\d+),rejected=(\d+),"
    r"missing=(\d+),first_generation=(\d+),last_generation=(\d+)$",
    re.M,
)
CONFIG = re.compile(
    r"^RACE_CONFIG,object_key=([^,]+),object_mib=(\d+),stripe_count=(\d+),"
    r"direct_staging=true$",
    re.M,
)
MODE = re.compile(
    r"^RACE_MODE,direct=(\d+),one=(\d+),packed_greedy=(\d+),"
    r"packed_contiguous=(\d+)$",
    re.M,
)
RAIL = re.compile(
    r"^RACE_RAIL,id=(rail[01]),bytes=(\d+),reads_ok=(\d+),reads_err=(\d+),"
    r"inflight_requests=(\d+),inflight_bytes=(\d+),registered_bytes=(\d+)$",
    re.M,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_one(path: Path, expected_mib: int) -> dict:
    raw = path.read_text()
    if "test result: ok. 1 passed; 0 failed" not in raw:
        raise ValueError(f"hardware-gated test did not pass: {path}")
    writes = [(int(revision), int(generation)) for revision, generation in WRITE.findall(raw)]
    if len(writes) != 15 or [revision for revision, _ in writes] != list(range(15)):
        raise ValueError(f"missing or repeated writer revision: {path}")
    generations = [generation for _, generation in writes]
    if generations != list(range(2, 17)):
        raise ValueError(f"writer generation sequence changed: {path}")
    reads = [(int(attempt), outcome, error) for attempt, outcome, error in READ.findall(raw)]
    if not 20 <= len(reads) <= 36 or [attempt for attempt, _, _ in reads] != list(range(len(reads))):
        raise ValueError(f"missing or repeated concurrent read: {path}")
    counts = {
        "accepted_a": sum(outcome == "accepted_A" for _, outcome, _ in reads),
        "accepted_b": sum(outcome == "accepted_B" for _, outcome, _ in reads),
        "rejected": sum(outcome == "rejected" for _, outcome, _ in reads),
        "missing": sum(outcome == "missing" for _, outcome, _ in reads),
        "version_rejected": sum(
            outcome == "rejected" and error == "object version changed during rail read"
            for _, outcome, error in reads
        ),
        "stale_descriptor_rejected": sum(
            outcome == "rejected" and error == "object descriptor became stale"
            for _, outcome, error in reads
        ),
    }
    if counts["version_rejected"] + counts["stale_descriptor_rejected"] != counts["rejected"]:
        raise ValueError(f"a rejected read had another error cause: {path}")
    if any(error for _, outcome, error in reads if outcome != "rejected"):
        raise ValueError(f"an accepted/missing read had an error field: {path}")
    summary = SUMMARY.findall(raw)
    if len(summary) != 1 or tuple(map(int, summary[0])) != (
        counts["accepted_a"],
        counts["accepted_b"],
        counts["rejected"],
        counts["missing"],
        1,
        16,
    ):
        raise ValueError(f"printed summary differs from raw events: {path}")
    if counts["accepted_a"] + counts["accepted_b"] == 0 or counts["rejected"] == 0:
        raise ValueError(f"campaign did not expose both accepted and version-rejected reads: {path}")
    config = CONFIG.findall(raw)
    modes = MODE.findall(raw)
    rails = RAIL.findall(raw)
    if config:
        if (
            len(config) != 1
            or int(config[0][1]) != expected_mib
            or int(config[0][2]) != expected_mib // 4
            or not config[0][0].startswith("direct-race-")
        ):
            raise ValueError(f"object configuration does not match test fixture: {path}")
        if len(modes) != 1 or tuple(map(int, modes[0])) != (len(reads) + 1, 0, 0, 0):
            raise ValueError(f"test did not admit every read through direct mode: {path}")
        if len(rails) != 2 or {rail[0] for rail in rails} != {"rail0", "rail1"}:
            raise ValueError(f"test did not use both Rails: {path}")
        for rail in rails:
            _, byte_count, reads_ok, reads_err, inflight, inflight_bytes, registered = rail
            if (
                int(byte_count) == 0
                or int(reads_ok) + int(reads_err) != len(reads) + 1
                or any(int(value) for value in (inflight, inflight_bytes, registered))
            ):
                raise ValueError(f"Rail activity or resource closure mismatch: {path}")
    elif modes or rails:
        raise ValueError(f"incomplete enriched mode/Rail records: {path}")
    return {
        "file": path.name,
        "sha256": digest(path),
        "object_mib": expected_mib,
        "concurrent_read_attempts": len(reads),
        "configuration_logged": bool(config),
        "object_key": config[0][0] if config else None,
        "mode_direct_admissions": int(modes[0][0]) if modes else None,
        "rail_bytes": {rail[0]: int(rail[1]) for rail in rails} if rails else None,
        **counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--object-mib", type=int, required=True)
    parser.add_argument("--expected-runs", type=int, required=True)
    args = parser.parse_args()
    paths = sorted(args.logs.glob("run*.log"))
    if len(paths) != args.expected_runs or args.object_mib < 8 or args.object_mib % 4:
        parser.error("unexpected run count or non-striped object size")
    runs = [audit_one(path, args.object_mib) for path in paths]
    keys = [run["object_key"] for run in runs if run["object_key"]]
    if len(keys) != len(set(keys)):
        raise ValueError("two runs reused the same test object")
    totals = {
        name: sum(run[name] for run in runs)
        for name in (
            "accepted_a", "accepted_b", "rejected", "missing",
            "version_rejected", "stale_descriptor_rejected",
        )
    }
    result = {
        "environment": f"two independent software RXE paths, {args.object_mib} MiB and {args.object_mib // 4} stored stripes",
        "scope": f"{len(runs)} process runs; each performs 15 rewrites and at least 20 concurrent direct reads, followed by one asserted final-B read",
        "binary_sha256": digest(args.binary) if args.binary else None,
        "auditor_sha256": digest(Path(__file__)),
        "runs": runs,
        "totals": totals,
        "interpretation": "test code checks every accepted byte against complete A or B and failure sentinels; transcript audit verifies exposed version rejections, generations, outcomes and test success, not every interleaving",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output, totals)


if __name__ == "__main__":
    main()
