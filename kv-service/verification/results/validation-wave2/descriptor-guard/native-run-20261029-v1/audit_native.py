from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

root = Path(__file__).resolve().parent
ansi = re.compile(r"\x1b\[[0-9;]*m")
expected = bytes(index % 251 for index in range(8192))
(root / "expected-payload.bin").write_bytes(expected)
summary = {
    "state": "PASS_NATIVE_BRANCH_REGRESSION",
    "scope": "Full-object descriptor GET cold-slab fallback retirement guard; native coverage is one 8192-byte unstriped object per variant, not all layouts or performance.",
    "runtime_freeze": json.loads((root / "runtime-freeze.json").read_text()),
    "payload": {
        "bytes": 8192,
        "definition": "byte[index] = index % 251",
        "expected_sha256": hashlib.sha256(expected).hexdigest(),
        "verification": "Frozen native probe compares every byte of RegisteredBuffer against the complete expected payload before and after the controlled failure. expected-payload.bin is the expected vector, not a captured RDMA receive dump.",
    },
    "variants": {},
}
for variant in ("baseline", "candidate"):
    directory = root / variant
    log_path = directory / "server/server.log"
    raw = log_path.read_bytes()
    lines = ansi.sub("", raw.decode()).splitlines()
    (directory / "server/server.clean.log").write_text("\n".join(lines) + "\n")
    client_log = (directory / "client/client.log").read_text()
    assert (directory / "client/client.exit").read_text().strip() == "0"
    client_result = next(json.loads(line) for line in client_log.splitlines() if line.startswith('{'))
    assert client_result["private_target_bytes"] == 8192
    assert all(client_result[name] for name in ("controlled_eof", "target_unchanged_after_server_close", "fresh_exact_read_before", "fresh_exact_read_after"))
    connections = [(index, re.search(r"RDMA client connected: ([^ ]+)", line).group(1)) for index, line in enumerate(lines) if "RDMA client connected: " in line]
    assert len(connections) == 3, connections
    fault_index, fault_peer = connections[1]
    fault_end = connections[2][0]
    window = lines[fault_index:fault_end]
    disconnects = [(index, line) for index, line in enumerate(lines) if f"RDMA client {fault_peer} (nic_idx=0) disconnected:" in line]
    assert len(disconnects) == 1
    disconnect_index, disconnect = disconnects[0]
    assert fault_index < disconnect_index < fault_end
    fallback_lines = [(index, line) for index, line in enumerate(lines) if "RDMA descriptor GET slab path failed, fallback to per-chunk reg_mr:" in line]
    if variant == "baseline":
        assert len(fallback_lines) == 1
        assert fault_index < fallback_lines[0][0] < disconnect_index
        assert "RDMA connection retired after uncertain legacy GET" in fallback_lines[0][1]
        assert "disconnected: poll_n timeout after 300ms, got 0/1" in disconnect
    else:
        assert fallback_lines == [], fallback_lines
        assert "disconnected: RDMA connection retired after uncertain legacy GET: poll final RDMA completions: poll_n timeout after 300ms, got 0/1" in disconnect
    misses = [(index, re.search(r"key=(\S+) bytes=(\d+)", line).groups()) for index, line in enumerate(lines) if "MISS_DETAIL " in line]
    assert len(misses) == 2
    assert misses[0][0] < fault_index < fault_end < misses[1][0]
    assert misses[0][1] == misses[1][1]
    canonical_key, byte_count = misses[0][1]
    assert byte_count == "8192"
    summary["variants"][variant] = {
        "client_exit": 0,
        "client_result": client_result,
        "server_process": json.loads((directory / "server/server-identity.json").read_text()),
        "client_process": json.loads((directory / "client/client-identity.json").read_text()),
        "canonical_key": canonical_key,
        "namespace": "descriptor-guard",
        "object_key": canonical_key.removeprefix("16:descriptor-guard"),
        "fault_key_mapping": "The frozen probe captures and forwards the unchanged descriptor request between its two exact reads using the same descriptor. The only object key observed in this isolated server window is the matching key in both normal-read logs.",
        "peers": {"fresh_before": connections[0][1], "controlled_failure": fault_peer, "fresh_after": connections[2][1]},
        "server_full_log_lines": len(lines),
        "server_full_log_sha256": hashlib.sha256(raw).hexdigest(),
        "fault_window_lines_1based": [fault_index + 1, disconnect_index + 1],
        "fault_window": window,
        "terminal_event": disconnect,
        "fallback_events_in_entire_server_window": len(fallback_lines),
        "source_of_retirement_type": "Server display of existing RetireLegacyGet marker; typed identity additionally covered by the unit regression's error-chain downcast, not inferred from client EOF alone.",
        "server_window_start": (directory / "server/server.start").read_text().strip(),
        "server_window_stop": (directory / "server/server.stop").read_text().strip(),
    }
for role in ("server", "client"):
    post = (root / f"{role}-after.txt").read_text()
    for port in ("55451", "55453", "6396"):
        assert re.search(rf"LISTEN.*:{port}\s", post) is None
    assert "type RC" not in post
    if role == "server":
        assert "958" in post and "1097" in post
        assert "pid 965 comm contextstore-se" in post and "pid 1104 comm contextstore-se" in post
    else:
        assert " mrn " not in post
summary["cleanup"] = {
    "owned_server_redis_probe_processes": "No owned processes remain in post-run process listing.",
    "private_listeners": "No listener remains on 55451, 55453, or 6396.",
    "client_rdma": "No MRs or RC QPs; only rxe0/rxe1 kernel GSI QPs.",
    "server_rdma": "Only pre-existing large/small fixture slab MRs owned by threads 965/1104 and kernel GSI QPs remain.",
    "preexisting_fixture_processes": "PIDs 958 and 1097 plus Redis 953 and 1092 remain running with original command lines.",
}
(root / "native-receipt.json").write_text(json.dumps(summary, indent=2) + "\n")
manifest = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.rglob('*')) if path.is_file() and path.name != 'evidence-sha256.json'}
(root / "evidence-sha256.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"state": summary["state"], "variants": {name: {"fault_peer": item["peers"]["controlled_failure"], "key": item["object_key"], "fallback_events": item["fallback_events_in_entire_server_window"], "server_log_sha256": item["server_full_log_sha256"]} for name, item in summary["variants"].items()}, "expected_payload_sha256": summary['payload']['expected_sha256']}, indent=2))
