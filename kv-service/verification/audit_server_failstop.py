"""Audit isolated RXE server cleanup-error return injection receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--server-binary", type=Path)
    parser.add_argument("--shim", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expectations = {
        "destroy_qp": (
            "destroy_qp\n",
            "ContextStore RDMA server QP destruction failed; aborting",
            "RDMA_SUBSET_DETAIL",
        ),
        "dereg_mr": (
            "dereg_mr\n",
            "ContextStore RDMA server MR deregistration failed; aborting",
            "slab capacity is 16777216",
        ),
    }
    cases = []
    hashes = {}
    for operation, (events_expected, failure_message, path_evidence) in expectations.items():
        case = args.results / operation
        exit_file = case / "exit-code.txt"
        events_file = case / "verbs.events"
        server_file = case / "server.log"
        trigger_file = args.results / f"server-failstop-{operation.replace('_', '-')}-trigger.log"
        # The QP trigger uses the longer public filename for clarity.
        if operation == "destroy_qp":
            trigger_file = args.results / "server-failstop-destroy-qp-trigger.log"
        if exit_file.read_text().strip() != "134":
            raise ValueError(f"server did not exit with SIGABRT shell code: {operation}")
        if events_file.read_text() != events_expected:
            raise ValueError(f"cleanup call sequence differed: {operation}")
        server = server_file.read_text()
        if failure_message not in server or path_evidence not in server:
            raise ValueError(f"server did not reach the intended cleanup path: {operation}")
        trigger = trigger_file.read_text()
        if "Error:" not in trigger or "summary," in trigger:
            raise ValueError(f"client did not observe a failed request: {operation}")
        for path in (exit_file, events_file, server_file, trigger_file):
            hashes[str(path.relative_to(args.results))] = digest(path)
        cases.append(
            {
                "operation": operation,
                "server_shell_exit_code": 134,
                "event_sequence": events_file.read_text().splitlines(),
                "client_request": "failed without a completed summary",
            }
        )
    recovery = args.results / "server-failstop-normal-recovery.log"
    text = recovery.read_text()
    if (
        "sample,environment=soft-roce,rails=1,iteration=1,bytes=33554432" not in text
        or "xxh3=cbc0b23c1894ad96" not in text
        or "mode_effective,one=1" not in text
    ):
        raise ValueError("normal server restart did not read the same whole object")
    hashes[recovery.name] = digest(recovery)
    result = {
        "environment": "isolated two-RXE KVM server; one-Rail 32 MiB object trigger",
        "scope": "test-only LD_PRELOAD returns -1 from one libibverbs cleanup call; not a genuine provider failure",
        "server_binary_sha256": digest(args.server_binary) if args.server_binary else None,
        "shim_sha256": digest(args.shim) if args.shim else None,
        "auditor_sha256": digest(Path(__file__)),
        "cases": cases,
        "normal_recovery": "same 32 MiB object read successfully after an uninjected server restart",
        "source_sha256": hashes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output, "2 server fail-stop paths and normal recovery audited")


if __name__ == "__main__":
    main()
