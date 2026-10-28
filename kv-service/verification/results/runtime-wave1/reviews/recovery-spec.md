# Recovery MVP — independent spec and safety review

2026-10-29. **FINAL STATIC PASS for the final production pin below; no unresolved blocking findings.** The initial pass missed the zero-I/O-timeout compatibility edge subsequently identified by the implementer; that issue is fixed and re-reviewed below. This is not native-RXE acceptance or performance evidence.

Scope: working-tree diff against `75dba7aa5243ef62df4f9104aec098d4659adbee` in `work/ContextStore-runtime-wave1`. Authority: the parent's explicit bounded MVP and `outputs/research-plan-20261028/designs/03-object-recovery-engine.md`. Future ledgers, asynchronous retirement, runtime close, and Python ABI are outside this MVP. No source edits, builds, or test executions were performed. `git diff --check 75dba7a` passed.

## Reviewed behavior

- **Eligibility and cleanup:** `connection_pool.rs:9–11,19–74,273–293` keeps the certificate's field private, whitelists structured transient I/O, and constructs the certificate only after QP retirement and MR deregistration. Connect failures occur before data submission; `rdma.rs:355–388` destroys the handshake QP. Registration failures, strings, protocol/count/stale errors, and panics do not receive certificates.
- **Ownership and bounds:** `rail_read.rs:1384–1481,1516–1561,1685–1720` joins every initial scoped worker before recovery, preserves whole successful task buffers/private ranges, rejects a wave containing an uncertified failure, and retries each failed task once sequentially. The immutable validated disjoint plan bounds requested retry payload by object size. Route replacement preserves task stripes and requires the same unambiguous owner/advertised endpoint, excluding every failed initial route.
- **Resources:** `rail_read.rs:818–873,1219–1289,1432–1474` retains global reservations through worker cleanup and recovery; only after the barrier does it release old rail credits and atomically re-admit each retry under concurrent per-rail limits.
- **Deadline/publication:** `lib.rs:244–288` starts before the initial lookup and carries the same deadline through staging, post-lookup and commit. Low-level entry owns its start (`rail_read.rs:1831–1884`); overflow is rejected. Cancellation/deadline checks occur inside the publication mutex (`328–360`), with no post-copy timeout failure. Documentation correctly permits DNS/TCP/provider cleanup to exceed the publication deadline.
- **Integrity and compatibility:** Successful initial tasks are checked before retry work only when recovery is needed; normal reads retain one final checksum pass. Exact length/count, coverage, all nine descriptor fields and full placement equality remain guarded. Recovery and pooling remain opt-in; unstriped reads bypass both. CLI pattern expectations are computed outside timing; recovery statistics distinguish requested retry payload/private task success from wire bytes/publication.

## Evidence boundary

Reviewed test source covers join/cancel ordering, blocked cleanup certification, rejection classes, alternate-route admission, deadline overflow/gate ordering, failed-retry termination and direct partial-range overwrite. Native direct/packed × pooled/unpooled tests were still being developed/run; their result receipts were not reviewed. This pass permits the next quality-review stage, not a claim that native failover or performance has passed.

## Initial review production SHA-256

| File under `kv-service/client-rs/src/` | SHA-256 |
| --- | --- |
| `rail_read.rs` | `ffc82a32c832a5ac0c9747ea6c30e207a8487ab1ac2409b16ae87cbdbce40c13` |
| `rail_read/connection_pool.rs` | `f21d9bcea6a3bce0d34c3679aea9153e91c0daa3a544a2c9c610ffb942f1510a` |
| `lib.rs` | `e6c1c9f78ce0709fd90a247f42e627550b387c145e0ed77e02faa692a5569964` |
| `rdma.rs` | `62a73733c9455677cb529f563a49f157c58cdf687e08de6d2ced3ac059328d3d` |

## Final compatibility amendment and final pin

Resolved issue: with recovery disabled, the first pin returned `DeadlineExceeded` for a configured zero `io_timeout`, changing the previous transport-error behavior. At final `rail_read.rs:1317–1320`, the zero-timeout branch now calls `check_live(cancel, deadline)` instead of returning an unconditional deadline error. A configured zero timeout reaches the legacy transport error; an actually expired request deadline is still rejected before transport. No retry eligibility, ownership, budget, or publication code changed.

Final `rail_read.rs` SHA-256: `1d46ea5f1813cc78693299adb8e3dc6cbdf97a66370d96c09e810dfe3f8f2ab5`. The other three production hashes in the initial table are unchanged. Independently reversing only this source hunk reproduced the exact previous `ffc82a32…c13` hash. The final hash was independently read from both the local checkout and SKV_2's `src-candidate`; `git diff --check 75dba7a` passed again.

Independently inspected regression evidence from SKV_2. Logs 16–20 were also read from the local `kv-service/verification/results/runtime-wave1/recovery/`; log 25 was read remotely:

- `16-zero-timeout-red.log`: targeted compatibility regression failed before the fix.
- `17-zero-timeout-green.log`: the same regression passed after the fix (1 passed, 90 filtered).
- `25-zero-timeout-matrix.log`: expanded `is_striped ∈ {false,true}` × `recovery_enabled ∈ {false,true}` regression passed (1 test containing all four cases, 90 filtered). At review time this log was read directly from SKV_2's `/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/verification/results/runtime-wave1/recovery/`.
- `18-final-all-tests.log`: 90 library tests passed, 1 ignored; CLI tests 3 + 2 passed; cluster tests 4 passed. `19-final-clippy.log` and `20-final-default-check.log` finished successfully. These logs were inspected, not rerun by this reviewer.

The parent reports native four-mode success at the **previous** production pin. The final-pin native rerun was still in progress when this amendment was written; neither that prior result nor this static pass establishes final-pin native acceptance. Final disposition: **SPEC + SAFETY PASS for the final source pin, native acceptance separately pending receipt review.**
