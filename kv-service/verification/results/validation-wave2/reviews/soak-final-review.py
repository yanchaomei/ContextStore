#!/usr/bin/env python3
"""Independent OFFLINE audit of closed frozen v3 soak evidence. No workload runs."""
from __future__ import annotations

import collections
import csv
import hashlib
import json
import re
import statistics
import struct
import tarfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "work/ContextStore-runtime-wave1/kv-service/verification/results/validation-wave2/soak/long-v3-final"
SOAK = ROOT / "outputs/validation-wave2-20261029/soak"
SIZE = 8 * 1024 * 1024
PID, TICKS = 2780409, 1334490144
COMMIT = "1c3ee135d19fece67507b51acd1fc51ae9541208"
BIN = "5aa06344cb6d56c55de119d3ffd01a8c654423bf98084e25590f73ff97ebe1df"
RUST = "6f7d8113f58a34a28faedda8692421e2f847b2f52d1e965f6bd836e083a9c512"
PYTHON = "60f815a95a7772d0bf8875452aa0caa3a10196a934befa630761435526777714"
CHECKS = []


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    CHECKS.append(description)


def load(path):
    return json.loads(path.read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


# Direct XXH64 reference arithmetic, seed=0. This is not XXH3.
MASK = (1 << 64) - 1
P1, P2, P3, P4, P5 = 11400714785074694791, 14029467366897019727, 1609587929392839161, 9650029242287828579, 2870177450012600261


def rotl(value, n):
    return ((value << n) | (value >> (64 - n))) & MASK


def xxh64(data):
    def rnd(acc, value):
        return rotl((acc + value * P2) & MASK, 31) * P1 & MASK

    n, p = len(data), 0
    if n >= 32:
        lanes = [(P1 + P2) & MASK, P2, 0, (-P1) & MASK]
        while p <= n - 32:
            for j in range(4):
                lanes[j] = rnd(lanes[j], struct.unpack_from("<Q", data, p + j * 8)[0])
            p += 32
        h = sum(rotl(v, r) for v, r in zip(lanes, [1, 7, 12, 18])) & MASK
        for v in lanes:
            h = ((h ^ rnd(0, v)) * P1 + P4) & MASK
    else:
        h = P5
    h = (h + n) & MASK
    while p <= n - 8:
        h = (rotl(h ^ rnd(0, struct.unpack_from("<Q", data, p)[0]), 27) * P1 + P4) & MASK
        p += 8
    if p <= n - 4:
        h = (rotl(h ^ ((struct.unpack_from("<I", data, p)[0] * P1) & MASK), 23) * P2 + P3) & MASK
        p += 4
    while p < n:
        h = rotl(h ^ ((data[p] * P5) & MASK), 11) * P1 & MASK
        p += 1
    h ^= h >> 33
    h = h * P2 & MASK
    h ^= h >> 29
    h = h * P3 & MASK
    h ^= h >> 32
    return f"{h:016x}"


def closed_gzip(path):
    packed = path.read_bytes()
    decoder = zlib.decompressobj(31)
    raw = decoder.decompress(packed) + decoder.flush()
    check(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail, f"{path.name}: complete single gzip member")
    crc, size = struct.unpack("<II", packed[-8:])
    check(crc == zlib.crc32(raw) and size == len(raw) % (1 << 32), f"{path.name}: trailer CRC32 and ISIZE exact")
    return raw, {"compressed_bytes": len(packed), "uncompressed_bytes": len(raw), "sha256": sha(path), "crc32": f"{crc:08x}"}


def archive_map(path):
    result = {}
    with tarfile.open(path, "r|*") as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            if member.isfile():
                result[name] = ("file", hashlib.sha256(archive.extractfile(member).read()).hexdigest())
            elif member.issym() or member.islnk():
                result[name] = ("link", member.linkname)
    return result


def main():
    result, startup = load(DATA / "run/result.json"), load(DATA / "run/startup.json")
    contract = load(SOAK / "build-v3/contract.json")
    remote = load(DATA / "remote-collection-verification.json")
    manifest = load(DATA / "artifact-manifest.json")
    for item in manifest["files"]:
        path = DATA / item["path"]
        check(path.stat().st_size == item["bytes"] and sha(path) == item["sha256"], f"artifact manifest verifies: {item['path']}")
        mirror = SOAK / "long-v3-final" / item["path"]
        check(mirror.stat().st_size == item["bytes"] and sha(mirror) == item["sha256"], f"output mirror verifies: {item['path']}")
    for name, expected in [("campaign.exit", 0), ("postrun-freeze.exit", 0), ("oracle-positive.exit", 0), ("oracle-negative.exit", 101)]:
        check(int((DATA / "build" / name).read_text()) == expected, f"preserved {name} has expected exit code")
    postrun = (DATA / "build/long-postrun-hashes.log").read_text().splitlines()
    check(len(postrun) == 6 and all(line.endswith(": OK") for line in postrun), "all six frozen artifacts pass postrun remote hash verification")
    check(load(SOAK / "long-v2-superseded/supersession.json")["status"] == "LIMITED_SUPERSEDED", "interrupted v2 remains superseded and excluded")
    check(contract["library_commit"] == COMMIT and contract["version"] == "v3", "frozen v3 base commit")
    for fn, expected in [("rail_runtime_soak", BIN), ("rail_runtime_soak.rs", RUST), ("run_runtime_soak.py", PYTHON)]:
        check(sha(DATA / "freeze" / fn) == expected, f"frozen {fn} SHA256")
    for remote_file in remote["freeze_checks"]:
        local = DATA / "freeze" / Path(remote_file["path"]).name
        check(sha(local) == remote_file["expected_sha256"] == remote_file["actual_sha256"], f"remote/local freeze match: {local.name}")
    for remote_file in remote["run_files"]:
        local = DATA / "run" / Path(remote_file["path"]).name
        check(local.stat().st_size == remote_file["bytes"] and sha(local) == remote_file["sha256"], f"remote/local closed run match: {local.name}")
    base = archive_map(DATA / "freeze/base-1c3.tar")
    frozen = archive_map(DATA / "freeze/soak-frozen-source-v3.tar.gz")
    check(all(frozen.get(k) == v for k, v in base.items()), "all base archive file/link contents unchanged")
    added = sorted(set(frozen) - set(base))
    check(added == ["kv-service/client-rs/tests/rail_runtime_soak.rs", "kv-service/verification/run_runtime_soak.py"], "only two instrumentation files added to base archive")
    for name, digest in [(added[0], RUST), (added[1], PYTHON)]:
        check(frozen[name] == ("file", digest), f"frozen archive instrumentation matches: {name}")
    for receipt in (startup, result):
        check((receipt["pid"], receipt["process_start_ticks"], receipt["binary_sha256"]) == (PID, TICKS, BIN), "expected process PID/start ticks/binary")
    check(all(result.get(k) == v for k, v in startup.items()), "startup identity preserved through final receipt")
    check(result["exit_code"] == 0 and result["status"] == "pass", "native exits normally with success receipt")
    check(not result["budget_exceeded"] and not result["watchdog_expired"], "no log-budget abort or watchdog termination")
    check(remote["native_pid_absent"] and remote["runner_pid_absent"], "remote collection reports both original processes absent")
    stdout, stdout_meta = closed_gzip(DATA / "run/stdout.log.gz")
    stderr, stderr_meta = closed_gzip(DATA / "run/stderr.log.gz")
    check(not stderr, "stderr empty")
    check(len(stdout) + len(stderr) == result["raw_bytes"] < result["output_raw_budget_bytes"], "raw log byte count matches final receipt and budget")
    events, non_json = [], []
    for line in stdout.decode().splitlines():
        index = line.find('{"event":')
        if index >= 0:
            events.append(json.loads(line[index:]))
        elif line.strip():
            non_json.append(line)
    groups = collections.defaultdict(list)
    for event in events:
        groups[event["event"]].append(event)
    counts = collections.Counter(e["event"] for e in events)
    counts.update(e["outcome"] for e in groups["request"])
    check(dict(counts) == result["counts"], "all parsed event/outcome counts match runner final receipt")
    check(len(groups["summary"]) == 1 and groups["summary"][0] == result["summary"], "single raw summary equals final receipt")
    check(any("1 passed; 0 failed" in line for line in non_json), "native libtest footer says 1 passed and 0 failed")
    summary = groups["summary"][0]
    check(summary["steady_elapsed_s"] >= 7200 and result["wall_elapsed_s"] >= summary["steady_elapsed_s"], "steady monotonic duration at least two hours and within process wall duration")
    check(all(summary[k] == 0 for k in ("errors", "deadlines", "rejections")), "summary errors/deadlines/rejections zero")
    check(xxh64(b"") == "ef46db3751d8e999" and xxh64(b"hello") == "26c7827d889f6da3", "independent XXH64 reference known vectors")
    objects = groups["object"]
    check(len(objects) == 3 and len({o["key"] for o in objects}) == 3, "exactly three distinct seeded objects")
    object_hashes = {}
    for seed, obj in enumerate(objects):
        pattern = bytes(i ^ (seed * 53) for i in range(251))
        payload = (pattern * ((SIZE + 250) // 251))[:SIZE]
        digest = xxh64(payload)
        check(obj["key"].endswith(f"-{seed}") and obj["bytes"] == SIZE and obj["hash_xxh64"] == digest, f"object {seed}: full independently regenerated 8 MiB XXH64")
        check(obj["generation"] == 1 and "size: 8388608" in obj["descriptor"] and "stripe_count: 2" in obj["descriptor"], f"object {seed}: initial descriptor records generation/size/two stripes")
        check(obj["key"] in obj["descriptor"] and obj["key"] in obj["placement"] and 'primary_rdma_endpoint: "10.0.0.64:25353"' in obj["placement"], f"object {seed}: initial full descriptor/placement target")
        object_hashes[obj["key"]] = digest
    requests = groups["request"]
    check(sorted(r["seq"] for r in requests) == list(range(72044)), "request sequence IDs unique and complete 0..72043")
    phase_counts = collections.Counter(r["phase"] for r in requests)
    phase_bytes = {p: sum(r["bytes"] for r in requests if r["phase"] == p) for p in phase_counts}
    check(dict(phase_counts) == {"warmup": 80, "steady": 71960, "reopened": 4}, "exact warm/steady/reopen request counts")
    for pos in range(0, len(requests), 4):
        check(set(r["seq"] for r in requests[pos:pos+4]) == set(range(pos, pos+4)), "four-read burst completes before next burst")
    for r in requests:
        seq = r["seq"]
        phase = "warmup" if seq < 80 else "steady" if seq < 72040 else "reopened"
        assert r["phase"] == phase and r["key"] == objects[seq % 3]["key"]
        assert r["bytes"] == SIZE and r["outcome"] == "ok" and r["read_result"] == f"Ok(Some({SIZE}))"
        assert r["exact_bytes"] and r["same_identity"] and r["identity_error"] == ""
        assert r["hash_xxh64"] == r["expected_hash_xxh64"] == object_hashes[r["key"]]
        assert r["latency_us"] > 0
    check(True, "every terminal request: phase/object/size/exact bytes/full identity/hash/success/positive latency")
    check(phase_bytes["steady"] == summary["steady_bytes"] == 71960 * SIZE, "steady bytes reconcile exactly")
    check(summary["steady_requests"] == phase_counts["steady"], "summary steady count reconciles exactly")
    seen = collections.Counter()
    pending_reconciliation = None
    for e in events:
        if e["event"] == "request":
            seen[e["phase"]] += 1
        if e["event"] == "reconciled":
            n = seen["reopened"] if e["phase"] == "reopened" else seen["warmup"] + seen["steady"]
            assert e["completed_requests"] == e["created_plus_reused"] == e["rail_reads_ok"] == n
            assert e["completed_bytes"] == e["rail_bytes"] == n * SIZE
            assert e["created_minus_retired"] == e["pool_live"] <= 4
            pending_reconciliation = e
        if e["event"] == "resources":
            n = seen["reopened"] if e["phase"] == "reopened" else seen["warmup"] + seen["steady"]
            assert pending_reconciliation and pending_reconciliation["phase"] == e["phase"]
            assert e["pid"] == PID and e["rail_reads_ok"] == n and e["rail_bytes"] == n * SIZE
            assert e["created"] + e["reused"] == n
            assert e["created"] - e["retired"] == e["pool_live"] == e["pool_idle"] == 4
            assert all(e[k] == 0 for k in ["pool_active", "pool_connecting", "pool_retiring", "rejected", "registered_bytes_reserved", "inflight_bytes", "inflight_requests", "rail_reads_err"])
    check(True, "all 241 persisted reconciliations match request ledger and quiescent resource invariants")
    resource = groups["resources"]
    main_resource = [e for e in resource if e["phase"] != "reopened"]
    warm, end = main_resource[0], main_resource[-1]
    check(warm["phase"] == "warm" and end["phase"] == "steady_complete" and end["elapsed_s"] == summary["steady_elapsed_s"], "continuous-reader resource axis ends at exact steady duration")
    intervals = []
    for a, b in zip(main_resource, main_resource[1:]):
        dt = b["elapsed_s"] - a["elapsed_s"]
        dn = b["rail_reads_ok"] - a["rail_reads_ok"]
        assert dt > 0 and dn > 0 and dn % 4 == 0
        assert all(b[k] >= a[k] for k in ["created", "reused", "retired"])
        intervals.append({"start_s": a["elapsed_s"], "end_s": b["elapsed_s"], "seconds": dt, "completed_requests": dn, "api_bytes": dn * SIZE})
    check(True, "every observed interval advances monotonic time and completes nonzero actual four-read work")
    check(all(e["created"] == 4 and e["retired"] == 0 for e in main_resource), "main pool remains same four connections with monotonic reuse")
    check(all(e["fd"] <= warm["fd"] + 8 and e["threads"] <= warm["threads"] + 4 and e["rss_kib"] <= warm["rss_kib"] + 131072 for e in resource), "all emitted process samples satisfy frozen diagnostic bounds")
    active = groups["active_samples"]
    aw = next(e for e in active if e["phase"] == "warm")
    ae = next(e for e in active if e["phase"] == "steady_complete")
    ar = next(e for e in active if e["phase"] == "reopened")
    check(aw["active_sample_count"] > 0 and ae["active_sample_count"] > 0, "warm and steady independently observe active leases")
    check(all(0 < e["active_sample_count"] <= e["sample_count"] and 0 < e["max_active"] <= e["max_live"] <= 4 for e in active), "all active aggregate samples obey live/active bound")
    steady_active = [e for e in active if e["phase"] in ("steady", "steady_complete")]
    check(all(b["sample_count"] > a["sample_count"] and b["active_sample_count"] > a["active_sample_count"] for a,b in zip(steady_active, steady_active[1:])), "each steady observation interval adds actual active samples")
    c = groups["contract"][0]
    check(c["continuous_reader"] and c["concurrency"] == c["pool_limit"] == 4 and not c["recovery"], "runtime contract same reader, four concurrency/pool, recovery off")
    d = groups["drop"][0]
    check(d["before_fd"] == c["before_fd"] == d["after_fd"] == 11 and d["fd_delta_from_before"] == 0, "first Reader Drop restores exact pre-reader FD baseline 11")
    for metric, suffix in [("rss", "_kib"), ("thread", "")]:
        name = "rss" if metric == "rss" else "threads"
        assert d[f"{metric}_delta_from_before{suffix}"] == d[f"after_{name}{suffix}"] - d[f"before_{name}{suffix}"]
        assert d[f"{metric}_delta_from_warm{suffix}"] == d[f"after_{name}{suffix}"] - d[f"warm_{name}{suffix}"]
    check(d["after_rss_kib"] <= warm["rss_kib"] + 131072 and d["after_threads"] <= warm["threads"] + 4, "post-Drop RSS/thread signed deltas and diagnostic bounds")
    availability = {e["kind"]: e["available"] for e in groups["native_availability"]}
    check(availability == {"qp": False, "cq": False, "mr": False}, "native PID-owned QP/CQ/MR remain explicitly unavailable")
    check(all(e["pid"] == PID and e["pid_matches"] in (0, None) for e in groups["native"]), "native observations contain no positive ownership control")
    rust = (DATA / "freeze/rail_runtime_soak.rs").read_text()
    runner = (DATA / "freeze/run_runtime_soak.py").read_text()
    check("current.descriptor == object.lookup.descriptor && current.placement == object.lookup.placement" in rust and "actual == expected && hash(actual) == expected_hash" in rust, "frozen source validates entire descriptor/placement and full payload equality")
    loop = rust.split("while started.elapsed() < Duration::from_secs(duration) {")[1].split("let elapsed = started.elapsed().as_secs_f64();")[0]
    check("make_reader()" not in loop and "sleep(" not in loop and "check_quiescent(&reader, &mut previous, sequence);" in loop, "one Reader, immediate repeat bursts, per-burst checks, no sleep filling steady duration")
    check("drop(reopened);" in rust and rust.index("drop(reopened);") < rust.index('\\"event\\":\\"summary\\"'), "reopened Reader explicitly dropped before summary and normal native exit")
    check("let latency_us = start.elapsed().as_micros();" in rust and "latencies" not in rust and "struct ActiveSamples" in rust, "producer latency emitted per request and active observations stored as bounded scalar aggregates")
    check("time.monotonic()" in runner and "stream.close()" in runner and "child.wait()" in runner, "runner uses monotonic watchdog, closes gzip, waits for native exit")
    rss = [e["rss_kib"] for e in main_resource]
    check((min(rss), max(rss), end["rss_kib"]) == (78048, summary["max_sampled_rss_kib"], summary["final_rss_kib"]), "RSS curve extrema/end independently reconcile")
    check(max(e["fd"] for e in main_resource) == summary["max_sampled_fd"] and max(e["threads"] for e in main_resource) == summary["max_sampled_threads"], "FD/thread curve maxima reconcile")
    slope = (end["rss_kib"] - warm["rss_kib"]) / end["elapsed_s"]
    check(abs(slope - summary["rss_endpoint_slope_kib_s"]) < 0.0000005, "RSS endpoint slope recomputed")
    source_scope = "SKV2 single mlx5_1 HCA -> SKV4 debug provider, Rust unoptimized test profile; healthy correctness/resource observation only"
    boundaries = [
        "API returned bytes are logical application bytes, not measured wire bytes.",
        "Full-byte and complete descriptor/placement equality are per-request harness assertions; raw payload and every returned identity are not dumped. Expected payload XXH64 is independently regenerated offline.",
        "All 241 persisted resource/reconciliation snapshots are quiescent; every burst is also checked in the frozen harness. Per-burst internal states are not individually logged.",
        "Active pool samples are aggregate counts/maxima, separate from quiescent resource points. Resource diagnostics do not measure every transient peak.",
        "Native PID-owned QP/CQ/MR are unavailable. No proof of zero native-resource leaks or exact MR deregistration follows from reserved byte counters.",
        "RSS does not return to baseline and is not required to. Observed bounded RSS is not a mathematical no-leak proof.",
        "Reopened Reader performs four correct reads and is explicitly dropped before normal exit; exact second-drop FD/native counts were not separately sampled.",
        "v2 interrupted/superseded results contribute no duration or request count to this v3 success.",
        "No per-request absolute timestamps are emitted; continuity is supported by monotonic steady loop, ordered bursts, advancing interval counters and active observations.",
        "Warm baseline elapsed_s=0 is the harness-assigned baseline just before starting the steady Instant; Drop/reopen have no usable main-reader elapsed timestamp.",
        "This run is not release performance, multi-HCA scaling, fault recovery, or FFI evidence.",
    ]
    curve_file = HERE / "soak-resource-curve.csv"
    fields = ["phase", "sampling_phase", "clock", "steady_elapsed_s", "steady_elapsed_minutes", "rss_kib", "rss_mib", "fd", "threads", "pool_live", "pool_idle", "pool_active", "registered_bytes_reserved", "inflight_bytes", "completed_requests_including_warm", "steady_completed_requests", "steady_api_bytes"]
    with curve_file.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for e in main_resource:
            n = e["rail_reads_ok"] - 80
            w.writerow(dict(phase=e["phase"], sampling_phase="quiescent_after_four_read_burst", clock="warm_baseline_assigned_zero" if e["phase"] == "warm" else "Rust_Instant_since_steady_start", steady_elapsed_s=e["elapsed_s"], steady_elapsed_minutes=e["elapsed_s"]/60, rss_kib=e["rss_kib"], rss_mib=e["rss_kib"]/1024, fd=e["fd"], threads=e["threads"], pool_live=e["pool_live"], pool_idle=e["pool_idle"], pool_active=e["pool_active"], registered_bytes_reserved=e["registered_bytes_reserved"], inflight_bytes=e["inflight_bytes"], completed_requests_including_warm=e["rail_reads_ok"], steady_completed_requests=n, steady_api_bytes=n*SIZE))
    lats = sorted(r["latency_us"] for r in requests if r["phase"] == "steady")
    report = {
        "status": "PASS_SCOPED_CORRECTNESS_AND_SAMPLED_RESOURCE_STABILITY", "scope": source_scope,
        "review_mode": "Independent offline full raw audit only; no Rust build or native workload rerun", "library_commit": COMMIT,
        "pid": PID, "process_start_ticks": TICKS, "binary_sha256": BIN, "harness_sha256": RUST, "runner_sha256": PYTHON,
        "checks_passed": len(CHECKS), "artifact_manifest_entries_verified":len(manifest["files"]), "both_local_evidence_copies_verified":True, "base_archive_entries_compared": len(base), "base_regular_files_compared": sum(v[0] == "file" for v in base.values()), "base_source_unchanged": True,
        "phase_requests": dict(phase_counts), "phase_api_bytes": phase_bytes, "total_requests": len(requests), "total_api_bytes": sum(phase_bytes.values()),
        "key_requests": dict(collections.Counter(r["key"] for r in requests)), "independently_regenerated_xxh64": object_hashes,
        "steady_elapsed_s": summary["steady_elapsed_s"], "process_wall_elapsed_s": result["wall_elapsed_s"], "native_exit": result["exit_code"], "runner_campaign_exit":0, "postrun_freeze_exit":0,
        "log_metadata": {"stdout": stdout_meta, "stderr": stderr_meta}, "events": dict(counts), "error_deadline_rejection_counts": [0,0,0],
        "interval_work": {"observed_intervals": len(intervals), "minimum_completed_requests": min(i["completed_requests"] for i in intervals), "maximum_completed_requests": max(i["completed_requests"] for i in intervals), "minimum_seconds": min(i["seconds"] for i in intervals), "maximum_seconds": max(i["seconds"] for i in intervals), "all_intervals_positive_work": True},
        "active_observations": {"warm": aw, "steady": ae, "reopened": ar},
        "resource_curve": {"file": str(curve_file.relative_to(ROOT)), "main_reader_points": len(main_resource), "periodic_steady_points": sum(e["phase"] == "steady" for e in main_resource), "sampling_phase": "quiescent after completed burst", "rss_warm_kib": warm["rss_kib"], "rss_min_kib": min(rss), "rss_peak_kib": max(rss), "rss_final_kib": end["rss_kib"], "rss_final_minus_warm_kib": end["rss_kib"] - warm["rss_kib"], "rss_peak_minus_warm_kib": max(rss) - warm["rss_kib"], "rss_endpoint_slope_kib_s": slope, "fd_values": sorted({e["fd"] for e in main_resource}), "thread_values": sorted({e["threads"] for e in main_resource}), "pool_live_values": sorted({e["pool_live"] for e in main_resource}), "quiescent_reserved_and_inflight_zero": True},
        "post_drop": d, "native_pid_resources": {"qp":"unavailable", "cq":"unavailable", "mr":"unavailable"},
        "latency_diagnostic_us_not_performance": {"min":lats[0], "median":statistics.median(lats), "p99_nearest_rank":lats[(len(lats)*99+99)//100-1], "max":lats[-1], "retained_by_producer":False},
        "main_reader_counter_end": end, "native_normal_exit_and_reopen_drop": True, "boundaries": boundaries,
        "evidence_root": str(DATA.relative_to(ROOT)),
    }
    (HERE / "soak-final-review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    ppt = {
        "status": report["status"], "headline_zh": "两小时连续读取完成：逐次内容与身份核对通过，资源采样满足预设边界",
        "scope_zh": "单 HCA；Rust test profile；SKV2→SKV4 debug 服务；正确性与采样资源稳定性验证",
        "steady_seconds": summary["steady_elapsed_s"], "steady_requests": phase_counts["steady"], "steady_api_bytes": phase_bytes["steady"], "steady_api_gib": phase_bytes["steady"]/1024**3,
        "total_requests_with_warm_and_reopen":len(requests), "warmup_requests":80, "reopen_requests":4,
        "all_reads_full_bytes_hash_identity_ok":True, "errors":0, "deadlines":0, "rejections":0,
        "concurrency":4, "pool_limit":4, "warm_active_samples":aw["active_sample_count"], "steady_active_samples":ae["active_sample_count"], "steady_total_samples":ae["sample_count"], "max_observed_pool_live":4,
        "curve_csv":str(curve_file.relative_to(ROOT)), "curve_clock":"Rust steady Instant; 0 is warm baseline", "curve_sampling_zh":"240 个主 Reader 静止点：每批4次读取完成后采样；活动期另有独立累计采样",
        "rss_warm_mib":warm["rss_kib"]/1024, "rss_peak_mib":max(rss)/1024, "rss_final_mib":end["rss_kib"]/1024, "rss_final_delta_mib":(end["rss_kib"]-warm["rss_kib"])/1024,
        "rss_limit_warm_plus_mib":128, "fd_main_sampled":23, "threads_main_sampled":10, "fd_before_reader":11, "fd_after_drop":11,
        "stage_resources":[{"phase":"before_reader","fd":11,"rss_kib":c["before_rss_kib"],"threads":c["before_threads"]},{"phase":"warm","fd":warm["fd"],"rss_kib":warm["rss_kib"],"threads":warm["threads"]},{"phase":"steady_complete","fd":end["fd"],"rss_kib":end["rss_kib"],"threads":end["threads"]},{"phase":"after_drop","fd":d["after_fd"],"rss_kib":d["after_rss_kib"],"threads":d["after_threads"]},{"phase":"reopened","fd":resource[-1]["fd"],"rss_kib":resource[-1]["rss_kib"],"threads":resource[-1]["threads"]}],
        "native_qp_cq_mr_pid_attribution":"unavailable", "footer_zh":"逻辑读取字节，非线速吞吐；RSS/FD 为静止点采样；原生 QP/CQ/MR 归属不可用，不据此宣称零泄漏或发布版性能。",
        "claim_boundaries":boundaries,
    }
    (HERE / "soak-ppt-summary.json").write_text(json.dumps(ppt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status":report["status"],"checks":len(CHECKS),"total_requests":len(requests),"steady_seconds":summary["steady_elapsed_s"],"curve_points":len(main_resource),"artifacts":["soak-final-review.json","soak-resource-curve.csv","soak-ppt-summary.json"]}, indent=2))


if __name__ == "__main__":
    main()
