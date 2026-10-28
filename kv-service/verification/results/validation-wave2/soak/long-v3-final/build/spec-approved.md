# Soak SPEC v3 re-review — PASS

Scope: close the three P2 findings in `soak-spec.md`, inspect their related execution paths, and independently read back the completed 60-second smoke. No runtime edits, local build, or additional experiment was performed by this reviewer.

Frozen identities: base/HEAD `1c3ee135d19fece67507b51acd1fc51ae9541208`; tracked runtime diff empty. Rust SHA256 `6f7d8113f58a34a28faedda8692421e2f847b2f52d1e965f6bd836e083a9c512`; runner `60f815a95a7772d0bf8875452aa0caa3a10196a934befa630761435526777714`; smoke binary `5aa06344cb6d56c55de119d3ffd01a8c654423bf98084e25590f73ff97ebe1df` matches frozen-v3 manifest.

- Active-pool sampling finding CLOSED: bounded scalar sample counts/maxima are persisted for warmup and steady periods; both require positive active observations. Smoke steady recorded 15,548 samples, 9,816 active observations, max active=4 and max live=4.
- Post-Drop metrics finding CLOSED: the Drop event preserves FD/RSS/threads, baselines and signed deltas; FD restoration and warm RSS+128 MiB / threads+4 guards are asserted. Smoke FD 11→11; RSS warm 75,484→post-Drop 76,048 KiB (+564 KiB); threads warm 10→10. The contract correctly does not demand allocator RSS return exactly to baseline.
- Counter reconciliation finding CLOSED: each quiescent burst asserts created−retired=live, created+reused=completed, rail reads_ok=completed, and rail bytes=completed×8 MiB. Steady completion including warmup reconciles 684 reads and 5,737,807,872 bytes; reopened Reader independently reconciles 4 reads.

Readback evidence: `soak/build-v3/frozen.sha256`, negative oracle exit 101, positive oracle exit 0, `soak/smoke-v3/result.json`, and gzip stdout/stderr. Smoke normal exit=0, steady duration 60.364347 s, 604 steady requests, 688 total. Independently parsed all 688 terminal records: unique contiguous sequence IDs, expected byte count, full-byte equality, matching hashes and identity, all success; stderr empty. No output-budget or watchdog failure.

No remaining substantive SPEC finding in the reviewed fix scope. Implementation and smoke are ready for the separately gated 7200-second run after quality review. This is not a two-hour PASS. Native QP/CQ/MR ownership remains explicitly unavailable. Sampled diagnostics are bounded observations, not proof of zero resource growth between samples. The v2 report remains preserved as historical review evidence.
