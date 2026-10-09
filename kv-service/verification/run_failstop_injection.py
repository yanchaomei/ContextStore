"""Exercise fail-stop cleanup in an isolated RXE guest with stderr closed.

The test-only LD_PRELOAD shim returns an error from one Verbs cleanup call.
This tests our Rust error branch, not a genuine provider or firmware failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import signal
import subprocess
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close_stderr_and_core() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        os.close(2)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--shim", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(args.binary),
        "--environment", "soft-roce",
        "--coordinator", "http://10.31.0.2:55151",
        "--namespace", "rust-bench",
        "--object-key", "paper640/__combined__",
        "--warmup", "1",
        "--iterations", "1",
        "--rail", "r0,rxe_c0,10.31.0.2:55153,10.31.0.2:55153,1,1",
    ]
    cases = []
    for operation in ("destroy_qp", "dereg_mr"):
        events_path = args.output_dir / f"{operation}.events"
        events_path.unlink(missing_ok=True)
        env = dict(os.environ)
        env.update(
            LD_PRELOAD=str(args.shim),
            CS_FAIL_VERBS_OP=operation,
            CS_FAIL_VERBS_LOG=str(events_path),
        )
        completed = subprocess.run(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
            preexec_fn=close_stderr_and_core,
            check=False,
        )
        stdout_path = args.output_dir / f"{operation}.stdout"
        stdout_path.write_text(completed.stdout)
        events = events_path.read_text().splitlines() if events_path.exists() else []
        expected_events = ["destroy_qp"] if operation == "destroy_qp" else [
            "destroy_qp", "dereg_mr"
        ]
        if completed.returncode != -signal.SIGABRT or events != expected_events:
            raise RuntimeError(
                f"{operation}: expected SIGABRT and {expected_events}, "
                f"got returncode={completed.returncode}, events={events}"
            )
        cases.append(
            {
                "operation": operation,
                "returncode": completed.returncode,
                "signal": "SIGABRT",
                "stderr_fd": "closed before program start",
                "verbs_cleanup_events_before_abort": events,
                "stdout_sha256": digest(stdout_path),
            }
        )
    normal = subprocess.run(
        command,
        env={key: value for key, value in os.environ.items() if key not in (
            "LD_PRELOAD", "CS_FAIL_VERBS_OP", "CS_FAIL_VERBS_LOG"
        )},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    (args.output_dir / "normal-after-fault.stdout").write_text(normal.stdout)
    if normal.returncode != 0 or "xxh3=a0a4cbfa5cad46af" not in normal.stdout:
        raise RuntimeError("normal one-Rail read did not recover after isolated aborts")
    result = {
        "scope": "isolated single-Rail RXE process, test-only Verbs return-value interposition",
        "non_claim": "does not reproduce a real provider failure or establish multi-Rail late-WRITE safety",
        "binary_sha256": digest(args.binary),
        "shim_sha256": digest(args.shim),
        "cases": cases,
        "normal_after_fault": {
            "returncode": normal.returncode,
            "stdout_sha256": digest(args.output_dir / "normal-after-fault.stdout"),
        },
    }
    output = args.output_dir / "result.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(output, "two injected aborts and one normal recovery passed")


if __name__ == "__main__":
    main()
