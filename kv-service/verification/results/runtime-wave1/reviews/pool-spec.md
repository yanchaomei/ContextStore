# Pool spec review

Result: **PASS for the corrected, striped-only pool scope. No remaining actionable spec findings.**

Reviewed the uncommitted diff against `3fe4b26d72fffbe3a3d95236b858ab2ed0d78a6b`, including untracked `connection_pool.rs`. Independent source/receipt inspection only; no implementation edits or local test runs.

## Resolved findings

- **P1 — unsafe reuse after full-descriptor fallback ACK:** The existing server can enter fallback on the same QP/CQ after `RetireLegacyGet` marks an uncertain WRITE (`server/src/rdma/server.rs:908–914,1721–1737`). An old completion can satisfy fallback's completion count before the fallback WRITE finishes. Pool reuse would deregister the target MR while that QP remains live. The correction excludes all non-striped descriptors from pooling before owner/admission checks (`client-rs/src/rail_read.rs:1656–1665`), preserving QP-before-MR teardown. This closes the new pool exposure; the underlying server defect remains open.
- **P2 — legacy ACK-count regression:** Full-descriptor responses report native WRITE splits/storage segments, not one logical stripe (`server/src/rdma/server.rs:642–644,728`). Requiring one rejected valid complete non-striped reads, including slab hits larger than 1 GiB. Corrected validation retains exact bytes, applies exact selected-stripe counts only to striped reads, and preserves legacy non-striped behavior (`client-rs/src/rail_read.rs:1719–1736`). Tests cover valid counts 2/7 and unchanged striped rejection.

## Verified requirements

Opt-in per-reader ownership, unchanged public configuration literals, complete route/owner keys, exclusive leases, request-local buffers/MRs, exact framed striped ACK validation, MR deregistration before idle return, and QP retirement before MR release for failed/stale/incomplete/caught-transfer-panic outcomes. Connecting/active/idle/retiring capacity remains charged through client destruction; eviction reserves the slot. Setup, transfer, and teardown run outside the pool mutex. Socket timeouts refresh on checkout.

## Evidence and limits

All six current files match `pool/tested-source-unstriped-fix.sha256`. Inspected raw red/green logs for both corrections, fresh client results **82 passed / 25 ignored**, and successful all-target clippy. Fresh provider logs confirm two non-striped 8192-byte reads with all pool counts zero; striped reuse/stale **created=2,reused=2,retired=1**; eight parallel reads over two waves **created=4,reused=4,retired=0**.

Provider evidence is single-HCA correctness only. Multi-WR count validation is a deterministic unit seam, not a native >1 GiB transfer. No native delayed-CQ/panic proof, dual-HCA result, performance claim, or recovery implementation is established. The server fallback issue is excluded, not repaired. Timeouts bound socket operations, not whole reads.
