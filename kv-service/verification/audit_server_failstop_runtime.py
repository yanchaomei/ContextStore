"""Audit launch-time binary binding for isolated server cleanup errors."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_hashes(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        value, path = line.split("  ", 1)
        if len(value) != 64 or path in result:
            raise ValueError("bad launch-time SHA-256 record")
        result[path] = value
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--object-key", required=True)
    parser.add_argument("--expected-xxh3", required=True)
    parser.add_argument("--server-binary", type=Path)
    parser.add_argument("--shim", type=Path)
    parser.add_argument("--client-binary", type=Path)
    parser.add_argument("--launcher-script", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw_hashes = {}
    cases = []
    identities = []
    for operation, slab_mb, expected_path in (
        ("destroy_qp", 128, "RDMA_SUBSET_DETAIL"),
        ("dereg_mr", 4, "slab capacity is 4194304"),
    ):
        case = args.results / operation
        manifest = case / "launch-manifest.txt"
        lines = manifest.read_text().splitlines()
        if lines[:2] != [f"operation={operation}", f"slab_mb={slab_mb}"] or len(lines) != 6:
            raise ValueError(f"bad server launch manifest: {operation}")
        hashes = parse_hashes(lines[2:])
        expected_files = {
            "/home/railtest/bin/contextstore-server": args.server_binary,
            "/home/railtest/libverbs_failstop.so": args.shim,
            "/home/railtest/evidence/server.toml": None,
            "/home/railtest/launch_server_failstop.sh": args.launcher_script,
        }
        if set(hashes) != set(expected_files):
            raise ValueError(f"server/shim/config/script identity incomplete: {operation}")
        for path, supplied in expected_files.items():
            if supplied and digest(supplied) != hashes[path]:
                raise ValueError(f"supplied file differs from launch hash: {path}")
        identities.append(hashes)
        server_log = case / "server.log"
        text = server_log.read_text()
        failure_message = (
            "ContextStore RDMA server QP destruction failed; aborting"
            if operation == "destroy_qp"
            else "ContextStore RDMA server MR deregistration failed; aborting"
        )
        if failure_message not in text or expected_path not in text or args.object_key not in text:
            raise ValueError(f"server did not reach expected object/path: {operation}")
        events_file = case / "verbs.events"
        if events_file.read_text() != f"{operation}\n":
            raise ValueError(f"unexpected Verbs cleanup sequence: {operation}")
        exit_file = case / "exit-code.txt"
        if exit_file.read_text().strip() != "134":
            raise ValueError(f"server did not exit with SIGABRT shell code: {operation}")
        trigger_prefix = args.results / f"{operation}-trigger"
        trigger_manifest = trigger_prefix.with_suffix(".launch")
        trigger_lines = trigger_manifest.read_text().splitlines()
        if trigger_lines[:2] != ["namespace=rail-e2e", f"object_key={args.object_key}"] or len(
            trigger_lines
        ) != 3:
            raise ValueError(f"bad trigger launch manifest: {operation}")
        trigger_hashes = parse_hashes(trigger_lines[2:])
        if set(trigger_hashes) != {"/home/railtest/bin/cs-rail-read-bench-v3"}:
            raise ValueError(f"unexpected trigger binary path: {operation}")
        if args.client_binary and digest(args.client_binary) != next(iter(trigger_hashes.values())):
            raise ValueError(f"trigger binary differs from launch hash: {operation}")
        trigger_exit = trigger_prefix.with_suffix(".exit")
        if trigger_exit.read_text().strip() == "0":
            raise ValueError(f"client request unexpectedly succeeded: {operation}")
        trigger_log = trigger_prefix.with_suffix(".log")
        if "Error:" not in trigger_log.read_text() or "summary," in trigger_log.read_text():
            raise ValueError(f"client returned a completed object: {operation}")
        for path in (
            manifest, server_log, events_file, exit_file,
            trigger_manifest, trigger_exit, trigger_log,
        ):
            raw_hashes[str(path.relative_to(args.results))] = digest(path)
        cases.append(
            {
                "operation": operation,
                "slab_mb": slab_mb,
                "server_shell_exit_code": 134,
                "event_sequence": [operation],
                "server_launch_sha256": hashes,
                "client_trigger_binary_sha256": next(iter(trigger_hashes.values())),
            }
        )
    if identities[0] != identities[1]:
        raise ValueError("the two faults used different server/shim/config/script binaries")

    recovery_prefix = args.results / "normal-recovery"
    recovery_lines = recovery_prefix.with_suffix(".launch").read_text().splitlines()
    if recovery_lines[:2] != ["namespace=rail-e2e", f"object_key={args.object_key}"]:
        raise ValueError("recovery used another object")
    recovery_hashes = parse_hashes(recovery_lines[2:])
    if recovery_hashes != {
        "/home/railtest/bin/cs-rail-read-bench-v3": cases[0]["client_trigger_binary_sha256"]
    }:
        raise ValueError("recovery used another client binary")
    if recovery_prefix.with_suffix(".exit").read_text().strip() != "0":
        raise ValueError("normal server recovery process failed")
    recovery = recovery_prefix.with_suffix(".log").read_text()
    if (
        not re.search(r"^sample,environment=soft-roce,rails=1,iteration=1,bytes=8388608,", recovery, re.M)
        or f"xxh3={args.expected_xxh3}" not in recovery
        or "mode_effective,one=1" not in recovery
    ):
        raise ValueError("normal server did not restore the same complete 8 MiB object")
    for suffix in ("launch", "exit", "log"):
        path = recovery_prefix.with_suffix(f".{suffix}")
        raw_hashes[str(path.relative_to(args.results))] = digest(path)
    result = {
        "environment": "isolated two-RXE server, one-Rail 8 MiB GET trigger",
        "scope": "test-only libibverbs return-value interposition with launch-time server/shim/client/script hashes; not a real provider failure",
        "object_key": args.object_key,
        "auditor_sha256": digest(Path(__file__)),
        "cases": cases,
        "normal_recovery_xxh3": args.expected_xxh3,
        "raw_sha256": raw_hashes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output, "two launch-bound server aborts and normal recovery audited")


if __name__ == "__main__":
    main()
