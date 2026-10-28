"""Run the frozen native soak executable and preserve all output under a hard budget."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=7200)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment["CS_SOAK_SECONDS"] = str(args.seconds)
    command = [str(args.binary.resolve()), "healthy_single_hca_two_hour_runtime", "--ignored", "--exact", "--nocapture", "--test-threads=1"]
    start = time.monotonic()
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
    process_fields = Path(f"/proc/{child.pid}/stat").read_text().rsplit(")", 1)[1].split()
    receipt = {"pid": child.pid, "runner_pid": os.getpid(), "command": command, "duration_s": args.seconds,
               "process_start_ticks": int(process_fields[19]),
               "started_unix": time.time(), "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
               "output_raw_budget_bytes": 500_000_000, "timeout_policy": "No 2-hour kill; graceful final burst and cleanup required"}
    (args.output / "startup.json").write_text(json.dumps(receipt, indent=2) + "\n")
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ, "stdout")
    selector.register(child.stderr, selectors.EVENT_READ, "stderr")
    streams = {kind: gzip.open(args.output / f"{kind}.log.gz", "wb") for kind in ("stdout", "stderr")}
    total = 0
    budget_exceeded = False
    watchdog_expired = False
    last_progress = 0.0
    progress_buffer = b""
    event_counts: dict[str, int] = {}
    summary = None
    try:
        while selector.get_map():
            if time.monotonic() - start > args.seconds + 300 and not watchdog_expired:
                watchdog_expired = True
                child.terminate()
            for key, _ in selector.select(timeout=1):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                streams[key.data].write(data)
                streams[key.data].flush()
                total += len(data)
                if total > receipt["output_raw_budget_bytes"] and not budget_exceeded:
                    budget_exceeded = True
                    child.terminate()
                if key.data == "stdout":
                    progress_buffer += data
                    while b"\n" in progress_buffer:
                        line, progress_buffer = progress_buffer.split(b"\n", 1)
                        # libtest may prepend its test name to the first event.
                        marker = line.find(b'{"event":')
                        if marker < 0:
                            continue
                        event = json.loads(line[marker:])
                        kind = event["event"]
                        event_counts[kind] = event_counts.get(kind, 0) + 1
                        if kind == "request":
                            event_counts[event["outcome"]] = event_counts.get(event["outcome"], 0) + 1
                        else:
                            (args.output / "latest.json").write_text(json.dumps({"last_event": event, "counts": event_counts, "raw_bytes": total}, indent=2) + "\n")
                        if kind == "summary":
                            summary = event
            if time.monotonic() - last_progress >= 1:
                (args.output / "progress.json").write_text(json.dumps({"pid": child.pid, "elapsed_s": time.monotonic() - start, "counts": event_counts, "raw_bytes": total}) + "\n")
                last_progress = time.monotonic()
    finally:
        for stream in streams.values():
            stream.close()
    code = child.wait()
    result = {**receipt, "exit_code": code, "wall_elapsed_s": time.monotonic() - start,
              "raw_bytes": total, "budget_exceeded": budget_exceeded, "watchdog_expired": watchdog_expired, "counts": event_counts, "summary": summary,
              "status": "pass" if code == 0 and not budget_exceeded and not watchdog_expired and summary and summary["steady_elapsed_s"] >= args.seconds else "fail"}
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    raise SystemExit(0 if result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
