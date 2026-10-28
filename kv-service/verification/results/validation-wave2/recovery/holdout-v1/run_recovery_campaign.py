#!/usr/bin/env python3
"""Run a frozen native test and retain bounded per-process resource samples."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def sample_process(pid: int) -> dict[str, object]:
    proc = Path(f"/proc/{pid}")
    record: dict[str, object] = {"monotonic_seconds": time.monotonic(), "pid": pid}
    try:
        fields = dict(line.split(":", 1) for line in (proc / "status").read_text().splitlines())
        for name in ("VmRSS", "VmHWM", "VmSize", "Threads"):
            record[name] = fields.get(name, "unavailable").strip()
        record["file_descriptors"] = len(list((proc / "fd").iterdir()))
        record["task_count"] = len(list((proc / "task").iterdir()))
    except FileNotFoundError:
        record["process_exited"] = True
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--test", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    frozen = json.loads(args.freeze.read_text())
    assert hashlib.sha256(args.binary.read_bytes()).hexdigest() == frozen["binary_sha256"], "frozen native test binary mismatch"
    run = next(entry for entry in frozen["runs"] if entry["name"] == args.name)
    assert run["test"] == args.test, "frozen test selection mismatch"
    for name, value in run.items():
        if name.startswith("CS_"):
            assert os.environ.get(name) == str(value), f"frozen setting mismatch: {name}"
    args.output.mkdir(parents=True, exist_ok=False)
    command = [str(args.binary), "--ignored", "--nocapture", "--exact", f"rail_read::tests::recovery_campaign::{args.test}"]
    started = time.time()
    environment = {key: value for key, value in os.environ.items() if key.startswith(("CS_RAIL_", "CS_RECOVERY_", "CS_LIFETIME_", "CS_PUBLICATION_"))}
    (args.output / "launch.json").write_text(json.dumps({"command": command, "environment": environment, "started_unix_seconds": started}, indent=2) + "\n")
    raw = args.output / "run.log"
    samples: list[dict[str, object]] = []
    with raw.open("wb") as log, (args.output / "resources.jsonl").open("w") as resources:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        (args.output / "pid").write_text(f"{process.pid}\n")
        while process.poll() is None:
            sample = sample_process(process.pid)
            samples.append(sample)
            resources.write(json.dumps(sample) + "\n")
            resources.flush()
            time.sleep(1)
        resources.write(json.dumps(sample_process(process.pid)) + "\n")
        code = process.wait()
    compressed = args.output / "run.log.gz"
    with raw.open("rb") as source, gzip.open(compressed, "wb") as target:
        shutil.copyfileobj(source, target)
    assert compressed.stat().st_size < 500 * 1024 * 1024, "compressed evidence exceeds frozen 500 MiB limit"
    peaks = {
        "rss_kib": max((int(str(row.get("VmRSS", "0 kB")).split()[0]) for row in samples), default=0),
        "file_descriptors": max((int(row.get("file_descriptors", 0)) for row in samples), default=0),
        "threads": max((int(str(row.get("Threads", "0"))) for row in samples), default=0),
    }
    limits = {"rss_kib": int(os.environ.get("CS_RECOVERY_RSS_MAX_KIB", "524288")), "file_descriptors": 128, "threads": 64}
    within_limits = all(peaks[name] <= value for name, value in limits.items())
    (args.output / "exit.json").write_text(json.dumps({"returncode": code, "elapsed_seconds": time.time() - started, "raw_log_bytes": raw.stat().st_size, "gzip_log_bytes": compressed.stat().st_size, "resource_sample_count": len(samples), "process_resource_peaks": peaks, "process_resource_limits": limits, "within_process_resource_limits": within_limits}, indent=2) + "\n")
    print((args.output / "exit.json").read_text().strip())
    raise SystemExit(code if code else (0 if within_limits else 1))


if __name__ == "__main__":
    main()
