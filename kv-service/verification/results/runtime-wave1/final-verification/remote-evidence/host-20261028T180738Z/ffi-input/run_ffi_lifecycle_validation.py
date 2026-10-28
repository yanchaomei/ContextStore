from __future__ import annotations

# Run isolated native FFI failure probes and preserve raw outcomes.

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_passed(
    name: str, operation: str, returncode: int, stdout: bytes, events: list[str]
) -> bool:
    if b"UNSAFE_CLEANUP_RETURNED" in stdout:
        return False
    tail = ["destroy_cq", "dealloc_pd", "close_device"]
    if name == "init_failure":
        return (
            returncode == 0
            and b"SAFE_INIT_FAILURE,resources_released=true" in stdout
            and events == ["query_gid"] + tail
        )
    if operation:
        return returncode == -signal.SIGABRT and events == [operation]
    marker = f"SAFE_ERROR_RETURN,case={name},qp_retired=true".encode()
    return (
        returncode == 0
        and marker in stdout
        and events == ["destroy_qp", "dereg_mr", "dereg_mr"] + tail
    )


def main() -> None:
    if sys.flags.optimize:
        raise RuntimeError(
            "optimized Python cannot run FFI safety validation; "
            "run without -O/-OO or PYTHONOPTIMIZE"
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--shim", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--gid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--require-timeout", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    probe = Path(__file__).with_name("ffi_lifecycle_probe.py")
    cases = [("free_qp", "destroy_qp"), ("free_mr", "dereg_mr"), ("unregister_mr", "dereg_mr")]
    cases += [
        (name, "")
        for name in (
            "connect_error",
            "get_error",
            "get_into_error",
            "descriptor_error",
            "descriptor_into_error",
            "put_error",
            "oversized_reply",
            "short_descriptor",
        )
    ]
    cases.append(("init_failure", "query_gid"))
    if args.require_timeout:
        cases.append(("timeout", ""))
    manifest = {
        "source_commit_label": args.source_commit,
        "library_sha256": digest(args.library),
        "shim_sha256": digest(args.shim),
        "probe_sha256": digest(probe),
        "runner_sha256": digest(Path(__file__)),
        "python_optimize": sys.flags.optimize,
        "device": args.device,
        "gid_index": args.gid,
        "scope": "real local Verbs allocations and self-QP control handshake; no RDMA data WR, no bandwidth claim; shim fakes cleanup return codes",
    }
    (args.output / "launch.json").write_text(json.dumps(manifest, indent=2) + "\n")
    results = []
    for name, operation in cases:
        events = args.output / f"{name}.events"
        if events.exists():
            raise ValueError(f"refusing to reuse prior event file: {events}")
        # Preserve optimization settings so an unsafe child invocation fails
        # visibly and its stderr/exit code remain in the raw case evidence.
        env = os.environ.copy()
        env.update(
            LD_PRELOAD=str(args.shim.resolve()),
            CS_FAIL_VERBS_LOG=str(events.resolve()),
            CS_FAIL_VERBS_OP=operation,
        )
        process = subprocess.run(
            [
                sys.executable,
                str(probe),
                "--library",
                str(args.library.resolve()),
                "--device",
                args.device,
                "--gid",
                str(args.gid),
                "--case",
                name,
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=12,
        )
        (args.output / f"{name}.stdout").write_bytes(process.stdout)
        (args.output / f"{name}.stderr").write_bytes(process.stderr)
        (args.output / f"{name}.exit").write_text(str(process.returncode) + "\n")
        expected = -signal.SIGABRT if operation and name != "init_failure" else 0
        recorded_events = events.read_text().splitlines() if events.exists() else []
        result = {
            "case": name,
            "exit": process.returncode,
            "expected_exit": expected,
            "passed": case_passed(
                name, operation, process.returncode, process.stdout, recorded_events
            ),
            "events": recorded_events,
            "stdout_sha256": hashlib.sha256(process.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(process.stderr).hexdigest(),
            "events_sha256": digest(events) if events.exists() else None,
        }
        results.append(result)
        print(name, result["exit"], result["passed"], flush=True)
    output = {**manifest, "cases": results, "all_passed": all(item["passed"] for item in results)}
    (args.output / "result.json").write_text(json.dumps(output, indent=2) + "\n")
    raise SystemExit(0 if output["all_passed"] else 1)


if __name__ == "__main__":
    main()
