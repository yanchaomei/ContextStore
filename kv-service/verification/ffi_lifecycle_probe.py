from __future__ import annotations

# Isolated C-ABI lifecycle probe; peer sends no RDMA data work requests.

import argparse
import ctypes as ct
import os
from pathlib import Path
import resource
import socket
import struct
import sys
import threading
import time


def receive(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise RuntimeError("peer closed during handshake")
        data.extend(part)
    return bytes(data)


def main() -> None:
    if sys.flags.optimize:
        raise RuntimeError(
            "optimized Python cannot run FFI safety validation; "
            "run without -O/-OO or PYTHONOPTIMIZE"
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--gid", type=int, required=True)
    parser.add_argument("--case", required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # The LD_PRELOAD interposer must resolve the CDLL's libibverbs dependency.
    lib = ct.CDLL(str(args.library.resolve()), mode=ct.RTLD_GLOBAL)
    signatures = {
        "new": (ct.c_void_p, [ct.c_char_p, ct.c_uint8, ct.c_uint8, ct.c_uint64]),
        "connect": (ct.c_int, [ct.c_void_p, ct.c_char_p]),
        "free": (None, [ct.c_void_p]),
        "get": (ct.c_int64, [ct.c_void_p, ct.c_char_p]),
        "register_external_buffer": (ct.c_int32, [ct.c_void_p, ct.c_void_p, ct.c_uint64]),
        "unregister_external_buffer": (ct.c_int32, [ct.c_void_p, ct.c_int32]),
        "get_into": (ct.c_int64, [ct.c_void_p, ct.c_int32, ct.c_char_p, ct.c_uint64]),
        "get_descriptor": (
            ct.c_int64,
            [
                ct.c_void_p,
                ct.c_char_p,
                ct.c_char_p,
                ct.c_uint64,
                ct.c_char_p,
                ct.c_uint64,
                ct.c_uint64,
                ct.c_uint8,
                ct.c_uint32,
                ct.c_uint64,
            ],
        ),
        "get_descriptor_into": (
            ct.c_int64,
            [
                ct.c_void_p,
                ct.c_int32,
                ct.c_char_p,
                ct.c_char_p,
                ct.c_uint64,
                ct.c_char_p,
                ct.c_uint64,
                ct.c_uint64,
                ct.c_uint8,
                ct.c_uint32,
                ct.c_uint64,
                ct.c_uint64,
            ],
        ),
        "put": (ct.c_int32, [ct.c_void_p, ct.c_int32, ct.c_char_p, ct.c_uint64, ct.c_uint64]),
    }
    for name, (restype, argtypes) in signatures.items():
        fn = getattr(lib, "cs_rdma_client_" + name)
        fn.restype, fn.argtypes = restype, argtypes
    if args.case == "init_failure":
        assert not lib.cs_rdma_client_new(args.device.encode(), 1, args.gid, 4096)
        path = Path(os.environ["CS_FAIL_VERBS_LOG"])
        events = path.read_text().splitlines() if path.exists() else []
        assert events == ["query_gid", "destroy_cq", "dealloc_pd", "close_device"], events
        print("SAFE_INIT_FAILURE,resources_released=true", flush=True)
        return
    client = lib.cs_rdma_client_new(args.device.encode(), 1, args.gid, 4096)
    assert client, "real Verbs client allocation failed"
    external = ct.create_string_buffer(4096)
    rid = lib.cs_rdma_client_register_external_buffer(client, external, len(external))
    assert rid >= 0, "external MR registration failed"
    if args.case in ("free_mr", "unregister_mr"):
        os.close(2)  # cleanup must abort even if stderr is unusable
        if args.case == "unregister_mr":
            lib.cs_rdma_client_unregister_external_buffer(client, rid)
        else:
            lib.cs_rdma_client_free(client)
        print("UNSAFE_CLEANUP_RETURNED", flush=True)
        return

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    release = threading.Event()
    peer_errors = []

    def peer() -> None:
        try:
            with listener.accept()[0] as sock:
                sock.settimeout(3)
                hello = receive(sock, 25)
                assert hello[0] == 1
                # Echo the locally allocated QP coordinates. No SEND/WRITE is
                # posted: this is a control-path/teardown test, not a data test.
                sock.sendall(b"\xff" if args.case == "connect_error" else hello)
                if args.case in ("free_qp", "connect_error"):
                    release.wait(3)
                    return
                receive(sock, 1)
                if args.case == "timeout":
                    release.wait(3)
                elif args.case == "oversized_reply":
                    sock.sendall(b"\x03" + struct.pack("<BQI", 1, 8192, 1))
                    release.wait(3)
                elif args.case == "short_descriptor":
                    sock.sendall(b"\x03" + struct.pack("<BQI", 1, 2048, 1))
                    release.wait(3)
                else:
                    sock.sendall(b"\xff")
                    release.wait(3)
        except Exception as exc:
            peer_errors.append(str(exc))

    thread = threading.Thread(target=peer, daemon=True)
    thread.start()
    if args.case == "timeout":
        setter = getattr(lib, "cs_rdma_client_set_timeout_ms", None)
        assert setter is not None, "control deadline ABI is absent"
        setter.argtypes = [ct.c_void_p, ct.c_uint64]
        setter.restype = ct.c_int32
        assert setter(client, 100) == 0
    connected = lib.cs_rdma_client_connect(
        client, f"127.0.0.1:{listener.getsockname()[1]}".encode()
    )
    if args.case == "connect_error":
        assert connected < 0
    else:
        assert connected == 0, "loopback QP handshake failed"
        if args.case == "free_qp":
            os.close(2)
            lib.cs_rdma_client_free(client)
            print("UNSAFE_CLEANUP_RETURNED", flush=True)
            return
        identity = [b"1:nkey", b"handle", 1, b"etag", 1, 4096, 1, 1, 4096]
        started = time.monotonic()
        if args.case in ("get_error", "oversized_reply", "timeout"):
            result = lib.cs_rdma_client_get(client, b"1:nkey")
        elif args.case == "get_into_error":
            result = lib.cs_rdma_client_get_into(client, rid, b"1:nkey", 0)
        elif args.case in ("descriptor_error", "short_descriptor"):
            result = lib.cs_rdma_client_get_descriptor(client, *identity)
        elif args.case == "descriptor_into_error":
            result = lib.cs_rdma_client_get_descriptor_into(client, rid, *identity, 0)
        elif args.case == "put_error":
            result = lib.cs_rdma_client_put(client, rid, b"1:nkey", 0, 4096)
        else:
            raise AssertionError(f"unknown case: {args.case}")
        elapsed = time.monotonic() - started
        assert result < 0, f"malformed/failed operation returned success: {result}"
        if args.case == "timeout":
            assert elapsed < 1, f"configured deadline was not bounded: {elapsed}"
    events = (
        Path(os.environ["CS_FAIL_VERBS_LOG"]).read_text().splitlines()
        if Path(os.environ["CS_FAIL_VERBS_LOG"]).exists()
        else []
    )
    assert events == ["destroy_qp"], f"QP was not retired before error return: {events}"
    # A second call must not consume a stale response on the failed connection.
    assert lib.cs_rdma_client_get(client, b"1:nkey") < 0
    lib.cs_rdma_client_free(client)
    release.set()
    thread.join(4)
    listener.close()
    assert not peer_errors, peer_errors
    print(f"SAFE_ERROR_RETURN,case={args.case},qp_retired=true", flush=True)


if __name__ == "__main__":
    main()
