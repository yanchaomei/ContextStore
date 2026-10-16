from __future__ import annotations

# Live demonstration driver. Run on the configured isolated RXE client.
import argparse
import ctypes as ct
import hashlib
import json
import os
from pathlib import Path
import shlex
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
SERVER = "10.31.0.2:55153"
DEVICE = "rxe_c0"
KEY = "3:ffirailfuse-demo-v2"


def emit(label, values):
    print(label, flush=True)
    for field, value in values.items():
        print(f"  {field}: {value}", flush=True)


def seed_and_read(out):
    os.environ["CONTEXTSTORE_RDMA_LIB"] = str(ROOT / "candidate.so")
    from rdma_client import RdmaClient

    size = 64 * 1024 * 1024
    payload = bytes(range(251)) * (size // 251) + bytes(range(size % 251))
    target = ct.create_string_buffer(payload, size)
    with RdmaClient(SERVER, device=DEVICE, gid_index=1, buf_size_mb=1) as client:
        rid = client.register_external_buffer(ct.addressof(target), size)
        client.put(rid, KEY, 0, size)
        ct.memset(ct.addressof(target), 0xA5, size)
        count = client.get_into(rid, KEY)
        if count != size or target.raw != payload:
            raise RuntimeError("seed/full-content GET verification failed")
        client.unregister_external_buffer(rid)
    result = dict(
        bytes=size,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        full_content_verified=True,
    )
    (out / "seed.json").write_text(json.dumps(result, indent=2) + "\n")
    emit("LIVE: PUT then full-content GET", result)
    base = [
        "./cs-rail-read-bench",
        "--environment",
        "soft-roce",
        "--coordinator",
        "http://10.31.0.2:55151",
        "--namespace",
        "ffi",
        "--object-key",
        "railfuse-demo-v2",
        "--warmup",
        "0",
        "--iterations",
        "1",
    ]
    commands = {}
    for mode, flags in [
        ("single", ["--rail", "r0,rxe_c0,10.31.0.2:55153,10.31.0.2:55153,1,1"]),
        (
            "dual",
            [
                "--direct-staging",
                "--rail",
                "r0,rxe_c0,10.31.0.2:55153,10.31.0.2:55153,1,1",
                "--rail",
                "r1,rxe_c1,10.31.0.2:55153,10.32.0.2:55154,1,1",
            ],
        ),
    ]:
        cmd = base + flags
        commands[mode] = cmd
        print("$ " + shlex.join(cmd), flush=True)
        process = subprocess.run(
            cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60
        )
        (out / f"{mode}.log").write_bytes(process.stdout)
        (out / f"{mode}.exit").write_text(str(process.returncode) + "\n")
        print(process.stdout.decode(errors="replace"), flush=True)
        samples = re.findall(
            r"^sample,.*xxh3=([0-9a-f]+)$", process.stdout.decode(), re.MULTILINE
        )
        if process.returncode or samples != ["a0a4cbfa5cad46af"]:
            raise RuntimeError(f"{mode} read failed or fixture checksum mismatched")
    (out / "read-commands.json").write_text(json.dumps(commands, indent=2) + "\n")


def late_write(out):
    for baseline in [True, False]:
        name = "baseline" if baseline else "fixed"
        cmd = [
            sys.executable,
            "ffi_rxe_late_write.py",
            "--library",
            "baseline.so" if baseline else "candidate.so",
            "--module",
            "rdma_client.py",
            "--server",
            SERVER,
            "--device",
            DEVICE,
            "--key",
            f"3:ffi-demo-v2-{time.time_ns()}",
            "--bytes",
            "1048576",
            "--failure-mode",
            "bad_tag",
            "--output",
            str(out / f"{name}.json"),
        ]
        if baseline:
            cmd.append("--unsafe-baseline")
        print("$ " + shlex.join(cmd), flush=True)
        if not baseline:
            print(
                "Waiting for the real delayed server terminal state (about 30 seconds).",
                flush=True,
            )
        process = subprocess.run(
            cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90
        )
        (out / f"{name}.log").write_bytes(process.stdout)
        (out / f"{name}.exit").write_text(str(process.returncode) + "\n")
        (out / f"{name}.command.json").write_text(json.dumps(cmd) + "\n")
        result = json.loads((out / f"{name}.json").read_text())
        fields = [
            "reused_sentinel_intact",
            "exact_payload_corruption",
            "sentinel_sha256",
            "expected_payload_sha256",
            "same_handle_reconnect_put_get_full_content",
        ]
        emit("LIVE: " + name + " result", {key: result[key] for key in fields})
        if baseline:
            valid = (
                process.returncode == 1
                and result["exact_payload_corruption"]
                and not result["reused_sentinel_intact"]
                and result["sentinel_sha256"] == result["expected_payload_sha256"]
            )
        else:
            valid = (
                process.returncode == 0
                and result["reused_sentinel_intact"]
                and result["same_handle_reconnect_put_get_full_content"]
            )
        if not valid:
            raise RuntimeError(f"{name} late-WRITE evidence did not meet the contract")


def put_recovery(out):
    cmd = [
        sys.executable,
        "ffi_rxe_put_reconnect.py",
        "--library",
        "candidate.so",
        "--module",
        "rdma_client.py",
        "--server",
        SERVER,
        "--device",
        DEVICE,
        "--output",
        str(out / "put-cq.json"),
    ]
    env = os.environ.copy()
    env.update(
        LD_PRELOAD=str(ROOT / "verbs_cq_delay.so"),
        CS_CQ_DELAY_LOG=str(out / "put-cq.events"),
    )
    print(
        "$ CS_CQ_DELAY_LOG=... LD_PRELOAD=./verbs_cq_delay.so " + shlex.join(cmd),
        flush=True,
    )
    print(
        "Controlled CQ poll suppression; recorded WC contents come from the real provider.",
        flush=True,
    )
    process = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
    )
    (out / "put-cq.log").write_bytes(process.stdout)
    (out / "put-cq.exit").write_text(str(process.returncode) + "\n")
    (out / "put-cq.command.json").write_text(json.dumps(cmd) + "\n")
    result = json.loads((out / "put-cq.json").read_text())
    emit(
        "LIVE: PUT/CQ recovery",
        {
            key: result[key]
            for key in [
                "old_qp_real_completion_drained_before_error_return",
                "same_registered_buffer_reused",
                "reconnect_put_get_full_content_rounds",
                "passed",
            ]
        },
    )
    print((out / "put-cq.events").read_text(), flush=True)
    if process.returncode or not result["passed"]:
        raise RuntimeError("PUT/CQ recovery failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=["read", "late", "put", "all"], nargs="?", default="all"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.flags.optimize:
        raise RuntimeError("optimized Python cannot run the live verification demo")
    args.output.mkdir(parents=True, exist_ok=False)
    hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in [
            "candidate.so",
            "baseline.so",
            "verbs_cq_delay.so",
            "cs-rail-read-bench",
            "rdma_client.py",
            "ffi_rxe_late_write.py",
            "ffi_rxe_put_reconnect.py",
        ]
    }
    (args.output / "launch.json").write_text(
        json.dumps(
            dict(
                hashes=hashes,
                mode=args.mode,
                scope="real RXE on isolated KVM guests; controlled CQ suppression only in PUT probe",
            ),
            indent=2,
        )
        + "\n"
    )
    emit("Live RXE demonstration", dict(mode=args.mode, server=SERVER, device=DEVICE))
    if args.mode in ["read", "all"]:
        seed_and_read(args.output)
    if args.mode in ["late", "all"]:
        late_write(args.output)
    if args.mode in ["put", "all"]:
        put_recovery(args.output)
    hashes_after = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in hashes
    }
    if hashes_after != hashes:
        raise RuntimeError("demo executable or source changed during capture")
    (args.output / "completed.json").write_text(
        json.dumps(
            dict(passed=True, mode=args.mode, hashes_after=hashes_after), indent=2
        )
        + "\n"
    )
    print("LIVE_DEMO_COMPLETE: all requested checks passed", flush=True)


if __name__ == "__main__":
    main()
