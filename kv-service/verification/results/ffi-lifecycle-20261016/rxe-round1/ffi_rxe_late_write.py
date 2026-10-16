"""Real RXE legacy FFI GET, then a delayed request after client retirement."""

from __future__ import annotations

import argparse
import ctypes as ct
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time


def receive(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise RuntimeError("unexpected control EOF")
        data.extend(part)
    return bytes(data)


def main() -> None:
    if sys.flags.optimize:
        raise RuntimeError("verification requires Python assertions; optimization is forbidden")
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--bytes", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--failure-mode", choices=("timeout", "bad_tag"), default="timeout")
    parser.add_argument("--unsafe-baseline", action="store_true", help="isolated negative control only: bypass the Python lifecycle capability check")
    args = parser.parse_args()
    if args.bytes <= 0 or args.bytes % (1024 * 1024):
        raise ValueError("bytes must be a positive whole number of MiB")
    if args.unsafe_baseline and args.failure_mode != "bad_tag":
        raise ValueError("the unsafe baseline only supports the matching bad_tag control")
    ct.CDLL(str(args.library.resolve()), mode=ct.RTLD_GLOBAL)
    os.environ["CONTEXTSTORE_RDMA_LIB"] = str(args.library.resolve())
    spec = importlib.util.spec_from_file_location("rxe_rdma_client", args.module)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if args.unsafe_baseline:
        module.RdmaClient._require_safe_lifecycle = lambda self: None
    config = dict(device=args.device, port=1, gid_index=1, buf_size_mb=args.bytes // (1024 * 1024))
    launch = {
        "library_sha256": hashlib.sha256(args.library.read_bytes()).hexdigest(),
        "python_module_sha256": hashlib.sha256(args.module.read_bytes()).hexdigest(),
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment": "real RXE Verbs in two KVM guests on one host; TCP relay delays a GET frame, no provider return-code injection",
        "key": args.key,
        "bytes": args.bytes,
        "failure_mode": args.failure_mode,
        "unsafe_baseline": args.unsafe_baseline,
        "python_optimize": sys.flags.optimize,
        "expected_payload_sha256": hashlib.sha256(bytes(range(251)) * (args.bytes // 251) + bytes(range(args.bytes % 251))).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".launch.json").write_text(json.dumps(launch, indent=2) + "\n")
    expected = bytes(index % 251 for index in range(args.bytes))
    with module.RdmaClient(args.server, **config) as client:
        seed = ct.create_string_buffer(expected, args.bytes)
        seed_rid = client.register_external_buffer(ct.addressof(seed), args.bytes)
        client.put(seed_rid, args.key, 0, args.bytes)
        client.unregister_external_buffer(seed_rid)
        assert client.get(args.key) == args.bytes
        assert client.buffer_view(args.bytes) == expected
        target = ct.create_string_buffer(args.bytes)
        rid = client.register_external_buffer(ct.addressof(target), args.bytes)
        assert client.get_into(rid, args.key) == args.bytes
        assert target.raw == expected
        client.unregister_external_buffer(rid)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    forwarded = threading.Event()
    timeline = {}
    peer_errors = []
    host, port = args.server.rsplit(":", 1)

    def relay() -> None:
        try:
            with (
                listener.accept()[0] as downstream,
                socket.create_connection((host, int(port)), timeout=8) as upstream,
            ):
                downstream.settimeout(45)
                upstream.settimeout(40)
                upstream.sendall(receive(downstream, 25))
                downstream.sendall(receive(upstream, 25))
                header = receive(downstream, 3)
                assert header[0] == 2, "expected a legacy GET request"
                key_len = int.from_bytes(header[1:], "little")
                request = header + receive(downstream, key_len + 20)
                timeline["relay_received_ns"] = time.monotonic_ns()
                if args.failure_mode == "bad_tag":
                    downstream.sendall(b"\xff")
                time.sleep(0.6)
                timeline["relay_forwarded_ns"] = time.monotonic_ns()
                upstream.sendall(request)
                forwarded.set()
                # A terminal response or EOF is mandatory. A timeout never proves
                # that the real server finished its delayed WRITE attempt.
                reply = bytearray()
                while len(reply) < 14:
                    part = upstream.recv(14 - len(reply))
                    if not part:
                        if reply:
                            raise RuntimeError("truncated server GET response")
                        timeline["server_terminal"] = "eof"
                        break
                    reply.extend(part)
                else:
                    if reply[0] != 3:
                        raise RuntimeError("unexpected server response tag")
                    timeline["server_terminal"] = "get_response"
                timeline["server_reply_hex"] = reply.hex()
                timeline["server_terminal_ns"] = time.monotonic_ns()
        except Exception as exc:
            peer_errors.append(str(exc))

    thread = threading.Thread(target=relay, daemon=True)
    thread.start()
    target = ct.create_string_buffer(b"\xa5" * args.bytes, args.bytes)
    deadline = {"timeout": 0.1} if args.failure_mode == "timeout" else {}
    client = module.RdmaClient(f"127.0.0.1:{listener.getsockname()[1]}", **deadline, **config)
    client.connect()
    original_handle = client._handle
    launch["native_handle_before"] = original_handle
    rid = client.register_external_buffer(ct.addressof(target), args.bytes)
    try:
        client.get_into(rid, args.key)
    except RuntimeError:
        timeline["ffi_error_return_ns"] = time.monotonic_ns()
    else:
        raise AssertionError("delayed GET did not fail before the real server WRITE")
    assert target.raw == b"\xa5" * args.bytes
    assert not client._connected
    # Reuse the still-registered allocation, retaining the exact advertised rkey.
    # The old native client can therefore reproduce a real late WRITE without a
    # use-after-free; the fixed client must retire its QP before returning.
    ct.memset(ct.addressof(target), 0x5A, args.bytes)
    timeline["sentinel_reused_ns"] = time.monotonic_ns()
    assert forwarded.wait(3)
    thread.join(45)
    assert not thread.is_alive() and not peer_errors, peer_errors
    assert timeline["server_terminal"] in ("eof", "get_response")
    assert timeline["ffi_error_return_ns"] < timeline["sentinel_reused_ns"] < timeline["relay_forwarded_ns"]
    assert timeline["relay_forwarded_ns"] < timeline["server_terminal_ns"]
    sentinel_intact = target.raw == b"\x5a" * args.bytes
    sentinel_sha256 = hashlib.sha256(target.raw).hexdigest()
    exact_payload_corruption = target.raw == expected
    timeline["sentinel_checked_ns"] = time.monotonic_ns()
    client.unregister_external_buffer(rid)
    # Reconnect the same native handle after error, then exercise actual client
    # PUT completions and a server WRITE into a newly registered target.
    recovery_verified = False
    if sentinel_intact:
        client.server_addr = args.server
        client.connect()
        assert client._handle == original_handle
        ct.memmove(ct.addressof(target), expected, args.bytes)
        recovery_rid = client.register_external_buffer(ct.addressof(target), args.bytes)
        recovery_key = args.key + "-recovery-" + str(time.time_ns())
        client.put(recovery_rid, recovery_key, 0, args.bytes)
        ct.memset(ct.addressof(target), 0xA5, args.bytes)
        assert client.get_into(recovery_rid, recovery_key) == args.bytes
        assert target.raw == expected
        client.unregister_external_buffer(recovery_rid)
        recovery_verified = True
    client.close()
    thread.join(9)
    listener.close()
    assert not thread.is_alive() and not peer_errors, peer_errors
    result = {
        **launch,
        **timeline,
        "classic_get_full_content": True,
        "external_get_full_content": True,
        "error_before_forward": True,
        "reused_sentinel_intact": sentinel_intact,
        "sentinel_sha256": sentinel_sha256,
        "exact_payload_corruption": exact_payload_corruption,
        "native_handle_after": original_handle,
        "expected_sentinel_sha256": hashlib.sha256(b"\x5a" * args.bytes).hexdigest(),
        "same_handle_reconnect_put_get_full_content": recovery_verified,
        "non_claim": "a delayed real server WRITE attempt after client QP retirement; not all interleavings or physical dual-HCA performance",
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    assert sentinel_intact, "late server RDMA WRITE corrupted the reused registered buffer"


if __name__ == "__main__":
    main()
