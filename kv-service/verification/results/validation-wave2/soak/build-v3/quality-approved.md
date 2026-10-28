# Soak quality review — PASS for frozen v3 long-run launch

Reviewed the new harness and runner against the frozen v3 contract and `soak-spec-v3.md`. No actionable quality defect found within this healthy single-HCA stability campaign. The completed v3 smoke independently passes raw-evidence readback. The frozen 7200-second run may start; this review is not two-hour acceptance.

## Frozen scope

- Base/HEAD: `1c3ee135d19fece67507b51acd1fc51ae9541208`; tracked runtime diff is empty. The separate build receipt records 2,485 unchanged base files and no mismatches.
- `kv-service/client-rs/tests/rail_runtime_soak.rs`: SHA256 `6f7d8113f58a34a28faedda8692421e2f847b2f52d1e965f6bd836e083a9c512`.
- `kv-service/verification/run_runtime_soak.py`: SHA256 `60f815a95a7772d0bf8875452aa0caa3a10196a934befa630761435526777714`.
- Frozen smoke binary: SHA256 `5aa06344cb6d56c55de119d3ffd01a8c654423bf98084e25590f73ff97ebe1df`, matching `build-v3/frozen.sha256` and smoke startup/result.
- Review performed by reading source and existing receipts and parsing local compressed logs; no production edits, local Rust build/tests, or additional remote workload.

## Quality assessment

- Memory remains bounded by three fixed 8 MiB expected payloads, four concurrent destinations and finite per-burst futures. Historical requests are emitted to compressed output rather than retained. Active sampling uses scalar counts and maxima.
- Each returning request records its result, exact-byte equality, independently recomputed hash, descriptor/placement identity, and sequence. All four outcomes join before the burst success assertion. Failed reads, content mismatch or identity mismatch cannot reach the success summary. External termination remains failed/incomplete evidence, not a complete request ledger.
- Active snapshots enforce the live limit and state sum; warmup and steady periods independently require positive active observations. Every completed burst checks quiescent zero states/reservations, created minus retired equals live, created plus reused equals completed, and acknowledged reads/bytes equal completed work. The one configured rail makes these totals directly comparable.
- One Reader survives the entire steady interval. `Instant` drives the workload loop, which continuously issues bursts until the selected duration is reached, completes the last burst, and then performs cleanup/reopen checks. There is no artificial wait used to manufacture elapsed duration. The runtime I/O limit is 10 seconds; the runner's duration plus 300-second watchdog is a failure path, not successful completion.
- Drop records and checks FD restoration and the agreed RSS/thread diagnostic bounds; reopening performs four verified reads and independent new-pool reconciliation before success. Native PID ownership is only accepted after a positive observation; this platform's QP/CQ/MR attribution remains explicitly unavailable.
- The runner records PID/start ticks and binary hash, drains stdout/stderr concurrently, preserves compressed raw logs, counts parsed events, and rejects nonzero exit, output-budget breach, watchdog expiry, missing summary or short steady duration. Independent terminal-log reconciliation remains part of final acceptance; a runner status alone is insufficient.
- The negative oracle receipt exits 101 and the positive oracle exits 0. Both exercise the same payload predicate used by the live harness.

## Independent v3 smoke readback

Read and fully decompressed `soak/smoke-v3/stdout.log.gz` and `stderr.log.gz`, independently parsed JSON events, and compared with `result.json`:

- Normal exit 0; steady interval **60.364347 seconds**; no watchdog/output-budget failure.
- **688** terminal requests, unique contiguous sequence IDs: **80** warmup, **604** steady, **4** reopened.
- Every request has 8,388,608 bytes, exact-byte equality, matching expected/actual hash, matching identity, and successful outcome.
- All five persisted counter reconciliation events match completed request/byte totals. Parsed event counts match the runner receipt exactly.
- Steady active observations: 9,816 of 15,548 samples; maximum active/live both 4. Post-Drop FD 11 to 11, RSS warm 75,484 to 76,048 KiB, threads warm/post-Drop 10 to 10.
- Total raw bytes 226,593, matching the receipt; stderr empty; both gzip streams closed and valid. One success summary matches the receipt exactly.

## Acceptance boundary

The v2 limited/superseded campaign is not reused as v3 duration evidence. Final acceptance still requires the same frozen v3 executable to complete at least 7200 steady seconds, normal process exit, all cleanup/reopen checks, closed valid raw logs and independent full request/counter reconciliation. Process diagnostics are sampled observations and do not prove zero transient growth. Native QP/CQ/MR cleanup counts remain unavailable; no native-resource or multi-HCA performance claim follows from this review.
