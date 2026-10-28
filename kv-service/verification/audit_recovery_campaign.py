#!/usr/bin/env python3
"""Join native recovery request receipts with exact server-side terminal evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re


def read_text(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as stream:
            return stream.read()
    return path.read_text()


def frame_data(frame: str) -> dict[str, object]:
    wire_text = frame.split(" wire=[", 1)[1].split("]", 1)[0]
    wire = bytes(int(part.strip(), 16) for part in wire_text.split(","))
    assert wire[0] == 15, "only a real descriptor-SGE request is valid"
    offset = 1

    def number(length: int) -> int:
        nonlocal offset
        value = int.from_bytes(wire[offset : offset + length], "little")
        offset += length
        return value

    def string() -> str:
        nonlocal offset
        length = number(2)
        value = wire[offset : offset + length].decode()
        offset += length
        return value

    identity = {"key": string(), "handle": string(), "generation": number(8), "etag": string(),
                "layout": number(8), "size": number(8), "striped": number(1), "stripe_count": number(4), "chunk_size": number(8)}
    number(8); number(4); number(8)
    stripes = [number(4) for _ in range(number(2))]
    segments = [(number(8), number(4), number(8)) for _ in range(number(2))]
    assert offset == len(wire), "captured frame must parse completely"
    return {"identity": identity, "stripes": stripes, "segments": segments,
            "lane": int(re.search(r"\blane=(\d+)", frame)[1]),
            "sequence": int(re.search(r"\bsequence=(\d+)", frame)[1]),
            "terminal": re.search(r"\bterminal=(\S+)", frame)[1]}


def fields(row: str) -> dict[str, str]:
    return dict(re.findall(r"\b([a-z_]+)=([^ ]+)", row))


def verify_frozen_matrix(rows: list[str], frame_lines: list[str], frozen: dict[str, object], name: str, client: str) -> dict[str, object]:
    run = next(entry for entry in frozen["runs"] if entry["name"] == name)
    kind = run["test"]
    seed = int(run["CS_RECOVERY_SEED"])
    expected_size = int(run.get("CS_RAIL_OBJECT_MIB", 1)) * 1024 * 1024
    by_op: dict[int, list[dict[str, object]]] = {}
    for line in frame_lines:
        op = int(re.search(r"\bop=(\d+)", line)[1])
        by_op.setdefault(op, []).append(frame_data(line))
    actual_matrix = Counter()
    expected_matrix = Counter()
    frame_total = late_total = ack_total = 0
    keys: dict[str, str] = {}
    for row in rows:
        value = fields(row)
        op = int(value["op"])
        assert int(value["seed"]) == seed
        direct, pooled = value["direct"], value["pooled"]
        failed = value.get("failed_lane", "-")
        control = value.get("control", "-")
        cut = value.get("cut", "-")
        close = value.get("close_each", "-")
        capability = value.get("capability_control_injection", "-")
        actual_matrix[(direct, pooled, failed, control, cut, close, capability)] += 1
        frames = by_op.get(op, [])
        if kind == "native_recovery_control_campaign":
            needed = {"F0": 2, "F1": 4, "F2": 3}[control]
            late, ack = 1, 0
            expected_bytes = {"F0": 0, "F1": expected_size, "F2": expected_size // 2}[control]
            assert int(value["expected_retry"]) == int(value["observed_retry"]) == expected_bytes
            assert value["terminal"] == ("expected_error" if control == "F0" else "exact_publication")
            assert value["manual_rearm"] == "true"
            assert keys.setdefault(value["key"], value["expected_hash"]) == value["expected_hash"]
        elif kind == "native_recovery_cut_campaign":
            needed = 2 if cut in {"CancelInitial", "DeadlineRetryGate"} else 3
            late = int(cut in {"CancelRetry", "Overwrite", "Delete"})
            ack = int(cut in {"AckTimeout", "DeadlineRetryGate"})
            retries = int(cut not in {"CancelInitial", "DeadlineRetryGate"})
            assert int(value["retry_tasks"]) == retries and int(value["retry_bytes"]) == retries * expected_size // 2
            assert value["terminal"] == ("exact_publication_after_ack_timeout" if cut == "AckTimeout" else "expected_atomic_rejection")
            if cut in {"Overwrite", "Delete"}:
                mutations = [line for line in client.splitlines() if line.startswith(f"RECOVERY_MUTATION op={op} ")]
                assert len(mutations) == 1, "missing remote-effect receipt"
                mutation = fields(mutations[0])
                if cut == "Overwrite":
                    assert int(mutation["new_generation"]) > int(mutation["old_generation"])
                    assert mutation["version_b_hash"] != value["initial_hash"]
                    assert mutation["observed_before_retry_ack_release"] == "true"
                else:
                    assert mutation["observed_deleted_before_retry_ack_release"] == "true"
        elif kind == "native_recovery_reader_lifetime_campaign":
            needed, late, ack = 2, 0, 0
            assert value["exact_payload"] == "true"
            assert "created: 2," in row and "live: 2," in row and "idle: 2," in row and "retired: 0," in row
            if close == "true": assert "reused: 0," in row
        elif kind == "native_recovery_publication_gate_campaign":
            needed, late, ack = 3, 1, 0
            assert value["actual_postlookup_response_intercepted"] == "true"
            assert value["sentinel_unchanged"] == value["server_identity_unchanged"] == "true"
            assert ("terminal=object version changed during rail read" in row) if capability == "true" else ("terminal=rail read deadline exceeded" in row)
        elif kind == "native_recovery_admission_campaign":
            needed = late = int(cut != "registered_byte_cap")
            ack = 0
            assert value["retry_tasks"] == "0" and value["sentinel_unchanged"] == "true"
        else: raise AssertionError(f"unknown frozen suite {kind}")
        assert len(frames) == needed, (op, "missing native frame", len(frames), needed)
        assert sum(frame["terminal"] == "eof_after_late_request" for frame in frames) == late
        assert sum(frame["terminal"] == "success_ack_held_until_client_timeout" for frame in frames) == ack
        frame_total += needed; late_total += late; ack_total += ack
        if frames:
            identity = frames[0]["identity"]
            assert identity["size"] == expected_size and identity["stripe_count"] == 4 and identity["striped"] == 1
            assert identity["chunk_size"] == expected_size // 4
            assert all(frame["identity"] == identity for frame in frames), "mixed frozen object identity"
            if "key" in value: assert identity["key"] == "17:recovery-campaign" + value["key"]
            initial = [frame for frame in frames if frame["sequence"] == 1]
            assert sorted(stripe for frame in initial for stripe in frame["stripes"]) == [0, 1, 2, 3]
            retries = [frame for frame in frames if frame["sequence"] > 1]
            if control == "F1":
                assert sorted(stripe for frame in retries for stripe in frame["stripes"]) == [0, 1, 2, 3]
            elif retries:
                old = next(frame for frame in initial if frame["lane"] == int(failed))
                assert len(retries) == 1 and retries[0]["stripes"] == old["stripes"]
                if direct == "true":
                    for stripe in old["stripes"]: assert retries[0]["segments"][stripe][0] == old["segments"][stripe][0]
    if kind == "native_recovery_control_campaign":
        controls = run.get("CS_RECOVERY_CONTROLS", "F0,F1,F2").split(",")
        per = int(run["CS_RECOVERY_OPS"]) // (4 * len(controls))
        for direct in ["false", "true"]:
            for pooled in ["false", "true"]:
                for control in controls:
                    for index in range(per): expected_matrix[(direct, pooled, str((index + seed) % 2), control, "-", "-", "-")] += 1
        expected_key_count = min(4, per)
        assert len(keys) == len(set(keys.values())) == expected_key_count, "fixed distinct object set not exercised exactly"
    elif kind == "native_recovery_cut_campaign":
        for direct in ["false", "true"]:
            for pooled in ["false", "true"]:
                for cut in ["AckTimeout", "CancelInitial", "CancelRetry", "DeadlineRetryGate", "Overwrite", "Delete"]:
                    for index in range(int(run["CS_RECOVERY_CUT_REPEATS"])): expected_matrix[(direct, pooled, str((index+seed)%2), "-", cut, "-", "-")] += 1
    elif kind == "native_recovery_reader_lifetime_campaign":
        for close in ["false", "true"]:
            for direct in ["false", "true"]: expected_matrix[(direct, "true", "-", "-", "-", close, "-")] = int(run["CS_LIFETIME_OPS"]) // 2
    elif kind == "native_recovery_publication_gate_campaign":
        for direct in ["false", "true"]:
            for pooled in ["false", "true"]:
                for capability in ["false", "true"]:
                    for index in range(int(run["CS_PUBLICATION_REPEATS"])): expected_matrix[(direct, pooled, str((index+seed)%2), "-", "-", "-", capability)] += 1
    else:
        for direct in ["false", "true"]:
            for pooled in ["false", "true"]:
                for failed in ["0", "1"]:
                    for cut in ["single_available_path", "unsupported_alternate_owner", "registered_byte_cap"]: expected_matrix[(direct, pooled, failed, "-", cut, "-", "-")] = 1
    assert actual_matrix == expected_matrix, ("frozen matrix mismatch", actual_matrix, expected_matrix)
    assert set(by_op).issubset({int(fields(row)["op"]) for row in rows}), "orphan native frame"
    assert len(frame_lines) == frame_total, "extra or missing native frames"
    assert sum(frame["terminal"] == "eof_after_late_request" for frames in by_op.values() for frame in frames) == late_total
    assert sum(frame["terminal"] == "success_ack_held_until_client_timeout" for frames in by_op.values() for frame in frames) == ack_total
    return {"frozen_run": name, "full_matrix_verified": True, "expected_frames": frame_total,
            "expected_late_eof": late_total, "expected_held_success_ack": ack_total, "fixed_control_key_hashes": keys}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True, type=Path)
    parser.add_argument("--server", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-operations", required=True, type=int)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--name")
    args = parser.parse_args()
    client = read_text(args.client)
    server_text = re.sub(r"\x1b\[[0-9;]*m", "", read_text(args.server))
    server = server_text.splitlines()
    assert "test result: ok." in client, "campaign did not complete successfully"
    assert "test result: FAILED" not in client and "panicked at" not in client
    row_prefixes = (
        "RECOVERY_REQUEST ", "RECOVERY_CUT ", "RECOVERY_LIFETIME ",
        "RECOVERY_PUBLICATION_GATE ", "RECOVERY_ADMISSION ",
    )
    rows = [line for line in client.splitlines() if line.startswith(row_prefixes)]
    assert len(rows) == args.expected_operations, (len(rows), args.expected_operations)
    ids = [int(re.search(r"\bop=(\d+)", row)[1]) for row in rows]
    assert sorted(ids) == list(range(args.expected_operations)), "missing/duplicate operation ID"
    frame_lines = [line for line in client.splitlines() if line.startswith("RECOVERY_FRAME ")]
    late = [line for line in frame_lines if "terminal=eof_after_late_request " in line]
    completed_ack = [line for line in frame_lines if "terminal=success_ack_held_until_client_timeout " in line]
    proof = []
    used_terminals: set[int] = set()
    for frame in late:
        peer = re.search(r"\bpeer=(\S+)", frame)[1]
        key = frame_data(frame)["identity"]["key"]
        matches = []
        for index, line in enumerate(server):
            if f"RDMA client {peer} " not in line or "connection retired after uncertain stripe-subset completion" not in line:
                continue
            prior = server[max(0, index - 1) : index]
            failures = [entry for entry in prior if f"key={key} " in entry and "stripe-subset stream failed" in entry and "poll" in entry]
            if failures:
                matches.append((index, line, failures[-1]))
        assert len(matches) == 1, ("missing/ambiguous exact-peer WR retirement", peer, key, matches)
        index, terminal, failure = matches[0]
        assert index not in used_terminals, "server terminal was counted twice"
        used_terminals.add(index)
        proof.append({
            "op": int(re.search(r"\bop=(\d+)", frame)[1]),
            "canonical_key": key, "exact_peer": peer,
            "server_terminal_line": index + 1, "server_wr_failure": failure,
            "server_terminal": terminal,
        })
    modes = Counter()
    for row in rows:
        direct = re.search(r"\bdirect=(\S+)", row)[1]
        pooled = re.search(r"\bpooled=(\S+)", row)[1]
        control = re.search(r"\bcontrol=(\S+)", row)
        cut = re.search(r"\bcut=(\S+)", row)
        modes[f"direct={direct},pooled={pooled},control={control[1] if control else '-'},cut={cut[1] if cut else '-'}"] += 1
    frozen_matrix = verify_frozen_matrix(rows, frame_lines, json.loads(args.freeze.read_text()), args.name, client) if args.freeze else {"full_matrix_verified": False, "reason": "no frozen run manifest supplied"}
    result = {
        "matrix_validation": frozen_matrix,
        "client_log": str(args.client), "client_sha256": hashlib.sha256(args.client.read_bytes()).hexdigest(),
        "server_log": str(args.server), "server_sha256": hashlib.sha256(args.server.read_bytes()).hexdigest(),
        "operations": len(rows), "mode_counts": dict(modes), "native_frames": len(frame_lines),
        "late_eof_requests": len(late), "exact_peer_and_key_wr_retirements": len(proof),
        "server_success_ack_held_until_client_timeout": len(completed_ack),
        "server_not_found_used_as_late_write_proof": 0, "proof": proof,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in {"proof", "mode_counts"}}))


if __name__ == "__main__":
    main()
