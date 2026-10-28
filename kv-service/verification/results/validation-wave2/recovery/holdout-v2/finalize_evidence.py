#!/usr/bin/env python3
"""Offline final readback; preserves frozen-auditor RED and adds ordered bijection evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CLIENT = ROOT / "collected/client"
SERVER = ROOT / "collected/server"
OUT = ROOT / "audits"
OUT.mkdir(exist_ok=True)
FROZEN = json.loads((CLIENT / "freeze.json").read_text())
COUNTS = {"f2-recovery1000": 1000, "matched-controls240": 240, "cuts480": 480, "lifetime2000": 2000, "publication160": 160, "admission24": 24, "representative16mib4": 4}
PREFIXES = ("RECOVERY_REQUEST ", "RECOVERY_CUT ", "RECOVERY_LIFETIME ", "RECOVERY_PUBLICATION_GATE ", "RECOVERY_ADMISSION ")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, ZoneInfo("Asia/Shanghai")).isoformat()


def numbers(records: list[dict[str, object]], key: str) -> list[int]:
    return [int(str(row[key]).split()[0]) for row in records if str(row.get(key, "")).split() and str(row[key]).split()[0].isdigit()]


def decode_json_sequence(text: str) -> list[object]:
    decoder = json.JSONDecoder()
    values = []
    while text.strip():
        text = text.lstrip()
        value, length = decoder.raw_decode(text)
        values.append(value)
        text = text[length:]
    return values


assert (CLIENT / "freeze.json").read_bytes() == (ROOT / "freeze.json").read_bytes()
assert sha(CLIENT / "recovery-test") == FROZEN["binary_sha256"]
assert sha(CLIENT / "run_recovery_campaign.py") == FROZEN["runner_sha256"]
assert sha(CLIENT / "audit_recovery_campaign.py") == FROZEN["audit_sha256"]
for source, expected_hash in FROZEN["source_hashes"].items():
    snapshot = ROOT / Path(source).name
    if snapshot.exists():
        assert sha(snapshot) == expected_hash, source
cutoffs = json.loads((SERVER / "server-cutoffs.json").read_text())
for name, cutoff in cutoffs.items():
    data = (SERVER / name).read_bytes()
    assert len(data) == cutoff["bytes"] and len(data.splitlines()) == cutoff["end_line"] and sha(SERVER / name) == cutoff["sha256"]

spec = importlib.util.spec_from_file_location("frozen_auditor", CLIENT / "audit_recovery_campaign.py")
assert spec and spec.loader
frozen_auditor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frozen_auditor)

suite_receipts = []
exits = []
all_rows = []
frozen_results = []
for run in FROZEN["runs"]:
    name = run["name"]
    folder = CLIENT / name
    exit_record = json.loads((folder / "exit.json").read_text())
    launch = json.loads((folder / "launch.json").read_text())
    raw = (folder / "run.log").read_bytes()
    assert gzip.decompress((folder / "run.log.gz").read_bytes()) == raw, name
    text = raw.decode()
    assert exit_record["returncode"] == exit_record["native_returncode"] == 0 and exit_record["state"] == "FINISHED", name
    assert exit_record["within_process_resource_limits"] is True
    assert len(raw) == exit_record["raw_log_bytes"] and (folder / "run.log.gz").stat().st_size == exit_record["gzip_log_bytes"]
    assert exit_record["gzip_log_bytes"] < FROZEN["process_resource_limits"]["compressed_log_limit_bytes"]
    assert "test result: ok." in text and "panicked at" not in text and "test result: FAILED" not in text
    assert launch["command"][0] == "/home/railtest/cswave1/wave2/recovery-holdout-v2/recovery-test"
    assert launch["command"][-1] == "rail_read::tests::recovery_campaign::" + run["test"]
    for key, value in run.items():
        if key.startswith("CS_"):
            assert launch["environment"][key] == str(value), (name, key)
    assert exit_record["run_timeout_seconds"] == run["CS_RECOVERY_RUN_TIMEOUT_SECS"]
    assert exit_record["elapsed_seconds"] < exit_record["effective_budget_seconds"] <= exit_record["run_timeout_seconds"]
    end = launch["started_unix_seconds"] + exit_record["elapsed_seconds"]
    assert end < exit_record["campaign_deadline_unix_seconds"]
    resources = [json.loads(line) for line in (folder / "resources.jsonl").read_text().splitlines()]
    assert len(resources) == exit_record["resource_sample_count"] + 1
    samples = resources[:-1]
    pid = int((folder / "pid").read_text())
    assert all(row["pid"] == pid for row in resources)
    intervals = [right["monotonic_seconds"] - left["monotonic_seconds"] for left, right in zip(samples, samples[1:])]
    assert all(value >= 0 for value in intervals)
    peaks = {"rss_kib": max(numbers(samples, "VmRSS"), default=0), "file_descriptors": max(numbers(samples, "file_descriptors"), default=0), "threads": max(numbers(samples, "Threads"), default=0)}
    limits = {"rss_kib": int(run["CS_RECOVERY_RSS_MAX_KIB"]), "file_descriptors": 128, "threads": 64}
    assert peaks == exit_record["process_resource_peaks"] and limits == exit_record["process_resource_limits"]
    assert all(peaks[key] <= value for key, value in limits.items())
    rows = [line for line in text.splitlines() if line.startswith(PREFIXES)]
    frames = [line for line in text.splitlines() if line.startswith("RECOVERY_FRAME ")]
    assert len(rows) == COUNTS[name]
    assert sorted(int(frozen_auditor.fields(row)["op"]) for row in rows) == list(range(COUNTS[name]))
    matrix = frozen_auditor.verify_frozen_matrix(rows, frames, FROZEN, name, text)
    server_path = SERVER / ("large-server.log" if name == "representative16mib4" else "small-server.log")
    command = ["python3", str(CLIENT / "audit_recovery_campaign.py"), "--client", str(folder / "run.log.gz"), "--server", str(server_path), "--expected-operations", str(COUNTS[name]), "--freeze", str(CLIENT / "freeze.json"), "--name", name, "--output", str(OUT / (name + ".json"))]
    frozen_result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (OUT / (name + "-frozen-audit.log")).write_text(frozen_result.stdout)
    frozen_results.append({"suite": name, "returncode": frozen_result.returncode, "log": name + "-frozen-audit.log"})
    if frozen_result.returncode:
        assert name == "f2-recovery1000" and "missing/ambiguous exact-peer WR retirement" in frozen_result.stdout, "unexpected frozen auditor failure"

    # Independent supplementary evidence, not a rewritten frozen auditor. The
    # source joins each old terminal before beginning its next operation. Thus
    # rank within the complete serial old-request stream supplies an occurrence
    # identity when a TCP source port is reused many minutes later.
    old_frames = [line for line in frames if "terminal=eof_after_late_request " in line]
    expected = []
    for frame in old_frames:
        data = frozen_auditor.frame_data(frame)
        expected.append({"op": int(re.search(r"\bop=(\d+)", frame)[1]), "canonical_key": data["identity"]["key"], "peer": re.search(r"\bpeer=(\S+)", frame)[1]})
    assert [entry["op"] for entry in expected] == sorted(set(entry["op"] for entry in expected))
    keys = {entry["canonical_key"] for entry in expected}
    server_lines = re.sub(r"\x1b\[[0-9;]*m", "", server_path.read_text()).splitlines()
    observed = []
    for index, line in enumerate(server_lines):
        match = re.search(r"RDMA client (\S+) \(nic_idx=(\d+)\) disconnected: RDMA connection retired after uncertain stripe-subset completion:", line)
        if not match or index == 0:
            continue
        failure = server_lines[index - 1]
        key_match = re.search(r"stripe-subset stream failed without deleting object metadata key=(\S+) error=(.*)", failure)
        if not key_match or key_match[1] not in keys:
            continue
        assert "poll final RDMA completions" in key_match[2]
        timestamp = datetime.fromisoformat(line.split()[0].replace("Z", "+00:00")).timestamp()
        observed.append({"canonical_key": key_match[1], "peer": match[1], "server_terminal_line": index + 1, "server_failure_line": index, "unix_seconds": timestamp, "wr_failure": failure, "terminal": line})
    assert len(observed) == len(expected) == matrix["expected_late_eof"], (name, len(observed), len(expected))
    assert all(left["unix_seconds"] < right["unix_seconds"] for left, right in zip(observed, observed[1:]))
    proof = []
    for occurrence, (wanted, actual) in enumerate(zip(expected, observed)):
        assert (wanted["canonical_key"], wanted["peer"]) == (actual["canonical_key"], actual["peer"]), (name, occurrence, wanted, actual)
        assert launch["started_unix_seconds"] <= actual["unix_seconds"] <= end
        proof.append({"serial_occurrence": occurrence, **wanted, **actual})
    duplicates = defaultdict(list)
    for row in proof:
        duplicates[(row["canonical_key"], row["peer"])].append({"op": row["op"], "server_terminal_line": row["server_terminal_line"], "unix_seconds": row["unix_seconds"]})
    reused = [{"canonical_key": key[0], "peer": key[1], "occurrences": values} for key, values in duplicates.items() if len(values) > 1]
    terminal_receipt = {"status": "LOCAL_BIJECTION_PASS_PENDING_INDEPENDENT_REVIEW", "suite": name, "frozen_auditor_returncode": frozen_result.returncode, "method": "complete serial old-frame sequence paired by occurrence rank with complete relevant server terminal sequence; every pair must match exact canonical key and peer; strict adjacent WR failure; no reused event; no omissions or extras", "expected_old_requests": len(expected), "matched_unique_server_terminals": len(proof), "reused_key_peer_groups": reused, "proof": proof}
    (OUT / (name + "-ordered-terminal-bijection.json")).write_text(json.dumps(terminal_receipt, indent=2) + "\n")
    (OUT / (name + "-matrix.json")).write_text(json.dumps(matrix, indent=2) + "\n")
    exact_publications = 0
    for row in rows:
        data = frozen_auditor.fields(row)
        if row.startswith("RECOVERY_REQUEST "):
            exact_publications += data["terminal"] == "exact_publication"
        elif row.startswith("RECOVERY_CUT "):
            exact_publications += data["terminal"] == "exact_publication_after_ack_timeout"
        elif row.startswith("RECOVERY_LIFETIME "):
            exact_publications += data["exact_payload"] == "true"
    receipt = {"suite": name, "operations": len(rows), "exact_publications": exact_publications, "expected_atomic_rejections": len(rows) - exact_publications, "native_frames": len(frames), "late_write_terminal_pairs": len(proof), "held_success_ack_timeouts": matrix["expected_held_success_ack"], "frozen_auditor_returncode": frozen_result.returncode, "ordered_terminal_bijection_pass": True, "reused_key_peer_group_count": len(reused), "full_matrix_pass": True, "seed": run["CS_RECOVERY_SEED"], "object_mib": run["CS_RAIL_OBJECT_MIB"], "started_china": iso(launch["started_unix_seconds"]), "finished_china": iso(end), "started_unix_seconds": launch["started_unix_seconds"], "finished_unix_seconds": end, "elapsed_seconds_including_fault_gates": exit_record["elapsed_seconds"], "watchdog_limit_seconds": exit_record["run_timeout_seconds"], "watchdog_pass": True, "resource_samples": len(samples), "sample_interval_min_seconds": min(intervals) if intervals else None, "sample_interval_max_seconds": max(intervals) if intervals else None, "sampled_resource_peaks": peaks, "observed_kernel_hwm_kib": max(numbers(samples, "VmHWM"), default=0), "resource_limits": limits, "resources_within_limits": True, "raw_log_bytes": len(raw), "gzip_log_bytes": exit_record["gzip_log_bytes"]}
    suite_receipts.append(receipt)
    exits.append(exit_record)
    all_rows.extend(rows)

assert decode_json_sequence((CLIENT / "launcher.log").read_text()) == exits, "launcher did not return all seven successful runner receipts in frozen order"
quiescence = json.loads((CLIENT / "guest-quiescence.json").read_text())
assert quiescence["owned_process_count"] == 0 and not quiescence["owned_processes_remaining"]
assert quiescence["tmux_has_session_returncode"] != 0 and not quiescence["rdma_mr_readback"].strip()
assert len(quiescence["rdma_qp_readback"].splitlines()) == 2 and all("type GSI" in line for line in quiescence["rdma_qp_readback"].splitlines())
assert sum(row["operations"] for row in suite_receipts) == 3908
assert sum(row["exact_publications"] for row in suite_receipts) == 3244
assert sum(row["expected_atomic_rejections"] for row in suite_receipts) == 664
assert sum(row["native_frames"] for row in suite_receipts) == 9508
assert sum(row["late_write_terminal_pairs"] for row in suite_receipts) == 1660
assert sum(row["held_success_ack_timeouts"] for row in suite_receipts) == 160
campaign_deadlines = {record["campaign_deadline_unix_seconds"] for record in exits}
assert len(campaign_deadlines) == 1
campaign_deadline = campaign_deadlines.pop()
campaign_start = campaign_deadline - FROZEN["watchdog"]["campaign_timeout_seconds"]
campaign_finish = max(row["finished_unix_seconds"] for row in suite_receipts)
assert campaign_finish < campaign_deadline
controls = Counter()
for row in all_rows:
    if row.startswith("RECOVERY_REQUEST ") and "seed=2026102936 " in row:
        value = frozen_auditor.fields(row)
        controls[value["control"]] += int(value["observed_retry"])
assert controls == Counter({"F0": 0, "F1": 80 * 1048576, "F2": 80 * 524288})
summary = {"status": "LOCAL_VALIDATION_PASS_PENDING_INDEPENDENT_REVIEW", "formal_run_only": True, "development_smoke_and_old_first100_excluded": True, "core_revision": FROZEN["core_revision"], "test_binary_sha256": FROZEN["binary_sha256"], "freeze_sha256": sha(CLIENT / "freeze.json"), "runner_sha256": FROZEN["runner_sha256"], "frozen_auditor_sha256": FROZEN["audit_sha256"], "frozen_auditor_passed_suites": 6, "frozen_auditor_failed_suites": 1, "unresolved_acceptance_note": "F2 frozen auditor RED is preserved: the same TCP source port plus key recurs minutes later. An independent complete serial-order bijection matches all 1000 exact key/peer occurrences without event reuse; independent review must accept this supplementary evidence before final PASS.", "formal_operations": 3908, "exact_publications": 3244, "expected_atomic_rejections": 664, "incorrect_publications_observed": 0, "unexpected_native_outcomes_observed": 0, "native_frames": 9508, "late_write_terminal_pairs": 1660, "held_server_success_ack_timeout_cuts": 160, "whole_campaign_start_china": iso(campaign_start), "whole_campaign_finish_china": iso(campaign_finish), "whole_campaign_elapsed_seconds": campaign_finish - campaign_start, "whole_campaign_budget_seconds": 5400, "whole_campaign_watchdog_pass": True, "matched_controls": {"operations_per_control": 80, "payload_bytes_per_object": 1048576, "F0_retry_payload_bytes": controls["F0"], "F1_retry_payload_bytes": controls["F1"], "F2_retry_payload_bytes": controls["F2"], "F1_retry_payload_per_operation": 1048576, "F2_retry_payload_per_operation": 524288, "F2_requested_retry_payload_reduction": 0.5, "claim_boundary": "requested retry payload only; F1 repeats two frozen tasks, F2 retries one failed task; no wire-byte, latency or throughput improvement claim"}, "f2_holdout_distribution": "1000 total, four staging/pool modes at 250 each, each failed lane at 125 per mode", "lifetime_distribution": {"healthy_reader_reuse_operations": 1000, "startup_reads_in_healthy_group": 2, "warm_reader_reads": 998, "warm_connection_lease_reuses": 1996, "reader_close_recreate_operations": 1000}, "resource_measurement_boundary": "per-request logical budgets and pool state asserted by frozen native tests; process RSS/FD/threads sampled around 1 s, not continuous peak instrumentation; VmHWM values are sampled kernel high-water readings", "small_fixture_sampled_peaks": {key: max(row["sampled_resource_peaks"][key] for row in suite_receipts if row["object_mib"] == 1) for key in ["rss_kib", "file_descriptors", "threads"]}, "large_fixture_sampled_peaks": next(row["sampled_resource_peaks"] for row in suite_receipts if row["object_mib"] == 16), "guest_owned_processes_remaining": 0, "guest_memory_registrations_remaining": 0, "server_log_cutoffs": cutoffs, "capability_case_scope": "80 production publication rejections after valid capability response injection by test-only coordinator relay; not a physical capability reconfiguration", "not_claimed": ["runtime automatic health rearm; campaign explicitly re-enables both routes", "MR cache reuse", "hardware multi-rail throughput", "end-to-end model/inference improvement", "native server restart coverage in this campaign"], "suites": suite_receipts}
(ROOT / "local-validation-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
(ROOT / "audits/frozen-audit-results.json").write_text(json.dumps(frozen_results, indent=2) + "\n")
with (ROOT / "ppt-recovery-table.csv").open("w", newline="") as stream:
    columns = ["suite", "operations", "exact_publications", "expected_atomic_rejections", "native_frames", "late_write_terminal_pairs", "frozen_auditor_returncode", "full_matrix_pass", "ordered_terminal_bijection_pass", "seed", "object_mib", "elapsed_seconds_including_fault_gates", "resource_samples"]
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows({key: row[key] for key in columns} for row in suite_receipts)
manifest = {str(path.relative_to(ROOT)): {"bytes": path.stat().st_size, "sha256": sha(path)} for path in sorted((ROOT / "collected").rglob("*")) if path.is_file()}
(ROOT / "collected-evidence-sha256.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"status": summary["status"], "operations": 3908, "exact_publications": 3244, "expected_atomic_rejections": 664, "native_frames": 9508, "late_write_terminal_pairs": 1660, "frozen_auditor_failed_suites": 1, "collected_files": len(manifest)}, indent=2))
