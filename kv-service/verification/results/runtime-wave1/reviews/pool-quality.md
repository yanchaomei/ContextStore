# Pool quality review

Result: **PASS. No actionable correctness, concurrency, unsafe-lifetime, API-regression, or maintainability findings in the corrected striped-only pool diff.**

Independently reviewed all five tracked changed files plus untracked `client-rs/src/rail_read/connection_pool.rs` against `3fe4b26d72fffbe3a3d95236b858ab2ed0d78a6b`. Standards sources: repository `CLAUDE.md`, README, and the supplied project instructions. This was source/receipt inspection; no code edits or local builds/tests.

## Checks

- `connection_pool.rs:80–129,177–214`: exclusive ownership and mutex-protected state transitions prevent simultaneous use of one client. Connecting slots are reserved before opening; retirement/eviction remains charged until client destruction. Connection setup, transfer, registration destruction, and provider teardown run outside the mutex. The small private generic resource seam serves concrete lifecycle tests without expanding the public API.
- `rail_read.rs:1656–1665,1719–1736`: non-striped descriptors bypass pooling and pooled owner validation, retaining byte-only ACK validation. Existing configuration struct literals remain compatible; pool activation is an additive builder. RDMA modules remain feature-gated, with no dependency or server changes.
- `rail_read.rs:1768–1793,1836–1870` and `connection_pool.rs:194–214`: both receive paths catch transfer unwinding while registrations remain owned. Successful striped ACK validation precedes MR deregistration and idle return; errors/stale/incomplete/panic paths destroy QPs before MRs. No new unsafe implementation is introduced; existing client `Send` is used under exclusive leases. Pool clients never use the MR cache.
- `rdma.rs:1254–1273`: complete framing and exact success status precede outcome acceptance. README accurately limits reuse to stripe-subset ACKs and states immediate capacity rejection, per-operation timeouts, and the unresolved full-descriptor fallback defect.

## Evidence and limits

All six current SHA-256 values match `fix-unstriped-receipt.json` and `tested-source-unstriped-fix.sha256`. Raw logs corroborate **82 passed / 25 ignored**, successful client all-target clippy, and three explicit provider checks: non-striped bypass, healthy reuse/stale retirement, and parallel leases.

Provider checks use a single HCA and default packed receives. They do not establish pooled direct-staging hardware coverage, delayed-CQ/panic fault-injection safety, dual-HCA aggregation, or performance improvement. Lifecycle tests establish deterministic ownership/drop order. Multi-WR count compatibility is a unit seam, not a native >1 GiB transfer. Whole-repository build/server-suite results were not audited in this scoped client review. The pre-existing full-descriptor fallback issue remains excluded rather than repaired.
