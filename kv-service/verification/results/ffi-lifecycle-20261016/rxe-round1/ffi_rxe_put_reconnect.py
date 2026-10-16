"""Real RXE PUT with genuine CQ entries delayed by a test-only observer shim."""

from __future__ import annotations

import argparse
import ctypes as ct
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time


def main() -> None:
    if sys.flags.optimize:
        raise RuntimeError("verification requires Python assertions; optimization is forbidden")
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    events_path = Path(os.environ["CS_CQ_DELAY_LOG"])
    if events_path.exists():
        raise ValueError("refusing to reuse a CQ delay log")
    ct.CDLL(str(args.library.resolve()), mode=ct.RTLD_GLOBAL)
    os.environ["CONTEXTSTORE_RDMA_LIB"] = str(args.library.resolve())
    spec = importlib.util.spec_from_file_location("rxe_rdma_client", args.module)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    launch = {
        "library_sha256": hashlib.sha256(args.library.read_bytes()).hexdigest(),
        "module_sha256": hashlib.sha256(args.module.read_bytes()).hexdigest(),
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "shim_sha256": hashlib.sha256(Path(os.environ["LD_PRELOAD"]).read_bytes()).hexdigest(),
        "scope": "real RXE RDMA WRITE and real CQ entries; test-only shim hides CQ polling until QP destruction, no synthetic WC or provider return code",
        "python_optimize": sys.flags.optimize,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".launch.json").write_text(json.dumps(launch, indent=2) + "\n")
    size = 1024 * 1024
    expected = bytes(range(251)) * (size // 251) + bytes(range(size % 251))
    target = ct.create_string_buffer(expected, size)
    client = module.RdmaClient(args.server, device=args.device, gid_index=1, buf_size_mb=1)
    client.connect()
    handle = client._handle
    rid = client.register_external_buffer(ct.addressof(target), size)
    key = "3:ffiput-cq-" + str(time.time_ns())
    started = time.monotonic_ns()
    try:
        client.put(rid, key, 0, size)
    except RuntimeError as exc:
        error = str(exc)
    else:
        raise AssertionError("hidden PUT completion did not cause a timeout")
    elapsed = (time.monotonic_ns() - started) / 1e9
    assert 30 <= elapsed < 40, elapsed
    assert not client._connected
    events_at_return = events_path.read_text().splitlines()
    assert events_at_return[0:2] == ["cq_delay_installed", "qp_destroyed_reveal_real_cq"]
    drained = [e for e in events_at_return[2:] if e.startswith("real_completion,")]
    assert len(drained) == 1 and drained[0].endswith("status=0"), events_at_return
    old_qp = int(drained[0].split("qp=")[1].split(",")[0])
    # Reuse exactly the same still-registered allocation after the failed PUT.
    ct.memset(ct.addressof(target), 0x5A, size)
    client.connect()
    assert client._handle == handle
    for index in range(3):
        ct.memmove(ct.addressof(target), expected, size)
        recovery_key = key + "-recovery-" + str(index)
        client.put(rid, recovery_key, 0, size)
        ct.memset(ct.addressof(target), 0xA5, size)
        assert client.get_into(rid, recovery_key) == size
        assert target.raw == expected
    events_after_recovery = events_path.read_text().splitlines()
    completions = [e for e in events_after_recovery if e.startswith("real_completion,")]
    assert len(completions) == 4, completions
    # RXE may immediately reuse a destroyed QPN. The ordering witness is the
    # captured old WC before error return, followed by three newly posted PUTs.
    assert all(e.endswith("status=0") for e in completions[1:]), completions
    client.unregister_external_buffer(rid)
    client.close()
    result = {**launch, "elapsed_seconds": elapsed, "error": error,
              "old_qp_real_completion_drained_before_error_return": True,
              "retired_qp_number": old_qp,
              "same_native_handle": handle, "same_registered_buffer_reused": True,
              "reconnect_put_get_full_content_rounds": 3,
              "payload_sha256": hashlib.sha256(expected).hexdigest(),
              "events": events_path.read_text().splitlines(), "passed": True}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
